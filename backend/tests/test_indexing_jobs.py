import asyncio

from fastapi import HTTPException
import httpx
import pytest
import respx

from app.ai.providers import ProviderError
from app.auth.auth0 import get_authenticated_user_id
from app.indexing import jobs
from app.indexing.chunking import chunk_file, chunk_repository
from app.indexing.vector_store import get_namespace, get_namespace_prefix
from app.ingestion.snapshot import RepositorySnapshot
from tests.conftest import OTHER_USER_HEADERS, TEST_USER_ID


SHA = "0123456789abcdef0123456789abcdef01234567"
NEW_SHA = "fedcba9876543210fedcba9876543210fedcba98"
METADATA = {"owner": "ZainX38", "name": "demo", "default_branch": "main", "private": True}
# 3 chunks: the import window, run() and the README
FILES = {"app.py": "import os\n\ndef run():\n    return 1\n", "README.md": "# Demo\n"}


class FakeProvider:
    embedding_model = "fake-embed"

    def __init__(self, error: Exception | None = None):
        self.batches = []
        self.error = error

    async def embed(self, texts):
        if self.error:
            raise self.error
        self.batches.append(texts)
        return [[float(index), 1.0] for index, _ in enumerate(texts)]


class FakeVectorStore:
    instances = []
    cleanup_error: Exception | None = None

    def __init__(self, model, dimensions):
        self.model, self.dimensions = model, dimensions
        self.upserts, self.cleaned, self.waited = [], [], []
        FakeVectorStore.instances.append(self)

    def upsert_chunks(self, namespace, chunks, vectors):
        self.upserts.append((namespace, chunks, vectors))

    def wait_until_searchable(self, namespace, expected_count):
        self.waited.append((namespace, expected_count))

    def delete_other_commits(self, prefix, keep_namespace):
        if FakeVectorStore.cleanup_error:
            raise FakeVectorStore.cleanup_error
        self.cleaned.append((prefix, keep_namespace))
        return []


@pytest.fixture
def fakes(monkeypatch):
    provider = FakeProvider()
    FakeVectorStore.instances = []
    FakeVectorStore.cleanup_error = None
    commits = {"sha": SHA}

    async def fake_resolve(github_token, owner, repo_name):
        return METADATA, commits["sha"]

    async def fake_download(github_token, metadata, commit_sha):
        return RepositorySnapshot("ZainX38", "demo", "main", commit_sha, dict(FILES), {})

    monkeypatch.setattr(jobs, "resolve_latest_commit", fake_resolve)
    monkeypatch.setattr(jobs, "download_snapshot", fake_download)
    monkeypatch.setattr(jobs, "get_provider", lambda: provider)
    monkeypatch.setattr(jobs.VectorStore, "connect", FakeVectorStore)
    provider.commits = commits  # lets a test push a "new commit"
    return provider


async def finish_jobs():
    await asyncio.gather(*list(jobs._running_jobs))


async def start(store, user_id=TEST_USER_ID):
    return await jobs.start_indexing(user_id, "gho_token", "ZainX38", "demo", store=store)


def test_embedding_text_includes_the_path_and_symbol():
    window, function = chunk_file("app.py", "import os\n\ndef run():\n    pass\n", SHA)

    assert jobs.get_embedding_text(function) == "File: app.py\nSymbol: run\n\ndef run():\n    pass\n"
    assert jobs.get_embedding_text(window).startswith("File: app.py\n\nimport os")


async def test_job_downloads_chunks_embeds_and_stores_everything(fakes, index_store, monkeypatch):
    monkeypatch.setattr(jobs.config, "EMBEDDING_BATCH_SIZE", 2)
    namespace = get_namespace(TEST_USER_ID, "ZainX38", "demo", SHA)

    job = await jobs.start_indexing(TEST_USER_ID, "gho_token", "zainx38", "DEMO", store=index_store)
    await finish_jobs()

    finished = index_store.get_job(job["id"])
    assert (finished["status"], finished["progress"], finished["total"], finished["error"]) == ("ready", 3, 3, None)

    [vector_store] = FakeVectorStore.instances
    assert (vector_store.model, vector_store.dimensions) == ("fake-embed", 2)
    # Every batch is uploaded straight after it is embedded, so vectors never pile up in memory
    assert [len(batch) for batch in fakes.batches] == [2, 1]
    assert [(upsert_namespace, len(chunks)) for upsert_namespace, chunks, _ in vector_store.upserts] == [(namespace, 2), (namespace, 1)]
    uploaded = [chunk for _, chunks, _ in vector_store.upserts for chunk in chunks]
    assert uploaded == chunk_repository(FILES, SHA)
    assert vector_store.waited == [(namespace, 3)]  # "ready" only once Pinecone has every vector
    assert vector_store.cleaned == [(get_namespace_prefix(TEST_USER_ID, "ZainX38", "demo"), namespace)]

    assert index_store.get_files(namespace) == FILES
    assert index_store.get_chunks(namespace) == uploaded


async def test_the_same_commit_is_not_indexed_twice(fakes, index_store):
    first = await start(index_store)
    second = await start(index_store)
    await finish_jobs()
    third = await start(index_store)

    assert first["id"] == second["id"] == third["id"]
    assert len(FakeVectorStore.instances) == 1


async def test_a_new_commit_waits_until_the_running_job_has_finished(fakes, index_store, monkeypatch):
    # Without this, the job that finishes first would delete the other job's namespace
    download_can_finish = asyncio.Event()
    real_download = jobs.download_snapshot

    async def slow_download(github_token, metadata, commit_sha):
        await download_can_finish.wait()
        return await real_download(github_token, metadata, commit_sha)

    monkeypatch.setattr(jobs, "download_snapshot", slow_download)

    running = await start(index_store)
    fakes.commits["sha"] = NEW_SHA  # someone pushes while the first job is still running
    while_running = await start(index_store)

    download_can_finish.set()
    await finish_jobs()
    after_finish = await start(index_store)
    await finish_jobs()

    assert while_running["id"] == running["id"]
    assert after_finish["id"] != running["id"]
    assert after_finish["commit_sha"] == NEW_SHA
    assert index_store.get_job(after_finish["id"])["status"] == "ready"


async def test_an_old_job_does_not_clean_up_if_a_newer_job_exists(fakes, index_store, monkeypatch):
    monkeypatch.setattr(index_store, "is_latest_job", lambda job_id: False)

    job = await start(index_store)
    await finish_jobs()

    assert index_store.get_job(job["id"])["status"] == "ready"
    assert FakeVectorStore.instances[0].cleaned == []


async def test_a_failed_clean_up_keeps_the_new_index_ready(fakes, index_store):
    FakeVectorStore.cleanup_error = RuntimeError("Pinecone hiccup")

    job = await start(index_store)
    await finish_jobs()

    assert index_store.get_job(job["id"])["status"] == "ready"


async def test_a_failed_job_can_be_started_again(fakes, index_store, monkeypatch):
    monkeypatch.setattr(jobs, "get_provider", lambda: FakeProvider(error=ProviderError("Ollama is not running")))
    failed = await start(index_store)
    await finish_jobs()

    monkeypatch.setattr(jobs, "get_provider", lambda: fakes)
    retried = await start(index_store)
    await finish_jobs()

    assert index_store.get_job(failed["id"])["error"] == "Ollama is not running"
    assert retried["id"] != failed["id"]
    assert index_store.get_job(retried["id"])["status"] == "ready"


async def test_another_user_gets_their_own_job_and_namespace(fakes, index_store):
    mine = await start(index_store)
    theirs = await start(index_store, user_id="github|999999")
    await finish_jobs()

    assert mine["id"] != theirs["id"]
    assert mine["namespace"] != theirs["namespace"]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (HTTPException(status_code=413, detail="Repository is too large to index"), "Repository is too large to index"),
        (KeyError("secret internal detail"), "Indexing failed unexpectedly"),
    ],
)
async def test_job_failures_are_saved_with_a_safe_message(fakes, index_store, monkeypatch, error, message):
    async def failing_download(github_token, metadata, commit_sha):
        raise error

    monkeypatch.setattr(jobs, "download_snapshot", failing_download)

    job = await start(index_store)
    await finish_jobs()

    finished = index_store.get_job(job["id"])
    assert (finished["status"], finished["error"]) == ("failed", message)


async def test_a_repository_with_nothing_to_index_fails_clearly(fakes, index_store, monkeypatch):
    async def empty_download(github_token, metadata, commit_sha):
        return RepositorySnapshot("ZainX38", "demo", "main", commit_sha, {}, {"logo.png": "binary file"})

    monkeypatch.setattr(jobs, "download_snapshot", empty_download)

    job = await start(index_store)
    await finish_jobs()

    assert index_store.get_job(job["id"])["error"] == "No files in this repository could be indexed"


# ---------------------- Auth0 user ID cache ------------------------------------
@respx.mock
async def test_user_id_is_cached_so_polling_does_not_hit_the_auth0_rate_limit():
    userinfo = respx.get("https://test.auth0.local/userinfo").mock(
        return_value=httpx.Response(200, json={"sub": TEST_USER_ID})
    )

    for _ in range(5):
        assert await get_authenticated_user_id("Bearer token-a") == TEST_USER_ID

    assert userinfo.call_count == 1


@respx.mock
async def test_failed_logins_are_not_cached():
    userinfo = respx.get("https://test.auth0.local/userinfo").mock(return_value=httpx.Response(401))

    for _ in range(2):
        with pytest.raises(HTTPException):
            await get_authenticated_user_id("Bearer bad-token")

    assert userinfo.call_count == 2


# ---------------------- Endpoints ----------------------------------------------
def test_index_endpoints_require_authentication(client, authenticated):
    assert client.post("/api/repositories/ZainX38/demo/index").status_code == 401
    assert client.get("/api/repositories/ZainX38/demo/index/status").status_code == 401


@pytest.fixture
def no_background_work(monkeypatch):
    async def do_nothing(*args):
        return None

    # The background work is tested above; here only the request/response is checked
    monkeypatch.setattr(jobs, "run_indexing_job", do_nothing)


def test_start_and_poll_indexing_through_the_api(client, authenticated, fakes, no_background_work):
    assert client.get("/api/repositories/ZainX38/demo/index/status", headers=authenticated).json() == {
        "status": "not_indexed",
    }

    started = client.post("/api/repositories/ZainX38/demo/index", headers=authenticated)
    status = client.get("/api/repositories/ZainX38/demo/index/status", headers=authenticated).json()

    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    assert (status["status"], status["commit_sha"]) == ("queued", SHA)
    # Internal details never reach the frontend
    assert "namespace" not in status and "user_key" not in status


def test_users_cannot_see_each_others_indexing_status(client, authenticated, fakes, no_background_work):
    client.post("/api/repositories/ZainX38/demo/index", headers=authenticated)

    other_user_status = client.get("/api/repositories/ZainX38/demo/index/status", headers=OTHER_USER_HEADERS)

    assert other_user_status.json() == {"status": "not_indexed"}


async def test_old_commits_are_cleaned_up_before_the_job_says_ready(fakes, index_store, monkeypatch):
    # While the status is still active, no new job can start and be deleted by this clean-up
    statuses_during_cleanup = []
    real_cleanup = FakeVectorStore.delete_other_commits

    def recording_cleanup(vector_store, prefix, keep_namespace):
        statuses_during_cleanup.append(index_store.get_latest_job(jobs.get_user_key(TEST_USER_ID), "ZainX38/demo")["status"])
        return real_cleanup(vector_store, prefix, keep_namespace)

    monkeypatch.setattr(FakeVectorStore, "delete_other_commits", recording_cleanup)

    await start(index_store)
    await finish_jobs()

    assert statuses_during_cleanup == ["uploading"]


async def test_a_failed_reindex_still_reports_the_commit_that_is_ready(fakes, index_store, monkeypatch):
    await start(index_store)
    await finish_jobs()

    fakes.commits["sha"] = NEW_SHA
    monkeypatch.setattr(jobs, "get_provider", lambda: FakeProvider(error=ProviderError("Ollama is not running")))
    await start(index_store)
    await finish_jobs()

    status = await jobs.get_status(TEST_USER_ID, "ZainX38", "demo", store=index_store)

    assert (status["status"], status["commit_sha"], status["ready_commit_sha"]) == ("failed", NEW_SHA, SHA)
