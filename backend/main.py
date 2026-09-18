from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from typing import Annotated

import httpx
import os
from urllib.parse import quote

app = FastAPI(title="Codebase AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Schema degines the data received from the request body
class UserData(BaseModel):
    owner: str
    repo_name: str


# Dictionary to temporary store user data (owner, repo_name, authorization token etc.)
# Used only for development
# TODO: create an authentic database
user_dict = {}

# ------------- This function gets the POST request from the frontend ---------------

# user_data must be of type UserData (pydantic) which is how the request body is sent from the frontend
# This MUST always be done as if not FastAPI would confuse user_data with query parameters
# e.g. can't do owner: str and repo_name: str in the parameters as FastAPI will misinterpret it for a Query Parameter
# So always define a schema for request bodies

# Request has all the information about the request including the headers
# This is good but FastAPI recommends using their features like user_data: UserData to get the request body
# Or using Annotated and Header() to get the desired headers
@app.post("/api/data")
async def get_user_data(user_data: UserData, request: Request, authorization: Annotated[str | None, Header()] = None):
    user_dict["owner"] = user_data.owner
    user_dict["repo_name"] = user_data.repo_name
    # user_dict["headers"] = dict(request.headers)
    user_dict["authorization"] = authorization

    return user_dict


# ---------------------- Gets repository data from GitHub API --------------
@app.get("/api/repo")
async def get_user_repo():
    github_api = "https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    # Replace owner, repo and path values with user data
    github_api_url = github_api.format(
        owner=user_dict["owner"],
        repo=user_dict["repo_name"],
        path="")

    user_id = "github|152643175"

    github_token = await get_github_token(user_id)

    # Gets public repository data
    # Public as this does not need authorization to access
    async with httpx.AsyncClient() as client:
        response = await client.get(
            github_api_url,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
            }
        )
        repo_data = response.json()

    return repo_data

# --------------------- Gets Auth0 Management API Access Token -----------------

# If getting error with env variables, start server with command: uv run --env-file .env fastapi dev
# This command loads the env variables before running application
AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]

async def get_management_token() -> str:
    async with httpx.AsyncClient() as client:
        # This is just OAuth2 flow to access the access token through Auth0 Management API
        response = await client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            data={
                "grant_type": "client_credentials",         # Shows app is authenticating itself, and not through an interface
                "client_id": AUTH0_CLIENT_ID,               # Identidies client application
                "client_secret": AUTH0_CLIENT_SECRET,       # Shows that you have the "secret" to access the app
                "audience": f"https://{AUTH0_DOMAIN}/api/v2/"   # Specifies the API for which access token is requested
            },
        )

        response.raise_for_status()

        data = response.json()

        return data["access_token"]

# ------------------------- Get GitHub Access Token ------------------------
async def get_github_token(user_id="github|152643175") -> str:
    management_token = await get_management_token()

    # URL encoding turns unsafe characters, in this case | from the user id into suitable for the URL path
    # "|" becomes "%7C"
    encoded_user_id = quote(user_id, safe="")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://{AUTH0_DOMAIN}/api/v2/users/{encoded_user_id}",
            headers={
                "Authorization": f"Bearer {management_token}",
            },
        )

        response.raise_for_status()

        user_data = response.json()

        return user_data["identities"][0]["access_token"]