from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from pathlib import PurePosixPath

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


# ---------------------- Chunks one file ---------------------------------------
def chunk_file(path: str, content: str, commit_sha: str) -> list[Chunk]:
    return window_chunks(split_lines(content), path, commit_sha)


def chunk_repository(files: dict[str, str], commit_sha: str) -> list[Chunk]:
    chunks = []
    for path in sorted(files):
        chunks.extend(chunk_file(path, files[path], commit_sha))
    return chunks
