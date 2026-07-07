from __future__ import annotations

"""P8 pilot operations tracker.

P8 converts the P7 commercial handoff into an operational task tracker. It does
not create external tickets; it produces a customer-safe task board that can be
copied into CRM, Jira, Slack or a customer-success workspace.
"""

from typing import Any


_STAGE_TASK_TEMPLATES = {
    "procurement_followup": [
        ("schedule_procurement_alignment", "Schedule procurement-scope alignment", "sales_lead", "P0"),
        ("prepare_security_review_packet", "Prepare security and deployment review packet", "solution_lead", "P0"),
        ("confirm_commercial_timeline", "Confirm commercial timeline and decision process", "sales_lead", "P1"),
        ("map_private_deployment_requirements", "Map private deployment or SaaS-control requirements", "solution_lead", "P1"),
    ],
    "executive_readout": [
        ("schedule_executive_readout", "Schedule executive value readout", "sales_lead", "P0"),
        ("review_customer_safe_stories", "Review customer-safe P0/P1 evidence stories", "cs_lead", "P0"),
        ("close_evidence_warnings", "Close evidence warnings before procurement motion", "solution_lead", "P1"),
    ],
    "internal_remediation": [
        ("resolve_delivery_blockers", "Resolve delivery blockers before customer-facing motion", "product_owner", "P0"),
        ("rerun_benchmark", "Rerun benchmark and regenerate delivery package", "solution_lead", "P0"),
        ("review_not_deliverable_reason", "Review not-deliverable reason with product and engineering", "product_owner", "P1"),
    ],
    "internal_qualification": [
        ("complete_pilot_qualification", "Complete internal pilot qualification", "cs_lead", "P1"),
        ("validate_customer_safe_outputs", "Validate customer-safe output chain", "solution_lead", "P1"),
    ],
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_text(value: Any, limit: int = 320) -> str:
    return str(value or "").strip()[:limit]


def _task(task_id: str, title: str, owner_role: str, priority: str, *, source: str, exit_criteria: list[str], blockers: list[str] | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": title,
        "owner_role": owner_role,
        "priority": priority,
        "status": "open",
        "source": source,
        "blockers": blockers or [],
        "exit_criteria": exit_criteria,
    }


def _stage_tasks(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    stage = _safe_text(handoff.get("sales_stage") or "internal_qualification", 80)
    templates = _STAGE_TASK_TEMPLATES.get(stage, _STAGE_TASK_TEMPLATES["internal_qualification"])
    tasks: list[dict[str, Any]] = []
    for task_id, title, owner, priority in templates:
        tasks.append(
            _task(
                task_id,
                title,
                owner,
                priority,
                source="p7_sales_handoff_package",
                exit_criteria=["Owner accepted", "Next step recorded", "Status updated in operating tracker"],
            )
        )
    return tasks


def _risk_tasks(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, risk in enumerate(_as_list(handoff.get("risk_register"))):
        if not isinstance(risk, dict):
            continue
        severity = _safe_text(risk.get("severity"), 40)
        if severity not in {"blocker", "warning"}:
            continue
        code = _safe_text(risk.get("code"), 120) or f"RISK_{index + 1}"
        priority = "P0" if severity == "blocker" else "P1"
        tasks.append(
            _task(
                f"triage_{code.lower()}",
                f"Triage {code}",
                "solution_lead" if severity == "warning" else "product_owner",
                priority,
                source="p7_risk_register",
                blockers=[code] if severity == "blocker" else [],
                exit_criteria=["Risk owner assigned", "Mitigation documented", "Rerun or customer decision recorded"],
            )
        )
    return tasks


def _action_tasks(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, action in enumerate(_as_list(handoff.get("commercial_next_actions"))[:5]):
        if not str(action or "").strip():
            continue
        tasks.append(
            _task(
                f"commercial_action_{index + 1}",
                _safe_text(action, 220),
                "sales_lead" if index == 0 else "cs_lead",
                "P1",
                source="p7_commercial_next_actions",
                exit_criteria=["Action completed or explicitly deferred", "Outcome captured in CRM-safe notes"],
            )
        )
    return tasks


def _dedupe_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for task in tasks:
        key = _safe_text(task.get("task_id"), 160)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(task)
    return deduped


def _summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    by_priority: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    blocked = 0
    for task in tasks:
        priority = _safe_text(task.get("priority"), 20) or "P2"
        owner = _safe_text(task.get("owner_role"), 80) or "unassigned"
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_owner[owner] = by_owner.get(owner, 0) + 1
        if _as_list(task.get("blockers")):
            blocked += 1
    return {
        "total_tasks": len(tasks),
        "open_tasks": len([task for task in tasks if task.get("status") == "open"]),
        "blocked_tasks": blocked,
        "tasks_by_priority": dict(sorted(by_priority.items())),
        "tasks_by_owner_role": dict(sorted(by_owner.items())),
    }


def _operating_status(handoff: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    if not handoff:
        return "handoff_missing"
    if any(_as_list(task.get("blockers")) for task in tasks):
        return "blocked"
    if handoff.get("handoff_ready") is True:
        return "ready_for_customer_motion"
    return "internal_followup_required"


def build_p8_pilot_operations_tracker(scan_result: dict[str, Any]) -> dict[str, Any]:
    result = _as_dict(scan_result)
    handoff = _as_dict(result.get("p7_sales_handoff_package"))
    tasks = _dedupe_tasks(_stage_tasks(handoff) + _risk_tasks(handoff) + _action_tasks(handoff))
    status = _operating_status(handoff, tasks)
    return {
        "schema_version": "p8-pilot-operations-tracker-v1",
        "customer_safe": True,
        "project": _safe_text(result.get("project"), 120),
        "operating_status": status,
        "sales_stage": _safe_text(handoff.get("sales_stage"), 80),
        "customer_success_stage": _safe_text(handoff.get("customer_success_stage"), 80),
        "handoff_ready": bool(handoff.get("handoff_ready")),
        "procurement_ready": bool(handoff.get("procurement_ready")),
        "task_summary": _summary(tasks),
        "tasks": tasks,
        "raci_roles": {
            "sales_lead": "Owns customer-facing commercial motion and CRM updates.",
            "cs_lead": "Owns customer success cadence and stakeholder follow-through.",
            "solution_lead": "Owns technical evidence packaging, security review and deployment scoping.",
            "product_owner": "Owns product blockers, benchmark gaps and remediation planning.",
        },
        "done_when": [
            "All P0 tasks are closed or explicitly accepted as customer risk.",
            "Customer-facing package is confirmed customer-safe before sending.",
            "Next meeting type, attendees and owner are recorded.",
            "CRM-safe summary is copied from P7 handoff and updated after customer response.",
        ],
        "non_goals": [
            "Do not create external tickets automatically from this tracker.",
            "Do not assign named customer owners without explicit customer confirmation.",
            "Do not move blocked pilots into procurement motion.",
        ],
    }
