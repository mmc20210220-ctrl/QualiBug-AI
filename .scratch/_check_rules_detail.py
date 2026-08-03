# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
ROOT = Path(__file__).resolve().parent
d = json.loads((ROOT / "platform_outputs/contractflow_project_c/enterprise_knowledge_center/enterprise_business_knowledge_asset.json").read_text(encoding="utf-8"))
rl = d.get("rule_library", [])

print("=== ALL RULES BY TYPE ===")
for rt in ["conservation", "state_transition", "idempotency", "business_rule", "permission", "reconciliation"]:
    rules = [r for r in rl if r.get("rule_type") == rt]
    print(f"\n--- {rt} ({len(rules)}) ---")
    for r in rules[:6]:
        stmt = r.get("statement", "?")
        tokens = r.get("tokens", [])
        print(f"  {r['rule_id']}: {stmt[:100]}")
        if tokens:
            print(f"    tokens: {tokens[:5]}")
