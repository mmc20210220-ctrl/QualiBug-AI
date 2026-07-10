from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_evaluation_contract import (
    EvaluationContractError,
    MANIFEST_SCHEMA,
    aggregate_evaluation_receipts,
    assess_commercial_dataset_shape,
    build_paired_evaluation_evidence,
    build_runtime_view,
    evaluate_completed_scan,
    load_evaluation_manifest,
    policy_metrics_from_evaluation_reports,
    persist_evaluation_receipt,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _target(tmp_path: Path, target_id: str, *, industry: str, split: str, expectation: str) -> dict:
    input_path = tmp_path / "runtime" / f"{target_id}_input.json"
    fixture_path = tmp_path / "runtime" / f"{target_id}_fixture.json"
    context_path = tmp_path / "runtime" / f"{target_id}_context.json"
    _write_json(input_path, {"target_id": target_id, "source": "real-test-input"})
    _write_json(fixture_path, {"snapshot": target_id})
    _write_json(context_path, {"context": target_id})
    evaluator: dict[str, str] = {}
    if expectation == "seeded_defects":
        truth_path = tmp_path / "private" / f"{target_id}_bugs.json"
        _write_json(
            truth_path,
            [
                {
                    "bug_id": f"BUG-{target_id}",
                    "title": f"{target_id} seeded defect",
                    "severity": "P1",
                    "type": "authorization_access_control",
                    "match_keywords": ["alpha", "beta", "gamma", "delta"],
                }
            ],
        )
        evaluator["ground_truth_ref"] = str(truth_path.relative_to(tmp_path))
    return {
        "target_id": target_id,
        "project_id": f"project-{target_id}",
        "industry": industry,
        "split": split,
        "expectation": expectation,
        "runtime": {
            "environment_ref": f"env-{target_id}",
            "environment_type": "test",
            "input_bundle_ref": str(input_path.relative_to(tmp_path)),
            "fixture_snapshot_ref": str(fixture_path.relative_to(tmp_path)),
            "context_artifact_ref": str(context_path.relative_to(tmp_path)),
        },
        "evaluator": evaluator,
    }


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "evaluation_manifest.json"
    _write_json(
        path,
        {
            "schema_version": MANIFEST_SCHEMA,
            "dataset_id": "cross-industry-real-defects",
            "dataset_version": "2026.07.10-v1",
            "targets": [
                _target(tmp_path, "held-in", industry="commerce", split="held_in", expectation="seeded_defects"),
                _target(tmp_path, "held-out-fin", industry="finance", split="held_out", expectation="seeded_defects"),
                _target(tmp_path, "held-out-health", industry="healthcare", split="held_out", expectation="seeded_defects"),
                _target(tmp_path, "held-out-saas", industry="enterprise-saas", split="held_out", expectation="seeded_defects"),
                _target(tmp_path, "clean", industry="commerce", split="held_out", expectation="clean"),
            ],
        },
    )
    return path


def _matched_finding(target_id: str) -> dict:
    return {
        **_customer_deliverable_clean_finding(),
        "title": f"alpha beta gamma delta on {target_id}",
        "severity": "P1",
        "reproduction": {
            **_customer_deliverable_clean_finding()["reproduction"],
            "path": f"/api/{target_id}",
        },
        "raw_evidence": {
            **_customer_deliverable_clean_finding()["raw_evidence"],
            "request_raw": {"method": "GET", "path": f"/api/{target_id}"},
        },
    }


def _customer_deliverable_clean_finding() -> dict:
    return {
        "title": "clean target false positive",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "evidence_level": "runtime",
        "execution_source": "live-test-target",
        "expected": "denied",
        "actual": "allowed",
        "timestamp": "2026-07-10T00:00:00Z",
        "evidence_consistency": {"verdict": "confirmed"},
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "reproduction": {
            "method": "GET",
            "path": "/api/clean",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"allowed": True}},
        },
        "raw_evidence": {
            "request_raw": {"method": "GET", "path": "/api/clean"},
            "response_raw": {"status_code": 200, "body": {"allowed": True}},
            "timestamp": "2026-07-10T00:00:00Z",
            "has_real_evidence": True,
        },
    }


def _receipt(
    manifest,
    target_id: str,
    findings: list[dict],
    *,
    policy_id: str = "policy-champion",
    evaluation_mode: str = "replay",
) -> dict:
    return evaluate_completed_scan(
        manifest,
        target_id,
        run_id=f"run-{target_id}",
        policy_id=policy_id,
        evaluation_mode=evaluation_mode,
        findings=findings,
        candidates=[],
        pipeline_health={"status": "OK"},
        operational_metrics={
            "wall_clock_seconds": 10,
            "estimated_cost_usd": 1.25,
            "request_count": 12,
            "production_http_requests": 0,
            "cleanup_failures": 0,
            "safety_incidents": 0,
            "dirty_test_environments": 0,
            "execution_success_rate": 1.0,
            "engine_success_rate": 1.0,
            "duplicate_rate": 0.0,
        },
    )


def test_runtime_view_never_exposes_evaluator_ground_truth(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    runtime_view = build_runtime_view(manifest, "held-in")

    serialized = json.dumps(runtime_view, ensure_ascii=False).lower()
    assert "ground_truth" not in serialized
    assert "expectation" not in runtime_view["target"]
    assert "split" not in runtime_view["target"]
    assert "private" not in serialized
    assert runtime_view["target"]["runtime_fingerprint"]
    assert manifest.target_fingerprints["held-in"]["ground_truth_fingerprint"]


def test_manifest_rejects_non_declared_safe_environment(tmp_path: Path) -> None:
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["targets"][0]["runtime"]["environment_type"] = "unknown"
    _write_json(path, payload)

    with pytest.raises(EvaluationContractError, match="explicitly non-production"):
        load_evaluation_manifest(path)


def test_commercial_shape_requires_three_held_out_industries_and_clean_target(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    shape = assess_commercial_dataset_shape(manifest)

    assert shape["commercial_shape_ready"] is True
    assert shape["held_out_industries"] == ["enterprise-saas", "finance", "healthcare"]
    assert shape["clean_target_count"] == 1


def test_aggregate_uses_hidden_truth_and_clean_false_positive_measurement(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipts = []
    for target in manifest.targets:
        findings = (
            [_customer_deliverable_clean_finding()]
            if target.expectation == "clean"
            else [_matched_finding(target.target_id)]
        )
        receipts.append(_receipt(manifest, target.target_id, findings))

    report = aggregate_evaluation_receipts(manifest, receipts)

    assert report["claim_status"] == "MEASURED"
    assert report["commercial_promotion_evidence_ready"] is True
    assert report["held_in"]["true_positives"] == 1
    assert report["held_in"]["micro_recall"] == 1.0
    assert report["held_out"]["true_positives"] == 3
    assert report["held_out"]["micro_precision"] == 1.0
    assert report["held_out_macro_industry_recall"] == 1.0
    assert report["clean"]["customer_deliverable_false_positives"] == 1
    assert report["clean"]["critical_high_false_positives"] == 1
    assert report["operational"]["complete"] is True
    assert report["operational"]["total_request_count"] == 60
    assert report["operational"]["total_estimated_cost_usd"] == 6.25
    assert "ground_truth_source" not in json.dumps(report, ensure_ascii=False)


def test_failed_pipeline_is_not_reported_as_zero_bug_or_zero_false_positive(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipt = evaluate_completed_scan(
        manifest,
        "held-in",
        run_id="failed-run",
        policy_id="policy-champion",
        evaluation_mode="replay",
        findings=[],
        candidates=[],
        pipeline_health={"status": "FAILED_SAFE"},
        operational_metrics={},
    )
    report = aggregate_evaluation_receipts(manifest, [receipt])

    assert receipt["measurement_status"] == "NOT_MEASURED"
    assert receipt["not_measured_reason"] == "pipeline_health_failed_safe"
    assert receipt["metrics"] == {}
    assert report["claim_status"] == "NOT_MEASURED"
    assert report["evaluation_complete"] is False
    assert "held-in" in {item["target_id"] for item in report["not_measured_targets"]}


def test_receipts_are_immutable(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipt = _receipt(manifest, "held-in", [_matched_finding("held-in")])
    output_root = tmp_path / "receipts"

    path = persist_evaluation_receipt(receipt, output_root)
    assert persist_evaluation_receipt(receipt, output_root) == path
    changed = {**receipt, "measurement_status": "NOT_MEASURED"}
    with pytest.raises(EvaluationContractError, match="immutable evaluation receipt"):
        persist_evaluation_receipt(changed, output_root)


def _policy_report(manifest, policy_id: str, evaluation_mode: str) -> dict:
    receipts = []
    for target in manifest.targets:
        findings = [] if target.expectation == "clean" else [_matched_finding(target.target_id)]
        receipts.append(
            _receipt(
                manifest,
                target.target_id,
                findings,
                policy_id=policy_id,
                evaluation_mode=evaluation_mode,
            )
        )
    return aggregate_evaluation_receipts(manifest, receipts)


def test_paired_evidence_requires_four_real_identical_evaluations(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    champion_replay = _policy_report(manifest, "champion", "replay")
    challenger_replay = _policy_report(manifest, "challenger", "replay")
    champion_shadow = _policy_report(manifest, "champion", "shadow")
    challenger_shadow = _policy_report(manifest, "challenger", "shadow")

    evidence = build_paired_evaluation_evidence(
        manifest,
        champion_replay=champion_replay,
        challenger_replay=challenger_replay,
        champion_shadow=champion_shadow,
        challenger_shadow=challenger_shadow,
    )
    metrics = policy_metrics_from_evaluation_reports(challenger_replay, challenger_shadow)

    assert evidence["replay_executed"] is True
    assert evidence["shadow_executed"] is True
    assert evidence["paired_target_count"] == 5
    assert len(evidence["replay_run_ids"]) == 10
    assert len(evidence["target_receipt_fingerprints"]) == 5
    assert metrics["evaluation_complete"] is True
    assert metrics["commercial_shape_ready"] is True
    assert metrics["operational_metrics_complete"] is True
    assert metrics["sample_count"] == 10
    assert metrics["held_out_recall"] == 1.0
    assert metrics["unique_industry_count"] == 3
    assert metrics["production_http_requests"] == 0


def test_paired_evidence_rejects_target_fingerprint_drift(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    champion_replay = _policy_report(manifest, "champion", "replay")
    challenger_replay = _policy_report(manifest, "challenger", "replay")
    champion_shadow = _policy_report(manifest, "champion", "shadow")
    challenger_shadow = _policy_report(manifest, "challenger", "shadow")
    challenger_shadow["target_receipts"][0]["input_fingerprint"] = "drifted"

    with pytest.raises(EvaluationContractError, match="target fingerprints differ"):
        build_paired_evaluation_evidence(
            manifest,
            champion_replay=champion_replay,
            challenger_replay=challenger_replay,
            champion_shadow=champion_shadow,
            challenger_shadow=challenger_shadow,
        )
