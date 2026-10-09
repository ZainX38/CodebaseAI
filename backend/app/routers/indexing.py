from typing import Annotated

from fastapi import APIRouter, Header

from app.auth.auth0 import get_authenticated_user_id
from app.auth.github_identity import get_github_token
from app.indexing import jobs


router = APIRouter(prefix="/api/repositories")


# ---------------------- Starts indexing a repository -------------------------
# 202 Accepted: the request only starts the job, the work continues in the background
@router.post("/{owner}/{repo_name}/index", status_code=202)
async def index_repository(
    owner: str,
    repo_name: str,
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await get_authenticated_user_id(authorization)
    github_token = await get_github_token(user_id)

    job = await jobs.start_indexing(user_id, github_token, owner, repo_name)
    return jobs.to_status(job)


# ---------------------- Gets the indexing status -----------------------------
# The frontend polls this every few seconds. Jobs are stored per user, so a user
# can only ever see their own jobs and no GitHub request is needed
@router.get("/{owner}/{repo_name}/index/status")
async def get_index_status(
    owner: str,
    repo_name: str,
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await get_authenticated_user_id(authorization)

    return await jobs.get_status(user_id, owner, repo_name)
