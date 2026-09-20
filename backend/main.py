from base64 import b64decode
import os
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx


app = FastAPI(title="Codebase AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type"],
)


# If getting errors with environment variables, start the server with:
# uv run --env-file .env fastapi dev main.py
AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]


# It gets authorization header as a parameter
# Uses this header to return the authenticated user's Auth0 ID (which was previously hardcoded)
async def get_authenticated_user_id(authorization: str | None) -> str:
    # Raises error if authorization is an empty string (or None) or doesn't start with "Bearer"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    # Removes Bearer and whitespaces from the start and end of the authorization header
    access_token = authorization.removeprefix("Bearer ").strip()

    # Get user information from Auth0 using access token
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://{AUTH0_DOMAIN}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    # If the request did not work, error 401 Unauthorized
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # The sub field has the Auth0 ID: e.g. {"sub": "github|123456", "name": "ZainX38"}
    user_id = response.json().get("sub")

    # Reject response if Auth0 didn't provide a "sub" value
    if not user_id:
        raise HTTPException(status_code=404, detail="Unable to identify user")

    return user_id


# --------------------- Gets Auth0 Management API Access Token -----------------
async def get_management_token() -> str:
    async with httpx.AsyncClient() as client:
        # This is the OAuth2 flow used by the application to access the Auth0 Management API
        # A POST request is sent with all the data by submitting information to Auth0
        # The Auth0 app must be configured with the following scopes for this to work:
        # read:users and read:user_idp_tokens
        # These permissions aren't granted in the code but in the Auth0 application
        response = await client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
            },
        )

    # Raise an HTTPX exception for an unsuccessful response.
    # Prevent extracting an access token from a failed request.
    response.raise_for_status()

    return response.json()["access_token"]


# ------------------------- Gets GitHub Access Token ------------------------
async def get_github_token(user_id: str) -> str:
    management_token = await get_management_token()

    # URL encoding turns unsafe characters, in this case | from the user id, into a suitable URL path
    encoded_user_id = quote(user_id, safe="")

    # Retrieves the user's Auth0 profile information, including linked-identity
    # Management API token authorizes access to the endpoint
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://{AUTH0_DOMAIN}/api/v2/users/{encoded_user_id}",
            headers={"Authorization": f"Bearer {management_token}"},
        )

    response.raise_for_status()

    # Gets the identities array inside the response dictionary
    # If it doesn't exist, it returns an empty array to prevent error when iterating over the array
    identities = response.json().get("identities", [])

    # This is just an iteration until "provider" == "github"
    # FOR loop could also be used
    # It finds the first linked GitHub identity.
    # The generator stops at the first match; next() returns None if none exists.
    # This prevents unecessary iterations
    github_identity = next(
        (identity for identity in identities if identity.get("provider") == "github"),
        None,
    )

    # Return error if: no GitHub identity was found OR identity exists but no token found
    if not github_identity or not github_identity.get("access_token"):
        raise HTTPException(
            status_code=403,
            detail="A GitHub connection is required to access repository contents",
        )

    return github_identity["access_token"]


# Synchronous operation as it is only string manipulation and no network requests to wait
def get_github_contents_url(owner: str, repo_name: str, path: str) -> str:
    encoded_owner = quote(owner, safe="")
    encoded_repo_name = quote(repo_name, safe="")
    encoded_path = "/".join(quote(path_part, safe="") for path_part in path.split("/") if path_part)
    github_api_url = f"https://api.github.com/repos/{encoded_owner}/{encoded_repo_name}/contents"

    if encoded_path:
        github_api_url = f"{github_api_url}/{encoded_path}"

    return github_api_url


# ---------------------- Gets repository contents from GitHub API --------------
# The first route handles the repository root.
# The second handles nested paths; {path:path} captures the remaining URL
# path, including "/" separators, into the parameter named "path".
@app.get("/api/repositories/{owner}/{repo_name}/contents")
@app.get("/api/repositories/{owner}/{repo_name}/contents/{path:path}")
async def get_repository_contents(
    owner: str,
    repo_name: str,
    path: str = "",
    authorization: Annotated[str | None, Header()] = None,
):
    user_id = await get_authenticated_user_id(authorization)
    github_token = await get_github_token(user_id)
    github_api_url = get_github_contents_url(owner, repo_name, path)

    # Request the specified file or directory using the github access token
    async with httpx.AsyncClient() as client:
        response = await client.get(
            github_api_url,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
            },
        )

    # It preserves all client error codes (4xx) but all server errors (5xx) get mapped to 502
    if not response.is_success:
        status_code = response.status_code if response.status_code < 500 else 502
        detail = "Repository contents could not be loaded"
        if response.status_code == 404:
            detail = "Repository or path not found"
        elif response.status_code == 403:
            detail = "GitHub denied access to this repository"

        raise HTTPException(status_code=status_code, detail=detail)

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
