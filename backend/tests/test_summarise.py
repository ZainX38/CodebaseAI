import csv

from evaluation import summarise


CASE = {"id": 4, "answerable": True, "reference": "ref", "target_regions": [["src/auth.py", 10, 14], ["src/b.py", 1, 2]]}
CASES = {"repository": "psf/requests", "commit_sha": "a" * 40, "cases": [CASE]}


def record(stage=1, model="m", error=None, citations=("source-1",), excerpts=(("src/auth.py", 8, 12),)):
    return {
        "stage": stage, "model": model, "case_id": 4, "answerable": True, "question": "q?",
        "retrieval_mode": "vector", "context_mode": "chunks",
        "answer": "" if error else "It encodes them.", "error": error,
        "citations": list(citations) if not error else [],
        "rejected_citations": ["source-9"], "normalised_citations": [],
        "excerpts": [{"id": f"source-{i + 1}", "path": p, "start_line": s, "end_line": e, "text": "x"} for i, (p, s, e) in enumerate(excerpts)],
        "retrieval": {"candidates": [{"path": "src/auth.py", "start_line": 1, "end_line": 20}]},
        "seconds": {"total": 12.5},
    }


def test_coverage_counts_target_lines_and_complete_regions():
    # Lines 10-12 of the first region are shown, the second region not at all
    assert summarise.covered_lines(CASE, [("src/auth.py", 8, 12)]) == (3, 7, 0, 2)
    assert summarise.covered_lines(CASE, [("src/auth.py", 1, 20), ("src/b.py", 1, 2)]) == (7, 7, 2, 2)
    assert summarise.covered_lines({"target_regions": []}, [("x", 1, 2)]) == (0, 0, 0, 0)


def test_review_sheet_has_one_row_per_answer_and_empty_grades(tmp_path):
    path = tmp_path / "review-sheet.csv"
    ragas = {(1, "m", 4): {"faithfulness": 0.5, "answer_relevancy": None, "context_precision": 1.0}}

    count = summarise.build_review_sheet([record(), record(stage=2)], CASES, ragas, path)

    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert count == len(rows) == 2
    assert all(row[summarise.GRADE_COLUMN] == "" and row["notes"] == "" for row in rows)  # never filled in by code
    first = rows[0]
    assert (first["cited_lines"], first["target_lines_shown"], first["rejected_citations"]) == ("src/auth.py:8-12", "3/7", "source-9")
    assert (first["faithfulness"], first["answer_relevancy"], first["context_precision"]) == ("0.500", "", "1.000")
    assert first["reference"] == "ref"


def test_report_counts_errors_and_citations_without_judging_correctness():
    records = [record(), record(error="The model returned an empty answer")]

    report = summarise.build_report(records, CASES, {}, {"models": ["m"], "embedding_model": "e"})

    assert "**Correctness is not judged here**" in report
    assert "`ragas.jsonl` doesn't exist yet" in report
    # 2 answers, 1 error, 1 with a valid citation, 2 rejected citations
    assert "| 2 | 1 | 1 | 2 | 0 |" in report
