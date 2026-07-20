"""Check Behavior IR conflicts."""
import json

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
bir = v12.get('behavior_ir', {})
conflicts = bir.get('conflicts', [])
print(f"Behavior IR conflicts: {len(conflicts)}")

for c in conflicts[:10]:
    if isinstance(c, dict):
        cid = c.get('id', '')[:30]
        op = c.get('operation_ref', '')
        status = c.get('status', '')
        print(f"  {cid}: op={op}, status={status}")
