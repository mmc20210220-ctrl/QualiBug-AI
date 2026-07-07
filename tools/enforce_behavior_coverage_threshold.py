"""Enforce minimum behavior coverage thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def enforce_behavior_coverage_threshold(report: dict, minimum_percent: float) -> dict:
    observed = float(report.get("covered_behavior_percent", 0.0))
    passed = observed >= minimum_percent
    return {
        "passed": passed,
        "minimum_percent": minimum_percent,
        "observed_percent": observed,
        "total_behaviors": int(report.get("total_behaviors", 0)),
        "covered_behaviors": int(report.get("coverage_bucket_counts", {}).get("covered", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce behavior coverage threshold")
    parser.add_argument("--input", required=True, help="Path to behavior coverage report JSON")
    parser.add_argument("--minimum-percent", type=float, required=True, help="Minimum covered behavior percent")
    args = parser.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = enforce_behavior_coverage_threshold(report, args.minimum_percent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
