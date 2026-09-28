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
