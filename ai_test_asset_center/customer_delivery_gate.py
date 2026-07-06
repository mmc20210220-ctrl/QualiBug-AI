from __future__ import annotations

"""Backend customer-delivery gate for commercial defect promotion.

This module is intentionally stricter than discovery/runtime verdicts. A finding
is allowed into the customer-facing defect list only when the business evidence
contract is explicit, replayable, and free of missing requirements. Everything
else remains an internal clue until more evidence is collected.
"""

from typing import Any

CUSTOMER_READY_MIN_EVIDENCE_SCORE = 90
_ALLOWED_FINAL_REVIEW_STATUSES = {"PENDING_REVIEW", "VALIDATED_CANDIDATE", "CUSTOMER_READY"}
_BLOCKED_LANE_MARKERS = {
    "route_blocked",
    "auth_blocked",
    "environment_blocked",
    "coverage_gap",
    "validation_lead",
    "not_reproduced",
}
_SYNTHETIC_MARKERS = {"simulation", "simulated", "demo", "synthetic", "mock"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if parsed == parsed else default


def has_validated_evidence_quality(item: dict[str, Any]) -> bool:
    quality = _dict(item.get("evidence_quality"))
    return (
        _lower(quality.get("level")) == "validated"
        and _number(quality.get("score")) >= CUSTOMER_READY_MIN_EVIDENCE_SCORE
        and bool(quality.get("can_reproduce"))
    )


def has_passed_business_evidence_status(item: dict[str, Any]) -> bool:
    status = _dict(item.get("evidence_status"))
    if not status:
        return False
    if _upper(status.get("semantic_verdict")) != "SEMANTIC_CONFIRMED":
        return False
    if _upper(status.get("business_evidence_status")) != "VALIDATED":
        return False
    if _upper(status.get("final_review_status")) not in _ALLOWED_FINAL_REVIEW_STATUSES:
        return False
    return len(_list(status.get("missing_requirements"))) == 0


def has_explicit_failure_assertion(item: dict[str, Any]) -> bool:
    if _list(item.get("failed_assertions")):
        return True
    comparison = _dict(item.get("expected_actual_comparison"))
    if _text(comparison.get("difference")):
        return True
    expected = _text(item.get("expected") or comparison.get("expected"))
    actual = _text(item.get("actual") or comparison.get("actual"))
    return bool(expected and actual and expected != actual)


def has_customer_facing_hard_evidence(item: dict[str, Any]) -> bool:
    raw_evidence = _dict(item.get("raw_evidence"))
    reproduction = _dict(item.get("reproduction"))
    request_raw = _dict(raw_evidence.get("request_raw"))
    response_raw = _dict(raw_evidence.get("response_raw"))
    har = _dict(reproduction.get("har_evidence"))

    has_request = bool(request_raw.get("method") and request_raw.get("path")) or bool(
        reproduction.get("method") and reproduction.get("path")
    )
    has_response = bool(
        response_raw.get("status_code")
        or response_raw.get("body")
        or har.get("status_code")
        or har.get("response_body")
    )
    has_timestamp = bool(raw_evidence.get("timestamp") or item.get("timestamp"))
    has_real_evidence = bool(raw_evidence.get("has_real_evidence") or har)

    return has_request and has_response and has_explicit_failure_assertion(item) and has_timestamp and has_real_evidence


def has_customer_replay_asset(item: dict[str, Any]) -> bool:
    reproduction = _dict(item.get("reproduction"))
    har = _dict(reproduction.get("har_evidence"))
    method = _text(reproduction.get("method") or item.get("repro_method")).upper()
    path = _text(reproduction.get("path") or item.get("repro_path"))
    if not method or not path:
        return False
    if bool(reproduction.get("is_synthetic")):
        return False
    return bool(har.get("status_code") or har.get("response_body"))


def is_customer_deliverable_defect(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("customer_delivery_status") not in {None, "defect"}:
        return False
    if _text(item.get("bug_status")) != "reproduced":
        return False
    if not bool(item.get("gate_passed")):
        return False

    execution_status = _lower(item.get("execution_status"))
    confirmation_status = _lower(item.get("confirmation_status"))
    evidence_level = _lower(item.get("evidence_level"))
    execution_source = _lower(item.get("execution_source"))
    if any(marker in evidence_level or marker in execution_source for marker in _SYNTHETIC_MARKERS):
        return False
    if execution_status and execution_status != "executed":
        return False
    if confirmation_status and confirmation_status not in {"confirmed", "validated_candidate"}:
        return False

    consistency = _dict(item.get("evidence_consistency"))
    if _lower(consistency.get("verdict")) in {"rejected", "missing"}:
        return False

    lane = " ".join(
        _lower(item.get(key))
        for key in ("value_lane", "_value_lane", "execution_block", "block_reason")
    )
    if any(marker in lane for marker in _BLOCKED_LANE_MARKERS):
        return False

    return (
        has_validated_evidence_quality(item)
        and has_passed_business_evidence_status(item)
        and has_customer_replay_asset(item)
        and has_customer_facing_hard_evidence(item)
    )


def split_customer_delivery_tracks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    defects: list[dict[str, Any]] = []
    clues: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if is_customer_deliverable_defect(item):
            defects.append({
                **item,
                "delivery_track": "defect",
                "customer_delivery_status": "defect",
                "customer_delivery_label": "客户可交付缺陷",
                "customer_visible": True,
            })
        else:
            clues.append({
                **item,
                "delivery_track": "clue",
                "customer_delivery_status": "clue",
                "customer_delivery_label": "内部待验证线索",
                "customer_visible": False,
            })
    return defects, clues
