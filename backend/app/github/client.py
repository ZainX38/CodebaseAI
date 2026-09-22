from fastapi import HTTPException
import httpx


# Every GitHub API call is authenticated with the user's token and asks for the
# recommended media type so the response shape stays stable
def get_github_headers(github_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }


# It preserves all client error codes (4xx) but all server errors (5xx) get mapped to 502
# details_by_status allows an endpoint to explain specific client errors to the frontend
def raise_for_github_error(
    response: httpx.Response,
    default_detail: str,
    details_by_status: dict[int, str] | None = None,
) -> None:
    if response.is_success:
        return

    status_code = response.status_code if response.status_code < 500 else 502
    detail = (details_by_status or {}).get(response.status_code, default_detail)

    raise HTTPException(status_code=status_code, detail=detail)
