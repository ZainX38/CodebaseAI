from urllib.parse import quote

from fastapi import HTTPException
import httpx

from app.auth.auth0 import get_management_token
from app.config import AUTH0_DOMAIN


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
