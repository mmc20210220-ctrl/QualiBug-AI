"""V1.4.0 Field Comprehension Verification Script.

Runs the full behavior IR build against the real benchmark_mall knowledge asset
and verifies all Phase 1 gate criteria:
  1. Core entities >= 8
  2. Core fields with semantic classification >= 40
  3. Core fields API or DB binding rate >= 90%
  4. Scope binding rate = 100%
  5. Rules with source references = 100%
  6. High-confidence rules operation binding rate >= 90%
  7. Random-guess bindings = 0
  8. Umbrella rules entering execution = 0

Gate: V1_4_0_FIELD_COMPREHENSION = PASS / FAIL
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset

ASSET_PATH = ROOT / "platform_workspace" / "benchmark_mall" / "defect_discovery" / "enterprise_business_knowledge_asset.json"
OUTPUT_PATH = ROOT / "artifacts" / "spec_v1_4_0" / "v140_field_comprehension_gate.json"


def _load_asset() -> dict:
    with open(ASSET_PATH, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    asset = _load_asset()
    model = build_behavior_ir_from_knowledge_asset(asset, project_id="benchmark_mall")

    entities = [e for e in model.get("entities", []) if isinstance(e, dict)]
    invariants = [i for i in model.get("invariants", []) if isinstance(i, dict)]

    # ── Criterion 1: Core entities >= 8 ──
    entity_count = len(entities)
    c1_pass = entity_count >= 8

    # ── Criterion 2: Core fields with semantic classification >= 40 ──
    total_fields = 0
    classified_fields = 0
    for ent in entities:
        fields = ent.get("fields", [])
        if not fields or not isinstance(fields[0], dict):
            continue
        for f in fields:
            total_fields += 1
            if f.get("semantic_type") and f["semantic_type"] != "UNKNOWN":
                classified_fields += 1
    c2_pass = classified_fields >= 40

    # ── Criterion 3: Core fields API or DB binding rate >= 90% ──
    bound_fields = 0
    bindable_fields = 0
    for ent in entities:
        fields = ent.get("fields", [])
        if not fields or not isinstance(fields[0], dict):
            continue
        for f in fields:
            bindable_fields += 1
            status = f.get("binding_status", "NOT_DECLARED")
            if status in ("RESOLVED", "INCOMPLETE"):
                bound_fields += 1
    binding_rate = (bound_fields / bindable_fields * 100) if bindable_fields else 0
    c3_pass = binding_rate >= 90.0

    # ── Criterion 4: Scope binding rate = 100% ──
    entities_with_scope = 0
    entities_structured = 0
    for ent in entities:
        fields = ent.get("fields", [])
        if not fields or not isinstance(fields[0], dict):
            continue
        entities_structured += 1
        scope = ent.get("scope_fields")
        if isinstance(scope, dict):
            entities_with_scope += 1
    scope_rate = (entities_with_scope / entities_structured * 100) if entities_structured else 0
    c4_pass = scope_rate >= 100.0

    # ── Criterion 5: Rules with source references = 100% ──
    rules_with_source = 0
    total_rules = len(invariants)
    for inv in invariants:
        src_refs = inv.get("source_refs", [])
        if src_refs:
            rules_with_source += 1
    source_rate = (rules_with_source / total_rules * 100) if total_rules else 0
    c5_pass = source_rate >= 100.0

    # ── Criterion 6: High-confidence rules operation binding rate >= 90% ──
    high_conf_rules = 0
    high_conf_bound = 0
    for inv in invariants:
        conf = float(inv.get("confidence") or 0)
        if inv.get("binding_status") == "umbrella_rule_excluded":
            continue
        if conf >= 0.7:
            high_conf_rules += 1
            op_refs = inv.get("operation_refs", [])
            if op_refs:
                high_conf_bound += 1
    op_bind_rate = (high_conf_bound / high_conf_rules * 100) if high_conf_rules else 0
    c6_pass = op_bind_rate >= 90.0

    # ── Criterion 7: Random-guess bindings = 0 ──
    # A random-guess binding is one with derivation "random" or confidence < 0.3
    random_bindings = 0
    for rel in model.get("relations", []):
        if not isinstance(rel, dict):
            continue
        if rel.get("derivation") == "random" or float(rel.get("confidence") or 1) < 0.3:
            random_bindings += 1
    c7_pass = random_bindings == 0

    # ── Criterion 8: Umbrella rules entering execution = 0 ──
    umbrella_in_execution = 0
    for inv in invariants:
        if inv.get("binding_status") == "umbrella_rule_excluded":
            # Check if it still has operation relations (should not)
            inv_id = inv.get("id", "")
            for rel in model.get("relations", []):
                if isinstance(rel, dict) and rel.get("to_ref") == inv_id:
                    umbrella_in_execution += 1
                    break
    c8_pass = umbrella_in_execution == 0

    # ── Gate Decision ──
    all_pass = all([c1_pass, c2_pass, c3_pass, c4_pass, c5_pass, c6_pass, c7_pass, c8_pass])
    gate = "PASS" if all_pass else "FAIL"

    report = {
        "gate": f"V1_4_0_FIELD_COMPREHENSION = {gate}",
        "PHASE_2_ENTRY_ALLOWED": all_pass,
        "criteria": {
            "C1_core_entities": {"value": entity_count, "threshold": ">=8", "pass": c1_pass},
            "C2_classified_fields": {"value": classified_fields, "threshold": ">=40", "pass": c2_pass},
            "C3_binding_rate_pct": {"value": round(binding_rate, 1), "threshold": ">=90", "pass": c3_pass},
            "C4_scope_rate_pct": {"value": round(scope_rate, 1), "threshold": "100", "pass": c4_pass},
            "C5_source_ref_rate_pct": {"value": round(source_rate, 1), "threshold": "100", "pass": c5_pass},
            "C6_op_binding_rate_pct": {"value": round(op_bind_rate, 1), "threshold": ">=90", "pass": c6_pass},
            "C7_random_bindings": {"value": random_bindings, "threshold": "0", "pass": c7_pass},
            "C8_umbrella_in_execution": {"value": umbrella_in_execution, "threshold": "0", "pass": c8_pass},
        },
        "stats": {
            "total_entities": entity_count,
            "total_fields": total_fields,
            "classified_fields": classified_fields,
            "bound_fields": bound_fields,
            "bindable_fields": bindable_fields,
            "total_invariants": total_rules,
            "high_conf_rules": high_conf_rules,
            "high_conf_bound": high_conf_bound,
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n{'='*60}")
    print(f"  V1_4_0_FIELD_COMPREHENSION = {gate}")
    print(f"  PHASE_2_ENTRY_ALLOWED = {all_pass}")
    print(f"{'='*60}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
