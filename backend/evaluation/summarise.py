"""Builds review-sheet.csv and report.md from the saved evaluation answers.

    cd backend
    uv run python -m evaluation.summarise

Nothing here judges whether an answer is correct. The review sheet has empty grade
and notes columns for a person to fill in. The report only counts things that can be
measured mechanically (errors, citations, which target lines the model was shown,
timings) plus the RAGAS scores if ragas.jsonl exists.
"""

import csv
import json
from pathlib import Path
from statistics import mean, median
from typing import Any

from evaluation.run_eval import MODELS, STAGES, load_cases, model_file_name, results_dir


GRADE_COLUMN = "grade (correct / partial / wrong)"
RAGAS_METRICS = ["faithfulness", "answer_relevancy", "context_precision"]


def load_records(output_dir: Path) -> list[dict[str, Any]]:
    records = []
    for stage in STAGES:
        for model in MODELS:
            path = output_dir / f"stage-{stage}" / model_file_name(model)
            if path.exists():
                records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return records


def load_ragas(output_dir: Path) -> dict[tuple[int, str, int], dict[str, Any]]:
    path = output_dir / "ragas.jsonl"
    if not path.exists():
        return {}
    scores = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            scores[(row["stage"], row["model"], row["case_id"])] = row
    return scores


# ---------------------- Evidence coverage ------------------------------------
# Which of the case's target lines were inside what the model was shown. This says
# whether the evidence was visible, not whether the answer used it correctly
def covered_lines(case: dict[str, Any], ranges: list[tuple[str, int, int]]) -> tuple[int, int, int, int]:
    target_lines = {(path, line) for path, start, end in case["target_regions"] for line in range(start, end + 1)}
    shown = {(path, line) for path, start, end in ranges for line in range(start, end + 1)}
    complete_regions = sum(
        all((path, line) in shown for line in range(start, end + 1)) for path, start, end in case["target_regions"]
    )
    return len(target_lines & shown), len(target_lines), complete_regions, len(case["target_regions"])


def excerpt_ranges(record: dict[str, Any]) -> list[tuple[str, int, int]]:
    return [(excerpt["path"], excerpt["start_line"], excerpt["end_line"]) for excerpt in record["excerpts"]]


def retrieved_ranges(record: dict[str, Any]) -> list[tuple[str, int, int]]:
    return [(candidate["path"], candidate["start_line"], candidate["end_line"]) for candidate in record["retrieval"].get("candidates", [])]


def citation_labels(record: dict[str, Any]) -> list[str]:
    by_id = {excerpt["id"]: excerpt for excerpt in record["excerpts"]}
    return [f"{by_id[i]['path']}:{by_id[i]['start_line']}-{by_id[i]['end_line']}" for i in record["citations"] if i in by_id]


# ---------------------- Review sheet -----------------------------------------
def build_review_sheet(records, cases, ragas, path: Path) -> int:
    cases_by_id = {case["id"]: case for case in cases["cases"]}
    columns = [
        "stage", "retrieval_mode", "context_mode", "model", "case_id", "answerable", "question", "reference",
        "answer", "error", "cited_lines", "rejected_citations", "normalised_citations",
        "target_lines_shown", *RAGAS_METRICS, "seconds", GRADE_COLUMN, "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:  # utf-8-sig so Excel reads accents correctly
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for record in sorted(records, key=lambda item: (item["stage"], item["model"], item["case_id"])):
            case = cases_by_id[record["case_id"]]
            shown, total, _, _ = covered_lines(case, excerpt_ranges(record))
            scores = ragas.get((record["stage"], record["model"], record["case_id"]), {})
            writer.writerow({
                "stage": record["stage"],
                "retrieval_mode": record["retrieval_mode"],
                "context_mode": record["context_mode"],
                "model": record["model"],
                "case_id": record["case_id"],
                "answerable": record["answerable"],
                "question": record["question"],
                "reference": case["reference"],
                "answer": record["answer"],
                "error": record["error"] or "",
                "cited_lines": "; ".join(citation_labels(record)),
                "rejected_citations": "; ".join(str(item) for item in record["rejected_citations"]),
                "normalised_citations": "; ".join(record["normalised_citations"]),
                "target_lines_shown": f"{shown}/{total}" if total else "",
                **{metric: format_score(scores.get(metric)) for metric in RAGAS_METRICS},
                "seconds": record["seconds"].get("total", ""),
                GRADE_COLUMN: "",  # left empty on purpose: graded by hand
                "notes": "",
            })
    return len(records)


def format_score(value: Any) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) else ""


# ---------------------- Report -----------------------------------------------
def summarise_group(records, cases_by_id, ragas) -> dict[str, Any]:
    answered = [record for record in records if not record["error"]]
    unanswerable = [record for record in records if not record["answerable"]]
    coverage = [covered_lines(cases_by_id[r["case_id"]], excerpt_ranges(r)) for r in records if r["answerable"]]
    retrieved = [covered_lines(cases_by_id[r["case_id"]], retrieved_ranges(r)) for r in records if r["answerable"]]
    seconds = [record["seconds"]["total"] for record in records if "total" in record["seconds"]]
    scores = {
        metric: [row[metric] for record in records
                 if isinstance((row := ragas.get((record["stage"], record["model"], record["case_id"]), {})).get(metric), (int, float))]
        for metric in RAGAS_METRICS
    }
    return {
        "answers": len(records),
        "errors": len(records) - len(answered),
        "with_valid_citation": sum(1 for record in answered if record["citations"]),
        "rejected": sum(len(record["rejected_citations"]) for record in records),
        "normalised": sum(len(record["normalised_citations"]) for record in records),
        "unanswerable_without_citations": sum(1 for record in unanswerable if not record["error"] and not record["citations"]),
        "unanswerable": len(unanswerable),
        "lines_in_prompt": (sum(item[0] for item in coverage), sum(item[1] for item in coverage)),
        "regions_in_prompt": (sum(item[2] for item in coverage), sum(item[3] for item in coverage)),
        "lines_retrieved": (sum(item[0] for item in retrieved), sum(item[1] for item in retrieved)),
        "mean_seconds": mean(seconds) if seconds else None,
        "median_seconds": median(seconds) if seconds else None,
        "ragas": {metric: (mean(values) if values else None, len(values)) for metric, values in scores.items()},
    }


def build_report(records, cases, ragas, settings: dict[str, Any]) -> str:
    cases_by_id = {case["id"]: case for case in cases["cases"]}
    lines = [
        "# Evaluation report",
        "",
        f"Repository `{cases['repository']}` at commit `{cases['commit_sha']}`. "
        f"{len(records)} answers recorded ({len(STAGES)} stages x {len(MODELS)} models x {len(cases['cases'])} questions = "
        f"{len(STAGES) * len(MODELS) * len(cases['cases'])} expected).",
        "",
        "Every number below is counted from the saved answers. **Correctness is not judged here**: "
        "it is graded by hand in `review-sheet.csv`.",
        "",
        "## Answers and citations",
        "",
        "| Stage | Model | Answers | Errors | With a valid citation | Rejected citations | Normalised citations | Unanswerable with no citations | Mean s | Median s |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    groups = {}
    # The models actually in the results, in the run's order first
    present = {record["model"] for record in records}
    models = [model for model in MODELS if model in present] + sorted(present - set(MODELS))
    for stage, modes in STAGES.items():
        for model in models:
            group = [record for record in records if record["stage"] == stage and record["model"] == model]
            if not group:
                continue
            summary = groups[(stage, model)] = summarise_group(group, cases_by_id, ragas)
            label = f"{stage}: {modes['retrieval_mode']} + {modes['context_mode']}"
            lines.append(
                f"| {label} | {model} | {summary['answers']} | {summary['errors']} | {summary['with_valid_citation']} | "
                f"{summary['rejected']} | {summary['normalised']} | {summary['unanswerable_without_citations']}/{summary['unanswerable']} | "
                f"{summary['mean_seconds']:.1f} | {summary['median_seconds']:.1f} |"
            )

    lines += [
        "",
        "## Evidence shown to the model",
        "",
        "Target regions are the source lines each answerable question depends on (fixed in `cases.json` before the run). "
        "\"Retrieved\" counts the top 6 retrieved chunks; \"in prompt\" counts the final excerpts after context assembly. "
        "Seeing the lines doesn't mean the answer used them correctly.",
        "",
        "| Stage | Model | Target lines retrieved | Target lines in prompt | Complete target regions in prompt |",
        "|---|---|---:|---:|---:|",
    ]
    for (stage, model), summary in groups.items():
        retrieved, in_prompt, regions = summary["lines_retrieved"], summary["lines_in_prompt"], summary["regions_in_prompt"]
        lines.append(f"| {stage} | {model} | {retrieved[0]}/{retrieved[1]} | {in_prompt[0]}/{in_prompt[1]} | {regions[0]}/{regions[1]} |")

    lines += ["", "## RAGAS", ""]
    if not ragas:
        lines.append("`ragas.jsonl` doesn't exist yet, so there are no RAGAS scores.")
    else:
        lines += [
            "Mean scores (0-1) with the number of answers that got a score. Errors and answers the judge couldn't score are left out.",
            "",
            "| Stage | Model | " + " | ".join(RAGAS_METRICS) + " |",
            "|---|---|" + "---:|" * len(RAGAS_METRICS),
        ]
        for (stage, model), summary in groups.items():
            cells = [f"{value:.3f} (n={count})" if value is not None else f"- (n={count})" for value, count in summary["ragas"].values()]
            lines.append(f"| {stage} | {model} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Settings",
        "",
        f"- Models: {', '.join(f'`{model}`' for model in settings.get('models', MODELS))}; embeddings `{settings.get('embedding_model')}`",
        f"- Context {settings.get('context_tokens')} tokens, output limit {settings.get('max_output_tokens')}, temperature 0",
        f"- Code: git `{settings.get('git_commit')}`, code hash `{str(settings.get('code_sha256'))[:12]}`, Ollama {settings.get('ollama', {}).get('version')}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    cases = load_cases()
    output_dir = results_dir(cases)
    records = load_records(output_dir)
    ragas = load_ragas(output_dir)
    settings = json.loads((output_dir / "settings.json").read_text(encoding="utf-8"))

    count = build_review_sheet(records, cases, ragas, output_dir / "review-sheet.csv")
    (output_dir / "report.md").write_text(build_report(records, cases, ragas, settings), encoding="utf-8")
    print(f"review-sheet.csv: {count} rows; report.md written in {output_dir}")


if __name__ == "__main__":
    main()
