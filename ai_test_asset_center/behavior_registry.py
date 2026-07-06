"""Behavior registry primitives for enterprise behavior validation.

The registry is intentionally evidence- and violation-oriented. It does not
produce repair guidance. QualiBug-AI discovers, proves, reports, and validates;
customers decide how to fix their systems.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


BEHAVIOR_STATUS_ORDER = ("violated", "validated", "observed", "untested")


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
    return _first_text(
        item.get("behavior_id"),
        item.get("id"),
        item.get("behavior"),
        default=f"BEH-{fallback_index:04d}",
    )


def _behavior_name(item: dict[str, Any], behavior_id: str) -> str:
    return _first_text(
        item.get("behavior_name"),
        item.get("name"),
        item.get("title"),
        item.get("behavior"),
        default=behavior_id,
    )


def _evidence_refs(item: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for key in ("evidence_id", "evidence_ids", "evidence", "runtime_evidence"):
        refs.extend(_as_list(item.get(key)))
    return refs


def _violation_refs(item: dict[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for key in ("violation_id", "violation_ids", "bug_id", "bug_ids", "finding_id"):
        refs.extend(_as_list(item.get(key)))
    return refs


def _status_for_record(record: dict[str, Any]) -> str:
    if record["violations"]:
        return "violated"
    if record["evidence"]:
        return "validated"
    if record["validation_runs"]:
        return "observed"
    return "untested"


def build_behavior_registry(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic behavior registry from validation artifacts.

    Input records may be findings, confirmed violations, evidence records, or
    pre-normalized behavior records. The output is a stable registry keyed by
    behavior ID, with violation and evidence references attached for reporting
    and downstream traceability.
    """

    grouped: dict[str, dict[str, Any]] = {}
    validation_runs_by_behavior: dict[str, list[Any]] = defaultdict(list)

    for index, item in enumerate(items, start=1):
        behavior_id = _behavior_id(item, index)
        record = grouped.setdefault(
            behavior_id,
            {
                "behavior_id": behavior_id,
                "behavior_name": _behavior_name(item, behavior_id),
                "category": _first_text(item.get("category"), item.get("domain"), default="uncategorized"),
                "violations": [],
                "evidence": [],
                "validation_runs": [],
            },
        )

        # Preserve the first meaningful business name/category, but allow sparse
        # later records to enrich default placeholders.
        if record["behavior_name"] == behavior_id:
            record["behavior_name"] = _behavior_name(item, behavior_id)
        if record["category"] == "uncategorized":
            record["category"] = _first_text(item.get("category"), item.get("domain"), default="uncategorized")

        for violation_ref in _violation_refs(item):
            if violation_ref not in record["violations"]:
                record["violations"].append(violation_ref)

        for evidence_ref in _evidence_refs(item):
            if evidence_ref not in record["evidence"]:
                record["evidence"].append(evidence_ref)

        for run_ref in _as_list(item.get("validation_run_id") or item.get("validation_runs")):
            if run_ref not in validation_runs_by_behavior[behavior_id]:
                validation_runs_by_behavior[behavior_id].append(run_ref)

    behaviors = []
    for behavior_id in sorted(grouped):
        record = grouped[behavior_id]
        record["validation_runs"] = validation_runs_by_behavior.get(behavior_id, [])
        record["status"] = _status_for_record(record)
        behaviors.append(record)

    status_counts = {status: 0 for status in BEHAVIOR_STATUS_ORDER}
    for record in behaviors:
        status_counts[record["status"]] += 1

    return {
        "total_behaviors": len(behaviors),
        "status_counts": status_counts,
        "behaviors": behaviors,
    }


def build_behavior_registry_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a report-ready behavior registry summary."""

    registry = build_behavior_registry(items)
    total = registry["total_behaviors"]
    validated_or_violated = registry["status_counts"]["validated"] + registry["status_counts"]["violated"]
    coverage = round((validated_or_violated / total) * 100, 2) if total else 0.0

    highest_attention = None
    for status in BEHAVIOR_STATUS_ORDER:
        candidates = [item for item in registry["behaviors"] if item["status"] == status]
        if candidates:
            highest_attention = candidates[0]
            break

    return {
        **registry,
        "behavior_coverage_percent": coverage,
        "highest_attention_behavior": highest_attention,
    }
