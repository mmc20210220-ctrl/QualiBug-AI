"""Quick analysis of latest scan results."""
import json, sys

with open("_scan_result_latest.json", "r", encoding="utf-8") as f:
    d = json.load(f)

print(f"success: {d.get('success')}")
print(f"total_findings: {d.get('total_findings')}")
print(f"total_candidates: {d.get('total_candidates')}")
print(f"delivered_defects: {d.get('delivered_defects')}")

findings = d.get("findings", [])
print(f"findings_count: {len(findings)}")
print()

# Categorize findings
print("=== ALL FINDINGS ===")
for i, f in enumerate(findings):
    title = (f.get("title") or "?")[:80]
    sev = f.get("severity", "?")
    conf = f.get("confidence", "?")
    cat = f.get("category", "?")
    print(f"  [{i:2d}] sev={sev:8s} conf={conf:8s} cat={cat[:30]:30s} title={title}")

print()
# Check for 401 patterns
print("=== 401 PATTERN CHECK ===")
count_401 = 0
for i, f in enumerate(findings):
    evidence = f.get("evidence") or {}
    obs = evidence.get("observations") or []
    actual_codes = []
    for o in obs:
        if isinstance(o, dict):
            sc = o.get("status_code") or o.get("actual_status")
            if sc:
                actual_codes.append(int(sc))
    if 401 in actual_codes:
        count_401 += 1
        if count_401 <= 5:
            print(f"  [{i}] has 401: {actual_codes} title={f.get('title','')[:60]}")
print(f"  Total findings with 401 in evidence: {count_401}")

# Check v12 pipeline stats
v12 = d.get("v12_pipeline") or d.get("pipeline_stats") or {}
if v12:
    print(f"\n=== V12 PIPELINE STATS ===")
    for k in sorted(v12.keys())[:20]:
        v = v12[k]
        if isinstance(v, (int, float, str, bool)):
            print(f"  {k}: {v}")

# Check blocked attempts
blocked = d.get("blocked_attempts") or d.get("blocked_obligations") or []
print(f"\nblocked_attempts: {len(blocked) if isinstance(blocked, list) else blocked}")
