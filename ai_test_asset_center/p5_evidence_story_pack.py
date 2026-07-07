from __future__ import annotations

"""P5 customer-safe evidence story pack.

The story pack converts high-severity customer-safe findings into concise
"problem-impact-evidence-action" narratives for executive readout and sales
handoff. It intentionally avoids raw request/response payloads.
"""

from typing import Any


_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
_KIND_IMPACT = {
    "should_reject_but_succeeded": "Business rule enforcement failed: an operation that should have been rejected succeeded.",
    "field_mismatch": "Data consistency risk: two business-critical fields diverged during the flow.",
    "field_equals_forbidden_value": "Access-control or data-isolation risk: response contained a forbidden value.",
    "unexpected_server_error": "Reliability risk: a business flow produced an unexpected server error.",
    "status_mismatch": "Contract risk: observed HTTP status did not match the expected behavior.",
}
_KIND_ACTION = {
    "should_reject_but_succeeded": "Add or harden server-side validation and negative-path regression coverage.",
    "field_mismatch": "Add consistency checks, reconciliation rules and regression assertions for affected fields.",
    "field_equals_forbidden_value": "Review tenant/permission boundary checks and add isolation regression tests.",
    "unexpected_server_error": "Fix error handling and add resilience coverage for repeated or boundary operations.",
    "status_mismatch": "Align API contract, implementation and automated contract assertions.",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_text(value: Any, limit: int = 260) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _severity(value: Any) -> str:
    text = str(value or "P2").upper().strip()
    return text if text in _SEVERITY_RANK else "P2"


def _priority(severity: str) -> str:
    if severity == "P0":
        return "executive_critical"
    if severity == "P1":
        return "high"
    return "standard"


def _customer_safe_findings(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    scorecard = _as_dict(scan_result.get("p4_customer_value_scorecard"))
    readout = _as_dict(scan_result.get("p5_executive_readout_pack"))
    rows = _as_list(readout.get("customer_safe_findings")) or _as_list(scorecard.get("customer_safe_findings"))
    safe_rows = [row for row in rows if isinstance(row, dict)]
    return sorted(safe_rows, key=lambda row: (_SEVERITY_RANK.get(_severity(row.get("severity")), 99), _safe_text(row.get("seed_id"))))


def _story(row: dict[str, Any], index: int, context: dict[str, Any]) -> dict[str, Any]:
    severity = _severity(row.get("severity"))
    kind = _safe_text(row.get("kind"), 80)
    seed_id = _safe_text(row.get("seed_id") or row.get("id") or f"story_{index}", 120)
    title = _safe_text(row.get("title") or seed_id, 240)
    impact = _KIND_IMPACT.get(kind, "Business risk: the observed behavior may violate the intended product or customer workflow.")
    action = _KIND_ACTION.get(kind, "Review the flow with product and engineering owners, then add regression coverage.")
    return {
        "story_id": f"EVIDENCE_STORY_{index + 1:02d}",
        "seed_id": seed_id,
        "title": title,
        "severity": severity,
        "priority": _priority(severity),
        "kind": kind,
        "problem": f"{title} was observed during the approved pilot benchmark.",
        "business_impact": impact,
        "customer_safe_evidence": {
            "evidence_type": "benchmark_observation_summary",
            "seed_id": seed_id,
            "finding_status": _safe_text(row.get("status") or "found", 40),
            "raw_payload_included": False,
            "runtime_status": _safe_text(context.get("runtime_status"), 80),
            "evidence_bundle_status": _safe_text(context.get("evidence_bundle_status"), 80),
        },
        "recommended_action": action,
        "owner_discussion_prompt": "Confirm expected behavior, affected scope, fix owner and regression coverage before close-out.",
        "customer_safe": True,
    }


def build_p5_evidence_story_pack(scan_result: dict[str, Any], max_stories: int = 8) -> dict[str, Any]:
    result = _as_dict(scan_result)
    scorecard = _as_dict(result.get("p4_customer_value_scorecard"))
    readout = _as_dict(result.get("p5_executive_readout_pack"))
    context = _as_dict(scorecard.get("execution_context"))
    findings = _customer_safe_findings(result)
    high_severity = [row for row in findings if _severity(row.get("severity")) in {"P0", "P1"}]
    selected = (high_severity or findings)[: max(1, int(max_stories or 8))]
    stories = [_story(row, index, context) for index, row in enumerate(selected)]
    p0_count = sum(1 for story in stories if story["severity"] == "P0")
    p1_count = sum(1 for story in stories if story["severity"] == "P1")
    return {
        "schema_version": "p5-evidence-story-pack-v1",
        "customer_safe": True,
        "project": _safe_text(result.get("project"), 120),
        "source": "p5_executive_readout_pack" if readout else "p4_customer_value_scorecard",
        "story_count": len(stories),
        "p0_story_count": p0_count,
        "p1_story_count": p1_count,
        "stories": stories,
        "readout_ready": bool(readout.get("executive_readout_ready")) if readout else False,
        "procurement_motion_ready": bool(readout.get("procurement_motion_ready")) if readout else False,
        "usage_guidance": [
            "Use these stories as executive-safe examples; keep raw request/response payloads in the evidence bundle only.",
            "Review each story with customer product, QA and engineering owners before remediation commitment.",
            "Attach verified evidence bundle references only after customer approval.",
        ],
        "non_goals": [
            "Do not expose raw request/response payloads in this pack.",
            "Do not present a story as fixed without rerun evidence.",
        ],
    }
