from __future__ import annotations

import json

import pytest

from benchmark_evaluator.scored_run_comparison import (
    COMPARISON_SCHEMA,
    ComparisonError,
    _read_artifact,
    build_scored_run_snapshot,
    compare_scored_runs,
    normalize_external_score,
    render_markdown,
)


def _submission(*, target_fingerprint: str = "target-fp", policy_fingerprint: str = "gt-policy-fp") -> dict:
    return {
        "schema_version": "qualibug.discovery-evaluation-report.v1",
        "measurement_status": "NOT_MEASURED",
        "policy_identity": {
            "policy_id": "funnel_candidate_v1",
            "policy_version": "1",
        },
        "mainline_run": {
            "run_id": "run-1",
            "campaign_id": "campaign-1",
            "source_snapshot_hash": "source-snapshot-fp",
        },
        "manifest": {
            "target_fingerprint": target_fingerprint,
            "ground_truth_policy_fingerprint": policy_fingerprint,
            "target_id": "qualibug-enterprise-benchmark-131",
        },
    }


def _result(
    *,
    blocked_observer: int = 10,
    ui_family: int = 0,
    event_family: int = 0,
    performance_family: int = 0,
    executed: int = 5,
) -> dict:
    return {
        "experiment_execution": {
            "selected_count": 20,
            "scheduled_count": 12,
            "executed_count": executed,
            "blocked_count": 15,
            "harness_failure_count": 0,
            "cleanup_failures": 0,
        },
        "operational_receipt_summary": {
            "observed_http_request_count": 50,
            "production_http_requests": 0,
            "accepted_write_count": 3,
        },
        "discovery_loss_funnel": {
            "terminal_reason_counts": {
                "BLOCKED_MISSING_OBSERVER": blocked_observer,
                "BLOCKED_NON_REVERSIBLE_WRITE": 5,
            },
            "observer_status_counts": {
                "OBSERVED": 7,
                "INDETERMINATE": 2,
            },
        },
        "test_obligations": {
            "by_family": {
                "authorization": 5,
                "ui_state_consistency": ui_family,
                "event_delivery_consistency": event_family,
                "performance_latency": performance_family,
            },
        },
        "canonical_defect_registry": {
            "canonical_defect_count": 2,
        },
        "evidence_graphs": [{"graph_id": "g1"}, {"graph_id": "g2"}],
    }


def _score(
    *,
    reported: int,
    matched: int,
    false_positives: int | None = None,
) -> dict:
    score = {
        "ground_truth_total": 131,
        "reported_total": reported,
        "matched_total": matched,
        "missing_total": 131 - matched,
        "coverage_rate": matched / 131,
        "match_strategy": "exact bug_id only",
        "target_fingerprint": "target-fp",
        "ground_truth_policy_fingerprint": "gt-policy-fp",
    }
    score["estimated_false_positives"] = (
        reported - matched if false_positives is None else false_positives
    )
    return score


def test_external_score_is_the_only_quality_metric_authority() -> None:
    normalized = normalize_external_score(
        _score(reported=10, matched=4, false_positives=6)
    )

    assert normalized["true_positives"] == 4
    assert normalized["false_positives"] == 6
    assert normalized["false_negatives"] == 127
    assert normalized["precision"] == pytest.approx(0.4)
    assert normalized["recall"] == pytest.approx(4 / 131)
    assert normalized["metric_authority"] == "external_target_scorer"


def test_plain_key_value_scorer_output_is_safely_normalized(tmp_path) -> None:
    path = tmp_path / "score.txt"
    path.write_text(
        "\n".join([
            "ground_truth_total: 131",
            "reported_total: 4",
            "matched_total: 0",
            "missing_total: 131",
            "coverage_rate: 0",
            "match_strategy: exact bug_id only",
            "estimated_false_positives: 4",
            "target_fingerprint: target-fp",
            "ground_truth_policy_fingerprint: gt-policy-fp",
        ]),
        encoding="utf-8",
    )

    parsed = _read_artifact(path)
    normalized = normalize_external_score(parsed)

    assert normalized["true_positives"] == 0
    assert normalized["false_positives"] == 4
    assert normalized["false_negatives"] == 131
    assert normalized["match_strategy"] == "exact bug_id only"


def test_same_ground_truth_identity_produces_quality_and_funnel_deltas() -> None:
    baseline = build_scored_run_snapshot(
        label="baseline",
        product_result=_result(blocked_observer=12, executed=5),
        evaluation_submission=_submission(),
        external_score=_score(reported=4, matched=0),
    )
    candidate = build_scored_run_snapshot(
        label="candidate",
        product_result=_result(
            blocked_observer=3,
            ui_family=2,
            event_family=1,
            performance_family=1,
            executed=12,
        ),
        evaluation_submission={
            **_submission(),
            "mainline_run": {
                "run_id": "run-2",
                "campaign_id": "campaign-2",
                "source_snapshot_hash": "source-snapshot-fp",
            },
        },
        external_score=_score(reported=8, matched=3, false_positives=5),
    )

    comparison = compare_scored_runs(baseline, candidate)

    assert comparison["schema_version"] == COMPARISON_SCHEMA
    assert comparison["status"] == "COMPARABLE"
    assert comparison["quality_metric_authority"] == "external_target_scorer_only"
    assert comparison["hidden_answer_key_consumed_by_comparator"] is False
    assert comparison["quality_delta"]["true_positives"] == 3
    assert comparison["quality_delta"]["false_positives"] == 1
    assert comparison["quality_delta"]["false_negatives"] == -3
    assert comparison["loss_funnel_delta"]["terminal_reason_counts"][
        "BLOCKED_MISSING_OBSERVER"
    ] == -9
    assert comparison["loss_funnel_delta"]["obligation_family_counts"][
        "ui_state_consistency"
    ] == 2
    assert comparison["loss_funnel_delta"]["obligation_family_counts"][
        "event_delivery_consistency"
    ] == 1
    assert comparison["loss_funnel_delta"]["obligation_family_counts"][
        "performance_latency"
    ] == 1
    assert comparison["operational_delta"]["executed_count"] == 7
    assert comparison["safety_regression"] is False

    markdown = render_markdown(comparison)
    assert "Status: `COMPARABLE`" in markdown
    assert "new_true_positives" in markdown
    assert "BLOCKED_MISSING_OBSERVER" in markdown


def test_target_fingerprint_mismatch_blocks_all_quality_deltas() -> None:
    baseline = build_scored_run_snapshot(
        label="baseline",
        product_result=_result(),
        evaluation_submission=_submission(target_fingerprint="target-A"),
        external_score={
            **_score(reported=4, matched=0),
            "target_fingerprint": "target-A",
        },
    )
    candidate = build_scored_run_snapshot(
        label="candidate",
        product_result=_result(),
        evaluation_submission=_submission(target_fingerprint="target-B"),
        external_score={
            **_score(reported=6, matched=2),
            "target_fingerprint": "target-B",
        },
    )

    comparison = compare_scored_runs(baseline, candidate)

    assert comparison["status"] == "BLOCKED_IDENTITY_MISMATCH"
    assert "target_fingerprint" in comparison["identity_mismatches"]
    assert comparison["quality_delta"] is None
    assert comparison["loss_funnel_delta"] is None


def test_no_strong_ground_truth_fingerprint_blocks_comparison() -> None:
    submission = {
        "run_id": "run-1",
        "campaign_id": "campaign-1",
        "target_id": "same-readable-name",
        "measurement_status": "NOT_MEASURED",
    }
    score = {
        "ground_truth_total": 131,
        "reported_total": 4,
        "matched_total": 0,
        "missing_total": 131,
        "estimated_false_positives": 4,
    }
    baseline = build_scored_run_snapshot(
        label="baseline",
        product_result=_result(),
        evaluation_submission=submission,
        external_score=score,
    )
    candidate = build_scored_run_snapshot(
        label="candidate",
        product_result=_result(),
        evaluation_submission={**submission, "run_id": "run-2"},
        external_score=score,
    )

    comparison = compare_scored_runs(baseline, candidate)

    assert comparison["status"] == "BLOCKED_IDENTITY_UNPROVEN"
    assert comparison["reason_code"] == (
        "EXTERNAL_SCORE_GROUND_TRUTH_FINGERPRINT_MISSING"
    )
    assert comparison["quality_delta"] is None


@pytest.mark.parametrize(
    "patch, expected_error",
    [
        ({"matched_total": 132}, "score_matched_total_exceeds_ground_truth"),
        ({"missing_total": 130}, "score_missing_total_identity_mismatch"),
        ({"coverage_rate": 0.5}, "score_coverage_rate_disagrees_with_tp_fn"),
        ({"estimated_false_positives": 0}, "score_false_positive_count_below_unmatched_reports"),
    ],
)
def test_inconsistent_external_scorer_receipt_is_rejected(
    patch: dict,
    expected_error: str,
) -> None:
    score = _score(reported=4, matched=0)
    score.update(patch)

    with pytest.raises(ComparisonError, match=expected_error):
        normalize_external_score(score)


def test_score_and_submission_identity_must_agree() -> None:
    with pytest.raises(
        ComparisonError,
        match="score_submission_target_fingerprint_mismatch",
    ):
        build_scored_run_snapshot(
            label="bad",
            product_result=_result(),
            evaluation_submission=_submission(target_fingerprint="submission-target"),
            external_score={
                **_score(reported=4, matched=0),
                "target_fingerprint": "score-target",
            },
        )
