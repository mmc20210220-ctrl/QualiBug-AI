"""Source-declared identity outputs close sequential precondition bindings."""

from __future__ import annotations

from ai_test_asset_center.flow_data_execution_contract import (
    STATUS_FROZEN as EXECUTION_FROZEN,
    freeze_flow_data_execution_contract,
)
from ai_test_asset_center.blocker_attribution import profile_reason_code
from ai_test_asset_center.flow_data_requirement import (
    STATUS_FROZEN,
    build_flow_data_requirement,
)
from ai_test_asset_center.multi_level_dependency_chain import (
    BLOCKED,
    PLANNED,
    REASON_IDENTITY_AMBIGUOUS,
    REASON_IDENTITY_MISSING,
    plan_multi_level_dependency_chain,
)


def _behavior_ir(identity_fields: list[str]) -> dict:
    return {
        "entities": [
            {
                "id": "ent_record",
                "name": "record",
                "source_entity_names": ["records"],
                "identity_fields": identity_fields,
            }
        ],
        "actors": [{"id": "actor_operator"}],
        "operations": [
            {
                "id": "op_create_record",
                "method": "POST",
                "path": "/api/records",
                "request_example": {"label": "source-example"},
            },
            {
                "id": "op_list_records",
                "method": "GET",
                "path": "/api/records",
            },
            {
                "id": "op_delete_record",
                "method": "DELETE",
                "path": "/api/records/{record_key}",
            },
            {
                "id": "op_advance_record",
                "method": "PATCH",
                "path": "/api/records/{record_key}/advance",
                "request_example": {},
            },
        ],
        "relations": [
            {
                "relation_type": "permits",
                "actor_ref": "actor_operator",
                "operation_ref": "op_create_record",
            }
        ],
    }


def test_planner_projects_source_identity_output_binding() -> None:
    result = plan_multi_level_dependency_chain(
        behavior_ir=_behavior_ir(["record_key"]),
        entity_id="ent_record",
        reference_field="recordRef",
        actor_refs=["actor_operator"],
    )

    assert result["status"] == PLANNED
    binding = result["steps"][0]["identity_output_binding"]
    assert binding == {
        "schema_version": "qualibug.identity-output-binding.v1",
        "status": "FROZEN",
        "entity_ref": "ent_record",
        "source_identity_field": "record_key",
        "source_path": "record_key",
        "consumer_targets": ["recordRef"],
        "alias_targets": ["recordRef", "record_key"],
        "source_authority": "behavior_ir.entities.identity_fields",
    }


def test_source_identity_missing_or_ambiguous_fails_closed() -> None:
    missing = plan_multi_level_dependency_chain(
        behavior_ir=_behavior_ir([]),
        entity_id="ent_record",
        reference_field="recordRef",
        actor_refs=["actor_operator"],
    )
    ambiguous = plan_multi_level_dependency_chain(
        behavior_ir=_behavior_ir(["tenant_key", "record_key"]),
        entity_id="ent_record",
        reference_field="recordRef",
        actor_refs=["actor_operator"],
    )

    assert missing["status"] == BLOCKED
    assert missing["reason_code"] == REASON_IDENTITY_MISSING
    assert ambiguous["status"] == BLOCKED
    assert ambiguous["reason_code"] == REASON_IDENTITY_AMBIGUOUS
    assert ambiguous["detail"]["identity_fields"] == [
        "tenant_key",
        "record_key",
    ]


def test_sequential_identity_output_is_receipted_and_executable() -> None:
    ir = _behavior_ir(["record_key"])
    binding = {
        "schema_version": "qualibug.identity-output-binding.v1",
        "status": "FROZEN",
        "entity_ref": "ent_record",
        "source_identity_field": "record_key",
        "source_path": "record_key",
        "consumer_targets": ["recordRef"],
        "alias_targets": ["recordRef", "record_key"],
        "source_authority": "behavior_ir.entities.identity_fields",
    }
    experiment = {
        "precondition_plan": [
            {
                "step_id": "precondition_1",
                "operation_ref": "op_create_record",
                "identity_output_binding": binding,
            },
            {
                "step_id": "precondition_2",
                "operation_ref": "op_advance_record",
                "identity_input_binding": {
                    "schema_version": "qualibug.identity-input-binding.v1",
                    "status": "FROZEN",
                    "producer_step_id": "precondition_1",
                    "producer_output_field": "record_key",
                    "consumer_targets": ["record_key"],
                    "source_authority": "behavior_ir.entities.identity_fields",
                },
            },
        ],
        "control_plan": [],
        "treatment_plan": [],
        "binding_plan": [{"target": "recordRef", "status": "runtime_resolvable"}],
        "cleanup_plan": [],
    }

    requirement = build_flow_data_requirement(experiment, behavior_ir=ir)

    assert requirement["status"] == STATUS_FROZEN
    assert requirement["identity_output_binding_receipts"] == [
        {
            "producer_step_id": "precondition_1",
            "entity_ref": "ent_record",
            "source_identity_field": "record_key",
            "source_path": "record_key",
            "produced_targets": ["recordRef", "record_key"],
            "source_authority": "behavior_ir.entities.identity_fields",
            "status": "FROZEN",
        }
    ]
    second = requirement["step_requirements"][1]
    assert second["required_binding_targets"] == ["record_key"]
    assert second["materialized_before_step"] == ["record_key"]

    execution = freeze_flow_data_execution_contract(experiment, requirement)
    assert execution["status"] == EXECUTION_FROZEN
    second_contract = execution["step_contracts"][1]
    assert second_contract["sequential_identity_targets"] == ["record_key"]
    assert second_contract["identity_input_binding"]["status"] == "RESOLVED"
    assert second_contract["identity_input_binding"]["producer_step_id"] == (
        "precondition_1"
    )
    assert second_contract["missing_targets"] == []


def test_flow_identity_failures_are_registered_binding_gaps() -> None:
    for reason_code in (
        "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE",
        "BLOCKED_PRECONDITION_IDENTITY_OUTPUT_MISSING",
        "BLOCKED_PRECONDITION_IDENTITY_OUTPUT_AMBIGUOUS",
        REASON_IDENTITY_MISSING,
        REASON_IDENTITY_AMBIGUOUS,
    ):
        profile = profile_reason_code(reason_code)
        assert profile["registry_status"] == "REGISTERED"
        assert profile["reason_family"] == "BINDING_GRAPH_GAP"
