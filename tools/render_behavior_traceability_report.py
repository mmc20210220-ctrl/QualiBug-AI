"""Render behavior traceability reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.behavior_traceability import build_behavior_traceability_report


TRACE_SOURCE_KEYS = (
    "artifacts",
    "behaviors",
    "traces",
    "packages",
    "evidence_packages",
    "violations",
    "confirmed_violations",
    "regression_assets",
    "regression_results",
    "comparisons",
)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def extract_traceability_sources(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _as_list(payload)
    if not isinstance(payload, dict):
        return []

    extracted: list[dict[str, Any]] = []
    for key in TRACE_SOURCE_KEYS:
        extracted.extend(_as_list(payload.get(key)))

    return extracted or [payload]


def render_behavior_traceability_report(payload: Any) -> dict[str, Any]:
    return build_behavior_traceability_report(extract_traceability_sources(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render behavior traceability report")
    parser.add_argument("--input", required=True, help="Path to source artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write behavior traceability report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_behavior_traceability_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Behavior traceability report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
