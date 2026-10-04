import os
from pathlib import Path


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

# ---------------- Chunking ----------------
# A chunk must fit comfortably in one embedding request and still be readable
# as a citation, so long functions are split into windows of this size
MAX_CHUNK_CHARS = 4_000
WINDOW_LINES = 60
WINDOW_OVERLAP_LINES = 10
# Minified or generated lines are kept in the file (line numbers stay correct)
# but never put into a chunk
MAX_LINE_CHARS = 3_000

# ---------------- AI providers ----------------
# "ollama" runs everything locally. "openai" needs OPENAI_API_KEY.
# Switching the embedding model needs a new index, so the Pinecone index name
# includes the model (see app/indexing/vector_store.py)
AI_PROVIDER = os.environ.get("AI_PROVIDER", "ollama").strip().lower()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
OLLAMA_CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen3.5:4b")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-6-luna")
AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "180"))
# Fixed before any evaluation run and never tuned afterwards
CONTEXT_TOKENS = 8192
MAX_OUTPUT_TOKENS = 700
EMBEDDING_BATCH_SIZE = 32

# ---------------- Pinecone + local store ----------------
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "").strip()
PINECONE_CLOUD = os.environ.get("PINECONE_CLOUD", "aws")
PINECONE_REGION = os.environ.get("PINECONE_REGION", "us-east-1")
# File text, chunks and job status live in SQLite, because BM25 and the symbol
# lookup need the text and Pinecone only needs the vectors
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
INDEX_DB_PATH = DATA_DIR / "index.sqlite"
