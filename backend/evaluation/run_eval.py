"""Runs the 3-stage evaluation on psf/requests and saves every answer.

    cd backend
    uv run --env-file .env python -m evaluation.run_eval

Stage 1: vector retrieval only, retrieved chunks as context (the baseline)
Stage 2: hybrid retrieval (vector + BM25 + symbol lookup), retrieved chunks as context
Stage 3: hybrid retrieval + implementation-aware context (complete function bodies)

Every setting, and a hash of all the code that answers, is frozen in settings.json
before the first answer. Nothing is tuned between stages.

What happens when something fails:
- the model's output is invalid (bad JSON, wrong shape, empty): recorded as that
  case's answer and never asked again, because that is a result
- Ollama or Pinecone fails (not running, timeout, connection): retried, and if it keeps
  failing the run stops WITHOUT recording anything, so a dead service can't be counted
  as a model failure. Starting the run again continues where it stopped
"""

import asyncio
from datetime import datetime, UTC
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

# The evaluation never uses Auth0, but app.config requires these to exist
for name in ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET"):
    os.environ.setdefault(name, "not-used-by-the-evaluation")

import httpx

from app import config
from app.ai.providers import OllamaProvider
from app.indexing import jobs
from app.indexing.store import IndexStore, get_store
from app.indexing.vector_store import QueryVectorStore, VectorStore, get_namespace, get_user_key
from app.rag.answer import AnswerResult, generate_answer
from app.retrieval.retriever import load_repository_index


EVALUATION_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVALUATION_DIR.parent
CASES_PATH = EVALUATION_DIR / "cases.json"

# The evaluation's own "user", so its index never mixes with a real user's namespaces
EVALUATION_USER_ID = "evaluation|psf-requests"

STAGES = {
    1: {"retrieval_mode": "vector", "context_mode": "chunks"},
    2: {"retrieval_mode": "hybrid", "context_mode": "chunks"},
    3: {"retrieval_mode": "hybrid", "context_mode": "implementation"},
}
MODELS = ["qwen3.5:4b", "deepseek-coder-v2:16b"]

INFRASTRUCTURE_RETRIES = 2
RETRY_WAIT_SECONDS = 30


class InfrastructureFailure(Exception):
    pass


def load_cases(path: Path = CASES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def results_dir(cases: dict[str, Any]) -> Path:
    _, name = cases["repository"].split("/")
    return EVALUATION_DIR / "results" / f"{name}-{cases['commit_sha'][:7]}"


def model_file_name(model: str) -> str:
    return model.replace(":", "-").replace("/", "-") + ".jsonl"


# Line endings are normalised first, so git converting LF/CRLF on checkout doesn't
# look like a changed file
def text_hash(paths: list[Path]) -> str:
    digest = sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(BACKEND_DIR)).replace("\\", "/").encode())
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return digest.hexdigest()


# Every file that can change an answer: retrieval, context, prompt, providers,
# citation checks, chunking constants... and this runner
def code_paths() -> list[Path]:
    return [*(BACKEND_DIR / "app").rglob("*.py"), Path(__file__).resolve()]


# ---------------------- Frozen settings ---------------------------------------
def get_ollama_info(models: list[str]) -> dict[str, Any]:
    response = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=30)
    response.raise_for_status()
    installed = {item["name"]: item["digest"] for item in response.json()["models"]}
    missing = [model for model in models if model not in installed]
    if missing:
        raise SystemExit(f"These Ollama models are not installed: {missing}")
    version = httpx.get(f"{config.OLLAMA_URL}/api/version", timeout=30).json().get("version")
    return {"version": version, "url": config.OLLAMA_URL, "digests": {model: installed[model] for model in models}}


def git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], capture_output=True, text=True, cwd=BACKEND_DIR).stdout.strip()


def build_settings(cases: dict[str, Any]) -> dict[str, Any]:
    return {
        "repository": cases["repository"],
        "commit_sha": cases["commit_sha"],
        "cases_sha256": text_hash([CASES_PATH]),
        "code_sha256": text_hash(code_paths()),
        "stages": STAGES,
        "models": MODELS,
        "embedding_model": config.OLLAMA_EMBEDDING_MODEL,
        "ollama": get_ollama_info([*MODELS, config.OLLAMA_EMBEDDING_MODEL]),
        "context_tokens": config.CONTEXT_TOKENS,
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
        "ai_timeout_seconds": config.AI_TIMEOUT_SECONDS,
    }


# Written once before the first answer and compared on every restart, so a resumed
# run can never mix answers from different code, prompts, models or settings
def check_or_save_settings(path: Path, settings: dict[str, Any]) -> None:
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        frozen = {key: value for key, value in saved.items() if key not in ("created_at", "git_commit")}
        if frozen != json.loads(json.dumps(settings)):
            changed = sorted(key for key in settings if json.loads(json.dumps(settings)).get(key) != frozen.get(key))
            raise SystemExit(f"Settings differ from {path} ({', '.join(changed)}); refusing to mix results")
        return
    path.write_text(
        json.dumps({**settings, "created_at": datetime.now(UTC).isoformat(), "git_commit": git("rev-parse", "HEAD")}, indent=2),
        encoding="utf-8",
    )


# The code that runs must be committed, so the frozen commit really is the code that answered
def require_committed_code() -> None:
    if git("status", "--porcelain", "--", "app", "evaluation/run_eval.py", "evaluation/cases.json"):
        raise SystemExit("Commit the backend code and cases.json before running the evaluation")


# ---------------------- Indexing the repository -------------------------------
# The same pipeline as the app: tarball at the pinned commit --> filters --> chunks
# --> Ollama embeddings --> Pinecone + SQLite. Requests is public, so no token is needed
async def ensure_index(cases: dict[str, Any], store: IndexStore) -> dict[str, Any]:
    owner, name = cases["repository"].split("/")
    user_key = get_user_key(EVALUATION_USER_ID)
    ready = store.get_latest_ready_job(user_key, cases["repository"])
    if ready and ready["commit_sha"] == cases["commit_sha"]:
        print(f"Index already ready: {ready['total']} chunks", flush=True)
        return ready

    metadata = {"owner": owner, "name": name, "default_branch": "main", "private": False}
    namespace = get_namespace(EVALUATION_USER_ID, owner, name, cases["commit_sha"])
    job, _ = store.create_job_unless_busy(user_key, cases["repository"], namespace, cases["commit_sha"])
    print("Indexing psf/requests at the pinned commit (about 30 minutes on CPU)...", flush=True)
    await jobs.run_indexing_job(job["id"], EVALUATION_USER_ID, "", metadata, cases["commit_sha"], store)

    finished = store.get_job(job["id"])
    if finished["status"] != "ready":
        raise SystemExit(f"Indexing failed: {finished['error']}")
    print(f"Indexed {finished['total']} chunks", flush=True)
    return finished


# A missing or partial index would quietly hurt stage 1 most (it only has vector
# search), so both SQLite and Pinecone must hold every chunk before anything is asked
async def check_index_is_complete(job: dict[str, Any], repository, provider) -> None:
    if len(repository.chunks) != job["total"]:
        raise SystemExit(f"SQLite has {len(repository.chunks)} chunks but the index has {job['total']}")

    [probe] = await provider.embed(["dimension probe"])
    vector_store = await asyncio.to_thread(VectorStore.connect, provider.embedding_model, len(probe), False)
    namespaces = (await asyncio.to_thread(vector_store.index.describe_index_stats)).namespaces
    count = namespaces[job["namespace"]].vector_count if job["namespace"] in namespaces else 0
    if count != job["total"]:
        raise SystemExit(f"Pinecone has {count} vectors for this namespace but the index has {job['total']}")
    print(f"Index complete: {count} chunks in SQLite and Pinecone", flush=True)


# ---------------------- Saved answers -----------------------------------------
def to_record(case: dict[str, Any], stage: int, result: AnswerResult) -> dict[str, Any]:
    return {
        "case_id": case["id"],
        "stage": stage,
        "answerable": case["answerable"],
        "reference": case["reference"],
        "recorded_at": datetime.now(UTC).isoformat(),
        **result.to_dict(),
    }


# A crash in the middle of writing can leave half a line at the end. It is removed,
# so the restart neither crashes on it nor glues the next record onto it
def read_done_case_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()

    lines = path.read_text(encoding="utf-8").splitlines()
    done, valid_lines = set(), []
    for line in lines:
        try:
            done.add(json.loads(line)["case_id"])
            valid_lines.append(line)
        except (json.JSONDecodeError, KeyError):
            print(f"Removing an incomplete line from {path.name}", flush=True)

    if len(valid_lines) != len(lines) or (lines and not path.read_text(encoding="utf-8").endswith("\n")):
        path.write_text("".join(line + "\n" for line in valid_lines), encoding="utf-8")
    return done


async def answer_case(case, stage, model, repository, provider, vector_store) -> AnswerResult:
    result = None
    async for event in generate_answer(case["question"], repository, provider, vector_store, model=model, **STAGES[stage]):
        if event.kind == "result":
            result = event.data
    return result


# Infrastructure failures (and unexpected exceptions) are retried; if they keep
# happening the whole run stops, and nothing is recorded for this case
async def answer_with_retries(case, stage, model, repository, provider, vector_store, wait=RETRY_WAIT_SECONDS):
    for attempt in range(INFRASTRUCTURE_RETRIES + 1):
        try:
            result = await answer_case(case, stage, model, repository, provider, vector_store)
            if result.error_kind != "infrastructure":
                return result
            problem = result.error
        except Exception as error:
            problem = f"{type(error).__name__}: {error}"
        print(f"  attempt {attempt + 1} failed ({problem}); waiting {wait}s", flush=True)
        await asyncio.sleep(wait)
    raise InfrastructureFailure(f"stage {stage}, {model}, case {case['id']}: {problem}")


# The first request to a model loads it into memory, which can take a while. A tiny
# request first means that loading time isn't counted in the first case's timing
# A failed warm-up is only logged: the first real case has its own retries
async def warm_up(provider, model: str) -> None:
    try:
        async for _ in provider.chat([{"role": "user", "content": "Reply with: ok"}], model=model, max_tokens=5):
            pass
    except Exception as error:
        print(f"Warm-up of {model} failed ({type(error).__name__}); continuing", flush=True)


async def run_all(cases, repository, provider, vector_store, output_dir: Path, stages=STAGES, models=MODELS, wait=RETRY_WAIT_SECONDS) -> int:
    answered = 0
    for stage in stages:
        for model in models:
            path = output_dir / f"stage-{stage}" / model_file_name(model)
            path.parent.mkdir(parents=True, exist_ok=True)
            done = read_done_case_ids(path)
            remaining = [case for case in cases["cases"] if case["id"] not in done]
            if not remaining:
                continue

            await warm_up(provider, model)
            for case in remaining:
                result = await answer_with_retries(case, stage, model, repository, provider, vector_store, wait)
                # Appended and flushed straight away, so a crash loses at most one answer
                with path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(to_record(case, stage, result), ensure_ascii=False) + "\n")
                answered += 1
                status = f"{result.error_kind} error: {result.error}" if result.error else f"{len(result.citations)} citations"
                print(f"stage {stage} | {model} | case {case['id']:2} | {result.seconds.get('total', 0):6.1f}s | {status}", flush=True)
    return answered


async def main() -> None:
    require_committed_code()
    cases = load_cases()
    output_dir = results_dir(cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    check_or_save_settings(output_dir / "settings.json", build_settings(cases))

    store = get_store()
    job = await ensure_index(cases, store)
    repository = await load_repository_index(store, job["namespace"])
    provider = OllamaProvider()
    await check_index_is_complete(job, repository, provider)

    try:
        answered = await run_all(cases, repository, provider, QueryVectorStore(provider.embedding_model), output_dir)
    except InfrastructureFailure as failure:
        sys.exit(f"Stopped because a service kept failing ({failure}). Fix it and run again to continue.")
    print(f"Done: {answered} new answers saved in {output_dir}", flush=True)


if __name__ == "__main__":
    if config.AI_PROVIDER != "ollama":
        sys.exit("The evaluation uses local Ollama models; set AI_PROVIDER=ollama")
    asyncio.run(main())
