# -*- coding: utf-8 -*-
"""Task 8 offline re-eval: run3 scores BEFORE vs AFTER family normalization.

Before = product family normalization disabled (_evaluator_family -> identity).
After  = production code path.  Same inputs as .scratch/_score_run3_evb.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\QualiBug-AI\QualiBug-AI-main")
REPO = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
GT = REPO / "_private_eval" / "_evaluator_private" / "benchmark_mall_131" / "bugs.json"
EVB = REPO / "platform_workspace/native_stable_e2e/evidence_bundles/evb_5a2c622bad9eede460693392"

import benchmark_evaluator.benchmark_compute as bc

findings = json.loads((EVB / "findings.json").read_text(encoding="utf-8"))
candidates = json.loads((EVB / "candidate_findings.json").read_text(encoding="utf-8"))


def summarize(result):
    return {
        "true_positives": result.get("true_positives"),
        "false_positives": result.get("false_positives"),
        "false_negatives": result.get("false_negatives"),
        "recall": result.get("recall"),
        "precision": result.get("precision"),
        "f1": result.get("f1_score"),
        "matched_bug_ids": sorted(result.get("matched_bug_ids", [])),
    }


# AFTER (production code)
after = bc.compute_benchmark(
    project="native_stable_e2e", findings=findings, candidates=candidates,
    root=REPO, ground_truth_path=str(GT),
)

# BEFORE: disable normalization exactly (identity pass-through)
saved = bc._evaluator_family
bc._evaluator_family = lambda f: str(f or "")
before = bc.compute_benchmark(
    project="native_stable_e2e", findings=findings, candidates=candidates,
    root=REPO, ground_truth_path=str(GT),
)
bc._evaluator_family = saved

print("=== BEFORE (no normalization) ===")
print(json.dumps(summarize(before), ensure_ascii=False, indent=1))
print("=== AFTER (normalization) ===")
print(json.dumps(summarize(after), ensure_ascii=False, indent=1))

b_ids, a_ids = set(before["matched_bug_ids"]), set(after["matched_bug_ids"])
print("newly matched:", sorted(a_ids - b_ids))
print("lost:", sorted(b_ids - a_ids))

# Family resolution change inventory on the run3 findings
from collections import Counter
rf_before, rf_after = Counter(), Counter()
for f in findings:
    rf_before[bc._canonical_match_family(dict(f)) if False else "n/a"] += 0
# recompute properly with saved fn
bc._evaluator_family = lambda f: str(f or "")
rf_b = Counter(bc._canonical_match_family(f) for f in findings)
bc._evaluator_family = saved
rf_a = Counter(bc._canonical_match_family(f) for f in findings)
print("=== findings family BEFORE ===", dict(rf_b))
print("=== findings family AFTER  ===", dict(rf_a))
