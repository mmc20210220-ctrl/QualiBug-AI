"""Render customer-grade evidence packages from violation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.evidence_package import build_evidence_package_report


CONTAINER_KEYS = (
    "violations",
    "confirmed_violations",
    "bugs",
    "confirmed_bugs",
    "findings",
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


def extract_violation_artifacts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _as_list(payload)

    if not isinstance(payload, dict):
        return []

    for key in CONTAINER_KEYS:
        extracted = _as_list(payload.get(key))
        if extracted:
            return extracted

    return [payload]


def render_evidence_package_report(payload: Any) -> dict[str, Any]:
    return build_evidence_package_report(extract_violation_artifacts(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render evidence package report")
    parser.add_argument("--input", required=True, help="Path to violation artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write evidence package report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_evidence_package_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evidence package report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
