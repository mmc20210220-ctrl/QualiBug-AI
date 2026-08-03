"""Execution budget planning, drift guardrails, feedback summarization."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403




def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _read_budget_setting(key: str, default: Any) -> Any:
    """Read budget settings from policy first, then allow env overrides."""
    from ai_test_asset_center.policy_wiring import get_policy_value

    policy_value = get_policy_value("execution", key, default)
    env_key = f"QUALIBUG_{key.upper()}"
    raw_env = os.environ.get(env_key)
    if raw_env is None or raw_env == "":
        return policy_value
    if isinstance(default, bool):
        return str(raw_env).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return _safe_int(raw_env, int(policy_value))
    if isinstance(default, float):
        return _safe_float(raw_env, float(policy_value))
    return raw_env


def _verification_step_count(vm: dict[str, Any]) -> int:
    if not isinstance(vm, dict):
        return 0
    return sum(1 for key in ("path", "step1", "step2", "step3", "step4", "step5") if str(vm.get(key, "") or "").strip())


def _hypothesis_source_count(hypothesis: dict[str, Any]) -> int:
    merged_sources = hypothesis.get("_merged_sources", [])
    return len(merged_sources) if isinstance(merged_sources, list) and merged_sources else 1


def _is_write_hypothesis(hypothesis: dict[str, Any]) -> bool:
    vm = hypothesis.get("verification_method", {})
    if not isinstance(vm, dict):
        return False
    method = str(vm.get("method", "") or "").upper()
    if not method:
        for key in ("path", "step1", "step2", "step3"):
            value = str(vm.get(key, "") or "").upper()
            if value:
                method = value.split(None, 1)[0]
                break
    return method in {"POST", "PUT", "PATCH", "DELETE"}


def _classify_budget_tier(hypothesis: dict[str, Any]) -> str:
    """Assign a conservative execution tier using source strength and probe quality."""
    severity = str(hypothesis.get("severity", "") or "").upper()
    vm = hypothesis.get("verification_method", {})
    step_count = _verification_step_count(vm)
    source_count = _hypothesis_source_count(hypothesis)

    if source_count >= 2 and step_count >= 2:
        return "A"
    if severity in {"P0", "P1"} and step_count >= 1:
        return "A"
    if step_count >= 1 or severity in {"P0", "P1", "P2"}:
        return "B"
    return "C"


def _get_execution_budget_settings() -> dict[str, Any]:
    """Resolve dynamic budget settings with caps used only as safety rails."""
    enabled = bool(_read_budget_setting("execution_budget_enabled", True))
    tier_a_max = max(0, _safe_int(_read_budget_setting("tier_a_max_hypotheses", 0), 0))
    tier_b_max = max(0, _safe_int(_read_budget_setting("tier_b_max_hypotheses", 0), 0))
    tier_c_max = max(0, _safe_int(_read_budget_setting("tier_c_max_hypotheses", 0), 0))
    # ── Enhanced: higher default max for broader coverage ──
    overall_max = max(1, _safe_int(_read_budget_setting("max_hypotheses_execute", 120), 120))

    return {
        "enabled": enabled,
        "tier_a_max_hypotheses": tier_a_max,
        "tier_b_max_hypotheses": tier_b_max,
        "tier_c_max_hypotheses": tier_c_max,
        "tier_a_async_delay_seconds": max(0.0, _safe_float(_read_budget_setting("tier_a_async_delay_seconds", 3.0), 3.0)),
        "tier_b_async_delay_seconds": max(0.0, _safe_float(_read_budget_setting("tier_b_async_delay_seconds", 0.5), 0.5)),
        "tier_c_async_delay_seconds": max(0.0, _safe_float(_read_budget_setting("tier_c_async_delay_seconds", 0.0), 0.0)),
        "tier_b_trim_steps_to": max(1, _safe_int(_read_budget_setting("tier_b_trim_steps_to", 3), 3)),
        "tier_c_trim_steps_to": max(1, _safe_int(_read_budget_setting("tier_c_trim_steps_to", 1), 1)),
        "overall_max_hypotheses": overall_max,
    }


def _apply_drift_guardrails(budget_summary: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(budget_summary or {})
    quotas = dict(adjusted.get("quotas", {}) or {"A": 0, "B": 0, "C": 0})
    drift_status = str(settings.get("drift_unlock_status", "not_required") or "not_required")
    effective_unlock = str(settings.get("drift_effective_unlock_level", "normal") or "normal")
    drift_severity = str(settings.get("drift_severity", "none") or "none")

    ratio_multiplier = 1.0
    tier_a_multiplier = 1.0
    if drift_status in {"unapproved", "expired"}:
        if drift_severity == "high":
            ratio_multiplier = 0.45
            tier_a_multiplier = 0.55
        elif drift_severity == "medium":
            ratio_multiplier = 0.65
            tier_a_multiplier = 0.75
    elif effective_unlock == "limited":
        if drift_severity == "high":
            ratio_multiplier = 0.75
            tier_a_multiplier = 0.85
        elif drift_severity == "medium":
            ratio_multiplier = 0.88
            tier_a_multiplier = 0.92

    target_execute = int(adjusted.get("target_execute", 0) or 0)
    if ratio_multiplier < 1.0 and target_execute > 0:
        minimum_execute = 1 if target_execute > 0 else 0
        adjusted_target = max(minimum_execute, int(round(target_execute * ratio_multiplier)))
        adjusted["target_execute"] = min(target_execute, adjusted_target)
        current_a = int(quotas.get("A", 0) or 0)
        adjusted_a = min(adjusted["target_execute"], max(0, int(round(current_a * tier_a_multiplier))))
        if adjusted["target_execute"] > 0 and current_a > 0 and adjusted_a <= 0:
            adjusted_a = 1
        adjusted_b = max(0, adjusted["target_execute"] - adjusted_a)
        quotas["A"] = adjusted_a
        quotas["B"] = adjusted_b
        quotas["C"] = 0
        adjusted["execution_ratio"] = float(adjusted.get("execution_ratio", 0.0) or 0.0) * ratio_multiplier
        adjusted["tier_a_ratio"] = float(adjusted.get("tier_a_ratio", 0.0) or 0.0) * tier_a_multiplier

    adjusted["quotas"] = quotas
    adjusted["drift_guard"] = {
        "status": drift_status,
        "effective_unlock_level": effective_unlock,
        "severity": drift_severity,
        "ratio_multiplier": ratio_multiplier,
        "tier_a_multiplier": tier_a_multiplier,
    }
    return adjusted


def _summarize_execution_feedback(findings: list[Any] | None) -> dict[str, Any]:
    findings = findings or []
    reviewed_count = len(findings)
    confirmed_count = 0
    falsified_count = 0
    by_tier: dict[str, dict[str, Any]] = {}
    # ── Enhanced: track consecutive hits and high-risk ratio for momentum ──
    consecutive_hits = 0
    max_consecutive_hits = 0
    high_risk_count = 0
    _HIGH_RISK_TYPES = {"permission_boundary", "data_conservation", "state_machine", "authorization", "isolation"}
    for finding in findings:
        verdict = str(getattr(finding, "verdict", "") or (finding.get("verdict", "") if isinstance(finding, dict) else "")).lower()
        evidence = getattr(finding, "evidence", None)
        if evidence is None and isinstance(finding, dict):
            evidence = finding.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        budget_info = evidence.get("execution_budget", {})
        tier = str(budget_info.get("tier", "") or "").upper()
        if tier:
            tier_bucket = by_tier.setdefault(
                tier,
                {"reviewed_count": 0, "confirmed_count": 0, "falsified_count": 0, "hit_rate": 0.0},
            )
            tier_bucket["reviewed_count"] += 1
        # Track risk type for high-risk ratio
        risk_type = str(evidence.get("risk_type", "") or getattr(finding, "risk_type", "") or "").lower()
        if risk_type in _HIGH_RISK_TYPES:
            high_risk_count += 1
        if verdict in {"confirmed", "validated_candidate"}:
            confirmed_count += 1
            consecutive_hits += 1
            max_consecutive_hits = max(max_consecutive_hits, consecutive_hits)
            if tier:
                by_tier[tier]["confirmed_count"] += 1
        elif verdict in {"falsified", "rejected"}:
            falsified_count += 1
            consecutive_hits = 0  # reset on falsified
            if tier:
                by_tier[tier]["falsified_count"] += 1
        else:
            consecutive_hits = 0  # reset on inconclusive
    for tier_bucket in by_tier.values():
        reviewed = int(tier_bucket.get("reviewed_count", 0) or 0)
        tier_bucket["hit_rate"] = (int(tier_bucket.get("confirmed_count", 0) or 0) / reviewed) if reviewed else 0.0
    hit_rate = (confirmed_count / reviewed_count) if reviewed_count else 0.0
    high_risk_ratio = (high_risk_count / reviewed_count) if reviewed_count else 0.0
    return {
        "reviewed_count": reviewed_count,
        "confirmed_count": confirmed_count,
        "falsified_count": falsified_count,
        "hit_rate": hit_rate,
        "by_tier": by_tier,
        # ── Enhanced: momentum and risk metrics ──
        "consecutive_hits": max_consecutive_hits,
        "high_risk_ratio": high_risk_ratio,
    }


def _derive_execution_budget_targets(
    hypotheses: list[dict[str, Any]],
    settings: dict[str, Any],
    feedback_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive adaptive tier quotas from the current hypothesis pool."""
    total = len(hypotheses)
    if total <= 0:
        return {
            "total_hypotheses": 0,
            "candidate_pool": 0,
            "executable_count": 0,
            "dual_source_count": 0,
            "critical_count": 0,
            "write_count": 0,
            "hit_rate": 0.0,
            "execution_ratio": 0.0,
            "tier_a_ratio": 0.0,
            "quotas": {"A": 0, "B": 0, "C": 0},
        }

    executable_count = 0
    dual_source_count = 0
    critical_count = 0
    write_count = 0
    for hypothesis in hypotheses:
        step_count = _verification_step_count(hypothesis.get("verification_method", {}))
        if step_count >= 1:
            executable_count += 1
        if _hypothesis_source_count(hypothesis) >= 2:
            dual_source_count += 1
        if str(hypothesis.get("severity", "") or "").upper() in {"P0", "P1"}:
            critical_count += 1
        if _is_write_hypothesis(hypothesis):
            write_count += 1

    candidate_pool = executable_count or total
    cross_source_ratio = dual_source_count / max(total, 1)
    critical_ratio = critical_count / max(total, 1)
    write_ratio = write_count / max(executable_count, 1)
    route_surface_size = max(0, _safe_int(settings.get("route_surface_size", 0), 0))
    hit_rate = float((feedback_summary or {}).get("hit_rate", 0.0) or 0.0)
    tier_feedback = (feedback_summary or {}).get("by_tier", {}) if isinstance(feedback_summary, dict) else {}
    tier_a_hit_rate = float((tier_feedback.get("A", {}) or {}).get("hit_rate", 0.0) or 0.0)
    tier_b_hit_rate = float((tier_feedback.get("B", {}) or {}).get("hit_rate", 0.0) or 0.0)

    # ── Enhanced: higher base ratio for broader coverage ──
    execution_ratio = 0.55
    execution_ratio += min(0.25, cross_source_ratio * 0.50)
    execution_ratio += min(0.18, critical_ratio * 0.40)
    execution_ratio -= min(0.08, write_ratio * 0.10)
    execution_ratio += max(-0.06, min(0.10, (hit_rate - 0.10) * 0.40))
    execution_ratio += max(-0.05, min(0.08, (tier_a_hit_rate - tier_b_hit_rate) * 0.25))
    # ── Discovery momentum: expand budget on consecutive hits ──
    consecutive_hits = int((feedback_summary or {}).get("consecutive_hits", 0) or 0)
    if consecutive_hits >= 3:
        execution_ratio += min(0.15, consecutive_hits * 0.03)
    # ── Risk-type budget boost: high-risk types get more coverage ──
    high_risk_ratio = float((feedback_summary or {}).get("high_risk_ratio", 0.0) or 0.0)
    execution_ratio += min(0.10, high_risk_ratio * 0.20)
    if route_surface_size > 0:
        hypothesis_density = total / max(route_surface_size, 1)
        if hypothesis_density > 0.60:
            execution_ratio += 0.10
        elif hypothesis_density < 0.15:
            execution_ratio -= 0.04
    execution_ratio = min(0.95, max(0.40, execution_ratio))

    overall_max = max(1, int(settings.get("overall_max_hypotheses", 120)))
    # ── Enhanced: higher minimum execution for broader coverage ──
    minimum_execute = 3
    if candidate_pool >= 5 and (dual_source_count > 0 or critical_count > 1):
        minimum_execute = 5
    if candidate_pool >= 10 and consecutive_hits >= 2:
        minimum_execute = max(minimum_execute, 8)
    target_execute = min(overall_max, total, max(minimum_execute, int(round(candidate_pool * execution_ratio))))

    # ── Enhanced: higher tier-A ratio for critical hypotheses ──
    tier_a_ratio = 0.20
    tier_a_ratio += min(0.40, cross_source_ratio * 0.70)
    tier_a_ratio += min(0.20, critical_ratio * 0.40)
    tier_a_ratio += max(0.0, min(0.12, (hit_rate - 0.15) * 0.30))
    tier_a_ratio += max(-0.10, min(0.15, (tier_a_hit_rate - tier_b_hit_rate) * 0.35))
    # Momentum boost for tier-A
    if consecutive_hits >= 3:
        tier_a_ratio += min(0.10, consecutive_hits * 0.02)
    tier_a_ratio = min(0.80, max(0.18, tier_a_ratio))

    target_a = min(target_execute, int(round(target_execute * tier_a_ratio)))
    target_a = max(target_a, min(target_execute, dual_source_count))
    if critical_count and target_execute > target_a:
        target_a = max(target_a, min(target_execute, max(1, (critical_count + 1) // 2)))

    target_b = max(0, target_execute - target_a)
    target_c = 0

    a_cap = max(0, int(settings.get("tier_a_max_hypotheses", 0) or 0))
    b_cap = max(0, int(settings.get("tier_b_max_hypotheses", 0) or 0))
    c_cap = max(0, int(settings.get("tier_c_max_hypotheses", 0) or 0))
    if a_cap > 0:
        target_a = min(target_a, a_cap)
    if b_cap > 0:
        target_b = min(target_b, b_cap)

    capped_execute = target_a + target_b
    spill = max(0, min(target_execute, total) - capped_execute)
    if spill > 0 and b_cap <= 0:
        target_b += spill
        spill = 0
    if spill > 0 and c_cap > 0:
        target_c = min(c_cap, spill)
        spill -= target_c

    return {
        "total_hypotheses": total,
        "candidate_pool": candidate_pool,
        "executable_count": executable_count,
        "dual_source_count": dual_source_count,
        "critical_count": critical_count,
        "write_count": write_count,
        "hit_rate": hit_rate,
        "tier_a_hit_rate": tier_a_hit_rate,
        "tier_b_hit_rate": tier_b_hit_rate,
        "execution_ratio": execution_ratio,
        "tier_a_ratio": tier_a_ratio,
        "target_execute": target_execute,
        "quotas": {"A": target_a, "B": target_b, "C": target_c},
    }


def _plan_execution_budget(
    hypotheses: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
    feedback_summary: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn sorted hypotheses into an adaptive execution plan."""
    settings = settings or _get_execution_budget_settings()
    if not settings.get("enabled", True):
        summary = {
            "total_hypotheses": len(hypotheses),
            "candidate_pool": len(hypotheses),
            "executable_count": len(hypotheses),
            "dual_source_count": 0,
            "critical_count": 0,
            "write_count": 0,
            "hit_rate": float((feedback_summary or {}).get("hit_rate", 0.0) or 0.0),
            "execution_ratio": 1.0,
            "tier_a_ratio": 1.0,
            "target_execute": len(hypotheses),
            "quotas": {"A": len(hypotheses), "B": 0, "C": 0},
        }
        return ([{"hypothesis": h, "tier": "A", "budget_action": "full"} for h in hypotheses], summary)

    budget_summary = _derive_execution_budget_targets(hypotheses, settings, feedback_summary)
    budget_summary = _apply_drift_guardrails(budget_summary, settings)
    quotas = dict(budget_summary.get("quotas", {"A": 0, "B": 0, "C": 0}))
    plan: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        desired_tier = _classify_budget_tier(hypothesis)
        tier = desired_tier
        action = "full"

        if tier == "A" and quotas["A"] <= 0:
            tier = "B"
        if tier == "B" and quotas["B"] <= 0:
            tier = "C"
        if tier == "C":
            if quotas["C"] <= 0:
                plan.append({"hypothesis": hypothesis, "tier": "DEFER", "budget_action": "deferred"})
                continue
            action = "light"
        elif tier == "B":
            action = "light"

        quotas[tier] -= 1
        plan.append({"hypothesis": hypothesis, "tier": tier, "budget_action": action})
    return plan, budget_summary


def _apply_execution_budget_profile(vm: dict[str, Any], tier: str, settings: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """Trim expensive observer steps for lower tiers while keeping probe validity."""
    if not isinstance(vm, dict):
        vm = {}
    tier = str(tier or "A").upper()
    trimmed = dict(vm)
    if tier == "A":
        return trimmed, float(settings.get("tier_a_async_delay_seconds", 3.0))

    if tier == "B":
        max_steps = int(settings.get("tier_b_trim_steps_to", 3))
        async_delay = float(settings.get("tier_b_async_delay_seconds", 0.5))
    else:
        max_steps = int(settings.get("tier_c_trim_steps_to", 1))
        async_delay = float(settings.get("tier_c_async_delay_seconds", 0.0))

    step_items: list[tuple[int, Any]] = []
    for key, value in vm.items():
        if key.startswith("step"):
            try:
                step_items.append((int(key[4:]), value))
            except Exception:
                continue
    step_items.sort()

    light_vm: dict[str, Any] = {}
    if vm.get("method"):
        light_vm["method"] = vm.get("method")
    if vm.get("path"):
        light_vm["path"] = vm.get("path")

    for new_index, (_, value) in enumerate(step_items[:max_steps], start=1):
        light_vm[f"step{new_index}"] = value

    if vm.get("check"):
        light_vm["check"] = vm.get("check")

    return light_vm or trimmed, async_delay


