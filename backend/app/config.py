import os


# If getting errors with environment variables, start the server with:
# uv run --env-file .env fastapi dev main.py
AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]

# Application metadata and CORS policy used when the FastAPI app is created
APP_TITLE = "Codebase AI"
ALLOWED_ORIGINS = ["http://localhost:3000"]
ALLOWED_METHODS = ["POST", "GET"]
ALLOWED_HEADERS = ["Authorization", "Content-Type"]
