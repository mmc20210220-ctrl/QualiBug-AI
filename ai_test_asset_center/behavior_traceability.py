"""Behavior traceability primitives for enterprise validation artifacts.

Traceability links behavior records to validation runs, evidence packages,
violations, regression assets, and regression results. It intentionally stays
inside the QualiBug-AI product boundary: discover, prove, report, and
regression validate.
"""

from __future__ import annotations

from typing import Any

TRACE_STATUSES = ("complete", "partial", "unlinked")
TRACE_CHAIN_KEYS = (
    "behavior_id",
    "validation_run_ids",
    "evidence_package_ids",
    "violation_ids",
    "regression_asset_ids",
    "regression_result_ids",
)


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


def _extend_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _first_text(*values: Any, default: str) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _behavior_id(item: dict[str, Any], fallback_index: int) -> str:
    violation = item.get("violation") if isinstance(item.get("violation"), dict) else {}
    behavior = item.get("behavior") if isinstance(item.get("behavior"), dict) else {}
    traceability = item.get("traceability") if isinstance(item.get("traceability"), dict) else {}
    return _first_text(
        item.get("behavior_id"),
        behavior.get("behavior_id"),
        violation.get("behavior_id"),
        traceability.get("behavior_id"),
        item.get("behavior"),
        default=f"BEH-{fallback_index:04d}",
    )


def _behavior_name(item: dict[str, Any], behavior_id: str) -> str:
    violation = item.get("violation") if isinstance(item.get("violation"), dict) else {}
    behavior = item.get("behavior") if isinstance(item.get("behavior"), dict) else {}
    return _first_text(
        item.get("behavior_name"),
        behavior.get("behavior_name"),
        item.get("name"),
        item.get("title"),
        violation.get("behavior_name"),
        default=behavior_id,
    )


def _traceability(item: dict[str, Any]) -> dict[str, Any]:
    traceability = item.get("traceability")
    return traceability if isinstance(traceability, dict) else {}


def _violation_refs(item: dict[str, Any]) -> list[Any]:
    violation = item.get("violation") if isinstance(item.get("violation"), dict) else {}
    traceability = _traceability(item)
    refs: list[Any] = []
    for value in (
        item.get("violation_id"),
        item.get("violation_ids"),
        item.get("bug_id"),
        item.get("bug_ids"),
        item.get("finding_id"),
        violation.get("violation_id"),
        traceability.get("violation_id"),
        traceability.get("violation_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _evidence_package_refs(item: dict[str, Any]) -> list[Any]:
    traceability = _traceability(item)
    refs: list[Any] = []
    for value in (
        item.get("package_id"),
        item.get("evidence_package_id"),
        item.get("evidence_package_ids"),
        traceability.get("package_id"),
        traceability.get("evidence_package_id"),
        traceability.get("evidence_package_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _validation_run_refs(item: dict[str, Any]) -> list[Any]:
    traceability = _traceability(item)
    evidence_linkage = item.get("evidence_linkage") if isinstance(item.get("evidence_linkage"), dict) else {}
    refs: list[Any] = []
    for value in (
        item.get("validation_run_id"),
        item.get("validation_run_ids"),
        item.get("validation_runs"),
        traceability.get("validation_run_id"),
        traceability.get("validation_run_ids"),
        evidence_linkage.get("validation_run_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _regression_asset_refs(item: dict[str, Any]) -> list[Any]:
    traceability = _traceability(item)
    refs: list[Any] = []
    for value in (
        item.get("asset_id"),
        item.get("regression_asset_id"),
        item.get("regression_asset_ids"),
        traceability.get("regression_asset_id"),
        traceability.get("regression_asset_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _regression_result_refs(item: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for value in (
        item.get("result_id"),
        item.get("regression_result_id"),
        item.get("regression_result_ids"),
        item.get("id") if item.get("comparison_status") else None,
    ):
        refs.extend(_as_list(value))
    return refs


def _status_for_trace(record: dict[str, Any]) -> str:
    linked_sections = sum(
        1
        for key in TRACE_CHAIN_KEYS[1:]
        if record[key]
    )
    if linked_sections == len(TRACE_CHAIN_KEYS) - 1:
        return "complete"
    if linked_sections:
        return "partial"
    return "unlinked"


def _lifecycle_for_trace(record: dict[str, Any]) -> list[str]:
    lifecycle = ["registered"]
    if record["validation_run_ids"]:
        lifecycle.append("observed")
    if record["evidence_package_ids"]:
        lifecycle.append("evidence-packaged")
    if record["violation_ids"]:
        lifecycle.append("violated")
    if record["regression_asset_ids"]:
        lifecycle.append("regression-tracked")
    if record["regression_result_ids"]:
        lifecycle.append("regression-validated")
    return lifecycle


def build_behavior_traceability(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build behavior-centered traceability chains from mixed artifact records."""

    grouped: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        behavior_id = _behavior_id(item, index)
        record = grouped.setdefault(
            behavior_id,
            {
                "behavior_id": behavior_id,
                "behavior_name": _behavior_name(item, behavior_id),
                "validation_run_ids": [],
                "evidence_package_ids": [],
                "violation_ids": [],
                "regression_asset_ids": [],
                "regression_result_ids": [],
                "status": "unlinked",
                "status_lifecycle": [],
            },
        )
        if record["behavior_name"] == behavior_id:
            record["behavior_name"] = _behavior_name(item, behavior_id)

        _extend_unique(record["validation_run_ids"], _validation_run_refs(item))
        _extend_unique(record["evidence_package_ids"], _evidence_package_refs(item))
        _extend_unique(record["violation_ids"], _violation_refs(item))
        _extend_unique(record["regression_asset_ids"], _regression_asset_refs(item))
        _extend_unique(record["regression_result_ids"], _regression_result_refs(item))

    traces = []
    for behavior_id in sorted(grouped):
        record = grouped[behavior_id]
        record["status"] = _status_for_trace(record)
        record["status_lifecycle"] = _lifecycle_for_trace(record)
        traces.append(record)

    status_counts = {status: 0 for status in TRACE_STATUSES}
    for trace in traces:
        status_counts[trace["status"]] += 1

    return {
        "total_traces": len(traces),
        "status_counts": status_counts,
        "traces": traces,
    }


def build_behavior_traceability_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return report-ready behavior traceability metrics and chains."""

    traceability = build_behavior_traceability(items)
    total = traceability["total_traces"]
    complete = traceability["status_counts"]["complete"]
    return {
        **traceability,
        "complete_traceability_percent": round((complete / total) * 100, 2) if total else 0.0,
    }
