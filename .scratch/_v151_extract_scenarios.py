"""V1.5.1 Phase 3: Extract multi-step scenarios from enterprise materials."""
import json
import sys
sys.path.insert(0, ".")

asset = json.load(open(
    "platform_workspace/benchmark_mall_131/defect_discovery/enterprise_business_knowledge_asset.json",
    "r", encoding="utf-8"
))

# State machines
sm = asset.get("state_machines", [])
print(f"=== State Machines: {len(sm)} ===")
for m in sm:
    entity = m.get("entity", "?")
    states = m.get("states", [])
    transitions = m.get("transitions", [])
    print(f"  {entity}: states={states}")
    for t in transitions[:5]:
        print(f"    {t.get('from','?')} --[{t.get('action','?')}]--> {t.get('to','?')}")

# Interfaces (API operations)
interfaces = asset.get("interfaces", [])
print(f"\n=== Interfaces: {len(interfaces)} ===")
for iface in interfaces[:30]:
    method = iface.get("method", "?")
    path = iface.get("path", "?")
    name = iface.get("name", iface.get("operation_id", "?"))
    print(f"  {method} {path} ({name})")

# Business objects
biz = asset.get("business_objects", [])
print(f"\n=== Business Objects: {len(biz)} ===")
for b in biz[:15]:
    name = b.get("name", b.get("entity", "?"))
    fields = b.get("fields", b.get("attributes", []))
    print(f"  {name}: {len(fields)} fields")

# Rule library
rules = asset.get("rule_library", [])
print(f"\n=== Rules: {len(rules)} ===")
for r in rules[:10]:
    rid = r.get("rule_id", r.get("id", "?"))
    desc = r.get("description", r.get("rule", "?"))[:80]
    print(f"  {rid}: {desc}")
