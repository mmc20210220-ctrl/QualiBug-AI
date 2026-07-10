from __future__ import annotations

import json

from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe
from ai_test_asset_center.real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from ai_test_asset_center.snapshot_observer_planner import plan_snapshot_observers_for_probe


def _probe(path: str) -> dict:
    return {
        "candidate_id": "QBPATH-1",
        "risk_type": "conservation_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": path},
        "probe_plan": {"mutation": {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1}},
    }


def test_shared_placeholder_normalization_supports_common_path_styles() -> None:
    assert normalize_path_placeholders("/api/orders/:orderId/items/${lineId}") == "/api/orders/{orderId}/items/{lineId}"
    assert normalize_path_placeholders("/api/orders/<orderId>") == "/api/orders/{orderId}"
    assert normalize_path_placeholders("/api/orders/{orderId:int}") == "/api/orders/{orderId}"
    assert infer_path_params("/api/orders/:orderId/items/<lineId>") == ["orderId", "lineId"]
    assert path_has_placeholders("/api/orders/${orderId}")


def test_sku_binding_uses_product_catalog_fallback_and_field_aliases() -> None:
    from ai_test_asset_center.real_id_resolver import (
        alternate_collection_paths,
        bind_entity_fields,
        extract_fields_for_path,
        param_field_candidates,
    )

    assert "sku" in param_field_candidates("sku")
    assert "sku" in extract_fields_for_path("/api/inventory/:sku")
    assert "/api/products" in alternate_collection_paths("/api/inventory/{sku}")
    assert "/api/materials" in alternate_collection_paths("/api/inventory/{sku}")
    bindings = bind_entity_fields(
        [{"sku": "SKU-PHONE-001", "title": "phone", "status": "ON_SALE"}],
        "/api/inventory/{sku}",
    )
    assert bindings["sku"] == "SKU-PHONE-001"
    assert bindings.get("id") == "SKU-PHONE-001"


def test_alternate_collection_paths_derives_non_ecommerce_siblings() -> None:
    from ai_test_asset_center.real_id_resolver import alternate_collection_paths

    patient_alts = alternate_collection_paths("/api/encounters/{patient_id}/vitals")
    assert "/api/patients" in patient_alts

    material_alts = alternate_collection_paths("/api/reservations/{material_code}")
    assert "/api/materials" in material_alts

    settlement_alts = alternate_collection_paths("/api/settlements/{settlement_id}/callback")
    assert "/api/settlement" in settlement_alts
    assert any("settlement" in path for path in settlement_alts)


def test_resolve_entity_steps_include_sibling_catalog_for_inventory() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    steps, probe = SemanticScenarioGenerator._resolve_entity_steps(
        "/api/inventory/:sku", actor="buyer", start_order=2,
    )
    assert probe == "/api/inventory/{sku}"
    assert steps
    resolve_paths = [step.api_path for step in steps]
    assert any(p.startswith("/api/products") for p in resolve_paths)
    assert "sku" in steps[0].extract_from_response or any("sku" in (s.extract_from_response or []) for s in steps)


def test_runtime_body_template_converts_angle_placeholders() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = """
### POST /api/inventory/reserve

请求：

```json
{"sku":"SKU-PHONE-001","qty":1,"orderId":"<order_id>"}
```
"""
    body = SemanticScenarioGenerator._runtime_body_template(api_doc, "POST", "/api/inventory/reserve")
    assert body["sku"] == "SKU-PHONE-001"
    assert body["qty"] == 1
    assert body["orderId"] == "{order_id}"


def test_inventory_slice_includes_write_body_and_order_resolve() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = """
### POST /api/inventory/reserve

请求：

```json
{"sku":"SKU-PHONE-001","qty":1,"orderId":"<order_id>"}
```
### GET /api/products
### GET /api/orders
"""
    slice_meta = {
        "entity": "inventory",
        "slice_id": "inv_test",
        "priority": 0.84,
        "source_refs": [],
        "_inventory_method": "POST",
        "_inventory_path": "/api/inventory/reserve",
        "_login_path": "/api/auth/login",
        "_login_body": {"email": "a", "password": "b"},
        "_default_actor": "buyer",
        "_default_email": "buyer@test.com",
        "_default_password": "pass",
    }
    scenario = SemanticScenarioGenerator._inventory_slice(slice_meta, 1, api_doc)
    assert scenario is not None
    write_steps = [
        step for step in scenario.steps
        if str(step.api_method).upper() == "POST" and "inventory_probe" in step.action
    ]
    assert write_steps
    assert write_steps[0].body_template.get("orderId") == "{order_id}"
    resolve_paths = [step.api_path for step in scenario.steps if step.action.startswith("resolve_")]
    assert any("/api/orders" in path for path in resolve_paths)


def test_observation_read_candidates_prefer_product_catalog_for_inventory_actions() -> None:
    from ai_test_asset_center.semantic_scenario_generator import _observation_read_candidates

    paths = _observation_read_candidates("/api/inventory/reserve")
    assert paths[0] == "/api/products"


def test_nested_admin_user_paths_resolve_via_search_or_me() -> None:
    from ai_test_asset_center.real_id_resolver import alternate_collection_paths
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    alts = alternate_collection_paths("/api/users/admin/users/{id}/balance")
    assert "/api/users/admin/search" in alts
    assert "/api/auth/me" in alts

    alts2 = alternate_collection_paths("/api/auth/admin/users/{id}/status")
    assert "/api/users/admin/search" in alts2 or "/api/auth/me" in alts2

    steps, probe = SemanticScenarioGenerator._resolve_entity_steps(
        "/api/users/admin/users/:id/balance", actor="admin", start_order=1,
    )
    assert probe == "/api/users/admin/users/{id}/balance"
    resolve_paths = [step.api_path for step in steps]
    assert any(p.endswith("/search") or p.endswith("/me") for p in resolve_paths)
    # Nested admin collection should not be the first attempt when search/me exist.
    assert not resolve_paths[0].endswith("/admin/users")


def test_orderid_path_prefers_orders_collection() -> None:
    from ai_test_asset_center.real_id_resolver import alternate_collection_paths
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    alts = alternate_collection_paths("/api/payments/order/{orderId}")
    assert "/api/orders" in alts
    steps, probe = SemanticScenarioGenerator._resolve_entity_steps(
        "/api/payments/order/:orderId", actor="buyer", start_order=1,
    )
    assert probe == "/api/payments/order/{orderId}"
    assert any(s.api_path.startswith("/api/orders") for s in steps)
    # Invented /payments/.../search must not crowd out the orders list.
    assert steps[0].api_path.startswith("/api/orders")


def test_address_body_binding_uses_users_addresses() -> None:
    from ai_test_asset_center.real_id_resolver import body_field_collection_paths
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    assert "/api/users/addresses" in body_field_collection_paths("address_id")
    api_doc = """
### POST /api/orders
请求：
```json
{"items":[{"sku":"SKU-PHONE-001","qty":1}],"addressId":"<address_id>"}
```
"""
    body = SemanticScenarioGenerator._runtime_body_template(api_doc, "POST", "/api/orders")
    assert body.get("addressId") == "{address_id}"
    bind_steps, _ = SemanticScenarioGenerator._body_binding_resolve_steps(
        body, actor="buyer", start_order=1,
    )
    assert any("/api/users/addresses" in s.api_path or "/api/addresses" in s.api_path for s in bind_steps)
    assert bind_steps[0].api_path == "/api/users/addresses"


def test_body_field_collection_paths_are_generic_rest() -> None:
    from ai_test_asset_center.real_id_resolver import body_field_collection_paths, extract_body_binding_fields

    assert "/api/orders" in body_field_collection_paths("orderId")
    assert "/api/products" in body_field_collection_paths("sku")
    assert extract_body_binding_fields({"orderId": "{order_id}", "qty": 1}) == ["order_id"]


def test_execution_skip_telemetry_aggregates_path_binding_misses() -> None:
    from ai_test_asset_center.v12_pipeline import _summarize_execution_skip_telemetry

    traces = [
        ("scn1", {
            "steps": [{"method": "POST", "path": "/api/inventory/reserve", "status": 0,
                       "skipped_reason": "missing_runtime_path_binding:sku,orderId",
                       "request": {"body": {}}, "response": {"status_code": 0}}],
            "errors": ["missing_runtime_path_binding:sku,orderId"],
        }),
        ("scn2", {
            "steps": [{"method": "GET", "path": "/api/products", "status": 200, "response": {"status_code": 200}}],
            "errors": [],
        }),
    ]
    summary = _summarize_execution_skip_telemetry(traces)
    assert summary["scenarios_with_http"] == 1
    assert summary["scenarios_blocked"] == 1
    assert summary["reason_counts"]["missing_runtime_path_binding"] == 2
    assert "sku,orderId" in summary["path_binding_misses"]
    assert summary["blocked_samples"][0]["reason"] == "missing_runtime_path_binding:sku,orderId"


def test_auto_fixture_uses_shared_placeholder_normalization_for_colon_paths() -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "amount": {"type": "number"}},
                                }
                            }
                        }
                    }
                }
            },
            "/api/orders/:order_id": {"get": {}, "delete": {}},
        },
    }

    bundle = build_auto_fixture_for_probe(
        _probe("/api/orders/:order_id"),
        config={"qualibug_auto_create_test_data": True, "api_doc_text": json.dumps(spec)},
    )

    assert bundle["setup_requests"][0]["path"] == "/api/orders"
    assert bundle["cleanup_requests"][0]["path"] == "/api/orders/{order_id}"
    assert bundle["snapshots"]["before"][0]["path"] == "/api/orders/{order_id}"
    assert bundle["path_params"]["order_id"].startswith("qb_auto_qbpath_1_")


def test_snapshot_observer_planner_normalizes_candidate_paths_before_binding() -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders/:order_id": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "tenant_id"},
                    ]
                }
            },
            "/api/orders": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "tenant_id"},
                    ]
                }
            },
        },
    }

    plan = plan_snapshot_observers_for_probe(
        _probe("/api/orders/:order_id"),
        spec=spec,
        primary_fixture_id="ord_123",
        seed="qbtest",
        max_observers=3,
    )

    assert plan["observers"]
    detail = next(item for item in plan["observers"] if item["observer_kind"] == "primary_resource_detail")
    assert detail["path"] == "/api/orders/{order_id}"
    assert detail["path_params"]["order_id"] == "ord_123"
