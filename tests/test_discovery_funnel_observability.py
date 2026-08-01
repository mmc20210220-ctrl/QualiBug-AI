"""Attempt-receipt-only discovery funnel and pipeline health tests."""
from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center import discovery_funnel as funnel_module
from ai_test_asset_center.discovery_funnel import (
    DiscoveryFunnelError,
    build_funnel,
    build_pipeline_health,
    effective_execution_status,
)
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    build_obligation_attempt_ledger,
)


REQUIRED_STAGES = [
    "obligation_generation",
    "experiment_compile",
    "binding_materialization",
    "fixture_setup",
    "governed_execution",
    "observation",
    "assertion",
    "oracle_resolution",
    "delivery_gate",
    "cleanup",
    "formal_projection",
]


def _result(*, harness_failed: bool = False) -> dict:
    selected = [
        {
            "obligation_id": "obl-1",
            "risk_family": "authorization",
            "required_operations": ["op-read"],
            "required_actors": ["actor-admin"],
            "adapter": "http_api",
            "planning_round": 1,
            "source_refs": [{"source_type": "openapi", "source_id": "SRC-1"}],
        },
        {
            "obligation_id": "obl-2",
            "risk_family": "state",
            "required_operations": ["op-write"],
            "adapter": "http_api",
            "planning_round": 2,
            "source_refs": [{"source_type": "requirement", "source_id": "SRC-2"}],
        },
    ]
    compile_results = {
        "obl-1": {
            "status": "COMPILED",
            "experiment_id": "exp-1",
            "elapsed_ms": 4,
        },
        "obl-2": {
            "status": "COMPILED" if harness_failed else "BLOCKED",
            "experiment_id": "exp-2" if harness_failed else "",
            "reason_code": "" if harness_failed else "BLOCKED_MISSING_BINDING",
            "elapsed_ms": 8,
        },
    }
    execution_results = {
        "obl-1": {
            "status": "EXECUTED",
            "execution_id": "exec-1",
            "observation_receipt_ids": ["obs-1"],
            "oracle_receipt_id": "oracle-1",
            "elapsed_ms": 12,
        }
    }
    gate_results = {
        "obl-1": {
            "status": "REJECTED",
            "reason_code": "ORACLE_NOT_VIOLATED",
            "gate_receipt_id": "gate-1",
            "elapsed_ms": 3,
        }
    }
    if harness_failed:
        execution_results["obl-2"] = {
            "status": "HARNESS_FAILED",
            "reason_code": "CLEANUP_COMPENSATION_FAILED",
            "execution_id": "exec-2",
            "elapsed_ms": 20,
        }
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-1", "campaign_id": "CMP-1"},
        selected=selected,
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
        # Conflicting legacy counters must have no authority.
        "phases": {"execution": {"status": "completed", "executed": 999}},
        "experiment_execution": {"selected_count": 999, "executed_count": 999},
        "findings": [{"finding_id": "legacy-fake", "gate_passed": True}],
    }


def test_execution_status_requires_attempt_ledger() -> None:
    with pytest.raises(DiscoveryFunnelError, match="obligation_attempt_ledger_missing"):
        effective_execution_status({
            "phases": {"execution": {"status": "completed"}},
        })


def test_attempt_ledger_is_the_only_execution_status_source() -> None:
    result = _result()

    assert effective_execution_status(result) == "completed"
    assert build_pipeline_health(result)["selected_obligation_count"] == 2
    assert build_pipeline_health(result)["terminal_obligation_count"] == 2


def test_pipeline_health_requires_formal_projection_receipt() -> None:
    result = _result()
    result.pop("formal_count_projection")

    with pytest.raises(DiscoveryFunnelError, match="formal_count_projection_missing"):
        build_pipeline_health(result)


def test_pipeline_health_rejects_formal_ids_not_backed_by_attempts() -> None:
    result = _result()
    result["formal_count_projection"] = {
        "schema_version": "qualibug.discovery-quality-projection.v2",
        "authority_status": "VERIFIED",
        "formal_customer_deliverable_count": 1,
        "canonical_defect_count": 1,
        "canonical_defect_ids": ["cdef-without-attempt"],
        "delivery_occurrence_count": 1,
        "delivery_occurrence_finding_ids": ["finding-without-attempt"],
    }

    with pytest.raises(DiscoveryFunnelError, match="formal_projection_attempt_id_mismatch"):
        build_pipeline_health(result)


def test_conservation_uses_validated_formal_scope_for_quarantined_attempts(
    monkeypatch,
) -> None:
    ledger = {
        "selected_count": 1,
        "terminal_count": 1,
        "attempts": [
            {
                "obligation_id": "obl-quarantined",
                "terminal_status": "DELIVERABLE",
                "finding_id": "finding-quarantined",
                "stages": [
                    {"stage": "compile", "status": "COMPILED"},
                    {"stage": "execution", "status": "EXECUTED"},
                    {"stage": "gate", "status": "DELIVERABLE"},
                ],
            }
        ],
    }
    monkeypatch.setattr(
        funnel_module,
        "validate_obligation_attempt_ledger",
        lambda value: value,
    )
    monkeypatch.setattr(
        funnel_module,
        "validated_delivery_gate_finding_ids",
        lambda value: ["finding-formal"],
    )

    conservation = funnel_module.build_funnel_conservation(
        {
            "obligation_attempt_ledger": ledger,
            "formal_count_projection": {
                "schema_version": "qualibug.discovery-quality-projection.v2",
                "formal_customer_deliverable_count": 1,
                "canonical_defect_ids": ["cdef-formal"],
                "delivery_occurrence_count": 1,
                "delivery_occurrence_finding_ids": ["finding-formal"],
            },
        }
    )

    identity_check = next(
        row
        for row in conservation["checks"]
        if row["name"] == "delivery_identity_conservation"
    )
    assert identity_check["status"] == "PASS"
    assert identity_check["expected"] == ["finding-formal"]
    assert identity_check["observed"] == ["finding-formal"]


def test_shadow_attempt_cannot_use_legacy_deliverable_gate() -> None:
    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-SHADOW",
        campaign_id="CMP-SHADOW",
        target_id="TARGET-SHADOW",
        environment_id="ENV-SHADOW",
        policy_version="policy-shadow",
        evaluation_mode="shadow",
    )
    with pytest.raises(
        ObligationAttemptLedgerError,
        match="formal_gate_v2_required:obl-shadow",
    ):
        build_obligation_attempt_ledger(
            mainline_run=contract,
            selected=[{"obligation_id": "obl-shadow"}],
            compile_results={"obl-shadow": {"status": "COMPILED"}},
            execution_results={
                "obl-shadow": {
                    "status": "EXECUTED",
                    "observation_receipt_ids": ["obs-shadow"],
                    "oracle_receipt_id": "oracle-shadow",
                }
            },
            gate_results={
                "obl-shadow": {
                    "status": "DELIVERABLE",
                    "finding_id": "finding-shadow",
                    "gate_receipt_id": "gate-shadow",
                }
            },
        )


def test_zero_selected_obligations_cannot_claim_no_bugs() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-EMPTY", "campaign_id": "CMP-EMPTY"},
        selected=[],
        compile_results={},
        execution_results={},
        gate_results={},
    )

    health = build_pipeline_health({
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
    })

    assert effective_execution_status({"obligation_attempt_ledger": ledger}) == "not_executed"
    assert health["status"] == "BLOCKED"
    assert health["empty_findings_means_no_bugs"] is False
    assert health["planning_gap_reason"] == "NO_OBLIGATIONS_SELECTED"


def test_all_terminal_obligations_blocked_reports_blocked_execution() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-BLOCKED", "campaign_id": "CMP-BLOCKED"},
        selected=[{"obligation_id": "obl-blocked"}],
        compile_results={
            "obl-blocked": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            }
        },
        execution_results={},
        gate_results={},
    )

    assert effective_execution_status({"obligation_attempt_ledger": ledger}) == "blocked"


def test_terminal_harness_failure_without_http_receipt_is_not_completed() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-FAILED", "campaign_id": "CMP-FAILED"},
        selected=[{"obligation_id": "obl-failed"}],
        compile_results={"obl-failed": {"status": "COMPILED"}},
        execution_results={
            "obl-failed": {
                "status": "HARNESS_FAILED",
                "reason_code": "LEGACY_EXECUTION_ERROR",
            }
        },
        gate_results={},
    )

    assert effective_execution_status({"obligation_attempt_ledger": ledger}) == "blocked"


def test_funnel_exposes_required_stage_receipt_metrics_and_does_not_mutate_formal_count() -> None:
    result = _result()
    before = deepcopy(result["formal_count_projection"])

    funnel = build_funnel(result)

    assert [stage["name"] for stage in funnel["stages"]] == REQUIRED_STAGES
    for stage in funnel["stages"]:
        assert set(("input", "success", "blocked", "failed", "elapsed_ms", "reason_counts")) <= set(stage)
        assert set(("p50", "p95")) <= set(stage["elapsed_ms"])
    compile_stage = next(
        stage for stage in funnel["stages"] if stage["name"] == "experiment_compile"
    )
    assert compile_stage["input"] == 2
    assert compile_stage["success"] == 1
    assert compile_stage["blocked"] == 1
    assert compile_stage["reason_counts"] == {"BLOCKED_MISSING_BINDING": 1}
    assert compile_stage["elapsed_ms"] == {"p50": 4, "p95": 8}
    assert compile_stage["dimensions"]["actor"] == {"actor-admin": 1}
    assert compile_stage["dimensions"]["round"] == {"1": 1, "2": 1}
    assert funnel["validated_bug_count"] == 0
    assert funnel["canonical_defect_ids"] == []
    assert result["formal_count_projection"] == before


def test_harness_failure_is_visible_and_empty_findings_never_mean_no_bugs() -> None:
    result = _result(harness_failed=True)

    health = build_pipeline_health(result)
    funnel = build_funnel(result)

    assert health["status"] == "DEGRADED"
    assert health["harness_failure_count"] == 1
    assert health["empty_findings_means_no_bugs"] is False
    assert funnel["pipeline_health"]["status"] == "DEGRADED"
    assert funnel["top_blocking_reasons"][0] == {
        "reason": "CLEANUP_COMPENSATION_FAILED",
        "count": 1,
    }
