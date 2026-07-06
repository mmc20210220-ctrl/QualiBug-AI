"""Render confirmed-bug evidence promotion metrics as stable JSON.

This CLI is for CI and deployment diagnostics. It reads an existing discovery
or verification report and reports how many confirmed-bug candidates are backed
by concrete runtime evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.confirmed_bug_gate import build_confirmed_bug_evidence_report


BUG_CONTAINER_KEYS = (
    "confirmed_bugs",
    "bugs",
    "findings",
    "issues",
    "verification_results",
    "results",
    "_last_engine_report",
)


def _extract_bug_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    for key in BUG_CONTAINER_KEYS:
        value = payload.get(key)
        if value is not None:
            return value

    return payload


def render_confirmed_bug_evidence_report(payload: Any) -> dict[str, Any]:
    return build_confirmed_bug_evidence_report(_extract_bug_payload(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render confirmed-bug evidence promotion metrics")
    parser.add_argument("--input", required=True, help="Path to discovery or verification JSON")
    parser.add_argument("--output", required=True, help="Path to write confirmed-bug evidence JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_confirmed_bug_evidence_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
