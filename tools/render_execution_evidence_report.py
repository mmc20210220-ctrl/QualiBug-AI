"""Render execution-evidence quality metrics as stable JSON.

Input can be either:
1. A raw list/dict of verification outputs, or
2. A larger run report containing one of these keys:
   - verification_items
   - verification_results
   - results_by_engine
   - _last_engine_report

The renderer does not call LLMs or customer systems. It only reads an existing
JSON artifact and emits normalized evidence-backed metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.execution_evidence_report import build_execution_evidence_report


VERIFICATION_CONTAINER_KEYS = (
    "verification_items",
    "verification_results",
    "results_by_engine",
    "_last_engine_report",
)


def _extract_verification_items(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    for key in VERIFICATION_CONTAINER_KEYS:
        value = payload.get(key)
        if value is not None:
            return value

    return payload


def render_execution_evidence_report(payload: Any, *, engine_names: list[str] | None = None) -> dict[str, Any]:
    verification_items = _extract_verification_items(payload)
    return build_execution_evidence_report(verification_items, engine_names=engine_names)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render execution-evidence quality metrics")
    parser.add_argument("--input", required=True, help="Path to raw verification JSON")
    parser.add_argument("--output", required=True, help="Path to write rendered evidence quality JSON")
    parser.add_argument(
        "--engine",
        action="append",
        dest="engines",
        default=None,
        help="Expected engine name. Can be repeated to preserve/report zero-output engines.",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_execution_evidence_report(payload, engine_names=args.engines)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
