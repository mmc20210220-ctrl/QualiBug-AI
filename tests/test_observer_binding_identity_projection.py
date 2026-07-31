from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.binding_identity_projection import (
    project_binding_identities_to_materializations,
    project_binding_identities_to_runtime_plans,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.observer_binding_identity_projection import (
    project_observer_identities_to_materializations,
    project_observer_identities_to_runtime_plans,
)


BINDING_ID = "binding:create-order"
PLAN_ID = "runtime-plan:create-order"
MATERIALIZATION_ID = "materialization:create-order"
ACTION_REF = "action-surface:create-order"
EVENT_REF = "observer-binding:event"
DB_REF = "observer-binding:database"
DB_CONTRACT_REF = "database-observer:orders"


def _base_asset() -> tuple[dict, dict]:
    plan = {
        "plan_id": PLAN_ID,
        "implementation_binding_ref": BINDING_ID,
        "execution_contract_ref": "execution-contract:create-order",
        "status": "TEMPLATE_READY",
        "formal_runtime_plan": True,
        "action_entry": {
            "interface_id": "api:POST:/orders",
            "action_surface_binding_ref": ACTION_REF,
        },
        "request_template": {
            "path_parameters": [],
            "query_parameters": [],
            "header_parameters": [],
            "cookie_parameters": [],
            "body_fields": [],
            "form_fields": [],
        },
        "oracle_query_templates": {
            "templates": [
                {
                    "template_id": "event-template",
                    "template_kind": "SOURCE_EVENT_DELIVERY_OBSERVATION",
                    "observer_binding_ref": EVENT_REF,
                },
                {
                    "template_id": "database-template",
                    "template_kind": "APPROVED_DATABASE_OBSERVER_SNAPSHOT",
                    "observer_contract_ref": DB_CONTRACT_REF,
                },
            ]
        },
        "binding_identity_refs": {
            "action_surface_binding_ref": ACTION_REF,
            "observer_binding_refs": [EVENT_REF, DB_REF],
        },
    }
    materialization = {
        "materialization_id": MATERIALIZATION_ID,
        "runtime_plan_ref": PLAN_ID,
        "status": "DRAFT_READY",
        "formal_runtime_materialization": True,
        "request_value_bindings": [],
        "request_draft": {},
        "assertion_drafts": [
            {
                "draft_id": "event-draft",
                "draft_kind": "SOURCE_EVENT_DELIVERY_ASSERTION_DRAFT",
                "observer_binding_ref": EVENT_REF,
            }
        ],
        "database_observer_execution_drafts": [
            {
                "draft_id": "database-draft",
                "observer_contract_ref": DB_CONTRACT_REF,
            }
        ],
        "binding_identity_refs": {
            "action_surface_binding_ref": ACTION_REF,
            "observer_binding_refs": [EVENT_REF, DB_REF],
        },
    }
    asset = {
        "binding_identity_graph": {
            "observer_bindings": [
                {
                    "observer_binding_id": EVENT_REF,
                    "implementation_binding_ref": BINDING_ID,
                    "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
                },
                {
                    "observer_binding_id": DB_REF,
                    "implementation_binding_ref": BINDING_ID,
                    "binding_kind": "DATABASE_FIELD",
                    "observer_id": DB_CONTRACT_REF,
                },
            ]
        },
        "scenario_execution_contracts": [
            {
                "contract_id": "execution-contract:create-order",
                "action_contract": {
                    "action_surface_binding_ref": ACTION_REF,
                },
                "request_contract": {
                    "path_parameter_requirements": [],
                    "request_field_requirements": [],
                },
            }
        ],
        "runtime_plans": [plan],
        "runtime_plan_unknowns": [],
        "runtime_plan_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_plan_ready": True,
            "metrics": {},
        },
        "runtime_materializations": [materialization],
        "runtime_materialization_unknowns": [],
        "runtime_materialization_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_materialization_ready": True,
            "metrics": {},
        },
        "binding_identity_unknowns": [],
    }
    return asset, {}


def test_observer_refs_are_restored_after_generic_runtime_projection() -> None:
    asset, model = _base_asset()

    project_binding_identities_to_runtime_plans(asset, model)
    assert asset["runtime_plans"][0]["binding_identity_refs"].get(
        "observer_binding_refs"
    ) is None

    project_observer_identities_to_runtime_plans(asset, model)

    plan = asset["runtime_plans"][0]
    assert plan["binding_identity_refs"]["observer_binding_refs"] == [
        DB_REF,
        EVENT_REF,
    ]
    assert plan["observer_binding_refs"] == [DB_REF, EVENT_REF]
    assert plan["formal_runtime_plan"] is True
    assert asset["runtime_plan_gate"]["entry_allowed"] is True


def test_observer_refs_are_proved_after_generic_materialization_projection() -> None:
    asset, model = _base_asset()
    project_binding_identities_to_runtime_plans(asset, model)
    project_observer_identities_to_runtime_plans(asset, model)

    project_binding_identities_to_materializations(asset, model)
    assert asset["runtime_materializations"][0]["binding_identity_refs"].get(
        "observer_binding_refs"
    ) is None

    project_observer_identities_to_materializations(asset, model)

    materialization = asset["runtime_materializations"][0]
    assert materialization["binding_identity_refs"]["observer_binding_refs"] == [
        DB_REF,
        EVENT_REF,
    ]
    assert materialization["observer_binding_refs"] == [DB_REF, EVENT_REF]
    assert materialization["formal_runtime_materialization"] is True
    assert asset["runtime_materialization_gate"]["entry_allowed"] is True


def test_missing_event_assertion_draft_closes_materialization_gate() -> None:
    asset, model = _base_asset()
    asset["runtime_materializations"][0]["assertion_drafts"] = []
    project_binding_identities_to_runtime_plans(asset, model)
    project_observer_identities_to_runtime_plans(asset, model)
    project_binding_identities_to_materializations(asset, model)

    project_observer_identities_to_materializations(asset, model)

    materialization = asset["runtime_materializations"][0]
    assert materialization["formal_runtime_materialization"] is False
    assert materialization["status"] == "INCOMPLETE"
    assert materialization["binding_identity_refs"]["observer_binding_refs"] == [
        DB_REF
    ]
    assert asset["runtime_materialization_gate"]["entry_allowed"] is False
    assert (
        asset["runtime_materialization_gate"]["status"]
        == "BLOCKED_RUNTIME_MATERIALIZATION_OBSERVER_IDENTITY_INCOMPLETE"
    )
    assert any(
        row["reason_code"] == "RUNTIME_MATERIALIZATION_OBSERVER_DRAFT_MISSING"
        and row["observer_binding_ref"] == EVENT_REF
        for row in asset["binding_identity_unknowns"]
    )


def test_unresolved_direct_event_observer_ref_closes_runtime_plan_gate() -> None:
    asset, model = _base_asset()
    asset["runtime_plans"][0]["oracle_query_templates"]["templates"][0][
        "observer_binding_ref"
    ] = "observer-binding:missing"
    project_binding_identities_to_runtime_plans(asset, model)

    project_observer_identities_to_runtime_plans(asset, model)

    assert asset["runtime_plans"][0]["formal_runtime_plan"] is False
    assert asset["runtime_plan_gate"]["entry_allowed"] is False
    assert (
        asset["runtime_plan_gate"]["status"]
        == "BLOCKED_RUNTIME_PLAN_OBSERVER_IDENTITY_INCOMPLETE"
    )
    assert any(
        row["reason_code"] == "RUNTIME_PLAN_OBSERVER_BINDING_REF_UNRESOLVED"
        for row in asset["binding_identity_unknowns"]
    )
