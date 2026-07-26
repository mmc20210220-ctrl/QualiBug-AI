from __future__ import annotations

"""Normalize the shape of command-center delivery payloads.

This adapter is intentionally small and side-effect free. It runs immediately before
`/command-center` responses are returned and only normalizes SHAPE — it does not
decide which findings are customer defects.

It used to re-run the v1 field-inspection gate (`split_customer_delivery_tracks`)
over `data.defects`, which was fatal rather than redundant. By the time this adapter
runs, `data.defects` already holds the v2 authority's published canonical
representatives, and those legitimately carry `bug_status='suspected'` /
`confirmation_status='candidate'` because the v2 chain proves delivery through sealed
receipts instead of those v1 fields. Measured on
platform_outputs/benchmark_mall/scan_result.json: `customer_delivery_rejection_reasons`
rejected 10 of 10 receipt-backed `delivery_occurrences` with
['BUG_STATUS_NOT_REPRODUCED', 'NOT_CONFIRMED'], every one of which carried
`delivery_gate_receipt.status == 'DELIVERABLE'`. So a fully release-ready run rendered
zero defects.

`_collect_candidate_items` was removed with it: folding `data.risks` and
`data.findings` back into the defect candidate pool could resurrect rows the authority
never published.

Delivery authority is `formal_delivery_scope.formal_customer_deliverable_findings` ->
`canonical_defect_registry` -> `discovery_quality_projection`. There is one gate.
"""

from typing import Any


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe_by_identity(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        key = str(
            item.get("id")
            or item.get("finding_id")
            or item.get("risk_id")
            or item.get("title")
            or index
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _set_nested_counter(data: dict[str, Any], section: str, key: str, value: int) -> None:
    target = data.get(section)
    if isinstance(target, dict):
        target[key] = value


def _sync_command_center_counters(data: dict[str, Any], defects: list[dict[str, Any]], clues: list[dict[str, Any]]) -> None:
    ready_count = len(defects)
    clue_count = len(clues)
    data["ready_bug_count"] = ready_count
    data["internal_clue_count"] = clue_count

    _set_nested_counter(data, "scan_meta", "ready_bug_count", ready_count)
    _set_nested_counter(data, "scan_meta", "customer_ready_defects", ready_count)
    _set_nested_counter(data, "scan_meta", "internal_clue_count", clue_count)
    _set_nested_counter(data, "value_metrics", "ready_bug_count", ready_count)
    _set_nested_counter(data, "value_metrics", "defect_count", ready_count)
    _set_nested_counter(data, "value_metrics", "clue_count", clue_count)
    _set_nested_counter(data, "executive_summary", "total_bugs_found", ready_count)
    _set_nested_counter(data, "executive_summary", "ready_bugs", ready_count)
    _set_nested_counter(data, "executive_summary", "customer_ready_defects", ready_count)
    _set_nested_counter(data, "executive_summary", "internal_clues", clue_count)


def normalize_command_center_delivery(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the delivery lists' shape and sync the derived counters.

    Reads `data.defects` and `data.clues` as already authoritative. It does NOT
    re-decide membership: the v2 receipt chain published them upstream, and
    re-judging them here on v1 display fields zeroed every real result.

    Rewrites only:
    - data.risks: compatibility alias for data.defects
    - data.delivery_contract: explicit summary for UI and tests
    - the derived counters, so they cannot disagree with the lists

    A non-list on either key is coerced to [] and recorded, never rebuilt from
    `data.risks` or `data.findings` — deriving defects from a legacy alias is how
    unpublished rows get resurrected.
    """
    if not isinstance(payload, dict):
        return {"data": {"defects": [], "clues": [], "risks": []}, "delivery_contract": {"ready_bug_count": 0, "clue_count": 0}}

    normalized = dict(payload)
    data = dict(_dict(payload.get("data"))) if isinstance(payload.get("data"), dict) else dict(payload)

    shape_notes: list[str] = []
    if not isinstance(data.get("defects"), list):
        shape_notes.append("defects_not_a_list_coerced_empty")
    if not isinstance(data.get("clues"), list):
        shape_notes.append("clues_not_a_list_coerced_empty")

    defects = [item for item in _list(data.get("defects")) if isinstance(item, dict)]
    all_clues = _dedupe_by_identity(
        [item for item in _list(data.get("clues")) if isinstance(item, dict)]
    )
    data["defects"] = defects
    data["clues"] = all_clues
    data["risks"] = defects

    data["delivery_contract"] = {
        "source": "backend_formal_delivery_authority",
        "ready_bug_count": len(defects),
        "clue_count": len(all_clues),
        "contract_rule": (
            "data.defects is published by the formal delivery authority "
            "(experiment_batch_executor gate receipt -> obligation_attempt_ledger -> "
            "formal_delivery_scope.formal_customer_deliverable_findings -> "
            "canonical_defect_registry -> discovery_quality_projection). "
            "This adapter normalizes shape and counters only and never re-judges "
            "membership; data.clues contains internal validation leads."
        ),
    }
    if shape_notes:
        data["delivery_contract"]["normalization_notes"] = shape_notes
    _sync_command_center_counters(data, defects, all_clues)

    if "data" in payload:
        normalized["data"] = data
    else:
        normalized.update(data)
    normalized["delivery_contract"] = data["delivery_contract"]
    return normalized
