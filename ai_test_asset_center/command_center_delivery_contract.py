from __future__ import annotations

"""Normalize command-center delivery payloads through the customer delivery gate.

This adapter is intentionally small and side-effect free. Service layers can call
it immediately before returning `/command-center` responses so legacy `risks`,
`findings`, or previously materialized `defects` cannot bypass the backend
customer-delivery gate.
"""

from typing import Any

from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks


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


def _collect_candidate_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("defects", "risks", "findings"):
        for item in _list(data.get(key)):
            if isinstance(item, dict):
                candidates.append(item)
    return _dedupe_by_identity(candidates)


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
    """Return a command-center payload whose customer defects pass the gate.

    The function preserves the original envelope fields but rewrites:
    - data.defects: only backend-gate accepted customer defects
    - data.clues: original clues plus all non-ready candidates
    - data.risks: compatibility alias for data.defects
    - delivery_contract: explicit summary for UI and tests
    """
    if not isinstance(payload, dict):
        return {"data": {"defects": [], "clues": [], "risks": []}, "delivery_contract": {"ready_bug_count": 0, "clue_count": 0}}

    normalized = dict(payload)
    data = dict(_dict(payload.get("data"))) if isinstance(payload.get("data"), dict) else dict(payload)

    candidates = _collect_candidate_items(data)
    existing_clues = [item for item in _list(data.get("clues")) if isinstance(item, dict)]
    defects, rejected_clues = split_customer_delivery_tracks(candidates)

    all_clues = _dedupe_by_identity([*existing_clues, *rejected_clues])
    data["defects"] = defects
    data["clues"] = all_clues
    data["risks"] = defects

    data["delivery_contract"] = {
        "source": "backend_customer_delivery_gate",
        "ready_bug_count": len(defects),
        "clue_count": len(all_clues),
        "contract_rule": "data.defects contains only gate-accepted customer-deliverable defects; data.clues contains internal validation leads.",
    }
    _sync_command_center_counters(data, defects, all_clues)

    if "data" in payload:
        normalized["data"] = data
    else:
        normalized.update(data)
    normalized["delivery_contract"] = data["delivery_contract"]
    return normalized
