"""Run the external 131-bug scorer for baseline and candidate, then compare them.

This evaluator-only workflow binds every score to:

* one benchmark repository checkout;
* one exact scorer script fingerprint;
* one benchmark repository HEAD when Git metadata is available;
* one exact evaluation-submission fingerprint.

The hidden ground-truth registry remains inside the benchmark repository's scorer process. This
module receives only the aggregate JSON receipt printed by ``score_qualibug_output.py`` and then
uses ``scored_run_comparison`` for fail-closed identity checking and delta calculation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .scored_run_comparison import (
    ComparisonError,
    _fingerprint,
    _read_artifact,
    build_scored_run_snapshot,
    compare_scored_runs,
    render_markdown,
)

SCORER_RECEIPT_SCHEMA = "qualibug.external-target-scorer-receipt.v1"
WORKFLOW_RECEIPT_SCHEMA = "qualibug.external-scored-comparison-workflow.v1"


class ScorerExecutionError(RuntimeError):
    """The external scorer could not produce a trustworthy aggregate receipt."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _git_head(repository: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _run_scorer_process(
    *,
    benchmark_repo: Path,
    scorer_script: Path,
    submission_path: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(scorer_script), str(submission_path)],
        cwd=str(benchmark_repo),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise ScorerExecutionError(
            "external_scorer_failed:"
            f"returncode={completed.returncode}:"
            f"stderr={completed.stderr.strip()[:500]}"
        )
    stdout = completed.stdout.strip()
    if not stdout:
        raise ScorerExecutionError("external_scorer_stdout_empty")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ScorerExecutionError(
            "external_scorer_stdout_not_json:"
            + stdout[:500]
        ) from exc
    if not isinstance(payload, dict):
        raise ScorerExecutionError("external_scorer_receipt_not_object")
    return payload


def build_external_score_receipt(
    *,
    benchmark_repo: Path,
    scorer_script: Path,
    benchmark_head: str,
    submission_path: Path,
    submission: Any,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Execute one score and bind the aggregate output to immutable input identities."""
    score = _run_scorer_process(
        benchmark_repo=benchmark_repo,
        scorer_script=scorer_script,
        submission_path=submission_path,
        timeout_seconds=timeout_seconds,
    )
    receipt = {
        "schema_version": SCORER_RECEIPT_SCHEMA,
        "scorer_script_relative_path": str(
            scorer_script.relative_to(benchmark_repo)
        ).replace("\\", "/"),
        "scorer_script_fingerprint": _sha256_file(scorer_script),
        "benchmark_repository_head": benchmark_head,
        "evaluation_submission_path": str(submission_path),
        "evaluation_submission_fingerprint": _fingerprint(submission),
        "aggregate_score": score,
        "hidden_answer_key_exported": False,
        "raw_ground_truth_rows_exported": False,
        "metric_authority": "external_target_scorer",
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    return receipt


def run_external_scored_comparison(
    *,
    benchmark_repo: Path | str,
    baseline_result_path: Path | str,
    baseline_submission_path: Path | str,
    candidate_result_path: Path | str,
    candidate_submission_path: Path | str,
    output_dir: Path | str,
    scorer_relative_path: str = "scripts/score_qualibug_output.py",
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Score both submissions with one immutable scorer checkout and compare them."""
    repo = Path(benchmark_repo).resolve()
    scorer = (repo / scorer_relative_path).resolve()
    if not repo.is_dir():
        raise ScorerExecutionError(f"benchmark_repository_missing:{repo}")
    if not scorer.is_file() or not _within(scorer, repo):
        raise ScorerExecutionError("scorer_script_missing_or_outside_benchmark_repo")

    baseline_result_file = Path(baseline_result_path).resolve()
    baseline_submission_file = Path(baseline_submission_path).resolve()
    candidate_result_file = Path(candidate_result_path).resolve()
    candidate_submission_file = Path(candidate_submission_path).resolve()
    for label, path in (
        ("baseline_result", baseline_result_file),
        ("baseline_submission", baseline_submission_file),
        ("candidate_result", candidate_result_file),
        ("candidate_submission", candidate_submission_file),
    ):
        if not path.is_file():
            raise ScorerExecutionError(f"{label}_missing:{path}")

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline_result = _read_artifact(baseline_result_file)
    baseline_submission = _read_artifact(baseline_submission_file)
    candidate_result = _read_artifact(candidate_result_file)
    candidate_submission = _read_artifact(candidate_submission_file)

    benchmark_head = _git_head(repo)
    scorer_fingerprint_before = _sha256_file(scorer)
    baseline_score = build_external_score_receipt(
        benchmark_repo=repo,
        scorer_script=scorer,
        benchmark_head=benchmark_head,
        submission_path=baseline_submission_file,
        submission=baseline_submission,
        timeout_seconds=timeout_seconds,
    )
    candidate_score = build_external_score_receipt(
        benchmark_repo=repo,
        scorer_script=scorer,
        benchmark_head=benchmark_head,
        submission_path=candidate_submission_file,
        submission=candidate_submission,
        timeout_seconds=timeout_seconds,
    )
    scorer_fingerprint_after = _sha256_file(scorer)
    if scorer_fingerprint_before != scorer_fingerprint_after:
        raise ScorerExecutionError("scorer_script_changed_during_comparison")
    if _text(baseline_score.get("scorer_script_fingerprint")) != _text(
        candidate_score.get("scorer_script_fingerprint")
    ):
        raise ScorerExecutionError("baseline_candidate_scorer_fingerprint_mismatch")
    if _text(baseline_score.get("benchmark_repository_head")) != _text(
        candidate_score.get("benchmark_repository_head")
    ):
        raise ScorerExecutionError("benchmark_repository_head_changed_during_comparison")

    baseline_snapshot = build_scored_run_snapshot(
        label=baseline_label,
        product_result=baseline_result,
        evaluation_submission=baseline_submission,
        external_score=baseline_score,
    )
    candidate_snapshot = build_scored_run_snapshot(
        label=candidate_label,
        product_result=candidate_result,
        evaluation_submission=candidate_submission,
        external_score=candidate_score,
    )
    comparison = compare_scored_runs(baseline_snapshot, candidate_snapshot)

    artifacts = {
        "baseline_score": output / "baseline.external_score.json",
        "candidate_score": output / "candidate.external_score.json",
        "baseline_snapshot": output / "baseline.scored_snapshot.json",
        "candidate_snapshot": output / "candidate.scored_snapshot.json",
        "comparison_json": output / "comparison.json",
        "comparison_markdown": output / "comparison.md",
        "workflow_receipt": output / "workflow_receipt.json",
    }
    for key, value in (
        ("baseline_score", baseline_score),
        ("candidate_score", candidate_score),
        ("baseline_snapshot", baseline_snapshot),
        ("candidate_snapshot", candidate_snapshot),
        ("comparison_json", comparison),
    ):
        artifacts[key].write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    artifacts["comparison_markdown"].write_text(
        render_markdown(comparison),
        encoding="utf-8",
    )
    workflow_receipt = {
        "schema_version": WORKFLOW_RECEIPT_SCHEMA,
        "status": _text(comparison.get("status")),
        "reason_code": _text(comparison.get("reason_code")),
        "benchmark_repository": str(repo),
        "benchmark_repository_head": benchmark_head,
        "scorer_script_relative_path": scorer_relative_path,
        "scorer_script_fingerprint": scorer_fingerprint_after,
        "baseline_submission_fingerprint": _fingerprint(baseline_submission),
        "candidate_submission_fingerprint": _fingerprint(candidate_submission),
        "comparison_fingerprint": _text(comparison.get("comparison_fingerprint")),
        "hidden_answer_key_exported": False,
        "artifact_paths": {
            key: str(path) for key, path in artifacts.items() if key != "workflow_receipt"
        },
    }
    workflow_receipt["receipt_fingerprint"] = _fingerprint(workflow_receipt)
    artifacts["workflow_receipt"].write_text(
        json.dumps(workflow_receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "comparison": comparison,
        "workflow_receipt": workflow_receipt,
        "artifact_paths": {key: str(path) for key, path in artifacts.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score and compare two QualiBug funnel runs with one external benchmark checkout.",
    )
    parser.add_argument("--benchmark-repo", required=True)
    parser.add_argument("--scorer-relative-path", default="scripts/score_qualibug_output.py")
    parser.add_argument("--baseline-result", required=True)
    parser.add_argument("--baseline-submission", required=True)
    parser.add_argument("--candidate-result", required=True)
    parser.add_argument("--candidate-submission", required=True)
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--candidate-label", default="candidate")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        result = run_external_scored_comparison(
            benchmark_repo=args.benchmark_repo,
            scorer_relative_path=args.scorer_relative_path,
            baseline_result_path=args.baseline_result,
            baseline_submission_path=args.baseline_submission,
            candidate_result_path=args.candidate_result,
            candidate_submission_path=args.candidate_submission,
            baseline_label=args.baseline_label,
            candidate_label=args.candidate_label,
            output_dir=args.output_dir,
            timeout_seconds=max(1, int(args.timeout_seconds)),
        )
    except (ComparisonError, ScorerExecutionError, OSError) as exc:
        print(json.dumps({
            "schema_version": WORKFLOW_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "reason_code": type(exc).__name__,
            "detail": str(exc)[:1000],
        }, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if _text(result["comparison"].get("status")) == "COMPARABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
