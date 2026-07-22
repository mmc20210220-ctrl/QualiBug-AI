"""Check scan failure details."""
import json, sys, os
from pathlib import Path
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Read stderr
stderr = Path("_scan_stderr.log").read_text("utf-8", errors="replace")
lines = stderr.strip().split("\n")
print(f"=== STDERR ({len(lines)} lines) ===")
for line in lines[-60:]:
    print(line)

# Read scan result top-level
print("\n=== SCAN RESULT TOP-LEVEL ===")
try:
    d = json.loads(Path("_scan_result_latest.json").read_text("utf-8", errors="replace"))
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, (int, float, str, bool, type(None))):
            print(f"  {k}: {v}")
        elif isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)}]")
    
    # Check pipeline health
    ph = d.get("pipeline_health") or {}
    if ph:
        print("\n=== PIPELINE HEALTH ===")
        for k in sorted(ph.keys()):
            v = ph[k]
            if isinstance(v, (int, float, str, bool)):
                print(f"  {k}: {v}")
    
    # Check v12
    v12 = d.get("v12") or {}
    if v12:
        print("\n=== V12 STATS ===")
        for k in sorted(v12.keys()):
            v = v12[k]
            if isinstance(v, (int, float, str, bool)):
                print(f"  {k}: {v}")
    
    # Check error/failure info
    print("\n=== ERROR INFO ===")
    print(f"  success: {d.get('success')}")
    print(f"  error: {d.get('error')}")
    print(f"  failure_reason: {d.get('failure_reason')}")
    print(f"  execution_status: {d.get('execution_status')}")
    
    # Discovery funnel
    funnel = d.get("discovery_funnel") or {}
    if funnel:
        print("\n=== DISCOVERY FUNNEL ===")
        for k, v in sorted(funnel.items()):
            if isinstance(v, (int, float, str)):
                print(f"  {k}: {v}")
except Exception as e:
    print(f"  Error reading result: {e}")
