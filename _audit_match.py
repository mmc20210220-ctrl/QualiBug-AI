"""
Comprehensive Finding Truth Audit
- Match 33 findings against 131 ground truth bugs
- Deep audit conservation finding
- Classify each finding
- Dedup by root cause
"""
import json, sys, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load findings
with open("_scan_result_latest.json", encoding="utf-8") as f:
    result = json.load(f)
findings = result.get("findings") or []

# Load ground truth
gt_path = Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json")
with open(gt_path, encoding="utf-8") as f:
    ground_truth = json.load(f)
print(f"Ground truth bugs: {len(ground_truth)}")
print(f"Findings to audit: {len(findings)}")

# ============================================================
# MATCHING LOGIC
# ============================================================
def normalize_path(path):
    """Normalize path by replacing random IDs."""
    path = re.sub(r'qb_test_\d+', '{ID}', path)
    path = re.sub(r'QB-TEST-\w+', '{ID}', path)
    path = re.sub(r'/\d+', '/{NUM}', path)
    return path

def extract_finding_signature(f):
    """Extract matching signature from a finding."""
    ev = f.get("evidence") or {}
    raw = f.get("raw_evidence") or {}
    title = f.get("title", "")
    category = f.get("category", "")
    
    # Extract method and path
    request_str = ev.get("request", "")
    method = ""
    path = ""
    if " " in request_str:
        parts = request_str.split(" ", 1)
        method = parts[0]
        path = normalize_path(parts[1])
    
    # Extract from raw
    req_raw = raw.get("request_raw") or {}
    if not method:
        method = req_raw.get("method", "")
    if not path:
        path = normalize_path(req_raw.get("path", ""))
    
    actor = ev.get("actor", "") or req_raw.get("actor", "")
    
    # Response status
    resp_raw = raw.get("response_raw") or {}
    status_code = resp_raw.get("status_code", 0)
    
    return {
        "method": method,
        "path": path,
        "actor": actor,
        "status_code": status_code,
        "category": category,
        "title": title,
    }

def match_finding_to_bug(finding, bugs):
    """Match a finding to ground truth bugs. Returns list of (bug, score)."""
    sig = extract_finding_signature(finding)
    matches = []
    
    for bug in bugs:
        score = 0
        reasons = []
        keywords = bug.get("match_keywords", [])
        trigger = bug.get("trigger", "")
        bug_path = ""
        
        # Extract path from trigger
        path_match = re.search(r'(/api/[^\s,，]+)', trigger)
        if path_match:
            bug_path = normalize_path(path_match.group(1))
        
        # Path matching
        if sig["path"] and bug_path:
            if sig["path"] == bug_path:
                score += 40
                reasons.append(f"path_exact:{sig['path']}")
            elif bug_path.split("{")[0] in sig["path"] or sig["path"].split("{")[0] in bug_path:
                score += 25
                reasons.append(f"path_partial:{sig['path']}~{bug_path}")
        
        # Keyword matching in title/path
        finding_text = f"{sig['title']} {sig['path']} {sig['method']} {sig['actor']}".lower()
        kw_hits = 0
        for kw in keywords:
            if kw.lower() in finding_text:
                kw_hits += 1
        if kw_hits >= 2:
            score += 20
            reasons.append(f"keywords:{kw_hits}")
        elif kw_hits == 1:
            score += 10
            reasons.append(f"keyword:{kw_hits}")
        
        # Category/type alignment
        bug_type = bug.get("type", "")
        if sig["category"] == "owner_tenant_visibility" and "越权" in bug_type:
            score += 15
            reasons.append("type:越权")
        elif sig["category"] == "http_status_class" and ("权限" in bug_type or "越权" in bug_type):
            score += 10
            reasons.append("type:权限")
        elif sig["category"] == "validation_rejection" and "校验" in bug_type:
            score += 15
            reasons.append("type:校验")
        elif sig["category"] == "conservation" and "守恒" in bug_type:
            score += 20
            reasons.append("type:守恒")
        
        # Actor matching
        if sig["actor"] and sig["actor"] in trigger:
            score += 10
            reasons.append(f"actor:{sig['actor']}")
        
        # Status code matching
        if sig["status_code"] == 200 and "成功" in bug.get("actual", ""):
            score += 5
            reasons.append("status:200_success")
        elif sig["status_code"] == 500 and "500" in bug.get("actual", ""):
            score += 10
            reasons.append("status:500")
        
        if score >= 25:
            matches.append((bug, score, reasons))
    
    matches.sort(key=lambda x: -x[1])
    return matches

# ============================================================
# RUN MATCHING
# ============================================================
print(f"\n{'='*80}")
print("FINDING-TO-BUG MATCHING")
print(f"{'='*80}")

matched_bugs = set()  # Track unique bugs matched
finding_classifications = []

for i, f in enumerate(findings):
    sig = extract_finding_signature(f)
    matches = match_finding_to_bug(f, ground_truth)
    
    best_match = matches[0] if matches else None
    classification = {
        "index": i,
        "finding_id": f.get("finding_id"),
        "category": f.get("category"),
        "risk_family": f.get("risk_family"),
        "title": f.get("title"),
        "method": sig["method"],
        "path": sig["path"],
        "actor": sig["actor"],
        "status_code": sig["status_code"],
        "control_succeeded": (f.get("evidence") or {}).get("control_succeeded"),
        "execution_semantics": (f.get("evidence") or {}).get("execution_semantics"),
    }
    
    if best_match and best_match[1] >= 35:
        bug, score, reasons = best_match
        bug_id = bug["bug_id"]
        is_duplicate = bug_id in matched_bugs
        matched_bugs.add(bug_id)
        
        classification["matched_bug_id"] = bug_id
        classification["match_score"] = score
        classification["match_reasons"] = reasons
        classification["match_status"] = "DUPLICATE_TP" if is_duplicate else "UNIQUE_TP"
        classification["bug_title"] = bug.get("title", "")
    elif best_match and best_match[1] >= 25:
        bug, score, reasons = best_match
        classification["matched_bug_id"] = bug["bug_id"]
        classification["match_score"] = score
        classification["match_reasons"] = reasons
        classification["match_status"] = "WEAK_MATCH"
        classification["bug_title"] = bug.get("title", "")
    else:
        classification["matched_bug_id"] = None
        classification["match_score"] = 0
        classification["match_reasons"] = []
        classification["match_status"] = "NO_MATCH"
    
    finding_classifications.append(classification)
    
    # Print
    status = classification["match_status"]
    bug_info = f"→ {classification.get('matched_bug_id', 'NONE')}" if classification.get('matched_bug_id') else "→ NO MATCH"
    print(f"  [{i:2d}] {f.get('category'):<25} {sig['method']:<5} {sig['path'][:40]:<40} {bug_info} (score={classification['match_score']})")

# ============================================================
# SUMMARY STATISTICS
# ============================================================
print(f"\n{'='*80}")
print("MATCHING SUMMARY")
print(f"{'='*80}")

unique_tp = sum(1 for c in finding_classifications if c["match_status"] == "UNIQUE_TP")
duplicate_tp = sum(1 for c in finding_classifications if c["match_status"] == "DUPLICATE_TP")
weak_match = sum(1 for c in finding_classifications if c["match_status"] == "WEAK_MATCH")
no_match = sum(1 for c in finding_classifications if c["match_status"] == "NO_MATCH")

print(f"  UNIQUE_TP: {unique_tp}")
print(f"  DUPLICATE_TP: {duplicate_tp}")
print(f"  WEAK_MATCH: {weak_match}")
print(f"  NO_MATCH: {no_match}")
print(f"  Total unique bugs matched: {len(matched_bugs)}")
print(f"  Recall: {len(matched_bugs)}/131 = {len(matched_bugs)/131*100:.1f}%")
print(f"  Precision (unique_tp / total_findings): {unique_tp}/{len(findings)} = {unique_tp/len(findings)*100:.1f}%")

# List unique TP matches
print(f"\n=== UNIQUE TP MATCHES ===")
for c in finding_classifications:
    if c["match_status"] == "UNIQUE_TP":
        print(f"  {c['finding_id']} → {c['matched_bug_id']}: {c.get('bug_title', '')}")
        print(f"    {c['method']} {c['path']} actor={c['actor']} score={c['match_score']}")

# List NO_MATCH findings (potential FP or new bugs)
print(f"\n=== NO_MATCH FINDINGS (need manual review) ===")
for c in finding_classifications:
    if c["match_status"] == "NO_MATCH":
        print(f"  {c['finding_id']} [{c['category']}] {c['method']} {c['path']} actor={c['actor']} status={c['status_code']}")

# Save classifications
Path("_audit_classifications.json").write_text(
    json.dumps(finding_classifications, indent=2, ensure_ascii=False, default=str),
    encoding="utf-8"
)
print(f"\nSaved classifications to _audit_classifications.json")
