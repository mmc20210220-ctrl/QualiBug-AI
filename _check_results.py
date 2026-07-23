"""Check scan results after timeout."""
from pathlib import Path
import json

# Check for scan results
proj_dir = Path("platform_outputs/qb_ecommerce_single_retest")
if proj_dir.exists():
    print(f"Project dir exists: {proj_dir}")
    for f in sorted(proj_dir.glob("*.json"))[:10]:
        print(f"  {f.name}")
        if f.name in ["intelligence_report.json", "scan_result.json"]:
            try:
                d = json.load(open(f, "r", encoding="utf-8"))
                print(f"    keys: {list(d.keys())[:10]}")
                tf = d.get("total_findings")
                if tf is not None:
                    print(f"    total_findings: {tf}")
            except Exception as e:
                print(f"    error: {e}")
else:
    print(f"Project dir not found: {proj_dir}")

# Check for real_project_demo (the ingest showed this path)
demo_dir = Path("platform_outputs/real_project_demo")
if demo_dir.exists():
    print(f"\nreal_project_demo dir exists")
    for f in sorted(demo_dir.glob("*.json"))[:5]:
        print(f"  {f.name}")
        if f.name in ["intelligence_report.json", "scan_result.json"]:
            try:
                d = json.load(open(f, "r", encoding="utf-8"))
                tf = d.get("total_findings")
                if tf is not None:
                    print(f"    total_findings: {tf}")
            except:
                pass

# Check platform_workspace for experiment files
ws_dir = Path("platform_workspace/qb_ecommerce_single_retest")
if ws_dir.exists():
    print(f"\nWorkspace dir exists: {ws_dir}")
    exp_files = list(ws_dir.rglob("*experiment*.json"))
    print(f"Experiment files: {len(exp_files)}")
    for f in exp_files[:5]:
        print(f"  {f.relative_to(ws_dir)}")
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
