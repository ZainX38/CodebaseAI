# Codebase AI

A full stack app that reads a GitHub repository and answers questions about its code. You log in with GitHub, choose one of your repositories, index it and ask questions in a chat panel next to the file explorer. Every answer comes with **citations** that point to the exact lines it was based on, so it can be checked.

I built it because, when working on a repository with dozens or hundreds of files, I kept forgetting what a specific file did or where something was implemented. It is also a learning project: the aim is to understand how a **RAG (Retrieval-Augmented Generation)** system works from end to end, from downloading a repository and chunking its code up to streaming a cited answer to the browser. It runs locally with **Ollama** by default, so no paid AI API is needed.

> **Note:** the model never sees the whole repository. It only sees the excerpts that retrieval selects, so a citation proves *where* a claim came from, not that the claim is correct.

## Features

- **Login with GitHub.** Auth0 handles the login, and the backend gets the user's linked GitHub token through the Auth0 Management API. This means private repositories work too, and tokens never reach the browser.
- **Repository explorer.** A file tree that loads folders as they are opened, and a file viewer.
- **Safe ingestion.** The repository is downloaded as one tarball pinned to the latest commit of the default branch. Path traversal, links and zip bombs are rejected, and `node_modules`, lockfiles, binaries, generated files, credentials and big files are filtered out.
- **AST chunking.** Python files are split into one chunk per function, class and method using the `ast` module. Other languages, and Python that doesn't parse, are split into overlapping line windows.
- **Background indexing.** Chunks are embedded and stored in Pinecone, with one namespace per user, repository and commit. File text, chunks and job status are saved in SQLite.
- **Hybrid retrieval.** Vector search, BM25 keyword search and an exact symbol lookup for functions named in the question are merged with reciprocal rank fusion, and the top candidates are reranked.
- **Complete function bodies.** The model gets whole functions within a token budget, and functions that don't fit are labelled as partial.
- **Validated citations.** Repository code is treated as untrusted data. Every excerpt is fenced with a random code, the answer is structured JSON, and any citation that wasn't one of the retrieved excerpts is rejected.
- **Streaming chat.** Answers are streamed with Server-Sent Events. Clicking a citation opens the file at the indexed commit and highlights the cited lines.
- **Evaluation.** 18 questions on [psf/requests](https://github.com/psf/requests), run in 3 stages and scored with RAGAS (see [Evaluation](#evaluation)).

## Tech Stack

### Frontend
- Next.js 16 (App Router), React 19 and TypeScript
- Tailwind CSS v4
- Auth0 (`@auth0/nextjs-auth0`) for login with GitHub
- `route.ts` Route Handlers as a backend-for-frontend (BFF), so tokens stay on the server

### Backend
- Python 3.14 and FastAPI, managed with `uv`
- `httpx` for every call to Auth0 and GitHub
- Pinecone for vectors, SQLite for file text, chunks and job status
- Ollama (`qwen3-embedding:0.6b` and `qwen3.5:4b`) by default, with OpenAI as an option
- pytest, with every real network call blocked during tests

## How It Works

The browser never talks to the backend directly. Every request goes through a Next.js `route.ts`, which adds the user's Auth0 access token. The backend uses that token to find out who the user is and to get their GitHub token.

```
Browser ──▶ Next.js route.ts ──▶ FastAPI ──▶ Auth0 /userinfo        (who is this?)
                (adds token)        │    ──▶ Auth0 Management API  (linked GitHub token)
                                    │    ──▶ GitHub API            (with the user's token)
                                    ▼
                          JSON or SSE back to the browser
```

A repository is indexed once per commit. After that, every question goes through retrieval, context assembly and generation:

```
Indexing:   download tarball ─▶ filter files ─▶ chunk code ─▶ embed ─▶ Pinecone + SQLite
                                                                             │
Question:   hybrid retrieval ◀───────────────────────────────────────────────┘
               │  (vector + BM25 + symbols, fused and reranked)
               ▼
            assemble context ─▶ generate answer ─▶ validate citations ─▶ stream to chat
```

## Getting Started

### Requirements

- Node.js
- Python 3.14 with [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com)
- An **Auth0** tenant with the GitHub social connection enabled, a Regular Web Application for the frontend, and a Machine to Machine application with the `read:users` and `read:user_idp_tokens` permissions for the backend
- A **Pinecone** API key from [app.pinecone.io](https://app.pinecone.io)

### 1. Download the models

```bash
ollama pull qwen3-embedding:0.6b
ollama pull qwen3.5:4b
```

### 2. Start the backend

```bash
cd backend
cp .env.example .env      # then fill in the values
uv sync
uv run --env-file .env fastapi dev main.py
```

The backend runs on `http://localhost:8000`. If you get errors about missing environment variables, make sure the server was started with `--env-file .env`.

### 3. Start the frontend

Create `frontend/.env.local`:

```
AUTH0_DOMAIN=
AUTH0_CLIENT_ID=
AUTH0_CLIENT_SECRET=
AUTH0_SECRET=                    # any long random string, e.g. openssl rand -hex 32
APP_BASE_URL=http://localhost:3000
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

Then run:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000), log in with GitHub, choose a repository, press index and start asking questions once it is ready.

### Backend configuration

All values go in `backend/.env`. Only the Auth0 values and `PINECONE_API_KEY` are required, everything else has a default.

| Variable | Default | What it does |
| --- | --- | --- |
| `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET` | | Machine to Machine app used to get the linked GitHub token |
| `PINECONE_API_KEY` | | Where chunk embeddings are stored |
| `PINECONE_CLOUD`, `PINECONE_REGION` | `aws`, `us-east-1` | Where the Pinecone index is created |
| `AI_PROVIDER` | `ollama` | `ollama` runs everything locally, `openai` needs `OPENAI_API_KEY` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `OLLAMA_EMBEDDING_MODEL`, `OLLAMA_CHAT_MODEL` | `qwen3-embedding:0.6b`, `qwen3.5:4b` | Models used with Ollama |
| `OPENAI_EMBEDDING_MODEL`, `OPENAI_CHAT_MODEL` | `text-embedding-3-small`, `gpt-6-luna` | Models used with OpenAI |
| `AI_TIMEOUT_SECONDS` | `180` | Timeout for each model request |
| `DATA_DIR` | `backend/data` | Where the SQLite database is saved |

Changing the embedding model needs a new index, so the Pinecone index name includes the model name.

## Tests

```bash
cd backend
uv run pytest

cd frontend
npm run lint
```

The tests never call a real model, Auth0, GitHub or Pinecone. Models are replaced with fakes, and any real network call makes the test fail.

## Evaluation

To check whether each part of the pipeline actually improves the answers, the evaluation asks 18 questions about `psf/requests`. Each question has a reference answer and the source lines it needs (`backend/evaluation/cases.json`). The questions are run in 3 stages with `qwen3.5:4b` and `deepseek-coder-v2:16b`:

| Stage | Retrieval | Context |
| --- | --- | --- |
| 1 | Vector only (the baseline) | Retrieved chunks |
| 2 | Hybrid (vector + BM25 + symbols) | Retrieved chunks |
| 3 | Hybrid | Complete function bodies |

Every setting is frozen before the first answer, and nothing is tuned between stages.

```bash
cd backend
uv run --env-file .env python -m evaluation.run_eval   # saves every answer
uv run python -m evaluation.summarise                  # builds review-sheet.csv and report.md

cd evaluation/ragas
uv run python ragas_score.py                           # faithfulness, answer relevancy, context precision
```

- The run can be stopped and started again, and it continues where it stopped.
- If Ollama or Pinecone keeps failing, the run stops without recording anything, so a service that is down is never counted as a model failure.
- RAGAS uses `qwen3.5:9b` as the judge. It has its own `uv` project because it needs older packages than the backend.
- Results are saved in `backend/evaluation/results/`.

## Project Structure

```
backend/
|-- main.py                 creates the FastAPI app and registers the routers
|-- app/
|    |-- auth/              Auth0 session and the linked GitHub identity
|    |-- github/            GitHub URLs, headers and repository data
|    |-- ingestion/         tarball download and file filters
|    |-- indexing/          chunking, Pinecone, SQLite store, background jobs
|    |-- retrieval/         BM25, symbol lookup, fusion and rerank
|    |-- rag/               context, prompt, citations and answers
|    |-- ai/                Ollama and OpenAI providers
|    |-- routers/           one router per feature
|-- tests/                  pytest, models always mocked
|-- evaluation/             3-stage evaluation on psf/requests
frontend/
|-- app/api/...             route.ts handlers that call the backend
|-- app/components/         Profile, repository-explorer, chat-panel
|-- lib/api.ts              helpers that build URLs and parse responses
docs/                       overview, milestones, journey and concept notes
```

### API

| Method | Path | What it does |
| --- | --- | --- |
| `GET` | `/api/repositories` | Lists the user's GitHub repositories |
| `GET` | `/api/repositories/{owner}/{repo}/contents/{path}` | File or folder contents, optionally at a commit with `?ref=<sha>` |
| `POST` | `/api/repositories/{owner}/{repo}/index` | Starts indexing the latest commit of the default branch |
| `GET` | `/api/repositories/{owner}/{repo}/index/status` | Status of the indexing job |
| `POST` | `/api/repositories/{owner}/{repo}/chat` | Answers a question, streamed with SSE |

## Limitations

- AST chunking and the symbol lookup only understand Python. Other languages use line windows, but they still work with vector and keyword search.
- Indexing jobs run inside the server process, so if the server stops while a job is running, that job is marked as failed on the next start.
- With Ollama running on CPU only, indexing a large repository and generating answers is slow.
- Repositories are limited to 10,000 files, 30 MB of source code and 200 KB per file.used (BM25, reciprocal rank fusion, token budgets, SSE, RAGAS and more) in `docs/Concepts/`.
