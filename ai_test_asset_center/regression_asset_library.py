"""Regression asset library primitives."""

from __future__ import annotations

from typing import Any


REGRESSION_STATUSES = ("ready", "validated", "failed", "blocked")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def build_regression_asset(item: dict[str, Any], index: int = 1) -> dict[str, Any]:
    asset_id = str(item.get("regression_asset_id") or item.get("asset_id") or f"REG-{index:04d}")
    behavior_id = str(item.get("behavior_id") or item.get("behavior") or f"BEH-{index:04d}")
    source_id = str(item.get("violation_id") or item.get("bug_id") or item.get("finding_id") or f"VIO-{index:04d}")
    evidence = item.get("runtime_evidence") or item.get("evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {"observations": _as_list(evidence)}
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
        "evidence_linkage": {
            "evidence_ids": _as_list(item.get("evidence_id") or item.get("evidence_ids")),
            "validation_run_ids": _as_list(item.get("validation_run_id") or item.get("validation_runs")),
        },
        "replay_input": {
            "request": item.get("request") or evidence.get("request") or {},
            "runtime_evidence": evidence,
            "reproduction_steps": _as_list(item.get("reproduction_steps") or item.get("steps_to_reproduce")),
        },
        "expected_outcome": item.get("expected_outcome") or {"violation_absent": True, "behavior_matches_contract": True},
        "status": "ready",
    }


def compare_regression_result(asset: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    result_asset_id = str(result.get("asset_id") or result.get("regression_asset_id") or "")
    outcome = str(result.get("status") or result.get("outcome") or "unknown").lower()
    violation_present = bool(result.get("violation_present") or result.get("violation_reproduced"))
    behavior_matches = bool(result.get("behavior_matches_contract") or result.get("passed"))
    matches_asset = not result_asset_id or result_asset_id == asset["asset_id"]

    if matches_asset and not violation_present and (behavior_matches or outcome in {"passed", "validated"}):
        comparison_status = "validated"
    elif matches_asset and (violation_present or outcome in {"failed", "violation_present"}):
        comparison_status = "failed"
    else:
        comparison_status = "blocked"

    return {
        "asset_id": asset["asset_id"],
        "behavior_id": asset["behavior"]["behavior_id"],
        "source_violation_id": asset["source_violation"]["violation_id"],
        "result_id": str(result.get("result_id") or result.get("id") or "untracked-result"),
        "comparison_status": comparison_status,
        "violation_present": violation_present,
        "behavior_matches_contract": behavior_matches,
        "matches_asset": matches_asset,
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


def build_regression_asset_report(items: list[dict[str, Any]], results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    library = build_regression_asset_library(items)
    by_asset = {str(result.get("asset_id") or result.get("regression_asset_id") or ""): result for result in (results or [])}
    comparisons = [
        compare_regression_result(asset, by_asset[asset["asset_id"]])
        for asset in library["assets"]
        if asset["asset_id"] in by_asset
    ]
    counts = {status: 0 for status in REGRESSION_STATUSES}
    counts["ready"] = library["total_assets"] - len(comparisons)
    for comparison in comparisons:
        counts[comparison["comparison_status"]] += 1
    return {**library, "comparison_counts": counts, "comparisons": comparisons}
