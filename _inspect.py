import json
from pathlib import Path
from collections import Counter

root = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
d = json.load(open(root / "_funnel_runs" / "llm_throughput.json", encoding="utf-8"))
full = d["full_result"]
cand = full.get("candidate_findings") or []
nme = [f for f in cand if "NEEDS_MORE" in str(f.get("final_review_status") or "").upper()]

# Categorize the ROOT technical cause of each NME by scanning reproduction/evidence
causes = Counter()
samples = {}
for f in nme:
    rep = f.get("reproduction") or {}
    har = rep.get("har_evidence") or {}
    body = har.get("response_body") or {}
    baa = f.get("before_after_snapshot") or {}
    before = baa.get("before") or {}
    err_blob = json.dumps({"har": body, "before": before, "steps": f.get("reproduction_steps")}, ensure_ascii=False, default=str).lower()
    if "missing_runtime_path_binding" in err_blob:
        cause = "missing_runtime_path_binding (unresolved {id})"
    elif "uuid" in err_blob and ("<" in err_blob or "语法" in err_blob or "syntax" in err_blob):
        cause = "unfilled body placeholder (<xxx_id> not resolved)"
    elif "http 500" in err_blob or "status_code\": 500" in err_blob or '"status_code": 500' in err_blob:
        cause = "server_5xx (other)"
    elif "http 0" in err_blob or '"status_code": 0' in err_blob:
        cause = "no_response (status 0)"
    else:
        cause = "other"
    causes[cause] += 1
    samples.setdefault(cause, err_blob[:280])

print("ROOT CAUSE of 122 NME pending findings:")
for k, v in causes.most_common():
    print(f"  {v:4d}  {k}")
print()
for k, s in samples.items():
    print(f"--- sample [{k}] ---")
    print("   ", s[:260])
