"""Behavior coverage metrics for enterprise validation reporting.

Coverage measures how much of the registered behavior surface has validation,
evidence, violation, and regression tracking attached. It reports assurance
state only; it does not produce implementation guidance.
"""

from __future__ import annotations

from typing import Any

COVERAGE_STATUSES = ("validated", "violated", "regression_tracked", "observed", "untested")


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


def _first_text(*values: Any, default: str) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _behavior_id(item: dict[str, Any], fallback_index: int) -> str:
    behavior = item.get("behavior") if isinstance(item.get("behavior"), dict) else {}
    violation = item.get("violation") if isinstance(item.get("violation"), dict) else {}
    return _first_text(
        item.get("behavior_id"),
        behavior.get("behavior_id"),
        violation.get("behavior_id"),
        item.get("id") if item.get("behavior_name") or item.get("category") else None,
        item.get("behavior"),
        default=f"BEH-{fallback_index:04d}",
    )


def _behavior_name(item: dict[str, Any], behavior_id: str) -> str:
    behavior = item.get("behavior") if isinstance(item.get("behavior"), dict) else {}
    violation = item.get("violation") if isinstance(item.get("violation"), dict) else {}
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


def _evidence_refs(item: dict[str, Any]) -> list[Any]:
    traceability = _traceability(item)
    refs: list[Any] = []
    for value in (
        item.get("evidence_id"),
        item.get("evidence_ids"),
        item.get("package_id"),
        item.get("evidence_package_id"),
        item.get("evidence_package_ids"),
        traceability.get("evidence_ids"),
        traceability.get("package_id"),
        traceability.get("evidence_package_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _validation_refs(item: dict[str, Any]) -> list[Any]:
    traceability = _traceability(item)
    refs: list[Any] = []
    for value in (
        item.get("validation_run_id"),
        item.get("validation_run_ids"),
        item.get("validation_runs"),
        traceability.get("validation_run_id"),
        traceability.get("validation_run_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _violation_refs(item: dict[str, Any]) -> list[Any]:
    violation = item.get("violation") if isinstance(item.get("violation"), dict) else {}
    traceability = _traceability(item)
    refs: list[Any] = []
    for value in (
        item.get("violation_id"),
        item.get("violation_ids"),
        item.get("bug_id"),
        item.get("bug_ids"),
        violation.get("violation_id"),
        traceability.get("violation_id"),
        traceability.get("violation_ids"),
    ):
        refs.extend(_as_list(value))
    return refs


def _regression_refs(item: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for value in (
        item.get("regression_asset_id"),
        item.get("regression_asset_ids"),
        item.get("asset_id"),
        item.get("regression_result_id"),
        item.get("regression_result_ids"),
        item.get("result_id"),
    ):
        refs.extend(_as_list(value))
    return refs


def _status_for_record(record: dict[str, Any]) -> str:
    if record["regression_refs"]:
        return "regression_tracked"
    if record["violation_refs"]:
        return "violated"
    if record["evidence_refs"]:
        return "validated"
    if record["validation_run_refs"]:
        return "observed"
    return "untested"


def _bucket_for_record(record: dict[str, Any]) -> str:
    if record["status"] in {"validated", "violated", "regression_tracked"}:
        return "covered"
    if record["status"] == "observed":
        return "partially_covered"
    return "uncovered"


def _extend_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def build_behavior_coverage(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build behavior coverage records from mixed validation artifacts."""

    grouped: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        behavior_id = _behavior_id(item, index)
        record = grouped.setdefault(
            behavior_id,
            {
                "behavior_id": behavior_id,
                "behavior_name": _behavior_name(item, behavior_id),
                "category": _first_text(item.get("category"), item.get("domain"), default="uncategorized"),
                "validation_run_refs": [],
                "evidence_refs": [],
                "violation_refs": [],
                "regression_refs": [],
                "status": "untested",
                "coverage_bucket": "uncovered",
            },
        )
        if record["behavior_name"] == behavior_id:
            record["behavior_name"] = _behavior_name(item, behavior_id)
        if record["category"] == "uncategorized":
            record["category"] = _first_text(item.get("category"), item.get("domain"), default="uncategorized")

        _extend_unique(record["validation_run_refs"], _validation_refs(item))
        _extend_unique(record["evidence_refs"], _evidence_refs(item))
        _extend_unique(record["violation_refs"], _violation_refs(item))
        _extend_unique(record["regression_refs"], _regression_refs(item))

    behaviors = []
    for behavior_id in sorted(grouped):
        record = grouped[behavior_id]
        record["status"] = _status_for_record(record)
        record["coverage_bucket"] = _bucket_for_record(record)
        behaviors.append(record)

    status_counts = {status: 0 for status in COVERAGE_STATUSES}
    bucket_counts = {"covered": 0, "partially_covered": 0, "uncovered": 0}
    for behavior in behaviors:
        status_counts[behavior["status"]] += 1
        bucket_counts[behavior["coverage_bucket"]] += 1

    return {
        "total_behaviors": len(behaviors),
        "status_counts": status_counts,
        "coverage_bucket_counts": bucket_counts,
        "behaviors": behaviors,
    }


def build_behavior_coverage_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return report-ready behavior coverage metrics."""

    coverage = build_behavior_coverage(items)
    total = coverage["total_behaviors"]
    covered = coverage["coverage_bucket_counts"]["covered"]
    partial = coverage["coverage_bucket_counts"]["partially_covered"]
    return {
        **coverage,
        "covered_behavior_percent": round((covered / total) * 100, 2) if total else 0.0,
        "observed_or_covered_behavior_percent": round(((covered + partial) / total) * 100, 2) if total else 0.0,
    }
