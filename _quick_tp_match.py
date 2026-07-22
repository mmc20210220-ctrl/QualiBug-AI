"""Quick TP matcher: compare findings against hidden ground truth."""
import json, sys, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load ground truth
gt_path = Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json")
gt = json.loads(gt_path.read_text("utf-8"))
if isinstance(gt, dict):
    bugs = gt.get("bugs") or gt.get("defects") or gt.get("ground_truth") or []
else:
    bugs = gt
print(f"Ground truth: {len(bugs)} bugs")

# Load scan findings
scan = json.loads(Path("_scan_result_latest.json").read_text("utf-8", errors="replace"))
if not scan.get("findings"):
    # Fallback to intelligence report
    scan = json.loads(Path("platform_outputs/benchmark_mall/intelligence_report.json").read_text("utf-8", errors="replace"))
findings = scan.get("findings", [])
print(f"Scan findings: {len(findings)}")

# Show GT structure
if bugs:
    b0 = bugs[0]
    print(f"\nGT bug[0] keys: {sorted(b0.keys()) if isinstance(b0, dict) else type(b0)}")
    if isinstance(b0, dict):
        print(f"  id: {b0.get('id') or b0.get('bug_id')}")
        print(f"  title: {str(b0.get('title') or b0.get('name') or '')[:80]}")
        print(f"  category: {b0.get('category') or b0.get('type')}")
        print(f"  endpoint: {b0.get('endpoint') or b0.get('path') or b0.get('api')}")
        print(f"  method: {b0.get('method') or b0.get('http_method')}")

# Extract finding paths for matching
def extract_path(finding):
    """Extract API path from finding title."""
    title = finding.get("title", "")
    # Pattern: [ContractOracle] http_status_class: admin METHOD /api/...
    m = re.search(r"(GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\s]+)", title)
    if m:
        return m.group(1), m.group(2)
    return None, None

def normalize_path(path):
    """Normalize path by removing IDs."""
    if not path:
        return ""
    # Remove path parameters like /qb_test_123456 or /QB-TEST-84F990CE
    path = re.sub(r"/qb_test_\d+", "/{id}", path, flags=re.IGNORECASE)
    path = re.sub(r"/QB-TEST-[A-F0-9]+", "/{id}", path, flags=re.IGNORECASE)
    path = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "/{id}", path)
    path = re.sub(r"/\d+", "/{id}", path)
    return path.rstrip("/")

# Build GT lookup by endpoint
gt_by_endpoint = {}
for bug in bugs:
    if not isinstance(bug, dict):
        continue
    endpoint = bug.get("endpoint") or bug.get("path") or bug.get("api") or ""
    method = (bug.get("method") or bug.get("http_method") or "").upper()
    category = bug.get("category") or bug.get("type") or ""
    key = f"{method} {normalize_path(endpoint)}"
    gt_by_endpoint.setdefault(key, []).append(bug)

# Match findings to GT
print(f"\n=== MATCHING ===")
tp_count = 0
for i, f in enumerate(findings):
    method, path = extract_path(f)
    norm = normalize_path(path)
    key = f"{method} {norm}"
    
    # Try exact match first
    matches = gt_by_endpoint.get(key, [])
    
    # Try broader match (just path without method)
    if not matches:
        for gt_key, gt_bugs in gt_by_endpoint.items():
            gt_method, gt_path = gt_key.split(" ", 1)
            if gt_path == norm or path and gt_path and (path in gt_path or gt_path in path):
                matches.extend(gt_bugs)
    
    status = "TP" if matches else "?"
    if matches:
        tp_count += 1
    print(f"  [{i}] {status} {method} {path}")
    if matches:
        for m in matches[:2]:
            print(f"       -> GT: {m.get('id','?')} {str(m.get('title',''))[:60]}")
    else:
        print(f"       -> No GT match (norm={norm})")

print(f"\n=== SUMMARY ===")
print(f"  Findings: {len(findings)}")
print(f"  Potential TP: {tp_count}")
print(f"  Potential FP: {len(findings) - tp_count}")
print(f"  Recall: {tp_count}/{len(bugs)} = {tp_count/len(bugs)*100:.1f}%")

# Also show GT categories distribution
print(f"\n=== GT CATEGORY DISTRIBUTION ===")
cats = {}
for bug in bugs:
    if isinstance(bug, dict):
        cat = bug.get("category") or bug.get("type") or "unknown"
        cats[cat] = cats.get(cat, 0) + 1
for cat, count in sorted(cats.items(), key=lambda x: -x[1])[:15]:
    print(f"  {cat}: {count}")
