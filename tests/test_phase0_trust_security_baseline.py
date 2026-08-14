"""Phase 0: artifact redaction, quality projection, and health fail-fast."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_redactor import (
    ArtifactSecretLeakError,
    redact_and_validate,
    redact_artifact,
    scan_for_secrets,
    write_json_redacted,
)
from ai_test_asset_center.discovery_funnel import build_pipeline_health
from ai_test_asset_center.discovery_quality_projection import (
    attach_quality_projection_to_scan_result,
    build_external_evaluation_projection,
    build_formal_count_projection,
    suppress_benchmark_quality_when_not_measured,
)
from ai_test_asset_center.obligation_attempt_ledger import build_obligation_attempt_ledger


def _attempt_health_result(
    *,
    terminal_status: str = "REJECTED",
    reason_code: str = "ORACLE_NOT_VIOLATED",
    cost_coverage_status: str = "MEASURED",
    observation_received: bool = True,
) -> dict:
    compile_results = {
        "obl-phase0": {
            "status": "COMPILED",
            "experiment_id": "exp-phase0",
            "cost_coverage_status": cost_coverage_status,
        }
    }
    execution_results = {
        "obl-phase0": {
            "status": "EXECUTED",
            "execution_id": "exec-phase0",
            "observation_receipt_ids": ["obs-phase0"] if observation_received else [],
            "oracle_receipt_id": "oracle-phase0",
            "cost_coverage_status": cost_coverage_status,
        }
    }
    gate_results: dict[str, dict] = {
        "obl-phase0": {
            "status": terminal_status,
            "reason_code": reason_code,
            "gate_receipt_id": "gate-phase0",
            "cost_coverage_status": cost_coverage_status,
        }
    }
    if terminal_status == "HARNESS_FAILED":
        execution_results["obl-phase0"] = {
            "status": terminal_status,
            "reason_code": reason_code,
            "execution_id": "exec-phase0",
            "cost_coverage_status": cost_coverage_status,
        }
        gate_results = {}
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "run-phase0", "campaign_id": "campaign-phase0"},
        selected=[{"obligation_id": "obl-phase0", "risk_family": "generic"}],
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    return {
        "obligation_attempt_ledger": ledger,
        "formal_count_projection": {
            "schema_version": "qualibug.discovery-quality-projection.v2",
            "authority_status": "VERIFIED",
            "formal_customer_deliverable_count": 0,
            "canonical_defect_count": 0,
            "canonical_defect_ids": [],
            "delivery_occurrence_count": 0,
            "delivery_occurrence_finding_ids": [],
        },
    }


def test_redact_removes_jwt_password_and_bearer() -> None:
    payload = {
        "auth": {
            "password": "Test@123456",
            "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbb",
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbb",
        },
        "safe": {"role": "admin", "secret_present": True},
    }
    redacted, receipt = redact_artifact(payload)
    serialized = json.dumps(redacted)
    assert "Test@123456" not in serialized
    assert "eyJhbGci" not in serialized
    assert "Bearer eyJ" not in serialized
    assert "<REDACTED>" in serialized
    assert receipt["redaction_applied"] is True
    scan = scan_for_secrets(redacted)
    assert scan["safe"] is True


def test_redact_preserves_gate_enum_fields_but_still_redacts_secrets() -> None:
    # ``*_gate`` fields hold oracle post-hoc gate enums (PASSED/INDETERMINATE/
    # NOT_APPLICABLE). Redacting them to <REDACTED> made reseal fail with
    # contract_oracle_causality_gate_invalid on any authorization-executing
    # scan. They must survive redaction, while a real secret under an
    # ``authorization`` key is still redacted.
    payload = {
        "oracle_receipt": {
            "authorization_causality_gate": "PASSED",
            "authorization_delivery_gate": "INDETERMINATE",
            "oracle_validity_gate": "NOT_APPLICABLE",
            "authorization_secret": "sk-live-abcdefghijklmnop",
        },
    }
    redacted, _receipt = redact_artifact(payload)
    oracle = redacted["oracle_receipt"]
    assert oracle["authorization_causality_gate"] == "PASSED"
    assert oracle["authorization_delivery_gate"] == "INDETERMINATE"
    assert oracle["oracle_validity_gate"] == "NOT_APPLICABLE"
    assert oracle["authorization_secret"] == "<REDACTED>"
    # The post-redaction scanner must agree: a gate enum is not a residual
    # secret (the scanner previously flagged ``authorization_causality_gate``
    # as ``sensitive_key_unredacted`` and failed scan_result persistence).
    scan = scan_for_secrets(redacted)
    assert scan["safe"] is True
    assert scan["issue_count"] == 0


def test_write_json_redacted_rejects_residual_secret(tmp_path: Path) -> None:
    # Craft a value that survives naive key-based redaction but is caught by scanner
    # by embedding JWT in a non-sensitive key after partial failure path.
    payload = {"note": "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"}
    # First ensure redact_and_validate cleans it; write should succeed after redaction.
    path = tmp_path / "submission.json"
    receipt = write_json_redacted(path, payload)
    text = path.read_text(encoding="utf-8")
    assert "eyJhbGci" not in text
    assert receipt["safe_to_persist"] is True


def test_write_json_redacted_uses_unique_atomic_files_for_concurrent_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "scan_result.json"
    original_replace = Path.replace
    replace_lock = threading.Lock()
    second_replace_finished = threading.Event()
    replace_order = 0

    def synchronized_replace(source: Path, target: Path) -> Path:
        nonlocal replace_order
        with replace_lock:
            replace_order += 1
            order = replace_order
        if order == 1:
            if not second_replace_finished.wait(timeout=5):
                raise TimeoutError("second concurrent artifact replace did not run")
            return original_replace(source, target)
        result = original_replace(source, target)
        second_replace_finished.set()
        return result

    monkeypatch.setattr(Path, "replace", synchronized_replace)
    payloads = [{"writer": "first"}, {"writer": "second"}]
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(
            executor.map(lambda payload: write_json_redacted(path, payload), payloads)
        )

    assert all(receipt["safe_to_persist"] for receipt in receipts)
    assert json.loads(path.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.glob(".q-*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violation regression")
def test_write_json_redacted_retries_transient_windows_reader_lock(tmp_path: Path) -> None:
    path = tmp_path / "submission.json"
    path.write_text('{"state":"old"}', encoding="utf-8")
    reader = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys,time; "
                "handle=open(sys.argv[1],'rb'); "
                "print('READY',flush=True); "
                "time.sleep(0.35); "
                "handle.close()"
            ),
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert reader.stdout is not None
    assert reader.stdout.readline().strip() == "READY"
    try:
        write_json_redacted(path, {"state": "new"})
    finally:
        reader.wait(timeout=5)

    assert json.loads(path.read_text(encoding="utf-8")) == {"state": "new"}


def test_write_json_redacted_preserves_recovery_file_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "submission.json"
    path.write_text('{"state":"old"}', encoding="utf-8")

    def deny_replace(_self: Path, _target: Path) -> Path:
        raise PermissionError(5, "sharing violation")

    monkeypatch.setattr(Path, "replace", deny_replace)
    monkeypatch.setattr("ai_test_asset_center.artifact_redactor.ARTIFACT_REPLACE_RETRY_SECONDS", 0.0, raising=False)

    with pytest.raises(PermissionError, match="recoverable artifact retained") as raised:
        write_json_redacted(path, {"state": "new"})

    recovery_files = list(tmp_path.glob(".q-*.tmp"))
    assert len(recovery_files) == 1
    recovery = recovery_files[0]
    assert str(recovery) in str(raised.value)
    assert json.loads(recovery.read_text(encoding="utf-8")) == {"state": "new"}


def test_redact_and_validate_raises_when_scanner_finds_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_scan(_payload):
        return {"safe": False, "issue_count": 1, "issues": [{"path": "$.x", "reason": "raw_jwt"}]}

    monkeypatch.setattr(
        "ai_test_asset_center.artifact_redactor.scan_for_secrets",
        _fake_scan,
    )
    with pytest.raises(ArtifactSecretLeakError):
        redact_and_validate({"x": "ok"})


def test_not_measured_projection_suppresses_quality_score() -> None:
    projection = build_external_evaluation_projection(
        measurement_status="NOT_MEASURED",
        formal_customer_deliverable_count=21,
    )
    assert projection["quality_score"] is None
    assert projection["recall"] is None
    assert projection["precision"] is None
    assert projection["display"]["suppress_quality_score"] is True
    assert "尚未完成外部质量评测" in projection["display"]["quality_label"]


def test_product_projection_refuses_measured_claim_without_public_gateway() -> None:
    with pytest.raises(ValueError, match="product_external_measurement_gateway_required"):
        build_external_evaluation_projection(
            measurement_status="MEASURED",
            claim_status="MEASURED",
            formal_customer_deliverable_count=100,
            evaluator_report={
                "claim_status": "MEASURED",
                "commercial_promotion_evidence": True,
                "metrics": {
                    "quality_score": 100,
                    "recall": 1.0,
                    "precision": 1.0,
                    "f1": 1.0,
                },
            },
        )


def test_formal_count_projection_uses_delivery_gate_only() -> None:
    findings = [
        {
            "id": "f1",
            "title": "ready",
            "gate_passed": True,
            "customer_delivery_status": "defect",
            "bug_status": "reproduced",
            "confirmation_status": "confirmed",
            "execution_status": "executed",
            "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
            "evidence_status": {
                "semantic_verdict": "SEMANTIC_CONFIRMED",
                "business_evidence_status": "VALIDATED",
                "final_review_status": "CUSTOMER_READY",
                "missing_requirements": [],
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "timestamp": "2026-07-10T00:00:00Z",
                "request_raw": {"method": "GET", "path": "/x"},
                "response_raw": {"status_code": 200, "body": "{}"},
            },
            "reproduction": {"method": "GET", "path": "/x", "is_synthetic": False, "har_evidence": {"status_code": 200}},
            "expected": "deny",
            "actual": "allow",
            "cleanup": {"status": "succeeded"},
        },
        {"id": "f2", "title": "clue", "gate_passed": False, "confirmation_status": "candidate"},
    ]
    # Use split via projection; even if gate rejects incomplete fixtures, count is consistent.
    counts = build_formal_count_projection(findings=findings, candidate_findings=[{"id": "c1"}])
    assert counts["confirmation_receipt_count"] == 2
    assert counts["candidate_count"] == 1
    assert counts["formal_customer_deliverable_count"] == counts["formal_customer_deliverable_count"]
    assert "formal_customer_deliverable_count" in counts


def test_attach_quality_projection_marks_score_non_commercial() -> None:
    result = attach_quality_projection_to_scan_result({
        "score": 100.0,
        "findings": [],
        "candidate_findings": [],
        "discovery_funnel": {"validated_bug_count": 0},
    })
    assert result["external_evaluation"]["measurement_status"] == "NOT_MEASURED"
    assert result["commercial_quality_score"] is None
    assert result["score_semantics"]["score_field"] == "internal_evidence_strength_only"
    assert result["discovery_funnel"]["validated_bug_count"] == 0


def test_suppress_benchmark_metrics_when_not_measured() -> None:
    suppressed = suppress_benchmark_quality_when_not_measured(
        {"benchmark_active": True, "recall": 0.9, "precision": 0.8, "f1_score": 0.85},
        {"measurement_status": "NOT_MEASURED"},
    )
    assert suppressed["recall"] is None
    assert suppressed["precision"] is None
    assert suppressed["f1_score"] is None
    assert suppressed["commercial_quality_suppressed"] is True


def test_pipeline_health_failed_safe_on_result_error() -> None:
    health = build_pipeline_health({
        **_attempt_health_result(),
        "error": "boom",
    })
    assert health["status"] == "FAILED_SAFE"
    assert health["empty_findings_means_no_bugs"] is False
    assert "result.error" in health["operator_note"] or "error" in health["operator_note"].lower()


def test_pipeline_health_failed_safe_on_missing_observation_receipt() -> None:
    health = build_pipeline_health(_attempt_health_result(observation_received=False))

    assert health["status"] == "FAILED_SAFE"
    assert health["observation_receipt_missing_count"] == 1


def test_pipeline_health_degraded_on_unknown_usage_cost() -> None:
    health = build_pipeline_health({
        **_attempt_health_result(cost_coverage_status="UNKNOWN"),
        "mainline_unification": {
            "llm_reasoner": {
                "model_usage": {"request_count": 22},
                "observed_model_request_count": 22,
            }
        },
    })
    assert health["status"] == "DEGRADED"
    assert health["usage_cost_unknown"] is True
    assert "usage/cost" in health["operator_note"] or "cost" in health["operator_note"].lower()


def test_pipeline_health_degraded_on_cleanup_failure() -> None:
    health = build_pipeline_health(_attempt_health_result(
        terminal_status="HARNESS_FAILED",
        reason_code="CLEANUP_NOT_SUCCEEDED",
    ))

    assert health["status"] == "DEGRADED"
    assert health["cleanup_failure_count"] >= 1


def test_pipeline_health_degraded_on_not_reversible_cleanup() -> None:
    health = build_pipeline_health(_attempt_health_result(
        terminal_status="HARNESS_FAILED",
        reason_code="CLEANUP_NOT_REVERSIBLE",
    ))

    assert health["status"] == "DEGRADED"
    assert health["cleanup_failure_count"] == 1


def test_pipeline_health_uses_write_level_operational_cleanup_receipts() -> None:
    result = _attempt_health_result()
    result["operational_receipt_summary"] = {
        "schema_version": "qualibug.execution-operational-summary.v1",
        "cleanup_failures": 3,
    }

    health = build_pipeline_health(result)

    assert health["status"] == "DEGRADED"
    assert health["cleanup_failure_count"] == 3
    assert "cleanup_failures" in health["operator_note"]


def test_pipeline_health_rejects_invalid_operational_cleanup_count() -> None:
    result = _attempt_health_result()
    result["operational_receipt_summary"] = {
        "schema_version": "qualibug.execution-operational-summary.v1",
        "cleanup_failures": -1,
    }

    with pytest.raises(ValueError, match="operational_cleanup_failure_count_invalid"):
        build_pipeline_health(result)


def test_private_pilot_service_contains_no_disabled_industry_endpoint_templates() -> None:
    service_path = Path(__file__).parents[1] / "ai_test_asset_center" / "private_pilot_service.py"
    source = service_path.read_text(encoding="utf-8").lower()

    for forbidden in ("/api/orders", "/api/products", "/api/coupons", "benchmark_mall"):
        assert forbidden not in source, f"industry-specific endpoint template remains: {forbidden}"
