import asyncio
from dataclasses import dataclass

from app.github.repositories import get_latest_commit_sha, get_repository_metadata
from app.ingestion.archive import download_tarball, extract_files


# One repository at one exact commit. Everything that gets indexed comes from here
@dataclass
class RepositorySnapshot:
    owner: str
    name: str
    default_branch: str
    commit_sha: str
    files: dict[str, str]
    excluded: dict[str, str]


# ---------------------- Downloads a repository snapshot ----------------------
# 1. Metadata (with the user's token) --> access check + default branch
# 2. Latest commit SHA of the default branch --> pins the snapshot
# 3. Tarball at that SHA --> all files in one request
async def download_repository_snapshot(
    github_token: str,
    owner: str,
    repo_name: str,
) -> RepositorySnapshot:
    metadata = await get_repository_metadata(github_token, owner, repo_name)
    commit_sha = await get_latest_commit_sha(
        github_token,
        metadata["owner"],
        metadata["name"],
        metadata["default_branch"],
    )

    archive_bytes = await download_tarball(github_token, metadata["owner"], metadata["name"], commit_sha)
    # Decompressing is CPU work, so it runs in a thread instead of blocking the event loop
    extracted = await asyncio.to_thread(extract_files, archive_bytes)

    return RepositorySnapshot(
        owner=metadata["owner"],
        name=metadata["name"],
        default_branch=metadata["default_branch"],
        commit_sha=commit_sha,
        files=extracted.files,
        excluded=extracted.excluded,
    )
