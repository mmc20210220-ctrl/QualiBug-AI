from __future__ import annotations

from ai_test_asset_center.validation_body_contract_authority import install_validation_body_contract_authority

install_validation_body_contract_authority()

from ai_test_asset_center.request_build_contract import (
    STATUS_BLOCKED,
    STATUS_READY,
    build_request_build_contract,
)


def _experiment(body: dict) -> dict:
    return {
        "experiment_id": "exp-body-required-presence",
        "obligation_id": "obl-body-required-presence",
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "op-create",
                "actor_ref": "actor-public",
                "body": body,
            }
        ],
        "treatment_plan": [],
        "binding_plan": [],
    }


def _operation() -> dict:
    return {
        "id": "op-create",
        "method": "POST",
        "path": "/resources",
        "request_schema": {
            "type": "object",
            "required": ["text", "items", "metadata"],
            "properties": {
                "text": {"type": "string"},
                "items": {"type": "array"},
                "metadata": {"type": "object"},
            },
        },
    }


def test_json_schema_required_means_key_presence_not_non_empty_value() -> None:
    contract = build_request_build_contract(
        _experiment({"text": "", "items": [], "metadata": {}}),
        behavior_ir={"operations": [_operation()]},
        flow_execution_contract={},
    )
    assert contract["status"] == STATUS_READY
    body = contract["steps"][0]["components"][3]
    assert [row["status"] for row in body["required"]] == [
        STATUS_READY,
        STATUS_READY,
        STATUS_READY,
    ]


def test_truly_absent_required_key_remains_blocked() -> None:
    contract = build_request_build_contract(
        _experiment({"text": "", "items": []}),
        behavior_ir={"operations": [_operation()]},
        flow_execution_contract={},
    )
    assert contract["status"] == STATUS_BLOCKED
    body = contract["steps"][0]["components"][3]
    metadata = next(row for row in body["required"] if row["field"] == "metadata")
    assert metadata["status"] == STATUS_BLOCKED
    assert metadata["reason_code"] == "REQUEST_REQUIRED_BODY_FIELD_MISSING"


def test_sealed_body_placeholder_is_ready_without_runtime_deferral() -> None:
    operation = {
        "id": "op-create",
        "method": "POST",
        "path": "/resources",
        "request_schema": {
            "type": "object",
            "required": ["resourceId"],
            "properties": {"resourceId": {"type": "string"}},
        },
    }
    experiment = _experiment({"resourceId": "{resourceId}"})
    experiment["binding_plan"] = [
        {
            "target": "resourceId",
            "status": "bound",
            "source_priority": "source_value",
            "materialized_value": "resource-1",
        }
    ]
    contract = build_request_build_contract(
        experiment,
        behavior_ir={"operations": [operation]},
        flow_execution_contract={},
    )
    assert contract["status"] == STATUS_READY
    body = contract["steps"][0]["components"][3]
    assert body["status"] == STATUS_READY
    assert body["placeholders"][0]["authority"] == "sealed_materialized_value"
