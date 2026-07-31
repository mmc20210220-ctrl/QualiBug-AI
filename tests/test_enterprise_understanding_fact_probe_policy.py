from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.probe_policy import (
    probe_generation_block_reason,
)


def _ready_asset() -> dict:
    return {
        "scenario_planning_gate": {"scenario_planning_allowed": True},
        "scenario_ir_gate": {"entry_allowed": True},
        "binding_identity_gate": {"entry_allowed": True},
        "scenario_execution_contract_gate": {"entry_allowed": True},
        "runtime_plan_gate": {"entry_allowed": True},
        "runtime_materialization_gate": {"entry_allowed": True},
    }


def test_formal_fact_receipts_close_probe_admission() -> None:
    asset = _ready_asset()
    asset["semantic_lexicon_contract"] = {
        "status": "BLOCKED_COMPREHENSION_POLICY_INVALID",
        "entry_allowed": False,
    }
    assert probe_generation_block_reason(asset) == "SEMANTIC_LEXICON_CONTRACT_CLOSED"

    asset = _ready_asset()
    asset["semantic_lexicon_contract"] = {"status": "PASS", "entry_allowed": True}
    asset["structure_first_business_fact_compilation_receipt"] = {
        "status": "BLOCKED"
    }
    assert probe_generation_block_reason(asset) == (
        "STRUCTURE_FIRST_BUSINESS_FACT_COMPILATION_CLOSED"
    )

    asset = _ready_asset()
    asset["semantic_lexicon_contract"] = {"status": "PASS", "entry_allowed": True}
    asset["structure_first_business_fact_compilation_receipt"] = {"status": "PASS"}
    asset["enterprise_comprehension_gate"] = {
        "status": "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INCOMPLETE",
        "entry_allowed": False,
    }
    assert probe_generation_block_reason(asset) == "ENTERPRISE_COMPREHENSION_GATE_CLOSED"


@pytest.mark.parametrize(
    ("receipt_key", "expected_reason"),
    [
        (
            "explicit_fact_semantic_normalization_receipt",
            "EXPLICIT_FACT_SEMANTIC_NORMALIZATION_CLOSED",
        ),
        (
            "atomic_claim_fact_projection_receipt",
            "ATOMIC_CLAIM_FACT_PROJECTION_CLOSED",
        ),
        (
            "typed_fact_value_projection_receipt",
            "TYPED_FACT_VALUE_PROJECTION_CLOSED",
        ),
        (
            "typed_object_relation_projection_receipt",
            "TYPED_OBJECT_RELATION_PROJECTION_CLOSED",
        ),
        (
            "typed_business_fact_conflict_receipt",
            "TYPED_BUSINESS_FACT_CONFLICT_CLOSED",
        ),
    ],
)
def test_each_present_formal_fact_receipt_is_authoritative(
    receipt_key: str,
    expected_reason: str,
) -> None:
    asset = _ready_asset()
    asset[receipt_key] = {"status": "BLOCKED"}
    assert probe_generation_block_reason(asset) == expected_reason

    asset[receipt_key] = {"status": "PASS"}
    assert probe_generation_block_reason(asset) == ""


def test_legacy_gate_fixture_without_new_receipts_stays_compatible() -> None:
    assert probe_generation_block_reason(_ready_asset()) == ""
