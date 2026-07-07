from __future__ import annotations

"""P5 executive readout pack.

P5 converts P4 decision evidence into a customer executive readout structure.
It does not create slides; it returns a customer-safe content pack that can feed
slides, customer success notes, or sales handoff material.
"""

from typing import Any


_DECISION_TITLES = {
    "procurement_ready": "Pilot proved critical value and is ready for procurement discussion.",
    "executive_readout_ready": "Pilot proved value and is ready for executive readout.",
    "needs_evidence_hardening": "Pilot has signal but needs stronger evidence before executive decision.",
    "not_ready": "Pilot is not ready for executive success claim.",
    "error": "Pilot readout requires remediation before sharing.",
}


_ZH_DECISION_TITLES = {
    "procurement_ready": "试点已证明关键价值，可进入采购推进讨论。",
    "executive_readout_ready": "试点已证明价值，可进入客户高层复盘。",
    "needs_evidence_hardening": "试点已有价值信号，但高层决策前需要补强证据。",
    "not_ready": "当前试点还不能作为成功案例对外呈现。",
    "error": "复盘材料生成异常，需要先修复。",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pct(value: Any) -> str:
    try:
        return f"{float(value or 0.0) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _safe_text(value: Any, limit: int = 240) -> str:
    return str(value or "")[:limit]


def _top_findings(scorecard: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    findings = []
    for row in _as_list(scorecard.get("customer_safe_findings"))[:limit]:
        if not isinstance(row, dict):
            continue
        findings.append(
            {
                "seed_id": _safe_text(row.get("seed_id"), 120),
                "title": _safe_text(row.get("title"), 240),
                "severity": _safe_text(row.get("severity"), 20),
                "kind": _safe_text(row.get("kind"), 80),
            }
        )
    return findings


def _missed_items(scorecard: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    missed = []
    for row in _as_list(scorecard.get("customer_safe_missed"))[:limit]:
        if not isinstance(row, dict):
            continue
        missed.append(
            {
                "seed_id": _safe_text(row.get("seed_id"), 120),
                "title": _safe_text(row.get("title"), 240),
                "severity": _safe_text(row.get("severity"), 20),
                "status": _safe_text(row.get("status"), 40),
            }
        )
    return missed


def _agenda(decision: str) -> list[str]:
    base = [
        "Confirm pilot scope, environment and safety constraints.",
        "Review defect discovery scorecard and high-severity examples.",
        "Review evidence readiness and remaining blockers.",
    ]
    if decision == "procurement_ready":
        base.append("Align on procurement path, deployment model and commercial next step.")
    elif decision == "executive_readout_ready":
        base.append("Agree evidence-hardening steps before procurement motion.")
    else:
        base.append("Agree remediation plan before presenting pilot as a success.")
    return base


def _zh_agenda(decision: str) -> list[str]:
    base = [
        "确认试点范围、环境和安全约束。",
        "复盘缺陷发现分数和高严重级别样例。",
        "复盘证据就绪度和剩余阻断项。",
    ]
    if decision == "procurement_ready":
        base.append("对齐采购路径、部署方式和商务下一步。")
    elif decision == "executive_readout_ready":
        base.append("确认进入采购前需要补强的证据动作。")
    else:
        base.append("确认作为成功案例呈现前的修复计划。")
    return base


def _readout_sections(decision: str, scorecard: dict[str, Any], gate: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = _as_dict(scorecard.get("board_metrics"))
    blockers = _as_list(gate.get("blockers"))
    warnings = _as_list(gate.get("warnings"))
    return [
        {
            "id": "executive_decision",
            "title": "Executive decision",
            "purpose": "State whether the pilot is ready for customer executive readout or procurement motion.",
            "bullets": [
                _DECISION_TITLES.get(decision, _DECISION_TITLES["not_ready"]),
                f"Pilot success: {bool(gate.get('pilot_success'))}.",
                f"Procurement motion ready: {bool(gate.get('procurement_motion_ready'))}.",
            ],
        },
        {
            "id": "value_metrics",
            "title": "Value metrics",
            "purpose": "Show measurable proof of bug-finding value.",
            "bullets": [
                f"Seed defects found: {int(metrics.get('seed_defects_found') or 0)}/{int(metrics.get('seed_defects_total') or 0)}.",
                f"Detection rate: {_pct(metrics.get('detection_rate'))}.",
                f"P0 found: {int(metrics.get('p0_found') or 0)}; P1 found: {int(metrics.get('p1_found') or 0)}.",
            ],
        },
        {
            "id": "top_findings",
            "title": "Customer-safe high severity examples",
            "purpose": "Give executives concrete examples without leaking raw request/response data.",
            "items": _top_findings(scorecard),
        },
        {
            "id": "evidence_readiness",
            "title": "Evidence readiness and risks",
            "purpose": "Separate value proof from evidence packaging work.",
            "bullets": [
                f"Evidence bundle status: {_safe_text(_as_dict(scorecard.get('execution_context')).get('evidence_bundle_status'))}.",
                f"Warnings: {len(warnings)}.",
                f"Blockers: {len(blockers)}.",
            ],
            "warnings": warnings[:5],
            "blockers": blockers[:5],
        },
        {
            "id": "next_steps",
            "title": "Next steps",
            "purpose": "Convert pilot result into customer and commercial motion.",
            "bullets": [_safe_text(item, 260) for item in _as_list(gate.get("next_actions"))[:5]],
        },
    ]


def _zh_summary(decision: str, scorecard: dict[str, Any], gate: dict[str, Any]) -> str:
    metrics = _as_dict(scorecard.get("board_metrics"))
    return (
        f"{_ZH_DECISION_TITLES.get(decision, _ZH_DECISION_TITLES['not_ready'])}"
        f" 本轮命中 {int(metrics.get('seed_defects_found') or 0)}/{int(metrics.get('seed_defects_total') or 0)} 个种子缺陷，"
        f"发现率 {_pct(metrics.get('detection_rate'))}，"
        f"P0 命中 {int(metrics.get('p0_found') or 0)} 个，P1 命中 {int(metrics.get('p1_found') or 0)} 个。"
        f" 当前采购推进状态：{bool(gate.get('procurement_motion_ready'))}。"
    )


def _en_summary(decision: str, scorecard: dict[str, Any], gate: dict[str, Any]) -> str:
    metrics = _as_dict(scorecard.get("board_metrics"))
    return (
        f"{_DECISION_TITLES.get(decision, _DECISION_TITLES['not_ready'])} "
        f"This run found {int(metrics.get('seed_defects_found') or 0)}/{int(metrics.get('seed_defects_total') or 0)} seed defects "
        f"with a detection rate of {_pct(metrics.get('detection_rate'))}. "
        f"P0 found: {int(metrics.get('p0_found') or 0)}; P1 found: {int(metrics.get('p1_found') or 0)}. "
        f"Procurement motion ready: {bool(gate.get('procurement_motion_ready'))}."
    )


def build_p5_executive_readout_pack(scan_result: dict[str, Any]) -> dict[str, Any]:
    result = _as_dict(scan_result)
    scorecard = _as_dict(result.get("p4_customer_value_scorecard"))
    gate = _as_dict(result.get("p4_pilot_success_gate"))
    decision = _safe_text(gate.get("decision") or "not_ready", 80)
    return {
        "schema_version": "p5-executive-readout-pack-v1",
        "customer_safe": True,
        "project": _safe_text(result.get("project"), 120),
        "decision": decision,
        "pilot_success": bool(gate.get("pilot_success")),
        "executive_readout_ready": bool(gate.get("executive_readout_ready")),
        "procurement_motion_ready": bool(gate.get("procurement_motion_ready")),
        "executive_summary_zh": _zh_summary(decision, scorecard, gate),
        "executive_summary_en": _en_summary(decision, scorecard, gate),
        "meeting_agenda": _agenda(decision),
        "meeting_agenda_zh": _zh_agenda(decision),
        "readout_sections": _readout_sections(decision, scorecard, gate),
        "customer_safe_findings": _top_findings(scorecard),
        "customer_safe_missed": _missed_items(scorecard),
        "board_metrics": _as_dict(scorecard.get("board_metrics")),
        "pilot_gate": gate,
        "recommended_owner_actions": [_safe_text(item, 260) for item in _as_list(gate.get("next_actions"))[:5]],
        "non_goals": [
            "Do not include raw request or response payloads in executive readout.",
            "Do not claim procurement readiness when evidence persistence is not ready.",
            "Do not present missed seed defects as resolved without rerun evidence.",
        ],
    }
