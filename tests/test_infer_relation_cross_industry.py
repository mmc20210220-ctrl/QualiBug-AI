from __future__ import annotations

from ai_test_asset_center.defect_discovery import (
    build_semantic_graph,
    infer_relation,
    read_path_for_resource,
)


def test_infer_relation_keeps_ecommerce_pairs_when_resources_exist():
    assert infer_relation("orders", "products", "") == "consumes_or_locks"
    assert infer_relation("orders", "payments", "") == "paid_by"
    assert infer_relation("cart", "orders", "") == "checkout_to_order"
    assert infer_relation("payments", "refunds", "") == "refund_depends_on_payment"


def test_infer_relation_covers_non_ecommerce_fulfillment_and_ownership():
    assert infer_relation("appointments", "beds", "") == "consumes_or_locks"
    assert infer_relation("claims", "settlements", "") == "paid_by"
    assert infer_relation("loans", "refunds", "") == "refunded_by"
    assert infer_relation("patients", "medical_records", "") == "owns_or_scopes"
    assert infer_relation("medical_records", "patients", "") == "belongs_to"
    assert infer_relation("students", "grades", "") == "owns_or_scopes"
    assert infer_relation("enrollments", "seats", "") == "consumes_or_locks"


def test_infer_relation_does_not_invent_mall_edges_for_unrelated_resources():
    assert infer_relation("patients", "products", "") is None
    assert infer_relation("tenants", "cart", "") is None
    assert infer_relation("reports", "coupons", "") is None


def test_infer_relation_uses_prd_cooccurrence_for_generic_links():
    prd = "The ticket approval workflow relates tickets and approvers across organization tenant scope."
    assert infer_relation("tickets", "approvers", prd) in {
        "workflow_related",
        "scope_dependency",
        "referenced_dependency",
    }


def test_build_semantic_graph_includes_healthcare_edges_not_only_mall():
    operations = [
        {"resource": "patients", "risk_hints": ["privacy_leak"], "operations": [], "method": "GET", "path": "/patients/{id}", "operation": "read"},
        {"resource": "medical_records", "risk_hints": ["privacy_leak"], "operations": [], "method": "GET", "path": "/medical_records/{id}", "operation": "read"},
        {"resource": "appointments", "risk_hints": ["appointment_conflict"], "operations": [], "method": "POST", "path": "/appointments", "operation": "create_or_action"},
        {"resource": "beds", "risk_hints": ["quantity_consistency"], "operations": [], "method": "GET", "path": "/beds/{id}", "operation": "read"},
    ]
    # build_semantic_graph expects operations with those keys used in the loop
    for op in operations:
        op.setdefault("operations", [])
    graph = build_semantic_graph(operations, "patient medical_record appointment bed clinic")
    edges = {(e["from"], e["to"], e["relation"]) for e in graph["edges"]}
    assert ("patients", "medical_records", "owns_or_scopes") in edges
    assert ("appointments", "beds", "consumes_or_locks") in edges
    assert not any(e[0] == "orders" or e[1] == "cart" for e in edges)


def test_read_path_for_resource_is_generic_not_synthetic_mall_ids():
    assert read_path_for_resource("orders") == "/orders/{id}"
    assert read_path_for_resource("patients") == "/patients/{id}"
    assert "o900" not in read_path_for_resource("payments")
    assert "p100" not in read_path_for_resource("products")
