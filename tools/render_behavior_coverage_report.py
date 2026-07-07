"""Render behavior coverage from validation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.behavior_registry import build_behavior_registry
from tools.render_behavior_registry_report import extract_behavior_records


def render_behavior_coverage_report(payload: Any) -> dict[str, Any]:
    registry = build_behavior_registry(extract_behavior_records(payload))
    counts = {"covered": 0, "partially_covered": 0, "uncovered": 0}
    behaviors = []
    for item in registry["behaviors"]:
        status = item.get("status")
        if status in {"violated", "validated"}:
            bucket = "covered"
        elif status == "observed":
            bucket = "partially_covered"
        else:
            bucket = "uncovered"
        counts[bucket] += 1
        behaviors.append({"behavior_id": item["behavior_id"], "coverage_bucket": bucket})

    total = registry["total_behaviors"]
    covered = counts["covered"]
    observed_or_covered = counts["covered"] + counts["partially_covered"]
    return {
        "total_behaviors": total,
        "coverage_bucket_counts": counts,
        "covered_behavior_percent": round((covered / total) * 100, 2) if total else 0.0,
        "observed_or_covered_behavior_percent": round((observed_or_covered / total) * 100, 2) if total else 0.0,
        "behaviors": behaviors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render behavior coverage report")
    parser.add_argument("--input", required=True, help="Path to validation artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write behavior coverage report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_behavior_coverage_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Behavior coverage report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
