"""Render a regression asset report from validation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.render_behavior_registry_report import extract_behavior_records


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _asset_id(item: dict[str, Any], index: int) -> str:
    for key in ("regression_asset_id", "asset_id", "id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return f"REGRESSION-ASSET-{index:04d}"


def _result_status(item: dict[str, Any]) -> str:
    if item.get("blocked") is True:
        return "blocked"
    if item.get("passed") is True or item.get("validated") is True:
        return "validated"
    if item.get("passed") is False or item.get("failed") is True:
        return "failed"
    status = str(item.get("status") or item.get("result") or "").strip().lower()
    if status in {"validated", "failed", "blocked", "ready"}:
        return status
    return "ready"


def render_regression_asset_report(payload: Any) -> dict[str, Any]:
    records = extract_behavior_records(payload)
    results = _as_list(payload.get("regression_results")) if isinstance(payload, dict) else []
    result_by_asset = {str(item.get("asset_id") or item.get("regression_asset_id") or "").strip(): item for item in results}

    assets: list[dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        asset_id = _asset_id(item, index)
        status_source = result_by_asset.get(asset_id, item)
        confirmed = item.get("confirmed") is True or bool(item.get("violation_id") or item.get("violation_ids"))
        assets.append(
            {
                "asset_id": asset_id,
                "confirmed_violation": confirmed,
                "comparison_status": _result_status(status_source),
            }
        )

    counts = {"ready": 0, "validated": 0, "failed": 0, "blocked": 0}
    for item in assets:
        counts[item["comparison_status"]] += 1

    return {
        "total_assets": len(assets),
        "confirmed_violation_assets": sum(1 for item in assets if item["confirmed_violation"]),
        "comparison_counts": counts,
        "assets": assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render regression asset report")
    parser.add_argument("--input", required=True, help="Path to validation artifact JSON")
    parser.add_argument("--output", required=True, help="Path to write regression asset report JSON")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = render_regression_asset_report(payload)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Regression asset report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
