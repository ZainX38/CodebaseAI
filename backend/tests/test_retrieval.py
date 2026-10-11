import math

from app.indexing.chunking import chunk_file, chunk_repository
from app.retrieval import retriever
import pytest

from app.retrieval.rerank import Candidate, is_documentation, is_path_mentioned, is_test_file, rerank
from app.retrieval.retriever import RepositoryIndex, get_symbol_ranking, load_repository_index, retrieve
from app.retrieval.symbols import SymbolMatch, SymbolTable
from app.ingestion.snapshot import RepositorySnapshot


SHA = "a" * 40


# ---------------------- Rerank -------------------------------------------------
def candidate(path, fused=0.03, symbol_score=0, content="x = 1\n"):
    [chunk] = chunk_file(path, content, SHA)
    return Candidate(chunk, fused_score=fused, symbol_score=symbol_score)


def test_exact_symbol_match_moves_up():
    vector_favourite = candidate("src/a.py", fused=0.033)
    named = candidate("src/b.py", fused=0.016, symbol_score=30)

    assert [c.chunk.path for c in rerank([vector_favourite, named], "q")] == ["src/b.py", "src/a.py"]


def test_tests_and_docs_go_down_unless_the_question_asks_for_them():
    implementation = candidate("src/sessions.py", fused=0.030)
    test = candidate("tests/test_sessions.py", fused=0.031)
    docs = candidate("docs/user/advanced.rst", fused=0.031)

    assert rerank([test, docs, implementation], "How does it work?")[0].chunk.path == "src/sessions.py"
    assert rerank([test, docs, implementation], "Which tests cover this?")[0].chunk.path == "tests/test_sessions.py"


def test_a_file_named_in_the_question_moves_up():
    other = candidate("src/models.py", fused=0.030)
    named = candidate("src/requests/auth.py", fused=0.025)

    assert rerank([other, named], "What is set in src/requests/auth.py?")[0].chunk.path == "src/requests/auth.py"


@pytest.mark.parametrize(
    "path",
    ["tests/test_api.py", "src/test_utils.py", "pkg/test/x.py", "src/foo_test.py", "cmd/server_test.go",
     "web/app.test.ts", "web/app.spec.js", "conftest.py", "src/__tests__/a.js", "lib/testing/helpers.py"],
)
def test_is_test_file(path):
    assert is_test_file(path)


@pytest.mark.parametrize("path", ["src/attestation.py", "src/contest.py", "src/latest_version.py"])
def test_is_not_test_file(path):
    assert not is_test_file(path)


def test_documentation_is_detected_by_extension_not_by_language():
    # get_language() returns "text" for any unknown extension, e.g. Lua code
    assert is_documentation("README.md") and is_documentation("docs/a.rst") and is_documentation("NOTES.txt")
    assert not is_documentation("src/game.lua") and not is_documentation("LICENSE")


def test_a_file_name_inside_a_longer_name_is_not_a_mention():
    assert is_path_mentioned("src/pkg/test_utils.py", "what does test_utils.py check?")
    assert not is_path_mentioned("src/pkg/utils.py", "what does test_utils.py check?")
    assert is_path_mentioned("src/requests/auth.py", "what is set in src/requests/auth.py?")
    assert is_path_mentioned("src/requests/auth.py", "what is set in requests/auth.py?")
    assert not is_path_mentioned("src/requests/auth.py", "what is set in myrequests/oauth.py?")


def test_a_named_test_file_is_not_penalised():
    utils = candidate("src/pkg/utils.py", fused=0.030)
    named_test = candidate("tests/test_utils.py", fused=0.025)

    assert rerank([utils, named_test], "What does test_utils.py check?")[0].chunk.path == "tests/test_utils.py"


def test_only_two_windows_of_one_long_function_get_the_exact_bonus():
    body = "".join(f"    value_{number} = compute(number={number}, scale=2)\n" for number in range(300))
    windows = chunk_file("build.py", "def build():\n" + body, SHA)
    named_windows = [Candidate(window, fused_score=0.02 - index * 0.001, symbol_score=25) for index, window in enumerate(windows)]
    helper = candidate("src/helper.py", fused=0.03, symbol_score=10)

    ranked = rerank(named_windows + [helper], "q")

    assert len(windows) > 3
    assert [c.chunk.id for c in ranked[:2]] == [windows[0].id, windows[1].id]
    assert ranked[2].chunk.path == "src/helper.py"  # the helper isn't pushed out by window 3, 4, 5...


# ---------------------- Symbol ranking ------------------------------------------
def test_symbol_ranking_includes_every_window_of_a_long_function():
    body = "".join(f"    value_{number} = compute(number={number}, scale=2)\n" for number in range(150))
    chunks = chunk_file("build.py", "def build():\n" + body, SHA)

    ranking, scores = get_symbol_ranking([SymbolMatch("build", "build.py", 25, "named")], SymbolTable.build(chunks, {}))

    assert ranking == [chunk.id for chunk in chunks] and len(ranking) == 3
    assert set(scores.values()) == {25}


# ---------------------- Full retrieval with fakes -------------------------------
FILES = {
    "src/auth.py": "def strip_authorization(headers):\n    headers.pop('Authorization')\n",
    "src/cookies.py": "def merge_cookies(jar, cookies):\n    return jar\n",
    "tests/test_auth.py": "def test_strip_authorization():\n    assert strip_authorization({}) == None\n",
    "docs/auth.md": "# Authentication\nRequests strips the Authorization header on redirects.\n",
}


# Embeds text as word counts over a tiny vocabulary, so cosine similarity is predictable
class FakeProvider:
    VOCABULARY = ["authorization", "strip", "cookies", "merge", "redirects", "header"]

    async def embed(self, texts):
        return [[float(text.lower().count(word)) + 0.01 for word in self.VOCABULARY] for text in texts]


# An in-memory stand-in for Pinecone: exact cosine similarity over every vector
class InMemoryVectorStore:
    def __init__(self, vectors):
        self.vectors = vectors

    def query(self, namespace, vector, top_k):
        def cosine(a, b):
            return sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b))

        scored = sorted(((chunk_id, cosine(vector, values)) for chunk_id, values in self.vectors.items()), key=lambda item: -item[1])
        return scored[:top_k]


async def make_index():
    chunks = chunk_repository(FILES, SHA)
    provider = FakeProvider()
    vectors = dict(zip([chunk.id for chunk in chunks], await provider.embed([chunk.text for chunk in chunks])))
    # A stale vector from an older chunking must be ignored
    vectors["stale-id"] = [1.0] * len(FakeProvider.VOCABULARY)
    return RepositoryIndex.build("ns", chunks, FILES), provider, InMemoryVectorStore(vectors)


async def test_vector_mode_is_plain_cosine_order():
    repository, provider, vector_store = await make_index()

    result = await retrieve("strip the authorization header on redirects", repository, provider, vector_store, mode="vector")

    assert result.mode == "vector"
    assert result.keyword_hits == [] and result.symbol_matches == []
    assert "stale-id" not in [chunk_id for chunk_id, _ in result.vector_hits]
    scores = [c.final_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


async def test_hybrid_mode_puts_the_named_implementation_first():
    repository, provider, vector_store = await make_index()

    result = await retrieve("How does strip_authorization work?", repository, provider, vector_store, mode="hybrid")

    first = result.candidates[0]
    assert (first.chunk.path, first.chunk.symbol) == ("src/auth.py", "strip_authorization")
    assert first.symbol_score == 25 and first.vector_rank is not None and first.keyword_rank is not None
    assert len(result.candidates) <= retriever.FINAL_RESULTS
    # Everything that was found is kept for the evaluation
    assert result.to_dict()["candidates"][0]["symbol"] == "strip_authorization"


async def test_repository_index_is_loaded_once_and_cached(index_store):
    chunks = chunk_repository(FILES, SHA)
    index_store.save_snapshot("ns-cache", RepositorySnapshot("o", "r", "main", SHA, FILES, {}), chunks)
    retriever._repository_cache.clear()

    first = await load_repository_index(index_store, "ns-cache")
    second = await load_repository_index(index_store, "ns-cache")

    assert first is second
    assert first.chunks == chunks and first.files == FILES


async def test_an_empty_namespace_is_not_cached(index_store):
    retriever._repository_cache.clear()

    empty = await load_repository_index(index_store, "not-ready")

    assert empty.chunks == [] and "not-ready" not in retriever._repository_cache


async def test_very_long_questions_are_refused():
    repository, provider, vector_store = await make_index()

    with pytest.raises(ValueError, match="at most"):
        await retrieve("word " * 500, repository, provider, vector_store)


async def test_a_function_only_found_by_the_symbol_lookup_still_reaches_the_reranker(monkeypatch):
    # 20 chunks that vector search and BM25 both rank at the top, and one named function
    # that only the symbol lookup finds. In RRF the named function scores 1/61, below
    # every noise chunk (2 lists each), so without the fix it never reaches the reranker
    files = {f"src/noise_{number}.py": f"def noise_{number}():\n    return 'authorization header'\n" for number in range(20)}
    files["src/named.py"] = "def very_specific_name():\n    return 1\n"
    chunks = chunk_repository(files, SHA)
    repository = RepositoryIndex.build("ns", chunks, files)
    noise_ids = [chunk.id for chunk in chunks if chunk.path != "src/named.py"]

    class NoiseFirstVectors:
        def query(self, namespace, vector, top_k):
            return [(chunk_id, 0.9) for chunk_id in noise_ids[:top_k]]

    monkeypatch.setattr(repository.bm25, "search", lambda question, top_k: [(chunk_id, 5.0) for chunk_id in noise_ids[:top_k]])

    result = await retrieve(
        "Where is very_specific_name and the authorization header?", repository, FakeProvider(), NoiseFirstVectors()
    )

    assert result.candidates[0].chunk.path == "src/named.py"
