"""Inspect Behavior IR transitions/compensates/conserves relations."""
import json

r = json.load(open("_scan_result.json", encoding="utf-8"))
bir = r["v12"]["behavior_ir"]
ops = {o["id"]: o for o in bir["operations"] if isinstance(o, dict)}


def opdesc(ref):
    o = ops.get(ref, {})
    return f"{o.get('method','?')} {o.get('path','?')}"


for rt in ["compensates", "transitions", "conserves", "produces"]:
    rels = [rel for rel in bir["relations"] if isinstance(rel, dict) and rel.get("relation_type") == rt]
    print(f"=== {rt} ({len(rels)}) ===")
    for rel in rels[:14]:
        fr = rel.get("from_ref") or rel.get("operation_ref")
        to = rel.get("to_ref")
        extra = rel.get("effect") or rel.get("to_state") or rel.get("invariant") or ""
        print(f"  {opdesc(fr)}  ->  {opdesc(to)}   {str(extra)[:60]}")
