from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_plan_governance import (
    project_governed_runtime_plans_to_asset,
)


def _contract() -> dict:
    return {
        "contract_id": "execution-contract:ship-body",
        "scenario_ref": "scenario:ship-body",
        "behavior_ref": "behavior:ship-body",
        "implementation_binding_ref": "binding:ship-body",
        "scenario_type": "POSITIVE",
        "status": "REQUIREMENTS_READY",
        "action_contract": {
            "interface_id": "api:POST:/ship",
            "method": "POST",
            "path": "/ship",
            "operation_id": "shipOrder",
            "authoritative": True,
        },
        "request_contract": {
            "path_parameter_requirements": [],
            "request_field_requirements": [
                {
                    "slot_ref": "condition:status",
                    "field": "status",
                    "field_candidate": "status",
                    "operator": "EQUALS",
                    "semantic_value_requirement": {
                        "raw": "approved",
                        "value_type": "TEXT",
                        "runtime_value_materialized": False,
                    },
                    "required": True,
                }
            ],
        },
        "credential_requirements": [],
        "test_data_requirements": [],
        "oracle_plan": {
            "permission_decision_requirement": "ALLOW",
            "condition_observers": [],
            "effect_observers": [],
            "response_observers": [
                {
                    "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                    "interface_id": "api:POST:/ship",
                    "authoritative": True,
                }
            ],
        },
        "snapshot_plan": {
            "before_snapshot_required": False,
            "after_snapshot_required": True,
        },
        "cleanup_requirements": {
            "write_action": True,
            "cleanup_required": True,
            "strategy_requirement": "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED",
            "source_backed_compensation_candidates": [],
        },
        "evidence": [
            {
                "source_id": "source:policy",
                "source_locator": "policy.pdf#page=2",
                "quote": "已审核订单允许发货",
                "derivation": "source_span",
            }
        ],
    }


def _asset(body_fields: list[dict]) -> dict:
    return {
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
            "execution_allowed": False,
        },
        "scenario_execution_contracts": [_contract()],
        "interfaces": [
            {
                "interface_id": "api:POST:/ship",
                "method": "POST",
                "path": "/ship",
                "operation_id": "shipOrder",
                "request_body_fields": body_fields,
                "request_body_media_types": sorted(
                    {
                        str(row.get("media_type") or "")
                        for row in body_fields
                        if row.get("media_type")
                    }
                ),
                "response_contracts": [{"status": "200"}],
            }
        ],
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def _field(media_type: str, schema_type: str = "STRING") -> dict:
    return {
        "field": "status",
        "name": "status",
        "location": "BODY",
        "required": True,
        "schema_type": schema_type,
        "media_type": media_type,
        "source": "OPENAPI_SCHEMA_PROPERTY",
    }


def test_equivalent_media_types_remain_runtime_selection_requirement() -> None:
    asset = _asset(
        [
            _field("application/json"),
            _field("application/xml"),
        ]
    )
    model = {"source_summary": {}, "metrics": {}}

    project_governed_runtime_plans_to_asset(asset, model)

    assert asset["runtime_plan_gate"]["status"] == "PASS"
    plan = asset["runtime_plans"][0]
    body = plan["request_template"]["body_fields"][0]
    assert "media_type" not in body
    assert body["media_type_candidates"] == ["application/json", "application/xml"]
    assert body["media_type_resolution_status"] == "RUNTIME_MEDIA_TYPE_SELECTION_REQUIRED"
    requirement = next(
        row
        for row in asset["runtime_plan_unknowns"]
        if row["kind"] == "RUNTIME_PLAN_REQUEST_MEDIA_TYPE_SELECTION_REQUIRED"
    )
    assert requirement["blocks_runtime_plan"] is False
    assert plan["network_calls_allowed"] is False
    assert plan["cleanup_actions_executable"] is False


def test_conflicting_schema_variants_block_runtime_plan() -> None:
    asset = _asset(
        [
            _field("application/json", "STRING"),
            _field("application/xml", "INTEGER"),
        ]
    )
    model = {"source_summary": {}, "metrics": {}}

    project_governed_runtime_plans_to_asset(asset, model)

    assert asset["runtime_plan_gate"]["status"] == "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    assert asset["runtime_plans"][0]["status"] == "INCOMPLETE"
    assert any(
        row["kind"] == "RUNTIME_PLAN_REQUEST_FIELD_CONTRACT_CONFLICT"
        and row["blocks_runtime_plan"] is True
        for row in asset["runtime_plan_unknowns"]
    )
    assert all(
        row["status"] == "candidate"
        for row in asset["runtime_plan_relationships"]
    )
    gap = next(
        row for row in asset["coverage_gaps"] if row["kind"] == "RUNTIME_PLAN_INCOMPLETE"
    )
    assert gap["gap_type"] == "runtime_plan_request_contract_conflict"


def test_non_variant_location_gap_keeps_original_diagnosis() -> None:
    asset = _asset([])
    model = {"source_summary": {}, "metrics": {}}

    project_governed_runtime_plans_to_asset(asset, model)

    assert asset["runtime_plan_gate"]["status"] == "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    gap = next(
        row for row in asset["coverage_gaps"] if row["kind"] == "RUNTIME_PLAN_INCOMPLETE"
    )
    assert gap["gap_type"] == "runtime_plan_template_not_closed"
    assert "source-declared request locations" in gap["operator_action"]
