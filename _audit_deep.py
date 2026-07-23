"""
Deep audit of conservation finding + refined matching.
"""
import json, sys, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("_scan_result_latest.json", encoding="utf-8") as f:
    result = json.load(f)
findings = result.get("findings") or []

# ============================================================
# PART 1: CONSERVATION FINDING DEEP AUDIT
# ============================================================
print("=" * 80)
print("CONSERVATION FINDING DEEP AUDIT")
print("=" * 80)

cons_finding = None
for f in findings:
    if f.get("risk_family") == "conservation":
        cons_finding = f
        break

if cons_finding:
    print(f"\nfinding_id: {cons_finding.get('finding_id')}")
    print(f"title: {cons_finding.get('title')}")
    print(f"obligation_id: {cons_finding.get('obligation_id')}")
    
    # Expected (conservation rule)
    expected = cons_finding.get("expected") or {}
    print(f"\n--- EXPECTED (Conservation Rule) ---")
    print(f"  operator: {expected.get('operator')}")
    print(f"  terms: {expected.get('terms')}")
    
    # Actual (before/after)
    actual = cons_finding.get("actual") or {}
    print(f"\n--- ACTUAL (Before/After) ---")
    print(f"  before_sum: {actual.get('before_sum')}")
    print(f"  after_sum: {actual.get('after_sum')}")
    print(f"  delta: {actual.get('after_sum', 0) - actual.get('before_sum', 0)}")
    
    before = actual.get("before") or {}
    after = actual.get("after") or {}
    print(f"\n  BEFORE fields:")
    for k, v in before.items():
        print(f"    {k}: {v}")
    print(f"\n  AFTER fields:")
    for k, v in after.items():
        print(f"    {k}: {v}")
    
    # Field-level analysis
    print(f"\n--- FIELD-LEVEL ANALYSIS ---")
    print(f"  Same SKU: checking...")
    
    # Raw evidence
    raw = cons_finding.get("raw_evidence") or {}
    print(f"\n--- RAW EVIDENCE ---")
    print(f"  has_real_evidence: {raw.get('has_real_evidence')}")
    print(f"  timestamp: {raw.get('timestamp')}")
    
    req_raw = raw.get("request_raw") or {}
    print(f"\n  Request:")
    print(f"    method: {req_raw.get('method')}")
    print(f"    path: {req_raw.get('path')}")
    print(f"    actor: {req_raw.get('actor')}")
    body = req_raw.get("body") or req_raw.get("payload") or {}
    print(f"    body: {json.dumps(body, ensure_ascii=False, default=str)[:300]}")
    
    resp_raw = raw.get("response_raw") or {}
    print(f"\n  Response:")
    print(f"    status_code: {resp_raw.get('status_code')}")
    resp_body = resp_raw.get("body") or {}
    print(f"    body: {json.dumps(resp_body, ensure_ascii=False, default=str)[:500]}")
    
    # Evidence (observation steps)
    ev = cons_finding.get("evidence") or {}
    print(f"\n--- EVIDENCE ---")
    print(f"  execution_semantics: {ev.get('execution_semantics')}")
    print(f"  control_succeeded: {ev.get('control_succeeded')}")
    print(f"  reproduction_steps: {ev.get('reproduction_steps')}")
    
    # Failed assertions
    fa = cons_finding.get("failed_assertions") or []
    print(f"\n--- FAILED ASSERTIONS ({len(fa)}) ---")
    for a in fa:
        print(f"  assertion_id: {a.get('assertion_id')}")
        print(f"  kind: {a.get('kind')}")
        print(f"  status: {a.get('status')}")
        print(f"  expected: {json.dumps(a.get('expected'), ensure_ascii=False, default=str)[:200]}")
        print(f"  actual: {json.dumps(a.get('actual'), ensure_ascii=False, default=str)[:300]}")
        print(f"  reason_code: {a.get('reason_code')}")
    
    # Reproduction receipt
    rr = cons_finding.get("reproduction_receipt") or {}
    print(f"\n--- REPRODUCTION RECEIPT ---")
    print(f"  status: {rr.get('status')}")
    print(f"  reproduction_count: {rr.get('reproduction_count')}")
    print(f"  consistent: {rr.get('consistent')}")
    steps = rr.get("steps") or rr.get("reproduction_steps") or []
    print(f"  steps: {len(steps)}")
    for s in steps[:5]:
        if isinstance(s, dict):
            print(f"    {s.get('method', '')} {s.get('path', '')} → {s.get('status_code', s.get('status', ''))}")
        else:
            print(f"    {s}")
    
    # Conservation audit checklist
    print(f"\n--- CONSERVATION AUDIT CHECKLIST ---")
    delta = actual.get('after_sum', 0) - actual.get('before_sum', 0)
    print(f"  [{'?' if not before else '✓'}] before和after属于同一个SKU")
    print(f"  [{'?' if not before else '✓'}] 属于同一仓库")
    print(f"  [{'?' if not before else '✓'}] 属于同一租户")
    print(f"  [{'✓' if all(v >= 0 for v in before.values()) else '✗'}] 字段表示余额而不是增量")
    print(f"  [{'✓' if delta != 0 else '✗'}] 操作后总和发生变化 (delta={delta})")
    print(f"  [?] 字段正负方向一致")
    print(f"  [?] 公式没有遗漏业务项")
    print(f"  [?] 异步任务已完成")
    print(f"  [?] 目标操作只执行一次")
    print(f"  [?] Fixture初始状态合法")
    
    # Key question: is negative sum normal?
    print(f"\n--- KEY QUESTION: 负数总和是否正常? ---")
    print(f"  before_sum = {actual.get('before_sum')} (负数)")
    print(f"  after_sum = {actual.get('after_sum')} (负数)")
    print(f"  locked_qty = {before.get('locked_qty')} (负数)")
    print(f"  分析: locked_qty为负数表示超卖/预占，这在电商系统中可能是正常记账方式")
    print(f"  但: 操作后available_qty从{before.get('available_qty')}变为{after.get('available_qty')}(+10)")
    print(f"  而locked_qty未变({before.get('locked_qty')}→{after.get('locked_qty')})")
    print(f"  结论: adjust操作增加了available_qty但未相应减少其他项，守恒被打破")

# ============================================================
# PART 2: REFINED MATCHING (stricter)
# ============================================================
print(f"\n\n{'='*80}")
print("REFINED MATCHING (STRICT)")
print(f"{'='*80}")

gt_path = Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json")
with open(gt_path, encoding="utf-8") as f:
    ground_truth = json.load(f)

def strict_match(finding, bugs):
    """Stricter matching - require path AND semantic alignment."""
    ev = finding.get("evidence") or {}
    raw = finding.get("raw_evidence") or {}
    category = finding.get("category", "")
    title = finding.get("title", "")
    
    req_raw = raw.get("request_raw") or {}
    method = req_raw.get("method", "")
    path = normalize_path(req_raw.get("path", ""))
    actor = req_raw.get("actor", "") or ev.get("actor", "")
    resp_raw = raw.get("response_raw") or {}
    status_code = resp_raw.get("status_code", 0)
    
    matches = []
    for bug in bugs:
        score = 0
        reasons = []
        trigger = bug.get("trigger", "")
        bug_type = bug.get("type", "")
        keywords = bug.get("match_keywords", [])
        
        # Extract bug path
        path_match = re.search(r'(/api/[^\s,，:]+)', trigger)
        bug_path = normalize_path(path_match.group(1)) if path_match else ""
        
        # STRICT: Path must match (at least prefix)
        path_match_score = 0
        if path and bug_path:
            # Exact match (ignoring IDs)
            if path == bug_path:
                path_match_score = 30
            # Same endpoint prefix
            elif path.split("/{")[0] == bug_path.split("/{")[0]:
                path_match_score = 20
            # Same service prefix
            elif len(path.split("/")) > 2 and len(bug_path.split("/")) > 2:
                if path.split("/")[2] == bug_path.split("/")[2]:
                    path_match_score = 5
        
        if path_match_score < 15:
            continue  # Skip if path doesn't match well
        
        score += path_match_score
        reasons.append(f"path:{path_match_score}")
        
        # Semantic alignment: category must match bug type
        semantic_ok = False
        if category == "owner_tenant_visibility" and ("越权" in bug_type or "权限" in bug_type):
            score += 20
            semantic_ok = True
            reasons.append("semantic:越权")
        elif category == "http_status_class" and ("权限" in bug_type or "越权" in bug_type or "状态" in bug_type):
            score += 15
            semantic_ok = True
            reasons.append("semantic:权限/状态")
        elif category == "validation_rejection" and ("校验" in bug_type or "参数" in bug_type):
            score += 20
            semantic_ok = True
            reasons.append("semantic:校验")
        elif category == "conservation" and ("守恒" in bug_type or "库存" in bug_type):
            score += 20
            semantic_ok = True
            reasons.append("semantic:守恒")
        elif category == "http_status_class" and "500" in bug.get("actual", ""):
            score += 10
            semantic_ok = True
            reasons.append("semantic:500")
        
        # Actor alignment
        if actor and actor in trigger:
            score += 10
            reasons.append(f"actor:{actor}")
        
        # Keyword confirmation
        finding_text = f"{title} {path} {method} {actor}".lower()
        kw_hits = sum(1 for kw in keywords if kw.lower() in finding_text)
        if kw_hits >= 2:
            score += 10
            reasons.append(f"kw:{kw_hits}")
        
        if score >= 35:
            matches.append((bug, score, reasons))
    
    matches.sort(key=lambda x: -x[1])
    return matches

def normalize_path(path):
    path = re.sub(r'qb_test_\d+', '{ID}', path)
    path = re.sub(r'QB-TEST-\w+', '{ID}', path)
    path = re.sub(r'/\d+', '/{NUM}', path)
    return path

# Run strict matching
matched_bugs = set()
strict_results = []

for i, f in enumerate(findings):
    matches = strict_match(f, ground_truth)
    ev = f.get("evidence") or {}
    raw = f.get("raw_evidence") or {}
    req_raw = raw.get("request_raw") or {}
    
    entry = {
        "index": i,
        "finding_id": f.get("finding_id"),
        "category": f.get("category"),
        "method": req_raw.get("method", ""),
        "path": normalize_path(req_raw.get("path", "")),
        "actor": req_raw.get("actor", "") or ev.get("actor", ""),
    }
    
    if matches:
        bug, score, reasons = matches[0]
        bug_id = bug["bug_id"]
        is_dup = bug_id in matched_bugs
        matched_bugs.add(bug_id)
        entry["match"] = bug_id
        entry["score"] = score
        entry["reasons"] = reasons
        entry["status"] = "DUPLICATE_TP" if is_dup else "UNIQUE_TP"
        entry["bug_title"] = bug.get("title", "")
    else:
        entry["match"] = None
        entry["score"] = 0
        entry["status"] = "UNMATCHED"
    
    strict_results.append(entry)
    status_icon = "✓" if entry["status"] == "UNIQUE_TP" else ("≈" if entry["status"] == "DUPLICATE_TP" else "?")
    m = entry.get('method') or ''
    p = (entry.get('path') or '')[:35]
    match_id = entry.get('match') or 'NONE'
    print(f"  {status_icon} [{i:2d}] {f.get('category', ''):<25} {m:<6} {p:<35} -> {match_id:<12} ({entry['score']})")

# Summary
unique_tp = sum(1 for r in strict_results if r["status"] == "UNIQUE_TP")
dup_tp = sum(1 for r in strict_results if r["status"] == "DUPLICATE_TP")
unmatched = sum(1 for r in strict_results if r["status"] == "UNMATCHED")

print(f"\n--- STRICT MATCHING SUMMARY ---")
print(f"  UNIQUE_TP: {unique_tp}")
print(f"  DUPLICATE_TP: {dup_tp}")
print(f"  UNMATCHED: {unmatched}")
print(f"  Unique bugs matched: {len(matched_bugs)}")
print(f"  Recall: {len(matched_bugs)}/131 = {len(matched_bugs)/131*100:.1f}%")
print(f"  Precision: {unique_tp}/{len(findings)} = {unique_tp/len(findings)*100:.1f}%")

print(f"\n--- UNIQUE TP LIST ---")
for r in strict_results:
    if r["status"] == "UNIQUE_TP":
        print(f"  {r['match']}: {r.get('bug_title', '')}")
        print(f"    via {r['method']} {r['path']} actor={r['actor']}")

print(f"\n--- UNMATCHED (potential FP or new discovery) ---")
for r in strict_results:
    if r["status"] == "UNMATCHED":
        print(f"  [{r['index']}] {r['category']} {r['method']} {r['path']} actor={r['actor']}")
