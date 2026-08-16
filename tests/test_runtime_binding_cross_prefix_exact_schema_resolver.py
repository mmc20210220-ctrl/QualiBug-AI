from __future__ import annotations

import importlib


def _object_response(name: str) -> dict:
    return {"200": {"content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{name}"}}}}}


def _array_response(name: str) -> dict:
    return {"200": {"content": {"application/json": {"schema": {"type": "array", "items": {"$ref": f"#/components/schemas/{name}"}}}}}}


def _resolver():
    target = importlib.import_module("ai_test_asset_center._runtime_binding_graph_target_mechanics")
    authority = importlib.import_module("ai_test_asset_center.runtime_read_resolver_authority")
    authority.install_runtime_read_resolver_authority(target)
    return target.declared_runtime_read_resolvers


def test_unique_cross_prefix_exact_schema_list_is_identity_authority() -> None:
    target = {
        "id": "set-role", "method": "POST", "path": "/api/auth/admin/users/{id}/role",
        "response_schema": _object_response("User"),
    }
    ir = {"operations": [target, {
        "id": "search-users", "method": "GET", "path": "/api/users/admin/search",
        "parameters": [{"name": "keyword", "in": "query", "required": False}],
        "response_schema": _array_response("User"),
    }]}
    assert _resolver()(target, behavior_ir=ir) == [
        {"operation_ref": "search-users", "method": "GET", "path": "/api/users/admin/search"}
    ]


def test_cross_prefix_different_schema_stays_fail_closed() -> None:
    target = {
        "id": "set-role", "method": "POST", "path": "/api/auth/admin/users/{id}/role",
        "response_schema": _object_response("User"),
    }
    ir = {"operations": [target, {
        "id": "sales", "method": "GET", "path": "/api/reports/customer/sales",
        "response_schema": _array_response("CustomerSales"),
    }]}
    assert _resolver()(target, behavior_ir=ir) == []


def test_cross_prefix_ambiguous_same_schema_lists_stay_fail_closed() -> None:
    target = {
        "id": "set-role", "method": "POST", "path": "/api/auth/admin/users/{id}/role",
        "response_schema": _object_response("User"),
    }
    ir = {"operations": [target,
        {"id": "users-a", "method": "GET", "path": "/api/users/search", "response_schema": _array_response("User")},
        {"id": "users-b", "method": "GET", "path": "/api/directory/users", "response_schema": _array_response("User")},
    ]}
    assert _resolver()(target, behavior_ir=ir) == []


def test_cross_prefix_candidate_with_required_parameter_stays_fail_closed() -> None:
    target = {
        "id": "set-role", "method": "POST", "path": "/api/auth/admin/users/{id}/role",
        "response_schema": _object_response("User"),
    }
    ir = {"operations": [target, {
        "id": "search-users", "method": "GET", "path": "/api/users/admin/search",
        "parameters": [{"name": "tenant", "in": "query", "required": True}],
        "response_schema": _array_response("User"),
    }]}
    assert _resolver()(target, behavior_ir=ir) == []


def test_array_target_does_not_gain_cross_prefix_entity_resolver() -> None:
    target = {
        "id": "customer-sales", "method": "GET", "path": "/api/reports/customer/{userId}/sales",
        "response_schema": _array_response("CustomerSales"),
    }
    ir = {"operations": [target, {
        "id": "search-users", "method": "GET", "path": "/api/users/admin/search",
        "response_schema": _array_response("CustomerSales"),
    }]}
    assert _resolver()(target, behavior_ir=ir) == []


def test_schema_exact_resolver_outranks_heuristic_module_prefix_fallback() -> None:
    """The schema-matched resolver (right entity) must be tried BEFORE the
    heuristic action-path fallback (wrong entity).

    Regression: status2's ``{id}`` is a USER id (path segment ``users``), but
    the action-path fallback picked ``GET /api/users/addresses`` (Address rows)
    because it is the only module-prefix GET that pairs with a create POST.
    When the addresses collection was non-empty (fixture residue), the executor
    short-circuited on the WRONG entity's id, cleanup restored the wrong
    identity and the DELIVERABLE finding was lost.
    """
    target = {
        "id": "status2",
        "method": "POST",
        "path": "/api/users/admin/users/{id}/status2",
        "response_schema": _object_response("User"),
    }
    ir = {"operations": [
        target,
        {
            "id": "addresses",
            "method": "GET",
            "path": "/api/users/addresses",
            "response_schema": _array_response("Address"),
        },
        {
            "id": "create-addresses",
            "method": "POST",
            "path": "/api/users/addresses",
            "request_example": {"userId": "<user_id>", "city": "C"},
        },
        {
            "id": "search-users",
            "method": "GET",
            "path": "/api/users/admin/search",
            "response_schema": _array_response("User"),
        },
    ]}
    rows = _resolver()(target, behavior_ir=ir)
    assert [r["path"] for r in rows] == [
        "/api/users/admin/search",
        "/api/users/addresses",
    ]
