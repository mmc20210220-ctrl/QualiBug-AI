"""Behavior registry primitives for enterprise behavior validation.

The registry is intentionally evidence- and violation-oriented. It does not
produce repair guidance. QualiBug-AI discovers, proves, reports, and validates;
customers decide how to fix their systems.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .scan_post_hooks import register_scan_post_hook

HOOK_NAME = "behavior_registry"

BEHAVIOR_STATUS_ORDER = ("violated", "validated", "observed", "untested")


def attach_behavior_registry(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Project a behavior registry from the scan's finding carriers.

    The registry feeds the executive validation summary; items are gathered
    from every finding carrier without changing their status.
    """
    if not isinstance(scan_result, dict):
        return scan_result
    items: list[dict[str, Any]] = []
    for key in (
        "real_findings",
        "bug_scores",
        "db_findings",
        "e2e_findings",
        "deep_findings",
        "ui_findings",
    ):
        carrier = scan_result.get(key)
        if isinstance(carrier, list):
            items.extend(item for item in carrier if isinstance(item, dict))
    if items:
        scan_result["behavior_registry"] = build_behavior_registry_report(items)
    return scan_result


def install_behavior_registry() -> None:
    register_scan_post_hook(HOOK_NAME, attach_behavior_registry)


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


def _extend_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


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


def _history_refs(item: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    refs: list[Any] = []
    for key in keys:
        refs.extend(_as_list(item.get(key)))
    return refs


def _status_for_record(record: dict[str, Any]) -> str:
    if record["violations"]:
        return "violated"
    if record["evidence"]:
        return "validated"
    if record["validation_runs"] or record["validation_history"]:
        return "observed"
    return "untested"


def _status_lifecycle(record: dict[str, Any]) -> list[str]:
    lifecycle = ["registered"]
    if record["validation_runs"] or record["validation_history"]:
        lifecycle.append("observed")
    if record["evidence"]:
        lifecycle.append("validated")
    if record["violations"]:
        lifecycle.append("violated")
    if record["regression_history"]:
        lifecycle.append("regression-tracked")
    return lifecycle


def build_behavior_registry(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic behavior registry from validation artifacts.

    Input records may be findings, confirmed violations, evidence records, or
    pre-normalized behavior records. The output is a stable registry keyed by
    behavior ID, with violation and evidence references attached for reporting
    and downstream traceability.
    """

    grouped: dict[str, dict[str, Any]] = {}

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
                "validation_history": [],
                "risk_history": [],
                "regression_history": [],
                "status_lifecycle": [],
            },
        )

        # Preserve the first meaningful business name/category, but allow sparse
        # later records to enrich default placeholders.
        if record["behavior_name"] == behavior_id:
            record["behavior_name"] = _behavior_name(item, behavior_id)
        if record["category"] == "uncategorized":
            record["category"] = _first_text(item.get("category"), item.get("domain"), default="uncategorized")

        _extend_unique(record["violations"], _violation_refs(item))
        _extend_unique(record["evidence"], _evidence_refs(item))
        _extend_unique(record["validation_runs"], _history_refs(item, ("validation_run_id", "validation_runs")))
        _extend_unique(record["validation_history"], _history_refs(item, ("validation_history", "validation_result")))
        _extend_unique(record["risk_history"], _history_refs(item, ("risk_history", "risk_assessment", "severity")))
        _extend_unique(record["regression_history"], _history_refs(item, ("regression_history", "regression_asset_id", "regression_result")))

    behaviors = []
    for behavior_id in sorted(grouped):
        record = grouped[behavior_id]
        record["status"] = _status_for_record(record)
        record["status_lifecycle"] = _status_lifecycle(record)
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
