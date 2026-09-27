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

# ---------------- Repository ingestion limits ----------------
# The whole repository is downloaded as one compressed tarball, so these caps
# stop a huge repository from filling memory before anything gets indexed
MAX_ARCHIVE_BYTES = 60_000_000  # compressed tarball size
MAX_FILE_BYTES = 200_000  # bigger files are usually data or generated code
MAX_SOURCE_BYTES = 30_000_000  # total size of all files that are kept
MAX_FILES = 10_000
# gzip can expand 60 MB into many GB, so the archive itself is bounded too, even
# for entries that end up excluded (they still have to be decompressed to skip them)
MAX_ARCHIVE_ENTRIES = 100_000
MAX_UNCOMPRESSED_BYTES = 500_000_000
DOWNLOAD_TIMEOUT_SECONDS = 120  # for the whole download, not each read
