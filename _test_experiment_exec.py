#!/usr/bin/env python
"""P0-11: Direct experiment execution test for field-level rules.

Bypasses campaign/source provenance to directly test:
1. Obligation -> Experiment compilation
2. Experiment execution against target
3. Oracle trace output
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler_base import compile_obligations_from_behavior_ir


def _parse_test_accounts_md(path: Path) -> list:
    """Parse TEST_ACCOUNTS.md into runtime_actors format."""
    accounts = []
    if not path.exists():
        return accounts
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line or "角色" in line:
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) >= 3:
            role_cn, email, password = parts[0], parts[1], parts[2]
            # Map Chinese role to English role key used in knowledge asset
            role_map = {
                "管理员": "admin",
                "财务": "finance",
                "商家": "seller",
                "仓库": "warehouse",
                "审计": "auditor",
                "普通买家": "buyer",
                "禁用买家": "disabled_buyer",
            }
            role = role_map.get(role_cn, role_cn)
            accounts.append({
                "role": role,
                "email": email,
                "password": password,
                "account_ref": email.split("@")[0] if "@" in email else email,
                "status": "disabled" if "禁用" in role_cn else "active",
            })
    return accounts


def main():
    # Load pre-built knowledge asset
    asset_path = Path("platform_outputs/benchmark_mall/enterprise_knowledge_center/enterprise_business_knowledge_asset.json")
    if not asset_path.exists():
        print(f"ERROR: {asset_path} not found")
        return 1

    print("=" * 60)
    print("P0-11: Direct Experiment Execution Test")
    print("=" * 60)

    # Step 1: Build Behavior IR with runtime actors from TEST_ACCOUNTS.md
    print("\n[1] Building Behavior IR...")
    asset = json.loads(asset_path.read_text(encoding="utf-8"))
    
    # Parse test accounts for runtime actor binding
    accounts_md = Path("projects/benchmark_mall/input/TEST_ACCOUNTS.md")
    runtime_actors = _parse_test_accounts_md(accounts_md)
    print(f"    Runtime actors from TEST_ACCOUNTS.md: {len(runtime_actors)}")
    for ra in runtime_actors:
        print(f"      {ra['role']}: {ra['email']} [{ra['status']}]")
    
    ir = build_behavior_ir_from_knowledge_asset(
        asset, project_id="benchmark_mall", runtime_actors=runtime_actors
    )
    print(f"    Operations: {len(ir.get('operations', []))}")
    print(f"    States: {len(ir.get('states', []))}")
    print(f"    Invariants: {len(ir.get('invariants', []))}")
    print(f"    Actors: {len(ir.get('actors', []))}")

    # Step 2: Compile obligations
    print("\n[2] Compiling obligations...")
    obligations_result = compile_obligations_from_behavior_ir(ir)
    obligations = obligations_result.get("obligations", [])
    print(f"    Total obligations: {len(obligations)}")

    # Filter state and conservation obligations
    state_obls = [o for o in obligations if o.get("risk_family") == "state"]
    cons_obls = [o for o in obligations if o.get("risk_family") == "conservation"]
    print(f"    State obligations: {len(state_obls)}")
    print(f"    Conservation obligations: {len(cons_obls)}")

    # Step 3: Try to compile experiments
    print("\n[3] Compiling experiments...")
    try:
        from ai_test_asset_center.experiment_compiler_obligation import compile_experiment_for_obligation
        
        compiled = []
        blocked = []
        for obl in obligations:  # All obligations
            try:
                exp = compile_experiment_for_obligation(
                    obligation=obl,
                    behavior_ir=ir,
                    environment_type="test",
                )
                # Check compile_receipt for real status
                receipt = exp.get("compile_receipt", {}) if isinstance(exp, dict) else {}
                status = receipt.get("status", "UNKNOWN") if isinstance(receipt, dict) else "UNKNOWN"
                if status == "BLOCKED" or status == "DEFERRED":
                    reason = receipt.get("reason_code", "?") + ":" + receipt.get("detail", "")
                    blocked.append((obl, reason))
                else:
                    compiled.append((obl, exp))
            except Exception as e:
                import traceback
                print(f"    EXCEPTION: {e}")
                traceback.print_exc()
                blocked.append((obl, f"EXCEPTION: {str(e)[:80]}"))
        
        print(f"\n    Compiled: {len(compiled)}, Blocked: {len(blocked)}")
        if blocked:
            print("    Blocked reasons:")
            for obl, reason in blocked[:8]:
                print(f"      {obl.get('obligation_id', '?')[:30]} [{obl.get('risk_family')}]: {reason}")
        
        # Detail compiled experiments
        print(f"\n    --- Compiled Experiment Details ---")
        for obl, exp in compiled:
            print(f"\n    [{obl.get('obligation_id', '?')[:25]}] family={obl.get('risk_family')}")
            assertions = exp.get("assertions", [])
            print(f"      assertions: {len(assertions)}")
            for a in assertions[:3]:
                if isinstance(a, dict):
                    print(f"        kind={a.get('kind')} fields={a.get('expected', {}).get('fields', a.get('expected', {}).get('terms', '?'))}")
            tp = exp.get("treatment_plan", [])
            steps = tp if isinstance(tp, list) else tp.get("steps", []) if isinstance(tp, dict) else []
            print(f"      treatment_steps: {len(steps)}")
            for s in steps[:2]:
                if isinstance(s, dict):
                    print(f"        {s.get('method', '?')} {str(s.get('path', '?'))[:40]}")
                
    except ImportError as e:
        print(f"    SKIP: {e}")

    # Step 4: Check conservation terms
    print("\n[4] Conservation terms verification:")
    for obl in cons_obls:
        prop = obl.get("property", {})
        expr = prop.get("expression", {})
        equation = expr.get("equation", {})
        terms = equation.get("terms", [])
        status = "OK" if terms else "EMPTY_TERMS!"
        print(f"    {obl.get('obligation_id', '?')[:30]}: terms={terms} [{status}]")

    print("\n" + "=" * 60)
    print("Test complete")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
