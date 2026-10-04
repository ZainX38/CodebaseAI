import os

# app.config reads the Auth0 settings as soon as it is imported, so fake values
# have to exist before any test imports the app. They overwrite real values (e.g.
# from --env-file .env) so a test can never call the real Auth0 tenant.
os.environ["AUTH0_DOMAIN"] = "test.auth0.local"
os.environ["AUTH0_CLIENT_ID"] = "test-client-id"
os.environ["AUTH0_CLIENT_SECRET"] = "test-client-secret"

import socket

import pytest
from fastapi.testclient import TestClient

from main import app


TEST_USER_ID = "github|123456"
TEST_GITHUB_TOKEN = "gho_test_token"


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
OLLAMA_PORT = 11434
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


# Unit tests must never reach a real API (GitHub, Auth0, OpenAI, Pinecone).
# If a mock is missing, the test fails here instead of silently sending a request.
# DNS is blocked too, because asyncio on Windows connects without socket.connect()
@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # localhost has to stay open (asyncio uses local sockets internally on Windows),
    # but a real Ollama on its default port is blocked, so a missing mock can't call it
    def guarded_getaddrinfo(host, port=None, *args, **kwargs):
        # Some clients pass the host as bytes, e.g. b"api.openai.com"
        name = host.decode() if isinstance(host, bytes) else host
        if name not in LOCAL_HOSTS or str(port) == str(OLLAMA_PORT):
            raise RuntimeError(f"Tests must not use the network (tried to resolve {host}:{port})")
        return _real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(sock, address):
        host, port = (address[0], address[1]) if isinstance(address, tuple) else (address, None)
        if host not in LOCAL_HOSTS or port == OLLAMA_PORT:
            raise RuntimeError(f"Tests must not use the network (tried to connect to {host}:{port})")
        return _real_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# Replaces the Auth0 -> GitHub token flow so endpoints can be tested on their own.
# Each router imports the functions by name, so they are patched where they are used.
@pytest.fixture
def authenticated(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    async def fake_user_id(authorization: str | None) -> str:
        if authorization != "Bearer test-session":
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="Authentication required")
        return TEST_USER_ID

    async def fake_github_token(user_id: str) -> str:
        return TEST_GITHUB_TOKEN

    from app.routers import repositories

    monkeypatch.setattr(repositories, "get_authenticated_user_id", fake_user_id)
    monkeypatch.setattr(repositories, "get_github_token", fake_github_token)

    return {"Authorization": "Bearer test-session"}
