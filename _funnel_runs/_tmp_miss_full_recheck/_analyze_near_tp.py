"""Offline: which deliverable findings almost match missed GT (family/path)."""
from __future__ import annotations

import json
from pathlib import Path

from benchmark_evaluator.benchmark_compute import (
    _canonical_match_family,
    _extract_api_paths,
    _match_finding_to_gt,
    _text_blob,
)

# Load submission findings
sub = json.loads(
    Path(
        r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/optimized.evaluation_submission.json"
    ).read_text(encoding="utf-8")
)
# extract findings from scan_result / formal authority
findings = []
scan = sub.get("scan_result") or {}
for key in ("findings", "bugs", "defects", "validated_findings"):
    rows = scan.get(key)
    if isinstance(rows, list) and rows:
        findings = rows
        print("from scan_result", key, len(findings))
        break
if not findings:
    fda = sub.get("formal_delivery_authority") or {}
    for key in ("deliverable_findings", "findings", "deliverables"):
        rows = fda.get(key)
        if isinstance(rows, list) and rows:
            findings = [r.get("finding") or r for r in rows]
            print("from fda", key, len(findings))
            break
if not findings:
    # walk
    def walk(o, d=0):
        if d > 5:
            return []
        if isinstance(o, list) and o and isinstance(o[0], dict):
            if any("title" in x and ("ContractOracle" in str(x.get("title")) or x.get("customer_delivery_status")) for x in o[:5]):
                return o
        if isinstance(o, dict):
            for v in o.values():
                r = walk(v, d + 1)
                if r:
                    return r
        return []

    findings = walk(sub)
    print("walk findings", len(findings))

# Load miss diagnosis matched + missed
miss = json.loads(
    Path(
        r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/miss_post_inv001_family/MISS_DIAGNOSIS.json"
    ).read_text(encoding="utf-8")
)
matched = set(miss.get("matched_bug_ids") or [])
print("matched", matched, "findings", len(findings))

# Try load private GT if available
gt_paths = list(
    Path(r"D:/QualiBug-AI/QualiBug-AI-main").glob(
        "_private_eval/**/benchmark_mall*/**/*ground*truth*.json"
    )
)
gt_paths += list(
    Path(r"D:/QualiBug-AI/QualiBug-AI-main").glob(
        "_private_eval/**/GT*.json"
    )
)
print("gt candidates", [str(p) for p in gt_paths[:10]])

gt = []
for p in gt_paths:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list) and data and isinstance(data[0], dict) and (
            "bug_id" in data[0] or "id" in data[0]
        ):
            gt = data
            print("loaded gt", p, len(gt))
            break
        if isinstance(data, dict):
            for k in ("bugs", "ground_truth", "defects"):
                if isinstance(data.get(k), list) and len(data[k]) > 50:
                    gt = data[k]
                    print("loaded gt", p, k, len(gt))
                    break
    except Exception as e:
        print("skip", p, e)

if not gt:
    print("No GT available for offline rematch; listing finding families only")
    from collections import Counter

    c = Counter(_canonical_match_family(f if isinstance(f, dict) else {}) for f in findings)
    print(c)
    for f in findings[:15]:
        if isinstance(f, dict):
            print(
                _canonical_match_family(f),
                (f.get("title") or "")[:90],
                _extract_api_paths(_text_blob(f)),
            )
else:
    used = set()
    hits = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        m = _match_finding_to_gt(f, gt, used)
        if m:
            used.add(m["bug_id"])
            hits.append((m["bug_id"], m["__match_score"], (f.get("title") or "")[:80]))
    print("rematch hits", hits)
    print("new vs prior", set(h[0] for h in hits) - matched)
