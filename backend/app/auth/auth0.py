import asyncio
import time

from fastapi import HTTPException
import httpx

from app.config import AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_DOMAIN


# ---------------- Management API token cache ----------------
# Shared by all requests: this token belongs to the application, not to a user
_management_token: str | None = None
_management_token_expires_at: float = 0.0
_management_token_lock = asyncio.Lock()

# Refresh the token this many seconds before it actually expires
TOKEN_EXPIRY_MARGIN_SECONDS = 60


# It gets authorization header as a parameter
# Uses this header to return the authenticated user's Auth0 ID (which was previously hardcoded)
async def get_authenticated_user_id(authorization: str | None) -> str:
    # Raises error if authorization is an empty string (or None) or doesn't start with "Bearer"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    # Removes Bearer and whitespaces from the start and end of the authorization header
    access_token = authorization.removeprefix("Bearer ").strip()

    # Get user information from Auth0 using access token
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://{AUTH0_DOMAIN}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    # If the request did not work, error 401 Unauthorized
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # The sub field has the Auth0 ID: e.g. {"sub": "github|123456", "name": "ZainX38"}
    user_id = response.json().get("sub")

    # Reject response if Auth0 didn't provide a "sub" value
    if not user_id:
        raise HTTPException(status_code=404, detail="Unable to identify user")

    return user_id


# --------------------- Gets Auth0 Management API Access Token -----------------
async def get_management_token() -> str:
    global _management_token, _management_token_expires_at

    # 1. Fast path: valid cached token --> reuse it
    if _management_token and time.monotonic() < _management_token_expires_at:
        print("[auth0] Using CACHED Management API token")
        return _management_token

    # 2. Only one request at a time can fetch a new token
    async with _management_token_lock:
        # 3. Check again: another request may have refreshed it while we waited
        if _management_token and time.monotonic() < _management_token_expires_at:
            print("[auth0] Token was refreshed by another request while waiting")
            return _management_token

        async with httpx.AsyncClient() as client:
            # The Auth0 app must be configured with the scopes:
            # read:users and read:user_idp_tokens
            response = await client.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": AUTH0_CLIENT_ID,
                    "client_secret": AUTH0_CLIENT_SECRET,
                    "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
                },
            )

        response.raise_for_status()

        # 4. Save the token and when it expires
        token_data = response.json()
        _management_token = token_data["access_token"]
        _management_token_expires_at = (
            time.monotonic() + token_data["expires_in"] - TOKEN_EXPIRY_MARGIN_SECONDS
        )

        print(f"[auth0] Requested NEW Management API token (valid for {token_data['expires_in']}s)")
        return _management_token
