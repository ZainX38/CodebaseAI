from typing import Annotated

from fastapi import APIRouter, Header

from app.auth.auth0 import get_authenticated_user_id
from app.auth.github_identity import get_github_token
from app.github import repositories as github_repositories


router = APIRouter(prefix="/api/repositories")


# ---------------------- Gets repositories from GitHub API --------------------
@router.get("")
async def get_repositories(
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await get_authenticated_user_id(authorization)
    github_token = await get_github_token(user_id)

    return await github_repositories.list_repositories(github_token)


# ---------------------- Gets repository contents from GitHub API --------------
# The first route handles the repository root.
# The second handles nested paths; {path:path} captures the remaining URL
# path, including "/" separators, into the parameter named "path".
@router.get("/{owner}/{repo_name}/contents")
@router.get("/{owner}/{repo_name}/contents/{path:path}")
async def get_repository_contents(
    owner: str,
    repo_name: str,
    path: str = "",
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await get_authenticated_user_id(authorization)
    github_token = await get_github_token(user_id)

    return await github_repositories.get_repository_contents(
        github_token,
        owner,
        repo_name,
        path,
    )
