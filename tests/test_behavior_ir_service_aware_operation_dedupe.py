from __future__ import annotations

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset


def _ops(model: dict, method: str, path: str) -> list[dict]:
    return [
        row
        for row in model.get("operations", [])
        if row.get("method") == method and row.get("path") == path
    ]


def _request_schema(value_type: str) -> dict:
    return {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"value": {"type": value_type}},
                }
            }
        }
    }


def _request_value_type(operation: dict) -> str:
    return (
        operation["request_schema"]["content"]["application/json"]["schema"]
        ["properties"]["value"]["type"]
    )


def test_same_transport_on_distinct_services_remains_distinct() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {
            "interfaces": [
                {
                    "operation_id": "svc_a_shared_items",
                    "service": "svc-a",
                    "method": "GET",
                    "path": "/shared/items",
                },
                {
                    "operation_id": "svc_b_shared_items",
                    "service": "svc-b",
                    "method": "GET",
                    "path": "/shared/items",
                },
            ]
        },
        project_id="service-aware-dedupe",
    )

    operations = _ops(model, "GET", "/shared/items")
    assert len(operations) == 2
    assert {row.get("service") for row in operations} == {"svc-a", "svc-b"}
    assert len({row.get("id") for row in operations}) == 2


def test_service_less_duplicate_merges_into_unique_declared_owner() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {
            "interfaces": [
                {
                    "operation_id": "asset_items",
                    "service": "svc-a",
                    "method": "GET",
                    "path": "/items",
                }
            ]
        },
        project_id="service-aware-dedupe",
        api_operations=[
            {
                "operation_id": "submitted_items",
                "method": "GET",
                "path": "/items",
            }
        ],
    )

    operations = _ops(model, "GET", "/items")
    assert len(operations) == 1
    assert operations[0].get("service") == "svc-a"
    assert {"submitted_items", "asset_items"}.issubset(
        set(operations[0].get("source_operation_refs") or [])
    )


def test_service_less_duplicate_fails_closed_when_owner_is_ambiguous() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {
            "interfaces": [
                {
                    "operation_id": "svc_a_health",
                    "service": "svc-a",
                    "method": "GET",
                    "path": "/health",
                },
                {
                    "operation_id": "svc_b_health",
                    "service": "svc-b",
                    "method": "GET",
                    "path": "/health",
                },
            ]
        },
        project_id="service-aware-dedupe",
        api_operations=[
            {
                "operation_id": "submitted_health",
                "method": "GET",
                "path": "/health",
            }
        ],
    )

    operations = _ops(model, "GET", "/health")
    assert len(operations) == 2
    assert {row.get("service") for row in operations} == {"svc-a", "svc-b"}
    assert all(row.get("service") for row in operations)
    gaps = [
        row
        for row in model.get("coverage_gaps", [])
        if row.get("reason_code") == "OPERATION_SERVICE_OWNERSHIP_AMBIGUOUS"
    ]
    assert len(gaps) == 1
    assert gaps[0].get("candidate_service_refs") == ["svc-a", "svc-b"]


def test_cross_service_schema_difference_does_not_merge_or_crash() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {
            "interfaces": [
                {
                    "operation_id": "svc_a_write",
                    "service": "svc-a",
                    "method": "POST",
                    "path": "/shared/items",
                    "request_schema": _request_schema("string"),
                },
                {
                    "operation_id": "svc_b_write",
                    "service": "svc-b",
                    "method": "POST",
                    "path": "/shared/items",
                    "request_schema": _request_schema("integer"),
                },
            ]
        },
        project_id="service-aware-dedupe",
    )

    operations = _ops(model, "POST", "/shared/items")
    assert len(operations) == 2
    assert {
        (row.get("service"), _request_value_type(row)) for row in operations
    } == {("svc-a", "string"), ("svc-b", "integer")}
    assert not [
        row
        for row in model.get("conflicts", [])
        if row.get("conflict_type") == "operation_schema_conflict"
    ]


def test_same_service_schema_conflict_keeps_service_scoped_identity() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {
            "interfaces": [
                {
                    "operation_id": "svc_a_write_v1",
                    "service": "svc-a",
                    "method": "POST",
                    "path": "/items",
                    "request_schema": _request_schema("string"),
                },
                {
                    "operation_id": "svc_a_write_v2",
                    "service": "svc-a",
                    "method": "POST",
                    "path": "/items",
                    "request_schema": _request_schema("integer"),
                },
            ]
        },
        project_id="service-aware-dedupe",
    )

    assert len(_ops(model, "POST", "/items")) == 1
    conflicts = [
        row
        for row in model.get("conflicts", [])
        if row.get("conflict_type") == "operation_schema_conflict"
    ]
    assert len(conflicts) == 1
    assert conflicts[0].get("service") == "svc-a"
    assert conflicts[0].get("method") == "POST"
    assert conflicts[0].get("path_shape") == "/items"
    assert any(
        path.endswith("properties.value.type")
        for path in conflicts[0]["conflict_paths"]
    )
