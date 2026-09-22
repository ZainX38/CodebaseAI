from fastapi import HTTPException
import httpx

from app.config import AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_DOMAIN


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
    async with httpx.AsyncClient() as client:
        # This is the OAuth2 flow used by the application to access the Auth0 Management API
        # A POST request is sent with all the data by submitting information to Auth0
        # The Auth0 app must be configured with the following scopes for this to work:
        # read:users and read:user_idp_tokens
        # These permissions aren't granted in the code but in the Auth0 application
        response = await client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
            },
        )

    # Raise an HTTPX exception for an unsuccessful response.
    # Prevent extracting an access token from a failed request.
    response.raise_for_status()

    return response.json()["access_token"]
