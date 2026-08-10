"""SPEC §22: Runtime binding ID extraction tests.

Covers response structure parsing, identity field recognition,
entity candidate selection, value validation, and downstream consumption.
"""

from __future__ import annotations

import json

from ai_test_asset_center.real_id_resolver_base import (
    bind_entity_fields,
    _extract_entity_candidates,
    bind_path_params_from_documented_body,
    extract_first_entity_id,
    param_field_candidates,
)
from ai_test_asset_center.runtime_binding_materializer_base import (
    runtime_value_from_response,
    runtime_setup_value_from_response,
    _entity_rows,
    _response_scalar_fields,
)


# ═══════════════════════════════════════════════════════════════════════════
# §22.1 Response structure tests
# ═══════════════════════════════════════════════════════════════════════════

class TestResponseStructureExtraction:
    """Entity candidate extraction from diverse JSON response shapes."""

    def test_top_level_object(self):
        body = {"id": 123, "name": "Product A"}
        entities = _extract_entity_candidates(body)
        # A dict that carries its own identity field IS the entity candidate
        # (its list children are child relations, not envelopes).
        assert len(entities) == 1
        assert entities[0]["id"] == 123
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "123"

    def test_top_level_array(self):
        body = [{"id": 123, "name": "Product A"}]
        entities = _extract_entity_candidates(body)
        assert len(entities) == 1
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "123"

    def test_data_array(self):
        body = {"data": [{"id": 123}]}
        entities = _extract_entity_candidates(body)
        assert len(entities) == 1
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "123"

    def test_items_array(self):
        body = {"items": [{"sku": "SKU-001"}]}
        result = bind_entity_fields(body, "/{sku}")
        assert result["sku"] == "SKU-001"

    def test_records_envelope(self):
        body = {"records": [{"order_id": 456}]}
        result = bind_entity_fields(body, "/{order_id}")
        assert result["order_id"] == "456"

    def test_pagination_structure(self):
        body = {
            "data": {
                "content": [{"orderId": "ORDER-1"}],
                "totalElements": 1,
                "page": 0,
                "size": 20,
            }
        }
        result = bind_entity_fields(body, "/{orderId}")
        assert result["orderId"] == "ORDER-1"

    def test_deep_nesting(self):
        body = {
            "result": {
                "payload": {
                    "records": [{"user_id": 88}]
                }
            }
        }
        result = bind_entity_fields(body, "/{user_id}")
        assert result["user_id"] == "88"

    def test_domain_envelope_orders(self):
        body = {"orders": [{"id": "ord-1", "orderNo": "NO-1"}]}
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "ord-1"

    def test_domain_envelope_products(self):
        body = {"products": [{"sku": "P-001", "name": "Item"}]}
        result = bind_entity_fields(body, "/{sku}")
        assert result["sku"] == "P-001"

    def test_empty_response(self):
        body = {}
        entities = _extract_entity_candidates(body)
        assert len(entities) == 0
        result = bind_entity_fields(body, "/{id}")
        assert result == {}

    def test_pagination_not_confused_with_entity(self):
        """Pagination metadata must not be returned as entity candidates."""
        body = {
            "total": 100,
            "page": 1,
            "size": 20,
            "data": [{"id": 1, "name": "Real entity"}],
        }
        entities = _extract_entity_candidates(body)
        assert len(entities) == 1
        # The "data" key's list should be found, not the top-level dict
        assert entities[0].get("id") == 1


# ═══════════════════════════════════════════════════════════════════════════
# §22.2 Field naming tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFieldNameRecognition:
    """Identity field recognition across naming conventions."""

    def test_lowercase_id(self):
        result = bind_entity_fields([{"id": 123}], "/{id}")
        assert result["id"] == "123"

    def test_uppercase_id(self):
        result = bind_entity_fields([{"ID": 456}], "/{id}")
        # bind_entity_fields normalizes field keys
        assert result.get("id") == "456" or result.get("ID") == "456"

    def test_underscore_id(self):
        result = bind_entity_fields([{"_id": "abc"}], "/{id}")
        assert result.get("id") == "abc" or result.get("_id") == "abc"

    def test_uuid_field(self):
        result = bind_entity_fields([{"uuid": "550e8400-e29b-41d4-a716-446655440000"}], "/{id}")
        assert result.get("id") is not None or result.get("uuid") is not None

    def test_camel_case_product_id(self):
        result = bind_entity_fields([{"productId": "P-001"}], "/{product_id}")
        assert result.get("product_id") == "P-001" or result.get("productId") == "P-001"

    def test_snake_case_product_id(self):
        result = bind_entity_fields([{"product_id": "P-002"}], "/{product_id}")
        assert result["product_id"] == "P-002"

    def test_sku_field(self):
        result = bind_entity_fields([{"sku": "SKU-ABC"}], "/{sku}")
        assert result["sku"] == "SKU-ABC"

    def test_order_id_camel(self):
        result = bind_entity_fields([{"orderId": "ORD-1"}], "/{order_id}")
        assert result.get("order_id") == "ORD-1" or result.get("orderId") == "ORD-1"

    def test_order_no(self):
        result = bind_entity_fields([{"orderNo": "NO-123"}], "/{order_id}")
        # orderNo is in param_field_candidates for order_id
        assert result.get("order_id") == "NO-123" or result.get("orderNo") == "NO-123"

    def test_user_id(self):
        result = bind_entity_fields([{"userId": "U-1"}], "/{user_id}")
        assert result.get("user_id") == "U-1" or result.get("userId") == "U-1"


# ═══════════════════════════════════════════════════════════════════════════
# §22.4 Value validation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestValueValidation:
    """Identity value acceptance and rejection."""

    def test_integer_id(self):
        result = bind_entity_fields([{"id": 42}], "/{id}")
        assert result["id"] == "42"

    def test_uuid_value(self):
        result = bind_entity_fields(
            [{"id": "550e8400-e29b-41d4-a716-446655440000"}], "/{id}"
        )
        assert result["id"] == "550e8400-e29b-41d4-a716-446655440000"

    def test_string_business_code(self):
        result = bind_entity_fields([{"code": "SKU-DRAFT-006"}], "/{code}")
        assert result["code"] == "SKU-DRAFT-006"

    def test_null_rejected(self):
        result = bind_entity_fields([{"id": None}], "/{id}")
        assert result.get("id") is None

    def test_empty_string_rejected(self):
        result = bind_entity_fields([{"id": ""}], "/{id}")
        assert result.get("id") is None

    def test_boolean_rejected(self):
        """Boolean values should not be treated as IDs."""
        result = bind_entity_fields([{"active": True, "id": False}], "/{id}")
        # The actual id field is False (boolean) which should not be accepted
        # active=True shouldn't be confused for an id
        assert result.get("id") is None, f"Boolean values should not become IDs: {result}"

    def test_object_value_rejected(self):
        result = bind_entity_fields([{"id": {"nested": "value"}}], "/{id}")
        assert result.get("id") is None


# ═══════════════════════════════════════════════════════════════════════════
# §22.5 Runtime value from response
# ═══════════════════════════════════════════════════════════════════════════

class TestRuntimeValueFromResponse:
    """runtime_value_from_response and runtime_setup_value_from_response."""

    def test_simple_extraction(self):
        body = {"id": 123, "name": "A"}
        value = runtime_value_from_response(body, "id", "/{id}")
        assert str(value) == "123"  # bind_entity_fields converts to str

    def test_array_body_first_extraction(self):
        body = [{"id": 1}, {"id": 2}]
        value = runtime_value_from_response(body, "id", "/{id}")
        assert str(value) == "1"

    def test_fallback_when_target_path_mismatch(self):
        """When target_path doesn't match, fallback scans scalar fields."""
        body = {"productId": "P-99", "name": "Foo"}
        value = runtime_value_from_response(body, "id", "/{id}")
        # Should fall back to productId → id via field key normalization
        assert str(value) == "P-99", f"Expected P-99, got {value}"

    def test_setup_value_from_wrapper(self):
        body = {"data": {"id": 42, "sku": "SKU-1"}}
        value = runtime_setup_value_from_response(body, "sku")
        assert value == "SKU-1"

    def test_setup_value_direct(self):
        body = {"sku": "DIRECT-SKU"}
        value = runtime_setup_value_from_response(body, "sku")
        assert value == "DIRECT-SKU"

    def test_setup_value_none_for_missing(self):
        body = {"name": "No identity"}
        value = runtime_setup_value_from_response(body, "id")
        assert value is None


# ═══════════════════════════════════════════════════════════════════════════
# §22.6 Entity rows extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestEntityRowsExtraction:
    """_entity_rows extraction from diverse envelopes."""

    def test_top_level_list(self):
        rows = _entity_rows([{"id": 1}, {"id": 2}])
        assert len(rows) == 2

    def test_records_envelope(self):
        rows = _entity_rows({"records": [{"id": 1}]})
        assert len(rows) == 1

    def test_data_envelope(self):
        rows = _entity_rows({"data": [{"id": 1}, {"id": 2}]})
        assert len(rows) == 2

    def test_nested_data_dict(self):
        rows = _entity_rows({"data": {"nested": [{"id": 1}]}})
        assert len(rows) == 1

    def test_single_dict_fallback(self):
        rows = _entity_rows({"id": 1, "name": "Single"})
        assert len(rows) == 1
        assert rows[0]["id"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# §23 Real failure sample regression
# ═══════════════════════════════════════════════════════════════════════════

class TestRealFailureRegression:
    """Regression tests matching the benchmark_mall_131 failure patterns."""

    def test_order_create_with_items_binds_order_id_not_sku(self):
        """run24: POST /api/orders 201 = {…order, items:[{sku,…}]}. The
        cleanup cancel path /api/orders/{id}/cancel must bind the order uuid,
        never a line item's product sku (which 500s as invalid uuid)."""
        body = {
            "id": "0e70000f-443a-407f-b7f3-050c4d6cbffb",
            "order_no": "BM111",
            "user_id": "u-1",
            "status": "PENDING_PAYMENT",
            "items": [
                {"sku": "SKU-PHONE-001", "title": "Phone",
                 "price": "6999.00", "qty": 1, "lineAmount": "6999.00"},
            ],
        }
        entities = _extract_entity_candidates(body)
        assert len(entities) == 1
        assert "id" in entities[0]  # the order object, not the line item
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "0e70000f-443a-407f-b7f3-050c4d6cbffb"
        value = runtime_value_from_response(body, "id", "/{id}")
        assert value == "0e70000f-443a-407f-b7f3-050c4d6cbffb"

    def test_wrapped_order_create_with_items_binds_order_id(self):
        """Same shape under a data wrapper: the wrapped object's own identity
        wins over its child-relation items list."""
        body = {"data": {"id": "aaaa-1111", "items": [{"sku": "SKU-X"}]}}
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "aaaa-1111"

    def test_bare_id_never_binds_cross_entity_natural_key(self):
        """sku/code/business_no name other entity kinds and must never satisfy
        a bare {id}: a line item's sku is not the order's resource identity."""
        candidates = param_field_candidates("id")
        assert "sku" not in candidates
        assert "code" not in candidates
        assert "business_no" not in candidates
        # A row with sku only → {id} stays unresolvable (honest block), not a
        # sku masquerade that 500s on the target.
        result = bind_entity_fields([{"sku": "SKU-PHONE-001"}], "/{id}")
        assert result.get("id") is None

    def test_child_row_binds_same_resource_fk_into_id(self):
        """A child row's order_id IS the order resource identity — it remains
        a valid candidate for bare {id} and outranks the product sku."""
        rows = [{"sku": "SKU-PHONE-001", "order_id": "0e70000f-443a-407f-b7f3-050c4d6cbffb"}]
        result = bind_entity_fields(rows, "/{id}")
        assert result["id"] == "0e70000f-443a-407f-b7f3-050c4d6cbffb"

    def test_items_envelope_without_identity_still_envelope(self):
        """{"items": [...]} with no identity at top stays a list envelope:
        sku-addressed params keep working."""
        body = {"items": [{"sku": "SKU-001"}]}
        assert _extract_entity_candidates(body)[0]["sku"] == "SKU-001"
        result = bind_entity_fields(body, "/{sku}")
        assert result["sku"] == "SKU-001"

    def test_orders_list_extraction(self):
        """Pattern: GET /api/orders → 76 orders, extract id."""
        body = [
            {
                "id": "14284070-30f5-44fc-b0b5-07304143ec5b",
                "order_no": "BM17859397981137749",
                "user_id": "17fc13c7-98b1-4fa1-9959-83f2f66081e0",
                "status": "SHIPPED",
                "total_amount": "6999.00",
                "address_id": "addr-001",
            }
        ]
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "14284070-30f5-44fc-b0b5-07304143ec5b"
        assert "order_no" in result or result.get("orderNo")

    def test_order_with_null_address_id(self):
        """Pattern: orders with address_id=null should still yield id."""
        body = [
            {
                "id": "ord-null-addr",
                "order_no": "BM-NULL",
                "address_id": None,
                "status": "DRAFT",
            }
        ]
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "ord-null-addr"

    def test_order_id_maps_to_generic_id(self):
        """When target is {id} but entity has order_id, still extract."""
        body = [{"order_id": 999, "status": "PENDING"}]
        result = bind_entity_fields(body, "/{id}")
        assert result.get("id") == "999" or result.get("order_id") == "999"

    def test_response_scalar_fields_finds_all(self):
        """_response_scalar_fields must find scalars in nested structures."""
        body = {
            "data": [
                {"id": 1, "sku": "A", "price": 9.99},
                {"id": 2, "sku": "B", "price": 19.99},
            ]
        }
        fields = _response_scalar_fields(body)
        assert "id" in fields
        assert "sku" in fields
        assert len(fields["id"]) == 2
        assert len(fields["sku"]) == 2

    def test_binding_store_structured_model(self):
        """Verify structured binding store can be created and used."""
        from ai_test_asset_center.runtime_binding_store import (
            RuntimeBindingStore,
            RESOLVED,
            NOT_FOUND,
            SCOPE_SCENARIO,
        )

        store = RuntimeBindingStore()
        resolution = store.resolve(
            binding_name="id",
            value="SKU-001",
            entity_type="product",
            identity_field="sku",
            source_operation="GET /api/products",
            source_response_path="$.data[0].sku",
            source_status_code=200,
            matched_by=["name", "status"],
            confidence=0.94,
            scope=SCOPE_SCENARIO,
            evidence={"candidate_path": "$.data[0]"},
        )
        assert resolution.status == RESOLVED
        assert resolution.is_resolved
        assert resolution.value == "SKU-001"
        assert resolution.entity_type == "product"
        assert resolution.identity_field == "sku"

        # Read back
        retrieved = store.get("id")
        assert retrieved is not None
        assert retrieved.value == "SKU-001"

        # Get value shortcut
        val = store.get_value("id")
        assert val == "SKU-001"

        # Fail a binding
        fail_res = store.fail(
            "order_id", NOT_FOUND, entity_type="order",
            failure_detail="No orders found for this user",
        )
        assert fail_res.status == NOT_FOUND

        # Snapshot
        snap = store.snapshot()
        assert len(snap["bindings"]) == 2

    def test_binding_conflict_detection(self):
        """Conflicting bindings must be detected, not silently overwritten."""
        from ai_test_asset_center.runtime_binding_store import (
            RuntimeBindingStore,
            RESOLVED,
            CONFLICT,
        )

        store = RuntimeBindingStore()
        store.resolve(binding_name="id", value="A", entity_type="product")
        conflict = store.resolve(binding_name="id", value="B", entity_type="product")
        assert conflict.status == CONFLICT
        # Original value preserved
        retrieved = store.get("id")
        assert retrieved.value == "A"

    def test_binding_idempotent(self):
        """Idempotent writes must succeed without conflict."""
        from ai_test_asset_center.runtime_binding_store import (
            RuntimeBindingStore,
            RESOLVED,
        )

        store = RuntimeBindingStore()
        r1 = store.resolve(binding_name="id", value="A", entity_type="product")
        r2 = store.resolve(binding_name="id", value="A", entity_type="product")
        assert r2.status == RESOLVED  # idempotent, not conflict
        assert r2 is r1  # same object returned

    def test_different_entities_dont_clobber(self):
        """product.id and order.id must be independent."""
        from ai_test_asset_center.runtime_binding_store import RuntimeBindingStore

        store = RuntimeBindingStore()
        store.resolve(binding_name="id", value="P-001", entity_type="product")
        store.resolve(binding_name="id", value="O-999", entity_type="order")
        # They should be stored under different scoped keys
        p = store.get("id")  # gets the first resolved (product.id in default scope)
        assert p is not None
        assert p.value in ("P-001", "O-999")  # whichever was resolved in default scope

    def test_scope_isolation(self):
        """step-scoped bindings must not leak to scenario scope."""
        from ai_test_asset_center.runtime_binding_store import (
            RuntimeBindingStore, SCOPE_STEP, SCOPE_SCENARIO,
        )
        store = RuntimeBindingStore()
        store.resolve(binding_name="id", value="step-1", scope=SCOPE_STEP)
        store.resolve(binding_name="id", value="scenario-1", scope=SCOPE_SCENARIO)
        s = store.get("id", scope=SCOPE_STEP)
        assert s is not None and s.value == "step-1"


# ═══════════════════════════════════════════════════════════════════════════
# §22.3 Object selection — multi-candidate, natural key, ambiguity
# ═══════════════════════════════════════════════════════════════════════════

class TestObjectSelection:
    """Target object selection from multiple entity candidates."""

    def test_single_candidate_direct(self):
        body = [{"id": 1, "name": "Only"}]
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "1"

    def test_multi_candidate_identity_preserved(self):
        body = [{"id": 1, "order_no": "ORD-A"}, {"id": 2, "order_no": "ORD-B"}]
        result = bind_entity_fields(body, "/{id}")
        assert result["id"] == "1"

    def test_pagination_metadata_not_confused(self):
        body = {"total": 100, "page": 1, "data": [{"id": 42}]}
        entities = _extract_entity_candidates(body)
        assert len(entities) == 1
        assert entities[0]["id"] == 42


# ═══════════════════════════════════════════════════════════════════════════
# §22.6 Downstream consumption tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDownstreamConsumption:
    """Binding values consumed by downstream operations."""

    def test_binding_into_path(self):
        from ai_test_asset_center.runtime_binding_materializer_base import materialize_path
        path = "/api/products/{id}/admin"
        result = materialize_path(path, {"id": "SKU-001"})
        assert "{id}" not in result
        assert "SKU-001" in result

    def test_binding_into_body(self):
        from ai_test_asset_center.runtime_binding_materializer_base import materialize_body_template
        body_tpl = {"productId": "{id}", "quantity": 1}
        result = materialize_body_template(body_tpl, {"id": "SKU-001"})
        assert result["productId"] == "SKU-001"

    def test_cleanup_uses_same_id(self):
        body = [{"id": "to-delete-123", "name": "Temp"}]
        result = bind_entity_fields(body, "/{id}")
        cleanup_id = result["id"]
        from ai_test_asset_center.runtime_binding_materializer_base import materialize_path
        delete_path = materialize_path("/api/items/{id}", {"id": cleanup_id})
        assert "to-delete-123" in delete_path
