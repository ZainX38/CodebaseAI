from types import SimpleNamespace

import pytest

from app import config
from app.indexing.chunking import chunk_file
from app.indexing.store import IndexStore
from app.indexing.vector_store import (
    VectorStore,
    VectorStoreError,
    get_chunk_metadata,
    get_index_name,
    get_namespace,
    get_namespace_prefix,
)
from app.ingestion.snapshot import RepositorySnapshot


SHA = "0123456789abcdef0123456789abcdef01234567"


# Records every call instead of talking to Pinecone
class FakeIndex:
    def __init__(self, namespaces=()):
        self.upserts = []
        self.deleted = []
        self.namespaces = list(namespaces)
        self.vector_counts = [{}]

    def upsert(self, *, vectors, namespace, show_progress):
        self.upserts.append((namespace, vectors))

    def query(self, *, namespace, vector, top_k, include_metadata):
        matches = [SimpleNamespace(id="b", score=0.9), SimpleNamespace(id="a", score=0.5)]
        return SimpleNamespace(matches=matches[:top_k])

    def delete_namespace(self, *, name):
        self.deleted.append(name)

    # Two pages, like Pinecone's paginated listing. Nothing may be deleted while the
    # listing is still being read, or later pages could shift and skip namespaces
    def list_namespaces(self, *, prefix):
        names = [name for name in self.namespaces if name.startswith(prefix)]
        middle = len(names) // 2
        yield SimpleNamespace(namespaces=[SimpleNamespace(name=name) for name in names[:middle]])
        assert self.deleted == [], "deleted a namespace before the listing was complete"
        yield SimpleNamespace(namespaces=[SimpleNamespace(name=name) for name in names[middle:]])

    def describe_index_stats(self):
        counts = self.vector_counts.pop(0) if len(self.vector_counts) > 1 else self.vector_counts[0]
        return SimpleNamespace(namespaces={name: SimpleNamespace(vector_count=count) for name, count in counts.items()})


def test_namespace_is_per_user_repository_and_commit():
    namespace = get_namespace("github|123", "ZainX38", "Codebase-AI", SHA)

    assert namespace.startswith(get_namespace_prefix("github|123", "ZainX38", "Codebase-AI"))
    assert namespace.endswith("-zainx38-codebase-ai-0123456789ab")
    assert "github|123" not in namespace  # the raw user ID is never stored in Pinecone
    assert namespace != get_namespace("github|456", "ZainX38", "Codebase-AI", SHA)
    assert namespace != get_namespace("github|123", "ZainX38", "Codebase-AI", "f" * 40)


def test_namespace_is_the_same_for_different_casing_of_the_repository():
    assert get_namespace("u", "ZainX38", "Codebase-AI", SHA) == get_namespace("u", "zainx38", "codebase-ai", SHA)


def test_similar_repository_names_never_share_a_namespace():
    # Both become "a-b-c" once "/" and "." are cleaned, so the hash keeps them apart
    first = get_namespace_prefix("u", "a-b", "c")
    second = get_namespace_prefix("u", "a", "b.c")

    assert first != second
    assert not first.startswith(second) and not second.startswith(first)


def test_index_name_follows_pinecone_rules():
    for model, dimensions in [("qwen3-embedding:0.6b", 1024), ("text-embedding-3-small", 1536), ("x" * 80, 3072)]:
        name = get_index_name(model, dimensions)
        assert len(name) <= 45
        assert name.endswith(f"-{dimensions}")
        assert all(character.islower() or character.isdigit() or character == "-" for character in name)

    assert get_index_name("qwen3-embedding:0.6b", 1024) == "codebase-ai-qwen3-embedding-0-6b-1024"


def test_metadata_leaves_out_missing_symbols_because_pinecone_rejects_null():
    window, function = chunk_file("a.py", "import os\n\ndef run():\n    pass\n", SHA)

    assert "symbol" not in get_chunk_metadata(window)
    assert get_chunk_metadata(function) == {
        "path": "a.py", "start_line": 3, "end_line": 4, "language": "python",
        "kind": "function", "commit_sha": SHA, "symbol": "run",
    }


def test_upsert_sends_batches_with_ids_vectors_and_metadata(monkeypatch):
    monkeypatch.setattr("app.indexing.vector_store.UPSERT_BATCH_SIZE", 2)
    chunks = chunk_file("notes.txt", "".join(f"line {number}\n" for number in range(200)), SHA)
    index = FakeIndex()

    VectorStore(index).upsert_chunks("ns", chunks, [[0.1, 0.2]] * len(chunks))

    assert [len(batch) for _, batch in index.upserts] == [2, 2]
    assert index.upserts[0][1][0] == {"id": chunks[0].id, "values": [0.1, 0.2], "metadata": get_chunk_metadata(chunks[0])}


def test_upsert_refuses_chunks_without_vectors():
    chunks = chunk_file("a.txt", "a\n", SHA)

    with pytest.raises(VectorStoreError):
        VectorStore(FakeIndex()).upsert_chunks("ns", chunks, [])


def test_query_returns_chunk_ids_with_scores():
    assert VectorStore(FakeIndex()).query("ns", [0.1], top_k=2) == [("b", 0.9), ("a", 0.5)]


def test_delete_other_commits_keeps_the_new_one_and_other_repositories():
    prefix = get_namespace_prefix("u", "o", "r")
    old, older, new = prefix + "aaaaaaaaaaaa", prefix + "cccccccccccc", prefix + "bbbbbbbbbbbb"
    other_repository = get_namespace("u", "o", "other", SHA)
    index = FakeIndex([old, new, older, other_repository])

    assert VectorStore(index).delete_other_commits(prefix, keep_namespace=new) == [old, older]
    assert index.deleted == [old, older]


def test_wait_until_searchable_polls_until_every_vector_is_counted():
    index = FakeIndex()
    index.vector_counts = [{}, {"ns": 2}, {"ns": 5}]
    sleeps = []

    VectorStore(index).wait_until_searchable("ns", 5, sleep=sleeps.append)

    assert len(sleeps) == 2


def test_wait_until_searchable_gives_up(monkeypatch):
    monkeypatch.setattr("app.indexing.vector_store.SEARCHABLE_TIMEOUT_SECONDS", -1)

    with pytest.raises(VectorStoreError, match="searchable"):
        VectorStore(FakeIndex()).wait_until_searchable("ns", 5, sleep=lambda seconds: None)


# A fake Pinecone client, so connect() can be tested without the real service
class FakePinecone:
    existing: set[str] = set()
    created: list[dict] = []
    conflict = False

    def __init__(self, api_key):
        self.api_key = api_key

    def has_index(self, name):
        return name in FakePinecone.existing

    def create_index(self, **options):
        if FakePinecone.conflict:
            from pinecone.errors import ConflictError

            raise ConflictError("already exists")
        FakePinecone.created.append(options)

    def Index(self, name):
        return SimpleNamespace(name=name)


@pytest.fixture
def fake_pinecone(monkeypatch):
    import pinecone

    FakePinecone.existing, FakePinecone.created, FakePinecone.conflict = set(), [], False
    monkeypatch.setattr(pinecone, "Pinecone", FakePinecone)
    monkeypatch.setattr(config, "PINECONE_API_KEY", "pc-test")
    return FakePinecone


def test_connect_creates_a_cosine_index_sized_for_the_model(fake_pinecone):
    store = VectorStore.connect("qwen3-embedding:0.6b", 1024)

    [options] = fake_pinecone.created
    assert (options["name"], options["dimension"], options["metric"]) == ("codebase-ai-qwen3-embedding-0-6b-1024", 1024, "cosine")
    assert options["timeout"] > 0  # never waits forever for the index to be ready
    assert store.index.name == "codebase-ai-qwen3-embedding-0-6b-1024"


def test_connect_reuses_an_existing_index(fake_pinecone):
    fake_pinecone.existing = {"codebase-ai-qwen3-embedding-0-6b-1024"}

    VectorStore.connect("qwen3-embedding:0.6b", 1024)

    assert fake_pinecone.created == []


def test_connect_survives_two_jobs_creating_the_same_index(fake_pinecone):
    fake_pinecone.conflict = True

    assert VectorStore.connect("qwen3-embedding:0.6b", 1024).index.name == "codebase-ai-qwen3-embedding-0-6b-1024"


def test_connect_without_an_api_key_fails_clearly(monkeypatch):
    monkeypatch.setattr(config, "PINECONE_API_KEY", "")

    with pytest.raises(VectorStoreError, match="PINECONE_API_KEY"):
        VectorStore.connect("model", 1024)


# ---------------------- SQLite store -------------------------------------------
def make_snapshot(files):
    return RepositorySnapshot("ZainX38", "demo", "main", SHA, files, {"logo.png": "binary file"})


def test_store_saves_and_loads_a_snapshot(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite")
    files = {"a.py": "def run():\n    pass\n", "b.md": "# Title\n"}
    chunks = [chunk for path, content in files.items() for chunk in chunk_file(path, content, SHA)]

    store.save_snapshot("ns", make_snapshot(files), chunks)

    assert store.get_files("ns") == files
    assert store.get_chunks("ns") == chunks
    assert store.get_chunks("other") == []


def test_saving_the_same_namespace_again_replaces_it(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite")
    store.save_snapshot("ns", make_snapshot({"a.txt": "old\n"}), chunk_file("a.txt", "old\n", SHA))
    store.save_snapshot("ns", make_snapshot({"b.txt": "new\n"}), chunk_file("b.txt", "new\n", SHA))

    assert store.get_files("ns") == {"b.txt": "new\n"}
    assert [chunk.path for chunk in store.get_chunks("ns")] == ["b.txt"]


def test_store_deletes_other_commits_with_the_same_prefix_only(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite")
    # "_" is a wildcard in SQL LIKE, so it must be escaped to only match a literal "_"
    for namespace in ("p_old", "p_new", "pXold"):
        store.save_snapshot(namespace, make_snapshot({"a.txt": "a\n"}), [])

    store.delete_other_commits("p_", keep_namespace="p_new")

    assert store.get_files("p_old") == {}
    assert store.get_files("p_new") == {"a.txt": "a\n"}
    assert store.get_files("pXold") == {"a.txt": "a\n"}


def test_only_one_job_per_user_and_repository_can_run(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite")

    first, created = store.create_job_unless_busy("user", "ZainX38/Demo", "ns1", SHA)
    blocked, blocked_created = store.create_job_unless_busy("user", "zainx38/demo", "ns2", "f" * 40)
    other_user, other_created = store.create_job_unless_busy("someone-else", "ZainX38/Demo", "ns3", SHA)

    assert created and other_created and not blocked_created
    assert blocked["id"] == first["id"]  # a different commit waits for the running job
    assert other_user["id"] != first["id"]


def test_a_ready_commit_is_not_indexed_again_but_a_new_one_is(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite")
    first, _ = store.create_job_unless_busy("user", "o/r", "ns1", SHA)
    store.update_job(first["id"], status="ready")

    same, same_created = store.create_job_unless_busy("user", "o/r", "ns1", SHA)
    newer, newer_created = store.create_job_unless_busy("user", "o/r", "ns2", "f" * 40)

    assert (same["id"], same_created) == (first["id"], False)
    assert newer_created
    assert store.is_latest_job(newer["id"]) and not store.is_latest_job(first["id"])


def test_jobs_progress_and_restart(tmp_path):
    store = IndexStore(tmp_path / "index.sqlite")
    job, _ = store.create_job_unless_busy("user", "ZainX38/Demo", "ns1", SHA)

    store.update_job(job["id"], status="embedding", progress=3, total=10)

    latest = store.get_latest_job("user", "ZAINX38/DEMO")
    assert (latest["id"], latest["status"], latest["progress"], latest["total"]) == (job["id"], "embedding", 3, 10)
    assert store.get_latest_job("someone-else", "zainx38/demo") is None

    assert store.fail_interrupted_jobs() == 1
    assert store.get_job(job["id"])["status"] == "failed"

    with pytest.raises(ValueError):
        store.update_job(job["id"], namespace="hacked")
