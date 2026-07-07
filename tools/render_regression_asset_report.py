"""Render regression asset library reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.regression_asset_library import build_regression_asset_library


CONTAINER_KEYS = (
    "violations",
    "confirmed_violations",
    "bugs",
    "confirmed_bugs",
    "findings",
    "packages",
    "results",
    "verification_results",
)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def extract_regression_sources(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _as_list(payload)
    if not isinstance(payload, dict):
        return []
    for key in CONTAINER_KEYS:
        extracted = _as_list(payload.get(key))
        if extracted:
            return extracted
    return [payload]


def render_regression_asset_report(payload: Any) -> dict[str, Any]:
    return build_regression_asset_library(extract_regression_sources(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render regression asset report")
    parser.add_argument("--input", required=True, help="Path to source artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write regression asset report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_regression_asset_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Regression asset report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
