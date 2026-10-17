import ast
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import secrets
from typing import Literal

from app import config
from app.indexing.chunking import Chunk, split_lines
from app.retrieval.bm25 import tokenize
from app.retrieval.retriever import QuestionError, RepositoryIndex
from app.retrieval.symbols import find_definitions, parse_python


ContextMode = Literal["chunks", "implementation"]

# Room for the chat template (role markers, "Question:", "Repository excerpts:") that
# the model adds around the messages, and that I can't measure exactly
TEMPLATE_RESERVE_TOKENS = 512
# Below this, there isn't enough room for any useful code
MIN_CONTEXT_TOKENS = 256
# A cut-down excerpt smaller than this is more confusing than helpful
MIN_EXCERPT_TOKENS = 120

# 4 random bytes = 8 hex characters, e.g. "9f3a01c2"
FENCE_BYTES = 4
FENCE_PLACEHOLDER = "0" * (FENCE_BYTES * 2)


# ---------------------- Counting tokens ---------------------------------------
# I don't have qwen3.5's or deepseek's tokenizer here, and an estimate like
# "4 characters per token" can be too low for code full of symbols. One token is
# always at least one byte of UTF-8, so the number of bytes is a safe upper bound:
# the prompt can never be longer than the model's context and silently cut off
def count_tokens(text: str) -> int:
    return len(text.encode("utf-8"))


def get_context_budget(
    system_prompt: str,
    question: str,
    context_tokens: int = config.CONTEXT_TOKENS,
    max_output_tokens: int = config.MAX_OUTPUT_TOKENS,
) -> int:
    budget = context_tokens - max_output_tokens - count_tokens(system_prompt) - count_tokens(question) - TEMPLATE_RESERVE_TOKENS
    if budget < MIN_CONTEXT_TOKENS:
        raise QuestionError("The question is too long to leave room for any code; please shorten it")
    return budget


# ---------------------- What the model is given -------------------------------
# One excerpt is one contiguous range of lines from one file, so a citation of its ID
# always points at exactly the text the model saw
@dataclass(frozen=True)
class Excerpt:
    id: str
    path: str
    start_line: int
    end_line: int
    text: str
    symbol: str | None
    scope: str  # "complete body", "partial body; other lines omitted", "excerpt", "excerpt cut to fit"
    commit_sha: str

    # The fence is a random code made for each question. A file can contain a line that
    # looks like "[source-1] src/auth.py:1-2", but it can't guess the fence, so the model
    # can always tell where an excerpt really starts and ends
    def header(self, fence: str = FENCE_PLACEHOLDER) -> str:
        label = f" ({self.symbol}; {self.scope})" if self.symbol else f" ({self.scope})"
        return f"[{self.id} | {fence}] {self.path}:{self.start_line}-{self.end_line}{label}\n"

    def footer(self, fence: str = FENCE_PLACEHOLDER) -> str:
        return f"[end of {self.id} | {fence}]"

    def render(self, fence: str = FENCE_PLACEHOLDER) -> str:
        newline = "" if self.text.endswith("\n") or not self.text else "\n"
        return self.header(fence) + self.text + newline + self.footer(fence)

    # The placeholder has the same length as a real fence, so the cost is exact
    def cost(self) -> int:
        return count_tokens(self.render() + "\n\n")


def new_fence() -> str:
    return secrets.token_hex(FENCE_BYTES)


def format_context(excerpts: list[Excerpt], fence: str = FENCE_PLACEHOLDER) -> str:
    return "\n\n".join(excerpt.render(fence) for excerpt in excerpts)


# ---------------------- Builds the context ------------------------------------
# "chunks": the retrieved chunks themselves (the evaluation's stages 1 and 2)
# "implementation": a retrieved function is replaced by its complete body (stage 3 and the app)
def build_context(
    candidates: list[Chunk],
    repository: RepositoryIndex,
    question: str,
    budget: int,
    mode: ContextMode = "implementation",
) -> list[Excerpt]:
    builder = ContextBuilder(repository, question, budget)

    for position, chunk in enumerate(candidates):
        if builder.remaining < MIN_EXCERPT_TOKENS:
            break
        if mode == "implementation" and chunk.kind in ("function", "method"):
            builder.add_function(chunk)
        else:
            # In chunks mode one excerpt may use at most half of what is left while other
            # candidates are waiting, so a big first chunk can't hide everything after it.
            # Candidates whose lines are all shown already don't count as waiting
            later = candidates[position + 1 :]
            others_waiting = any(builder.adds_new_lines(other, besides=chunk) for other in later)
            builder.add_chunk(chunk, limit=builder.remaining // 2 if others_waiting else builder.remaining)

    return builder.excerpts


class ContextBuilder:
    def __init__(self, repository: RepositoryIndex, question: str, budget: int):
        self.repository = repository
        self.query_terms = set(tokenize(question))
        self.remaining = budget
        self.excerpts: list[Excerpt] = []
        self.used_lines: dict[str, set[int]] = {}
        self._lines: dict[str, list[str]] = {}
        self._indexed_lines: dict[str, set[int]] = {}

    def lines(self, path: str) -> list[str]:
        if path not in self._lines:
            self._lines[path] = split_lines(self.repository.files.get(path, ""))
        return self._lines[path]

    # Only lines that are inside some indexed chunk can be shown. Lines the chunker
    # skipped (e.g. a 3,000-character minified line) must never come back in through
    # a "complete body". Blank lines are never indexed on their own (blank windows are
    # dropped), but they are always safe to show
    def is_indexed(self, path: str, start: int, end: int) -> bool:
        if path not in self._indexed_lines:
            self._indexed_lines[path] = {
                line for chunk in self.repository.chunks if chunk.path == path
                for line in range(chunk.start_line, chunk.end_line + 1)
            }
        lines = self.lines(path)
        return all(line in self._indexed_lines[path] or not lines[line - 1].strip() for line in range(start, end + 1))

    def make_excerpt(self, chunk: Chunk, start: int, end: int, scope: str) -> Excerpt:
        return Excerpt(
            id=f"source-{len(self.excerpts) + 1}",
            path=chunk.path,
            start_line=start,
            end_line=end,
            text="".join(self.lines(chunk.path)[start - 1 : end]),
            symbol=chunk.symbol,
            scope=scope,
            commit_sha=chunk.commit_sha,
        )

    def accept(self, excerpt: Excerpt) -> None:
        self.excerpts.append(excerpt)
        self.remaining -= excerpt.cost()
        self.used_lines.setdefault(excerpt.path, set()).update(range(excerpt.start_line, excerpt.end_line + 1))

    # The longest run of lines in start..end that isn't shown yet, so no line is ever
    # repeated. Overlapping windows share up to 10 lines at their ends, which get trimmed
    def unused_run(self, path: str, start: int, end: int) -> tuple[int, int] | None:
        used = self.used_lines.get(path, set())
        best, run_start = None, None
        for line in range(start, end + 2):
            if line <= end and line not in used:
                run_start = line if run_start is None else run_start
            elif run_start is not None:
                if best is None or line - run_start > best[1] - best[0] + 1:
                    best = (run_start, line - 1)
                run_start = None
        return best

    # Would this chunk still show something new once `besides` has been added?
    def adds_new_lines(self, chunk: Chunk, besides: Chunk) -> bool:
        used = self.used_lines.get(chunk.path, set())
        for line in range(chunk.start_line, chunk.end_line + 1):
            covered_by_besides = besides.path == chunk.path and besides.start_line <= line <= besides.end_line
            if line not in used and not covered_by_besides:
                return True
        return False

    # ---------------------- A plain chunk ---------------------------------------
    def add_chunk(self, chunk: Chunk, limit: int) -> None:
        run = self.unused_run(chunk.path, chunk.start_line, chunk.end_line)
        if run is None:
            return
        start, end = run

        excerpt = self.make_excerpt(chunk, start, end, "excerpt")
        if excerpt.cost() > limit:
            # Too big: cut at a line boundary, never in the middle of a line
            while end >= start and self.make_excerpt(chunk, start, end, "excerpt cut to fit").cost() > limit:
                end -= 1
            if end < start:
                return
            excerpt = self.make_excerpt(chunk, start, end, "excerpt cut to fit")
            if excerpt.cost() < MIN_EXCERPT_TOKENS:
                return

        self.accept(excerpt)

    # ---------------------- A complete function -------------------------------
    def add_function(self, chunk: Chunk) -> None:
        span = self.function_span(chunk)
        if span is None:
            self.add_chunk(chunk, limit=self.remaining)
            return
        start, end, definitions = span

        if self.unused_run(chunk.path, start, end) != (start, end):
            # Part of this function is already shown (e.g. by an earlier window of it)
            self.add_chunk(chunk, limit=self.remaining)
            return

        complete = self.make_excerpt(chunk, start, end, "complete body")
        if complete.cost() <= self.remaining:
            self.accept(complete)
            return

        partial = self.best_partial_window(chunk, start, end, definitions)
        if partial is not None:
            self.accept(partial)

    # The lines of the whole function the chunk belongs to. Long functions were split
    # into windows, so the windows are joined back together. A name defined several
    # times in a row (@overload stubs before the real method) counts as one function,
    # but a getter and setter with another method in between stay separate
    def function_span(self, chunk: Chunk) -> tuple[int, int, list[ast.AST]] | None:
        tree = parse_python(self.repository.files.get(chunk.path, ""))
        nodes = find_definitions(tree, chunk.symbol.split(".")) if chunk.symbol else []
        lines = self.lines(chunk.path)

        groups: list[list[ast.AST]] = []
        for node in nodes:
            node_start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            between = range(groups[-1][-1].end_lineno + 1, node_start) if groups else range(0)
            # Only blank lines or comments between two definitions keep them together
            if groups and all(not lines[line - 1].strip() or lines[line - 1].lstrip().startswith("#") for line in between):
                groups[-1].append(node)
            else:
                groups.append([node])

        for group in groups:
            group_start = min([group[0].lineno] + [decorator.lineno for decorator in group[0].decorator_list])
            group_end = group[-1].end_lineno
            windows = [
                window for window in self.repository.symbols.chunks_by_symbol.get((chunk.symbol, chunk.path), [chunk])
                if window.start_line <= group_end and window.end_line >= group_start
            ]
            if not any(window.start_line <= chunk.start_line <= window.end_line for window in windows):
                continue
            # The windows also contain the comment block right above the function
            start = min(window.start_line for window in windows)
            end = max(window.end_line for window in windows)
            return (start, end, group) if self.is_indexed(chunk.path, start, end) else None
        return None

    # The function doesn't fit, so the most relevant contiguous part of it is shown,
    # starting and ending on whole statements, and clearly labelled as partial.
    # More question words in the window is better. Adding lines can never lose words,
    # so for each start the longest window that fits is the best one with that start.
    # On a tie, a window starting on a statement directly in the function body wins
    # (rather than half way into a loop without its header), then the earlier start,
    # so a guard (if ...: return) comes before the code it protects
    def best_partial_window(self, chunk: Chunk, start: int, end: int, definitions: list[ast.AST]) -> Excerpt | None:
        starts, ends, top_level_starts = self.statement_boundaries(start, end, definitions)
        lines = self.lines(chunk.path)
        scope = "partial body; other lines omitted"

        # Sizes as running totals, so the cost of any window is one subtraction
        running = [0]
        for number in range(start, end + 1):
            running.append(running[-1] + count_tokens(lines[number - 1]))

        def text_cost(first: int, last: int) -> int:
            return running[last - start + 1] - running[first - start]

        # The header is largest with the biggest line numbers, so this is an upper bound
        # Header, footer and separator, with the biggest line numbers (an upper bound), and
        # +1 for the newline added when the last line of a file has none
        empty = Excerpt(f"source-{len(self.excerpts) + 1}", chunk.path, end, end, "", chunk.symbol, scope, chunk.commit_sha)
        header_cost = empty.cost() + 1
        # For each question word, the lines it appears on (sorted)
        # A line is only tokenized if a question word appears in it as plain text, because
        # tokenizing every line of a huge function is the slowest part
        term_lines: dict[str, list[int]] = {}
        for number in range(start, end + 1):
            lower_line = lines[number - 1].lower()
            if any(term in lower_line for term in self.query_terms):
                for term in self.query_terms & set(tokenize(lines[number - 1])):
                    term_lines.setdefault(term, []).append(number)

        best_score, best_range = None, None
        for first in starts:
            # The last statement end that still fits (binary search over the sorted ends)
            index = bisect_right(ends, end)
            low, high = bisect_left(ends, first), index - 1
            last = None
            while low <= high:
                middle = (low + high) // 2
                if header_cost + text_cost(first, ends[middle]) <= self.remaining:
                    last, low = ends[middle], middle + 1
                else:
                    high = middle - 1
            if last is None or header_cost + text_cost(first, last) < MIN_EXCERPT_TOKENS:
                continue

            words = sum(1 for positions in term_lines.values() if bisect_right(positions, last) > bisect_left(positions, first))
            score = (words, first in top_level_starts, -first, last - first)
            if best_score is None or score > best_score:
                best_score, best_range = score, (first, last)

        return self.make_excerpt(chunk, *best_range, scope) if best_range else None

    # Where statements start and end inside the function, plus the whole function.
    # Nested statements are allowed too: if a loop is bigger than the budget, the only
    # way to show part of it is to start inside it. The top-level starts are returned
    # separately so they can win ties
    def statement_boundaries(self, start: int, end: int, definitions: list[ast.AST]) -> tuple[list[int], list[int], set[int]]:
        # Only statement bodies are walked (not every expression node), which matters for
        # functions with tens of thousands of lines
        statements = []
        to_visit = [statement for node in definitions for statement in node.body]
        while to_visit:
            statement = to_visit.pop()
            statements.append(statement)
            for block in ("body", "orelse", "finalbody", "handlers", "cases"):
                to_visit.extend(getattr(statement, block, None) or [])
        top_level = {start} | {node.lineno for node in definitions} | {s.lineno for node in definitions for s in node.body}
        starts = sorted({line for line in top_level | {s.lineno for s in statements} if start <= line <= end})
        ends = sorted({end} | {node.end_lineno for node in definitions} | {s.end_lineno for s in statements if start <= s.end_lineno <= end})
        return starts, ends, top_level
