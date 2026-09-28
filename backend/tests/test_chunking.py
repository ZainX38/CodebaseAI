import pytest

from app.indexing import chunking
from app.indexing.chunking import chunk_file, chunk_repository, get_language, split_lines, window_chunks


SHA = "a" * 40


def numbered_lines(count: int) -> str:
    return "".join(f"line {number}\n" for number in range(1, count + 1))


# Every chunk must be an exact slice of the original file, otherwise citations
# would point at lines that say something different
def assert_matches_source(chunks, content):
    lines = split_lines(content)
    for chunk in chunks:
        assert chunk.text == "".join(lines[chunk.start_line - 1 : chunk.end_line])


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("src/app.py", "python"),
        ("frontend/app/page.tsx", "typescript"),
        ("lib/api.ts", "typescript"),
        ("cmd/main.go", "go"),
        ("Dockerfile", "dockerfile"),
        ("README.md", "markdown"),
        ("LICENSE", "text"),
    ],
)
def test_language_comes_from_the_file_name(path, language):
    assert get_language(path) == language


def test_split_lines_only_counts_real_line_endings():
    # \x0c (form feed) appears in some Python files. splitlines() would treat it as
    # a new line and every line number after it would be off by one
    assert split_lines("a = 1\x0c\nb = 2\r\nc = 3") == ["a = 1\x0c\n", "b = 2\r\n", "c = 3"]


def test_short_file_is_one_window_with_all_its_metadata():
    [chunk] = chunk_file("lib/api.ts", "export const a = 1;\nexport const b = 2;\n", SHA)

    assert (chunk.start_line, chunk.end_line) == (1, 2)
    assert (chunk.language, chunk.symbol, chunk.kind, chunk.commit_sha) == ("typescript", None, "window", SHA)


def test_long_file_is_split_into_overlapping_windows():
    content = numbered_lines(130)

    chunks = chunk_file("notes.txt", content, SHA)

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 60), (51, 110), (101, 130)]
    assert_matches_source(chunks, content)


def test_windows_stop_at_the_character_limit_and_still_move_forward(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CHUNK_CHARS", 100)
    content = "x" * 39 + "\n"  # 40 characters per line, so 2 lines per window
    content *= 6

    chunks = chunk_file("data.txt", content, SHA)

    # With a 10-line overlap these windows would only move one line at a time
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]


def test_lines_that_are_too_long_are_skipped_without_changing_line_numbers(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_LINE_CHARS", 50)
    content = numbered_lines(5) + "y" * 100 + "\n" + numbered_lines(3)

    chunks = chunk_file("bundle.js", content, SHA)

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 5), (7, 9)]
    assert_matches_source(chunks, content)


def test_blank_windows_are_dropped():
    assert chunk_file("empty.py", "\n\n   \n", SHA) == []


def test_window_chunks_can_cover_part_of_a_file_and_keep_a_symbol():
    lines = split_lines(numbered_lines(10))

    [chunk] = window_chunks(lines, "a.py", SHA, first_line=3, last_line=6, symbol="run", kind="function")

    assert (chunk.start_line, chunk.end_line, chunk.symbol, chunk.kind) == (3, 6, "run", "function")


def test_chunk_ids_are_stable_and_unique():
    files = {"a.txt": numbered_lines(130), "b.txt": numbered_lines(5)}

    first = chunk_repository(files, SHA)
    second = chunk_repository(files, SHA)

    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert len({chunk.id for chunk in first}) == len(first)
    assert {chunk.id for chunk in chunk_repository(files, "b" * 40)}.isdisjoint(chunk.id for chunk in first)


PYTHON_SOURCE = '''"""Example module."""
import os

TIMEOUT = 5


# Adds two numbers
@cache
@trace(level=2)
def add(a, b):
    return a + b


class Session:
    """A session."""

    retries = 3

    def __init__(self):
        self.headers = {}

    @property
    def is_open(self):
        return True

    class Adapter:
        name = "http"

        def send(self, request):
            return request

    max_redirects = 30


async def fetch(url):
    return url


if __name__ == "__main__":
    add(1, 2)
'''


def spans(chunks):
    return [(chunk.start_line, chunk.end_line, chunk.symbol, chunk.kind) for chunk in chunks]


def test_python_functions_classes_and_methods_get_their_own_chunks():
    chunks = chunk_file("src/session.py", PYTHON_SOURCE, SHA)

    assert spans(chunks) == [
        (1, 6, None, "window"),  # module docstring, imports and constants
        (7, 11, "add", "function"),  # leading comment + both decorators
        (14, 18, "Session", "class"),  # class line, docstring and attributes
        (19, 20, "Session.__init__", "method"),
        (22, 24, "Session.is_open", "method"),
        (26, 28, "Session.Adapter", "class"),
        (29, 30, "Session.Adapter.send", "method"),
        (31, 34, None, "window"),  # attribute after the last method
        (35, 36, "fetch", "function"),
        (37, 40, None, "window"),
    ]
    assert_matches_source(chunks, PYTHON_SOURCE)
    assert {chunk.language for chunk in chunks} == {"python"}


def test_python_chunks_never_share_a_line():
    chunks = chunk_file("src/session.py", PYTHON_SOURCE, SHA)
    used_lines = [line for chunk in chunks for line in range(chunk.start_line, chunk.end_line + 1)]

    assert len(used_lines) == len(set(used_lines))


def test_long_python_function_is_split_into_windows_that_keep_its_name():
    # About 6,000 characters, so it is over MAX_CHUNK_CHARS. Shorter functions stay in one
    # chunk even if they are longer than WINDOW_LINES, because a complete function is better
    body = "".join(f"    value_{number:03} = compute(number={number}, scale=2)\n" for number in range(150))
    content = "def build():\n" + body

    chunks = chunk_file("build.py", content, SHA)

    assert len(chunks) == 3
    assert {(chunk.symbol, chunk.kind) for chunk in chunks} == {("build", "function")}
    assert (chunks[0].start_line, chunks[-1].end_line) == (1, 151)
    assert_matches_source(chunks, content)


def test_python_that_does_not_parse_falls_back_to_windows():
    content = "print 'python 2'\n" + numbered_lines(70)

    chunks = chunk_file("legacy.py", content, SHA)

    assert {(chunk.symbol, chunk.kind) for chunk in chunks} == {(None, "window")}
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 60), (51, 71)]


def test_python_line_numbers_survive_form_feeds():
    content = "import os\n\x0c\ndef first():\n    return 1\n"

    [_, function] = chunk_file("a.py", content, SHA)

    assert (function.symbol, function.start_line, function.end_line) == ("first", 3, 4)
    assert function.text == "def first():\n    return 1\n"


def test_one_line_class_is_one_chunk():
    chunks = chunk_file("a.py", "class A: x = 1\n", SHA)

    assert spans(chunks) == [(1, 1, "A", "class")]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_python_with_windows_or_old_mac_line_endings(newline):
    content = newline.join(["import os", "", "def first():", "    return 1", ""])

    chunks = chunk_file("a.py", content, SHA)

    assert spans(chunks) == [(1, 2, None, "window"), (3, 4, "first", "function")]
    assert_matches_source(chunks, content)


def test_python_with_a_byte_order_mark_still_gets_symbols():
    content = "\ufeffdef first():\n    return 1\n"

    [chunk] = chunk_file("a.py", content, SHA)

    assert (chunk.symbol, chunk.start_line) == ("first", 1)
    assert chunk.text == content  # the BOM stays, so the text is still the exact file


def test_parenthesised_decorator_starts_at_the_at_sign():
    content = "x = 1\n@(\n    cache\n)\ndef first():\n    return 1\n"

    chunks = chunk_file("a.py", content, SHA)

    assert spans(chunks) == [(1, 1, None, "window"), (2, 6, "first", "function")]


def test_comment_above_the_first_method_belongs_to_the_method():
    content = "class A:\n    x = 1\n    # Explains f\n    def f(self):\n        pass\n"

    assert spans(chunk_file("a.py", content, SHA)) == [(1, 2, "A", "class"), (3, 5, "A.f", "method")]


def test_comments_are_only_attached_at_the_same_indentation():
    content = "class A:\n    def f(self):\n        pass\n    # end of A\ndef g():\n    pass\n"

    chunks = chunk_file("a.py", content, SHA)

    assert ("g", 5) in [(chunk.symbol, chunk.start_line) for chunk in chunks]


def test_licence_header_is_not_attached_to_the_first_function():
    content = "#!/usr/bin/env python\n# Licence: MIT\ndef main():\n    pass\n"

    chunks = chunk_file("a.py", content, SHA)

    assert spans(chunks) == [(1, 2, None, "window"), (3, 4, "main", "function")]


def test_definitions_inside_if_and_try_blocks_are_symbols():
    content = (
        "if TYPE_CHECKING:\n"
        "    def typed():\n"
        "        pass\n"
        "try:\n"
        "    import json\n"
        "except ImportError:\n"
        "    def loads(text):\n"
        "        return text\n"
    )

    symbols = [chunk.symbol for chunk in chunk_file("compat.py", content, SHA) if chunk.symbol]

    assert symbols == ["typed", "loads"]


def test_methods_inside_if_blocks_are_not_also_in_the_class_header():
    content = "class A:\n    if True:\n        def f(self):\n            pass\n"

    chunks = chunk_file("a.py", content, SHA)

    assert spans(chunks) == [(1, 2, "A", "class"), (3, 4, "A.f", "method")]


def test_oversized_symbol_windows_overlap_each_other_but_never_another_symbol():
    body = "".join(f"    value_{number} = compute(number={number}, scale=2)\n" for number in range(150))
    content = "def build():\n" + body + "def after():\n    pass\n"

    chunks = chunk_file("build.py", content, SHA)
    build_windows = [chunk for chunk in chunks if chunk.symbol == "build"]
    [after] = [chunk for chunk in chunks if chunk.symbol == "after"]

    # Windows of the same long function overlap on purpose, like normal windows
    assert build_windows[0].end_line >= build_windows[1].start_line
    assert all(window.end_line < after.start_line for window in build_windows)
    assert len({chunk.id for chunk in chunks}) == len(chunks)
