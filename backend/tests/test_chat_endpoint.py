import json

from fastapi import HTTPException
import pytest

from app.indexing.chunking import chunk_repository
from app.indexing.vector_store import get_namespace, get_user_key
from app.ingestion.snapshot import RepositorySnapshot
from app.retrieval import retriever
from app.routers import chat
from tests.conftest import OTHER_USER_HEADERS, TEST_USER_ID
from tests.test_answer import FILES, FakeProvider


SHA = "a" * 40
METADATA = {"owner": "ZainX38", "name": "demo", "default_branch": "main", "private": True}
URL = "/api/repositories/ZainX38/demo/chat"


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def indexed(index_store, monkeypatch):
    # A ready index for the test user, saved the same way the indexing job saves it
    namespace = get_namespace(TEST_USER_ID, "ZainX38", "demo", SHA)
    chunks = chunk_repository(FILES, SHA)
    index_store.save_snapshot(namespace, RepositorySnapshot("ZainX38", "demo", "main", SHA, FILES, {}), chunks)
    job, _ = index_store.create_job_unless_busy(get_user_key(TEST_USER_ID), "ZainX38/demo", namespace, SHA)
    index_store.update_job(job["id"], status="ready")
    retriever._repository_cache.clear()

    async def fake_metadata(github_token, owner, repo_name):
        return METADATA

    class AllVectors:
        def __init__(self, embedding_model):
            pass

        def query(self, namespace_name, vector, top_k):
            return [(chunk.id, 0.5) for chunk in chunks[:top_k]]

    monkeypatch.setattr(chat, "get_repository_metadata", fake_metadata)
    monkeypatch.setattr(chat, "QueryVectorStore", AllVectors)
    return chunks


def use_provider(monkeypatch, provider):
    monkeypatch.setattr(chat, "get_provider", lambda: provider)


def test_chat_streams_status_tokens_and_validated_citations(client, authenticated, indexed, monkeypatch):
    reply = json.dumps({"answer": "It pops the Authorization header.", "citations": ["[source-1]", "source-9"]})
    use_provider(monkeypatch, FakeProvider(reply))

    response = client.post(URL, json={"question": "How does strip_authorization work?"}, headers=authenticated)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[:2] == ["status", "status"] and names[-1] == "done" and "token" in names
    assert "".join(data["text"] for name, data in events if name == "token") == "It pops the Authorization header."

    done = events[-1][1]
    assert done["answer"] == "It pops the Authorization header."
    [citation] = done["citations"]
    assert (citation["id"], citation["path"], citation["start_line"], citation["commit_sha"]) == ("source-1", "src/auth.py", 1, SHA)
    assert citation["url"].startswith(f"https://github.com/ZainX38/demo/blob/{SHA}/src/auth.py#L1")
    assert done["rejected_citations"] == ["source-9"]


def test_an_invalid_model_answer_becomes_an_error_event(client, authenticated, indexed, monkeypatch):
    use_provider(monkeypatch, FakeProvider('{"answer": "cut off'))

    events = parse_sse(client.post(URL, json={"question": "q?"}, headers=authenticated).text)

    assert events[-1][0] == "error"
    assert "valid JSON" in events[-1][1]["detail"]


def test_an_unexpected_crash_becomes_a_safe_error_event(client, authenticated, indexed, monkeypatch):
    class BrokenProvider(FakeProvider):
        async def embed(self, texts):
            raise KeyError("internal detail")

    use_provider(monkeypatch, BrokenProvider())

    events = parse_sse(client.post(URL, json={"question": "q?"}, headers=authenticated).text)

    assert events[-1] == ("error", {"detail": "The answer could not be generated"})


def test_a_repository_that_is_not_indexed_returns_409(client, authenticated, indexed):
    # The other user has never indexed this repository, so their question can't use the test user's index
    response = client.post(URL, json={"question": "q?"}, headers=OTHER_USER_HEADERS)

    assert response.status_code == 409


def test_chat_requires_authentication(client, authenticated):
    assert client.post(URL, json={"question": "q?"}).status_code == 401


@pytest.mark.parametrize("question", ["", "x" * (retriever.MAX_QUESTION_CHARS + 1)])
def test_empty_or_huge_questions_are_rejected(client, authenticated, question):
    assert client.post(URL, json={"question": question}, headers=authenticated).status_code == 422


def test_access_is_checked_again_for_every_question(client, authenticated, indexed, monkeypatch):
    async def lost_access(github_token, owner, repo_name):
        raise HTTPException(status_code=404, detail="Repository not found or you don't have access to it")

    monkeypatch.setattr(chat, "get_repository_metadata", lost_access)

    assert client.post(URL, json={"question": "q?"}, headers=authenticated).status_code == 404


def test_special_characters_never_break_the_stream(client, authenticated, indexed, monkeypatch):
    # A lone surrogate, a newline and a carriage return inside the answer
    # A raw string, so these stay JSON escapes for the model's reply (not Python escapes)
    reply = r'{"answer": "line one\nline two\r\u00e9 \ud83d end", "citations": ["source-1"]}'
    use_provider(monkeypatch, FakeProvider(reply))

    response = client.post(URL, json={"question": "q?"}, headers=authenticated)
    events = parse_sse(response.text)

    assert events[-1][0] == "done"
    assert events[-1][1]["answer"] == "line one\nline two\ré  end"
    # Every event is still exactly one "event:" line and one "data:" line
    assert all(len(block.splitlines()) == 2 for block in response.text.strip().split("\n\n"))
