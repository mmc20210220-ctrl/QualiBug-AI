"""Gate confirmed bugs on runtime evidence.

A bug should only be promoted to a confirmed customer-facing finding when it is
backed by concrete execution evidence such as request/response/status data.
This module is intentionally small and dependency-light so it can be reused by
CLI tools, CI checks, private deployments, and future discovery-stage wiring.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .execution_evidence_report import has_runtime_evidence


CONFIRMED_STATUS_VALUES = {
    "confirmed",
    "confirmed_bug",
    "bug_confirmed",
    "verified_bug",
    "reproduced",
    "reproducible",
}

CONFIRMED_FLAG_KEYS = (
    "confirmed_bug",
    "is_confirmed_bug",
    "confirmed",
    "is_confirmed",
    "bug_confirmed",
)

STATUS_KEYS = (
    "status",
    "bug_status",
    "finding_status",
    "verification_status",
    "result_status",
)

BUG_CONTAINER_KEYS = (
    "bugs",
    "confirmed_bugs",
    "findings",
    "issues",
    "verification_results",
    "results",
)


def _normalize_status(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def is_confirmed_bug_candidate(item: Any) -> bool:
    """Return True when an item claims to be a confirmed bug."""
    if not isinstance(item, Mapping):
        return False

    for key in CONFIRMED_FLAG_KEYS:
        if item.get(key) is True:
            return True

    for key in STATUS_KEYS:
        if _normalize_status(item.get(key)) in CONFIRMED_STATUS_VALUES:
            return True

    finding_type = _normalize_status(item.get("type") or item.get("finding_type") or item.get("category"))
    if finding_type in {"confirmed_bug", "verified_bug"}:
        return True

    return False


def can_promote_confirmed_bug(item: Any) -> bool:
    """Return True only if the item is a confirmed-bug candidate with runtime evidence."""
    return is_confirmed_bug_candidate(item) and has_runtime_evidence(item)


def _iter_bug_items(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        for key in BUG_CONTAINER_KEYS:
            value = payload.get(key)
            if value is not None:
                return _iter_bug_items(value)
        return [payload]

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        items: list[Any] = []
        for value in payload:
            items.extend(_iter_bug_items(value))
        return items

    return []


def filter_promotable_confirmed_bugs(payload: Any) -> list[Mapping[str, Any]]:
    """Return confirmed bug candidates that have runtime evidence."""
    promotable: list[Mapping[str, Any]] = []
    for item in _iter_bug_items(payload):
        if can_promote_confirmed_bug(item) and isinstance(item, Mapping):
            promotable.append(item)
    return promotable


def build_confirmed_bug_evidence_report(payload: Any) -> dict[str, Any]:
    """Build promotion metrics for confirmed-bug candidates."""
    items = [item for item in _iter_bug_items(payload) if isinstance(item, Mapping)]
    confirmed_candidates = [item for item in items if is_confirmed_bug_candidate(item)]
    evidence_backed = [item for item in confirmed_candidates if has_runtime_evidence(item)]

    total = len(confirmed_candidates)
    evidence_count = len(evidence_backed)
    return {
        "confirmed_bug_candidates": total,
        "evidence_backed_confirmed_bugs": evidence_count,
        "non_evidence_backed_confirmed_bugs": total - evidence_count,
        "confirmed_bug_evidence_ratio": evidence_count / total if total else 0.0,
        "confirmed_bug_promotion_blocked": total - evidence_count,
    }
