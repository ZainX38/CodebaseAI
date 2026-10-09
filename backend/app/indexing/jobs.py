import asyncio
import logging
from typing import Any

from fastapi import HTTPException

from app import config
from app.ai.providers import ProviderError, get_provider
from app.indexing.chunking import Chunk, chunk_repository
from app.indexing.store import IndexStore, get_store
from app.indexing.vector_store import (
    VectorStore,
    VectorStoreError,
    get_namespace,
    get_namespace_prefix,
    get_user_key,
)
from app.ingestion.snapshot import download_snapshot, resolve_latest_commit


logger = logging.getLogger(__name__)

# asyncio only keeps a weak reference to tasks, so a running job could be garbage
# collected half way. Keeping them in a set until they finish prevents that
_running_jobs: set[asyncio.Task] = set()

MAX_CHUNKS = 20_000


class IndexingError(Exception):
    pass


# The text that gets embedded. The path and symbol name are added on top, so a
# question like "how does Session.send work" also matches on the name, not only the code
def get_embedding_text(chunk: Chunk) -> str:
    header = f"File: {chunk.path}"
    if chunk.symbol:
        header += f"\nSymbol: {chunk.symbol}"
    return f"{header}\n\n{chunk.text}"


# Only what the frontend needs. The namespace and user key stay on the server
def to_status(job: dict[str, Any] | None) -> dict[str, Any]:
    if job is None:
        return {"status": "not_indexed"}
    return {
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "commit_sha": job["commit_sha"],
        "error": job["error"],
        "updated_at": job["updated_at"],
    }


# SQLite calls run in a thread, so a slow write (e.g. saving a big snapshot) never
# blocks other requests on the event loop
async def get_status(user_id: str, owner: str, repo_name: str, store: IndexStore | None = None) -> dict[str, Any]:
    store = store or get_store()
    user_key, repository = get_user_key(user_id), f"{owner}/{repo_name}"
    job = await asyncio.to_thread(store.get_latest_job, user_key, repository)
    ready_job = await asyncio.to_thread(store.get_latest_ready_job, user_key, repository)

    # If a re-index failed, the previous commit is still indexed and still used for
    # questions, so the frontend is told about it instead of only showing "failed"
    status = to_status(job)
    if ready_job:
        status["ready_commit_sha"] = ready_job["commit_sha"]
    return status


# ---------------------- Starts indexing (inside the request) -----------------
async def start_indexing(
    user_id: str,
    github_token: str,
    owner: str,
    repo_name: str,
    store: IndexStore | None = None,
) -> dict[str, Any]:
    store = store or get_store()

    # Access check + commit SHA happen here, so errors like 404 go straight back to the user
    metadata, commit_sha = await resolve_latest_commit(github_token, owner, repo_name)
    repository = f"{metadata['owner']}/{metadata['name']}"
    namespace = get_namespace(user_id, metadata["owner"], metadata["name"], commit_sha)

    # Nothing new is started if a job is already running for this repository, or if
    # this exact commit is already indexed. The check and the insert are one atomic
    # SQLite transaction, so two requests at the same time can't both start a job
    job, created = await asyncio.to_thread(
        store.create_job_unless_busy, get_user_key(user_id), repository, namespace, commit_sha
    )
    if created:
        task = asyncio.create_task(run_indexing_job(job["id"], user_id, github_token, metadata, commit_sha, store))
        _running_jobs.add(task)
        task.add_done_callback(_running_jobs.discard)
    return job


async def update_job(store: IndexStore, job_id: str, **fields: Any) -> None:
    await asyncio.to_thread(store.update_job, job_id, **fields)


# ---------------------- Runs the job (in the background) ---------------------
async def run_indexing_job(
    job_id: str,
    user_id: str,
    github_token: str,
    metadata: dict[str, Any],
    commit_sha: str,
    store: IndexStore,
) -> None:
    namespace = get_namespace(user_id, metadata["owner"], metadata["name"], commit_sha)

    try:
        await update_job(store, job_id, status="downloading")
        snapshot = await download_snapshot(github_token, metadata, commit_sha)

        await update_job(store, job_id, status="chunking")
        chunks = await asyncio.to_thread(chunk_repository, snapshot.files, commit_sha)
        if not chunks:
            raise IndexingError("No files in this repository could be indexed")
        if len(chunks) > MAX_CHUNKS:
            raise IndexingError(f"Repository has more than {MAX_CHUNKS:,} chunks, which is too large to index")

        # Each batch is embedded and uploaded straight away, so only one batch of vectors
        # is ever in memory (20,000 chunks x 1,024 floats would be hundreds of MB).
        # The progress is saved after each batch so the frontend can show "64 / 983"
        await update_job(store, job_id, status="embedding", progress=0, total=len(chunks))
        provider = get_provider()
        vector_store = None
        for start in range(0, len(chunks), config.EMBEDDING_BATCH_SIZE):
            batch = chunks[start : start + config.EMBEDDING_BATCH_SIZE]
            vectors = await provider.embed([get_embedding_text(chunk) for chunk in batch])

            # The vector size comes from the first embedding, so the index always matches the model
            if vector_store is None:
                vector_store = await asyncio.to_thread(VectorStore.connect, provider.embedding_model, len(vectors[0]))

            await asyncio.to_thread(vector_store.upsert_chunks, namespace, batch, vectors)
            await update_job(store, job_id, progress=start + len(batch))

        await update_job(store, job_id, status="uploading")
        await asyncio.to_thread(store.save_snapshot, namespace, snapshot, chunks)
        await asyncio.to_thread(vector_store.wait_until_searchable, namespace, len(chunks))

        # Old commits are cleaned up while the status is still "uploading", so no new job
        # can start (and be deleted by this clean-up) until this one is completely done
        await delete_other_commits(store, vector_store, job_id, user_id, metadata, namespace)
        await update_job(store, job_id, status="ready", progress=len(chunks))
    except HTTPException as error:
        await update_job(store, job_id, status="failed", error=str(error.detail))
        return
    except (IndexingError, ProviderError, VectorStoreError) as error:
        await update_job(store, job_id, status="failed", error=str(error))
        return
    except Exception:
        # Unknown errors are logged for me, but the user only sees a generic message,
        # because the details could include internal information
        logger.exception("Indexing job %s failed", job_id)
        await update_job(store, job_id, status="failed", error="Indexing failed unexpectedly")
        return


# Old commits are only deleted once the new one is complete, so there is always a
# working index while a new one is being built. It is skipped if a newer job exists,
# so an old job can never delete a newer index. A failure here is only logged: the
# new index is complete and usable, and the next successful job will clean up again
async def delete_other_commits(
    store: IndexStore,
    vector_store: VectorStore,
    job_id: str,
    user_id: str,
    metadata: dict[str, Any],
    namespace: str,
) -> None:
    try:
        if await asyncio.to_thread(store.is_latest_job, job_id):
            prefix = get_namespace_prefix(user_id, metadata["owner"], metadata["name"])
            await asyncio.to_thread(vector_store.delete_other_commits, prefix, namespace)
            await asyncio.to_thread(store.delete_other_commits, prefix, namespace)
    except Exception:
        logger.exception("Could not delete old commits after indexing job %s", job_id)
