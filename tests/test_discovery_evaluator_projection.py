from __future__ import annotations

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    MainlineContractError,
    build_mainline_run_contract,
)
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


def _contract(mode: str = "shadow") -> dict:
    return build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id=f"run-{mode}",
        campaign_id=f"campaign-{mode}",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode=mode,
    )


def _shadow(contract: dict, finding_id: str, status: str) -> dict:
    return {
        "finding_id": finding_id,
        "id": finding_id,
        "finding_class": "shadow",
        "semantic_delivery_gate_status": status,
        "mainline_run": {
            "contract_fingerprint": contract["contract_fingerprint"],
        },
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "bug_status": "reproduced",
        "expected": "denied",
        "actual": "allowed",
        "timestamp": "2026-07-12T00:00:00Z",
        "evidence_quality": {
            "level": "validated",
            "score": 95,
            "can_reproduce": True,
        },
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


def test_private_evaluator_projection_scores_only_semantic_deliverable_shadow() -> None:
    from ai_test_asset_center.discovery_evaluator_projection import (
        build_evaluator_only_projection,
    )

    contract = _contract()
    formal, ledger = build_formal_evaluation_scope(
        [{"finding_id": "finding-deliverable"}],
        run_id=contract["run_id"],
        campaign_id=contract["campaign_id"],
        target_id=contract["target_id"],
        environment_id=contract["environment_id"],
        policy_version=contract["policy_version"],
        evaluation_mode=contract["evaluation_mode"],
    )
    deliverable = formal[0]
    scope = build_formal_scope_contract(
        mainline_run=contract,
        findings=formal,
        obligation_attempt_ledger=ledger,
    )
    rejected = _shadow(contract, "finding-rejected", "REJECTED")
    scan_result = {
        "mainline_run": contract,
        "findings": [],
        "candidate_findings": [],
        "shadow_findings": [deliverable, rejected],
        "obligation_attempt_ledger": ledger,
        "evaluator_canonical_findings": list(
            scope["formal_count_projection"][
                "canonical_representative_findings"
            ]
        ),
        **scope,
    }

    projection = build_evaluator_only_projection(scan_result)

    assert projection["authority_scope"] == "private_evaluator"
    assert [row["finding_id"] for row in projection["findings"]] == [
        "finding-deliverable"
    ]
    assert [row["finding_id"] for row in projection["candidates"]] == [
        "finding-rejected"
    ]
    assert projection["source_shadow_count"] == 2
    assert scan_result["findings"] == []


def test_private_evaluator_projection_rejects_operational_product_authority() -> None:
    from ai_test_asset_center.discovery_evaluator_projection import (
        build_evaluator_only_projection,
    )

    with pytest.raises(
        MainlineContractError,
        match="private_evaluator_observation_not_allowed",
    ):
        build_evaluator_only_projection({
            "mainline_run": _contract("operational"),
            "shadow_findings": [],
        })


def test_private_evaluator_projection_rejects_shadow_authority_mismatch() -> None:
    from ai_test_asset_center.discovery_evaluator_projection import (
        build_evaluator_only_projection,
    )

    contract = _contract()
    formal, ledger = build_formal_evaluation_scope(
        [{"finding_id": "finding-deliverable"}],
        run_id=contract["run_id"],
        campaign_id=contract["campaign_id"],
        target_id=contract["target_id"],
        environment_id=contract["environment_id"],
        policy_version=contract["policy_version"],
        evaluation_mode=contract["evaluation_mode"],
    )
    finding = _shadow(contract, "finding-1", "REJECTED")
    finding["mainline_run"]["contract_fingerprint"] = "wrong"
    scope = build_formal_scope_contract(
        mainline_run=contract,
        findings=formal,
        obligation_attempt_ledger=ledger,
    )

    with pytest.raises(
        MainlineContractError,
        match="evaluator_shadow_authority_fingerprint_mismatch:finding-1",
    ):
        build_evaluator_only_projection({
            "mainline_run": contract,
            "shadow_findings": [formal[0], finding],
            "obligation_attempt_ledger": ledger,
            "evaluator_canonical_findings": list(
                scope["formal_count_projection"][
                    "canonical_representative_findings"
                ]
            ),
            **scope,
        })
