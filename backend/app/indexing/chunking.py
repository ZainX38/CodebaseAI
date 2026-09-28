import ast
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from pathlib import PurePosixPath
import warnings

from app.config import MAX_CHUNK_CHARS, MAX_LINE_CHARS, WINDOW_LINES, WINDOW_OVERLAP_LINES


# Every chunk keeps enough information to cite it later: which file, which lines
# (1-based and inclusive, like GitHub) and which commit it came from
@dataclass(frozen=True)
class Chunk:
    id: str
    path: str
    start_line: int
    end_line: int
    language: str
    symbol: str | None  # e.g. "Session.request", None for plain line windows
    kind: str  # "function", "method", "class" or "window"
    commit_sha: str
    text: str


LANGUAGES_BY_EXTENSION = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".swift": "swift", ".scala": "scala", ".sh": "shell", ".bash": "shell",
    ".ps1": "powershell", ".sql": "sql", ".html": "html", ".css": "css", ".scss": "css",
    ".vue": "vue", ".svelte": "svelte", ".md": "markdown", ".rst": "restructuredtext",
    ".txt": "text", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".xml": "xml",
}

LANGUAGES_BY_NAME = {"Dockerfile": "dockerfile", "Makefile": "makefile"}


def get_language(path: str) -> str:
    file_path = PurePosixPath(path)
    return LANGUAGES_BY_NAME.get(file_path.name) or LANGUAGES_BY_EXTENSION.get(file_path.suffix.lower(), "text")


# str.splitlines() also splits on characters like form feed (\x0c), which Python's
# ast and GitHub don't count as new lines. That would shift every line number after it.
# StringIO(newline="") only splits on \n, \r\n and \r, and keeps the line endings,
# so "".join(lines) is exactly the original file
def split_lines(content: str) -> list[str]:
    return StringIO(content, newline="").readlines()


# The ID only depends on where the chunk is, so indexing the same commit twice
# gives the same IDs and Pinecone overwrites vectors instead of duplicating them
def get_chunk_id(commit_sha: str, path: str, start_line: int, end_line: int) -> str:
    return sha256(f"{commit_sha}:{path}:{start_line}:{end_line}".encode()).hexdigest()[:32]


def make_chunk(
    lines: list[str],
    path: str,
    start_line: int,
    end_line: int,
    commit_sha: str,
    symbol: str | None = None,
    kind: str = "window",
) -> Chunk:
    return Chunk(
        id=get_chunk_id(commit_sha, path, start_line, end_line),
        path=path,
        start_line=start_line,
        end_line=end_line,
        language=get_language(path),
        symbol=symbol,
        kind=kind,
        commit_sha=commit_sha,
        text="".join(lines[start_line - 1 : end_line]),
    )


# ---------------------- Overlapping line windows -----------------------------
# Splits lines first_line..last_line (1-based, inclusive) into windows of up to
# WINDOW_LINES lines and MAX_CHUNK_CHARS characters. Each window repeats the last
# WINDOW_OVERLAP_LINES lines of the previous one, so code that sits on a boundary
# (e.g. an if statement and its body) is complete in at least one chunk
def window_chunks(
    lines: list[str],
    path: str,
    commit_sha: str,
    first_line: int = 1,
    last_line: int | None = None,
    symbol: str | None = None,
    kind: str = "window",
) -> list[Chunk]:
    last_line = len(lines) if last_line is None else last_line
    chunks = []
    start = first_line

    while start <= last_line:
        # Lines that are too long are skipped, but line numbers stay correct because
        # every chunk is taken from the original list of lines
        if len(lines[start - 1]) > MAX_LINE_CHARS:
            start += 1
            continue

        end = start
        size = len(lines[start - 1])
        while (
            end < last_line
            and end - start + 1 < WINDOW_LINES
            and len(lines[end]) <= MAX_LINE_CHARS
            and size + len(lines[end]) <= MAX_CHUNK_CHARS
        ):
            size += len(lines[end])
            end += 1

        # Windows with only blank lines are useless for search
        if "".join(lines[start - 1 : end]).strip():
            chunks.append(make_chunk(lines, path, start, end, commit_sha, symbol, kind))

        if end >= last_line:
            break

        # If the window stopped before a line that is too long, there is nothing to
        # overlap with: the next window starts after it instead of repeating this one
        if len(lines[end]) > MAX_LINE_CHARS:
            start = end + 1
            continue

        # The overlap is at most half the window, so windows that were cut short by
        # MAX_CHUNK_CHARS still move forward instead of shifting one line at a time
        overlap = min(WINDOW_OVERLAP_LINES, (end - start + 1) // 2)
        start = end - overlap + 1

    return chunks


# ---------------------- Python: one chunk per function and class ------------
@dataclass(frozen=True)
class SymbolSpan:
    start_line: int
    end_line: int
    symbol: str
    kind: str


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# Blocks whose body can contain definitions, e.g. "if TYPE_CHECKING:" or
# "try: ... except ImportError: def fallback(): ..."
CompoundNode = ast.If | ast.Try | ast.TryStar | ast.With | ast.AsyncWith


def get_indent(line: str) -> int:
    return len(line) - len(line.lstrip())


# Decorators belong to the function (@property, @app.get(...)) but ast's lineno
# points at the "def" line, so the chunk starts at the first decorator instead.
# floor is the last line already used by something else, so lines are never shared
def get_definition_start(lines: list[str], node: FunctionNode | ast.ClassDef, floor: int) -> int:
    start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])

    # A decorator written as "@(\n    decorator\n)" reports the line after the "@",
    # so it walks up to the line that actually starts with "@"
    if node.decorator_list:
        while start - 1 > floor and not lines[start - 1].lstrip().startswith("@"):
            start -= 1

    return start


# A comment block right above a definition usually explains it, so it is included.
# Only comments with the same indentation count, so a comment at the end of a class
# body is not attached to the next top-level function
def include_leading_comments(lines: list[str], start_line: int, floor: int) -> int:
    definition_indent = get_indent(lines[start_line - 1])
    first_line = start_line

    while first_line - 1 > floor:
        line = lines[first_line - 2]
        if not line.lstrip().startswith("#") or get_indent(line) != definition_indent:
            break
        first_line -= 1

    # A block that reaches line 1 is the file header (shebang, licence), not
    # documentation for this one function
    return start_line if first_line == 1 else first_line


# Yields every definition in a body (including inside if/try/with blocks) with the
# last line used before it, which stops comments from being taken from earlier code
def iter_definitions(body: list[ast.stmt], floor: int):
    for node in body:
        if isinstance(node, FunctionNode | ast.ClassDef):
            yield node, floor
        elif isinstance(node, CompoundNode):
            blocks = [node.body, getattr(node, "orelse", []), getattr(node, "finalbody", [])]
            blocks[1:1] = [handler.body for handler in getattr(node, "handlers", [])]
            block_floor = node.lineno
            for block in blocks:
                yield from iter_definitions(block, block_floor)
                if block:
                    block_floor = block[-1].end_lineno
        floor = max(floor, node.end_lineno)


def get_symbol_start(lines: list[str], node: FunctionNode | ast.ClassDef, floor: int) -> int:
    return include_leading_comments(lines, get_definition_start(lines, node, floor), floor)


# Walks the module and finds every function, class and method.
# A class is split into its "header" (class line, docstring, attributes up to the
# first method) and one chunk per method, so no line ends up in two chunks.
# Nested functions stay inside their parent function's chunk
def get_python_symbol_spans(tree: ast.Module, lines: list[str]) -> list[SymbolSpan]:
    spans = []

    def visit(body: list[ast.stmt], prefix: str, floor: int) -> None:
        for node, node_floor in iter_definitions(body, floor):
            start = get_symbol_start(lines, node, node_floor)

            if isinstance(node, FunctionNode):
                kind = "method" if prefix else "function"
                spans.append(SymbolSpan(start, node.end_lineno, prefix + node.name, kind))
                continue

            qualified_name = prefix + node.name
            first_member = next(iter_definitions(node.body, start), None)
            # The header stops before the first method and the comments above it
            header_end = get_symbol_start(lines, *first_member) - 1 if first_member else node.end_lineno

            # Defensive: a header needs at least one line before it becomes a chunk
            if header_end >= start:
                spans.append(SymbolSpan(start, header_end, qualified_name, "class"))
            visit(node.body, qualified_name + ".", max(header_end, start))

    visit(tree.body, "", 0)
    return spans


def chunk_python_file(path: str, lines: list[str], tree: ast.Module, commit_sha: str) -> list[Chunk]:
    chunks = []
    covered_lines = set()

    for span in get_python_symbol_spans(tree, lines):
        span_lines = lines[span.start_line - 1 : span.end_line]
        covered_lines.update(range(span.start_line, span.end_line + 1))
        fits = sum(map(len, span_lines)) <= MAX_CHUNK_CHARS and all(len(line) <= MAX_LINE_CHARS for line in span_lines)

        if fits:
            chunks.append(make_chunk(lines, path, span.start_line, span.end_line, commit_sha, span.symbol, span.kind))
        else:
            # A function that is too long becomes several windows that all keep its name,
            # so a search for the function still finds every part of it
            chunks.extend(window_chunks(lines, path, commit_sha, span.start_line, span.end_line, span.symbol, span.kind))

    # Code outside any function or class (imports, constants, if __name__ == "__main__")
    # is split into normal windows, one group of consecutive lines at a time
    gap_start = None
    for line_number in range(1, len(lines) + 2):
        is_gap = line_number <= len(lines) and line_number not in covered_lines
        if is_gap and gap_start is None:
            gap_start = line_number
        elif not is_gap and gap_start is not None:
            chunks.extend(window_chunks(lines, path, commit_sha, gap_start, line_number - 1))
            gap_start = None

    return sorted(chunks, key=lambda chunk: (chunk.start_line, chunk.end_line))


def parse_python(content: str) -> ast.Module | None:
    try:
        # Old code can trigger SyntaxWarnings (e.g. invalid escape sequences). They don't
        # stop parsing, so they are silenced instead of filling the server logs
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Python accepts a UTF-8 BOM at the start of a file but ast.parse() on a str
            # doesn't. Removing it doesn't change any line numbers, and chunk text is
            # still taken from the original lines
            return ast.parse(content.removeprefix("﻿"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        # Python 2 files, templates with a .py extension, or deeply nested generated code
        return None


# ---------------------- Chunks one file ---------------------------------------
# Python files are parsed with the standard library ast module (nothing is ever
# executed). Other languages, and Python files that don't parse, use line windows
def chunk_file(path: str, content: str, commit_sha: str) -> list[Chunk]:
    lines = split_lines(content)

    if get_language(path) == "python":
        tree = parse_python(content)
        if tree is not None:
            return chunk_python_file(path, lines, tree, commit_sha)

    return window_chunks(lines, path, commit_sha)


def chunk_repository(files: dict[str, str], commit_sha: str) -> list[Chunk]:
    chunks = []
    for path in sorted(files):
        chunks.extend(chunk_file(path, files[path], commit_sha))
    return chunks
