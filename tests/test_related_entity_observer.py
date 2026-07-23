"""Unit tests for Related Entity Observer system.

Tests use industry-generic entity names (entity_a, entity_b, etc.)
to ensure no project-specific hardcoding.
"""
import pytest
from typing import Any

from ai_test_asset_center.related_entity_observer_binder import (
    discover_read_operations,
    bind_observer_plan,
    validate_collection_scope,
)
from ai_test_asset_center.related_entity_observer_executor import (
    extract_collection_from_response,
    detect_pagination_info,
    deduplicate_records,
)
from ai_test_asset_center.oracle_expression_resolver import (
    _build_observer_requirements,
    _detect_identity_fields,
)


# ─── Test Fixtures ───────────────────────────────────────────────────────────


def make_behavior_ir(
    entities: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a minimal Behavior IR for testing."""
    return {
        "entities": entities,
        "operations": operations,
        "relations": relations or [],
        "actors": [],
        "invariants": [],
    }


def make_entity(
    name: str,
    fields: list[str],
    entity_id: str = "",
) -> dict[str, Any]:
    """Create a test entity."""
    return {
        "id": entity_id or f"entity_{name}",
        "name": name,
        "field_list": fields,
    }


def make_operation(
    op_id: str,
    method: str,
    path: str,
    read_write: str = "read",
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a test operation."""
    return {
        "id": op_id,
        "method": method,
        "path": path,
        "read_write": read_write,
        "parameters": parameters or [],
    }


# ─── Test A: Parent entity with child collection ─────────────────────────────


class TestParentChildCollection:
    """Test A: entity_a.limit >= SUM(entity_b.value)"""

    def test_observer_requirements_generation(self):
        """Related entity with MANY cardinality gets collection_requirements."""
        entities = {
            "entity_a": {"entity_id": "ent_a", "field_list": ["id", "limit", "tenant_id"]},
            "entity_b": {"entity_id": "ent_b", "field_list": ["id", "value", "parent_id", "tenant_id"]},
        }
        related_entities = {
            "entity_b": {
                "entity_id": "ent_b",
                "cardinality": "MANY",
                "relation_key": "parent_id",
                "fields": ["value"],
            }
        }
        involved_entities = {
            "entity_a": {"entity_id": "ent_a", "fields": ["limit"]},
            "entity_b": {"entity_id": "ent_b", "fields": ["value"]},
        }

        reqs = _build_observer_requirements(
            root_entity="entity_a",
            related_entities=related_entities,
            involved_entities=involved_entities,
            expression_type="conservation",
            entities=entities,
        )

        # Should have root and related requirements
        assert len(reqs) == 2

        # Root requirement
        root_req = next(r for r in reqs if r["entity_alias"] == "root")
        assert root_req["entity_name"] == "entity_a"
        assert root_req["cardinality"] == "ONE"
        assert root_req["snapshot"] == "BEFORE_AND_AFTER"

        # Related requirement
        related_req = next(r for r in reqs if r["entity_alias"] == "related_a")
        assert related_req["entity_name"] == "entity_b"
        assert related_req["cardinality"] == "MANY"
        assert "collection_requirements" in related_req
        assert related_req["collection_requirements"]["pagination_required"] is True
        assert related_req["collection_requirements"]["empty_collection_policy"] == "INDETERMINATE"

    def test_operation_discovery(self):
        """Discover LIST operation for related entity."""
        operations = [
            make_operation("op_list_b", "GET", "/api/v1/entity-b"),
            make_operation("op_read_a", "GET", "/api/v1/entity-a/{id}"),
            make_operation("op_create_b", "POST", "/api/v1/entity-b", read_write="write"),
        ]

        candidates = discover_read_operations(
            "entity_b",
            operations,
            required_fields=["value"],
            relation_key="parent_id",
        )

        # Should find the LIST operation
        assert len(candidates) >= 1
        best = candidates[0]
        assert best["operation_id"] == "op_list_b"
        assert best["score"] > 0

    def test_parameter_binding(self):
        """Bind relation_key to query parameter."""
        operations = [
            make_operation(
                "op_list_b",
                "GET",
                "/api/v1/entity-b",
                parameters=[
                    {"name": "parent_id", "in": "query"},
                    {"name": "tenant_id", "in": "query"},
                ],
            ),
        ]
        behavior_ir = make_behavior_ir(
            entities=[make_entity("entity_b", ["id", "value", "parent_id"])],
            operations=operations,
        )

        observer_reqs = [{
            "entity_alias": "related_a",
            "entity_name": "entity_b",
            "entity_id": "ent_b",
            "cardinality": "MANY",
            "relation_key": "parent_id",
            "required_fields": ["value"],
            "scope_fields": ["tenant_id"],
            "identity_fields": ["id"],
            "snapshot": "BEFORE_AND_AFTER",
        }]

        plan = bind_observer_plan(
            observer_reqs,
            behavior_ir,
            root_identity_value="root-123",
            tenant_scope_values={"tenant_id": "tenant-456"},
        )

        assert len(plan["related_observers"]) == 1
        observer = plan["related_observers"][0]
        assert observer["status"] == "BOUND"
        assert observer["relation_bound"] is True

        # Check parameter bindings
        bindings = observer["parameter_bindings"]
        parent_binding = next((b for b in bindings if b.get("canonical_field_id") == "parent_id"), None)
        assert parent_binding is not None
        assert parent_binding["bound_value"] == "root-123"


# ─── Test B: Before/After collection change ──────────────────────────────────


class TestBeforeAfterCollection:
    """Test B: SUM(AFTER(entity_b.value)) = SUM(BEFORE(entity_b.value)) + delta"""

    def test_snapshot_requirement(self):
        """Conservation expression requires BEFORE_AND_AFTER snapshot."""
        entities = {
            "entity_a": {"entity_id": "ent_a", "field_list": ["id", "amount"]},
            "entity_b": {"entity_id": "ent_b", "field_list": ["id", "value", "parent_id"]},
        }
        related_entities = {
            "entity_b": {
                "entity_id": "ent_b",
                "cardinality": "MANY",
                "relation_key": "parent_id",
                "fields": ["value"],
            }
        }

        reqs = _build_observer_requirements(
            root_entity="entity_a",
            related_entities=related_entities,
            involved_entities={"entity_a": {"fields": ["amount"]}},
            expression_type="conservation",
            entities=entities,
        )

        for req in reqs:
            assert req["snapshot"] == "BEFORE_AND_AFTER"


# ─── Test C: State consistency ───────────────────────────────────────────────


class TestStateConsistency:
    """Test C: entity_a.state = state_3 → ALL(entity_b.state IN [state_4, state_5])"""

    def test_collection_extraction(self):
        """Extract records from various response formats."""
        # Direct array
        assert extract_collection_from_response([{"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]

        # Wrapped in data
        assert extract_collection_from_response({"data": [{"id": 1}]}) == [{"id": 1}]

        # Wrapped in records
        assert extract_collection_from_response({"records": [{"id": 1}]}) == [{"id": 1}]

        # Nested
        assert extract_collection_from_response({"data": {"items": [{"id": 1}]}}) == [{"id": 1}]

        # Empty
        assert extract_collection_from_response({}) == []
        assert extract_collection_from_response({"data": []}) == []


# ─── Test D: Pagination ──────────────────────────────────────────────────────


class TestPagination:
    """Test D: Related records span multiple pages."""

    def test_page_based_pagination_detection(self):
        """Detect page-based pagination metadata."""
        body = {
            "data": [{"id": i} for i in range(10)],
            "page": 1,
            "totalPages": 3,
            "total": 30,
        }
        info = detect_pagination_info(body)
        assert info["has_more"] is True
        assert info["next_page"] == 2
        assert info["total_count"] == 30
        assert info["current_count"] == 10

    def test_offset_based_pagination_detection(self):
        """Detect offset-based pagination metadata."""
        body = {
            "items": [{"id": i} for i in range(20)],
            "offset": 0,
            "limit": 20,
            "total": 100,
        }
        info = detect_pagination_info(body)
        assert info["has_more"] is True
        assert info["next_offset"] == 20

    def test_cursor_based_pagination_detection(self):
        """Detect cursor-based pagination metadata."""
        body = {
            "records": [{"id": i} for i in range(10)],
            "nextCursor": "abc123",
        }
        info = detect_pagination_info(body)
        assert info["has_more"] is True
        assert info["next_cursor"] == "abc123"

    def test_no_more_pages(self):
        """Detect when no more pages exist."""
        body = {
            "data": [{"id": 1}],
            "page": 3,
            "totalPages": 3,
        }
        info = detect_pagination_info(body)
        assert info["has_more"] is False


# ─── Test E: Empty collection ────────────────────────────────────────────────


class TestEmptyCollection:
    """Test E: Empty collection policies (PASS, FAIL, INDETERMINATE)."""

    def test_deduplication(self):
        """Deduplicate records by identity fields."""
        records = [
            {"id": 1, "value": 10},
            {"id": 2, "value": 20},
            {"id": 1, "value": 15},  # Duplicate
            {"id": 3, "value": 30},
        ]
        deduped, dup_count = deduplicate_records(records, ["id"])
        assert len(deduped) == 3
        assert dup_count == 1

    def test_deduplication_composite_key(self):
        """Deduplicate by composite identity fields."""
        records = [
            {"tenant_id": "t1", "code": "A", "value": 10},
            {"tenant_id": "t1", "code": "B", "value": 20},
            {"tenant_id": "t1", "code": "A", "value": 15},  # Duplicate
        ]
        deduped, dup_count = deduplicate_records(records, ["tenant_id", "code"])
        assert len(deduped) == 2
        assert dup_count == 1


# ─── Test F: Scope pollution ─────────────────────────────────────────────────


class TestScopeValidation:
    """Test F: Different tenants with same parent_id - only read current tenant."""

    def test_scope_validation_pass(self):
        """All records match expected scope."""
        records = [
            {"id": 1, "tenant_id": "t1", "value": 10},
            {"id": 2, "tenant_id": "t1", "value": 20},
        ]
        result = validate_collection_scope(
            records,
            scope_fields=["tenant_id"],
            expected_scope_values={"tenant_id": "t1"},
        )
        assert result["valid"] is True
        assert len(result["mismatched_records"]) == 0

    def test_scope_validation_fail(self):
        """Records from different tenant detected."""
        records = [
            {"id": 1, "tenant_id": "t1", "value": 10},
            {"id": 2, "tenant_id": "t2", "value": 20},  # Wrong tenant
        ]
        result = validate_collection_scope(
            records,
            scope_fields=["tenant_id"],
            expected_scope_values={"tenant_id": "t1"},
        )
        assert result["valid"] is False
        assert result["reason"] == "OBSERVER_SCOPE_MISMATCH"
        assert len(result["mismatched_records"]) == 1

    def test_scope_validation_no_scope_fields(self):
        """No scope fields - validation passes."""
        records = [{"id": 1, "value": 10}]
        result = validate_collection_scope(
            records,
            scope_fields=[],
            expected_scope_values={},
        )
        assert result["valid"] is True


# ─── Identity Field Detection ────────────────────────────────────────────────


class TestIdentityFieldDetection:
    """Test identity field detection for deduplication."""

    def test_detect_id_field(self):
        entities = {"entity_a": {"field_list": ["id", "name", "value"]}}
        fields = _detect_identity_fields("entity_a", entities)
        assert "id" in fields

    def test_detect_uuid_field(self):
        entities = {"entity_a": {"field_list": ["uuid", "name"]}}
        fields = _detect_identity_fields("entity_a", entities)
        assert "uuid" in fields

    def test_detect_code_field(self):
        entities = {"entity_a": {"field_list": ["code", "name"]}}
        fields = _detect_identity_fields("entity_a", entities)
        assert "code" in fields

    def test_default_to_id(self):
        entities = {"entity_a": {"field_list": ["name", "value"]}}
        fields = _detect_identity_fields("entity_a", entities)
        assert fields == ["id"]  # Default


# ─── Anti-Hardcoding Verification ────────────────────────────────────────────


class TestAntiHardcoding:
    """Verify no project-specific names in production code."""

    def test_no_project_specific_names_in_binder(self):
        """Binder module should not contain project-specific entity names."""
        import inspect
        from ai_test_asset_center import related_entity_observer_binder

        source = inspect.getsource(related_entity_observer_binder)
        forbidden = ["audit_logs", "budgets", "contracts", "payments", "milestones", "invoices", "ContractFlow"]
        for name in forbidden:
            assert name.lower() not in source.lower(), f"Found forbidden name: {name}"

    def test_no_project_specific_names_in_executor(self):
        """Executor module should not contain project-specific entity names."""
        import inspect
        from ai_test_asset_center import related_entity_observer_executor

        source = inspect.getsource(related_entity_observer_executor)
        forbidden = ["audit_logs", "budgets", "contracts", "payments", "milestones", "invoices", "ContractFlow"]
        for name in forbidden:
            assert name.lower() not in source.lower(), f"Found forbidden name: {name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
