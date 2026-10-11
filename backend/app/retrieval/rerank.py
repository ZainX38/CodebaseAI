from dataclasses import dataclass, replace
from pathlib import PurePosixPath
import re

from app.indexing.chunking import Chunk


# A deterministic, rule-based reranker. It doesn't use a model: it adjusts the fused
# score with signals RRF can't see. The bonuses are in the same units as RRF scores
# (one first place in one list is 1/61 = 0.016), and were fixed before any evaluation
EXACT_SYMBOL_BONUS = 0.05  # the chunk is a function named in the question
RELATED_SYMBOL_BONUS = 0.02  # a method of a named class, or a helper it calls
PATH_MENTIONED_BONUS = 0.02  # the question names this file, e.g. "in src/requests/auth.py"
TEST_FILE_PENALTY = 0.02  # tests show behaviour but aren't the implementation
DOCUMENTATION_PENALTY = 0.01  # .md/.rst/.txt, unless the question asks about docs

# Only the best windows of a long named function get the exact bonus, otherwise one
# function split into 5 windows could fill the whole top 6 on its own
EXACT_BONUS_WINDOWS_PER_SYMBOL = 2

DOCUMENTATION_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
TEST_DIRECTORIES = {"test", "tests", "testing", "__tests__", "spec", "specs"}


@dataclass(frozen=True)
class Candidate:
    chunk: Chunk
    fused_score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None
    symbol_score: int = 0
    final_score: float = 0.0


def is_test_file(path: str) -> bool:
    file_path = PurePosixPath(path)
    name = file_path.name.lower()
    return (
        bool(TEST_DIRECTORIES & {part.lower() for part in file_path.parts[:-1]})
        or name.startswith("test_")
        or name == "conftest.py"
        # foo_test.py, foo_test.go, foo.test.ts, foo.spec.js
        or re.search(r"(_test|\.test|\.spec)\.[a-z0-9]+$", name) is not None
    )


def is_documentation(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in DOCUMENTATION_EXTENSIONS


# The full path or any end of it counts ("src/requests/auth.py", "requests/auth.py",
# "auth.py"), but "utils.py" must not match inside "test_utils.py", so the name can't
# come directly after a letter, digit, "_", "." or "-", or be followed by one
def is_path_mentioned(path: str, question: str) -> bool:
    parts = PurePosixPath(path.lower()).parts
    for start in range(len(parts)):
        suffix = "/".join(parts[start:])
        if re.search(rf"(?<![\w.\-]){re.escape(suffix)}(?![\w\-])", question):
            return True
    return False


def rerank(candidates: list[Candidate], question: str) -> list[Candidate]:
    lower_question = question.lower()
    asks_about_tests = re.search(r"\btests?\b|\btesting\b", lower_question) is not None
    asks_about_docs = re.search(r"\b(docs?|documentation|readme|changelog)\b", lower_question) is not None

    exact_windows: dict[tuple[str | None, str], int] = {}
    reranked = []
    # Best fused score first, so the exact bonus goes to a long function's best windows
    for candidate in sorted(candidates, key=lambda candidate: -candidate.fused_score):
        chunk = candidate.chunk
        mentioned = is_path_mentioned(chunk.path, lower_question)
        bonus = 0.0

        if candidate.symbol_score >= 25:
            key = (chunk.symbol, chunk.path)
            exact_windows[key] = exact_windows.get(key, 0) + 1
            bonus += EXACT_SYMBOL_BONUS if exact_windows[key] <= EXACT_BONUS_WINDOWS_PER_SYMBOL else RELATED_SYMBOL_BONUS
        elif candidate.symbol_score > 0:
            bonus += RELATED_SYMBOL_BONUS

        if mentioned:
            bonus += PATH_MENTIONED_BONUS

        # Naming a test file is also asking about tests
        if is_test_file(chunk.path) and not asks_about_tests and not mentioned:
            bonus -= TEST_FILE_PENALTY

        if is_documentation(chunk.path) and not asks_about_docs and not mentioned:
            bonus -= DOCUMENTATION_PENALTY

        reranked.append(replace(candidate, final_score=candidate.fused_score + bonus))

    # Ties keep the fused order (sorted() is stable)
    return sorted(reranked, key=lambda candidate: -candidate.final_score)
