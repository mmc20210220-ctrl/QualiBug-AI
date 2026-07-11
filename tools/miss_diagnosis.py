#!/usr/bin/env python3
"""CLI: evaluator-private miss diagnosis for SPC Phase 1.

Usage:
  python tools/miss_diagnosis.py \\
    --run-envelope _funnel_runs/llm_throughput.json \\
    --ground-truth <path-to-hidden_ground_truth/bugs.json> \\
    --inputs-dir platform_inputs/benchmark_mall \\
    --output-dir _funnel_runs/miss_diagnosis_<ts>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmark_evaluator.miss_diagnosis import (  # noqa: E402
    diagnose_scan,
    render_miss_diagnosis_markdown,
)


def _load_scan_result(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"run envelope must be a JSON object: {path}")
    if isinstance(payload.get("full_result"), dict):
        return payload["full_result"]
    if isinstance(payload.get("scan_result"), dict):
        return payload["scan_result"]
    if "findings" in payload:
        return payload
    raise ValueError("could not locate scan_result/full_result/findings in run envelope")


def main() -> int:
    parser = argparse.ArgumentParser(description="SPC Phase 1 miss diagnosis (evaluator-private)")
    parser.add_argument("--run-envelope", required=True, help="Funnel/scan JSON with findings")
    parser.add_argument("--ground-truth", required=True, help="Hidden GT bugs.json (evaluator only)")
    parser.add_argument(
        "--inputs-dir",
        default=str(REPOSITORY_ROOT / "platform_inputs" / "benchmark_mall"),
        help="Visible enterprise inputs used by the scan",
    )
    parser.add_argument("--project", default="benchmark_mall")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    run_path = Path(args.run_envelope)
    gt_path = Path(args.ground_truth)
    inputs_dir = Path(args.inputs_dir)
    if not run_path.is_file():
        raise FileNotFoundError(run_path)
    if not gt_path.is_file():
        raise FileNotFoundError(gt_path)

    scan_result = _load_scan_result(run_path)
    report = diagnose_scan(
        scan_result,
        ground_truth_path=gt_path,
        inputs_dir=inputs_dir,
        project=args.project,
        root=REPOSITORY_ROOT,
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_dir) if args.output_dir else (REPOSITORY_ROOT / "_funnel_runs" / f"miss_diagnosis_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "MISS_DIAGNOSIS.json"
    md_path = out_dir / "MISS_DIAGNOSIS.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_miss_diagnosis_markdown(report), encoding="utf-8")

    summary = {
        "output_dir": str(out_dir),
        "true_positives": report.get("true_positives"),
        "missed_bug_count": report.get("missed_bug_count"),
        "recall": report.get("recall"),
        "top_failure_stage": report.get("top_failure_stage"),
        "bug_reach_rate": (report.get("metrics") or {}).get("bug_reach_rate"),
        "behavior_path_coverage": (report.get("metrics") or {}).get("behavior_path_coverage"),
        "business_understanding": (report.get("metrics") or {}).get("business_understanding"),
        "optimization_priority_hint": report.get("optimization_priority_hint"),
        "failure_stage_histogram": report.get("failure_stage_histogram"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"json={json_path}")
    print(f"markdown={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
