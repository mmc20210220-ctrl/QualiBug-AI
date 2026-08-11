from __future__ import annotations

import pytest


def _blocked_base_oracle() -> dict:
    from ai_test_asset_center.contract_oracles import evaluate_contract_oracle

    return evaluate_contract_oracle(
        experiment={
            "experiment_id": "exp-posthoc",
            "obligation_id": "obl-posthoc",
            "campaign_id": "camp-posthoc",
            "execution_id": "exec-posthoc",
            "source_refs": [],
            "control_plan": [],
            "treatment_plan": [],
            "assertions": [],
            "observers": [],
        },
        evidence={"contract_evidence_receipts": []},
    )


def test_posthoc_annotation_does_not_bypass_tampered_sealed_oracle() -> None:
    from ai_test_asset_center.contract_oracles import (
        validate_contract_oracle_receipt,
    )

    base = _blocked_base_oracle()
    validate_contract_oracle_receipt(base)

    tampered = {
        **base,
        "obligation_id": "obl-tampered",
        "oracle_validity_gate": "NOT_APPLICABLE",
        "oracle_validity_receipt_id": "ovg_" + "a" * 24,
        "oracle_validity_reason_codes": [],
        "effect_observation_graph_receipt_id": "eog_test",
    }

    with pytest.raises(ValueError):
        validate_contract_oracle_receipt(tampered)


def test_posthoc_annotation_can_wrap_an_unchanged_valid_sealed_oracle() -> None:
    from ai_test_asset_center.contract_oracles import (
        validate_contract_oracle_receipt,
    )

    base = _blocked_base_oracle()
    annotated = {
        **base,
        "oracle_validity_gate": "NOT_APPLICABLE",
        "oracle_validity_receipt_id": "ovg_" + "b" * 24,
        "oracle_validity_reason_codes": [],
        "effect_observation_graph_receipt_id": "eog_test",
    }

    assert validate_contract_oracle_receipt(annotated) == annotated


def test_validity_demotion_restores_pre_gate_sealed_semantics() -> None:
    from ai_test_asset_center.contract_oracles import _restore_pre_gate_oracle

    base = {
        "receipt_id": "oracle_original",
        "status": "VIOLATION",
        "verdict": "customer_deliverable_defect_candidate",
        "customer_deliverable": False,
        "customer_deliverable_candidate": True,
        "assertions": [{"receipt_id": "assert_1"}],
    }
    demoted = {
        **base,
        "status": "INDETERMINATE",
        "verdict": "indeterminate",
        "customer_deliverable_candidate": False,
        "oracle_validity_gate": "INDETERMINATE",
        "oracle_validity_receipt_id": "ovg_" + "c" * 24,
        "oracle_validity_reason_codes": ["MISSING_BEFORE_STATE"],
        "pre_validity_oracle_verdict": {
            "status": base["status"],
            "verdict": base["verdict"],
            "receipt_id": base["receipt_id"],
            "customer_deliverable_candidate": True,
        },
        "effect_observation_graph_receipt_id": "eog_test",
        "effect_observation_graph_status": "INCOMPLETE",
    }

    assert _restore_pre_gate_oracle(demoted) == base


def test_gate_shaped_fields_cannot_upgrade_a_demotion() -> None:
    from ai_test_asset_center.contract_oracles import _validate_posthoc_semantics

    forged = {
        "receipt_id": "oracle_original",
        "status": "VIOLATION",
        "verdict": "customer_deliverable_defect_candidate",
        "customer_deliverable_candidate": True,
        "oracle_validity_gate": "INDETERMINATE",
        "oracle_validity_receipt_id": "ovg_" + "d" * 24,
        "oracle_validity_reason_codes": ["MISSING_BEFORE_STATE"],
        "pre_validity_oracle_verdict": {
            "status": "VIOLATION",
            "verdict": "customer_deliverable_defect_candidate",
            "receipt_id": "oracle_original",
            "customer_deliverable_candidate": True,
        },
    }

    with pytest.raises(ValueError, match="validity_demotion_invalid"):
        _validate_posthoc_semantics(forged)
