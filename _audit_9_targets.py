# -*- coding: utf-8 -*-
"""§6 Pre-coding audit: examine rules and experiments for 9 ORACLE_NOT_VIOLATED targets."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Load knowledge asset
ka = json.loads((ROOT / "platform_outputs/contractflow_project_c/enterprise_knowledge_center/enterprise_business_knowledge_asset.json").read_text(encoding="utf-8"))
rl = ka.get("rule_library", [])

# Load behavior IR
bir_path = ROOT / "platform_outputs/contractflow_project_c/behavior_ir.json"
bir = json.loads(bir_path.read_text(encoding="utf-8")) if bir_path.exists() else {}

# 9 target keywords
TARGET_KEYWORDS = {
    "CF-CON-001": ["版本", "409", "乐观锁", "optimistic", "version"],
    "CF-CON-003": ["里程碑金额合计", "合同总额", "金额合计等于"],
    "CF-BUD-001": ["available", "reserved", "预算守恒", "预算预留"],
    "CF-BUD-002": ["取消", "释放", "预算", "reserved"],
    "CF-PAY-001": ["取消", "付款申请", "驳回", "禁止"],
    "CF-TIME-001": ["发票日期", "付款申请日期", "不得晚于"],
    "CF-PAY-004": ["剩余额度", "剩余可付", "不得超过"],
    "CF-STATE-004": ["取消", "付款", "禁止", "合同状态"],
    "CF-BUD-003": ["负数", "reserved_amount", "不得为负"],
}

print("=" * 80)
print("§6 PRE-CODING AUDIT: 9 ORACLE_NOT_VIOLATED TARGETS")
print("=" * 80)

for bug_id, keywords in TARGET_KEYWORDS.items():
    print(f"\n{'─' * 70}")
    print(f"TARGET: {bug_id}")
    print(f"Keywords: {keywords}")
    matching_rules = []
    for r in rl:
        stmt = r.get("statement", "") or ""
        tokens = r.get("tokens", [])
        causal = json.dumps(r.get("causal_chain", {}), ensure_ascii=False) if r.get("causal_chain") else ""
        combined = stmt + " " + " ".join(tokens) + " " + causal
        if any(kw.lower() in combined.lower() for kw in keywords):
            matching_rules.append(r)
    
    print(f"Matching Rules: {len(matching_rules)}")
    for r in matching_rules[:5]:
        print(f"  {r['rule_id']} [{r.get('rule_type','')}] {r.get('statement','')[:100]}")
        cc = r.get("causal_chain", {})
        if cc:
            print(f"    causal: preconditions={cc.get('preconditions',[])} trigger={cc.get('trigger_action','')}")
            print(f"    postconditions={cc.get('postconditions',[])}")

# Check operations in Behavior IR
print(f"\n{'=' * 80}")
print("BEHAVIOR IR OPERATIONS (relevant)")
ops = bir.get("operations", [])
relevant_paths = ["/contracts", "/payment-requests", "/invoices", "/budgets", "/milestones"]
for op in ops:
    if not isinstance(op, dict):
        continue
    path = op.get("path", "") or op.get("path_template", "")
    if any(rp in path for rp in relevant_paths):
        print(f"  {op.get('id','')} {op.get('method','')} {path}")

# Check states
print(f"\n{'=' * 80}")
print("BEHAVIOR IR STATES")
states = bir.get("states", [])
for s in states[:20]:
    if isinstance(s, dict):
        print(f"  {s.get('id','')} initial={s.get('initial',False)}")

# Check relations (transitions)
print(f"\n{'=' * 80}")
print("BEHAVIOR IR TRANSITIONS")
relations = bir.get("relations", [])
for rel in relations:
    if isinstance(rel, dict) and rel.get("relation_type") == "transitions":
        print(f"  {rel.get('from_ref','')} -> {rel.get('to_ref','')} via {rel.get('operation_ref','')}")
