"""Run miss-diagnosis against the latest scan to attribute the GT misses."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from benchmark_evaluator.miss_diagnosis import diagnose_scan

SCAN = ROOT / "_scan_result.json"
GT = ROOT / "_private_eval" / "_evaluator_private" / "benchmark_mall_131" / "bugs.json"
INPUTS = Path(
    r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
    r"\qualibug_enterprise_benchmark_v0_5_windows_native_stable\docs"
)

scan_result = json.loads(SCAN.read_text(encoding="utf-8"))
report = diagnose_scan(
    scan_result,
    ground_truth_path=GT,
    inputs_dir=INPUTS,
    project="benchmark_mall_131",
    root=ROOT,
)

print("TP=", report.get("true_positives"), "FP=", report.get("false_positives"),
      "recall=", report.get("recall"), "precision=", report.get("precision"))
print("missed_bug_count=", report.get("missed_bug_count"))
print("=== failure stage histogram ===")
for stage, row in sorted(report.get("failure_stage_histogram", {}).items(), key=lambda kv: int(kv[0])):
    print(f"  stage {stage}: {row['count']:3d}  {row['name']}")
print("top_failure_stage=", report.get("top_failure_stage"))
br = report.get("metrics", {}).get("bug_reach_rate", {})
print("reach_rate=", br.get("reach_rate"), "reached=", br.get("reached_related_path"),
      "unreached=", br.get("unreached_path"))
print("optimization_priority_hint=", report.get("optimization_priority_hint"))

# Per-stage breakdown of missed bugs by module
from collections import Counter, defaultdict
by_stage_module = defaultdict(Counter)
for r in report.get("miss_reports", []):
    by_stage_module[r["failure_stage"]][r["module"]] += 1
print("=== stage x module ===")
for stage in sorted(by_stage_module):
    mods = ", ".join(f"{m}:{c}" for m, c in by_stage_module[stage].most_common())
    print(f"  stage {stage}: {mods}")

OUT = ROOT / "_miss_diagnosis.json"
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved ->", OUT)
