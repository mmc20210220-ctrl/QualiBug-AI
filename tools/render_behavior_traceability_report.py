"""Render behavior traceability from validation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.render_behavior_registry_report import extract_behavior_records


def _trace_status(item: dict[str, Any]) -> str:
    has_behavior = bool(item.get("behavior_id") or item.get("behavior_name") or item.get("behavior"))
    has_evidence = bool(item.get("runtime_evidence") or item.get("evidence") or item.get("evidence_package"))
    has_violation = bool(item.get("violation_id") or item.get("violation_ids") or item.get("finding_id"))
    has_regression = bool(item.get("regression_asset_id") or item.get("regression_result"))
    if has_behavior and has_evidence and (has_violation or has_regression):
        return "complete"
    if has_behavior and (has_evidence or has_violation or has_regression):
        return "partial"
    return "unlinked"


def render_behavior_traceability_report(payload: Any) -> dict[str, Any]:
    records = extract_behavior_records(payload)
    traces = []
    for index, item in enumerate(records, start=1):
        trace_id = str(item.get("trace_id") or item.get("behavior_id") or item.get("id") or f"TRACE-{index:04d}")
        traces.append({"trace_id": trace_id, "status": _trace_status(item)})

    counts = {"complete": 0, "partial": 0, "unlinked": 0}
    for item in traces:
        counts[item["status"]] += 1

    total = len(traces)
    return {
        "total_traces": total,
        "status_counts": counts,
        "complete_traceability_percent": round((counts["complete"] / total) * 100, 2) if total else 0.0,
        "traces": traces,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render behavior traceability report")
    parser.add_argument("--input", required=True, help="Path to validation artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write behavior traceability report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_behavior_traceability_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Behavior traceability report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
