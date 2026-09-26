from base64 import b64encode

import httpx
import respx


# Uses the real get_authenticated_user_id: a missing header is rejected before any network call
def test_repositories_requires_authentication(client):
    response = client.get("/api/repositories")

    assert response.status_code == 401


@respx.mock
def test_repositories_returns_only_the_fields_the_frontend_uses(client, authenticated):
    respx.get("https://api.github.com/user/repos").mock(
        return_value=httpx.Response(200, json=[
            {"name": "demo", "description": None, "private": True, "owner": {"login": "ZainX38"}, "id": 1},
        ])
    )

    response = client.get("/api/repositories", headers=authenticated)

    assert response.status_code == 200
    assert response.json() == [
        {"name": "demo", "description": None, "private": True, "owner": "ZainX38"},
    ]


@respx.mock
def test_contents_decodes_a_text_file(client, authenticated):
    respx.get("https://api.github.com/repos/ZainX38/demo/contents/src/app.py").mock(
        return_value=httpx.Response(200, json={
            "type": "file",
            "name": "app.py",
            "path": "src/app.py",
            "content": b64encode(b"print('hi')\n").decode(),
        })
    )

    response = client.get("/api/repositories/ZainX38/demo/contents/src/app.py", headers=authenticated)

    assert response.status_code == 200
    assert response.json() == {"name": "app.py", "path": "src/app.py", "content": "print('hi')\n"}


@respx.mock
def test_contents_maps_github_not_found(client, authenticated):
    respx.get("https://api.github.com/repos/ZainX38/demo/contents").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    response = client.get("/api/repositories/ZainX38/demo/contents", headers=authenticated)

    assert response.status_code == 404
    assert response.json()["detail"] == "Repository or path not found"
