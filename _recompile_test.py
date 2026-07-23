"""Re-compile existing obligations with new code to verify structured_expression."""
import json
from pathlib import Path
from ai_test_asset_center.experiment_compiler_obligation import compile_experiment_for_obligation

# Load the existing scan result
result = json.loads(Path("platform_outputs/contractflow_project_c/scan_result.json").read_text(encoding="utf-8"))
v12 = result.get("v12", {})
ir = v12.get("behavior_ir", {})
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])

print(f"Total attempts: {len(attempts)}")
print(f"IR: {len(ir.get('entities', []))} entities, {len(ir.get('invariants', []))} invariants")

# Find ASSERTION_INDETERMINATE attempts
indeterminate = [a for a in attempts if a.get("reason_code") == "ASSERTION_INDETERMINATE"]
print(f"\nASSERTION_INDETERMINATE attempts: {len(indeterminate)}")

# Re-compile each indeterminate attempt
print("\n=== RE-COMPILATION RESULTS ===")
compiled_with_se = 0
compiled_with_diag = 0
still_indeterminate = 0
blocked = 0

for att in indeterminate:
    oid = att.get("obligation_id", "?")
    family = att.get("risk_family", "?")
    
    # Build obligation dict from attempt
    obl = {
        "obligation_id": oid,
        "risk_family": family,
        "operation_refs": att.get("operation_refs", []),
        "actor_refs": att.get("actor_refs", []),
        "property_specs": att.get("property_specs", []),
        "behavior_ir_refs": att.get("behavior_ir_refs", []),
        "source_refs": att.get("source_refs", []),
    }
    
    try:
        exp = compile_experiment_for_obligation(obl, behavior_ir=ir)
        status = exp.get("status", "?")
        
        if status == "BLOCKED":
            blocked += 1
            reason = exp.get("reason_code", "?")
            print(f"  {oid} [{family}]: BLOCKED ({reason})")
            continue
        
        assertions = exp.get("assertions", [])
        has_se = any(a.get("structured_expression") for a in assertions if isinstance(a, dict))
        has_diag = any(a.get("compile_diagnostic") for a in assertions if isinstance(a, dict))
        
        if has_se:
            compiled_with_se += 1
            se_kind = next((a.get("kind") for a in assertions if isinstance(a, dict) and a.get("structured_expression")), "?")
            print(f"  {oid} [{family}]: COMPILED with structured_expression (kind={se_kind})")
        elif has_diag:
            compiled_with_diag += 1
            diag = next((a.get("compile_diagnostic") for a in assertions if isinstance(a, dict) and a.get("compile_diagnostic")), "")
            print(f"  {oid} [{family}]: COMPILED with diagnostic={diag}")
        else:
            still_indeterminate += 1
            print(f"  {oid} [{family}]: COMPILED (no structured_expression)")
    except Exception as e:
        print(f"  {oid} [{family}]: ERROR - {e}")

print(f"\n=== SUMMARY ===")
print(f"Compiled with structured_expression: {compiled_with_se}")
print(f"Compiled with diagnostic: {compiled_with_diag}")
print(f"Compiled without SE: {still_indeterminate}")
print(f"Blocked: {blocked}")
