from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import runtime_materialization_governance as materialization_governance
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_plan import build_runtime_plans_v1
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import stable_id


def _evidence() -> list[dict]:
    return [{"source_id": "prd", "source_locator": "prd.md#ship", "quote": "发货规则"}]


def _contract() -> dict:
    return {
        "contract_id": "contract:ship",
        "scenario_ref": "scenario:ship",
        "behavior_ref": "behavior:ship",
        "implementation_binding_ref": "binding:ship",
        "scenario_type": "POSITIVE",
        "status": "REQUIREMENTS_READY",
        "action_contract": {
            "interface_id": "api:ship",
            "method": "POST",
            "path": "/orders/ship",
            "operation_id": "shipOrder",
            "authoritative": True,
        },
        "request_contract": {
            "path_parameter_requirements": [],
            "request_field_requirements": [],
        },
        "credential_requirements": [],
        "test_data_requirements": [],
        "oracle_plan": {
            "condition_observers": [
                {
                    "slot_ref": "predicate:approved",
                    "status": "BOUND",
                    "bindings": [
                        {
                            "binding_kind": "DATABASE_FIELD",
                            "table_id": "table:orders",
                            "table": "orders",
                            "field": "status",
                        }
                    ],
                }
            ],
            "outcome_assertion_requirements": [
                {
                    "outcome_ref": "outcome:permission",
                    "outcome_type": "PERMISSION_DECISION",
                    "expected_decision": "ALLOW",
                    "observer_binding_complete": True,
                    "observer_requirements": [
                        {
                            "outcome_ref": "outcome:permission",
                            "status": "BOUND",
                            "bindings": [
                                {
                                    "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                                    "interface_id": "api:ship",
                                }
                            ],
                        }
                    ],
                },
                {
                    "outcome_ref": "outcome:state",
                    "outcome_type": "STATE_TRANSITION",
                    "from_value": "APPROVED",
                    "to_value": "SHIPPED",
                    "observer_binding_complete": True,
                    "observer_requirements": [
                        {
                            "outcome_ref": "outcome:state",
                            "status": "BOUND",
                            "bindings": [
                                {
                                    "binding_kind": "DATABASE_FIELD",
                                    "table_id": "table:orders",
                                    "table": "orders",
                                    "field": "status",
                                }
                            ],
                        }
                    ],
                },
            ],
        },
        "snapshot_plan": {
            "before_snapshot_required": True,
            "after_snapshot_required": True,
            "snapshot_consistency_scope": "SAME_SCENARIO_ENTITY_IDENTITY",
        },
        "cleanup_requirements": {
            "write_action": True,
            "cleanup_required": True,
            "strategy_requirement": "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED",
            "source_backed_compensation_candidates": [],
        },
        "evidence": _evidence(),
    }


def _asset() -> dict:
    return {
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "scenario_execution_contracts": [_contract()],
        "interfaces": [
            {
                "interface_id": "api:ship",
                "method": "POST",
                "path": "/orders/ship",
                "operation_id": "shipOrder",
                "response_contracts": [{"status": "200"}],
            }
        ],
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def test_runtime_plan_separates_predicate_and_mandatory_outcome_identity() -> None:
    plans, unknowns, gate = build_runtime_plans_v1(_asset(), {})

    assert unknowns == []
    assert gate["status"] == "PASS"
    plan = plans[0]
    oracle = plan["oracle_query_templates"]
    assert oracle["mandatory_outcome_refs"] == [
        "outcome:permission",
        "outcome:state",
    ]
    assert oracle["covered_mandatory_outcome_refs"] == oracle["mandatory_outcome_refs"]
    condition = next(
        row for row in oracle["templates"] if row["semantic_role"] == "CONDITION_GUARD"
    )
    assert condition["predicate_ref"] == "predicate:approved"
    assert condition["outcome_ref"] is None
    outcomes = [
        row for row in oracle["templates"] if row["semantic_role"] == "MANDATORY_OUTCOME"
    ]
    assert {row["outcome_ref"] for row in outcomes} == {
        "outcome:permission",
        "outcome:state",
    }
    assert len(outcomes) == 2
    assert plan["snapshot_template"]["mandatory_outcome_refs"] == [
        "outcome:permission",
        "outcome:state",
    ]


def _seed_materialization(asset: dict, *, omit_outcome: str = "") -> None:
    plan = asset["runtime_plans"][0]
    materialization_id = "materialization:ship"
    templates = [
        row
        for row in plan["oracle_query_templates"]["templates"]
        if row.get("outcome_ref") != omit_outcome
    ]
    asset["runtime_materializations"] = [
        {
            "materialization_id": materialization_id,
            "runtime_plan_ref": plan["plan_id"],
            "status": "DRAFT_READY",
            "assertion_drafts": [
                {
                    "draft_id": stable_id(
                        "assertion_draft", materialization_id, row["template_id"]
                    ),
                    "template_ref": row["template_id"],
                    "assertion_executable": False,
                }
                for row in templates
            ],
            "request_draft": {},
        }
    ]
    asset["runtime_materialization_unknowns"] = []
    asset["runtime_materialization_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "metrics": {},
    }


def test_materialization_drafts_preserve_outcome_ref_and_fail_closed(monkeypatch) -> None:
    asset = _asset()
    plans, _, runtime_gate = build_runtime_plans_v1(asset, {})
    asset["runtime_plans"] = plans
    asset["runtime_plan_gate"] = runtime_gate
    _seed_materialization(asset)
    monkeypatch.setattr(
        materialization_governance._core,
        "project_governed_runtime_materializations_to_asset",
        lambda current_asset, _model: current_asset,
    )

    materialization_governance.project_governed_runtime_materializations_to_asset(
        asset, {}
    )
    materialization = asset["runtime_materializations"][0]
    outcome_drafts = [
        row
        for row in materialization["assertion_drafts"]
        if row["semantic_role"] == "MANDATORY_OUTCOME"
    ]
    assert {row["outcome_ref"] for row in outcome_drafts} == {
        "outcome:permission",
        "outcome:state",
    }
    assert materialization["outcome_assertion_identity_complete"] is True
    assert asset["runtime_materialization_gate"]["status"] == "PASS"

    broken = deepcopy(_asset())
    plans, _, runtime_gate = build_runtime_plans_v1(broken, {})
    broken["runtime_plans"] = plans
    broken["runtime_plan_gate"] = runtime_gate
    _seed_materialization(broken, omit_outcome="outcome:state")
    materialization_governance.project_governed_runtime_materializations_to_asset(
        broken, {}
    )
    assert broken["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    assert any(
        row["reason_code"]
        == "RUNTIME_MATERIALIZATION_OUTCOME_ASSERTION_DRAFT_UNRESOLVED"
        and row["outcome_ref"] == "outcome:state"
        for row in broken["runtime_materialization_unknowns"]
    )
