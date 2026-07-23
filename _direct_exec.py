#!/usr/bin/env python
"""P0-11: Direct experiment execution against live target.

Bypasses campaign/source provenance to directly execute compiled experiments
using the real experiment_executor against localhost:8080.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Set env for token resolution
os.environ.setdefault("QUALIBUG_TARGET_BASE_URL", "http://localhost:8080")
os.environ.setdefault("QUALIBUG_LOGIN_PATH", "/api/auth/login")

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler_base import compile_obligations_from_behavior_ir
from ai_test_asset_center.experiment_compiler_obligation import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.experiment_runtime_support import load_actor_tokens


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
    root = Path(".")
    project = "benchmark_mall"
    base_url = "http://localhost:8080"

    print("=" * 60)
    print("P0-11: Direct Experiment Execution Against Live Target")
    print("=" * 60)

    # Step 1: Build IR with runtime actors
    print("\n[1] Building Behavior IR with runtime actors...")
    asset_path = Path("platform_outputs/benchmark_mall/enterprise_knowledge_center/enterprise_business_knowledge_asset.json")
    asset = json.loads(asset_path.read_text(encoding="utf-8"))
    runtime_actors = _parse_test_accounts_md(Path("projects/benchmark_mall/input/TEST_ACCOUNTS.md"))
    ir = build_behavior_ir_from_knowledge_asset(
        asset, project_id=project, runtime_actors=runtime_actors
    )
    print(f"    Actors: {len(ir.get('actors', []))}, Operations: {len(ir.get('operations', []))}")

    # Step 2: Load actor tokens (login to target)
    print("\n[2] Loading actor tokens from target...")
    tokens = load_actor_tokens(root, project)
    print(f"    Tokens loaded: {len(tokens)}")
    if tokens:
        roles = [k for k in tokens if not k.startswith("secret_ref:")]
        print(f"    Roles: {roles[:10]}")
    else:
        print("    WARNING: No tokens! Experiments will be blocked.")
        print("    Trying direct login...")
        # Try direct login
        import urllib.request
        try:
            data = json.dumps({"email": "admin@example.com", "password": "Admin@123456"}).encode()
            req = urllib.request.Request(
                f"{base_url}/api/auth/login",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
                token = body.get("token") or body.get("access_token") or (body.get("data") or {}).get("token")
                if token:
                    tokens["admin"] = token
                    tokens["admin@example.com"] = token
                    tokens["secret_ref:test_accounts:admin"] = token
                    print(f"    Direct login OK, token: {token[:20]}...")
        except Exception as e:
            print(f"    Direct login FAILED: {e}")

    # Step 3: Compile obligations and filter state/conservation
    print("\n[3] Compiling obligations...")
    obligations_result = compile_obligations_from_behavior_ir(ir)
    obligations = obligations_result.get("obligations", [])
    
    # Focus on conservation and state (non-authorization)
    target_families = {"conservation", "state"}
    target_obls = [o for o in obligations if o.get("risk_family") in target_families]
    print(f"    Total obligations: {len(obligations)}")
    print(f"    Target (state+conservation): {len(target_obls)}")

    # Step 4: Compile experiments
    print("\n[4] Compiling experiments...")
    compiled_experiments = []
    for obl in target_obls:
        exp = compile_experiment_for_obligation(
            obligation=obl,
            behavior_ir=ir,
            environment_type="test",
        )
        receipt = exp.get("compile_receipt", {})
        status = receipt.get("status", "UNKNOWN") if isinstance(receipt, dict) else "UNKNOWN"
        if status not in ("BLOCKED", "DEFERRED"):
            compiled_experiments.append((obl, exp))
        else:
            reason = receipt.get("reason_code", "?")
            print(f"    BLOCKED: {obl.get('obligation_id', '?')[:25]} [{obl.get('risk_family')}]: {reason}")
    
    print(f"    Compiled: {len(compiled_experiments)}")

    # Step 5: Execute experiments
    print("\n[5] Executing experiments against target...")
    campaign_id = f"campaign_direct_{uuid.uuid4().hex[:12]}"
    execution_id = f"exec_{uuid.uuid4().hex[:12]}"
    runtime_contract = {
        "environment_type": "test",
        "environment_ref": "test",
        "base_url": base_url,
        "approved_base_url": base_url,
        "status": "approved",
        "execution_mode": "approved_sandbox_write",
    }

    results = []
    for obl, exp in compiled_experiments:  # Execute ALL
        oid = obl.get("obligation_id", "?")
        family = obl.get("risk_family", "?")
        print(f"\n    --- Executing {oid[:25]} [{family}] ---")
        try:
            result = execute_one_experiment(
                exp,
                behavior_ir=ir,
                root=root,
                project=project,
                base_url=base_url,
                runtime_contract=runtime_contract,
                campaign_id=campaign_id,
                execution_id=execution_id,
                actor_tokens=tokens,
                best_effort=True,
            )
            status = result.get("status", "?")
            reason = result.get("reason_code", "")
            finding = result.get("finding")
            print(f"      Status: {status}")
            if reason:
                print(f"      Reason: {reason}")
            if finding:
                print(f"      FINDING: {finding.get('title', '?')[:60]}")
                print(f"      Category: {finding.get('category')}")
                print(f"      Severity: {finding.get('severity')}")
            
            # Check oracle trace
            obs = result.get("observations", {})
            oracle_trace = obs.get("oracle_trace", [])
            if oracle_trace:
                print(f"      Oracle trace ({len(oracle_trace)} entries):")
                for t in oracle_trace[:2]:
                    print(f"        kind={t.get('kind')} result={t.get('result')}")
                    if t.get("kind") == "conservation":
                        print(f"          terms={t.get('terms')} before_sum={t.get('before_sum')} after_sum={t.get('after_sum')}")
                    elif t.get("kind") == "state_transition":
                        print(f"          from={t.get('from_state')} to={t.get('to_state')}")
            
            results.append(result)
        except Exception as e:
            import traceback
            print(f"      EXCEPTION: {e}")
            traceback.print_exc()
            results.append({"status": "ERROR", "error": str(e)})

    # Summary
    print("\n" + "=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)
    statuses = {}
    findings_count = 0
    for r in results:
        s = r.get("status", "ERROR")
        statuses[s] = statuses.get(s, 0) + 1
        if r.get("finding"):
            findings_count += 1
    print(f"  Total executed: {len(results)}")
    print(f"  Status distribution: {statuses}")
    print(f"  Findings produced: {findings_count}")

    # Save full results
    out_path = Path("_direct_exec_results.json")
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"  Full results saved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
