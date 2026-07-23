#!/usr/bin/env python
"""P0-12: Reproduce conservation finding and verify deduplication."""
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("QUALIBUG_TARGET_BASE_URL", "http://localhost:8080")
os.environ.setdefault("QUALIBUG_LOGIN_PATH", "/api/auth/login")

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler_base import compile_obligations_from_behavior_ir
from ai_test_asset_center.experiment_compiler_obligation import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.experiment_runtime_support import load_actor_tokens


def _parse_test_accounts_md(path: Path) -> list:
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
            role_map = {"管理员": "admin", "财务": "finance", "商家": "seller",
                        "仓库": "warehouse", "审计": "auditor", "普通买家": "buyer",
                        "禁用买家": "disabled_buyer"}
            role = role_map.get(role_cn, role_cn)
            accounts.append({"role": role, "email": email, "password": password,
                             "account_ref": email.split("@")[0] if "@" in email else email,
                             "status": "disabled" if "禁用" in role_cn else "active"})
    return accounts


def main():
    root = Path(".")
    project = "benchmark_mall"
    base_url = "http://localhost:8080"

    print("=" * 60)
    print("P0-12: Reproducibility & Deduplication Test")
    print("=" * 60)

    # Build IR
    asset = json.loads(Path("platform_outputs/benchmark_mall/enterprise_knowledge_center/enterprise_business_knowledge_asset.json").read_text(encoding="utf-8"))
    runtime_actors = _parse_test_accounts_md(Path("projects/benchmark_mall/input/TEST_ACCOUNTS.md"))
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id=project, runtime_actors=runtime_actors)

    # Load tokens
    tokens = load_actor_tokens(root, project)
    print(f"Tokens: {len(tokens)}")

    # Compile obligations - find conservation ones
    obligations_result = compile_obligations_from_behavior_ir(ir)
    obligations = obligations_result.get("obligations", [])
    cons_obls = [o for o in obligations if o.get("risk_family") == "conservation"]
    print(f"Conservation obligations: {len(cons_obls)}")

    # Run conservation experiment 3 times to verify reproducibility
    runtime_contract = {
        "environment_type": "test",
        "environment_ref": "test",
        "base_url": base_url,
        "approved_base_url": base_url,
        "status": "approved",
        "execution_mode": "approved_sandbox_write",
    }

    findings = []
    for run_idx in range(3):
        campaign_id = f"campaign_repro_{uuid.uuid4().hex[:8]}"
        execution_id = f"exec_{uuid.uuid4().hex[:8]}"
        print(f"\n--- Run {run_idx + 1}/3 (campaign={campaign_id}) ---")

        for obl in cons_obls:
            exp = compile_experiment_for_obligation(
                obligation=obl, behavior_ir=ir, environment_type="test"
            )
            receipt = exp.get("compile_receipt", {})
            if isinstance(receipt, dict) and receipt.get("status") in ("BLOCKED", "DEFERRED"):
                continue

            result = execute_one_experiment(
                exp, behavior_ir=ir, root=root, project=project,
                base_url=base_url, runtime_contract=runtime_contract,
                campaign_id=campaign_id, execution_id=execution_id,
                actor_tokens=tokens, best_effort=True,
            )
            finding = result.get("finding")
            if finding:
                print(f"  FINDING: {finding.get('title', '?')[:60]}")
                print(f"    severity={finding.get('severity')} category={finding.get('category')}")
                print(f"    experiment_id={finding.get('experiment_id')}")
                findings.append(finding)
            else:
                status = result.get("status", "?")
                reason = result.get("reason_code", "")
                print(f"  {obl.get('obligation_id', '?')[:25]}: {status} {reason}")

    # Deduplication analysis
    print(f"\n{'='*60}")
    print("REPRODUCIBILITY & DEDUP ANALYSIS")
    print(f"{'='*60}")
    print(f"Total findings across 3 runs: {len(findings)}")

    # Check dedup by experiment_id (stable across runs)
    exp_ids = set()
    titles = set()
    for f in findings:
        exp_ids.add(f.get("experiment_id", ""))
        titles.add(f.get("title", ""))
    
    print(f"Unique experiment_ids: {len(exp_ids)}")
    print(f"Unique titles: {len(titles)}")
    for eid in exp_ids:
        count = sum(1 for f in findings if f.get("experiment_id") == eid)
        print(f"  {eid}: {count} occurrences")

    # Verify canonical defect identity
    print(f"\nDedup verdict:")
    if len(exp_ids) == 1 and len(findings) >= 2:
        print("  PASS: Same defect reproduced multiple times, stable experiment_id")
        print("  Canonical defect count should be 1 (deduplicated)")
    elif len(findings) >= 1:
        print(f"  PARTIAL: {len(findings)} findings, {len(exp_ids)} unique experiments")
    else:
        print("  FAIL: No findings produced")

    # Check canonical_defect_registry if available
    try:
        from ai_test_asset_center.canonical_defect_registry import register_defect_occurrence
        print("\n  canonical_defect_registry: available")
    except ImportError:
        print("\n  canonical_defect_registry: not importable (ok for direct test)")

    return 0 if findings else 1


if __name__ == "__main__":
    sys.exit(main())
