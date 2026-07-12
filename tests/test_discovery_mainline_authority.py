from __future__ import annotations

import importlib

import pytest


def _contract_module():
    return importlib.import_module("ai_test_asset_center.discovery_mainline_contract")


def _contract(**overrides):
    values = {
        "mainline_authority": "experiment_candidate",
        "run_id": "RUN-1",
        "campaign_id": "CMP-1",
        "target_id": "TARGET-1",
        "environment_id": "ENV-1",
        "policy_version": "v2",
        "evaluation_mode": "replay",
    }
    values.update(overrides)
    return _contract_module().build_mainline_run_contract(**values)


def test_mainline_contract_requires_explicit_authority() -> None:
    module = _contract_module()

    with pytest.raises(module.MainlineContractError, match="mainline_authority_missing"):
        _contract(mainline_authority="")


def test_mainline_contract_rejects_unknown_authority() -> None:
    module = _contract_module()

    with pytest.raises(module.MainlineContractError, match="mainline_authority_invalid"):
        _contract(mainline_authority="automatic_fallback")


def test_shadow_contract_separates_product_and_private_evaluator_scopes() -> None:
    contract = _contract(evaluation_mode="shadow")

    assert contract["customer_outputs_published"] is False
    assert contract["product_evaluation_submission_published"] is False
    assert contract["private_evaluator_observation_allowed"] is True


def test_operational_contract_does_not_authorize_private_evaluator_observation() -> None:
    contract = _contract(evaluation_mode="operational")

    assert contract["customer_outputs_published"] is True
    assert contract["product_evaluation_submission_published"] is True
    assert contract["private_evaluator_observation_allowed"] is False


def test_replay_contract_is_private_and_does_not_publish_customer_output() -> None:
    contract = _contract(evaluation_mode="replay")

    assert contract["customer_outputs_published"] is False
    assert contract["product_evaluation_submission_published"] is False
    assert contract["private_evaluator_observation_allowed"] is True


def test_mainline_contract_fingerprint_detects_tampering() -> None:
    module = _contract_module()
    contract = _contract()
    contract["mainline_authority"] = "legacy_champion"

    with pytest.raises(module.MainlineContractError, match="mainline_contract_fingerprint_mismatch"):
        module.validate_mainline_run_contract(contract)


def test_product_run_authority_comes_from_execution_policy() -> None:
    from ai_test_asset_center.__main__ import _bind_discovery_mainline_identity
    from ai_test_asset_center.policy_registry import ExecutionPolicy, StrategyBundle
    from ai_test_asset_center.policy_wiring import policy_strategy_override

    strategy = StrategyBundle(
        execution=ExecutionPolicy(mainline_authority="legacy_champion")
    )
    with policy_strategy_override(strategy):
        context = _bind_discovery_mainline_identity(
            project="PROJECT-1",
            context={
                "scope_id": "SCOPE-1",
                "environment_ref": "ENV-1",
                "source_manifest": {"source_hash": "a" * 64},
            },
            started=1.0,
        )

    assert context["mainline_authority"] == "legacy_champion"


def test_product_run_rejects_context_authority_that_bypasses_policy() -> None:
    from ai_test_asset_center.__main__ import _bind_discovery_mainline_identity
    from ai_test_asset_center.policy_registry import ExecutionPolicy, StrategyBundle
    from ai_test_asset_center.policy_wiring import policy_strategy_override

    strategy = StrategyBundle(
        execution=ExecutionPolicy(mainline_authority="legacy_champion")
    )
    with policy_strategy_override(strategy):
        with pytest.raises(RuntimeError, match="mainline_authority_policy_mismatch"):
            _bind_discovery_mainline_identity(
                project="PROJECT-1",
                context={
                    "scope_id": "SCOPE-1",
                    "environment_ref": "ENV-1",
                    "source_manifest": {"source_hash": "a" * 64},
                    "mainline_authority": "experiment_candidate",
                },
                started=1.0,
            )


def _deliverable_finding(contract: dict) -> dict:
    return {
        "id": "FINDING-1",
        "finding_id": "FINDING-1",
        "candidate_id": "CANDIDATE-1",
        "slice_id": "SLICE-1",
        "obligation_id": "OBLIGATION-1",
        "experiment_id": "EXPERIMENT-1",
        "execution_id": "EXECUTION-1",
        "evidence_id": "EVIDENCE-1",
        "mainline_run": {"contract_fingerprint": contract["contract_fingerprint"]},
        "title": "Observed source-derived invariant violation",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "bug_status": "reproduced",
        "expected": "denied",
        "actual": "allowed",
        "timestamp": "2026-07-12T00:00:00Z",
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-12T00:00:00Z",
            "request_raw": {"method": "GET", "path": "/resources/1"},
            "response_raw": {"status_code": 200, "body": {"visible": True}},
        },
        "reproduction": {
            "method": "GET",
            "path": "/resources/1",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200},
        },
    }


def test_shadow_findings_cannot_enter_formal_projection() -> None:
    from ai_test_asset_center.discovery_quality_projection import (
        attach_quality_projection_to_scan_result,
    )

    contract = _contract(evaluation_mode="shadow")
    projected = attach_quality_projection_to_scan_result({
        "mainline_run": contract,
        "findings": [_deliverable_finding(contract)],
    })

    assert projected["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert projected["formal_count_projection"]["formal_finding_ids"] == []
    assert [row["finding_id"] for row in projected["finding_classification"]["shadow"]] == [
        "FINDING-1"
    ]
    assert projected["external_evaluation"]["commercial_promotion_evidence"] is False


def test_authoritative_finding_requires_matching_run_fingerprint() -> None:
    from ai_test_asset_center.discovery_quality_projection import (
        attach_quality_projection_to_scan_result,
    )

    contract = _contract(evaluation_mode="operational")
    missing = _deliverable_finding(contract)
    missing.pop("mainline_run")
    with pytest.raises(
        _contract_module().MainlineContractError,
        match="finding_authority_fingerprint_missing:FINDING-1",
    ):
        attach_quality_projection_to_scan_result({
            "mainline_run": contract,
            "findings": [missing],
        })

    mismatched = _deliverable_finding(contract)
    mismatched["mainline_run"] = {"contract_fingerprint": "wrong"}
    with pytest.raises(
        _contract_module().MainlineContractError,
        match="finding_authority_fingerprint_mismatch:FINDING-1",
    ):
        attach_quality_projection_to_scan_result({
            "mainline_run": contract,
            "findings": [mismatched],
        })


def test_formal_ids_match_gate_submission_trace_and_api() -> None:
    from ai_test_asset_center.discovery_quality_projection import (
        build_formal_id_consistency,
    )

    receipt = build_formal_id_consistency(
        delivery_gate_ids=["FINDING-1"],
        formal_projection_ids=["FINDING-1"],
        evaluator_submission_ids=["FINDING-1"],
        trace_ledger_ids=["FINDING-1"],
        product_projection_ids=["FINDING-1"],
    )
    assert receipt["consistent"] is True
    assert receipt["status"] == "OK"

    mismatch = build_formal_id_consistency(
        delivery_gate_ids=["FINDING-1"],
        formal_projection_ids=["FINDING-1"],
        evaluator_submission_ids=[],
        trace_ledger_ids=["FINDING-1"],
        product_projection_ids=["FINDING-1"],
    )
    assert mismatch["consistent"] is False
    assert mismatch["status"] == "PIPELINE_DEGRADED_COUNT_MISMATCH"
