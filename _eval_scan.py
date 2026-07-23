"""Evaluate scan results against ground truth (131 bugs)."""
import json
from pathlib import Path

# Load scan result
sr = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
findings = sr.get("findings", [])
candidates = sr.get("candidate_findings", [])
print(f"=== Scan Result ===")
print(f"Confirmed findings: {len(findings)}")
print(f"Candidate findings: {len(candidates)}")
print(f"Execution status: {sr.get('execution_status', '')}")
print(f"Coverage: {sr.get('coverage', 0)}")
print()

# Load ground truth
gt_path = Path(r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\qualibug_enterprise_benchmark_v0_5_windows_native_stable\hidden_ground_truth\bugs.json")
if not gt_path.exists():
    gt_path = Path(r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\qualibug_enterprise_benchmark_v0_5_windows_native_stable\hidden_ground_truth")
    if gt_path.is_dir():
        candidates_gt = list(gt_path.glob("*.json"))
        if candidates_gt:
            gt_path = candidates_gt[0]
        else:
            print("Ground truth not found!")
            exit(1)

gt = json.loads(gt_path.read_text(encoding="utf-8"))
if isinstance(gt, dict):
    gt_bugs = gt.get("bugs", gt.get("defects", []))
else:
    gt_bugs = gt
print(f"Ground truth bugs: {len(gt_bugs)}")
print()

# Print findings
print("=== Confirmed Findings ===")
for f in findings:
    title = f.get("title", f.get("defect_title", ""))[:70]
    family = f.get("risk_family", f.get("category", ""))
    module = f.get("module", f.get("service", f.get("entity", "")))
    print(f"  [{family:15s}] {title} ({module})")
print()

# Match findings to ground truth
def normalize(s):
    return str(s or "").lower().strip().replace("-", " ").replace("_", " ")

def extract_keywords(text):
    text = normalize(text)
    words = set(text.split())
    return words

# Build GT index
gt_matched = set()
finding_matches = []

for f in findings + candidates:
    f_title = f.get("title", f.get("defect_title", ""))
    f_desc = f.get("description", f.get("defect_description", ""))
    f_module = normalize(f.get("module", f.get("service", f.get("entity", ""))))
    f_text = f"{f_title} {f_desc} {f_module}"
    f_keywords = extract_keywords(f_text)
    
    best_match = None
    best_score = 0
    for i, bug in enumerate(gt_bugs):
        if i in gt_matched:
            continue
        b_title = bug.get("title", bug.get("bug_title", bug.get("name", "")))
        b_desc = bug.get("description", bug.get("bug_description", ""))
        b_module = normalize(bug.get("module", bug.get("service", bug.get("component", ""))))
        b_id = bug.get("id", bug.get("bug_id", ""))
        b_text = f"{b_title} {b_desc} {b_module} {b_id}"
        b_keywords = extract_keywords(b_text)
        
        # Keyword overlap score
        if not f_keywords or not b_keywords:
            continue
        overlap = f_keywords & b_keywords
        score = len(overlap) / min(len(f_keywords), len(b_keywords)) if min(len(f_keywords), len(b_keywords)) > 0 else 0
        
        # Module match bonus
        if f_module and b_module and (f_module in b_module or b_module in f_module):
            score += 0.3
        
        if score > best_score:
            best_score = score
            best_match = (i, b_id, b_title, score)
    
    if best_match and best_match[3] >= 0.25:
        gt_matched.add(best_match[0])
        finding_matches.append((f_title[:50], best_match[1], best_match[2][:50], best_match[3]))

print(f"=== Ground Truth Matching ===")
print(f"TP (matched GT bugs): {len(gt_matched)}")
print(f"Total GT bugs: {len(gt_bugs)}")
print(f"Real recall rate: {len(gt_matched)}/{len(gt_bugs)} = {len(gt_matched)/len(gt_bugs)*100:.1f}%")
print()
print("=== Matched Pairs ===")
for f_title, b_id, b_title, score in finding_matches:
    print(f"  F: {f_title}")
    print(f"  G: [{b_id}] {b_title} (score={score:.2f})")
    print()
