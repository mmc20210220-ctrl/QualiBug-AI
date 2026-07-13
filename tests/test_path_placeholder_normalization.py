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


def test_path_specific_sibling_id_binding_uses_response_primary_id() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"id": "ord-123", "status": "PENDING"},
        "/api/payments/order/{orderId}",
    )

    assert bindings["id"] == "ord-123"
    assert bindings["orderId"] == "ord-123"


def test_primary_id_is_not_broadcast_across_multiple_path_identities() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"id": "ord-123", "status": "PENDING"},
        "/api/tenants/{tenantId}/orders/{orderId}",
    )

    assert bindings["id"] == "ord-123"
    assert "tenantId" not in bindings
    assert "orderId" not in bindings


def test_alternate_collection_paths_derives_non_ecommerce_siblings() -> None:
    from ai_test_asset_center.real_id_resolver import (
        alternate_collection_paths,
        body_field_collection_paths,
        param_field_candidates,
    )

    patient_alts = alternate_collection_paths("/api/encounters/{patient_id}/vitals")
    assert "/api/patients" in patient_alts

    material_alts = alternate_collection_paths("/api/reservations/{material_code}")
    assert "/api/materials" in material_alts

    settlement_alts = alternate_collection_paths("/api/settlements/{settlement_id}/callback")
    assert "/api/settlement" in settlement_alts
    assert any("settlement" in path for path in settlement_alts)

    appointment_alts = alternate_collection_paths("/api/appointments/{appointment_id}/cancel")
    assert "/api/appointments" in appointment_alts or any("appointment" in p for p in appointment_alts)

    assert "patient_id" in param_field_candidates("patient_id") or "patientId" in param_field_candidates("patient_id")
    assert "claimId" in param_field_candidates("claim_id") or "claim_id" in param_field_candidates("claim_id")
    assert any("appointments" in p for p in body_field_collection_paths("appointment_id"))
    assert any("prescriptions" in p for p in body_field_collection_paths("prescriptionId"))


def test_resolve_entity_steps_include_sibling_catalog_for_inventory() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    steps, probe = SemanticScenarioGenerator._resolve_entity_steps(
        "/api/inventory/:sku",
        actor="buyer",
        start_order=2,
        api_doc="### GET /api/products\n### GET /api/inventory/:sku\n",
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
    assert body["orderId"] == "{orderId}"


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
    assert write_steps[0].body_template.get("orderId") == "{orderId}"
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
        "/api/users/admin/users/:id/balance",
        actor="admin",
        start_order=1,
        api_doc="### GET /api/users/admin/search\n### GET /api/auth/me\n### POST /api/users/admin/users/:id/balance\n",
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
        "/api/payments/order/:orderId",
        actor="buyer",
        start_order=1,
        api_doc="### GET /api/orders\n### POST /api/payments/order/:orderId\n",
    )
    assert probe == "/api/payments/order/{orderId}"
    assert any(s.api_path.startswith("/api/orders") for s in steps)
    # Invented /payments/.../search must not crowd out the orders list.
    assert steps[0].api_path.startswith("/api/orders")


def test_graph_invariant_with_placeholder_observation_keeps_runtime_resolver() -> None:
    from ai_test_asset_center.business_state_graph import BusinessStateGraph, behavior_slice_id
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    invariant = "The bound transaction remains observable."
    graph = BusinessStateGraph("transaction")
    graph.add_state("INIT", invariants=[invariant], source_refs=[])
    slice_id = behavior_slice_id("invariant", "transaction", "INIT", invariant)
    active_slice = {
        "slice_id": slice_id,
        "entity": "transaction",
        "kind": "invariant",
        "states": ["INIT"],
        "endpoints": ["/api/transactions/order/:orderId"],
        "priority": 0.6,
        "source_refs": [],
        "evidence_gaps": [],
    }
    api_doc = """
### GET /api/orders
### GET /api/transactions/order/:orderId
"""

    scenarios = SemanticScenarioGenerator().generate(
        {"transaction": graph},
        api_doc=api_doc,
        active_slice_ids={slice_id},
        active_slices=[active_slice],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert [step.action for step in scenario.steps] == ["resolve_entity_id", "observe_bound_entity"]
    assert scenario.steps[0].api_path == "/api/orders"
    assert scenario.steps[0].extract_from_response is not None
    assert "orderId" in scenario.steps[0].extract_from_response
    assert scenario.steps[1].api_path == "/api/transactions/order/{orderId}"


def test_graph_invariant_never_observes_post_only_route_as_get() -> None:
    from ai_test_asset_center.business_state_graph import BusinessStateGraph, behavior_slice_id
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    invariant = "The settlement record remains consistent."
    graph = BusinessStateGraph("settlement")
    graph.add_state("ACTIVE", invariants=[invariant], source_refs=[])
    slice_id = behavior_slice_id("invariant", "settlement", "ACTIVE", invariant)
    active_slice = {
        "slice_id": slice_id,
        "entity": "settlement",
        "kind": "invariant",
        "states": ["ACTIVE"],
        "endpoints": ["/api/settlements"],
        "_bound_method": "POST",
        "_bound_path": "/api/settlements",
        "_hypothesis_family": "consistency",
        "source_refs": [{"source_type": "api", "quote": "POST /api/settlements"}],
    }
    api_doc = """
### GET /api/references
### POST /api/settlements

Request body:
```json
{"referenceId":"<reference_id>","amount":10}
```
"""

    scenarios = SemanticScenarioGenerator().generate(
        {"settlement": graph},
        api_doc=api_doc,
        active_slice_ids={slice_id},
        active_slices=[active_slice],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert any(step.action == "execute_bound_write" for step in scenario.steps)
    assert all(
        not (step.api_method == "GET" and step.api_path == "/api/settlements")
        for step in scenario.steps
    )
    assert all(step.action != "verify_bound_entity_after_write" for step in scenario.steps)


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
    assert body.get("addressId") == "{addressId}"
    bind_steps, _ = SemanticScenarioGenerator._body_binding_resolve_steps(
        body, actor="buyer", start_order=1,
    )
    assert any("/api/users/addresses" in s.api_path or "/api/addresses" in s.api_path for s in bind_steps)
    assert bind_steps[0].api_path == "/api/users/addresses"


def test_body_binding_bootstraps_create_when_api_doc_present() -> None:
    from pathlib import Path

    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = Path("projects/benchmark_mall/input/API_SPEC.md").read_text(encoding="utf-8")
    steps, _ = SemanticScenarioGenerator._body_binding_resolve_steps(
        {"orderId": "{orderId}"}, actor="buyer", start_order=1, api_doc=api_doc,
    )
    assert any(s.api_method == "GET" and s.api_path == "/api/orders" for s in steps)
    bootstrap = next(s for s in steps if s.action == "bootstrap_create_orderId")
    assert bootstrap.api_method == "POST"
    assert bootstrap.api_path == "/api/orders"
    assert isinstance(bootstrap.body_template, dict) and bootstrap.body_template


def test_bootstrap_create_preserves_source_identity_strings() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = """
### POST /api/cart/items

请求：

```json
{"sku":"SKU-PHONE-001","qty":1}
```
"""

    body = SemanticScenarioGenerator._bootstrap_create_body(api_doc, "/api/cart/items")

    assert body == {"sku": "SKU-PHONE-001", "qty": 1}


def test_bootstrap_create_drops_optional_promotional_demo_strings() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = """
### POST /api/orders

请求：

```json
{"items":[{"sku":"SKU-PHONE-001","qty":1}],"couponCode":"NEW100","addressId":"<address_id>"}
```
"""

    body = SemanticScenarioGenerator._bootstrap_create_body(api_doc, "/api/orders")

    assert "couponCode" not in body
    assert body["items"] == [{"sku": "SKU-PHONE-001", "qty": 1}]
    assert body["addressId"] == "{addressId}"


def test_transition_create_uses_rich_project_source_when_runtime_catalog_has_no_body(tmp_path) -> None:
    from ai_test_asset_center.business_state_graph import StateTransition
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_shop"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
## Order

### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"couponCode":"PROMO-ONCE","addressId":"<address_id>"}
```

### GET /api/orders
### GET /api/orders/{id}
### POST /api/orders/{id}/ship

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/orders
### GET /api/orders
### GET /api/orders/{id}
### POST /api/orders/{id}/ship
### GET /api/users/addresses
### POST /api/users/addresses
"""

    scenario = SemanticScenarioGenerator()._transition(
        "order",
        StateTransition(
            from_state="PAID",
            to_state="SHIPPED",
            action="ship",
            api_endpoint="/api/orders/{id}/ship",
        ),
        graph=None,
        discovery_round=1,
        api_doc=compact_runtime_catalog,
        root=tmp_path,
        project=project,
    )

    create_step = next(step for step in scenario.steps if step.action == "create_entity")
    assert create_step.body_provenance == "documented_example"
    assert create_step.body_template["items"] == [{"sku": "SKU-001", "qty": 1}]
    assert create_step.body_template["addressId"] == "{addressId}"
    assert "couponCode" not in create_step.body_template
    assert any(step.action == "bootstrap_create_addressId" for step in scenario.steps)


def test_transition_action_uses_rich_project_source_when_runtime_catalog_has_no_body(tmp_path) -> None:
    from ai_test_asset_center.business_state_graph import StateTransition
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_workflow"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
### POST /api/orders

Request body:
```json
{"name":"source order"}
```

### GET /api/orders

### POST /api/transactions/settle

Request body:
```json
{"orderId":"<order_id>","channel":"documented-channel"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/orders
### GET /api/orders
### POST /api/transactions/settle
"""

    scenario = SemanticScenarioGenerator()._transition(
        "order",
        StateTransition(
            from_state="PENDING",
            to_state="SETTLED",
            action="settle",
            api_endpoint="/api/transactions/settle",
        ),
        graph=None,
        discovery_round=1,
        api_doc=compact_runtime_catalog,
        root=tmp_path,
        project=project,
    )

    action_step = next(step for step in scenario.steps if step.action == "transition_settle")
    assert action_step.body_template == {
        "orderId": "{orderId}",
        "channel": "documented-channel",
    }


def test_bound_write_fallback_uses_rich_project_source_body(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_shop"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
## Order

### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"couponCode":"PROMO-ONCE","addressId":"<address_id>"}
```

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/orders
### GET /api/users/addresses
### POST /api/users/addresses
"""
    active_slices = [
        {
            "slice_id": "slice-idempotency",
            "kind": "invariant",
            "entity": "order",
            "states": ["active"],
            "endpoints": ["/api/orders"],
            "_bound_method": "POST",
            "_bound_path": "/api/orders",
            "_hypothesis_family": "idempotency",
            "source_refs": [{"source_type": "api", "quote": "POST /api/orders"}],
        },
        {
            "slice_id": "slice-bound-write",
            "kind": "invariant",
            "entity": "order",
            "states": ["active"],
            "endpoints": ["/api/orders"],
            "_bound_method": "POST",
            "_bound_path": "/api/orders",
            "_hypothesis_family": "consistency",
            "source_refs": [{"source_type": "api", "quote": "POST /api/orders"}],
        },
    ]

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=compact_runtime_catalog,
        active_slice_ids={item["slice_id"] for item in active_slices},
        active_slices=active_slices,
        allow_source_runtime=True,
        root=tmp_path,
        project=project,
    )

    by_id = {scenario.behavior_slice_id: scenario for scenario in scenarios}
    idempotency_step = next(
        step
        for step in by_id["slice-idempotency"].steps
        if step.action == "execute_bound_idempotency_write"
    )
    bound_write_step = next(
        step
        for step in by_id["slice-bound-write"].steps
        if step.action == "execute_bound_write"
    )
    for step in (idempotency_step, bound_write_step):
        assert step.body_provenance == "documented_example"
        assert step.body_template["items"] == [{"sku": "SKU-001", "qty": 1}]
        assert step.body_template["addressId"] == "{addressId}"
        assert "couponCode" not in step.body_template
    for scenario in by_id.values():
        assert any(step.action == "bootstrap_create_addressId" for step in scenario.steps)


def test_source_bound_bodyless_mutation_stays_plan_only() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = """
### POST /api/resources/admin

Administrative resource creation. The source does not declare a request body.
"""
    active_slice = {
        "slice_id": "source-bound-body-contract-missing",
        "kind": "invariant",
        "entity": "resource",
        "states": ["active"],
        "endpoints": ["/api/resources/admin"],
        "_bound_method": "POST",
        "_bound_path": "/api/resources/admin",
        "_hypothesis_family": "idempotency",
        "source_refs": [{"source_type": "api", "quote": "POST /api/resources/admin"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=api_doc,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    assert scenarios[0].execution_policy == "plan_only_requires_fixture"
    assert scenarios[0].steps == []
    assert "BOUND_WRITE_PRECONDITION_CONTRACT_MISSING" in scenarios[0].evidence_gaps


def test_bound_write_bootstraps_missing_body_identity_from_rich_project_source(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_shop"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
## Order

### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"addressId":"<address_id>"}
```

## Address

### GET /api/users/addresses

### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/orders
### GET /api/users/addresses
### POST /api/users/addresses
"""
    active_slice = {
        "slice_id": "slice-bound-write-with-address",
        "kind": "invariant",
        "entity": "order",
        "states": ["active"],
        "endpoints": ["/api/orders"],
        "_bound_method": "POST",
        "_bound_path": "/api/orders",
        "_hypothesis_family": "consistency",
        "source_refs": [{"source_type": "api", "quote": "POST /api/orders"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=compact_runtime_catalog,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
        root=tmp_path,
        project=project,
    )

    scenario = next(item for item in scenarios if item.behavior_slice_id == active_slice["slice_id"])
    bootstrap = next(step for step in scenario.steps if step.action == "bootstrap_create_addressId")
    write = next(step for step in scenario.steps if step.action == "execute_bound_write")

    assert bootstrap.api_method == "POST"
    assert bootstrap.api_path == "/api/users/addresses"
    assert bootstrap.body_template == {
        "receiver": "A User",
        "phone": "10000000000",
        "city": "Example City",
        "detail": "Example Street",
    }
    assert bootstrap.order < write.order


def test_bound_action_bootstraps_missing_path_identity_from_rich_project_source(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_shop"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
## Order

### GET /api/orders
### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"addressId":"<address_id>"}
```

### POST /api/orders/{id}/cancel

## Address

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### GET /api/orders
### POST /api/orders
### POST /api/orders/{id}/cancel
### GET /api/users/addresses
### POST /api/users/addresses
"""
    active_slice = {
        "slice_id": "slice-bound-order-cancel",
        "kind": "invariant",
        "entity": "order",
        "states": ["active"],
        "endpoints": ["/api/orders/{id}/cancel"],
        "_bound_method": "POST",
        "_bound_path": "/api/orders/{id}/cancel",
        "_hypothesis_family": "consistency",
        "source_refs": [{"source_type": "api", "quote": "POST /api/orders/{id}/cancel"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=compact_runtime_catalog,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
        root=tmp_path,
        project=project,
    )

    scenario = next(item for item in scenarios if item.behavior_slice_id == active_slice["slice_id"])
    actions = [step.action for step in scenario.steps]

    assert "bootstrap_create_addressId" in actions
    assert "bootstrap_create_id" in actions
    assert actions.index("bootstrap_create_addressId") < actions.index("bootstrap_create_id")
    assert actions.index("bootstrap_create_id") < actions.index("execute_bound_write")


def test_permission_action_bootstraps_target_from_rich_project_source(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_shop"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
### POST /api/auth/login

Request body:
```json
{"email":"user@example.test","password":"secret"}
```

### GET /api/orders
### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"addressId":"<address_id>"}
```

### POST /api/orders/{id}/cancel
### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/auth/login
### GET /api/orders
### POST /api/orders
### POST /api/orders/{id}/cancel
### GET /api/users/addresses
### POST /api/users/addresses
"""
    active_slice = {
        "slice_id": "permission-order-cancel",
        "kind": "permission",
        "entity": "order",
        "_permission_actor": "buyer",
        "_permission_email": "user@example.test",
        "_permission_password": "secret",
        "_permission_method": "POST",
        "_permission_path": "/api/orders/{id}/cancel",
        "_permission_expected_permitted": [],
        "_login_path": "/api/auth/login",
        "_login_body": {"email": "", "password": ""},
        "source_refs": [{"source_type": "permission", "quote": "buyer cannot cancel other orders"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=compact_runtime_catalog,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
        root=tmp_path,
        project=project,
    )

    scenario = next(item for item in scenarios if item.behavior_slice_id == active_slice["slice_id"])
    actions = [step.action for step in scenario.steps]

    assert "bootstrap_create_addressId" in actions
    assert "bootstrap_create_id" in actions
    assert any(action.startswith("permission_probe_") for action in actions)


def test_actorless_permission_slice_still_materializes_source_bound_probe(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = """
### GET /api/admin/reports
### POST /api/admin/reports

Request body:
```json
{"name":"quarterly","scope":"tenant"}
```
"""
    active_slice = {
        "slice_id": "permission-admin-report",
        "kind": "permission",
        "entity": "report",
        "_permission_method": "POST",
        "_permission_path": "/api/admin/reports",
        "_permission_expected_permitted": [],
        "source_refs": [{"source_type": "permission", "quote": "unapproved roles must not create reports"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=api_doc,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
        root=tmp_path,
        project="generic_reporting",
    )

    scenario = next(item for item in scenarios if item.behavior_slice_id == active_slice["slice_id"])

    assert scenario.category == "permission"
    assert scenario.execution_policy == "approved_sandbox_write"
    assert scenario.runtime_hints["permission_actor_binding"] == "runtime_role_sweep"
    assert scenario.steps[-1].api_method == "POST"
    assert scenario.steps[-1].api_path == "/api/admin/reports"


def test_inventory_write_bootstraps_nested_prerequisites_from_rich_project_source(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_inventory"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
### POST /api/inventory/reserve

Request body:
```json
{"sku":"SKU-001","qty":1,"orderId":"<order_id>"}
```

### GET /api/orders
### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"addressId":"<address_id>"}
```

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/inventory/reserve
### GET /api/orders
### POST /api/orders
### GET /api/users/addresses
### POST /api/users/addresses
"""
    active_slice = {
        "slice_id": "inventory-reserve",
        "kind": "inventory",
        "entity": "inventory",
        "_inventory_method": "POST",
        "_inventory_path": "/api/inventory/reserve",
        "_default_actor": "warehouse",
        "source_refs": [{"source_type": "api", "quote": "POST /api/inventory/reserve"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=compact_runtime_catalog,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
        root=tmp_path,
        project=project,
    )

    scenario = next(item for item in scenarios if item.behavior_slice_id == active_slice["slice_id"])
    actions = [step.action for step in scenario.steps]

    assert "bootstrap_create_addressId" in actions
    assert "bootstrap_create_orderId" in actions
    assert "inventory_probe_POST" in actions


def test_isolation_probe_bootstraps_owner_resource_from_rich_project_source(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_tenant_app"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
### POST /api/auth/login

Request body:
```json
{"email":"user@example.test","password":"secret"}
```

### GET /api/orders
### GET /api/orders/{id}
### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"addressId":"<address_id>"}
```

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/auth/login
### GET /api/orders
### GET /api/orders/{id}
### POST /api/orders
### GET /api/users/addresses
### POST /api/users/addresses
"""
    active_slice = {
        "slice_id": "isolation-order-detail",
        "kind": "isolation",
        "entity": "order",
        "_isolation_viewer_role": "viewer",
        "_isolation_viewer_email": "viewer@example.test",
        "_isolation_viewer_password": "secret",
        "_isolation_owner_role": "owner",
        "_isolation_owner_email": "owner@example.test",
        "_isolation_owner_password": "secret",
        "_isolation_path": "/api/orders/{id}",
        "_isolation_mode": "path",
        "_login_path": "/api/auth/login",
        "_login_body": {"email": "", "password": ""},
        "source_refs": [{"source_type": "api", "quote": "GET /api/orders/{id}"}],
    }

    scenarios = SemanticScenarioGenerator().generate(
        {},
        api_doc=compact_runtime_catalog,
        active_slice_ids={active_slice["slice_id"]},
        active_slices=[active_slice],
        allow_source_runtime=True,
        root=tmp_path,
        project=project,
    )

    scenario = next(item for item in scenarios if item.behavior_slice_id == active_slice["slice_id"])
    actions = [step.action for step in scenario.steps]
    prerequisite_writes = [
        step.api_path
        for step in scenario.steps
        if step.api_method == "POST" and not step.action.startswith("login")
    ]

    assert "/api/users/addresses" in prerequisite_writes
    assert "/api/orders" in prerequisite_writes
    assert prerequisite_writes.index("/api/users/addresses") < prerequisite_writes.index("/api/orders")
    assert any(action.startswith("isolation_probe_") for action in actions)


def test_invariant_runtime_upgrade_uses_rich_source_body_and_prerequisites(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_payments"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
### POST /api/payments/pay

Request body:
```json
{"orderId":"<order_id>","amount":100,"channel":"BALANCE","idempotencyKey":"request-1"}
```

### GET /api/payments/order/{orderId}
### GET /api/orders
### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"addressId":"<address_id>"}
```

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/payments/pay
### GET /api/payments/order/{orderId}
### GET /api/orders
### POST /api/orders
### GET /api/users/addresses
### POST /api/users/addresses
"""

    scenario = SemanticScenarioGenerator()._invariant_runtime_upgrade(
        "payment",
        "ACTIVE",
        "Duplicate payment requests must be idempotent and succeed only once.",
        [],
        1,
        slice_id="payment-idempotency",
        observation_path="/api/payments/order/{orderId}",
        api_doc=compact_runtime_catalog,
        root=tmp_path,
        project=project,
    )

    assert scenario is not None
    actions = [step.action for step in scenario.steps]
    write_steps = [step for step in scenario.steps if step.api_path == "/api/payments/pay"]

    assert "bootstrap_create_addressId" in actions
    assert "bootstrap_create_orderId" in actions
    assert len(write_steps) == 2
    assert all(step.body_template["orderId"] == "{orderId}" for step in write_steps)
    assert all(step.body_template["amount"] == 100 for step in write_steps)


def test_money_and_concurrency_slices_use_rich_project_source_body(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_shop"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
## Order

### POST /api/orders

Request body:
```json
{"items":[{"sku":"SKU-001","qty":1}],"couponCode":"PROMO-ONCE","addressId":"<address_id>"}
```

### GET /api/orders

### GET /api/users/addresses
### POST /api/users/addresses

Request body:
```json
{"receiver":"A User","phone":"10000000000","city":"Example City","detail":"Example Street"}
```
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/orders
### GET /api/orders
### GET /api/users/addresses
### POST /api/users/addresses
"""
    base_slice = {
        "entity": "order",
        "priority": 0.9,
        "source_refs": [{"source_type": "api", "quote": "POST /api/orders"}],
        "_default_actor": "buyer",
    }

    money = SemanticScenarioGenerator._money_slice(
        {
            **base_slice,
            "slice_id": "money-orders",
            "_money_method": "POST",
            "_money_path": "/api/orders",
        },
        1,
        compact_runtime_catalog,
        root=tmp_path,
        project=project,
    )
    concurrency = SemanticScenarioGenerator._concurrency_slice(
        {
            **base_slice,
            "slice_id": "concurrency-orders",
            "_concurrency_method": "POST",
            "_concurrency_path": "/api/orders",
        },
        1,
        compact_runtime_catalog,
        root=tmp_path,
        project=project,
    )

    assert money is not None
    assert concurrency is not None
    money_step = next(step for step in money.steps if step.action == "money_probe_POST")
    concurrency_step = next(step for step in concurrency.steps if step.action == "concurrent_POST_1")
    for step in (money_step, concurrency_step):
        assert step.body_provenance == "documented_example"
        assert step.body_template["items"] == [{"sku": "SKU-001", "qty": 1}]
        assert step.body_template["addressId"] == "{addressId}"
        assert "couponCode" not in step.body_template
    for scenario in (money, concurrency):
        assert any(step.action == "bootstrap_create_addressId" for step in scenario.steps)


def test_money_slice_bootstraps_placeholder_identity_from_rich_project_source(tmp_path) -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    project = "generic_finance"
    source_dir = tmp_path / "projects" / project / "input"
    source_dir.mkdir(parents=True)
    (source_dir / "API_SPEC.md").write_text(
        """
### POST /api/settlements

Request body:
```json
{"reference":"case-001","amount":10}
```

### GET /api/settlements/{id}
### POST /api/settlements/{id}/approve
""",
        encoding="utf-8",
    )
    compact_runtime_catalog = """
### POST /api/settlements
### GET /api/settlements/{id}
### POST /api/settlements/{id}/approve
"""

    scenario = SemanticScenarioGenerator._money_slice(
        {
            "slice_id": "money-settlement-approval",
            "entity": "settlement",
            "priority": 0.9,
            "source_refs": [{"source_type": "api", "quote": "POST /api/settlements/{id}/approve"}],
            "_default_actor": "operator",
            "_money_method": "POST",
            "_money_path": "/api/settlements/{id}/approve",
        },
        1,
        compact_runtime_catalog,
        root=tmp_path,
        project=project,
    )

    assert scenario is not None
    actions = [step.action for step in scenario.steps]
    assert "bootstrap_create_id" in actions
    bootstrap = next(step for step in scenario.steps if step.action == "bootstrap_create_id")
    assert bootstrap.api_path == "/api/settlements"
    assert bootstrap.body_template == {"reference": "case-001", "amount": 10}
    assert actions.index("bootstrap_create_id") < actions.index("observe_money_endpoint")


def test_resolve_entity_bootstraps_generic_id_from_collection_path() -> None:
    from pathlib import Path

    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    api_doc = Path("projects/benchmark_mall/input/API_SPEC.md").read_text(encoding="utf-8")
    steps, path = SemanticScenarioGenerator._resolve_entity_steps(
        "/api/orders/{id}/cancel", actor="buyer", start_order=1, api_doc=api_doc,
    )
    assert path == "/api/orders/{id}/cancel"
    assert any(s.api_method == "GET" and s.api_path == "/api/orders" for s in steps)
    bootstrap = next(s for s in steps if s.action == "bootstrap_create_id")
    assert bootstrap.api_method == "POST"
    assert bootstrap.api_path == "/api/orders"
    assert isinstance(bootstrap.body_template, dict) and bootstrap.body_template.get("addressId")


def test_enriched_api_doc_still_yields_request_bodies_and_bootstrap() -> None:
    from pathlib import Path

    from ai_test_asset_center.api_doc_assets import enrich_api_spec_text
    from ai_test_asset_center.auto_test_data_factory import _markdown_request_example
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    root = Path(".")
    raw = Path("projects/benchmark_mall/input/API_SPEC.md").read_text(encoding="utf-8")
    enriched = enrich_api_spec_text(root, "benchmark_mall", raw) or raw
    body = _markdown_request_example(enriched, "POST", "/api/orders")
    assert isinstance(body, dict) and body.get("addressId") == "<address_id>"
    steps, _ = SemanticScenarioGenerator._resolve_entity_steps(
        "/api/orders/{id}/cancel", actor="buyer", start_order=1, api_doc=enriched,
    )
    assert any(s.action == "bootstrap_create_id" and s.api_method == "POST" for s in steps)


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
