from hashlib import sha256
import re
import time
from typing import Any

from app import config
from app.indexing.chunking import Chunk


class VectorStoreError(Exception):
    pass


UPSERT_BATCH_SIZE = 100
INDEX_CREATE_TIMEOUT_SECONDS = 300
SEARCHABLE_TIMEOUT_SECONDS = 60


# ---------------------- Namespace naming --------------------------------------
# One namespace per user + repository + commit, so one user's index can never be
# searched by another user, and an old commit never mixes with a new one.
# The namespace is always built here on the server from the logged-in user's ID,
# never taken from the request.
#
# - the user ID ("github|123") is hashed, so it isn't stored in Pinecone and "|" isn't a problem
# - a hash of "owner/repo" keeps "a-b/c" and "a/b-c" apart after the readable part is cleaned
# - the readable part is only there to make the Pinecone console easier to read
def get_user_key(user_id: str) -> str:
    return sha256(user_id.encode()).hexdigest()[:16]


def get_namespace_prefix(user_id: str, owner: str, repo_name: str) -> str:
    full_name = f"{owner}/{repo_name}".lower()
    repository_key = sha256(full_name.encode()).hexdigest()[:8]
    readable_name = re.sub(r"[^a-z0-9]+", "-", full_name).strip("-")[:60]
    return f"u{get_user_key(user_id)}-{repository_key}-{readable_name}-"


def get_namespace(user_id: str, owner: str, repo_name: str, commit_sha: str) -> str:
    return get_namespace_prefix(user_id, owner, repo_name) + commit_sha[:12].lower()


# Pinecone index names: lowercase letters, numbers and "-", at most 45 characters.
# The embedding model and its dimensions are part of the name, because vectors from
# different models can't be compared, so they must never share an index
def get_index_name(embedding_model: str, dimensions: int) -> str:
    model = re.sub(r"[^a-z0-9]+", "-", embedding_model.lower()).strip("-")
    suffix = f"-{dimensions}"
    return f"codebase-ai-{model}"[: 45 - len(suffix)].rstrip("-") + suffix


# Pinecone rejects null metadata values, so a chunk without a symbol leaves it out.
# The text itself is not stored in Pinecone: it lives in the local SQLite store
def get_chunk_metadata(chunk: Chunk) -> dict[str, Any]:
    metadata = {
        "path": chunk.path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "language": chunk.language,
        "kind": chunk.kind,
        "commit_sha": chunk.commit_sha,
    }
    if chunk.symbol:
        metadata["symbol"] = chunk.symbol
    return metadata


# ---------------------- Pinecone wrapper --------------------------------------
# The Pinecone SDK is synchronous, so callers run these methods with asyncio.to_thread()
class VectorStore:
    def __init__(self, index: Any):
        self.index = index

    @classmethod
    def connect(cls, embedding_model: str, dimensions: int) -> "VectorStore":
        if not config.PINECONE_API_KEY:
            raise VectorStoreError("PINECONE_API_KEY is not set")

        from pinecone import Pinecone, ServerlessSpec
        from pinecone.errors import ConflictError

        client = Pinecone(api_key=config.PINECONE_API_KEY)
        index_name = get_index_name(embedding_model, dimensions)

        # The index is created the first time a model is used, and reused afterwards.
        # create_index() waits until the index is ready; without a timeout it could wait forever
        if not client.has_index(index_name):
            try:
                client.create_index(
                    name=index_name,
                    dimension=dimensions,
                    metric="cosine",
                    spec=ServerlessSpec(cloud=config.PINECONE_CLOUD, region=config.PINECONE_REGION),
                    timeout=INDEX_CREATE_TIMEOUT_SECONDS,
                )
            except ConflictError:
                # Another job created the same index at the same moment, which is fine
                pass

        return cls(client.Index(index_name))

    def upsert_chunks(self, namespace: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise VectorStoreError("Every chunk needs exactly one vector")

        for start in range(0, len(chunks), UPSERT_BATCH_SIZE):
            batch = [
                {"id": chunk.id, "values": vector, "metadata": get_chunk_metadata(chunk)}
                for chunk, vector in zip(chunks[start : start + UPSERT_BATCH_SIZE], vectors[start : start + UPSERT_BATCH_SIZE])
            ]
            self.index.upsert(vectors=batch, namespace=namespace, show_progress=False)

    # Returns (chunk ID, cosine similarity), best match first
    def query(self, namespace: str, vector: list[float], top_k: int) -> list[tuple[str, float]]:
        response = self.index.query(namespace=namespace, vector=vector, top_k=top_k, include_metadata=False)
        return [(match.id, match.score) for match in response.matches]

    def delete_namespace(self, namespace: str) -> None:
        self.index.delete_namespace(name=namespace)

    # Pinecone serverless is "eventually consistent": vectors can take a few seconds
    # to become searchable after an upsert. The job only says "ready" once Pinecone
    # reports every vector, so the first question never searches a half-empty index
    def wait_until_searchable(self, namespace: str, expected_count: int, sleep=time.sleep) -> None:
        deadline = time.monotonic() + SEARCHABLE_TIMEOUT_SECONDS
        while True:
            namespaces = self.index.describe_index_stats().namespaces
            summary = namespaces.get(namespace)
            if summary and summary.vector_count >= expected_count:
                return
            if time.monotonic() > deadline:
                raise VectorStoreError("Pinecone did not make the vectors searchable in time")
            sleep(2)

    # When a newer commit is indexed, the old commits of the same user + repository
    # are deleted, so the free Pinecone plan doesn't fill up with stale namespaces.
    # All names are listed first: deleting while paging through the list could skip some
    def delete_other_commits(self, prefix: str, keep_namespace: str) -> list[str]:
        to_delete = [
            namespace.name
            for page in self.index.list_namespaces(prefix=prefix)
            for namespace in page.namespaces
            if namespace.name.startswith(prefix) and namespace.name != keep_namespace
        ]
        for namespace in to_delete:
            self.delete_namespace(namespace)
        return to_delete
