"""Scores the saved evaluation answers with RAGAS.

    cd backend/evaluation/ragas
    uv run python ragas_score.py

Metrics (each 0-1):
- faithfulness: are the answer's claims supported by the excerpts the model was given?
- answer_relevancy: does the answer address the question?
- context_precision: are the excerpts that matter for the reference answer ranked high?

The judge is qwen3.5:9b on local Ollama, which is not one of the two models being
evaluated. It is a small judge, so the scores are noisy and only a rough signal next
to the manual grades in review-sheet.csv.

Answers that failed in the evaluation are written with empty scores, so every one of
the 108 answers appears in ragas.jsonl. The script can be stopped and started again:
answers that already have a row are skipped.
"""

import asyncio
from datetime import datetime, UTC
import json
from pathlib import Path
import time
from typing import Any
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import httpx
from openai import AsyncOpenAI
import ragas
from ragas.embeddings import embedding_factory
from ragas.llms import llm_factory
from ragas.metrics.collections import AnswerRelevancy, ContextPrecisionWithReference, Faithfulness


OLLAMA_URL = "http://localhost:11434"
JUDGE_MODEL = "qwen3.5:9b"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
METRIC_TIMEOUT_SECONDS = 1800

EVALUATION_DIR = Path(__file__).resolve().parents[1]
CASES = json.loads((EVALUATION_DIR / "cases.json").read_text(encoding="utf-8"))
RESULTS_DIR = EVALUATION_DIR / "results" / f"{CASES['repository'].split('/')[1]}-{CASES['commit_sha'][:7]}"
OUTPUT_PATH = RESULTS_DIR / "ragas.jsonl"


def judge_settings() -> dict[str, Any]:
    tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=30).json()["models"]
    digests = {item["name"]: item["digest"] for item in tags}
    return {
        "ragas_version": ragas.__version__,
        "judge_model": JUDGE_MODEL,
        "judge_digest": digests.get(JUDGE_MODEL),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_digest": digests.get(EMBEDDING_MODEL),
        "temperature": 0,
        # qwen3.5 "thinks" by default through Ollama's OpenAI API, used up the whole output
        # budget on hidden reasoning and returned empty answers, so thinking is turned off
        "reasoning_effort": "none",
        "metric_timeout_seconds": METRIC_TIMEOUT_SECONDS,
    }


def check_or_save_settings(settings: dict[str, Any]) -> None:
    path = RESULTS_DIR / "ragas-settings.json"
    if path.exists():
        saved = {key: value for key, value in json.loads(path.read_text(encoding="utf-8")).items() if key != "created_at"}
        if saved != settings:
            raise SystemExit("RAGAS settings differ from ragas-settings.json; refusing to mix scores")
        return
    path.write_text(json.dumps({**settings, "created_at": datetime.now(UTC).isoformat()}, indent=2), encoding="utf-8")


def load_records() -> list[dict[str, Any]]:
    records = []
    for path in sorted(RESULTS_DIR.glob("stage-*/*.jsonl")):
        records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(records, key=lambda record: (record["stage"], record["model"], record["case_id"]))


def scored_keys() -> set[tuple[int, str, int]]:
    if not OUTPUT_PATH.exists():
        return set()
    keys = set()
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            keys.add((row["stage"], row["model"], row["case_id"]))
        except (json.JSONDecodeError, KeyError):
            pass  # a half-written last line is scored again
    return keys


async def score_one(metric, **arguments) -> tuple[float | None, str | None]:
    try:
        result = await asyncio.wait_for(metric.ascore(**arguments), METRIC_TIMEOUT_SECONDS)
        return float(result.value), None
    except Exception as error:
        return None, f"{type(error).__name__}: {str(error)[:300]}"


async def main() -> None:
    check_or_save_settings(judge_settings())
    references = {case["id"]: case["reference"] for case in CASES["cases"]}

    client = AsyncOpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama", max_retries=0, timeout=METRIC_TIMEOUT_SECONDS)
    llm = llm_factory(JUDGE_MODEL, client=client, temperature=0, max_tokens=2048, reasoning_effort="none")
    embeddings = embedding_factory("openai", model=EMBEDDING_MODEL, client=client)
    metrics = {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": ContextPrecisionWithReference(llm=llm),
    }

    done = scored_keys()
    records = [record for record in load_records() if (record["stage"], record["model"], record["case_id"]) not in done]
    print(f"{len(records)} answers to score", flush=True)

    for record in records:
        row = {"stage": record["stage"], "model": record["model"], "case_id": record["case_id"], "errors": {}}
        started = time.perf_counter()

        if record["error"]:
            # Nothing to score: the evaluation itself failed for this answer
            row.update({name: None for name in metrics})
            row["errors"]["answer"] = record["error"]
        else:
            question, answer = record["question"], record["answer"]
            contexts = [excerpt["text"] for excerpt in record["excerpts"]] or [""]
            arguments = {
                "faithfulness": {"user_input": question, "response": answer, "retrieved_contexts": contexts},
                "answer_relevancy": {"user_input": question, "response": answer},
                "context_precision": {"user_input": question, "reference": references[record["case_id"]], "retrieved_contexts": contexts},
            }
            for name, metric in metrics.items():
                row[name], error = await score_one(metric, **arguments[name])
                if error:
                    row["errors"][name] = error

        row["seconds"] = round(time.perf_counter() - started, 1)
        with OUTPUT_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row) + "\n")
        print(
            f"stage {row['stage']} | {row['model']} | case {row['case_id']:2} | {row['seconds']:6.0f}s | "
            + " ".join(f"{name}={row[name]:.2f}" if row[name] is not None else f"{name}=-" for name in metrics),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
