import pytest

from app.rag.citations import citation_to_dict, normalise_citation_id, validate_citations
from app.rag.context import Excerpt


def excerpt(number, path="src/app.py"):
    return Excerpt(f"source-{number}", path, number * 10, number * 10 + 5, "code\n", "run", "complete body", "a" * 40)


EXCERPTS = [excerpt(1), excerpt(2), excerpt(3)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("source-1", "source-1"),
        ("[source-1]", "source-1"),  # the bracket format models copy from the excerpt headers
        ("(source-2)", "source-2"),
        ("`source-3`", "source-3"),
        ('"source-1"', "source-1"),
        ("  Source-1 ", "source-1"),
        ("source 2", "source-2"),
        ("source_2", "source-2"),
        ("source-01", "source-1"),
        ("[[source-1]]", "source-1"),
        # deepseek-coder-v2 copied the header format from the prompt on a dev question
        ("source-1 | code", "source-1"),
        ("[source-2 | 9f3a01c2]", "source-2"),
    ],
)
def test_harmless_formatting_is_removed(raw, expected):
    assert normalise_citation_id(raw) == expected


@pytest.mark.parametrize("raw", ["1", "src/app.py", "source-", "source-1, source-2", "sources-1", None, 1, ["source-1"]])
def test_anything_else_is_not_guessed(raw):
    assert normalise_citation_id(raw) is None


def test_valid_bracketed_and_unknown_citations():
    check = validate_citations(["source-1", "[source-2]", "source-9", "src/other.py"], EXCERPTS)

    assert [item.id for item in check.valid] == ["source-1", "source-2"]
    assert check.rejected == ["source-9", "src/other.py"]  # not one of the excerpts sent
    assert check.normalised == ["[source-2]"]  # accepted, but only after cleaning


def test_repeated_citations_count_once_and_keep_their_order():
    check = validate_citations(["source-3", "source-1", "[source-3]"], EXCERPTS)

    assert [item.id for item in check.valid] == ["source-3", "source-1"]


def test_citations_that_are_not_a_list_are_rejected():
    assert validate_citations("source-1", EXCERPTS).rejected == ["source-1"]
    assert validate_citations(None, EXCERPTS).rejected == []
    assert validate_citations([], EXCERPTS).valid == []


def test_a_citation_can_only_point_at_excerpts_that_were_sent():
    # source-1 exists in general, but this answer was only given source-2
    check = validate_citations(["source-1"], [excerpt(2)])

    assert check.valid == [] and check.rejected == ["source-1"]


def test_citation_link_points_at_the_indexed_commit_and_lines():
    data = citation_to_dict(excerpt(1, path="docs/my file#1.md"), "ZainX38", "demo")

    assert data["url"] == f"https://github.com/ZainX38/demo/blob/{'a' * 40}/docs/my%20file%231.md#L10-L15"
    assert (data["start_line"], data["end_line"], data["commit_sha"]) == (10, 15, "a" * 40)
