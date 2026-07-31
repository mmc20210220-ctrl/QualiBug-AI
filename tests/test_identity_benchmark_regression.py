from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark_regression import (
    REGRESSION_GATE_SCHEMA,
    build_identity_benchmark_snapshot,
    build_identity_error_queue,
    combine_identity_quality_gates,
    evaluate_identity_benchmark_regression,
)


def _metrics(**overrides):
    values = {
        "pairwise_precision": 0.98,
        "pairwise_recall": 0.96,
        "pairwise_f1": 0.97,
        "exact_cluster_match_rate": 0.9,
        "overmerge_rate": 0.02,
        "undermerge_rate": 0.04,
        "identity_error_unknown_coverage_rate": 0.95,
        "silent_identity_error_count": 0,
    }
    values.update(overrides)
    return values


def _asset(*, manifest="manifest:1", truth="truth:1", baseline_metrics=None):
    baseline = {
        "schema": "qualibug.enterprise-identity-benchmark-snapshot.v1",
        "snapshot_id": "snapshot:baseline",
        "measurement_status": "MEASURED",
        "manifest_id": manifest,
        "ground_truth_fingerprint": truth,
        "metrics": baseline_metrics or _metrics(),
        "errors": [],
    }
    return {
        "enterprise_identity_annotation_manifest": {"manifest_id": manifest},
        "enterprise_identity_benchmark_repository_receipt": {
            "ground_truth_fingerprint": truth,
            "quality_policy_fingerprint": "policy:1",
        },
        "enterprise_identity_benchmark_history": {
            "schema": "qualibug.enterprise-identity-benchmark-history.v1",
            "snapshots": [baseline],
        },
    }


def test_regression_blocks_only_against_same_manifest_and_truth() -> None:
    asset = _asset(baseline_metrics=_metrics(pairwise_precision=0.99))
    benchmark = {
        "status": "MEASURED",
        "metrics": _metrics(pairwise_precision=0.90),
    }
    policy = {
        "enforce_regression": True,
        "regression_thresholds": {"maximum_pairwise_precision_drop": 0.02},
    }

    gate = evaluate_identity_benchmark_regression(asset, benchmark, policy)

    assert gate["schema"] == REGRESSION_GATE_SCHEMA
    assert gate["status"] == "BLOCKED_IDENTITY_REGRESSION"
    assert gate["entry_allowed"] is False
    assert gate["baseline_snapshot_id"] == "snapshot:baseline"


def test_unknown_regression_threshold_is_rejected_not_ignored() -> None:
    gate = evaluate_identity_benchmark_regression(
        _asset(),
        {"status": "MEASURED", "metrics": _metrics()},
        {
            "enforce_regression": True,
            "regression_thresholds": {
                "maximum_pairwise_precision_drop": 0.01,
                "maximum_pairwise_precison_drop": 0.01,
            },
        },
    )

    assert gate["status"] == "INVALID_IDENTITY_REGRESSION_POLICY"
    assert gate["entry_allowed"] is False
    assert gate["invalid_thresholds"] == ["maximum_pairwise_precison_drop"]


def test_changed_annotation_universe_is_not_called_regression() -> None:
    asset = _asset(manifest="manifest:new")
    asset["enterprise_identity_benchmark_history"]["snapshots"][0][
        "manifest_id"
    ] = "manifest:old"

    gate = evaluate_identity_benchmark_regression(
        asset,
        {"status": "MEASURED", "metrics": _metrics(pairwise_precision=0.1)},
        {
            "enforce_regression": True,
            "regression_thresholds": {"maximum_pairwise_precision_drop": 0.0},
        },
    )

    assert gate["status"] == "NOT_COMPARABLE"
    assert gate["entry_allowed"] is True
    assert gate["annotation_change_is_not_regression"] is True


def test_changed_ground_truth_is_not_called_regression() -> None:
    asset = _asset(truth="truth:new")
    asset["enterprise_identity_benchmark_history"]["snapshots"][0][
        "ground_truth_fingerprint"
    ] = "truth:old"

    gate = evaluate_identity_benchmark_regression(
        asset,
        {"status": "MEASURED", "metrics": _metrics(pairwise_recall=0.1)},
        {
            "enforce_regression": True,
            "regression_thresholds": {"maximum_pairwise_recall_drop": 0.0},
        },
    )

    assert gate["status"] == "NOT_COMPARABLE"
    assert gate["entry_allowed"] is True


def test_static_and_regression_gates_share_one_final_authority() -> None:
    combined = combine_identity_quality_gates(
        {
            "schema": "qualibug.enterprise-identity-quality-gate.v1",
            "status": "PASS",
            "entry_allowed": True,
            "enforced": True,
            "blocking_reasons": [],
        },
        {
            "schema": REGRESSION_GATE_SCHEMA,
            "status": "BLOCKED_IDENTITY_REGRESSION",
            "entry_allowed": False,
            "enforced": True,
            "blocking_reasons": ["pairwise_recall regression"],
        },
    )

    assert combined["status"] == "BLOCKED_IDENTITY_REGRESSION"
    assert combined["entry_allowed"] is False
    assert combined["combined_static_and_regression_authority"] is True


def _error(error_type: str, left: str, right: str):
    return {
        "error_type": error_type,
        "left": {"mention_ref": left, "label": left, "source_id": "prd"},
        "right": {"mention_ref": right, "label": right, "source_id": "api"},
        "uncertainty_surfaced": False,
    }


def test_error_queue_tracks_new_persisting_and_resolved_exact_pairs() -> None:
    persisting = _error("OVERMERGE_FALSE_POSITIVE_PAIR", "m1", "m2")
    resolved = _error("UNDERMERGE_FALSE_NEGATIVE_PAIR", "m3", "m4")
    initial = build_identity_error_queue(
        {
            "status": "MEASURED",
            "false_positive_pairs": [persisting],
            "false_negative_pairs": [resolved],
        }
    )
    baseline = {"errors": initial["active_errors"]}
    new_error = _error("UNDERMERGE_FALSE_NEGATIVE_PAIR", "m5", "m6")

    queue = build_identity_error_queue(
        {
            "status": "MEASURED",
            "false_positive_pairs": [persisting],
            "false_negative_pairs": [new_error],
        },
        baseline,
    )

    assert queue["persisting_error_count"] == 1
    assert queue["new_error_count"] == 1
    assert queue["resolved_error_count"] == 1
    assert queue["fuzzy_or_llm_root_cause_inference_used"] is False


def test_snapshot_freezes_external_truth_and_exact_errors() -> None:
    asset = _asset()
    asset["enterprise_identity_benchmark"] = {
        "benchmark_id": "benchmark:1",
        "status": "MEASURED",
        "metrics": _metrics(),
        "quality_gate": {"status": "PASS"},
        "regression": {"status": "PASS"},
        "false_positive_pairs": [
            _error("OVERMERGE_FALSE_POSITIVE_PAIR", "m1", "m2")
        ],
        "false_negative_pairs": [],
    }

    snapshot = build_identity_benchmark_snapshot(
        asset,
        trigger="MANUAL_REMEASURE",
        actor={"name": "qa", "role": "qa_lead"},
        recorded_at_utc="2026-07-31T13:00:00Z",
    )

    assert snapshot["measurement_status"] == "MEASURED"
    assert snapshot["manifest_id"] == "manifest:1"
    assert snapshot["ground_truth_fingerprint"] == "truth:1"
    assert snapshot["error_count"] == 1
    assert snapshot["external_ground_truth_only"] is True
