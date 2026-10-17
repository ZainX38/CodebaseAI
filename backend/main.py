from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    ALLOWED_HEADERS,
    ALLOWED_METHODS,
    ALLOWED_ORIGINS,
    APP_TITLE,
)
from app.indexing.store import get_store
from app.routers import chat, indexing, repositories


# Runs once when the server starts. Indexing jobs live inside this process, so any
# job that was still running when the server stopped can never finish
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store().fail_interrupted_jobs()
    yield


app = FastAPI(title=APP_TITLE, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=ALLOWED_METHODS,
    allow_headers=ALLOWED_HEADERS,
)

# Each feature lives in its own router module and is registered here
app.include_router(repositories.router)
app.include_router(indexing.router)
app.include_router(chat.router)
