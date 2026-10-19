import json

import pytest

from evaluation import run_eval
from tests.test_answer import FakeProvider, make_repository


class AllVectors:
    def __init__(self, repository):
        self.ids = [chunk.id for chunk in repository.chunks]

    def query(self, namespace, vector, top_k):
        return [(chunk_id, 0.5) for chunk_id in self.ids[:top_k]]


CASES = {
    "repository": "psf/requests",
    "commit_sha": "a" * 40,
    "cases": [
        {"id": 1, "question": "How does strip_authorization work?", "answerable": True, "reference": "x", "target_regions": []},
        {"id": 2, "question": "What is the password?", "answerable": False, "reference": "x", "target_regions": []},
    ],
}


async def run(tmp_path, provider, **options):
    repository = make_repository()
    return await run_eval.run_all(CASES, repository, provider, AllVectors(repository), tmp_path, **options)


async def test_every_stage_model_and_case_is_saved_with_everything_needed(tmp_path):
    provider = FakeProvider(json.dumps({"answer": "It pops the header.", "citations": ["source-1"]}))

    answered = await run(tmp_path, provider, models=["model-a", "model-b"])

    assert answered == 3 * 2 * 2
    records = [json.loads(line) for line in (tmp_path / "stage-3" / "model-b.jsonl").read_text().splitlines()]
    assert [record["case_id"] for record in records] == [1, 2]
    record = records[0]
    assert (record["stage"], record["model"], record["retrieval_mode"], record["context_mode"]) == (3, "model-b", "hybrid", "implementation")
    # The exact request, what was retrieved, the excerpts and the raw answer are all kept
    assert record["messages"][0]["role"] == "system" and "Question:" in record["messages"][1]["content"]
    assert record["retrieval"]["candidates"] and record["excerpts"][0]["text"]
    assert record["raw_response"] == json.dumps({"answer": "It pops the header.", "citations": ["source-1"]})
    assert record["citations"] == ["source-1"] and record["answerable"] is True


async def test_a_restarted_run_never_asks_a_recorded_case_again(tmp_path):
    failing = FakeProvider('{"answer": "cut off')
    await run(tmp_path, failing, stages={1: run_eval.STAGES[1]}, models=["model-a"])

    working = FakeProvider(json.dumps({"answer": "x", "citations": []}))
    answered_again = await run(tmp_path, working, stages={1: run_eval.STAGES[1]}, models=["model-a"])

    assert answered_again == 0  # failures are recorded, not retried
    records = [json.loads(line) for line in (tmp_path / "stage-1" / "model-a.jsonl").read_text().splitlines()]
    assert len(records) == 2 and all("valid JSON" in record["error"] for record in records)


def test_changed_settings_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "git", lambda *arguments: "abc123")
    path = tmp_path / "settings.json"

    run_eval.check_or_save_settings(path, {"context_tokens": 8192})
    run_eval.check_or_save_settings(path, {"context_tokens": 8192})  # same settings: fine

    with pytest.raises(SystemExit, match="Settings differ"):
        run_eval.check_or_save_settings(path, {"context_tokens": 4096})


def test_result_paths_are_stable():
    assert run_eval.results_dir(CASES).name == "requests-aaaaaaa"
    assert run_eval.model_file_name("deepseek-coder-v2:16b") == "deepseek-coder-v2-16b.jsonl"


async def test_an_ollama_outage_stops_the_run_without_recording_failures(tmp_path):
    from app.ai.providers import ProviderError

    provider = FakeProvider(error=ProviderError("The answer model failed. Is Ollama running?"))

    with pytest.raises(run_eval.InfrastructureFailure):
        await run(tmp_path, provider, stages={1: run_eval.STAGES[1]}, models=["model-a"], wait=0)

    path = tmp_path / "stage-1" / "model-a.jsonl"
    assert not path.exists() or path.read_text() == ""  # nothing counted against the model


async def test_unexpected_exceptions_are_retried_then_stop_the_run(tmp_path):
    class Broken(FakeProvider):
        async def embed(self, texts):
            raise KeyError("pinecone hiccup")

    with pytest.raises(run_eval.InfrastructureFailure, match="KeyError"):
        await run(tmp_path, Broken("{}"), stages={1: run_eval.STAGES[1]}, models=["model-a"], wait=0)


def test_a_half_written_last_line_is_removed_on_restart(tmp_path):
    path = tmp_path / "answers.jsonl"
    path.write_text('{"case_id": 1}\n{"case_id": 2, "answ', encoding="utf-8")

    assert run_eval.read_done_case_ids(path) == {1}
    assert path.read_text(encoding="utf-8") == '{"case_id": 1}\n'


def test_records_include_the_reference_for_ragas_and_the_review_sheet(tmp_path):
    from app.rag.answer import AnswerResult

    record = run_eval.to_record(CASES["cases"][0], 2, AnswerResult("q", "hybrid", "chunks", "m"))

    assert record["reference"] == "x" and record["stage"] == 2 and record["error_kind"] is None
