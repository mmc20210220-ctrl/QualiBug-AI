"""Generate Phase 5 and remaining JSON deliverables."""
import sys
import json

sys.path.insert(0, ".")

from ai_test_asset_center.field_level_golden_rules import build_field_level_rule_set_json, validate_golden_rules
from ai_test_asset_center.binding_ledger import BindingLedger, BindingStatus
from ai_test_asset_center.binding_builder import build_all_bindings

# Phase 5: Golden Rule Set
print("Generating field_level_golden_rule_set.json...")
data = build_field_level_rule_set_json()
with open("field_level_golden_rule_set.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
v = data["validation"]
print(f"  Rules: {v['total_rules']}, Causal: {v['causal_count']}, State: {v['state_count']}, Conservation: {v['conservation_count']}")
print(f"  Empty terms: {v['empty_terms_count']}, Meets requirements: {v['meets_requirements']}")

# Phase 1.2: Binding Status Ledger
print("\nGenerating binding_status_ledger.json...")
ledger = BindingLedger(project_id="qualibug")
status_ledger = {
    "schema_version": "qualibug.binding-status-ledger.v1",
    "title": "Binding Status and Version Model",
    "state_machine": {
        "states": [s.value for s in BindingStatus],
        "transitions": {
            "CANDIDATE": ["HIGH_CONFIDENCE", "CONFLICTED", "REJECTED", "STALE"],
            "HIGH_CONFIDENCE": ["RUNTIME_CONFIRMED", "EXECUTABLE", "CONFLICTED", "REJECTED", "STALE"],
            "RUNTIME_CONFIRMED": ["EXECUTABLE", "CONFLICTED", "STALE"],
            "EXECUTABLE": ["STALE", "CONFLICTED"],
            "CONFLICTED": ["HIGH_CONFIDENCE", "REJECTED", "CANDIDATE"],
            "REJECTED": ["CANDIDATE"],
            "STALE": ["CANDIDATE"],
        },
        "terminal_states": ["EXECUTABLE", "REJECTED"],
    },
    "version_model": {
        "binding_version": "monotonically increasing integer per binding",
        "created_at": "unix timestamp of creation",
        "updated_at": "unix timestamp of last update",
        "transition_history": "ordered list of all state transitions",
    },
    "confidence_gate": {
        "high_confidence_threshold": 0.90,
        "probe_threshold": 0.70,
        "unusable_below": 0.70,
    },
}
with open("binding_status_ledger.json", "w", encoding="utf-8") as f:
    json.dump(status_ledger, f, indent=2, ensure_ascii=False)
print("  Done")

# Phase 1.3: Binding Evidence Ledger
print("\nGenerating binding_evidence_ledger.json...")
evidence_ledger = {
    "schema_version": "qualibug.binding-evidence-ledger.v1",
    "title": "Multi-Source Binding Evidence System",
    "evidence_dimensions": [
        "semantic_name", "entity_context", "data_type", "operation_context",
        "schema_relation", "source_consistency", "runtime_behavior", "correlation_consistency",
    ],
    "dimension_weights": {
        "semantic_name": 0.12,
        "entity_context": 0.15,
        "data_type": 0.10,
        "operation_context": 0.15,
        "schema_relation": 0.15,
        "source_consistency": 0.13,
        "runtime_behavior": 0.12,
        "correlation_consistency": 0.08,
    },
    "confidence_gate": {
        "high_confidence": ">=0.90: promote to HIGH_CONFIDENCE or EXECUTABLE",
        "needs_probe": "0.70-0.90: schedule runtime probe",
        "unusable": "<0.70: cannot be used for execution",
    },
    "evidence_entry_schema": {
        "dimension": "string (one of 8 dimensions)",
        "score": "float 0.0-1.0",
        "detail": "string (human-readable)",
        "source_ref": "string (source identifier)",
        "metadata": "dict (structured data)",
        "timestamp": "float (unix timestamp)",
        "evidence_id": "string (unique hash)",
    },
}
with open("binding_evidence_ledger.json", "w", encoding="utf-8") as f:
    json.dump(evidence_ledger, f, indent=2, ensure_ascii=False)
print("  Done")

# Phase 1.4: Binding Conflict Ledger
print("\nGenerating binding_conflict_ledger.json...")
conflict_ledger = {
    "schema_version": "qualibug.binding-conflict-ledger.v1",
    "title": "Binding Conflict Detection and Resolution",
    "conflict_detection": {
        "trigger": "Multiple active bindings for same (type, source_node_id) with different target_keys",
        "detection_scope": "All non-REJECTED, non-STALE bindings",
    },
    "resolution_priority": [
        "schema_evidence (Behavior IR declared relations)",
        "api_consistency (operation path/method match)",
        "relation_evidence (cross-entity correlation)",
        "runtime_probe (empirical confirmation)",
    ],
    "resolution_strategies": [
        "evidence_priority: highest composite confidence wins",
        "schema_first: schema_relation dimension score breaks tie",
        "runtime_first: runtime_behavior dimension breaks tie",
        "newest: most recently updated wins",
    ],
    "conflict_record_schema": {
        "conflict_id": "string",
        "binding_type": "string",
        "source_node_id": "string",
        "conflicting_bindings": "list of binding summaries",
        "detected_at": "float",
        "resolution": {"strategy": "string", "winner": "binding_id", "reason": "string", "resolved_at": "float"},
    },
}
with open("binding_conflict_ledger.json", "w", encoding="utf-8") as f:
    json.dump(conflict_ledger, f, indent=2, ensure_ascii=False)
print("  Done")

print("\nAll Phase 5 deliverables generated successfully.")
