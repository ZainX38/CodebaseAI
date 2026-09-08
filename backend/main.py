from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from typing import Annotated

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

# ------------------ Displays the data in the dictionary ----------------
@app.get("/api/data")
async def display_user_data():
    return {"data": user_dict}

