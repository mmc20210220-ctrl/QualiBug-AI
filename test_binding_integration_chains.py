"""Binding Integration Tests — 7 complete chains.

Chains:
1. Entity + Operation: Build bindings → gate check → executable
2. Field Causal: Field classification → causal rule → binding
3. State Path: State binding → transition ops → fixture chain
4. Cross-Entity: Relation binding → correlation key → observer
5. Actor Scope: Actor binding → scope binding → isolation
6. Idempotency: Same binding proposed twice → no duplicate
7. Conservation: Oracle input → field binding → evidence chain

Run: python -m pytest test_binding_integration_chains.py -v
"""
import sys
import pytest

sys.path.insert(0, ".")

from ai_test_asset_center.binding_ledger import BindingLedger, BindingStatus
from ai_test_asset_center.binding_builder import build_all_bindings, classify_field_type
from ai_test_asset_center.binding_evidence import (
    compute_composite_confidence, create_evidence, evaluate_binding_evidence,
)
from ai_test_asset_center.binding_completeness_gate import check_binding_completeness, gate_or_block
from ai_test_asset_center.binding_conflict_resolver import detect_and_resolve_all
from ai_test_asset_center.field_level_golden_rules import get_golden_rules, validate_golden_rules


# ─── Shared Test IR ───────────────────────────────────────────────────────────

def _build_test_ir():
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "entities": [
            {"id": "ent_order", "name": "Order", "collection_path": "/api/orders", "alternate_paths": []},
            {"id": "ent_item", "name": "OrderItem", "collection_path": "/api/order-items", "alternate_paths": []},
            {"id": "ent_customer", "name": "Customer", "collection_path": "/api/customers", "alternate_paths": []},
        ],
        "operations": [
            {"id": "op_create_order", "method": "POST", "path": "/api/orders", "read_write": "write",
             "source_refs": [{"source_id": "s1"}],
             "request_schema": {"properties": {
                 "customer_id": {"type": "string"}, "quantity": {"type": "integer"},
                 "total_amount": {"type": "number"}, "status": {"type": "string", "enum": ["draft", "confirmed", "shipped"]},
             }}},
            {"id": "op_list_orders", "method": "GET", "path": "/api/orders", "read_write": "read",
             "source_refs": [{"source_id": "s1"}],
             "response_schema": {"type": "array", "items": {"properties": {
                 "id": {"type": "string"}, "status": {"type": "string"},
                 "customer_id": {"type": "string"}, "quantity": {"type": "integer"},
             }}}},
            {"id": "op_get_order", "method": "GET", "path": "/api/orders/{id}", "read_write": "read", "source_refs": []},
            {"id": "op_delete_order", "method": "DELETE", "path": "/api/orders/{id}", "read_write": "write", "source_refs": []},
            {"id": "op_confirm_order", "method": "POST", "path": "/api/orders/{id}/confirm", "read_write": "write", "source_refs": [{"source_id": "s1"}]},
            {"id": "op_list_items", "method": "GET", "path": "/api/order-items", "read_write": "read",
             "source_refs": [], "response_schema": {"properties": {"order_id": {"type": "string"}, "qty": {"type": "integer"}}}},
            {"id": "op_create_item", "method": "POST", "path": "/api/order-items", "read_write": "write", "source_refs": []},
        ],
        "actors": [
            {"id": "actor_admin", "role": "admin", "credential_secret_ref": "secret:admin",
             "account_ref": "acc_1", "runtime_bound": True, "tenant_scope": "tenant_a",
             "organization_scope": "org_1", "allowed_resources": ["orders", "order-items"], "allowed_actions": ["*"]},
            {"id": "actor_operator", "role": "operator", "credential_secret_ref": "secret:operator",
             "account_ref": "acc_2", "runtime_bound": True, "tenant_scope": "tenant_a",
             "allowed_resources": ["orders"], "allowed_actions": ["read", "write"]},
        ],
        "states": [
            {"id": "state_order_status", "entity_ref": "ent_order", "field": "status",
             "values": ["draft", "confirmed", "shipped"], "initial": "draft",
             "terminal_values": ["shipped"]},
        ],
        "relations": [
            {"id": "rel_order_customer", "relation_type": "consumes", "from_ref": "ent_order", "to_ref": "ent_customer",
             "operation_ref": "op_create_order", "source_refs": [{"source_id": "s1"}], "preconditions": [], "effects": []},
            {"id": "rel_order_items", "relation_type": "produces", "from_ref": "ent_order", "to_ref": "ent_item",
             "operation_ref": "op_create_item", "source_refs": [{"source_id": "s1"}], "preconditions": [], "effects": []},
            {"id": "rel_admin_permits", "relation_type": "permits", "from_ref": "actor_admin", "to_ref": "op_create_order",
             "operation_ref": "op_create_order", "actor_ref": "actor_admin", "source_refs": [], "preconditions": [], "effects": []},
            {"id": "rel_order_transition", "relation_type": "transitions", "from_ref": "state_order_status", "to_ref": "state_order_status",
             "operation_ref": "op_confirm_order", "source_refs": [], "preconditions": [], "effects": []},
        ],
        "invariants": [
            {"id": "inv_qty_conservation", "invariant_type": "conservation",
             "terms": [{"field": "quantity"}, {"field": "total_amount"}],
             "source_refs": [{"source_id": "s1"}]},
        ],
    }


@pytest.fixture
def ir():
    return _build_test_ir()


@pytest.fixture
def loaded_ledger(ir):
    ledger = BindingLedger(project_id="integration_test")
    build_all_bindings(ir, ledger)
    return ledger


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 1: Entity + Operation
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain1EntityOperation:
    def test_entity_to_operation_chain(self, loaded_ledger, ir):
        """Entity binding → operation binding → gate check."""
        # Entity bindings exist
        entity_bindings = loaded_ledger.get_by_type("entity")
        assert len(entity_bindings) == 3

        # Operation bindings exist
        op_bindings = loaded_ledger.get_by_type("operation")
        assert len(op_bindings) == 7

        # Entity binding references create/read operations
        order_binding = loaded_ledger.find(binding_type="entity", source_node_id="ent_order")
        assert len(order_binding) == 1
        meta = order_binding[0]["metadata"]
        assert meta["create_operation_ref"] == "op_create_order"
        assert meta["read_operation_ref"] == "op_list_orders"

        # Promote to executable for gate test
        for b in entity_bindings + op_bindings:
            if b["status"] == BindingStatus.HIGH_CONFIDENCE.value:
                try:
                    loaded_ledger.promote(b["binding_id"], BindingStatus.EXECUTABLE, reason="test")
                except ValueError:
                    pass

        # Gate check with executable bindings
        obligation = {
            "obligation_id": "obl_chain1",
            "risk_family": "authorization",
            "required_operations": ["op_create_order"],
            "required_actors": [],
            "required_fixtures": [],
            "required_observers": [],
            "property": {"operation_ref": "op_create_order"},
        }
        result = check_binding_completeness(loaded_ledger, obligation=obligation, behavior_ir=ir)
        # Entity and operation dimensions should pass
        assert "entity" in result["executable_dimensions"] or "operation" in result["executable_dimensions"]


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 2: Field Causal
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain2FieldCausal:
    def test_field_classification_to_causal_rule(self, loaded_ledger):
        """Field classification → causal rule matching → binding evidence."""
        # Classify fields from the IR
        assert classify_field_type("customer_id") == "FOREIGN_KEY"
        assert classify_field_type("quantity") == "QUANTITY_BALANCE"
        assert classify_field_type("total_amount") == "MONEY"
        assert classify_field_type("status") == "STATE"

        # Get causal rules
        causal_rules = get_golden_rules(category="causal")
        assert len(causal_rules) >= 8

        # Verify FK causal rule exists
        fk_rule = next((r for r in causal_rules if "foreign key" in r["description"].lower()), None)
        assert fk_rule is not None
        assert len(fk_rule["terms"]) >= 2

        # Field bindings exist in ledger
        field_bindings = loaded_ledger.get_by_type("field")
        assert len(field_bindings) >= 3

        # Evidence chain: field binding has evidence
        for fb in field_bindings:
            assert len(fb["evidence"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 3: State Path
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain3StatePath:
    def test_state_binding_to_transition_chain(self, loaded_ledger):
        """State binding → transition operations → fixture requirement."""
        # State binding exists
        state_bindings = loaded_ledger.get_by_type("state")
        assert len(state_bindings) >= 1

        order_state = loaded_ledger.find(binding_type="state", source_node_id="state_order_status")
        assert len(order_state) == 1
        meta = order_state[0]["metadata"]
        assert meta["state_field_name"] == "status"
        assert "draft" in meta["raw_values"]
        assert "confirmed" in meta["raw_values"]

        # Transition operations linked
        assert "op_confirm_order" in meta["transition_operations"]

        # Fixture binding exists for entity
        fixture_bindings = loaded_ledger.find(binding_type="fixture", source_node_id="ent_order")
        assert len(fixture_bindings) >= 1

        # State rules from golden set
        state_rules = get_golden_rules(category="state")
        assert len(state_rules) >= 6
        # Terminal state rule exists
        terminal_rule = next((r for r in state_rules if "terminal" in r["description"].lower()), None)
        assert terminal_rule is not None


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 4: Cross-Entity
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain4CrossEntity:
    def test_relation_to_observer_chain(self, loaded_ledger):
        """Relation binding → correlation key → observer binding."""
        # Relation bindings exist
        rel_bindings = loaded_ledger.get_by_type("relation")
        assert len(rel_bindings) >= 2

        # Order→Customer relation
        order_customer = loaded_ledger.find(binding_type="relation", source_node_id="rel_order_customer")
        assert len(order_customer) == 1
        meta = order_customer[0]["metadata"]
        assert meta["source_entity_ref"] == "ent_order"
        assert meta["target_entity_ref"] == "ent_customer"
        assert meta["correlation_key"] != ""

        # Observer bindings exist for related entities
        observer_bindings = loaded_ledger.get_by_type("observer")
        assert len(observer_bindings) >= 1

        # Order→Item relation (produces)
        order_items = loaded_ledger.find(binding_type="relation", source_node_id="rel_order_items")
        assert len(order_items) == 1
        assert order_items[0]["metadata"]["relation_type"] == "produces"


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 5: Actor Scope
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain5ActorScope:
    def test_actor_to_scope_chain(self, loaded_ledger):
        """Actor binding → scope binding → isolation verification."""
        # Actor bindings exist
        actor_bindings = loaded_ledger.get_by_type("actor")
        assert len(actor_bindings) == 2

        admin_binding = loaded_ledger.find(binding_type="actor", source_node_id="actor_admin")
        assert len(admin_binding) == 1
        meta = admin_binding[0]["metadata"]
        assert meta["role"] == "admin"
        assert meta["tenant_scope"] == "tenant_a"

        # Scope bindings exist
        scope_bindings = loaded_ledger.get_by_type("scope")
        assert len(scope_bindings) >= 1

        # Tenant scope extracted from actor
        tenant_scopes = [b for b in scope_bindings if "tenant" in b["target_key"]]
        assert len(tenant_scopes) >= 1

        # Actor has high confidence (runtime_bound)
        assert admin_binding[0]["confidence"] > 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 6: Idempotency
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain6Idempotency:
    def test_duplicate_proposal_idempotent(self, ir):
        """Proposing same binding twice returns same binding, no duplicate."""
        ledger = BindingLedger(project_id="idempotency_test")

        # Build bindings first time
        result1 = build_all_bindings(ir, ledger)
        size_after_first = ledger.size

        # Build bindings second time (same IR)
        result2 = build_all_bindings(ir, ledger)
        size_after_second = ledger.size

        # Size should not increase (idempotent)
        assert size_after_second == size_after_first

        # Same binding IDs
        assert result1["total_created"] == result2["total_created"]


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN 7: Conservation
# ═══════════════════════════════════════════════════════════════════════════════

class TestChain7Conservation:
    def test_oracle_input_to_evidence_chain(self, loaded_ledger):
        """Oracle input binding → field bindings → evidence chain."""
        # Oracle input binding exists
        oracle_bindings = loaded_ledger.get_by_type("oracle_input")
        assert len(oracle_bindings) >= 1

        inv_binding = loaded_ledger.find(binding_type="oracle_input", source_node_id="inv_qty_conservation")
        assert len(inv_binding) == 1
        meta = inv_binding[0]["metadata"]
        assert "quantity" in meta["input_field_bindings"]
        assert "total_amount" in meta["input_field_bindings"]

        # Conservation rules exist
        conservation_rules = get_golden_rules(category="conservation")
        assert len(conservation_rules) >= 6

        # Evidence evaluation works
        evidence = inv_binding[0]["evidence"]
        evaluation = evaluate_binding_evidence(evidence)
        assert evaluation["composite_confidence"] > 0.0
        assert evaluation["gate"] in ("high_confidence", "needs_probe", "unusable")

        # Full validation passes
        validation = validate_golden_rules()
        assert validation["meets_requirements"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
