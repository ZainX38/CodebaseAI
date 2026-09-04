from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any

app = FastAPI(title="Codebase AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class UserData(BaseModel):
    owner: str
    repo_name: str

user_dict = {}

@app.post("/api/repo")
async def get_user_data(user_data: UserData):
    print(user_data.owner)
    user_dict["owner"] = user_data.owner
    user_dict["repo_name"] = user_data.repo_name
    return user_dict

@app.get("/api/repo")
async def display_user_data():
    return user_dict