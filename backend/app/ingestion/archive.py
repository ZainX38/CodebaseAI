import asyncio
from dataclasses import dataclass, field
from io import BytesIO
import tarfile
import zlib

from fastapi import HTTPException
import httpx

from app.config import (
    DOWNLOAD_TIMEOUT_SECONDS,
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_ENTRIES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_SOURCE_BYTES,
    MAX_UNCOMPRESSED_BYTES,
)
from app.github.client import get_github_headers, raise_for_github_error
from app.github.urls import get_github_tarball_url
from app.ingestion.filters import (
    get_content_exclusion_reason,
    get_excluded_directory,
    get_path_exclusion_reason,
)


# files: path --> text of every file that is kept
# excluded: path (or folder ending in "/") --> reason, so it is clear why something
# is missing from the index
@dataclass
class ExtractedFiles:
    files: dict[str, str] = field(default_factory=dict)
    excluded: dict[str, str] = field(default_factory=dict)


# ---------------------- Downloads the repository tarball ---------------------
# One request for the whole repository instead of one contents request per file.
# GitHub answers with a redirect to codeload.github.com, so redirects are followed.
# httpx drops the Authorization header when it is redirected to a different host,
# so the user's token is only ever sent to api.github.com
async def download_tarball(
    github_token: str,
    owner: str,
    repo_name: str,
    commit_sha: str,
) -> bytes:
    archive_parts = []
    archive_size = 0

    try:
        # httpx timeouts apply to each read, so a server sending one byte at a time
        # would never time out. asyncio.timeout() limits the whole download instead
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                async with client.stream(
                    "GET",
                    get_github_tarball_url(owner, repo_name, commit_sha),
                    headers=get_github_headers(github_token),
                ) as response:
                    if not response.is_success:
                        await response.aread()
                        raise_for_github_error(
                            response,
                            "Repository archive could not be downloaded",
                            {404: "Repository not found or you don't have access to it"},
                        )

                    # The archive is streamed so the download stops as soon as it gets too big,
                    # instead of downloading everything and only then checking the size
                    async for part in response.aiter_bytes():
                        archive_size += len(part)
                        if archive_size > MAX_ARCHIVE_BYTES:
                            raise HTTPException(status_code=413, detail="Repository is too large to index")
                        archive_parts.append(part)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Repository download took too long")

    return b"".join(archive_parts)


# GitHub wraps everything in one top folder: "<owner>-<repo>-<short sha>/src/app.py"
# This removes that folder and rejects any path that could point outside the repository.
# Nothing is ever written to disk, but unsafe paths are still rejected so they can't
# be shown in the UI or used in citations
def get_repository_path(member_name: str) -> str | None:
    if member_name.startswith("/") or "\\" in member_name:
        return None

    # The top folder is checked too, otherwise "../outside.py" would lose its ".."
    # as if it was the top folder and be kept as "outside.py"
    parts = member_name.split("/")
    if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
        return None

    return "/".join(parts[1:])


# ---------------------- Reads the files inside the tarball -------------------
# This is CPU work (decompressing), so callers run it in a thread with
# asyncio.to_thread() to avoid blocking every other request on the server
def extract_files(archive_bytes: bytes) -> ExtractedFiles:
    try:
        return _read_archive(archive_bytes)
    except (tarfile.TarError, EOFError, OSError, zlib.error):
        # Corrupt or truncated archives can fail at any point while reading, not only on open
        raise HTTPException(status_code=502, detail="Repository archive could not be read")


def _read_archive(archive_bytes: bytes) -> ExtractedFiles:
    extracted = ExtractedFiles()
    source_bytes = 0
    uncompressed_bytes = 0
    top_folder = None

    with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as archive:
        for entry_count, member in enumerate(archive, start=1):
            # Every entry is decompressed even if it is skipped, so the archive itself is
            # bounded. Otherwise a small "zip bomb" could keep the server busy for minutes
            uncompressed_bytes += member.size
            if entry_count > MAX_ARCHIVE_ENTRIES or uncompressed_bytes > MAX_UNCOMPRESSED_BYTES:
                raise HTTPException(status_code=413, detail="Repository archive is too large to index")

            # A GitHub tarball has exactly one top folder. Anything outside it is ignored
            member_top_folder = member.name.split("/")[0]
            if top_folder is None:
                top_folder = member_top_folder
            if member_top_folder != top_folder:
                continue

            path = get_repository_path(member.name)
            if path is None or path in extracted.files:
                continue

            excluded_directory = get_excluded_directory(path)
            if excluded_directory:
                # Recorded once per folder, so a committed node_modules/ is one entry, not thousands
                extracted.excluded.setdefault(excluded_directory, "dependencies, build output or cache")
                continue

            # Directories are rebuilt from the file paths. Symlinks and hardlinks could
            # point anywhere, so only regular files are read
            if member.isdir():
                continue
            if not member.isreg():
                extracted.excluded[path] = "link, not a regular file"
                continue

            # Lockfiles, binaries, credentials... are skipped by their path, before being read
            path_reason = get_path_exclusion_reason(path)
            if path_reason:
                extracted.excluded[path] = path_reason
                continue

            # The size is in the tar header, so big files are skipped without reading them
            if member.size > MAX_FILE_BYTES:
                extracted.excluded[path] = f"larger than {MAX_FILE_BYTES // 1000} KB"
                continue

            if len(extracted.files) >= MAX_FILES:
                extracted.excluded[path] = "repository file limit reached"
                continue

            if source_bytes + member.size > MAX_SOURCE_BYTES:
                extracted.excluded[path] = "repository size limit reached"
                continue

            file_object = archive.extractfile(member)
            if file_object is None:
                continue

            raw_content = file_object.read()
            content_reason = get_content_exclusion_reason(raw_content)
            if content_reason:
                extracted.excluded[path] = content_reason
                continue

            try:
                content = raw_content.decode("utf-8")
            except UnicodeDecodeError:
                extracted.excluded[path] = "not a UTF-8 text file"
                continue

            extracted.files[path] = content
            source_bytes += member.size

    return extracted
