"""Render a behavior registry report from validation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.behavior_registry import build_behavior_registry_report


CONTAINER_KEYS = (
    "behaviors",
    "behavior_records",
    "findings",
    "bugs",
    "confirmed_bugs",
    "violations",
    "issues",
    "results",
    "verification_results",
    "artifacts",
    "_last_engine_report",
)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def extract_behavior_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _as_list(payload)

    if not isinstance(payload, dict):
        return []

    for key in CONTAINER_KEYS:
        value = payload.get(key)
        extracted = _as_list(value)
        if extracted:
            return extracted

    return [payload]


def render_behavior_registry_report(payload: Any) -> dict[str, Any]:
    return build_behavior_registry_report(extract_behavior_records(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render behavior registry report")
    parser.add_argument("--input", required=True, help="Path to validation artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write behavior registry report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_behavior_registry_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Behavior registry report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
