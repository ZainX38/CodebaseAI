from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    ALLOWED_HEADERS,
    ALLOWED_METHODS,
    ALLOWED_ORIGINS,
    APP_TITLE,
)
from app.routers import repositories


app = FastAPI(title=APP_TITLE)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=ALLOWED_METHODS,
    allow_headers=ALLOWED_HEADERS,
)

# Each feature lives in its own router module and is registered here
app.include_router(repositories.router)
