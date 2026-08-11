from __future__ import annotations


def _flow() -> dict:
    return {
        "schema_version": "qualibug.flow-data-execution-contract.v1",
        "status": "FROZEN",
        "step_contracts": [],
    }


def _experiment(step: dict, *, binding_plan=None) -> dict:
    return {
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "precondition_plan": [],
        "control_plan": [step],
        "treatment_plan": [],
        "binding_plan": list(binding_plan or []),
        "fixture_dag": {"nodes": []},
        "flow_data_execution_contract": _flow(),
    }


def test_missing_source_required_query_blocks_compile_contract() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_BLOCKED,
        build_request_build_contract,
    )

    operation = {
        "id": "list-orders",
        "method": "GET",
        "path": "/api/orders",
        "parameters": [
            {"name": "tenantId", "in": "query", "required": True}
        ],
    }
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "control_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-a",
                "query": {},
            }
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_BLOCKED
    query = contract["steps"][0]["components"][1]
    assert query["required"][0]["reason_code"] == "REQUEST_REQUIRED_QUERY_MISSING"


def test_actor_identity_query_ref_is_deferred_not_blocked() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_DEFERRED,
        build_request_build_contract,
    )

    operation = {
        "id": "list-orders",
        "method": "GET",
        "path": "/api/orders",
        "parameters": [
            {"name": "userId", "in": "query", "required": True}
        ],
    }
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "control_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-a",
                "query": {"userId": "actor_identity_ref:actor-a:userId"},
            }
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_DEFERRED
    query = contract["steps"][0]["components"][1]
    assert query["required"][0]["authority"] == "step_actor_identity_ref"


def test_required_custom_header_is_explicit_transport_gap() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_BLOCKED,
        build_request_build_contract,
    )

    operation = {
        "id": "tenant-read",
        "method": "GET",
        "path": "/api/orders",
        "parameters": [
            {"name": "X-Tenant-ID", "in": "header", "required": True}
        ],
    }
    contract = build_request_build_contract(
        _experiment({"step_id": "control_1", "operation_ref": "tenant-read"}),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_BLOCKED
    header = contract["steps"][0]["components"][2]
    assert header["required"][0]["reason_code"] == (
        "REQUEST_REQUIRED_HEADER_TRANSPORT_UNSUPPORTED"
    )


def test_transport_authorization_header_is_deferred_by_existing_actor_channel() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_DEFERRED,
        build_request_build_contract,
    )

    operation = {
        "id": "private-read",
        "method": "GET",
        "path": "/api/private",
        "parameters": [
            {"name": "Authorization", "in": "header", "required": True}
        ],
    }
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "control_1",
                "operation_ref": "private-read",
                "actor_ref": "actor-a",
            }
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_DEFERRED
    header = contract["steps"][0]["components"][2]
    assert header["required"][0]["authority"] == "actor_bearer_token"


def test_missing_required_body_field_blocks_before_runtime() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_BLOCKED,
        build_request_build_contract,
    )

    operation = {
        "id": "create-order",
        "method": "POST",
        "path": "/api/orders",
        "request_schema": {
            "type": "object",
            "required": ["sku", "qty"],
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
            },
        },
    }
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "control_1",
                "operation_ref": "create-order",
                "body": {"qty": 1},
            }
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_BLOCKED
    body = contract["steps"][0]["components"][3]
    missing = next(row for row in body["required"] if row["field"] == "sku")
    assert missing["reason_code"] == "REQUEST_REQUIRED_BODY_FIELD_MISSING"


def test_required_field_removal_mutation_is_intentional_request_not_gap() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_READY,
        build_request_build_contract,
    )

    operation = {
        "id": "create-order",
        "method": "POST",
        "path": "/api/orders",
        "request_schema": {
            "type": "object",
            "required": ["sku"],
            "properties": {"sku": {"type": "string"}},
        },
    }
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "treatment_1",
                "operation_ref": "create-order",
                "body": {},
                "required_field_removal": ["sku"],
            }
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_READY
    body = contract["steps"][0]["components"][3]
    assert body["required"][0]["authority"] == (
        "declared_required_field_removal_mutation"
    )
    assert body["required"][0]["intentional_absence"] is True


def test_observed_body_projection_can_defer_missing_required_field() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_DEFERRED,
        build_request_build_contract,
    )

    operation = {
        "id": "update-order",
        "method": "PATCH",
        "path": "/api/orders/{orderId}",
        "request_schema": {
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
    }
    binding_plan = [
        {
            "target": "__observed_body",
            "status": "runtime_resolvable",
            "source_priority": "observed_entity_write_body",
            "body_projection_fields": ["status"],
        },
        {
            "target": "orderId",
            "status": "bound",
            "source_priority": "source_value",
            "materialized_value": "O-1",
        },
    ]
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "control_1",
                "operation_ref": "update-order",
                "body": {},
            },
            binding_plan=binding_plan,
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_DEFERRED
    body = contract["steps"][0]["components"][3]
    assert body["required"][0]["authority"] == "observed_body_projection"


def test_body_placeholder_with_sealed_materialized_value_is_buildable() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_READY,
        build_request_build_contract,
    )

    operation = {
        "id": "create-line",
        "method": "POST",
        "path": "/api/lines",
        "request_schema": {
            "type": "object",
            "required": ["orderId"],
            "properties": {"orderId": {"type": "string"}},
        },
    }
    contract = build_request_build_contract(
        _experiment(
            {
                "step_id": "control_1",
                "operation_ref": "create-line",
                "body": {"orderId": "{orderId}"},
            },
            binding_plan=[
                {
                    "target": "orderId",
                    "status": "bound",
                    "source_priority": "source_value",
                    "materialized_value": "O-1",
                }
            ],
        ),
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )

    assert contract["status"] == STATUS_READY
    body = contract["steps"][0]["components"][3]
    assert body["placeholders"][0]["target_receipt"]["authority"] == (
        "sealed_materialized_value"
    )


def test_runtime_validation_detects_request_contract_drift() -> None:
    from ai_test_asset_center.request_build_contract import (
        STATUS_BLOCKED,
        build_request_build_contract,
        validate_request_build_contract,
    )

    operation = {
        "id": "list-orders",
        "method": "GET",
        "path": "/api/orders",
        "parameters": [
            {"name": "tenantId", "in": "query", "required": True}
        ],
    }
    exp = _experiment(
        {
            "step_id": "control_1",
            "operation_ref": "list-orders",
            "query": {"tenantId": "T-1"},
        }
    )
    exp["request_build_contract"] = build_request_build_contract(
        exp,
        behavior_ir={"operations": [operation]},
        flow_execution_contract=_flow(),
    )
    exp["control_plan"][0]["query"] = {}

    gate = validate_request_build_contract(
        exp,
        behavior_ir={"operations": [operation]},
    )
    assert gate["status"] == STATUS_BLOCKED
    assert gate["reason_code"] == "REQUEST_BUILD_CONTRACT_DRIFT"
