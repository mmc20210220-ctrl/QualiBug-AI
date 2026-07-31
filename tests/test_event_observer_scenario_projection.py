from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_observer_evidence_projection import (
    project_event_observer_evidence,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_observer_scenario_projection import (
    project_event_requirements_to_execution_contracts,
    project_event_requirements_to_scenarios,
)


BINDING_ID = "binding:create-order"
SCENARIO_ID = "scenario:create-order"
OBSERVER_REF = "observer-binding:order-created"
EVENT_CONTRACT_REF = "event-contract:order-created"


def _observer() -> dict:
    return {
        "observer_binding_id": OBSERVER_REF,
        "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
        "event_contract_ref": EVENT_CONTRACT_REF,
        "interface_id": "api:POST:/orders",
        "actor_ref": "actor:admin",
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 3000,
        "correlation_source": {
            "location": "treatment_response",
            "path": "$.id",
        },
        "assertion_kind": "event_delivery_contract",
        "risk_family": "event_delivery",
        "event_contract": {
            "contract_id": EVENT_CONTRACT_REF,
            "expected_event_type": "OrderCreated",
            "expected_min_count": 1,
            "expected_max_count": 1,
            "observation_window_ms": 3000,
        },
        "evidence": [
            {
                "source_id": "event-doc",
                "locator": "events.order-created",
                "quote_hash": "sha256:event-contract",
            }
        ],
    }


def _binding() -> dict:
    observer = _observer()
    return {
        "binding_id": BINDING_ID,
        "evidence": [],
        "formal_event_observer_bindings": [observer],
        "condition_observer_bindings": [],
        "effect_observer_bindings": [
            {
                "slot_ref": "event-slot:create-order",
                "purpose": "EFFECT_OBSERVER",
                "status": "BOUND",
                "bindings": [observer],
            }
        ],
    }


def test_event_evidence_is_normalized_and_merged_into_binding() -> None:
    binding = project_event_observer_evidence([_binding()])[0]

    evidence = binding["evidence"]
    assert evidence == [
        {
            "source_id": "event-doc",
            "source_locator": "events.order-created",
            "quote_hash": "sha256:event-contract",
            "asset_ref": EVENT_CONTRACT_REF,
            "derivation": "exact_formal_event_contract_identity",
        }
    ]
    observer = binding["formal_event_observer_bindings"][0]
    assert observer["evidence"] == evidence
    assert observer["evidence_contract"] == "ENTERPRISE_SOURCE_ID_AND_LOCATOR"
    assert binding["formal_event_observer_evidence_count"] == 1


def test_event_requirements_are_explicit_in_scenario_and_contract() -> None:
    binding = project_event_observer_evidence([_binding()])[0]
    scenario = {
        "scenario_id": SCENARIO_ID,
        "implementation_binding_ref": BINDING_ID,
        "coverage_dimensions": ["POSITIVE"],
        "expected_outcome": {
            "permission_decision": "ALLOW",
            "concrete_assertion_compiled": False,
        },
        "evidence": [],
    }
    asset = {
        "scenario_ir": [scenario],
        "scenario_execution_contracts": [
            {
                "contract_id": "execution-contract:create-order",
                "scenario_ref": SCENARIO_ID,
                "oracle_plan": {},
                "snapshot_plan": {},
            }
        ],
    }
    model = {
        "behavior_implementation_bindings": [binding],
        "scenario_ir": [dict(scenario)],
    }

    project_event_requirements_to_scenarios(asset, model)
    projected = asset["scenario_ir"][0]
    assert "EVENT_DELIVERY" in projected["coverage_dimensions"]
    requirement = projected["expected_outcome"]["event_effect_requirements"][0]
    assert requirement["observer_binding_ref"] == OBSERVER_REF
    assert requirement["event_contract_ref"] == EVENT_CONTRACT_REF
    assert requirement["expected_event_type"] == "OrderCreated"
    assert requirement["expected_min_count"] == 1
    assert requirement["expected_max_count"] == 1
    assert requirement["observation_window_ms"] == 3000
    assert requirement["source_declared"] is True
    assert projected["expected_outcome"]["event_oracle_level"] == (
        "SOURCE_DECLARED_EVENT_DELIVERY_CONTRACT"
    )
    assert projected["evidence"] == binding["evidence"]

    project_event_requirements_to_execution_contracts(asset, model)
    contract = asset["scenario_execution_contracts"][0]
    assert contract["oracle_plan"]["event_effect_requirements"] == [requirement]
    assert contract["oracle_plan"]["formal_event_observer_required"] is True
    assert contract["snapshot_plan"]["after_snapshot_required"] is True
    assert contract["snapshot_plan"]["formal_event_observation_required"] is True
    assert contract["formal_event_effect_requirements"] == [requirement]
