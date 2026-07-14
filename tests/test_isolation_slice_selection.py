"""Selection must reserve isolation/permission kinds under entity-diversity pressure."""
from __future__ import annotations

from ai_test_asset_center.oracle_engine import TenantIsolationOracle
from ai_test_asset_center.supplementary_behavior_slices import generate_isolation_slices
from ai_test_asset_center.v12_pipeline import _take_diverse_slice_batch


def test_isolation_slices_survive_entity_diversity_budget():
    isolation = {
        "slice_id": "BHV_iso_addresses",
        "kind": "isolation",
        "entity": "user",
        "endpoints": ["/api/users/addresses"],
        "priority": 0.9,
        "_isolation_path": "/api/users/addresses",
        "_isolation_mode": "query_param",
        "_isolation_query_param": "userId",
        "_isolation_owner_email": "owner@example.com",
        "_isolation_viewer_email": "viewer@example.com",
    }
    pool = [
        {
            "slice_id": f"BHV_inv{i:04d}",
            "kind": "invariant",
            "entity": f"ent{i}",
            "endpoints": [f"/api/e{i}"],
            "priority": 0.99,
        }
        for i in range(250)
    ]
    pool.append(isolation)
    selected = _take_diverse_slice_batch(pool, budget=200)
    selected_ids = {str(item.get("slice_id") or "") for item in selected}
    assert "BHV_iso_addresses" in selected_ids
    assert len(selected) == 200


def test_owned_collection_isolation_emitted_for_scoped_list_reads():
    endpoints = [
        {
            "method": "GET",
            "path": "/api/orders",
            "summary": "List orders",
            "entity": "order",
        },
        {
            "method": "GET",
            "path": "/api/users/addresses",
            "summary": "Query addresses with optional `userId`; caller may only query own addresses",
            "entity": "user",
        },
        {"method": "GET", "path": "/api/auth/me", "summary": "me", "entity": "auth"},
    ]
    actors = [
        {"role": "buyer", "email": "buyer01@example.com", "password": "x"},
        {"role": "buyer", "email": "buyer02@example.com", "password": "x"},
    ]
    matrix = [
        {"role": "buyer", "resource": "order", "scope": "own", "actions": ["read", "list"]},
        {"role": "buyer", "resource": "address", "scope": "own", "actions": ["read", "list"]},
    ]
    slices = generate_isolation_slices(
        endpoints,
        actors,
        login_path="/api/auth/login",
        permission_matrix=matrix,
    )
    by_path = {
        (
            str(item.get("_isolation_path") or ""),
            str(item.get("_isolation_mode") or ""),
            str(item.get("_isolation_query_param") or ""),
        ): item
        for item in slices
    }
    assert ("/api/users/addresses", "query_param", "userId") in by_path
    # Own-scoped collections reuse corpus-documented ownership binders even when
    # the local endpoint summary omits the query param name.
    assert ("/api/orders", "query_param", "userId") in by_path


def test_owned_collection_fallback_without_ownership_query_binder():
    endpoints = [
        {
            "method": "GET",
            "path": "/api/orders",
            "summary": "List orders for the current buyer",
            "entity": "order",
        },
        {"method": "GET", "path": "/api/auth/me", "summary": "me", "entity": "auth"},
    ]
    actors = [
        {"role": "buyer", "email": "buyer01@example.com", "password": "x"},
        {"role": "buyer", "email": "buyer02@example.com", "password": "x"},
    ]
    matrix = [
        {"role": "buyer", "resource": "order", "scope": "own", "actions": ["read", "list"]},
    ]
    slices = generate_isolation_slices(
        endpoints,
        actors,
        login_path="/api/auth/login",
        permission_matrix=matrix,
    )
    assert any(
        item.get("_isolation_path") == "/api/orders"
        and item.get("_isolation_mode") == "owned_collection"
        for item in slices
    )
    from ai_test_asset_center.supplementary_behavior_slices import (
        _is_identity_mutation_endpoint,
    )

    assert _is_identity_mutation_endpoint({
        "method": "POST",
        "path": "/api/auth/password/reset",
        "action": "reset",
        "entity": "auth",
    })
    assert not _is_identity_mutation_endpoint({
        "method": "POST",
        "path": "/api/auth/admin/users/{id}/status",
        "action": "status",
        "entity": "auth",
    })
    assert not _is_identity_mutation_endpoint({
        "method": "POST",
        "path": "/api/auth/register",
        "action": "register",
        "entity": "auth",
    })


def test_sibling_identity_body_binds_order_id_for_empty_action_write():
    from pathlib import Path

    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    doc = Path("projects/benchmark_mall/input/API_SPEC.md").read_text(encoding="utf-8")
    body, provenance = SemanticScenarioGenerator._sibling_identity_body_bindings(
        doc,
        "/api/payments/admin/manual-success",
        root=".",
        project="benchmark_mall",
    )
    assert body == {"orderId": "{orderId}"}
    assert "sibling_identity_binding" in provenance
    assert "/api/payments/pay" in provenance


def test_tenant_isolation_oracle_flags_collection_leak():
    oracle = TenantIsolationOracle()
    scenario = {
        "category": "isolation",
        "behavior_slice_kind": "isolation",
        "oracle_rules": ["TenantIsolationOracle.cross_user_isolation"],
    }
    trace = {
        "steps": [
            {
                "action": "resolve_owner_collection_ids",
                "response": {"status_code": 200, "body": {"items": [{"id": "order-owner-1"}]}},
            },
            {
                "action": "isolation_probe_buyer",
                "expected_status": 200,
                "response": {
                    "status_code": 200,
                    "body": {"items": [{"id": "order-owner-1"}, {"id": "order-viewer-2"}]},
                },
            },
        ]
    }
    result = oracle.evaluate(scenario, trace)
    assert result.passed is False
    assert result.violated_rule == "cross_user_collection_leak"
