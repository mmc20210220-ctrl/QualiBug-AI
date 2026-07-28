from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark_evaluator import run_external_scored_comparison as workflow


def _submission(run_id: str) -> dict:
    return {
        "schema_version": "qualibug.discovery-evaluation-report.v1",
        "measurement_status": "NOT_MEASURED",
        "mainline_run": {
            "run_id": run_id,
            "campaign_id": "campaign-" + run_id,
            "source_snapshot_hash": "source-fingerprint",
        },
        "manifest": {
            "target_fingerprint": "target-fingerprint",
            "ground_truth_policy_fingerprint": "gt-policy-fingerprint",
            "target_id": "benchmark-131",
        },
    }


def _result(*, blocked: int, executed: int) -> dict:
    return {
        "experiment_execution": {
            "selected_count": 20,
            "executed_count": executed,
            "blocked_count": blocked,
            "cleanup_failures": 0,
        },
        "operational_receipt_summary": {
            "production_http_requests": 0,
        },
        "discovery_loss_funnel": {
            "terminal_reason_counts": {
                "BLOCKED_MISSING_OBSERVER": blocked,
            },
        },
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_one_benchmark_checkout_scores_and_compares_both_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark_repo = tmp_path / "benchmark"
    scorer = benchmark_repo / "scripts" / "score_qualibug_output.py"
    scorer.parent.mkdir(parents=True)
    scorer.write_text("print('{}')\n", encoding="utf-8")

    baseline_submission = _write(
        tmp_path / "baseline.submission.json",
        _submission("baseline-run"),
    )
    candidate_submission = _write(
        tmp_path / "candidate.submission.json",
        _submission("candidate-run"),
    )
    baseline_result = _write(
        tmp_path / "baseline.result.json",
        _result(blocked=12, executed=4),
    )
    candidate_result = _write(
        tmp_path / "candidate.result.json",
        _result(blocked=3, executed=13),
    )

    calls: list[Path] = []

    def fake_run_scorer_process(**kwargs):
        submission_path = Path(kwargs["submission_path"])
        calls.append(submission_path)
        matched = 0 if "baseline" in submission_path.name else 3
        reported = 4 if matched == 0 else 8
        return {
            "ground_truth_total": 131,
            "reported_total": reported,
            "matched_total": matched,
            "missing_total": 131 - matched,
            "coverage_rate": matched / 131,
            "match_strategy": "exact bug_id only",
            "estimated_false_positives": reported - matched,
            "target_fingerprint": "target-fingerprint",
            "ground_truth_policy_fingerprint": "gt-policy-fingerprint",
        }

    monkeypatch.setattr(workflow, "_run_scorer_process", fake_run_scorer_process)
    monkeypatch.setattr(workflow, "_git_head", lambda _repo: "benchmark-head-sha")

    output_dir = tmp_path / "comparison"
    result = workflow.run_external_scored_comparison(
        benchmark_repo=benchmark_repo,
        baseline_result_path=baseline_result,
        baseline_submission_path=baseline_submission,
        candidate_result_path=candidate_result,
        candidate_submission_path=candidate_submission,
        output_dir=output_dir,
        baseline_label="commit-old",
        candidate_label="commit-new",
    )

    assert calls == [baseline_submission.resolve(), candidate_submission.resolve()]
    comparison = result["comparison"]
    assert comparison["status"] == "COMPARABLE"
    assert comparison["quality_delta"]["true_positives"] == 3
    assert comparison["quality_delta"]["false_negatives"] == -3
    assert comparison["loss_funnel_delta"]["terminal_reason_counts"][
        "BLOCKED_MISSING_OBSERVER"
    ] == -9
    receipt = result["workflow_receipt"]
    assert receipt["benchmark_repository_head"] == "benchmark-head-sha"
    assert receipt["hidden_answer_key_exported"] is False
    assert receipt["scorer_script_fingerprint"]

    expected_files = {
        "baseline.external_score.json",
        "candidate.external_score.json",
        "baseline.scored_snapshot.json",
        "candidate.scored_snapshot.json",
        "comparison.json",
        "comparison.md",
        "workflow_receipt.json",
    }
    assert {path.name for path in output_dir.iterdir()} == expected_files
    baseline_score = json.loads(
        (output_dir / "baseline.external_score.json").read_text(encoding="utf-8")
    )
    assert baseline_score["schema_version"] == workflow.SCORER_RECEIPT_SCHEMA
    assert baseline_score["evaluation_submission_fingerprint"]
    assert baseline_score["hidden_answer_key_exported"] is False
    assert "aggregate_score" in baseline_score


def test_scorer_path_must_remain_inside_benchmark_repository(tmp_path: Path) -> None:
    benchmark_repo = tmp_path / "benchmark"
    benchmark_repo.mkdir()
    outside = tmp_path / "outside_scorer.py"
    outside.write_text("print('{}')\n", encoding="utf-8")
    submission = _write(tmp_path / "submission.json", _submission("run"))
    result = _write(tmp_path / "result.json", _result(blocked=1, executed=1))

    with pytest.raises(
        workflow.ScorerExecutionError,
        match="scorer_script_missing_or_outside_benchmark_repo",
    ):
        workflow.run_external_scored_comparison(
            benchmark_repo=benchmark_repo,
            scorer_relative_path="../outside_scorer.py",
            baseline_result_path=result,
            baseline_submission_path=submission,
            candidate_result_path=result,
            candidate_submission_path=submission,
            output_dir=tmp_path / "out",
        )


def test_scorer_mutation_between_runs_blocks_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark_repo = tmp_path / "benchmark"
    scorer = benchmark_repo / "scripts" / "score_qualibug_output.py"
    scorer.parent.mkdir(parents=True)
    scorer.write_text("print('{}')\n", encoding="utf-8")
    submission = _write(tmp_path / "submission.json", _submission("run"))
    result = _write(tmp_path / "result.json", _result(blocked=1, executed=1))

    monkeypatch.setattr(workflow, "_git_head", lambda _repo: "head")
    monkeypatch.setattr(workflow, "_run_scorer_process", lambda **_kwargs: {
        "ground_truth_total": 131,
        "reported_total": 0,
        "matched_total": 0,
        "missing_total": 131,
        "coverage_rate": 0,
        "estimated_false_positives": 0,
        "target_fingerprint": "target-fingerprint",
        "ground_truth_policy_fingerprint": "gt-policy-fingerprint",
    })
    fingerprints = iter(["before", "before", "before", "after"])
    monkeypatch.setattr(workflow, "_sha256_file", lambda _path: next(fingerprints))

    with pytest.raises(
        workflow.ScorerExecutionError,
        match="scorer_script_changed_during_comparison",
    ):
        workflow.run_external_scored_comparison(
            benchmark_repo=benchmark_repo,
            baseline_result_path=result,
            baseline_submission_path=submission,
            candidate_result_path=result,
            candidate_submission_path=submission,
            output_dir=tmp_path / "out",
        )
