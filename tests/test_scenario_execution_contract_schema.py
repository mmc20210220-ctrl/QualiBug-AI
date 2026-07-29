from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA,
    empty_model,
    validate_model_shape,
)


def test_empty_model_includes_scenario_execution_contract_containers() -> None:
    model = empty_model()

    assert model["scenario_execution_contracts"] == []
    assert model["scenario_execution_contract_unknowns"] == []
    assert model["scenario_execution_contract_evidence_index"] == []
    assert model["scenario_execution_contract_relationships"] == []
    assert model["scenario_execution_contract_gate"] == {
        "schema": SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA,
        "status": "NOT_BUILT",
        "entry_allowed": False,
        "execution_contract_ready": False,
        "execution_allowed": False,
        "metrics": {},
    }


def test_persisted_pre_execution_contract_model_remains_shape_compatible() -> None:
    model = empty_model()
    for key in (
        "scenario_execution_contracts",
        "scenario_execution_contract_unknowns",
        "scenario_execution_contract_evidence_index",
        "scenario_execution_contract_relationships",
        "scenario_execution_contract_gate",
    ):
        model.pop(key)

    assert validate_model_shape(model) == []
