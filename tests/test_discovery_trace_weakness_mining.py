from __future__ import annotations

import json

import pytest

from ai_test_asset_center.discovery_trace_ledger import (
    DiscoveryTraceError,
    build_discovery_trace_ledger_v2,
    migrate_trace_ledger_v1_to_v3,
    persist_trace_ledger,
    validate_trace_ledger,
)
from ai_test_asset_center.discovery_weakness_miner import mine_discovery_weaknesses
from ai_test_asset_center.obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    build_obligation_attempt_ledger,
)


def _attempt_result(
    *,
    run_id: str = "RUN-1",
    terminal_status: str = "REJECTED",
    reason_code: str = "ORACLE_NOT_VIOLATED",
    finding_id: str = "",
) -> dict:
    selected = [{
        "obligation_id": "obl-1",
        "candidate_id": "cand-1",
        "risk_family": "authorization",
        "required_operations": ["op-read-resource"],
        "adapter": "http_api",
        "behavior_slice_id": "BHV-LINEAGE-1",
        "source_refs": [{"source_type": "openapi", "source_id": "SRC-1"}],
    }]
    compile_results = {
        "obl-1": {
            "status": "COMPILED",
            "experiment_id": "exp-1",
            "receipt_id": "private/path/must-not-persist",
        }
    }
    execution_results: dict[str, dict] = {}
    gate_results: dict[str, dict] = {}
    if terminal_status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}:
        execution_results["obl-1"] = {
            "status": terminal_status,
            "reason_code": reason_code,
            "execution_id": "exec-1",
            "receipt_id": "execution-1",
            "elapsed_ms": 8,
        }
    else:
        execution_results["obl-1"] = {
            "status": "EXECUTED",
            "execution_id": "exec-1",
            "receipt_id": "execution-1",
            "observation_receipt_ids": ["obs-1"],
            "oracle_receipt_id": "oracle-1",
            "elapsed_ms": 8,
        }
        gate_results["obl-1"] = {
            "status": terminal_status,
            "reason_code": reason_code,
            "gate_receipt_id": "gate-1",
            "finding_id": finding_id,
        }
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": run_id, "campaign_id": "CMP-1"},
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    return {
        "obligation_attempt_ledger": ledger,
        "formal_count_projection": {
            "delivery_occurrence_finding_ids": (
                [finding_id] if terminal_status == "DELIVERABLE" else []
            ),
            "canonical_defect_ids": [],
        },
        "private_payload": {
            "request_body": "must-not-persist",
            "response_body": "must-not-persist",
        },
    }


def _trace(result: dict, *, run_id: str = "RUN-1", target_id: str = "TARGET-1") -> dict:
    return build_discovery_trace_ledger_v2(
        result,
        run_id=run_id,
        policy_id="POLICY-1",
        target_id=target_id,
        project_id="PROJECT-1",
        industry="industry-a",
        evaluation_mode="replay",
    )


def test_trace_v2_has_one_row_per_obligation_attempt_without_raw_payloads() -> None:
    ledger = _trace(_attempt_result())

    assert ledger["schema_version"] == "qualibug.discovery-trace-ledger.v3"
    assert ledger["attempt_count"] == 1
    assert {row["obligation_id"] for row in ledger["attempts"]} == {"obl-1"}
    attempt = ledger["attempts"][0]
    assert attempt["risk_family"] == "authorization"
    assert attempt["operation_refs"] == ["op-read-resource"]
    assert attempt["adapter"] == "http_api"
    assert attempt["behavior_slice_id"] == "BHV-LINEAGE-1"
    assert attempt["source_kinds"] == ["openapi"]
    assert attempt["compile_reason_code"] == ""
    assert attempt["execution_reason_code"] == ""
    assert attempt["gate_reason_code"] == "ORACLE_NOT_VIOLATED"
    assert attempt["terminal_status"] == "REJECTED"
    assert attempt["outcome"] == "valid_success_control"
    assert ledger["delivery_occurrence_finding_ids"] == []
    assert ledger["redaction_contract"]["ground_truth_persisted"] is False
    serialized = json.dumps(ledger, ensure_ascii=False)
    assert "must-not-persist" not in serialized


def test_trace_persistence_bounds_long_windows_target_paths(tmp_path) -> None:
    target_id = "scope-" + ("x" * 180)
    ledger = _trace(_attempt_result(), target_id=target_id)

    path = persist_trace_ledger(ledger, tmp_path / ("output-" + ("x" * 70)))

    assert path.exists()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["target_id"] == target_id
    if __import__("os").name == "nt":
        assert len(str(path)) <= 240


def test_runtime_rejects_v1_without_explicit_migration() -> None:
    with pytest.raises(DiscoveryTraceError, match="trace_ledger_v2_required"):
        validate_trace_ledger({
            "schema_version": "qualibug.discovery-trace-ledger.v1",
            "traces": [],
        })


def test_v1_migration_requires_complete_operator_mapping() -> None:
    v1 = {
        "schema_version": "qualibug.discovery-trace-ledger.v1",
        "run_id": "RUN-OLD",
        "policy_id": "POLICY-1",
        "target_id": "TARGET-1",
        "project_id": "PROJECT-1",
        "industry": "industry-a",
        "evaluation_mode": "replay",
        "redaction_contract": {
            "raw_request_bodies_persisted": False,
            "raw_response_bodies_persisted": False,
            "credentials_persisted": False,
            "ground_truth_persisted": False,
        },
        "traces": [
            {
                "trace_id": "TRACE-1",
                "behavior_slice_id": "BHV-1",
                "outcome": "harness_or_execution_failure",
                "failure_signatures": ["RUNTIME_PATH_BINDING_MISSING"],
            }
        ],
    }

    with pytest.raises(
        DiscoveryTraceError,
        match="v1_migration_obligation_map_incomplete:BHV-1",
    ):
        migrate_trace_ledger_v1_to_v3(v1, obligation_map={})

    migrated = migrate_trace_ledger_v1_to_v3(
        v1,
        obligation_map={"BHV-1": "obl-1"},
    )
    assert migrated["schema_version"] == "qualibug.discovery-trace-ledger.v3"
    assert migrated["migration"] == {
        "source_schema": "qualibug.discovery-trace-ledger.v1",
        "explicit": True,
    }
    assert migrated["attempts"][0]["obligation_id"] == "obl-1"
    assert validate_trace_ledger(migrated)["ledger_fingerprint"]


def test_v1_migration_cli_writes_new_immutable_artifact(tmp_path) -> None:
    from tools.migrate_discovery_trace_ledger import main

    source = tmp_path / "trace-v1.json"
    mapping = tmp_path / "obligation-map.json"
    output = tmp_path / "trace-v2.json"
    source.write_text(json.dumps({
        "schema_version": "qualibug.discovery-trace-ledger.v1",
        "run_id": "RUN-OLD",
        "policy_id": "POLICY-1",
        "target_id": "TARGET-1",
        "project_id": "PROJECT-1",
        "industry": "industry-a",
        "evaluation_mode": "replay",
        "redaction_contract": {
            "raw_request_bodies_persisted": False,
            "raw_response_bodies_persisted": False,
            "credentials_persisted": False,
            "ground_truth_persisted": False,
        },
        "traces": [{
            "trace_id": "TRACE-1",
            "behavior_slice_id": "BHV-1",
            "outcome": "valid_success_control",
            "failure_signatures": [],
        }],
    }), encoding="utf-8")
    mapping.write_text(json.dumps({"BHV-1": "obl-1"}), encoding="utf-8")

    assert main([
        "--input", str(source),
        "--obligation-map", str(mapping),
        "--output", str(output),
    ]) == 0
    assert validate_trace_ledger(json.loads(output.read_text(encoding="utf-8")))
    with pytest.raises(FileExistsError, match="migration output already exists"):
        main([
            "--input", str(source),
            "--obligation-map", str(mapping),
            "--output", str(output),
        ])


def test_trace_cannot_start_from_legacy_deliverable_gate() -> None:
    with pytest.raises(
        ObligationAttemptLedgerError,
        match="formal_gate_v2_required:obl-1",
    ):
        _attempt_result(
            terminal_status="DELIVERABLE",
            reason_code="",
            finding_id="FINDING-1",
        )


def test_weakness_miner_clusters_attempt_stage_reasons_and_dimensions() -> None:
    first = _trace(
        _attempt_result(
            terminal_status="HARNESS_FAILED",
            reason_code="CLEANUP_COMPENSATION_FAILED",
        )
    )
    second = build_discovery_trace_ledger_v2(
        _attempt_result(
            run_id="RUN-2",
            terminal_status="HARNESS_FAILED",
            reason_code="CLEANUP_COMPENSATION_FAILED",
        ),
        run_id="RUN-2",
        policy_id="POLICY-1",
        target_id="TARGET-2",
        project_id="PROJECT-2",
        industry="industry-b",
        evaluation_mode="shadow",
    )

    report = mine_discovery_weaknesses([first, second])
    patterns = {item["failure_signature"]: item for item in report["patterns"]}
    cleanup = patterns["CLEANUP_FAILED"]
    assert cleanup["severity"] == "critical"
    assert cleanup["proposal_eligible"] is True
    assert cleanup["affected_run_count"] == 2
    assert cleanup["affected_industry_count"] == 2
    assert cleanup["affected_risk_families"] == ["authorization"]
    assert cleanup["affected_operation_refs"] == ["op-read-resource"]
    assert cleanup["affected_adapters"] == ["http_api"]
    assert cleanup["execution_reason_codes"] == ["CLEANUP_COMPENSATION_FAILED"]
    assert cleanup["terminal_statuses"] == ["HARNESS_FAILED"]
    assert report["privacy_contract"]["ground_truth_used"] is False


def test_multi_write_audit_reason_maps_to_sandbox_weakness() -> None:
    ledger = _trace(
        _attempt_result(
            terminal_status="HARNESS_FAILED",
            reason_code="MULTI_WRITE_AUDIT_INCOMPLETE",
        )
    )
    report = mine_discovery_weaknesses([ledger])
    pattern = next(
        row
        for row in report["patterns"]
        if row["failure_signature"] == "MULTI_WRITE_AUDIT_INCOMPLETE"
    )
    assert pattern["severity"] == "critical"
    assert pattern["harness_surface"] == "sandbox_write_policy"
