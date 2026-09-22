from base64 import b64decode
from typing import Any

from fastapi import HTTPException
import httpx

from app.github.client import get_github_headers, raise_for_github_error
from app.github.urls import get_github_contents_url, get_github_repositories_url


# ---------------------- Gets repositories from GitHub API --------------------
async def list_repositories(github_token: str) -> list[dict[str, Any]]:
    repositories = []
    page = 1
    per_page = 100

    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                get_github_repositories_url(),
                headers=get_github_headers(github_token),
                params={
                    "visibility": "all",
                    "affiliation": "owner,collaborator,organization_member",
                    "per_page": per_page,
                    "page": page,
                },
            )

            raise_for_github_error(response, "Repositories could not be loaded")

            page_repositories = response.json()
            repositories.extend(page_repositories)

            if len(page_repositories) < per_page:
                break

            page += 1

    return [
        {
            "name": repository["name"],
            "description": repository.get("description"),
            "private": repository["private"],
            "owner": repository["owner"]["login"],
        }
        for repository in repositories
    ]


# ---------------------- Gets repository contents from GitHub API --------------
async def get_repository_contents(
    github_token: str,
    owner: str,
    repo_name: str,
    path: str,
) -> list[dict[str, Any]] | dict[str, Any]:
    github_api_url = get_github_contents_url(owner, repo_name, path)

    # Request the specified file or directory using the github access token
    async with httpx.AsyncClient() as client:
        response = await client.get(
            github_api_url,
            headers=get_github_headers(github_token),
        )

    raise_for_github_error(
        response,
        "Repository contents could not be loaded",
        {
            404: "Repository or path not found",
            403: "GitHub denied access to this repository",
        },
    )

    # response is parsed from json to Python data structures
    # GitHub's repository endpoint can return 2 different responses:
    # 1. Normally, it requests an array of directory entries, so after parsing: JSON array --> Python list
    # 2. If an individual file is requested, a JSON object is returned which becomes a Python dictionary
    contents = response.json()

    # isinstance checks if the variable contents is a list and if it is, it will return an array in
    # the specified format for each item in contents
    if isinstance(contents, list):
        return [
            {
                "name": item["name"],
                "path": item["path"],
                "type": item["type"],
            }
            for item in contents
        ]

    # An error gets raised instead of returned. This is because a return is treated by FastAPI as a 200 OK
    # If we raise an error with HTTPException, we are signaling the error and terminate endpoint processing
    if contents.get("type") != "file":
        raise HTTPException(status_code=415, detail="This repository item cannot be displayed")

    try:
        # GitHub represents small file contents as Base64-encoded text.
        # b64decode --> decodes the file into normal bytes e.g. "asds21a" --> b"Hello"
        # .decode("utf-8") --> removes the b (which stands for bytes) so b"Hello" --> "Hello"
        file_content = b64decode(contents["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=415, detail="This file is not a text file")

    # Extract only the requiered metadata by the frontend and return it
    return {
        "name": contents["name"],
        "path": contents["path"],
        "content": file_content,
    }
