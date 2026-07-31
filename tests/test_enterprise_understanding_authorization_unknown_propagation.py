"""Explicit UNKNOWN authorization must remain unresolved through Probe admission."""
from __future__ import annotations

import hashlib

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir_logic_gate import (
    build_business_behavior_ir_v1,
    mandatory_outcomes,
    outcome_contracts_complete,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_governance import (
    _behavior_semantic_ready,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.probe_policy import (
    build_gated_probes,
    probe_generation_block_reason,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir import (
    build_scenario_ir_v1,
)


def _unknown_authorization_fact() -> dict:
    statement = "仓库员允许执行受控登记，但资料未明确最终授权结论"
    quote_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    return {
        "fact_id": f"fact:{quote_hash[:16]}",
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {
            "actor_refs": ["仓库员"],
            "entity_refs": ["入库单"],
        },
        "object": {"entity_refs": ["入库单"]},
        "action": {"canonical": "登记", "raw": "登记"},
        "conditions": [],
        "condition_combinator": "",
        "modality": "MAY",
        "polarity": "POSITIVE",
        "scope": {"organization": "本仓库"},
        "authorization_semantics": {
            "decision": "UNKNOWN",
            "source_backed": True,
        },
        "postconditions": [],
        "data_effects": [
            {
                "statement": "形成入库记录",
                "action": "创建",
                "object": "入库单",
            }
        ],
        "source_spans": [
            {
                "source_id": "src:authorization-unknown-propagation",
                "locator": "line:1",
                "quote": statement,
                "quote_hash": quote_hash,
            }
        ],
    }


def _canonical_unknown_behavior() -> dict:
    _rows, behaviors, conflicts, unknowns, gate = build_business_behavior_ir_v1(
        {}, [_unknown_authorization_fact()], []
    )
    assert conflicts == []
    assert len(behaviors) == 1
    assert unknowns
    assert gate["entry_allowed"] is False
    return behaviors[0]


def _adversarial_ready_binding(behavior: dict) -> dict:
    outcomes = mandatory_outcomes(behavior)
    return {
        "binding_id": "binding:forced-ready",
        "behavior_ref": behavior["behavior_id"],
        "scenario_planning_ready": True,
        "primary_api_interface_ref": "iface:register",
        "api_operation_bindings": [
            {
                "interface_id": "iface:register",
                "status": "BOUND",
                "authoritative": True,
                "method": "POST",
                "path": "/receipts",
                "operation_id": "registerReceipt",
                "derivation": "test_source_backed_interface",
            }
        ],
        "condition_observer_bindings": [],
        "outcome_observer_bindings": [
            {
                "outcome_ref": row["outcome_id"],
                "outcome_type": row["outcome_type"],
                "status": "BOUND",
                "binding_kind": "ADVERSARIAL_TEST_OBSERVER",
            }
            for row in outcomes
        ],
        "effect_observer_bindings": [],
        "response_observer_bindings": [
            {
                "status": "BOUND_CHANNEL_ONLY",
                "authoritative": True,
            }
        ],
        "evidence": [],
    }


def test_unknown_permission_outcome_is_never_confirmed() -> None:
    behavior = _canonical_unknown_behavior()
    permission = next(
        row
        for row in mandatory_outcomes(behavior)
        if row["outcome_type"] == "PERMISSION_DECISION"
    )

    assert permission["expected_decision"] == "UNKNOWN"
    assert permission["declared_decision"] == "UNKNOWN"
    assert permission["authorization_semantic_kind"] == "AUTHORIZATION"
    assert permission["status"] == "UNRESOLVED"
    assert permission["reason_code"] == "FACT_AUTHORIZATION_DECISION_UNRESOLVED"
    assert outcome_contracts_complete(behavior) is False
    assert behavior["status"] == "INCOMPLETE"
    assert behavior["formal_business_rule"] is False
    assert "BEHAVIOR_AUTHORIZATION_DECISION_UNRESOLVED" in behavior["unresolved_semantics"]
    assert "BEHAVIOR_MANDATORY_OUTCOME_UNRESOLVED" in behavior["unresolved_semantics"]
    assert _behavior_semantic_ready(behavior) is False


def test_unknown_authorization_cannot_become_positive_scenario_or_probe() -> None:
    behavior = _canonical_unknown_behavior()
    binding = _adversarial_ready_binding(behavior)
    asset = {
        "scenario_planning_gate": {
            "status": "PASS",
            "scenario_planning_allowed": True,
            "entry_allowed": True,
        }
    }
    scenarios, unknowns, gate = build_scenario_ir_v1(
        asset,
        {
            "business_behaviors": [behavior],
            "behavior_implementation_bindings": [binding],
        },
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario["scenario_type"] == "INCOMPLETE_AUTHORIZATION"
    assert scenario["coverage_dimensions"] == ["AUTHORIZATION_UNRESOLVED"]
    assert scenario["status"] == "INCOMPLETE"
    assert scenario["formal_scenario_ir"] is False
    assert scenario["candidate_only"] is True
    assert "SCENARIO_SOURCE_BEHAVIOR_NOT_READY" in scenario["unresolved_semantics"]
    assert "SCENARIO_OUTCOME_CONTRACTS_UNRESOLVED" in scenario["unresolved_semantics"]
    assert unknowns
    assert gate["status"] == "BLOCKED_SCENARIO_IR_INCOMPLETE"
    assert gate["entry_allowed"] is False
    assert gate["metrics"]["positive_scenario_count"] == 0
    assert gate["metrics"]["unauthorized_scenario_count"] == 0
    assert gate["metrics"]["incomplete_authorization_scenario_count"] == 1

    probe_asset = {
        "scenario_planning_gate": asset["scenario_planning_gate"],
        "scenario_ir_gate": gate,
        "binding_identity_gate": {"entry_allowed": True},
        "scenario_execution_contract_gate": {"entry_allowed": True},
        "runtime_plan_gate": {"entry_allowed": True},
        "runtime_materialization_gate": {"entry_allowed": True},
    }
    compiler_called = False

    def compiler(_asset: dict, _limit: int) -> list[dict]:
        nonlocal compiler_called
        compiler_called = True
        return [{"probe_id": "must-not-exist"}]

    assert probe_generation_block_reason(probe_asset) == "SCENARIO_IR_GATE_CLOSED"
    assert build_gated_probes(probe_asset, compiler=compiler) == []
    assert compiler_called is False
