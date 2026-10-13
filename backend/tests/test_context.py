import pytest

from app import config
from app.indexing import chunking
from app.indexing.chunking import chunk_repository, split_lines
from app.rag.context import (
    MIN_EXCERPT_TOKENS,
    TEMPLATE_RESERVE_TOKENS,
    build_context,
    count_tokens,
    format_context,
    get_context_budget,
)
from app.retrieval.retriever import RepositoryIndex


SHA = "a" * 40


def make_repository(files):
    return RepositoryIndex.build("ns", chunk_repository(files, SHA), files)


def chunk_named(repository, symbol, index=0):
    return [chunk for chunk in repository.chunks if chunk.symbol == symbol][index]


# The rule every excerpt must follow: its text is exactly those lines of the file
def assert_exact(excerpts, files):
    for excerpt in excerpts:
        lines = split_lines(files[excerpt.path])
        assert excerpt.text == "".join(lines[excerpt.start_line - 1 : excerpt.end_line])


def test_tokens_are_counted_as_utf8_bytes():
    assert count_tokens("abc") == 3
    assert count_tokens("é") == 2  # always at least as many as the real token count


def test_budget_leaves_room_for_the_answer_prompt_question_and_template():
    budget = get_context_budget("system prompt", "question?", context_tokens=8192, max_output_tokens=700)

    assert budget == 8192 - 700 - len("system prompt") - len("question?") - TEMPLATE_RESERVE_TOKENS


def test_a_question_that_leaves_no_room_is_refused():
    with pytest.raises(ValueError, match="too long"):
        get_context_budget("s", "q" * 8000, context_tokens=config.CONTEXT_TOKENS)


# ---------------------- Implementation mode ------------------------------------
LONG_BODY = "".join(f"    value_{number} = compute(number={number}, scale=2)\n" for number in range(150))
FILES = {
    "src/build.py": (
        "import os\n\n\n"
        "@decorate\n"
        "def build(config):\n"
        '    """Builds everything."""\n'
        + LONG_BODY
        + "    if config.retry_on_failure:\n"
        "        return retry_build(config)\n"
        "    return value_0\n"
    ),
    "src/small.py": "def tiny_helper():\n    return 1\n",
    "README.md": "# Build\n" + "Some text about building.\n" * 40,
}


def test_a_function_split_into_windows_is_given_as_one_complete_body():
    repository = make_repository(FILES)
    windows = [chunk for chunk in repository.chunks if chunk.symbol == "build"]
    assert len(windows) > 1  # the function really was split by the chunker

    [excerpt] = build_context([windows[1]], repository, "How does build work?", budget=20_000)

    assert (excerpt.start_line, excerpt.end_line) == (4, len(split_lines(FILES["src/build.py"])))
    assert excerpt.scope == "complete body"
    assert excerpt.text.startswith("@decorate\ndef build(config):")
    assert_exact([excerpt], FILES)


def test_an_oversized_body_becomes_a_labelled_partial_window_about_the_question():
    repository = make_repository(FILES)
    budget = 900

    [excerpt] = build_context([chunk_named(repository, "build")], repository, "When does build retry on failure?", budget)

    assert excerpt.scope == "partial body; other lines omitted"
    assert "retry_build(config)" in excerpt.text  # the part the question is about
    assert excerpt.cost() <= budget
    assert "[source-1] src/build.py:" in excerpt.header() and "(build; partial body" in excerpt.header()
    assert_exact([excerpt], FILES)


def test_every_window_of_a_function_only_produces_one_excerpt():
    repository = make_repository(FILES)
    windows = [chunk for chunk in repository.chunks if chunk.symbol == "build"]

    excerpts = build_context(windows + [chunk_named(repository, "tiny_helper")], repository, "q", budget=20_000)

    assert [excerpt.symbol for excerpt in excerpts] == ["build", "tiny_helper"]
    assert [excerpt.id for excerpt in excerpts] == ["source-1", "source-2"]


def test_lines_the_index_left_out_are_never_brought_back(monkeypatch):
    # A 100-character line is "too long" here, so the chunker skips it.
    # The complete body would include it, so the original chunk is used instead
    monkeypatch.setattr(chunking, "MAX_LINE_CHARS", 60)
    files = {"src/a.py": "def run_task():\n    x = 1\n    data = '" + "y" * 100 + "'\n    return x\n"}
    repository = make_repository(files)

    excerpts = build_context([chunk_named(repository, "run_task")], repository, "q", budget=5000)

    assert all("y" * 100 not in excerpt.text for excerpt in excerpts)
    assert_exact(excerpts, files)


# ---------------------- Chunks mode --------------------------------------------
def test_chunks_mode_uses_the_chunks_themselves():
    repository = make_repository(FILES)
    window = chunk_named(repository, "build")

    [excerpt] = build_context([window], repository, "q", budget=20_000, mode="chunks")

    assert (excerpt.start_line, excerpt.end_line) == (window.start_line, window.end_line)
    assert excerpt.scope == "excerpt"


def test_a_big_first_chunk_cannot_use_the_whole_budget():
    repository = make_repository(FILES)
    readme = [chunk for chunk in repository.chunks if chunk.path == "README.md"][0]
    budget = 900

    excerpts = build_context([readme, chunk_named(repository, "tiny_helper")], repository, "q", budget, mode="chunks")

    assert [excerpt.path for excerpt in excerpts] == ["README.md", "src/small.py"]
    assert excerpts[0].scope == "excerpt cut to fit" and excerpts[0].cost() <= budget // 2
    assert sum(excerpt.cost() for excerpt in excerpts) <= budget
    assert_exact(excerpts, FILES)


def test_overlapping_windows_are_trimmed_so_lines_are_not_repeated():
    files = {"notes.txt": "".join(f"line {number}\n" for number in range(1, 131))}
    repository = make_repository(files)
    first, second = repository.chunks[:2]  # lines 1-60 and 51-110

    excerpts = build_context([first, second], repository, "q", budget=50_000, mode="chunks")

    assert [(excerpt.start_line, excerpt.end_line) for excerpt in excerpts] == [(1, 60), (61, 110)]


def test_the_budget_is_never_exceeded():
    files = {f"src/f{number}.py": f"def function_{number}():\n" + "    x = 1\n" * 30 for number in range(20)}
    repository = make_repository(files)

    for mode in ("chunks", "implementation"):
        for budget in (300, 1000, 2500):
            excerpts = build_context(repository.chunks, repository, "q", budget, mode=mode)
            assert sum(excerpt.cost() for excerpt in excerpts) <= budget
            assert all(excerpt.cost() >= MIN_EXCERPT_TOKENS or excerpt.scope in ("excerpt", "complete body") for excerpt in excerpts)


def test_the_budget_is_never_exceeded_when_functions_must_be_cut():
    # 200-line functions don't fit in these budgets, so the partial-window path is used
    body = "".join(f"    step_{number}(item)\n" for number in range(200))
    files = {f"src/f{number}.py": f"def function_{number}(item):\n{body}" for number in range(3)}
    repository = make_repository(files)
    functions = [chunk for chunk in repository.chunks if chunk.kind == "function"]

    for budget in (300, 1000, 2500):
        excerpts = build_context(functions, repository, "step_150", budget)
        assert excerpts and excerpts[0].scope == "partial body; other lines omitted"
        assert sum(excerpt.cost() for excerpt in excerpts) <= budget
        assert_exact(excerpts, files)


def test_overloads_and_the_real_method_are_one_complete_body():
    files = {"src/models.py": (
        "class Response:\n"
        "    @overload\n"
        "    def read(self, size: int) -> bytes: ...\n"
        "\n"
        "    @overload\n"
        "    def read(self, size: None) -> str: ...\n"
        "\n"
        "    def read(self, size=None):\n"
        "        return self.raw.read(size)\n"
    )}
    repository = make_repository(files)
    stub = chunk_named(repository, "Response.read", index=0)

    [excerpt] = build_context([stub], repository, "q", budget=5000)

    # Even when retrieval returned only the first stub, the real implementation is shown
    assert (excerpt.start_line, excerpt.end_line, excerpt.scope) == (2, 9, "complete body")
    assert "return self.raw.read(size)" in excerpt.text


def test_a_getter_and_setter_with_another_method_between_are_not_merged():
    files = {"src/a.py": (
        "class A:\n"
        "    @property\n"
        "    def x(self):\n"
        "        return self._x\n"
        "    def other(self):\n"
        "        return 'unrelated'\n"
        "    @x.setter\n"
        "    def x(self, value):\n"
        "        self._x = value\n"
    )}
    repository = make_repository(files)
    getter = chunk_named(repository, "A.x", index=0)

    [excerpt] = build_context([getter], repository, "q", budget=5000)

    assert (excerpt.start_line, excerpt.end_line) == (2, 4)
    assert "unrelated" not in excerpt.text


def test_a_partial_window_prefers_to_start_on_a_top_level_statement():
    body = (
        "    if not items:\n        return check_empty_items(items)\n"
        "    for item in items:\n" + "".join(f"        step_{number}(item)\n" for number in range(60))
        + "    return total\n"
    )
    files = {"src/loop.py": "def process(items):\n" + body}
    repository = make_repository(files)

    # Both "def process" (line 1) and "return check_empty_items" (line 3) could start a
    # window with the same question words; the top-level start wins
    [excerpt] = build_context([chunk_named(repository, "process")], repository, "check_empty_items", budget=400)

    assert excerpt.scope == "partial body; other lines omitted"
    assert excerpt.start_line == 1
    assert "check_empty_items" in excerpt.text


def test_a_loop_bigger_than_the_budget_can_still_be_shown_in_part():
    body = "    for item in items:\n" + "".join(f"        step_{number}(item)\n" for number in range(200))
    files = {"src/loop.py": "def process(items):\n" + body}
    repository = make_repository(files)
    statement_lines = {1, 2} | set(range(3, 203))

    [excerpt] = build_context([chunk_named(repository, "process")], repository, "step_150", budget=400)

    assert "step_150" in excerpt.text
    assert excerpt.start_line in statement_lines and excerpt.cost() <= 400
    assert_exact([excerpt], files)


def test_no_line_is_shown_twice_with_mixed_candidates():
    files = {"notes.txt": "".join(f"line {number}\n" for number in range(1, 200))}
    repository = make_repository(files)
    chunks = repository.chunks

    excerpts = build_context([chunks[1], chunks[0], chunks[2], chunks[1]], repository, "q", budget=50_000, mode="chunks")

    shown = [line for excerpt in excerpts for line in range(excerpt.start_line, excerpt.end_line + 1)]
    assert len(shown) == len(set(shown))


def test_a_chunk_that_just_fits_is_not_cut():
    # Big enough to be above MIN_EXCERPT_TOKENS, so it is really tried at the exact budget
    files = {"src/small.py": "def tiny_helper():\n" + "    value = compute_something_useful()\n" * 5}
    repository = make_repository(files)
    [chunk] = repository.chunks
    exact_cost = build_context([chunk], repository, "q", budget=5000, mode="chunks")[0].cost()

    [excerpt] = build_context([chunk], repository, "q", budget=exact_cost, mode="chunks")

    assert (excerpt.scope, excerpt.end_line) == ("excerpt", 6)


def test_the_last_useful_chunk_gets_the_whole_remaining_budget():
    files = {"README.md": "# Build\n" + "Some text about building.\n" * 40, "src/small.py": "def tiny_helper():\n    return 1\n"}
    repository = make_repository(files)
    readme = [chunk for chunk in repository.chunks if chunk.path == "README.md"][0]

    # The second candidate is the same chunk again, so nothing is really waiting after it
    [excerpt] = build_context([readme, readme], repository, "q", budget=900, mode="chunks")

    assert excerpt.cost() > 900 // 2


def test_partial_windows_are_fast_on_huge_functions():
    import time

    body = "".join(f"    value_{number} = step(number={number})\n" for number in range(20_000))
    files = {"src/huge.py": "def huge():\n" + body}
    repository = make_repository(files)

    started = time.perf_counter()
    excerpts = build_context([chunk_named(repository, "huge")], repository, "value_19999 step", budget=6000)

    assert excerpts[0].scope == "partial body; other lines omitted"
    # About 1.5 s on my laptop, mostly tokenizing 20,000 lines that all contain "step".
    # Before the binary search, windows were re-read for every end and took far longer
    assert time.perf_counter() - started < 4


def test_format_context_puts_each_header_above_its_code():
    repository = make_repository(FILES)
    excerpts = build_context([chunk_named(repository, "tiny_helper")], repository, "q", budget=5000)

    assert format_context(excerpts) == "[source-1] src/small.py:1-2 (tiny_helper; complete body)\ndef tiny_helper():\n    return 1\n"
