from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.job_formal_planning_proof import (
    PROTOCOL_ID,
    attach_job_formal_planning_proof,
    build_job_formal_planning_proof,
)
from ai_test_asset_center.scan_post_hooks import (
    apply_scan_post_hooks,
    clear_scan_post_hooks,
    list_scan_post_hooks,
)


def _lineage(*, drift: bool = False) -> dict:
    return {
        "schema": "qualibug.async-job-lineage-receipt.v1",
        "job_asset_id": "job-asset-report-daily",
        "operation_id": "bir_operation_report_daily",
        "behavior_id": "business-behavior-report-daily",
        "invariant_id": "bir_invariant_report_daily",
        "obligation_id": "obl-report-daily",
        "experiment_id": "exp-report-daily",
        "protocol_id": PROTOCOL_ID,
        "identity_complete": True,
        "identity_drift": drift,
        "fingerprint": "a" * 64,
    }


def _scan_result(*, selected: bool = True, drift: bool = False) -> dict:
    lineage = _lineage(drift=drift)
    selected_rows = (
        [{"obligation_id": "obl-report-daily", "risk_family": "process"}]
        if selected
        else []
    )
    return {
        "success": True,
        "scan_id": "scan_job_02_proof",
        "project": "job_02_proof",
        "findings": [],
        "candidate_findings": [],
        "v12": {
            "mainline_run": {
                "run_id": "RUN_JOB_02_PROOF",
                "campaign_id": "campaign_job_02_proof",
                "contract_fingerprint": "b" * 64,
            },
            "behavior_ir": {
                "operations": [
                    {
                        "id": "bir_operation_report_daily",
                        "operation_kind": "ASYNC_JOB",
                    }
                ],
                "invariants": [
                    {
                        "id": "bir_invariant_report_daily",
                        "expression": {
                            "kind": "async_job_runtime_integrity_contract"
                        },
                    }
                ],
            },
            "test_obligations": {
                "obligations": [
                    {
                        "obligation_id": "obl-report-daily",
                        "risk_family": "process",
                        "property": {
                            "template": "source_declared_async_job_execution",
                            "runtime_integrity_only": True,
                            "formal_business_finding_eligible": False,
                        },
                    }
                ]
            },
            "experiment_compile": {
                "all_experiments": [
                    {
                        "experiment_id": "exp-report-daily",
                        "obligation_id": "obl-report-daily",
                        "risk_family": "process",
                        "property": {
                            "template": "source_declared_async_job_execution",
                            "runtime_integrity_only": True,
                            "formal_business_finding_eligible": False,
                        },
                        "treatment_plan": [
                            {
                                "step_id": "job_treatment_1",
                                "method": "JOB",
                                "intent": "source_declared_async_job_execution",
                                "protocol_step": "async_job_treatment",
                            }
                        ],
                        "assertion": {
                            "kind": "process_completion",
                            "runtime_integrity_only": True,
                            "formal_business_finding_eligible": False,
                        },
                        "compile_receipt": {
                            "status": "COMPILED",
                            "async_job_lineage_fingerprint": "a" * 64,
                        },
                        "async_job_lineage_receipt": lineage,
                    }
                ]
            },
            "obligation_plan": {"selected": selected_rows},
        },
    }


def test_build_job_formal_planning_proof_passes_only_for_selected_complete_lineage() -> None:
    proof = build_job_formal_planning_proof(_scan_result())

    assert proof["status"] == "PASS"
    assert proof["first_terminal_reason"] == ""
    assert proof["metrics"]["async_job_operation_count"] == 1
    assert proof["metrics"]["job_runtime_invariant_count"] == 1
    assert proof["metrics"]["job_obligation_count"] == 1
    assert proof["metrics"]["compiled_job_experiment_count"] == 1
    assert proof["metrics"]["selected_job_obligation_count"] == 1
    assert proof["metrics"]["complete_lineage_count"] == 1
    assert proof["metrics"]["new_findings_created_by_projection"] == 0
    assert proof["claim_boundary"]["job_asset_to_compiled_experiment"] == (
        "PROVEN_BY_FORMAL_SCAN_RESULT"
    )
    assert proof["claim_boundary"]["real_job_platform_runtime"] == "NOT_MEASURED"
    assert proof["claim_boundary"]["job_bug_discovery"] == "NOT_MEASURED"
    assert len(proof["proof_fingerprint"]) == 64


def test_compiled_but_unselected_job_is_not_reported_as_proven() -> None:
    proof = build_job_formal_planning_proof(_scan_result(selected=False))

    assert proof["status"] == "COMPILED_NOT_SELECTED"
    assert proof["first_terminal_reason"] == "ASYNC_JOB_OBLIGATION_NOT_SELECTED"
    assert proof["metrics"]["compiled_job_experiment_count"] == 1
    assert proof["metrics"]["selected_job_obligation_count"] == 0
    assert proof["claim_boundary"]["job_asset_to_compiled_experiment"] == "NOT_PROVEN"


def test_lineage_drift_fails_safe() -> None:
    proof = build_job_formal_planning_proof(_scan_result(drift=True))

    assert proof["status"] == "FAILED_SAFE"
    assert proof["first_terminal_reason"] == "ASYNC_JOB_LINEAGE_IDENTITY_DRIFT"
    assert proof["metrics"]["lineage_drift_count"] == 1
    assert proof["metrics"]["complete_lineage_count"] == 0


def test_attach_persists_proof_and_reseals_scan_result(tmp_path: Path) -> None:
    projected = attach_job_formal_planning_proof(
        _scan_result(),
        project="job_02_proof",
        root=tmp_path,
    )

    proof_path = (
        tmp_path
        / "platform_outputs"
        / "job_02_proof"
        / "job_02_proof"
        / "job_planning_proof.json"
    )
    scan_result_path = (
        tmp_path / "platform_outputs" / "job_02_proof" / "scan_result.json"
    )
    assert proof_path.is_file()
    assert scan_result_path.is_file()
    assert projected["job_planning_proof"]["status"] == "PASS"
    assert projected["job_planning_proof_ref"].endswith(
        "job_02_proof/job_planning_proof.json"
    )
    persisted_proof = json.loads(proof_path.read_text(encoding="utf-8"))
    persisted_scan = json.loads(scan_result_path.read_text(encoding="utf-8"))
    assert persisted_proof["proof_fingerprint"] == (
        projected["job_planning_proof"]["proof_fingerprint"]
    )
    assert persisted_scan["job_planning_proof"]["status"] == "PASS"
    assert persisted_scan["findings"] == []


def test_public_scan_post_hook_registry_auto_restores_job_proof(tmp_path: Path) -> None:
    clear_scan_post_hooks()
    assert "job_formal_planning_proof" not in list_scan_post_hooks()

    projected = apply_scan_post_hooks(
        _scan_result(),
        project="job_02_proof",
        root=tmp_path,
    )

    assert "job_formal_planning_proof" in list_scan_post_hooks()
    assert projected["job_planning_proof"]["status"] == "PASS"
    assert projected["findings"] == []
