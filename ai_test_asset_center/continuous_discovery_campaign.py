from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from .business_risk_coverage_map import coverage_key
from .real_project_onboarding import ROOT, _safe_project_id

SCHEMA_VERSION = "continuous-discovery-campaign-v1"
RUN_HISTORY_LIMIT = 20
REVALIDATION_TRIGGERS = {
    "environment_recovered",
    "knowledge_asset_updated",
    "data_ready",
    "path_alignment_changed",
    "blocker_cleared",
}
ACTIONABLE_STATUSES = {"untouched", "candidate", "pending", "revalidate_due"}
STATUS_PRIORITY = {
    "pending": 0,
    "revalidate_due": 1,
    "candidate": 2,
    "untouched": 3,
    "blocked": 4,
    "validated": 5,
}
SEVERITY_SCORES = {"P0": 1.0, "P1": 0.85, "P2": 0.65, "P3": 0.45}
FRONTIER_BUDGET_PROFILES = {
    "safe": {
        "default_frontier_budget": 9,
        "ratios": {"explore": 0.35, "exploit": 0.5, "revalidate": 0.15},
        "exploit_pending_reserve_slots": 2,
    },
    "standard": {
        "default_frontier_budget": 12,
        "ratios": {"explore": 0.3, "exploit": 0.5, "revalidate": 0.2},
        "exploit_pending_reserve_slots": 3,
    },
    "aggressive": {
        "default_frontier_budget": 16,
        "ratios": {"explore": 0.28, "exploit": 0.47, "revalidate": 0.25},
        "exploit_pending_reserve_slots": 4,
    },
}
LONG_BLOCKED_ROUND_THRESHOLD = 2
HIGH_VALUE_FRONTIER_SCORE_FLOOR = 0.8
MARGINAL_VALIDATED_YIELD_THRESHOLD = 0.15

CAMPAIGN_OBJECT_MODEL = {
    "campaign": "Long-lived continuous discovery container. A run is only one budget slice inside the campaign.",
    "run": "One execution slice that advances evidence, frontier selection and coverage state without claiming full project completion.",
    "frontier": "A behavior unit selected for the next round because it still has discovery value, evidence gaps or revalidation debt.",
    "coverage_ledger": "Cross-round durable ledger keyed by stable behavior-unit fingerprints and tracking current status, blockers and latest action.",
    "revalidation": "A deliberate revisit of previously validated or blocked high-value behavior after environment, knowledge or data conditions change.",
}
CAMPAIGN_STATE_MACHINE = {
    "states": {
        "scheduled": "Campaign has more work to do and is waiting for the next run budget.",
        "active": "A run is currently consuming budget and updating coverage/frontier state.",
        "blocked": "No actionable frontier is ready; remaining work is blocked by environment, data, safety or approval conditions.",
        "completed": "No actionable or blocked frontier remains. Current coverage debt is closed for now.",
        "paused": "Manual or governance pause. Work may resume later without losing the ledger.",
    },
    "transitions": [
        {"from": "scheduled", "to": "active", "when": "A new run starts."},
        {"from": "active", "to": "scheduled", "when": "A run ends and actionable frontier still exists."},
        {"from": "active", "to": "blocked", "when": "A run ends and only blocked frontier remains."},
        {"from": "active", "to": "completed", "when": "A run ends and no remaining frontier needs action."},
        {"from": "blocked", "to": "scheduled", "when": "A blocker is cleared or revalidation is triggered."},
        {"from": "scheduled", "to": "paused", "when": "A manual pause or governance hold is applied."},
        {"from": "paused", "to": "scheduled", "when": "A manual resume is approved."},
        {"from": "completed", "to": "scheduled", "when": "Fresh triggers reopen revalidation or new frontier appears."},
    ],
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _template_path(value: Any) -> str:
    text = str(value or "/").split("?", 1)[0].strip() or "/"
    if "://" in text:
        text = "/" + text.split("://", 1)[1].split("/", 1)[1] if "/" in text.split("://", 1)[1] else "/"
    parts = [part for part in text.split("/") if part]
    normalized: list[str] = []
    for part in parts:
        if part.isdigit():
            normalized.append("{id}")
        else:
            normalized.append(part)
    return "/" + "/".join(normalized) if normalized else "/"


def _extract_method(item: dict[str, Any]) -> str:
    request = _as_dict(item.get("request"))
    return str(
        item.get("method")
        or item.get("action_method")
        or request.get("method")
        or "GET"
    ).upper()


def _extract_path(item: dict[str, Any]) -> str:
    request = _as_dict(item.get("request"))
    return _template_path(
        item.get("path")
        or item.get("endpoint")
        or item.get("api")
        or item.get("url")
        or request.get("url")
        or "/"
    )


def _semantic_dimensions(item: dict[str, Any]) -> dict[str, str]:
    role = item.get("actor_role") or item.get("role") or item.get("persona") or item.get("permission_boundary") or ""
    state = item.get("state_transition") or item.get("lifecycle") or item.get("workflow_state") or item.get("status") or ""
    relation = item.get("relation") or item.get("cross_object_relation") or ""
    entity = item.get("entity") or item.get("entity_type") or item.get("resource") or ""
    risk_type = item.get("risk_type") or item.get("defect_family") or item.get("invariant_kind") or "unknown"
    prepared = {
        "entity": entity,
        "method": _extract_method(item),
        "path": _extract_path(item),
        "risk_type": risk_type,
        "actor_role": role,
        "permission_boundary": item.get("permission_boundary") or "",
        "state_transition": state,
        "relation": relation,
    }
    return {key: _clean_text(value) for key, value in prepared.items()}


def _behavior_key(item: dict[str, Any]) -> tuple[str, dict[str, str]]:
    dimensions = _semantic_dimensions(item)
    key = coverage_key(
        {
            "entity": dimensions["entity"],
            "method": dimensions["method"],
            "path": dimensions["path"],
            "risk_type": dimensions["risk_type"],
            "actor_role": dimensions["actor_role"],
            "permission_boundary": dimensions["permission_boundary"],
            "state_transition": dimensions["state_transition"],
            "relation": dimensions["relation"],
        }
    )
    return key, dimensions


def _value_score(probe: dict[str, Any], issue: dict[str, Any]) -> float:
    priority = float(probe.get("priority_score") or issue.get("priority_score") or 0.0)
    severity = str(issue.get("severity") or probe.get("severity") or "P2").upper()
    return round(max(priority, SEVERITY_SCORES.get(severity, SEVERITY_SCORES["P2"])), 4)


def _issue_accounting(issue: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(issue.get("validated_bug_accounting"))


def _evidence_maturity(issue: dict[str, Any]) -> str:
    accounting = _issue_accounting(issue)
    if accounting.get("strict_validated_bug"):
        return "strict_validated"
    if accounting.get("verifier_passed") and accounting.get("has_reproduction"):
        return "needs_evidence_refs"
    if accounting.get("verifier_passed"):
        return "needs_reproduction"
    if accounting.get("accounting_state") == "pending":
        return "pending_validation"
    if accounting.get("accounting_state") == "candidate":
        return "candidate_only"
    return "not_started"


def _probe_blocker_reason(probe: dict[str, Any]) -> str:
    return str(
        probe.get("blocker")
        or probe.get("blocked_reason")
        or probe.get("capability_gate")
        or ""
    ).strip()


def _issue_blocker_reason(issue: dict[str, Any]) -> str:
    accounting = _issue_accounting(issue)
    return str(
        accounting.get("primary_blocker_reason_code")
        or issue.get("blocker")
        or issue.get("reason")
        or issue.get("actual")
        or ""
    ).strip()[:300]


def _status_from_probe_issue(probe: dict[str, Any], issue: dict[str, Any]) -> str:
    accounting = _issue_accounting(issue)
    if accounting.get("strict_validated_bug"):
        return "validated"
    if accounting.get("accounting_state") == "pending":
        return "pending"
    if accounting.get("accounting_state") == "candidate":
        return "candidate"
    if issue and _issue_blocker_reason(issue):
        return "blocked"
    if probe and (probe.get("blocked") or probe.get("blocked_by_safety") or _probe_blocker_reason(probe)):
        return "blocked"
    return "untouched"


def _last_run_result(status: str, probe: dict[str, Any], issue: dict[str, Any]) -> str:
    if status == "validated":
        return "strict_validated_bug"
    if status == "pending":
        return "pending_finding"
    if status == "candidate":
        return "candidate_finding"
    if status == "blocked":
        return "blocked_execution" if probe else "blocked_issue_progression"
    if probe:
        return "planned_but_not_closed"
    return "carry_forward"


def _next_action(status: str, blocker_reason: str, evidence_maturity: str) -> str:
    if status == "validated":
        return "Wait for a revalidation trigger before replaying this behavior unit."
    if status == "pending":
        if evidence_maturity == "needs_evidence_refs":
            return "Capture missing evidence refs to promote the pending frontier to strict validated."
        if evidence_maturity == "needs_reproduction":
            return "Add deterministic reproduction so the verifier-passed finding can close."
        return "Close the remaining verifier, repro or evidence gap."
    if status == "candidate":
        return "Promote from candidate by executing the path and collecting verifier, repro and evidence signals."
    if status == "blocked":
        return f"Clear blocker and reschedule frontier: {blocker_reason or 'unknown_blocker'}"
    if status == "revalidate_due":
        return "Replay previously validated or stale frontier because a trigger reopened it."
    return "Schedule initial exploration for this untouched behavior unit."


def _trigger_label(trigger: str) -> str:
    return {
        "environment_recovered": "environment recovery",
        "knowledge_asset_updated": "knowledge asset update",
        "data_ready": "data readiness",
        "path_alignment_changed": "path alignment change",
        "blocker_cleared": "blocker clearance",
    }.get(trigger, trigger.replace("_", " "))


def _wake_conditions_for_blocker(blocker_reason: str) -> list[str]:
    text = blocker_reason.lower()
    if not text:
        return ["blocker_cleared"]
    wake_conditions = {"blocker_cleared"}
    if any(token in text for token in ("environment", "network", "timeout", "sandbox", "health", "unreachable")):
        wake_conditions.add("environment_recovered")
    if any(token in text for token in ("data", "fixture", "seed", "snapshot", "record_missing", "record missing")):
        wake_conditions.add("data_ready")
    if any(token in text for token in ("path", "alignment", "route", "contract", "schema", "doc")):
        wake_conditions.add("path_alignment_changed")
        wake_conditions.add("knowledge_asset_updated")
    if any(token in text for token in ("evidence", "reproduction", "verifier", "knowledge")):
        wake_conditions.add("knowledge_asset_updated")
    return sorted(wake_conditions)


def _normalized_budget_config(run_context: dict[str, Any]) -> dict[str, Any]:
    requested_mode = str(run_context.get("mode") or "standard").lower()
    profile = dict(FRONTIER_BUDGET_PROFILES.get(requested_mode, FRONTIER_BUDGET_PROFILES["standard"]))
    ratios = dict(profile.get("ratios") or {})
    custom_ratios = _as_dict(run_context.get("budget_ratios"))
    for bucket in ("explore", "exploit", "revalidate"):
        raw_value = custom_ratios.get(bucket)
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            ratios[bucket] = value
    total = sum(float(ratios.get(bucket) or 0.0) for bucket in ("explore", "exploit", "revalidate"))
    if total <= 0:
        ratios = dict(profile.get("ratios") or FRONTIER_BUDGET_PROFILES["standard"]["ratios"])
        total = sum(ratios.values())
    normalized = {
        bucket: round(float(ratios.get(bucket) or 0.0) / total, 4)
        for bucket in ("explore", "exploit", "revalidate")
    }
    frontier_budget = int(
        run_context.get("frontier_budget")
        or run_context.get("max_next_frontier")
        or profile.get("default_frontier_budget")
        or FRONTIER_BUDGET_PROFILES["standard"]["default_frontier_budget"]
    )
    exploit_pending_reserve_slots = int(
        run_context.get("exploit_pending_reserve_slots")
        or profile.get("exploit_pending_reserve_slots")
        or FRONTIER_BUDGET_PROFILES["standard"]["exploit_pending_reserve_slots"]
    )
    return {
        "mode": requested_mode,
        "frontier_budget": max(3, frontier_budget),
        "ratios": normalized,
        "exploit_pending_reserve_slots": max(1, exploit_pending_reserve_slots),
    }


def _validated_yield_score(entry: dict[str, Any]) -> float:
    status = str(entry.get("last_status") or "untouched")
    evidence_maturity = str(entry.get("evidence_maturity") or "not_started")
    frontier = _as_dict(entry.get("frontier"))
    score = {
        "pending": 0.92,
        "revalidate_due": 0.82,
        "candidate": 0.58,
        "untouched": 0.34,
        "blocked": 0.18,
        "validated": 0.12,
    }.get(status, 0.2)
    score += {
        "strict_validated": 0.08,
        "needs_evidence_refs": 0.2,
        "needs_reproduction": 0.16,
        "pending_validation": 0.1,
        "candidate_only": 0.03,
    }.get(evidence_maturity, 0.0)
    if frontier.get("recheck_ready"):
        score += 0.18
    if entry.get("recent_blocker_clearance"):
        score += 0.1
    return round(min(1.0, score), 4)


def _coverage_gap_score(entry: dict[str, Any]) -> float:
    status = str(entry.get("last_status") or "untouched")
    rounds_seen = int(entry.get("rounds_seen") or 0)
    base = {
        "untouched": 1.0,
        "candidate": 0.8,
        "pending": 0.64,
        "revalidate_due": 0.5,
        "blocked": 0.42,
        "validated": 0.12,
    }.get(status, 0.2)
    freshness_penalty = min(0.18, max(0, rounds_seen - 1) * 0.03)
    return round(max(0.05, base - freshness_penalty), 4)


def _closure_score(entry: dict[str, Any]) -> float:
    evidence_maturity = str(entry.get("evidence_maturity") or "not_started")
    frontier = _as_dict(entry.get("frontier"))
    score = {
        "strict_validated": 0.78,
        "needs_evidence_refs": 0.96,
        "needs_reproduction": 0.9,
        "pending_validation": 0.72,
        "candidate_only": 0.42,
        "not_started": 0.28,
    }.get(evidence_maturity, 0.28)
    if frontier.get("recheck_ready"):
        score += 0.08
    return round(min(1.0, score), 4)


def _blocker_relief_score(entry: dict[str, Any]) -> float:
    frontier = _as_dict(entry.get("frontier"))
    if frontier.get("recheck_ready"):
        return 1.0
    if entry.get("recent_blocker_clearance"):
        return 0.9
    blocker_reason = str(entry.get("last_blocker_reason") or "")
    return 0.25 if blocker_reason else 0.0


def _frontier_budget_class(entry: dict[str, Any]) -> str:
    status = str(entry.get("last_status") or "untouched")
    frontier = _as_dict(entry.get("frontier"))
    if status == "revalidate_due" or frontier.get("recheck_ready"):
        return "revalidate"
    if status == "pending":
        return "exploit"
    if status == "candidate":
        if _closure_score(entry) >= 0.72 or float(entry.get("business_value_score") or 0.0) >= 0.8:
            return "exploit"
        return "explore"
    return "explore"


def _frontier_schedule_score(entry: dict[str, Any]) -> float:
    budget_class = _frontier_budget_class(entry)
    business_value = float(entry.get("business_value_score") or 0.0)
    yield_score = _validated_yield_score(entry)
    coverage_gap = _coverage_gap_score(entry)
    closure_score = _closure_score(entry)
    blocker_relief = _blocker_relief_score(entry)
    score = (
        business_value * 0.34
        + yield_score * 0.28
        + closure_score * 0.18
        + coverage_gap * 0.14
        + blocker_relief * 0.06
    )
    if budget_class == "exploit" and str(entry.get("last_status") or "") == "pending":
        score += 0.08
    if budget_class == "revalidate":
        score += 0.05
    return round(min(1.5, score), 6)


def _entry_is_actionable(entry: dict[str, Any]) -> bool:
    status = str(entry.get("last_status") or "")
    frontier = _as_dict(entry.get("frontier"))
    return status in ACTIONABLE_STATUSES or bool(frontier.get("recheck_ready"))


def _entry_has_open_frontier(entry: dict[str, Any]) -> bool:
    return str(entry.get("last_status") or "untouched") != "validated"


def _entry_is_high_value_uncovered(entry: dict[str, Any]) -> bool:
    return _entry_has_open_frontier(entry) and float(entry.get("business_value_score") or 0.0) >= HIGH_VALUE_FRONTIER_SCORE_FLOOR


def _entry_is_in_revalidation_queue(entry: dict[str, Any]) -> bool:
    status = str(entry.get("last_status") or "untouched")
    frontier = _as_dict(entry.get("frontier"))
    return status == "revalidate_due" or bool(frontier.get("recheck_ready"))


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 3)


def _ledger_snapshot(ledger: dict[str, Any]) -> dict[str, Any]:
    validated_keys: set[str] = set()
    pending_keys: set[str] = set()
    open_keys: set[str] = set()
    high_value_uncovered_keys: set[str] = set()
    revalidation_queue_keys: set[str] = set()
    for key, raw_entry in ledger.items():
        entry = _as_dict(raw_entry)
        status = str(entry.get("last_status") or "untouched")
        if status == "validated":
            validated_keys.add(key)
        if status == "pending":
            pending_keys.add(key)
        if _entry_has_open_frontier(entry):
            open_keys.add(key)
        if _entry_is_high_value_uncovered(entry):
            high_value_uncovered_keys.add(key)
        if _entry_is_in_revalidation_queue(entry):
            revalidation_queue_keys.add(key)
    return {
        "validated_keys": validated_keys,
        "pending_keys": pending_keys,
        "open_keys": open_keys,
        "high_value_uncovered_keys": high_value_uncovered_keys,
        "revalidation_queue_keys": revalidation_queue_keys,
    }


def _build_campaign_dashboard(
    *,
    prior_snapshot: dict[str, Any],
    entries: list[dict[str, Any]],
    status_counts: dict[str, int],
    next_state: str,
    probe_count: int,
) -> dict[str, Any]:
    current_snapshot = _ledger_snapshot(
        {
            str(entry.get("behavior_key") or f"entry-{index}"): entry
            for index, entry in enumerate(entries)
        }
    )
    prior_validated_keys = set(prior_snapshot.get("validated_keys") or set())
    prior_pending_keys = set(prior_snapshot.get("pending_keys") or set())
    prior_open_keys = set(prior_snapshot.get("open_keys") or set())
    current_validated_keys = set(current_snapshot.get("validated_keys") or set())
    current_open_keys = set(current_snapshot.get("open_keys") or set())
    current_high_value_uncovered_keys = set(current_snapshot.get("high_value_uncovered_keys") or set())
    current_revalidation_queue_keys = set(current_snapshot.get("revalidation_queue_keys") or set())

    new_validated_keys = current_validated_keys - prior_validated_keys
    pending_to_validated_count = len(new_validated_keys & prior_pending_keys)
    prior_pending_count = len(prior_pending_keys)
    cumulative_validated_bug_count = len(current_validated_keys)
    burned_down_frontier_count = max(0, len(prior_open_keys) - len(current_open_keys))
    run_validated_yield = _safe_rate(len(new_validated_keys), probe_count)

    remaining_risks: list[str] = []
    if int(status_counts.get("pending") or 0) > 0:
        remaining_risks.append("仍有 pending frontier 只差 verifier、repro 或 evidence 补齐。")
    if int(status_counts.get("candidate") or 0) > 0:
        remaining_risks.append("仍有 candidate frontier 尚未进入 grounded execution 和 strict verification。")
    if len(current_high_value_uncovered_keys) > 0:
        remaining_risks.append("仍有高价值未覆盖行为尚未闭环，不能宣称 campaign 已充分扫完。")
    if len(current_revalidation_queue_keys) > 0:
        remaining_risks.append("重检队列仍有待消费项，历史 validated 或 blocker-cleared frontier 需要重放。")
    if int(status_counts.get("blocked") or 0) > 0:
        remaining_risks.append("仍有 blocked frontier 依赖环境、数据或路径对齐恢复后再推进。")
    if not remaining_risks:
        remaining_risks.append("当前没有显式 frontier 债务，后续只需等待新的 revalidation trigger 或新增行为面。")

    stop_conditions = [
        "No actionable frontier remains.",
        "No blocked frontier remains waiting for external unlock.",
        "No high-value uncovered behavior remains.",
        "No revalidation queue remains.",
    ]
    stop_conditions_met: list[str] = []
    if next_state == "completed":
        stop_conditions_met.append("No actionable frontier remains.")
    if int(status_counts.get("blocked") or 0) <= 0:
        stop_conditions_met.append("No blocked frontier remains waiting for external unlock.")
    if len(current_high_value_uncovered_keys) <= 0:
        stop_conditions_met.append("No high-value uncovered behavior remains.")
    if len(current_revalidation_queue_keys) <= 0:
        stop_conditions_met.append("No revalidation queue remains.")

    return {
        "this_run": {
            "new_validated_bug_count": len(new_validated_keys),
            "pending_to_validated_conversion_count": pending_to_validated_count,
            "pending_to_validated_conversion_rate": _safe_rate(pending_to_validated_count, prior_pending_count),
            "validated_bug_yield": run_validated_yield,
            "probe_count": probe_count,
        },
        "campaign_totals": {
            "cumulative_validated_bug_count": cumulative_validated_bug_count,
            "current_pending_finding_count": int(status_counts.get("pending") or 0),
            "reporting_basis": "validated_bug",
            "strict_validated_bug_only": True,
        },
        "frontier_burn_down": {
            "open_frontier_before_run": len(prior_open_keys),
            "open_frontier_after_run": len(current_open_keys),
            "burned_down_frontier_count": burned_down_frontier_count,
            "net_open_frontier_delta": len(current_open_keys) - len(prior_open_keys),
            "burn_down_rate": _safe_rate(burned_down_frontier_count, len(prior_open_keys)),
        },
        "frontier_health": {
            "remaining_high_value_uncovered_behavior_count": len(current_high_value_uncovered_keys),
            "revalidation_queue_size": len(current_revalidation_queue_keys),
            "blocked_frontier_count": int(status_counts.get("blocked") or 0),
        },
        "strict_reporting": {
            "reporting_basis": "validated_bug",
            "formal_summary_uses_strict_validated_bug_only": True,
            "excluded_from_formal_reporting": ["candidate", "pending", "blocked", "revalidate_due"],
        },
        "stop_decision": {
            "can_stop_now": next_state == "completed",
            "marginal_validated_yield_threshold": MARGINAL_VALIDATED_YIELD_THRESHOLD,
            "current_run_validated_yield": run_validated_yield,
            "threshold_reached": run_validated_yield <= MARGINAL_VALIDATED_YIELD_THRESHOLD,
            "stop_conditions": stop_conditions,
            "stop_conditions_met": stop_conditions_met,
            "remaining_risks": remaining_risks,
        },
    }


def _allocate_budget_counts(
    *,
    total_budget: int,
    ratios: dict[str, float],
    available_counts: dict[str, int],
    exploit_pending_reserve_slots: int,
    pending_exploit_count: int,
) -> dict[str, int]:
    classes = ("explore", "exploit", "revalidate")
    counts = {bucket: 0 for bucket in classes}
    available_budget = min(total_budget, sum(int(available_counts.get(bucket) or 0) for bucket in classes))
    remaining = available_budget
    for bucket in ("exploit", "revalidate", "explore"):
        if remaining <= 0:
            break
        if int(available_counts.get(bucket) or 0) <= 0:
            continue
        counts[bucket] += 1
        remaining -= 1
    while remaining > 0:
        candidates = [
            bucket
            for bucket in classes
            if counts[bucket] < int(available_counts.get(bucket) or 0)
        ]
        if not candidates:
            break
        target = max(
            candidates,
            key=lambda bucket: (
                float(ratios.get(bucket) or 0.0) - (counts[bucket] / max(1, available_budget)),
                float(ratios.get(bucket) or 0.0),
                1 if bucket == "exploit" else 0,
            ),
        )
        counts[target] += 1
        remaining -= 1
    reserve = min(exploit_pending_reserve_slots, pending_exploit_count, int(available_counts.get("exploit") or 0), available_budget)
    while counts["exploit"] < reserve:
        donor_candidates = [
            bucket
            for bucket in ("explore", "revalidate")
            if counts[bucket] > 0
        ]
        if not donor_candidates:
            break
        donor = max(
            donor_candidates,
            key=lambda bucket: (
                counts[bucket] - float(ratios.get(bucket) or 0.0) * max(1, available_budget),
                counts[bucket],
            ),
        )
        counts[donor] -= 1
        counts["exploit"] += 1
    return counts


def _pick_frontier_slice(entries: list[dict[str, Any]], limit: int, *, pending_first: bool = False) -> list[dict[str, Any]]:
    ranked = sorted(
        entries,
        key=lambda item: (
            0 if pending_first and str(item.get("last_status") or "") == "pending" else 1,
            -float(item.get("schedule_score") or 0.0),
            -float(item.get("business_value_score") or 0.0),
            str((_as_dict(item.get("frontier"))).get("title") or ""),
        ),
    )
    return ranked[:limit]


def _automation_plan(
    next_state: str,
    selected_frontier: list[dict[str, Any]],
    blocked_watchlist: list[dict[str, Any]],
    trigger: str,
) -> dict[str, Any]:
    if next_state == "scheduled" and selected_frontier:
        return {
            "status": "scheduled",
            "continuous_operation": True,
            "next_trigger": "scheduled_round",
            "activation_reason": "actionable_frontier_available",
            "recommended_delay_seconds": 0,
            "selected_frontier_count": len(selected_frontier),
        }
    if next_state == "blocked":
        wake_conditions = sorted(
            {
                condition
                for item in blocked_watchlist
                for condition in (item.get("wake_conditions") or [])
            }
        )
        return {
            "status": "waiting_for_trigger",
            "continuous_operation": True,
            "next_trigger": "external_recheck_trigger",
            "activation_reason": "all_remaining_frontier_blocked",
            "blocked_frontier_count": len(blocked_watchlist),
            "wake_conditions": wake_conditions,
            "last_seen_trigger": trigger,
        }
    return {
        "status": "idle",
        "continuous_operation": True,
        "next_trigger": "revalidation_trigger",
        "activation_reason": "coverage_debt_closed_for_now",
        "last_seen_trigger": trigger,
    }


def _frontier_title(dimensions: dict[str, str]) -> str:
    risk = dimensions.get("risk_type") or "unknown"
    return f"{dimensions.get('method') or 'GET'} {dimensions.get('path') or '/'} :: {risk}"


def _default_state(project_id: str) -> dict[str, Any]:
    created_at = _now()
    campaign_id = f"CMP_{_hash([project_id, created_at], 16)}"
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "campaign": {
            "campaign_id": campaign_id,
            "state": "scheduled",
            "created_at": created_at,
            "updated_at": created_at,
            "last_transition_at": created_at,
            "transition_history": [],
            "run_count": 0,
            "latest_run_id": "",
        },
        "coverage_ledger": {},
        "run_history": [],
    }


class ContinuousDiscoveryCampaign:
    def __init__(self, project_id: str = "real_project_demo", root: Path | str | None = None):
        self.project_id = _safe_project_id(project_id)
        self.root = Path(root or ROOT)
        self.path = self.root / "platform_workspace" / self.project_id / "defect_discovery" / "continuous_discovery_campaign.json"
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("coverage_ledger"), dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return _default_state(self.project_id)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _build_next_round_plan(
        self,
        entries: list[dict[str, Any]],
        *,
        run_context: dict[str, Any],
        trigger: str,
    ) -> dict[str, Any]:
        budget_config = _normalized_budget_config(run_context)
        frontier_pool = [dict(entry) for entry in entries if _entry_is_actionable(entry)]
        for entry in frontier_pool:
            entry["budget_class"] = _frontier_budget_class(entry)
            entry["schedule_score"] = _frontier_schedule_score(entry)
            entry["validated_yield_score"] = _validated_yield_score(entry)
            entry["coverage_gap_score"] = _coverage_gap_score(entry)
            entry["closure_score"] = _closure_score(entry)
            entry["blocker_relief_score"] = _blocker_relief_score(entry)
        grouped = {
            bucket: [entry for entry in frontier_pool if entry.get("budget_class") == bucket]
            for bucket in ("explore", "exploit", "revalidate")
        }
        pending_exploit_count = sum(
            1
            for entry in grouped["exploit"]
            if str(entry.get("last_status") or "") == "pending"
        )
        budget_slice_counts = _allocate_budget_counts(
            total_budget=int(budget_config["frontier_budget"]),
            ratios=_as_dict(budget_config.get("ratios")),
            available_counts={bucket: len(grouped[bucket]) for bucket in grouped},
            exploit_pending_reserve_slots=int(budget_config["exploit_pending_reserve_slots"]),
            pending_exploit_count=pending_exploit_count,
        )
        selected_entries: list[dict[str, Any]] = []
        for bucket in ("exploit", "revalidate", "explore"):
            slice_entries = _pick_frontier_slice(
                grouped[bucket],
                int(budget_slice_counts.get(bucket) or 0),
                pending_first=(bucket == "exploit"),
            )
            for entry in slice_entries:
                entry["selected_this_round"] = True
                selected_entries.append(entry)
        selected_entries = sorted(
            selected_entries,
            key=lambda item: (
                {"exploit": 0, "revalidate": 1, "explore": 2}.get(str(item.get("budget_class") or ""), 9),
                -float(item.get("schedule_score") or 0.0),
                -float(item.get("business_value_score") or 0.0),
                str((_as_dict(item.get("frontier"))).get("title") or ""),
            ),
        )
        recommended_frontier = [
            {
                "behavior_key": entry["behavior_key"],
                "frontier_id": _as_dict(entry.get("frontier")).get("frontier_id"),
                "title": _as_dict(entry.get("frontier")).get("title"),
                "status": entry.get("last_status"),
                "budget_class": entry.get("budget_class"),
                "schedule_score": entry.get("schedule_score"),
                "business_value_score": entry.get("business_value_score"),
                "validated_yield_score": entry.get("validated_yield_score"),
                "coverage_gap_score": entry.get("coverage_gap_score"),
                "closure_score": entry.get("closure_score"),
                "blocker_reason": entry.get("last_blocker_reason"),
                "evidence_maturity": entry.get("evidence_maturity"),
                "next_action": entry.get("next_action"),
                "why_selected": self._why_selected(entry),
            }
            for entry in selected_entries
        ]
        blocked_watchlist = [
            {
                "behavior_key": entry.get("behavior_key"),
                "frontier_id": _as_dict(entry.get("frontier")).get("frontier_id"),
                "title": _as_dict(entry.get("frontier")).get("title"),
                "pause_reason": entry.get("last_blocker_reason") or "unknown_blocker",
                "wake_conditions": _as_list(_as_dict(entry.get("frontier")).get("wake_conditions")),
                "rounds_blocked": int((_as_dict(entry.get("status_counts"))).get("blocked") or 0),
                "next_action": entry.get("next_action"),
            }
            for entry in sorted(
                entries,
                key=lambda item: (
                    -int((_as_dict(item.get("status_counts"))).get("blocked") or 0),
                    -float(item.get("business_value_score") or 0.0),
                ),
            )
            if str(entry.get("last_status") or "") == "blocked"
        ]
        long_blocked_watchlist = [
            item for item in blocked_watchlist
            if int(item.get("rounds_blocked") or 0) >= LONG_BLOCKED_ROUND_THRESHOLD
        ]
        automation = _automation_plan(
            "scheduled" if recommended_frontier else ("blocked" if blocked_watchlist else "completed"),
            recommended_frontier,
            blocked_watchlist,
            trigger,
        )
        why_this_round: list[str] = []
        if int(budget_slice_counts.get("exploit") or 0) > 0:
            why_this_round.append("Exploit budget holds space for high-value pending frontier instead of re-scanning low-yield candidates.")
        if int(budget_slice_counts.get("revalidate") or 0) > 0:
            why_this_round.append("Revalidate budget rechecks previously validated or blocker-cleared frontier after triggers.")
        if int(budget_slice_counts.get("explore") or 0) > 0:
            why_this_round.append("Explore budget keeps opening uncovered behavior units and risk families.")
        if pending_exploit_count > 0:
            why_this_round.append(
                f"Stable exploit reserve protects {min(pending_exploit_count, int(budget_config['exploit_pending_reserve_slots']))} pending frontier slots."
            )
        return {
            "strategy": "frontier_driven_incremental_scheduler",
            "budget_config": budget_config,
            "frontier_pool_counts": {bucket: len(grouped[bucket]) for bucket in grouped},
            "budget_slice_counts": budget_slice_counts,
            "selected_frontier": recommended_frontier,
            "selection_summary": {
                "why_this_round": why_this_round,
                "selected_frontier_count": len(recommended_frontier),
                "long_blocked_frontier_count": len(long_blocked_watchlist),
                "trigger": trigger,
            },
            "blocked_frontier_watchlist": blocked_watchlist[:12],
            "long_blocked_frontier_watchlist": long_blocked_watchlist[:12],
            "automation": automation,
        }

    def record_run(
        self,
        probes: Iterable[dict[str, Any]],
        issues: Iterable[dict[str, Any]],
        *,
        trigger: str = "scheduled_round",
        run_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_context = _as_dict(run_context)
        campaign = self._state["campaign"]
        previous_state = str(campaign.get("state") or "scheduled")
        prior_snapshot = _ledger_snapshot(_as_dict(self._state.get("coverage_ledger")))
        run_index = int(campaign.get("run_count") or 0) + 1
        run_started_at = _now()
        run_id = f"RUN_{_hash([self.project_id, run_index, run_started_at, trigger], 18)}"

        merged: dict[str, dict[str, Any]] = {}
        for probe in probes:
            if not isinstance(probe, dict):
                continue
            key, dimensions = _behavior_key(probe)
            merged.setdefault(key, {"key": key, "dimensions": dimensions, "probe": {}, "issue": {}})
            merged[key]["probe"] = probe
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            key, dimensions = _behavior_key(issue)
            merged.setdefault(key, {"key": key, "dimensions": dimensions, "probe": {}, "issue": {}})
            merged[key]["issue"] = issue
            merged[key]["dimensions"] = dimensions

        seen_keys: set[str] = set()
        for key, payload in merged.items():
            probe = _as_dict(payload.get("probe"))
            issue = _as_dict(payload.get("issue"))
            dimensions = payload["dimensions"]
            prior = _as_dict(self._state["coverage_ledger"].get(key))
            status = _status_from_probe_issue(probe, issue)
            blocker_reason = _issue_blocker_reason(issue) or _probe_blocker_reason(probe)
            evidence_maturity = _evidence_maturity(issue)
            value_score = _value_score(probe, issue)
            next_action = _next_action(status, blocker_reason, evidence_maturity)
            recent_blocker_clearance = bool(
                str(prior.get("last_status") or "") == "blocked" and status != "blocked"
            )
            entry = {
                "behavior_key": key,
                "semantic_dimensions": dimensions,
                "frontier": {
                    "frontier_id": f"FRT_{key[:12]}",
                    "title": _frontier_title(dimensions),
                    "status": status,
                    "business_value_score": value_score,
                    "blocker_reason": blocker_reason,
                    "last_run_result": _last_run_result(status, probe, issue),
                    "evidence_maturity": evidence_maturity,
                    "next_action": next_action,
                    "recommended": status in ACTIONABLE_STATUSES,
                    "wake_conditions": _wake_conditions_for_blocker(blocker_reason) if blocker_reason else [],
                    "recheck_ready": False,
                },
                "attempt_count": int(prior.get("attempt_count") or 0) + 1,
                "rounds_seen": int(prior.get("rounds_seen") or 0) + 1,
                "status_counts": dict(prior.get("status_counts") or {}),
                "last_status": status,
                "last_seen_at": run_started_at,
                "last_seen_run_id": run_id,
                "last_seen_run_index": run_index,
                "last_blocker_reason": blocker_reason,
                "last_run_result": _last_run_result(status, probe, issue),
                "evidence_maturity": evidence_maturity,
                "next_action": next_action,
                "business_value_score": value_score,
                "recent_blocker_clearance": recent_blocker_clearance,
            }
            entry["status_counts"][status] = int(entry["status_counts"].get(status) or 0) + 1
            self._state["coverage_ledger"][key] = entry
            seen_keys.add(key)

        if trigger in REVALIDATION_TRIGGERS:
            for key, raw_entry in list(self._state["coverage_ledger"].items()):
                if key in seen_keys:
                    continue
                entry = _as_dict(raw_entry)
                frontier = _as_dict(entry.get("frontier"))
                if str(entry.get("last_status") or "") == "validated":
                    entry["last_status"] = "revalidate_due"
                    entry["next_action"] = _next_action("revalidate_due", "", entry.get("evidence_maturity") or "strict_validated")
                    frontier.update(
                        {
                            "status": "revalidate_due",
                            "last_run_result": "triggered_revalidation",
                            "next_action": entry["next_action"],
                            "recommended": True,
                            "recheck_ready": False,
                        }
                    )
                elif str(entry.get("last_status") or "") == "blocked":
                    wake_conditions = _wake_conditions_for_blocker(str(entry.get("last_blocker_reason") or ""))
                    if trigger not in wake_conditions:
                        continue
                    entry["next_action"] = (
                        f"Recheck blocked frontier after {_trigger_label(trigger)}: "
                        f"{entry.get('last_blocker_reason') or 'unknown_blocker'}"
                    )
                    frontier.update(
                        {
                            "last_run_result": "triggered_blocker_recheck",
                            "next_action": entry["next_action"],
                            "recommended": True,
                            "recheck_ready": True,
                            "wake_conditions": wake_conditions,
                        }
                    )
                else:
                    continue
                entry["frontier"] = frontier
                self._state["coverage_ledger"][key] = entry

        entries = list(self._state["coverage_ledger"].values())
        next_run_plan = self._build_next_round_plan(entries, run_context=run_context, trigger=trigger)
        status_counts: dict[str, int] = {}
        for entry in entries:
            status = str(entry.get("last_status") or "untouched")
            status_counts[status] = status_counts.get(status, 0) + 1

        recommended_frontier = _as_list(next_run_plan.get("selected_frontier"))

        actionable_count = sum(1 for entry in entries if _entry_is_actionable(entry))
        blocked_count = int(status_counts.get("blocked") or 0)
        if actionable_count > 0:
            next_state = "scheduled"
        elif blocked_count > 0:
            next_state = "blocked"
        else:
            next_state = "completed"

        dashboard = _build_campaign_dashboard(
            prior_snapshot=prior_snapshot,
            entries=entries,
            status_counts=status_counts,
            next_state=next_state,
            probe_count=len([item for item in probes if isinstance(item, dict)]),
        )
        campaign["state"] = next_state
        campaign["updated_at"] = _now()
        campaign["run_count"] = run_index
        campaign["latest_run_id"] = run_id
        if previous_state != next_state:
            transition = {
                "from": previous_state,
                "to": next_state,
                "at": campaign["updated_at"],
                "reason": self._transition_reason(next_state, actionable_count, blocked_count),
                "run_id": run_id,
            }
            _as_list(campaign.get("transition_history")).append(transition)
            campaign["transition_history"] = _as_list(campaign.get("transition_history"))[-20:]
            campaign["last_transition_at"] = transition["at"]

        continue_conditions = self._continue_conditions(status_counts)
        current_run = {
            "run_id": run_id,
            "run_index": run_index,
            "started_at": run_started_at,
            "completed_at": campaign["updated_at"],
            "trigger": trigger,
            "state_path": ["scheduled", "active", next_state],
            "issue_count": len([item for item in issues if isinstance(item, dict)]),
            "probe_count": len([item for item in probes if isinstance(item, dict)]),
            "recommended_frontier_count": len(recommended_frontier),
            "strict_reporting_basis": "validated_bug",
            "continue_conditions": continue_conditions,
            "continue_campaign": bool(next_state in {"scheduled", "blocked"}),
            "next_run_plan": next_run_plan,
            "context": run_context,
        }
        self._state["run_history"] = [*(_as_list(self._state.get("run_history"))), current_run][-RUN_HISTORY_LIMIT:]
        self._save()
        return {
            "status": "ready",
            "campaign": {
                **campaign,
                "semantic_model": CAMPAIGN_OBJECT_MODEL,
                "state_machine": CAMPAIGN_STATE_MACHINE,
            },
            "summary": {
                "campaign_id": campaign["campaign_id"],
                "campaign_state": next_state,
                "run_count": run_index,
                "coverage_ledger_entry_count": len(entries),
                "status_counts": status_counts,
                "remaining_actionable_frontier_count": actionable_count,
                "blocked_frontier_count": blocked_count,
                "validated_frontier_count": int(status_counts.get("validated") or 0),
                "revalidate_due_count": int(status_counts.get("revalidate_due") or 0),
                "recommended_frontier_count": len(recommended_frontier),
                "continue_campaign": bool(next_state in {"scheduled", "blocked"}),
                "budget_slice_counts": _as_dict(next_run_plan.get("budget_slice_counts")),
                "auto_schedule_status": str((_as_dict(next_run_plan.get("automation"))).get("status") or "idle"),
                "reporting_basis": "validated_bug",
                "this_run_new_validated_bug_count": int((_as_dict(dashboard.get("this_run"))).get("new_validated_bug_count") or 0),
                "cumulative_validated_bug_count": int((_as_dict(dashboard.get("campaign_totals"))).get("cumulative_validated_bug_count") or 0),
                "pending_to_validated_conversion_count": int((_as_dict(dashboard.get("this_run"))).get("pending_to_validated_conversion_count") or 0),
                "pending_to_validated_conversion_rate": float((_as_dict(dashboard.get("this_run"))).get("pending_to_validated_conversion_rate") or 0.0),
                "frontier_burn_down_count": int((_as_dict(dashboard.get("frontier_burn_down"))).get("burned_down_frontier_count") or 0),
                "frontier_burn_down_rate": float((_as_dict(dashboard.get("frontier_burn_down"))).get("burn_down_rate") or 0.0),
                "remaining_high_value_uncovered_behavior_count": int((_as_dict(dashboard.get("frontier_health"))).get("remaining_high_value_uncovered_behavior_count") or 0),
                "revalidation_queue_size": int((_as_dict(dashboard.get("frontier_health"))).get("revalidation_queue_size") or 0),
                "marginal_validated_yield_threshold": float((_as_dict(dashboard.get("stop_decision"))).get("marginal_validated_yield_threshold") or 0.0),
                "current_run_validated_yield": float((_as_dict(dashboard.get("stop_decision"))).get("current_run_validated_yield") or 0.0),
                "can_stop_now": bool((_as_dict(dashboard.get("stop_decision"))).get("can_stop_now")),
            },
            "current_run": current_run,
            "dashboard": dashboard,
            "recommended_frontier": recommended_frontier,
            "next_run_plan": next_run_plan,
            "automation": _as_dict(next_run_plan.get("automation")),
            "coverage_ledger": {
                "path": str(self.path),
                "entries": sorted(
                    entries,
                    key=lambda item: (
                        STATUS_PRIORITY.get(str(item.get("last_status") or "untouched"), 99),
                        -float(item.get("business_value_score") or 0.0),
                        str((_as_dict(item.get("frontier"))).get("title") or ""),
                    ),
                ),
                "status_counts": status_counts,
            },
        }

    @staticmethod
    def _why_selected(entry: dict[str, Any]) -> list[str]:
        status = str(entry.get("last_status") or "untouched")
        budget_class = str(entry.get("budget_class") or _frontier_budget_class(entry))
        reasons: list[str] = []
        if status == "pending":
            reasons.append("Evidence gap is narrow enough to pursue strict validation.")
        elif status == "revalidate_due":
            reasons.append("A trigger reopened previously validated or stale frontier.")
        elif status == "candidate":
            reasons.append("Candidate behavior still needs deterministic execution and verifier closure.")
        elif status == "untouched":
            reasons.append("High-value behavior unit has not been advanced yet.")
        elif status == "blocked" and _as_dict(entry.get("frontier")).get("recheck_ready"):
            reasons.append("A matching trigger reopened a previously blocked frontier for recheck.")
        if entry.get("business_value_score"):
            reasons.append(f"Business value score={entry.get('business_value_score')}.")
        if entry.get("schedule_score"):
            reasons.append(f"Schedule score={entry.get('schedule_score')}.")
        reasons.append(f"Budget slice={budget_class}.")
        blocker_reason = str(entry.get("last_blocker_reason") or "")
        if blocker_reason and status == "blocked":
            reasons.append(f"Still blocked by {blocker_reason}.")
        return reasons

    @staticmethod
    def _continue_conditions(status_counts: dict[str, int]) -> list[str]:
        conditions: list[str] = []
        if int(status_counts.get("pending") or 0) > 0:
            conditions.append("Pending frontier still has verifier, repro or evidence gaps.")
        if int(status_counts.get("candidate") or 0) > 0:
            conditions.append("Candidate frontier still needs first grounded execution and strict verification.")
        if int(status_counts.get("untouched") or 0) > 0:
            conditions.append("Known behavior units remain untouched by the campaign.")
        if int(status_counts.get("revalidate_due") or 0) > 0:
            conditions.append("Revalidation debt exists after an environment, knowledge or data trigger.")
        if not conditions and int(status_counts.get("blocked") or 0) > 0:
            conditions.append("Only blocked frontier remains and needs an external unlock.")
        return conditions

    @staticmethod
    def _transition_reason(next_state: str, actionable_count: int, blocked_count: int) -> str:
        if next_state == "scheduled" and actionable_count > 0:
            return "campaign_has_more_actionable_frontier"
        if next_state == "blocked" and blocked_count > 0:
            return "all_remaining_frontier_blocked"
        return "coverage_debt_closed_for_now"


def record_continuous_discovery_campaign_run(
    project_id: str,
    root: Path | str | None,
    probes: Iterable[dict[str, Any]],
    issues: Iterable[dict[str, Any]],
    *,
    trigger: str = "scheduled_round",
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ContinuousDiscoveryCampaign(project_id, root).record_run(
        probes,
        issues,
        trigger=trigger,
        run_context=run_context,
    )
