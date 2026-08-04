"""Binding System Unit Tests — 53 test cases covering all binding dimensions.

Test categories:
- Binding Model (10 tests)
- Entity & Operation (5 tests)
- Field (9 tests)
- Relation (5 tests)
- Actor & State (6 tests)
- Fixture & Observer (5 tests)
- Oracle Input (7 tests)
- Runtime Probe (6 tests)

Run: python -m pytest test_binding_unit.py -v
"""
import sys
import time
import pytest

sys.path.insert(0, ".")

from ai_test_asset_center.binding_ledger import (
    BindingLedger, BindingStatus, BINDING_TYPES,
    create_binding_edge, transition_binding, can_transition, confidence_gate,
)
from ai_test_asset_center.binding_evidence import (
    EVIDENCE_DIMENSIONS, create_evidence, compute_composite_confidence,
    evaluate_binding_evidence, collect_semantic_name_evidence,
    collect_entity_context_evidence, collect_data_type_evidence,
    collect_operation_context_evidence, collect_schema_relation_evidence,
    collect_runtime_behavior_evidence,
)
from ai_test_asset_center.binding_conflict_resolver import (
    detect_conflicts, resolve_conflict, detect_and_resolve_all,
)
from ai_test_asset_center.binding_completeness_gate import (
    check_binding_completeness, gate_or_block,
)
from ai_test_asset_center.binding_builder import (
    build_all_bindings, classify_field_type,
)
from ai_test_asset_center.field_level_golden_rules import (
    GOLDEN_RULES, validate_golden_rules, get_golden_rules,
)


# ─── Test Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def ledger():
    return BindingLedger(project_id="test")


@pytest.fixture
def sample_ir():
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "entities": [
            {"id": "ent_order", "name": "Order", "collection_path": "/api/orders", "alternate_paths": []},
            {"id": "ent_item", "name": "OrderItem", "collection_path": "/api/order-items", "alternate_paths": []},
        ],
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/api/orders", "read_write": "write",
             "source_refs": [{"source_id": "s1"}],
             "request_schema": {"properties": {"item_id": {"type": "string"}, "quantity": {"type": "integer"}, "status": {"type": "string", "enum": ["draft", "confirmed"]}}}},
            {"id": "op_list", "method": "GET", "path": "/api/orders", "read_write": "read",
             "source_refs": [{"source_id": "s1"}],
             "response_schema": {"type": "array", "items": {"properties": {"id": {"type": "string"}, "status": {"type": "string"}}}}},
            {"id": "op_get", "method": "GET", "path": "/api/orders/{id}", "read_write": "read", "source_refs": []},
            {"id": "op_delete", "method": "DELETE", "path": "/api/orders/{id}", "read_write": "write", "source_refs": []},
        ],
        "actors": [
            {"id": "actor_admin", "role": "admin", "credential_secret_ref": "secret:admin",
             "account_ref": "acc_1", "runtime_bound": True, "tenant_scope": "t1",
             "allowed_resources": ["orders"], "allowed_actions": ["*"]},
        ],
        "states": [
            {"id": "state_status", "entity_ref": "ent_order", "field": "status",
             "values": ["draft", "confirmed", "shipped"], "initial": "draft"},
        ],
        "relations": [
            {"id": "rel_order_item", "relation_type": "consumes", "from_ref": "ent_order", "to_ref": "ent_item",
             "operation_ref": "op_create", "correlation_key": "item_id",
             "source_refs": [{"source_id": "s1"}], "preconditions": [], "effects": []},
        ],
        "invariants": [
            {"id": "inv_qty", "invariant_type": "conservation", "terms": [{"field": "quantity"}],
             "source_refs": [{"source_id": "s1"}]},
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BINDING MODEL TESTS (10)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBindingModel:
    def test_01_binding_types_complete(self):
        """All 10 binding types are defined."""
        assert len(BINDING_TYPES) == 10
        expected = {"entity", "operation", "field", "relation", "state",
                    "actor", "scope", "fixture", "observer", "oracle_input"}
        assert BINDING_TYPES == expected

    def test_02_status_enum_complete(self):
        """All 7 states are defined."""
        states = [s.value for s in BindingStatus]
        assert len(states) == 7
        assert "CANDIDATE" in states
        assert "EXECUTABLE" in states
        assert "CONFLICTED" in states

    def test_03_create_binding_edge(self):
        """Create a binding edge with correct defaults."""
        edge = create_binding_edge(
            binding_type="entity",
            source_node_id="ent_1",
            target_key="/api/orders",
        )
        assert edge["binding_type"] == "entity"
        assert edge["status"] == "CANDIDATE"
        assert edge["version"] == 1
        assert edge["confidence"] == 0.0
        assert edge["binding_id"].startswith("bind_")

    def test_04_invalid_binding_type_rejected(self):
        """Invalid binding type raises ValueError."""
        with pytest.raises(ValueError, match="invalid_binding_type"):
            create_binding_edge(binding_type="invalid", source_node_id="x", target_key="y")

    def test_05_state_transition_valid(self):
        """Valid state transitions succeed."""
        assert can_transition(BindingStatus.CANDIDATE, BindingStatus.HIGH_CONFIDENCE)
        assert can_transition(BindingStatus.HIGH_CONFIDENCE, BindingStatus.EXECUTABLE)
        assert can_transition(BindingStatus.RUNTIME_CONFIRMED, BindingStatus.EXECUTABLE)

    def test_06_state_transition_invalid(self):
        """Invalid state transitions are rejected."""
        assert not can_transition(BindingStatus.CANDIDATE, BindingStatus.EXECUTABLE)
        assert not can_transition(BindingStatus.EXECUTABLE, BindingStatus.CANDIDATE)
        assert not can_transition(BindingStatus.REJECTED, BindingStatus.EXECUTABLE)

    def test_07_transition_binding_immutable(self):
        """transition_binding returns new dict, doesn't mutate original."""
        edge = create_binding_edge(binding_type="entity", source_node_id="e1", target_key="t1")
        updated = transition_binding(edge, BindingStatus.HIGH_CONFIDENCE, reason="test")
        assert edge["status"] == "CANDIDATE"
        assert updated["status"] == "HIGH_CONFIDENCE"
        assert updated["version"] == 2

    def test_08_confidence_gate_classification(self):
        """Confidence gate classifies correctly."""
        assert confidence_gate(0.95) == "high_confidence"
        assert confidence_gate(0.90) == "high_confidence"
        assert confidence_gate(0.80) == "needs_probe"
        assert confidence_gate(0.70) == "needs_probe"
        assert confidence_gate(0.50) == "unusable"

    def test_09_ledger_insert_and_get(self, ledger):
        """Ledger insert and retrieval works."""
        edge = create_binding_edge(binding_type="entity", source_node_id="e1", target_key="t1")
        ledger.insert(edge)
        assert ledger.size == 1
        retrieved = ledger.get(edge["binding_id"])
        assert retrieved["binding_id"] == edge["binding_id"]

    def test_10_ledger_export_load_roundtrip(self, ledger):
        """Ledger export/load preserves data."""
        edge = create_binding_edge(binding_type="field", source_node_id="f1", target_key="name")
        ledger.insert(edge)
        exported = ledger.export()
        new_ledger = BindingLedger(project_id="test2")
        new_ledger.load(exported)
        assert new_ledger.size == 1
        assert new_ledger.get(edge["binding_id"]) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# ENTITY & OPERATION TESTS (5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntityOperation:
    def test_11_entity_binding_created(self, ledger, sample_ir):
        """Entity bindings are created from IR entities."""
        build_all_bindings(sample_ir, ledger)
        entity_bindings = ledger.get_by_type("entity")
        assert len(entity_bindings) == 2  # ent_order, ent_item

    def test_12_entity_binding_metadata(self, ledger, sample_ir):
        """Entity binding has correct metadata."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="entity", source_node_id="ent_order")
        assert len(bindings) == 1
        meta = bindings[0]["metadata"]
        assert meta["collection_path"] == "/api/orders"
        assert meta["create_operation_ref"] == "op_create"
        assert meta["read_operation_ref"] == "op_list"

    def test_13_operation_binding_created(self, ledger, sample_ir):
        """Operation bindings are created for all IR operations."""
        build_all_bindings(sample_ir, ledger)
        op_bindings = ledger.get_by_type("operation")
        assert len(op_bindings) == 4

    def test_14_operation_binding_target_key(self, ledger, sample_ir):
        """Operation binding target_key is METHOD PATH format."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="operation", source_node_id="op_create")
        assert len(bindings) == 1
        assert bindings[0]["target_key"] == "POST /api/orders"

    def test_15_operation_binding_has_placeholders(self, ledger, sample_ir):
        """Operation binding detects path placeholders."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="operation", source_node_id="op_get")
        assert bindings[0]["metadata"]["has_placeholders"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD TESTS (9)
# ═══════════════════════════════════════════════════════════════════════════════

class TestField:
    def test_16_field_binding_created(self, ledger, sample_ir):
        """Field bindings are created from operation schemas."""
        build_all_bindings(sample_ir, ledger)
        field_bindings = ledger.get_by_type("field")
        assert len(field_bindings) >= 3  # item_id, quantity, status

    def test_17_classify_identity_field(self):
        """ID fields classified as IDENTITY."""
        assert classify_field_type("id") == "IDENTITY"
        assert classify_field_type("order_id") == "FOREIGN_KEY"
        assert classify_field_type("uuid") == "IDENTITY"

    def test_18_classify_state_field(self):
        """Status fields classified as STATE."""
        assert classify_field_type("status") == "STATE"
        assert classify_field_type("order_state") == "STATE"

    def test_19_classify_money_field(self):
        """Money fields classified correctly."""
        assert classify_field_type("total_price") == "MONEY"
        assert classify_field_type("unit_cost") == "MONEY"

    def test_20_classify_temporal_field(self):
        """Temporal fields classified correctly."""
        assert classify_field_type("created_at") == "TEMPORAL"
        assert classify_field_type("deadline") == "TEMPORAL"

    def test_21_classify_quantity_field(self):
        """Quantity fields classified correctly."""
        assert classify_field_type("quantity") == "QUANTITY_BALANCE"
        assert classify_field_type("stock_count") == "QUANTITY_BALANCE"

    def test_22_classify_boolean_field(self):
        """Boolean fields classified correctly."""
        assert classify_field_type("is_active") == "BOOLEAN_FLAG"
        assert classify_field_type("has_children") == "BOOLEAN_FLAG"

    def test_23_classify_with_schema(self):
        """Schema-based classification works."""
        assert classify_field_type("flag", {"type": "boolean"}) == "BOOLEAN_FLAG"
        assert classify_field_type("items", {"type": "array"}) == "ARRAY_COLLECTION"
        assert classify_field_type("level", {"type": "string", "enum": ["a", "b"]}) == "ENUM_STATUS"

    def test_24_field_binding_has_type(self, ledger, sample_ir):
        """Field binding metadata includes type classification."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.get_by_type("field")
        types_found = {b["metadata"]["field_type_classification"] for b in bindings}
        assert "FOREIGN_KEY" in types_found or "QUANTITY_BALANCE" in types_found


# ═══════════════════════════════════════════════════════════════════════════════
# RELATION TESTS (5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelation:
    def test_25_relation_binding_created(self, ledger, sample_ir):
        """Relation bindings are created from IR relations."""
        build_all_bindings(sample_ir, ledger)
        rel_bindings = ledger.get_by_type("relation")
        assert len(rel_bindings) >= 1

    def test_26_relation_binding_metadata(self, ledger, sample_ir):
        """Relation binding has correct metadata."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="relation", source_node_id="rel_order_item")
        assert len(bindings) == 1
        meta = bindings[0]["metadata"]
        assert meta["source_entity_ref"] == "ent_order"
        assert meta["target_entity_ref"] == "ent_item"
        assert meta["relation_type"] == "consumes"

    def test_27_relation_correlation_key(self, ledger, sample_ir):
        """Relation binding preserves the exact source correlation key."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="relation", source_node_id="rel_order_item")
        assert bindings[0]["metadata"]["correlation_key"] == "item_id"

    def test_relation_without_declared_correlation_key_stays_unresolved(self):
        from ai_test_asset_center.binding_builder import _declared_correlation_key

        assert _declared_correlation_key({
            "relation_type": "owns",
            "from_ref": "parent",
            "to_ref": "child",
        }) == ""

    def test_28_relation_evidence_score(self, ledger, sample_ir):
        """Relation binding has schema_relation evidence."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="relation", source_node_id="rel_order_item")
        evidence_dims = {e["dimension"] for e in bindings[0]["evidence"]}
        assert "schema_relation" in evidence_dims

    def test_29_relation_materialization_op(self, ledger, sample_ir):
        """Relation binding links to materialization operation."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="relation", source_node_id="rel_order_item")
        assert bindings[0]["metadata"]["materialization_operation_ref"] == "op_create"


# ═══════════════════════════════════════════════════════════════════════════════
# ACTOR & STATE TESTS (6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestActorState:
    def test_30_actor_binding_created(self, ledger, sample_ir):
        """Actor bindings are created from IR actors."""
        build_all_bindings(sample_ir, ledger)
        actor_bindings = ledger.get_by_type("actor")
        assert len(actor_bindings) == 1

    def test_31_actor_binding_metadata(self, ledger, sample_ir):
        """Actor binding has credential and role info."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="actor", source_node_id="actor_admin")
        meta = bindings[0]["metadata"]
        assert meta["role"] == "admin"
        assert meta["credential_secret_ref"] == "secret:admin"

    def test_32_actor_runtime_bound_evidence(self, ledger, sample_ir):
        """Runtime-bound actor gets runtime_behavior evidence."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="actor", source_node_id="actor_admin")
        evidence_dims = {e["dimension"] for e in bindings[0]["evidence"]}
        assert "runtime_behavior" in evidence_dims

    def test_33_state_binding_created(self, ledger, sample_ir):
        """State bindings are created from IR states."""
        build_all_bindings(sample_ir, ledger)
        state_bindings = ledger.get_by_type("state")
        assert len(state_bindings) == 1

    def test_34_state_binding_metadata(self, ledger, sample_ir):
        """State binding has field name and values."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="state", source_node_id="state_status")
        meta = bindings[0]["metadata"]
        assert meta["state_field_name"] == "status"
        assert "draft" in meta["raw_values"]

    def test_35_state_binding_entity_ref(self, ledger, sample_ir):
        """State binding references correct entity."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="state", source_node_id="state_status")
        assert bindings[0]["metadata"]["entity_ref"] == "ent_order"


# ═══════════════════════════════════════════════════════════════════════════════
# STATE DIMENSION GATE TESTS — regression for business-state-literal blocking
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateDimensionGate:
    """Regression: a business state literal (``confirmed``, ``PAID``, …) is a
    transition *value*, not an IR node identity. The gate must not exact-match
    it against ``bir_`` node ids, or every state-family obligation is blocked."""

    @staticmethod
    def _state_obligation(state_ref):
        return {"risk_family": "state", "property": {"state_ref": state_ref}}

    @staticmethod
    def _insert_state_binding(ledger, status):
        edge = create_binding_edge(
            binding_type="state",
            source_node_id="bir_state_order_status",
            target_key="status",
        )
        edge["status"] = status
        ledger.insert(edge)
        return edge

    def test_state_literal_with_executable_binding_passes(self, ledger):
        """Executable state binding present → the literal passes the gate."""
        self._insert_state_binding(ledger, BindingStatus.EXECUTABLE.value)
        passed, reason = gate_or_block(
            ledger,
            obligation=self._state_obligation("confirmed"),
            behavior_ir={"operations": [], "entities": [], "states": []},
        )
        assert passed, f"state dimension blocked unexpectedly: {reason}"

    def test_state_literal_without_executable_binding_stays_blocked(self, ledger):
        """Fail-closed: only a CANDIDATE (non-executable) binding → still blocks."""
        self._insert_state_binding(ledger, BindingStatus.CANDIDATE.value)
        passed, reason = gate_or_block(
            ledger,
            obligation=self._state_obligation("confirmed"),
            behavior_ir={"operations": [], "entities": [], "states": []},
        )
        assert not passed
        assert "BINDING_GATE_BLOCKED" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURE & OBSERVER TESTS (5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureObserver:
    def test_36_fixture_binding_created(self, ledger, sample_ir):
        """Fixture bindings are created for entities with POST ops."""
        build_all_bindings(sample_ir, ledger)
        fixture_bindings = ledger.get_by_type("fixture")
        assert len(fixture_bindings) >= 1

    def test_37_fixture_binding_has_create_op(self, ledger, sample_ir):
        """Fixture binding references create operation."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.get_by_type("fixture")
        order_fixture = [b for b in bindings if b["source_node_id"] == "ent_order"]
        assert len(order_fixture) == 1
        assert order_fixture[0]["metadata"]["create_operation_ref"] == "op_create"

    def test_38_fixture_binding_has_cleanup(self, ledger, sample_ir):
        """Fixture binding includes cleanup operations."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.get_by_type("fixture")
        order_fixture = [b for b in bindings if b["source_node_id"] == "ent_order"]
        assert "op_delete" in order_fixture[0]["metadata"]["cleanup_operations"]

    def test_39_observer_binding_created(self, ledger, sample_ir):
        """Observer bindings are created for entities with GET ops."""
        build_all_bindings(sample_ir, ledger)
        observer_bindings = ledger.get_by_type("observer")
        assert len(observer_bindings) >= 1

    def test_40_observer_binding_has_fields(self, ledger, sample_ir):
        """Observer binding lists observable fields."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.get_by_type("observer")
        order_observers = [b for b in bindings if b["source_node_id"] == "ent_order"]
        assert len(order_observers) >= 1
        # At least one observer should have observed fields (from response schema)
        all_fields = []
        for obs in order_observers:
            all_fields.extend(obs["metadata"]["observed_fields"])
        assert len(all_fields) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ORACLE INPUT TESTS (7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOracleInput:
    def test_41_oracle_input_binding_created(self, ledger, sample_ir):
        """Oracle input bindings are created from invariants."""
        build_all_bindings(sample_ir, ledger)
        oracle_bindings = ledger.get_by_type("oracle_input")
        assert len(oracle_bindings) >= 1

    def test_42_oracle_input_has_field_bindings(self, ledger, sample_ir):
        """Oracle input binding lists required fields."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="oracle_input", source_node_id="inv_qty")
        assert len(bindings) == 1
        assert "quantity" in bindings[0]["metadata"]["input_field_bindings"]

    def test_43_oracle_input_has_observer_refs(self, ledger, sample_ir):
        """Oracle input binding links to observer operations."""
        build_all_bindings(sample_ir, ledger)
        bindings = ledger.find(binding_type="oracle_input", source_node_id="inv_qty")
        # May or may not have observer refs depending on schema
        assert "source_observer_refs" in bindings[0]["metadata"]

    def test_44_golden_rules_count(self):
        """Golden rule set has at least 20 rules."""
        assert len(GOLDEN_RULES) >= 20

    def test_45_golden_rules_categories(self):
        """Golden rules meet category minimums."""
        v = validate_golden_rules()
        assert v["causal_count"] >= 8
        assert v["state_count"] >= 6
        assert v["conservation_count"] >= 6

    def test_46_golden_rules_no_empty_terms(self):
        """No golden rule has empty terms."""
        v = validate_golden_rules()
        assert v["empty_terms_count"] == 0

    def test_47_golden_rules_all_have_field_type(self):
        """Every golden rule term has a field_type."""
        for rule in GOLDEN_RULES:
            for term in rule["terms"]:
                assert "field_type" in term, f"Rule {rule['rule_id']} term missing field_type"


# ═══════════════════════════════════════════════════════════════════════════════
# RUNTIME PROBE TESTS (6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRuntimeProbe:
    def test_48_evidence_dimensions_complete(self):
        """All 8 evidence dimensions are defined."""
        assert len(EVIDENCE_DIMENSIONS) == 8

    def test_49_composite_confidence_empty(self):
        """Empty evidence gives 0 confidence."""
        assert compute_composite_confidence([]) == 0.0

    def test_50_composite_confidence_single(self):
        """Single high-score evidence gives reasonable confidence."""
        evidence = [create_evidence(dimension="semantic_name", score=1.0, detail="exact")]
        conf = compute_composite_confidence(evidence)
        assert 0.0 < conf <= 1.0

    def test_51_evaluate_binding_evidence(self):
        """Evidence evaluation returns gate classification."""
        evidence = [
            create_evidence(dimension="semantic_name", score=0.95, detail="match"),
            create_evidence(dimension="entity_context", score=0.90, detail="path"),
            create_evidence(dimension="schema_relation", score=0.85, detail="rel"),
        ]
        result = evaluate_binding_evidence(evidence)
        assert result["gate"] in ("high_confidence", "needs_probe")
        assert result["dimensions_covered"] == 3

    def test_52_runtime_behavior_evidence(self):
        """Runtime behavior evidence scores correctly."""
        confirmed = collect_runtime_behavior_evidence(
            probe_type="entity_identity", probe_result="CONFIRMED"
        )
        assert confirmed["score"] == 1.0
        rejected = collect_runtime_behavior_evidence(
            probe_type="entity_identity", probe_result="REJECTED"
        )
        assert rejected["score"] == 0.0

    def test_53_conflict_detection_and_resolution(self, ledger):
        """Conflict detection finds and resolves conflicts."""
        # Create conflicting bindings
        b1 = create_binding_edge(binding_type="entity", source_node_id="e1", target_key="/api/a")
        b2 = create_binding_edge(binding_type="entity", source_node_id="e1", target_key="/api/b")
        ledger.insert(b1)
        ledger.insert(b2)
        conflicts = detect_conflicts(ledger)
        assert len(conflicts) >= 1
        # Resolve
        result = detect_and_resolve_all(ledger)
        assert result["total_conflicts"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
