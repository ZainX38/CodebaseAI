import asyncio
from collections.abc import AsyncIterator
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai.providers import get_provider
from app.auth.auth0 import get_authenticated_user_id
from app.auth.github_identity import get_github_token
from app.github.repositories import get_repository_metadata
from app.indexing.store import get_store
from app.indexing.vector_store import QueryVectorStore, get_user_key
from app.rag.answer import generate_answer
from app.rag.citations import citation_to_dict
from app.retrieval.retriever import MAX_QUESTION_CHARS, RepositoryIndex, load_repository_index


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repositories")


# FastAPI checks the body before the endpoint runs, so an empty or huge question
# gets a 422 straight away
class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


# One Server-Sent Event: a name and one line of JSON, ended by a blank line.
# ensure_ascii escapes every non-ASCII character (as é), so nothing in the data
# can fail to encode as UTF-8 half way through a stream
def to_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"


# ---------------------- Answers a question about a repository ----------------
# Everything that can fail before the answer starts (login, access, not indexed)
# is a normal HTTP error. Once streaming has started, errors become an "error" event
@router.post("/{owner}/{repo_name}/chat")
async def chat(
    owner: str,
    repo_name: str,
    body: ChatRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await get_authenticated_user_id(authorization)
    github_token = await get_github_token(user_id)

    # Access is checked again for every question: the user may have lost access to a
    # private repository since it was indexed
    metadata = await get_repository_metadata(github_token, owner, repo_name)

    store = get_store()
    job = await asyncio.to_thread(
        store.get_latest_ready_job, get_user_key(user_id), f"{metadata['owner']}/{metadata['name']}"
    )
    if job is None:
        raise HTTPException(status_code=409, detail="This repository hasn't been indexed yet")

    repository = await load_repository_index(store, job["namespace"])
    if not repository.chunks:
        raise HTTPException(status_code=409, detail="This repository's index is not available; index it again")

    return StreamingResponse(
        stream_answer(body.question.strip(), repository, metadata),
        media_type="text/event-stream",
        # no-cache and X-Accel-Buffering stop proxies from holding events back
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def stream_answer(question: str, repository: RepositoryIndex, metadata: dict[str, Any]) -> AsyncIterator[str]:
    try:
        provider = get_provider()
        vector_store = QueryVectorStore(provider.embedding_model)

        async for event in generate_answer(question, repository, provider, vector_store):
            if event.kind == "status":
                yield to_sse("status", {"stage": event.data})
            elif event.kind == "token":
                yield to_sse("token", {"text": event.data})
            elif event.data.error:
                yield to_sse("error", {"detail": event.data.error})
            else:
                result = event.data
                yield to_sse("done", {
                    "answer": result.answer,
                    "citations": [citation_to_dict(excerpt, metadata["owner"], metadata["name"]) for excerpt in result.citations],
                    # Shown to the user as a warning; never turned into links
                    "rejected_citations": [str(citation) for citation in result.rejected_citations],
                    "commit_sha": result.excerpts[0].commit_sha if result.excerpts else None,
                })
    except Exception:
        # Unknown errors are logged for me, the user gets a generic message
        logger.exception("Answering a question failed")
        yield to_sse("error", {"detail": "The answer could not be generated"})
