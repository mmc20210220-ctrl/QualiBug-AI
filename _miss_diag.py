#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run evaluator-private miss diagnosis against current scan result."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from benchmark_evaluator.miss_diagnosis import diagnose_scan

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
report = diagnose_scan(
    scan,
    ground_truth_path=Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json"),
    inputs_dir=Path("benchmark/multi_industry/ecommerce"),
    project="benchmark_mall_131",
    root=Path("."),
)
Path("_miss_diagnosis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("TP:", report.get("true_positives"), "recall:", report.get("recall"), "precision:", report.get("precision"))
print("missed:", report.get("missed_bug_count"))
print("failure_stage_histogram:")
for s, m in (report.get("failure_stage_histogram") or {}).items():
    if m.get("count"):
        print(f"  stage {s} ({m.get('name')}): {m.get('count')}")
print("top_failure_stage:", report.get("top_failure_stage"))
print("priority_hint:", report.get("optimization_priority_hint"))
reach = (report.get("metrics") or {}).get("bug_reach_rate") or {}
print("reach_rate:", reach.get("reach_rate"), "reached:", reach.get("reached_related_path"), "unreached:", reach.get("unreached_path"))
