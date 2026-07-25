"""Integration test for binding system."""
import sys
sys.path.insert(0, ".")

from ai_test_asset_center.binding_ledger import BindingLedger, BindingStatus
from ai_test_asset_center.binding_builder import build_all_bindings
from ai_test_asset_center.binding_completeness_gate import check_binding_completeness
from ai_test_asset_center.binding_conflict_resolver import detect_and_resolve_all

# Mock Behavior IR
ir = {
    "schema_version": "qualibug.behavior-ir.v2",
    "entities": [
        {"id": "ent_order", "name": "Order", "collection_path": "/api/orders", "alternate_paths": []},
        {"id": "ent_customer", "name": "Customer", "collection_path": "/api/customers", "alternate_paths": []},
    ],
    "operations": [
        {"id": "op_create_order", "method": "POST", "path": "/api/orders", "read_write": "write",
         "source_refs": [{"source_id": "s1"}],
         "request_schema": {"properties": {"customer_id": {"type": "string"}, "total_amount": {"type": "number"}, "status": {"type": "string", "enum": ["draft", "confirmed"]}}}},
        {"id": "op_list_orders", "method": "GET", "path": "/api/orders", "read_write": "read",
         "source_refs": [{"source_id": "s1"}],
         "response_schema": {"type": "array", "items": {"properties": {"id": {"type": "string"}, "status": {"type": "string"}, "customer_id": {"type": "string"}}}}},
        {"id": "op_get_order", "method": "GET", "path": "/api/orders/{id}", "read_write": "read", "source_refs": []},
        {"id": "op_delete_order", "method": "DELETE", "path": "/api/orders/{id}", "read_write": "write", "source_refs": []},
    ],
    "actors": [
        {"id": "actor_admin", "role": "admin", "credential_secret_ref": "secret:admin", "account_ref": "acc_1",
         "runtime_bound": True, "tenant_scope": "tenant_1", "allowed_resources": ["orders"], "allowed_actions": ["*"]},
        {"id": "actor_viewer", "role": "viewer", "credential_secret_ref": "secret:viewer", "account_ref": "acc_2",
         "runtime_bound": True, "tenant_scope": "tenant_1", "allowed_resources": ["orders"], "allowed_actions": ["read"]},
    ],
    "states": [
        {"id": "state_order_status", "entity_ref": "ent_order", "field": "status",
         "values": ["draft", "confirmed", "shipped"], "initial": "draft"},
    ],
    "relations": [
        {"id": "rel_order_customer", "relation_type": "consumes", "from_ref": "ent_order", "to_ref": "ent_customer",
         "operation_ref": "op_create_order", "source_refs": [{"source_id": "s1"}], "preconditions": [], "effects": []},
        {"id": "rel_admin_orders", "relation_type": "permits", "from_ref": "actor_admin", "to_ref": "op_create_order",
         "operation_ref": "op_create_order", "actor_ref": "actor_admin", "source_refs": [], "preconditions": [], "effects": []},
    ],
    "invariants": [
        {"id": "inv_order_total", "invariant_type": "conservation", "terms": [{"field": "total_amount"}],
         "source_refs": [{"source_id": "s1"}]},
    ],
}

# Test 1: Build all bindings
print("=" * 60)
print("TEST 1: Build All Bindings")
print("=" * 60)
ledger = BindingLedger(project_id="test")
result = build_all_bindings(ir, ledger)
print(f"Total bindings created: {result['total_created']}")
print(f"Per type: {result['per_type']}")
print(f"Ledger size: {ledger.size}")
print()

# Coverage summary
print("Coverage Summary:")
coverage = ledger.coverage_summary()
for btype, stats in sorted(coverage.items()):
    if stats["total"] > 0:
        print(f"  {btype}: {stats['total']} total, {stats['executable']} executable, rate={stats['coverage_rate']:.2f}")
print()

# Test 2: Completeness Gate
print("=" * 60)
print("TEST 2: Binding Completeness Gate")
print("=" * 60)
obligation = {
    "obligation_id": "obl_test_1",
    "risk_family": "state",
    "required_operations": ["op_create_order"],
    "required_actors": ["actor_admin"],
    "required_fixtures": [],
    "required_observers": [],
    "property": {"operation_ref": "op_create_order", "from_state": "draft"},
}
gate_result = check_binding_completeness(ledger, obligation=obligation, behavior_ir=ir)
print(f"Gate passed: {gate_result['gate_passed']}")
print(f"Executable dims: {gate_result['executable_dimensions']}")
print(f"Blocked dims: {[d['dimension'] for d in gate_result['blocked_dimensions']]}")
print(f"Coverage rate: {gate_result['coverage_rate']}")
print()

# Test 3: Conflict Detection
print("=" * 60)
print("TEST 3: Conflict Detection")
print("=" * 60)
conflict_result = detect_and_resolve_all(ledger)
print(f"Total conflicts: {conflict_result['total_conflicts']}")
print(f"Resolved: {conflict_result['resolved']}")
print()

# Test 4: State machine transitions
print("=" * 60)
print("TEST 4: State Machine")
print("=" * 60)
entity_bindings = ledger.get_by_type("entity")
for b in entity_bindings[:2]:
    print(f"  {b['binding_id']}: status={b['status']}, confidence={b['confidence']:.3f}, version={b['version']}")
print()

# Test 5: Export
print("=" * 60)
print("TEST 5: Export")
print("=" * 60)
exported = ledger.export()
print(f"Exported {exported['total_bindings']} bindings")
print(f"Schema: {exported['schema_version']}")
print()

print("ALL TESTS PASSED")
