"""Render a severity and risk report from discovery findings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_test_asset_center.bug_risk_scoring import build_bug_risk_report


CONTAINER_KEYS = (
    "findings",
    "bugs",
    "confirmed_bugs",
    "issues",
    "results",
    "verification_results",
    "_last_engine_report",
)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def extract_findings(payload: Any) -> list[dict[str, Any]]:
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


def render_bug_risk_report(payload: Any) -> dict[str, Any]:
    return build_bug_risk_report(extract_findings(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render bug severity and risk report")
    parser.add_argument("--input", required=True, help="Path to discovery report JSON")
    parser.add_argument("--output", required=True, help="Path to write risk report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_bug_risk_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bug risk report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
