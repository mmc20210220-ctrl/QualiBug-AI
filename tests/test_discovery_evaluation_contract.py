from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from ai_test_asset_center.canonical_defect_registry import (
    build_defect_identity_consistency,
)
from ai_test_asset_center.discovery_evaluation_contract import (
    EvaluationContractError,
    MANIFEST_SCHEMA,
    aggregate_evaluation_receipts,
    assess_commercial_dataset_shape,
    assess_discovery_goal_status,
    build_paired_evaluation_evidence,
    build_runtime_view,
    evaluate_completed_scan,
    load_evaluation_manifest,
    policy_metrics_from_evaluation_reports,
    persist_evaluation_receipt,
)
from ai_test_asset_center.discovery_trace_ledger import build_discovery_trace_ledger_v2
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.obligation_attempt_ledger import build_obligation_attempt_ledger
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
    build_test_execution_authority,
)


TEST_EVALUATOR_HMAC_KEY = "evaluation-contract-test-key-0123456789abcdef"


def _windows_extended_path(path: Path) -> str:
    resolved = str(path.resolve())
    if not resolved.startswith("\\\\?\\"):
        return "\\\\?\\" + resolved
    return resolved


@pytest.fixture(autouse=True)
def _evaluator_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY",
        TEST_EVALUATOR_HMAC_KEY,
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
                    "match_keywords": [
                        "seeded_defect_id", "alpha", "beta", "gamma", "delta"
                    ],
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
        "title": f"seeded_defect_id alpha beta gamma delta on {target_id}",
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
        "candidate_id": "candidate-clean",
        "slice_id": "slice-clean",
        "obligation_id": "obligation-clean",
        "experiment_id": "experiment-clean",
        "execution_id": "execution-clean",
        "evidence_id": "evidence-clean",
        "finding_id": "finding-clean",
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
    policy_version: str | None = None,
    evaluation_mode: str = "replay",
    product_only_identity: bool = False,
) -> dict:
    target = manifest.target(target_id)
    run_id = f"run-{target_id}"
    frozen_policy_version = policy_version or policy_id
    formal_findings, attempt_ledger = build_formal_evaluation_scope(
        findings,
        run_id=run_id,
        campaign_id=f"campaign-{target_id}",
        target_id=target_id,
        environment_id=target.environment_ref,
        policy_version=frozen_policy_version,
        evaluation_mode=evaluation_mode,
    )
    mainline_run = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id=run_id,
        campaign_id=f"campaign-{target_id}",
        target_id=target_id,
        environment_id=target.environment_ref,
        policy_version=frozen_policy_version,
        evaluation_mode=evaluation_mode,
    )
    formal_scope = build_formal_scope_contract(
        mainline_run=mainline_run,
        findings=formal_findings,
        obligation_attempt_ledger=attempt_ledger,
    )
    if product_only_identity:
        consistency = formal_scope["defect_identity_consistency"]
        formal_scope["defect_identity_consistency"] = (
            build_defect_identity_consistency(
                occurrence_scopes={
                    name: list(values)
                    for name, values in consistency["occurrence_scopes"].items()
                    if name
                    not in {
                        "formal_authority_occurrence_ids",
                        "evaluator_submission_occurrence_ids",
                    }
                },
                canonical_scopes={
                    name: list(values)
                    for name, values in consistency["canonical_scopes"].items()
                    if name != "evaluator_submission_ids"
                },
            )
        )
    policy_strategy_fingerprint = hashlib.sha256(
        f"{policy_id}:{frozen_policy_version}".encode("utf-8")
    ).hexdigest()
    execution_authority = build_test_execution_authority(
        mainline_run=mainline_run,
        obligation_attempt_ledger=attempt_ledger,
        policy_id=policy_id,
        strategy_fingerprint=policy_strategy_fingerprint,
    )
    return evaluate_completed_scan(
        manifest,
        target_id,
        run_id=run_id,
        policy_id=policy_id,
        evaluation_mode=evaluation_mode,
        findings=list(
            formal_scope["formal_count_projection"][
                "canonical_representative_findings"
            ]
        ),
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
        obligation_attempt_ledger=attempt_ledger,
        mainline_run=mainline_run,
        evaluator_policy_identity={
            "policy_id": policy_id,
            "policy_version": frozen_policy_version,
            "strategy_fingerprint": policy_strategy_fingerprint,
        },
        **execution_authority,
        **formal_scope,
    )


def test_evaluator_binds_its_identity_scopes_from_verified_submission(
    tmp_path: Path,
) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))

    receipt = _receipt(
        manifest,
        "held-in",
        [_matched_finding("held-in")],
        product_only_identity=True,
    )

    consistency = receipt["defect_identity_consistency"]
    occurrence_ids = consistency["occurrence_scopes"]["registry_occurrence_ids"]
    canonical_ids = consistency["canonical_scopes"]["canonical_registry_ids"]
    assert consistency["occurrence_scopes"][
        "formal_authority_occurrence_ids"
    ] == occurrence_ids
    assert consistency["occurrence_scopes"][
        "evaluator_submission_occurrence_ids"
    ] == occurrence_ids
    assert consistency["canonical_scopes"][
        "evaluator_submission_ids"
    ] == canonical_ids


def test_aggregate_rejects_mixed_policy_versions_with_valid_hmac(
    tmp_path: Path,
) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    first = _receipt(
        manifest,
        "held-in",
        [_matched_finding("held-in")],
        policy_id="same-policy",
        policy_version="version-a",
    )
    second = _receipt(
        manifest,
        "held-out-fin",
        [_matched_finding("held-out-fin")],
        policy_id="same-policy",
        policy_version="version-b",
    )

    with pytest.raises(EvaluationContractError, match="one policy identity"):
        aggregate_evaluation_receipts(manifest, [first, second])


@pytest.mark.skipif(os.name != "nt", reason="Windows path boundary only")
def test_persist_evaluation_receipt_handles_windows_max_path_boundary(
    tmp_path: Path,
) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipt = _receipt(manifest, "held-in", [_matched_finding("held-in")])
    final_tail = (
        Path(str(receipt["dataset_id"]))
        / str(receipt["dataset_version"])
        / str(receipt["policy_id"])
        / f"{receipt['target_id']}_{receipt['run_id']}.json"
    )
    padding = max(1, 260 - len(str(tmp_path / final_tail)))
    output_root = tmp_path / ("x" * padding)

    path = persist_evaluation_receipt(
        receipt,
        output_root,
        receipt_signing_key=TEST_EVALUATOR_HMAC_KEY,
    )

    with open(_windows_extended_path(path), encoding="utf-8") as handle:
        assert json.load(handle)["receipt_fingerprint"] == receipt[
            "receipt_fingerprint"
        ]


def _empty_scope(
    manifest,
    target_id: str,
    *,
    run_id: str,
    policy_id: str,
    evaluation_mode: str,
) -> dict:
    target = manifest.target(target_id)
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id=run_id,
        campaign_id=f"campaign-{run_id}",
        target_id=target_id,
        environment_id=target.environment_ref,
        policy_version=policy_id,
        evaluation_mode=evaluation_mode,
    )
    _, ledger = build_formal_evaluation_scope(
        [],
        run_id=run_id,
        campaign_id=f"campaign-{run_id}",
        target_id=target_id,
        environment_id=target.environment_ref,
        policy_version=policy_id,
        evaluation_mode=evaluation_mode,
    )
    formal_scope = build_formal_scope_contract(
        mainline_run=mainline,
        findings=[],
        obligation_attempt_ledger=ledger,
    )
    return {
        "mainline_run": mainline,
        "obligation_attempt_ledger": ledger,
        "evaluator_policy_identity": {
            "policy_id": policy_id,
            "policy_version": policy_id,
            "strategy_fingerprint": hashlib.sha256(
                f"{policy_id}:{policy_id}".encode("utf-8")
            ).hexdigest(),
        },
        **formal_scope,
    }


def _trace_ledger(
    target_id: str,
    *,
    policy_id: str = "policy-champion",
    evaluation_mode: str = "replay",
    oracle_failure_votes: int = 0,
    formal_defect_count: int = 0,
) -> tuple[dict, dict, dict]:
    run_id = f"run-{target_id}"
    finding_id = f"finding-{target_id}" if formal_defect_count else ""
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id=run_id,
        campaign_id=f"campaign-{target_id}",
        target_id=target_id,
        environment_id=f"env-{target_id}",
        policy_version=policy_id,
        evaluation_mode=evaluation_mode,
    )
    operational = build_execution_operational_receipt(
        receipt_id=f"operational-{target_id}",
        execution_status="EXECUTED",
        steps=[{
            "phase": "treatment",
            "method": "GET",
            "path": "/resources/resource-1",
            "status_code": 403,
        }],
        cleanup_failures=0,
    )
    attempt_ledger = build_obligation_attempt_ledger(
        mainline_run=mainline,
        selected=[{
            "obligation_id": f"obl-{target_id}",
            "risk_family": "authorization_access_control",
            "required_operations": [f"op-{target_id}"],
            "behavior_slice_id": f"slice-{target_id}",
        }],
        compile_results={
            f"obl-{target_id}": {
                "status": "COMPILED",
                "experiment_id": f"exp-{target_id}",
            }
        },
        execution_results={
            f"obl-{target_id}": {
                "status": "EXECUTED",
                "execution_id": f"exec-{target_id}",
                "observation_receipt_ids": [f"obs-{target_id}"],
                "oracle_receipt_id": f"oracle-{target_id}",
                "operational_receipt": operational,
            }
        },
        gate_results={
            f"obl-{target_id}": {
                "status": "DELIVERABLE" if formal_defect_count else "REJECTED",
                "reason_code": "" if formal_defect_count else "ORACLE_NOT_VIOLATED",
                "gate_receipt_id": f"gate-{target_id}",
                "finding_id": finding_id,
            }
        },
    )
    trace = build_discovery_trace_ledger_v2(
        {
            "obligation_attempt_ledger": attempt_ledger,
            "formal_count_projection": {
                "delivery_occurrence_finding_ids": (
                    [finding_id] if finding_id else []
                ),
                "canonical_defect_ids": [],
            },
        },
        run_id=run_id,
        policy_id=policy_id,
        target_id=target_id,
        project_id=f"project-{target_id}",
        industry="commerce",
        evaluation_mode=evaluation_mode,
    )
    return trace, attempt_ledger, mainline


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

    serialized_receipts = json.dumps(receipts, ensure_ascii=False)
    assert "matched_bug_ids" not in serialized_receipts
    assert "canonical_unmatched" not in serialized_receipts
    assert "gt_unmatched" not in serialized_receipts
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
    serialized = json.dumps(report, ensure_ascii=False)
    assert "ground_truth_source" not in serialized
    assert "matched_bug_ids" not in serialized
    assert "canonical_unmatched" not in serialized
    assert "gt_unmatched" not in serialized


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
        **_empty_scope(
            manifest,
            "held-in",
            run_id="failed-run",
            policy_id="policy-champion",
            evaluation_mode="replay",
        ),
    )
    report = aggregate_evaluation_receipts(manifest, [receipt])

    assert receipt["measurement_status"] == "NOT_MEASURED"
    assert receipt["not_measured_reason"] == "pipeline_health_failed_safe"
    assert receipt["metrics"] == {}
    assert report["claim_status"] == "NOT_MEASURED"
    assert report["evaluation_complete"] is False
    assert "held-in" in {item["target_id"] for item in report["not_measured_targets"]}


def test_runtime_self_hashed_request_receipts_cannot_be_measured_without_attestation(
    tmp_path: Path,
) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))

    receipt = evaluate_completed_scan(
        manifest,
        "held-in",
        run_id="unattested-run",
        policy_id="policy-unattested",
        evaluation_mode="replay",
        findings=[],
        candidates=[],
        pipeline_health={"status": "OK"},
        operational_metrics={},
        **_empty_scope(
            manifest,
            "held-in",
            run_id="unattested-run",
            policy_id="policy-unattested",
            evaluation_mode="replay",
        ),
    )

    assert receipt["measurement_status"] == "NOT_MEASURED"
    assert receipt["not_measured_reason"] == (
        "evaluator_execution_attestation_missing"
    )
    assert receipt["metrics"] == {}


def test_not_measured_operational_nulls_remain_unknown_during_aggregation(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    operational_metrics = {
        **{field: None for field in (
            "wall_clock_seconds",
            "estimated_cost_usd",
            "request_count",
            "cleanup_failures",
            "dirty_test_environments",
            "execution_success_rate",
            "engine_success_rate",
            "duplicate_rate",
        )},
        "production_http_requests": 0,
        "safety_incidents": 0,
        "measurement_status": "NOT_MEASURED",
    }
    receipt = evaluate_completed_scan(
        manifest,
        "held-in",
        run_id="blocked-run",
        policy_id="policy-candidate",
        evaluation_mode="shadow",
        findings=[],
        candidates=[],
        pipeline_health={"status": "BLOCKED"},
        operational_metrics=operational_metrics,
        **_empty_scope(
            manifest,
            "held-in",
            run_id="blocked-run",
            policy_id="policy-candidate",
            evaluation_mode="shadow",
        ),
    )

    report = aggregate_evaluation_receipts(manifest, [receipt])

    assert report["claim_status"] == "NOT_MEASURED"
    assert report["operational"]["complete"] is False
    assert report["operational"]["total_estimated_cost_usd"] is None
    assert report["operational"]["cost_per_true_positive_usd"] is None
    missing = report["operational"]["missing_fields"]
    assert missing == [{
        "target_id": "held-in",
        "fields": [
            "wall_clock_seconds",
            "estimated_cost_usd",
            "request_count",
            "cleanup_failures",
            "dirty_test_environments",
            "execution_success_rate",
            "engine_success_rate",
            "duplicate_rate",
        ],
    }]


def test_cost_unknown_does_not_erase_independently_observed_operational_totals(
    tmp_path: Path,
) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipt = evaluate_completed_scan(
        manifest,
        "held-in",
        run_id="cost-unknown-run",
        policy_id="policy-candidate",
        evaluation_mode="shadow",
        findings=[],
        candidates=[],
        pipeline_health={"status": "OK"},
        operational_metrics={
            "wall_clock_seconds": 10,
            "estimated_cost_usd": None,
            "request_count": 42,
            "production_http_requests": 0,
            "cleanup_failures": 0,
            "safety_incidents": 0,
            "dirty_test_environments": 0,
            "execution_success_rate": 0.8,
            "engine_success_rate": 1.0,
            "duplicate_rate": 0.1,
            "cost_measurement_status": "NOT_MEASURED",
            "promotion_blockers": ["COST_NOT_MEASURED"],
        },
        **_empty_scope(
            manifest,
            "held-in",
            run_id="cost-unknown-run",
            policy_id="policy-candidate",
            evaluation_mode="shadow",
        ),
    )

    operational = aggregate_evaluation_receipts(manifest, [receipt])["operational"]

    assert operational["complete"] is False
    assert operational["total_estimated_cost_usd"] is None
    assert operational["total_wall_clock_seconds"] == 10
    assert operational["total_request_count"] == 42
    assert operational["production_http_requests"] == 0
    assert operational["cleanup_failures"] == 0
    assert operational["execution_success_rate"] == 0.8
    assert operational["field_completeness"]["estimated_cost_usd"] is False
    assert operational["field_completeness"]["request_count"] is True


def test_private_evaluator_reports_per_bug_first_loss_stage(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    trace, attempt_ledger, mainline = _trace_ledger("held-in")
    formal_scope = build_formal_scope_contract(
        mainline_run=mainline,
        findings=[],
        obligation_attempt_ledger=attempt_ledger,
    )
    strategy = hashlib.sha256(
        b"policy-champion:policy-champion"
    ).hexdigest()
    execution_authority = build_test_execution_authority(
        mainline_run=mainline,
        obligation_attempt_ledger=attempt_ledger,
        policy_id="policy-champion",
        strategy_fingerprint=strategy,
    )
    receipt = evaluate_completed_scan(
        manifest,
        "held-in",
        run_id="run-held-in",
        policy_id="policy-champion",
        evaluation_mode="replay",
        findings=[],
        candidates=[],
        pipeline_health={"status": "OK"},
        operational_metrics={},
        trace_ledger=trace,
        obligation_attempt_ledger=attempt_ledger,
        mainline_run=mainline,
        evaluator_policy_identity={
            "policy_id": "policy-champion",
            "policy_version": "policy-champion",
            "strategy_fingerprint": strategy,
        },
        **execution_authority,
        **formal_scope,
    )

    diagnostics = receipt["metrics"]["stage_loss_diagnostics"]
    assert receipt["trace_ledger_projection"]["schema_version"] == (
        "qualibug.discovery-trace-ledger.v3"
    )
    assert receipt["trace_ledger_projection"]["trace_count"] == 1
    assert receipt["trace_ledger_projection"]["ledger_fingerprint"]
    assert receipt["metrics"]["true_positives"] == 0
    assert receipt["metrics"]["false_negatives"] == 1
    assert diagnostics["status"] == "READY"
    assert diagnostics["ground_truth_bug_count"] == 1
    assert diagnostics["first_loss_stage_counts"] == {"oracle_resolution": 1}
    assert diagnostics["stage_reached_rates"]["executed"] == 1.0
    assert diagnostics["stage_reached_rates"]["deliverable"] == 0.0
    bug = diagnostics["bugs"][0]
    assert bug["bug_id"] == "BUG-held-in"
    assert bug["hypothesis_generated"] is True
    assert bug["endpoint_bound"] is True
    assert bug["selected"] is True
    assert bug["executed"] is True
    assert bug["oracle_evaluated"] is True
    assert bug["oracle_matched"] is False
    assert bug["deliverable"] is False
    serialized = json.dumps(diagnostics, ensure_ascii=False).lower()
    assert "alpha" not in serialized
    assert "private" not in serialized


def test_trace_ledger_identity_must_match_evaluated_run(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    trace, attempt_ledger, mainline = _trace_ledger("held-in")
    formal_scope = build_formal_scope_contract(
        mainline_run=mainline,
        findings=[],
        obligation_attempt_ledger=attempt_ledger,
    )
    trace["run_id"] = "wrong-run"

    with pytest.raises(EvaluationContractError, match="trace_ledger.run_id"):
        evaluate_completed_scan(
            manifest,
            "held-in",
            run_id="run-held-in",
            policy_id="policy-champion",
            evaluation_mode="replay",
            findings=[],
            candidates=[],
            pipeline_health={"status": "OK"},
            operational_metrics={},
            trace_ledger=trace,
            obligation_attempt_ledger=attempt_ledger,
            mainline_run=mainline,
            evaluator_policy_identity={
                "policy_id": "policy-champion",
                "policy_version": "policy-champion",
                "strategy_fingerprint": hashlib.sha256(
                    b"policy-champion:policy-champion"
                ).hexdigest(),
            },
            **formal_scope,
        )


def test_receipts_are_immutable(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipt = _receipt(manifest, "held-in", [_matched_finding("held-in")])
    output_root = tmp_path / "receipts"

    path = persist_evaluation_receipt(receipt, output_root)
    assert persist_evaluation_receipt(receipt, output_root) == path
    changed = {**receipt, "measurement_status": "NOT_MEASURED"}
    with pytest.raises(EvaluationContractError, match="immutable evaluation receipt"):
        persist_evaluation_receipt(changed, output_root)


def test_receipt_atomic_temp_name_does_not_exceed_windows_path_limit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    receipt = _receipt(manifest, "held-in", [_matched_finding("held-in")])
    monkeypatch.setattr(
        "ai_test_asset_center.discovery_evaluation_contract.os.getpid",
        lambda: 12345,
    )
    probe_root = tmp_path / "x"
    probe_path = (
        probe_root
        / receipt["dataset_id"]
        / receipt["dataset_version"]
        / receipt["policy_id"]
        / f"{receipt['target_id']}_{receipt['run_id']}.json"
    )
    legacy_temporary = probe_path.with_suffix(
        probe_path.suffix + ".12345.tmp"
    )
    padding = 1 + (260 - len(str(legacy_temporary)))
    assert padding > 0
    output_root = tmp_path / ("x" * padding)
    expected_path = (
        output_root
        / receipt["dataset_id"]
        / receipt["dataset_version"]
        / receipt["policy_id"]
        / f"{receipt['target_id']}_{receipt['run_id']}.json"
    )
    assert len(str(expected_path)) < 260
    assert len(str(expected_path.with_suffix(".json.12345.tmp"))) == 260

    path = persist_evaluation_receipt(receipt, output_root)

    assert path == expected_path
    assert path.exists()


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

    with pytest.raises(EvaluationContractError, match="authentication"):
        build_paired_evaluation_evidence(
            manifest,
            champion_replay=champion_replay,
            challenger_replay=challenger_replay,
            champion_shadow=champion_shadow,
            challenger_shadow=challenger_shadow,
        )


def test_goal_status_reports_implementation_ready_without_inventing_quality(tmp_path: Path) -> None:
    status = assess_discovery_goal_status()

    assert status["schema_version"] == "qualibug.discovery-goal-gate-status.v1"
    assert status["product_ports"] == {"frontend": 5174, "backend": 8088}
    assert status["implementation_ready"] is True
    assert status["gates"]["gate_a_evaluation_integrity"]["passed"] is True
    assert status["gates"]["gate_b_trace_and_weakness_mining"]["passed"] is True
    assert status["gates"]["gate_c_bounded_proposal_and_real_runner"]["passed"] is True
    assert status["gates"]["gate_d_capability_breakthrough"]["status"] == "NOT_MEASURED"
    assert status["gates"]["controlled_commercial_pilot"]["status"] == "NOT_MEASURED"
    assert status["gates"]["full_autonomy_ga"]["status"] == "NOT_MEASURED"
    assert status["commercial_claim_status"] == "NOT_MEASURED"
    assert "evaluation_report_missing" in status["blockers"]


def test_goal_status_fail_closed_when_baseline_cost_missing(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    report = _policy_report(manifest, "policy-champion", "replay")

    status = assess_discovery_goal_status(evaluation_report=report)

    gate_d = status["gates"]["gate_d_capability_breakthrough"]
    assert gate_d["status"] == "NOT_MEASURED"
    assert any(
        item["name"] == "unit_cost_improvement_ratio"
        and item["reason"] == "baseline_cost_per_true_positive_usd_missing"
        for item in gate_d["checks"]
    )
    assert status["commercial_claim_status"] == "NOT_MEASURED"


def test_goal_status_passes_gate_d_only_with_measured_absolute_thresholds(tmp_path: Path) -> None:
    manifest = load_evaluation_manifest(_manifest(tmp_path))
    report = _policy_report(manifest, "policy-champion", "replay")

    status = assess_discovery_goal_status(
        evaluation_report=report,
        baseline_cost_per_true_positive_usd=10.0,
        consecutive_non_regressive_windows=3,
    )

    gate_d = status["gates"]["gate_d_capability_breakthrough"]
    assert gate_d["measurement_status"] == "MEASURED"
    assert gate_d["passed"] is True
    assert status["commercial_claim_status"] in {
        "CAPABILITY_BREAKTHROUGH_REACHED",
        "CONTROLLED_PILOT_ELIGIBLE",
        "FULL_AUTONOMY_GA_ELIGIBLE",
    }
    assert status["product_ports"]["frontend"] == 5174
    assert status["product_ports"]["backend"] == 8088


def test_goal_status_blocks_clean_target_p0_p1_false_positives(tmp_path: Path) -> None:
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

    status = assess_discovery_goal_status(
        evaluation_report=report,
        baseline_cost_per_true_positive_usd=10.0,
    )

    gate_d = status["gates"]["gate_d_capability_breakthrough"]
    assert gate_d["status"] == "FAILED"
    assert gate_d["passed"] is False
    clean_check = next(
        item for item in gate_d["checks"] if item["name"] == "clean_critical_high_false_positives"
    )
    assert clean_check["passed"] is False
    assert clean_check["actual"] == 1.0
    assert status["commercial_claim_status"] == "MEASURED_BELOW_GATE_D"
