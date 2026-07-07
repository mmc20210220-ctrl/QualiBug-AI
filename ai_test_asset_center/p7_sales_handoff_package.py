from __future__ import annotations

"""P7 sales and customer-success handoff package.

P7 converts the P6 delivery decision into a commercial motion: sales stage,
customer-success stage, recommended next meeting, attendees, risks and CRM-safe
summary. It remains customer-safe and does not include raw evidence.
"""

from typing import Any


_STAGE_BY_DECISION = {
    "deliverable_for_procurement": ("procurement_followup", "commercial_expansion"),
    "deliverable_for_executive_readout": ("executive_readout", "pilot_readout"),
    "not_deliverable": ("internal_remediation", "evidence_hardening"),
    "internal_only": ("internal_qualification", "pilot_preparation"),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_text(value: Any, limit: int = 320) -> str:
    return str(value or "").strip()[:limit]


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    scorecard = _as_dict(result.get("p4_customer_value_scorecard"))
    delivery = _as_dict(result.get("p6_pilot_delivery_package"))
    return _as_dict(scorecard.get("board_metrics")) or _as_dict(delivery.get("board_metrics"))


def _meeting_type(decision: str) -> str:
    if decision == "deliverable_for_procurement":
        return "procurement_scope_alignment"
    if decision == "deliverable_for_executive_readout":
        return "executive_value_readout"
    if decision == "not_deliverable":
        return "internal_remediation_review"
    return "internal_pilot_qualification"


def _attendees(decision: str) -> list[str]:
    if decision == "deliverable_for_procurement":
        return ["Customer executive sponsor", "Customer QA/Product owner", "Customer security/procurement owner", "Vendor sales lead", "Vendor solution/CS lead"]
    if decision == "deliverable_for_executive_readout":
        return ["Customer executive sponsor", "Customer QA/Product owner", "Customer engineering owner", "Vendor sales lead", "Vendor solution/CS lead"]
    return ["Vendor product owner", "Vendor solution/CS lead", "Vendor engineering owner"]


def _commercial_actions(decision: str, delivery: dict[str, Any]) -> list[str]:
    if decision == "deliverable_for_procurement":
        return [
            "Schedule procurement-scope alignment using the P6 delivery package.",
            "Confirm deployment model: private deployment, SaaS, or hybrid controlled pilot expansion.",
            "Translate P0/P1 evidence stories into commercial value and risk reduction narrative.",
            "Prepare pricing, security review and procurement timeline discussion.",
        ]
    if decision == "deliverable_for_executive_readout":
        return [
            "Schedule executive value readout before procurement motion.",
            "Resolve evidence warnings and agree a follow-up benchmark round if needed.",
            "Confirm customer success owner and remediation-review cadence.",
        ]
    if decision == "not_deliverable":
        blockers = ", ".join(_safe_text(row.get("code"), 80) for row in _as_list(delivery.get("blockers")) if isinstance(row, dict))
        return [
            "Keep the pilot package internal.",
            "Resolve delivery blockers" + (f": {blockers}." if blockers else "."),
            "Regenerate P6 delivery package before any customer-facing motion.",
        ]
    return ["Complete internal qualification before customer-facing follow-up."]


def _risk_register(delivery: dict[str, Any], gate: dict[str, Any]) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    for row in _as_list(delivery.get("blockers")):
        if isinstance(row, dict):
            risks.append({"severity": "blocker", "code": _safe_text(row.get("code"), 120), "detail": _safe_text(row.get("detail"), 260)})
    for row in _as_list(delivery.get("warnings")) + _as_list(gate.get("warnings")):
        if isinstance(row, dict):
            risks.append({"severity": "warning", "code": _safe_text(row.get("code"), 120), "detail": _safe_text(row.get("detail"), 260)})
    if not risks:
        risks.append({"severity": "info", "code": "NO_DELIVERY_BLOCKERS_RECORDED", "detail": "No blocking delivery risks were recorded in the package."})
    return risks[:8]


def _crm_summary(project: str, decision: str, sales_stage: str, metrics: dict[str, Any]) -> str:
    found = int(metrics.get("seed_defects_found") or 0)
    total = int(metrics.get("seed_defects_total") or 0)
    p0 = int(metrics.get("p0_found") or 0)
    p1 = int(metrics.get("p1_found") or 0)
    rate = float(metrics.get("detection_rate") or 0.0)
    return (
        f"{project or 'Pilot'}: P7 handoff decision={decision}, sales_stage={sales_stage}. "
        f"Seed defects found {found}/{total}, detection_rate={rate * 100:.1f}%, P0={p0}, P1={p1}. "
        "Use customer-safe P6/P5 materials only; raw evidence remains internal unless separately approved."
    )[:700]


def build_p7_sales_handoff_package(scan_result: dict[str, Any]) -> dict[str, Any]:
    result = _as_dict(scan_result)
    delivery = _as_dict(result.get("p6_pilot_delivery_package"))
    gate = _as_dict(result.get("p4_pilot_success_gate"))
    project = _safe_text(result.get("project"), 120)
    decision = _safe_text(delivery.get("delivery_decision") or "internal_only", 80)
    sales_stage, cs_stage = _STAGE_BY_DECISION.get(decision, ("internal_qualification", "pilot_preparation"))
    metrics = _metrics(result)
    handoff_ready = bool(delivery.get("external_delivery_allowed")) and decision in {"deliverable_for_procurement", "deliverable_for_executive_readout"}
    procurement_ready = bool(delivery.get("procurement_package")) and decision == "deliverable_for_procurement"
    return {
        "schema_version": "p7-sales-handoff-package-v1",
        "customer_safe": True,
        "project": project,
        "handoff_ready": handoff_ready,
        "procurement_ready": procurement_ready,
        "sales_stage": sales_stage,
        "customer_success_stage": cs_stage,
        "recommended_meeting_type": _meeting_type(decision),
        "required_attendees": _attendees(decision),
        "commercial_next_actions": _commercial_actions(decision, delivery),
        "risk_register": _risk_register(delivery, gate),
        "customer_shareable_keys": _as_list(delivery.get("customer_shareable_keys")),
        "internal_only_keys": _as_list(delivery.get("internal_only_keys")),
        "crm_summary": _crm_summary(project, decision, sales_stage, metrics),
        "qualification": {
            "delivery_decision": decision,
            "external_delivery_allowed": bool(delivery.get("external_delivery_allowed")),
            "executive_readout_package": bool(delivery.get("executive_readout_package")),
            "procurement_package": bool(delivery.get("procurement_package")),
        },
        "non_goals": [
            "Do not share raw evidence bundle content from the sales handoff.",
            "Do not move to procurement when procurement_ready is false.",
            "Do not bypass customer security/procurement review for private deployment discussions.",
        ],
    }
