import json
import re

import pytest

from app.ai.providers import ProviderError
from app.indexing.chunking import chunk_repository
from app.rag.answer import AnswerExtractor, generate_answer
from app.rag.prompt import ANSWER_SCHEMA, SYSTEM_PROMPT
from app.retrieval.retriever import RepositoryIndex


SHA = "a" * 40
FILES = {
    "src/auth.py": "def strip_authorization(headers):\n    headers.pop('Authorization')\n",
    "src/cookies.py": "def merge_cookies(jar, cookies):\n    return jar\n",
}


# ---------------------- Streaming extractor -------------------------------------
def feed_all(pieces):
    extractor = AnswerExtractor()
    return "".join(extractor.feed(piece) for piece in pieces)


def test_extractor_streams_the_answer_text_only():
    raw = json.dumps({"answer": "It pops the header.", "citations": ["source-1"]})

    assert feed_all(raw[i : i + 3] for i in range(0, len(raw), 3)) == "It pops the header."


@pytest.mark.parametrize("text", ['line one\nline "two"', "café \\ back", "emoji \U0001F600 ok", "tab\tend"])
def test_extractor_decodes_escapes_split_across_pieces(text):
    raw = json.dumps({"answer": text, "citations": []})

    # One character at a time, so every escape is split
    assert feed_all(list(raw)) == text


def test_extractor_skips_the_word_answer_when_it_is_not_the_key():
    raw = '{"citations": ["answer"], "answer": "The real one"}'

    assert feed_all(raw[i : i + 4] for i in range(0, len(raw), 4)) == "The real one"
    assert feed_all(['{"answer" "no colon", "x": 1}']) == ""


def test_extractor_waits_for_the_key_and_ignores_the_rest():
    assert feed_all(['{"ans', 'wer"', " :", ' "Hi', '", "citations": ["source-1"]}']) == "Hi"
    assert feed_all(['{"citations": []}']) == ""


# ---------------------- The pipeline with a fake model ------------------------
class FakeProvider:
    chat_model = "fake-chat"
    embedding_model = "fake-embed"

    def __init__(self, reply="", error=None, pieces=None):
        self.reply = reply
        self.error = error
        self.pieces = pieces
        self.calls = []

    async def embed(self, texts):
        return [[1.0, float(len(text) % 3)] for text in texts]

    async def chat(self, messages, *, schema=None, model=None, max_tokens=700):
        self.calls.append({"messages": messages, "schema": schema, "model": model, "max_tokens": max_tokens})
        if self.error:
            raise self.error
        for piece in self.pieces or [self.reply[i : i + 5] for i in range(0, len(self.reply), 5)]:
            yield piece


class AllVectors:
    def __init__(self, repository):
        self.ids = [chunk.id for chunk in repository.chunks]

    def query(self, namespace, vector, top_k):
        return [(chunk_id, 0.5) for chunk_id in self.ids[:top_k]]


def make_repository():
    return RepositoryIndex.build("ns", chunk_repository(FILES, SHA), FILES)


async def run(provider, question="How does strip_authorization work?", **options):
    repository = make_repository()
    events = [event async for event in generate_answer(question, repository, provider, AllVectors(repository), **options)]
    return events, events[-1].data


async def test_a_valid_answer_streams_tokens_and_keeps_valid_citations():
    provider = FakeProvider(json.dumps({"answer": "It removes the Authorization header.", "citations": ["source-1"]}))

    events, result = await run(provider)

    kinds = [event.kind for event in events]
    assert kinds[0] == "status" and kinds[-1] == "result" and "token" in kinds
    assert "".join(event.data for event in events if event.kind == "token") == "It removes the Authorization header."
    assert (result.error, result.answer) == (None, "It removes the Authorization header.")
    assert [excerpt.id for excerpt in result.citations] == ["source-1"]
    assert result.citations[0].path == "src/auth.py"  # hybrid retrieval put the named function first


async def test_the_model_gets_the_schema_system_prompt_and_numbered_excerpts():
    provider = FakeProvider(json.dumps({"answer": "x", "citations": []}))

    _, result = await run(provider, model="deepseek-coder-v2:16b")

    [call] = provider.calls
    assert call["schema"] == ANSWER_SCHEMA and call["model"] == "deepseek-coder-v2:16b"
    assert call["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    user_message = call["messages"][1]["content"]
    assert re.search(r"\[source-1 \| ([0-9a-f]{8})\] src/auth\.py:1-2 \(strip_authorization; complete body\)", user_message)
    fence = re.search(r"\[source-1 \| ([0-9a-f]{8})\]", user_message).group(1)
    assert f"[end of source-1 | {fence}]" in user_message
    # Everything is kept for the evaluation
    assert result.messages == call["messages"] and result.retrieval["mode"] == "hybrid"
    assert result.to_dict()["excerpts"][0]["id"] == "source-1"


async def test_bracketed_citations_are_accepted_and_unknown_ones_rejected():
    provider = FakeProvider(json.dumps({"answer": "x", "citations": ["[source-1]", "source-7"]}))

    _, result = await run(provider)

    assert [excerpt.id for excerpt in result.citations] == ["source-1"]
    assert result.normalised_citations == ["[source-1]"]
    assert result.rejected_citations == ["source-7"]


async def test_an_answer_cut_off_by_the_output_limit_is_an_error():
    provider = FakeProvider('{"answer": "It removes the head')

    events, result = await run(provider)

    assert "isn't valid JSON" in result.error
    assert result.raw_response == '{"answer": "It removes the head'  # the raw text is kept
    assert result.citations == []


async def test_json_with_the_wrong_shape_is_an_error():
    _, result = await run(FakeProvider(json.dumps({"text": "hello"})))

    assert result.error == "The model's answer doesn't have the expected shape"


async def test_a_model_failure_becomes_an_error_result():
    _, result = await run(FakeProvider(error=ProviderError("The answer model failed")))

    assert result.error == "The answer model failed"


async def test_vector_and_chunks_modes_are_passed_through():
    provider = FakeProvider(json.dumps({"answer": "x", "citations": []}))

    _, result = await run(provider, retrieval_mode="vector", context_mode="chunks")

    assert (result.retrieval_mode, result.context_mode, result.retrieval["mode"]) == ("vector", "chunks", "vector")
    assert all(excerpt.scope.startswith("excerpt") for excerpt in result.excerpts)


@pytest.mark.parametrize("answer", [None, "", "   "])
async def test_a_null_or_empty_answer_is_an_error(answer):
    _, result = await run(FakeProvider(json.dumps({"answer": answer, "citations": []})))

    assert result.error is not None and result.answer == ""


async def test_the_final_answer_matches_the_streamed_text_exactly():
    provider = FakeProvider(json.dumps({"answer": "  spaced out  ", "citations": []}))

    events, result = await run(provider)

    assert "".join(event.data for event in events if event.kind == "token") == result.answer == "  spaced out  "


async def test_lone_surrogates_are_removed_from_the_answer_and_rejected_citations():
    # Valid JSON, but "\ud83d" alone is half an emoji that can't be encoded as UTF-8
    # A raw string, so these stay JSON escapes in the model's reply (not Python escapes)
    provider = FakeProvider(r'{"answer": "bad \ud83d char", "citations": ["\udc00x"]}')

    _, result = await run(provider)

    assert result.answer == "bad  char"
    assert result.rejected_citations == ["x"]
    result.answer.encode("utf-8")  # must not raise


async def test_a_missing_vector_index_is_a_clear_error_not_a_crash():
    from app.indexing.vector_store import VectorStoreError

    class NoIndex:
        def query(self, namespace, vector, top_k):
            raise VectorStoreError("No index exists for the configured embedding model; index the repository first")

    repository = make_repository()
    events = [event async for event in generate_answer("q?", repository, FakeProvider("{}"), NoIndex())]

    assert events[-1].kind == "result"
    assert "index the repository first" in events[-1].data.error


async def test_missing_or_non_list_citations_go_through_parse_answer():
    _, missing = await run(FakeProvider(json.dumps({"answer": "x"})))
    _, not_a_list = await run(FakeProvider(json.dumps({"answer": "x", "citations": "source-1"})))

    assert (missing.error, missing.citations, missing.rejected_citations) == (None, [], [])
    assert not_a_list.citations == [] and not_a_list.rejected_citations == ["source-1"]
