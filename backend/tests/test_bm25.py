import math

import pytest

from app.retrieval.bm25 import BM25Index, tokenize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("should_strip_auth", ["should_strip_auth", "should", "strip", "auth"]),
        ("HTTPDigestAuth", ["httpdigestauth", "http", "digest", "auth"]),
        ("JSONDecodeError", ["jsondecodeerror", "json", "decode", "error"]),
        ("handle_401", ["handle_401", "handle", "401"]),
        ("How does the session work?", ["session", "work"]),  # stop words removed
        ("Session.send", ["session", "send"]),
    ],
)
def test_tokenize_keeps_whole_identifiers_and_their_parts(text, expected):
    assert tokenize(text) == expected


def test_exact_identifier_ranks_first():
    index = BM25Index({
        "strip": "def should_strip_auth(self, old_url, new_url):\n    return True",
        "rebuild": "def rebuild_auth(self, prepared_request, response):\n    pass",
        "other": "def merge_setting(request_setting, session_setting):\n    pass",
    })

    assert [doc_id for doc_id, _ in index.search("When does should_strip_auth return True?", top_k=3)][0] == "strip"


def test_words_from_a_split_identifier_still_match():
    index = BM25Index({"strip": "def should_strip_auth(self): pass", "other": "def merge_setting(): pass"})

    assert index.search("strip the auth header", top_k=2)[0][0] == "strip"


def test_rare_words_count_more_than_common_ones():
    documents = {f"common{number}": "def request(): return response" for number in range(10)}
    documents["rare"] = "def request(): return digest"
    index = BM25Index(documents)

    scores = index.score("request digest")

    assert max(scores, key=scores.get) == "rare"


def test_score_matches_the_bm25_formula_by_hand():
    # "short" has 1 token, "long" has 4, so the average length is 2.5
    index = BM25Index({"short": "auth", "long": "auth other words here"})

    idf = math.log((2 - 2 + 0.5) / (2 + 0.5) + 1)  # "auth" is in both documents
    short = idf * 1 * 2.5 / (1 + 1.5 * (1 - 0.75 + 0.75 * 1 / 2.5))
    long = idf * 1 * 2.5 / (1 + 1.5 * (1 - 0.75 + 0.75 * 4 / 2.5))

    scores = index.score("auth")
    assert scores["short"] == pytest.approx(short)
    assert scores["long"] == pytest.approx(long)


def test_b_controls_the_length_penalty():
    documents = {"short": "auth", "long": "auth other words here"}

    without_penalty = BM25Index(documents, b=0).score("auth")
    with_penalty = BM25Index(documents, b=0.75).score("auth")

    assert without_penalty["short"] == pytest.approx(without_penalty["long"])
    assert with_penalty["short"] > with_penalty["long"]


def test_repeated_words_saturate_at_k1_plus_one():
    index = BM25Index({"once": "auth", "many": "auth " * 50, "none": "nothing"})
    scores = index.score("auth")

    # However often a word repeats, it can never add more than (k1 + 1) x IDF
    assert scores["once"] < scores["many"] < (1.5 + 1) * index.idf["auth"]


def test_no_match_and_empty_index():
    assert BM25Index({"a": "def run(): pass"}).search("unrelated words", top_k=5) == []
    assert BM25Index({}).search("anything", top_k=5) == []


def test_idf_is_always_positive():
    # A word in every document still has a small positive weight because of the "+ 1"
    index = BM25Index({"a": "auth", "b": "auth"})

    assert all(value > 0 and math.isfinite(value) for value in index.idf.values())
