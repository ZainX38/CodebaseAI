import os

# app.config reads the Auth0 settings as soon as it is imported, so fake values
# have to exist before any test imports the app. They overwrite real values (e.g.
# from --env-file .env) so a test can never call the real Auth0 tenant.
os.environ["AUTH0_DOMAIN"] = "test.auth0.local"
os.environ["AUTH0_CLIENT_ID"] = "test-client-id"
os.environ["AUTH0_CLIENT_SECRET"] = "test-client-secret"

import pytest
from fastapi.testclient import TestClient

from main import app


TEST_USER_ID = "github|123456"
TEST_GITHUB_TOKEN = "gho_test_token"


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
