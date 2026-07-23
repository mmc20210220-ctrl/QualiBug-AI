"""P0-1: Extract field grounding baseline from latest scan result."""
import json, sys
from pathlib import Path

SCAN = Path(r"d:\QualiBug-AI\QualiBug-AI-main\_scan_result_latest.json")
OUT = Path(r"d:\QualiBug-AI\QualiBug-AI-main\field_grounding_baseline.json")

print("[baseline] Loading scan result...")
with open(SCAN, "r", encoding="utf-8") as f:
    data = json.load(f)

# --- Extract Behavior IR stats ---
bir = data.get("behavior_ir") or data.get("behavior_ir_model") or {}
entities = bir.get("entities") or []
operations = bir.get("operations") or []
invariants = bir.get("invariants") or []
states = bir.get("states") or []

# Count entity fields
total_entity_fields = 0
bound_fields = 0
unknown_fields = 0
entity_field_map = {}
for ent in entities:
    if not isinstance(ent, dict):
        continue
    eid = str(ent.get("id") or ent.get("name") or "")
    fields = ent.get("fields") or ent.get("properties") or []
    if isinstance(fields, dict):
        fields = list(fields.keys())
    field_count = len(fields) if isinstance(fields, list) else 0
    total_entity_fields += field_count
    entity_field_map[eid] = field_count

# Count invariant types
causal_count = 0
state_count = 0
conservation_count = 0
empty_terms_count = 0
for inv in invariants:
    if not isinstance(inv, dict):
        continue
    expr = inv.get("expression") or {}
    kind = str(expr.get("kind") or "").lower()
    if any(t in kind for t in ("conserv", "balance", "amount", "quantity")):
        conservation_count += 1
        # Check if terms/operands have field info
        operands = expr.get("operands") or []
        equation = expr.get("equation") or {}
        terms = equation.get("terms") or equation.get("fields") or []
        has_fields = any(
            isinstance(op, dict) and op.get("field")
            for op in operands
        ) if operands else False
        if not terms and not has_fields:
            empty_terms_count += 1
    elif any(t in kind for t in ("postcondition", "must_become", "must_create", "因果", "后置")):
        causal_count += 1
    elif any(t in kind for t in ("state_machine", "state", "状态", "status_")):
        state_count += 1

# --- Extract obligation attempt ledger stats ---
ledger = data.get("obligation_attempt_ledger") or {}
attempts = ledger.get("attempts") or []

# Count by risk_family and terminal_status
family_stats = {}
for att in attempts:
    if not isinstance(att, dict):
        continue
    family = str(att.get("risk_family") or "unknown")
    status = str(att.get("terminal_status") or "unknown")
    reason = str(att.get("reason_code") or "")
    if family not in family_stats:
        family_stats[family] = {"total": 0, "deliverable": 0, "rejected": 0, "blocked": 0, "harness_failed": 0}
    family_stats[family]["total"] += 1
    if status == "DELIVERABLE":
        family_stats[family]["deliverable"] += 1
    elif status == "REJECTED":
        family_stats[family]["rejected"] += 1
    elif status == "BLOCKED":
        family_stats[family]["blocked"] += 1
    elif status == "HARNESS_FAILED":
        family_stats[family]["harness_failed"] += 1

# --- Extract findings stats ---
findings = data.get("findings") or []
finding_by_category = {}
for f in findings:
    if not isinstance(f, dict):
        continue
    cat = str(f.get("category") or f.get("risk_family") or "unknown")
    finding_by_category[cat] = finding_by_category.get(cat, 0) + 1

# --- Conservation findings detail ---
conservation_findings = []
for f in findings:
    if not isinstance(f, dict):
        continue
    cat = str(f.get("category") or "").lower()
    rf = str(f.get("risk_family") or "").lower()
    if "conserv" in cat or "conserv" in rf:
        evidence = f.get("evidence") or f.get("raw_evidence") or {}
        conservation_findings.append({
            "finding_id": f.get("finding_id") or f.get("id"),
            "category": f.get("category"),
            "risk_family": f.get("risk_family"),
            "terms": evidence.get("conservation_terms") or [],
            "before_values": evidence.get("before_values") or evidence.get("before_sum"),
            "after_values": evidence.get("after_values") or evidence.get("after_sum"),
        })

# --- Build baseline ---
baseline = {
    "schema_version": "qualibug.field-grounding-baseline.v1",
    "extracted_from": "_scan_result_latest.json",
    "behavior_ir": {
        "entity_count": len(entities),
        "operation_count": len(operations),
        "invariant_count": len(invariants),
        "state_count": len(states),
        "total_entity_fields": total_entity_fields,
        "entity_field_map": entity_field_map,
    },
    "invariants_by_type": {
        "causal_postcondition": causal_count,
        "state_transition": state_count,
        "conservation": conservation_count,
        "conservation_empty_terms": empty_terms_count,
    },
    "obligation_attempt_ledger": {
        "total_attempts": len(attempts),
        "by_risk_family": family_stats,
    },
    "findings": {
        "total": len(findings),
        "by_category": finding_by_category,
    },
    "conservation_findings_detail": conservation_findings,
    "field_grounding_gaps": {
        "entities_without_field_semantics": total_entity_fields,  # All fields lack semantic classification
        "conservation_rules_with_empty_terms": empty_terms_count,
        "causal_rules_with_field_delta": 0,  # None currently produce field-level delta
        "state_rules_with_real_state_field": 0,  # Need to verify
        "observer_field_selection": "ALL_NUMERIC_FIELDS",  # Current behavior
        "oracle_formula_type": "GENERIC_SUM",  # Current behavior
    },
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(baseline, f, ensure_ascii=False, indent=2)

print(f"[baseline] Saved to {OUT}")
print(f"\n=== FIELD GROUNDING BASELINE ===")
print(f"Entities: {len(entities)}, Operations: {len(operations)}")
print(f"Invariants: {len(invariants)} (causal={causal_count}, state={state_count}, conservation={conservation_count})")
print(f"Conservation with EMPTY TERMS: {empty_terms_count}")
print(f"Total entity fields: {total_entity_fields}")
print(f"Findings: {len(findings)}")
print(f"Conservation findings: {len(conservation_findings)}")
for cf in conservation_findings:
    print(f"  - {cf['finding_id']}: terms={cf['terms']}")
print(f"\nObligation attempts by family:")
for fam, stats in sorted(family_stats.items()):
    print(f"  {fam}: total={stats['total']} deliverable={stats['deliverable']} blocked={stats['blocked']}")
