"""Regression asset library primitives."""

from __future__ import annotations

from typing import Any


def build_regression_asset(item: dict[str, Any], index: int = 1) -> dict[str, Any]:
    asset_id = str(item.get("regression_asset_id") or item.get("asset_id") or f"REG-{index:04d}")
    behavior_id = str(item.get("behavior_id") or item.get("behavior") or f"BEH-{index:04d}")
    source_id = str(item.get("violation_id") or item.get("bug_id") or item.get("finding_id") or f"VIO-{index:04d}")
    return {
        "asset_id": asset_id,
        "source_violation": {
            "violation_id": source_id,
            "confirmed": bool(item.get("confirmed") or item.get("confirmed_bug") or item.get("is_confirmed")),
        },
        "behavior": {
            "behavior_id": behavior_id,
            "behavior_name": str(item.get("behavior_name") or item.get("behavior") or behavior_id),
        },
        "status": "ready",
    }


def build_regression_asset_library(items: list[dict[str, Any]]) -> dict[str, Any]:
    assets = [build_regression_asset(item, index) for index, item in enumerate(items, start=1)]
    behavior_ids = sorted({asset["behavior"]["behavior_id"] for asset in assets})
    return {
        "total_assets": len(assets),
        "confirmed_violation_assets": sum(1 for asset in assets if asset["source_violation"]["confirmed"]),
        "linked_behaviors": len(behavior_ids),
        "behavior_ids": behavior_ids,
        "assets": sorted(assets, key=lambda asset: asset["asset_id"]),
    }
