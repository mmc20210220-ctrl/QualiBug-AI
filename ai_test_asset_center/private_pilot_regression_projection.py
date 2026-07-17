"""Regression suite/run projection for the private pilot command center.

Extracted from ``private_pilot_service`` so the HTTP handler module stays thinner.
Symbols remain importable from ``private_pilot_service`` for compatibility.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _read_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _regression_lookup_keys(*values: Any) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        keys.add(text)
        keys.add(text.lower())
        keys.add(text.upper())
    return keys


def _regression_status_label(status: str) -> str:
    normalized = str(status or "").strip().lower()
    return {
        "passed": "回归通过",
        "failed": "回归失败",
        "needs_review": "待人工复核",
        "skipped": "回归已跳过",
        "pending": "待执行回归",
        "not_covered": "未纳入回归",
    }.get(normalized, normalized or "未知")


def _regression_lifecycle(status: str, included_in_suite: bool, consecutive_passes: int = 0) -> dict[str, str]:
    normalized = str(status or "").strip().lower()
    # Stable: 2+ consecutive regression passes verify the fix is solid.
    if normalized == "passed" and consecutive_passes >= 2:
        return {"code": "stable", "label": "回归稳定", "description": f"已连续 {consecutive_passes} 轮回归通过，缺陷确认修复稳定。"}
    if normalized == "passed":
        return {"code": "verified_fixed", "label": "回归通过", "description": "最新一次回归已验证该缺陷不再复现，建议再执行一轮确认稳定性。"}
    if normalized == "failed":
        return {"code": "regression_failed", "label": "回归失败", "description": "最新一次回归仍能触发该缺陷，需继续修复。"}
    if normalized == "needs_review":
        return {"code": "manual_review_required", "label": "待人工复核", "description": "已执行回归，但当前断言不足以自动判定，需要 QA 复核。"}
    if normalized == "skipped":
        return {"code": "regression_skipped", "label": "回归已跳过", "description": "该缺陷探针被安全策略跳过，尚未完成验证。"}
    if normalized == "pending":
        return {"code": "pending_regression", "label": "待回归", "description": "缺陷已纳入回归套件，等待执行验证。"}
    if included_in_suite:
        return {"code": "pending_regression", "label": "待回归", "description": "缺陷已纳入回归套件，等待执行验证。"}
    return {"code": "pending_fix", "label": "待纳入回归", "description": "该缺陷尚未纳入正式回归套件，暂不能验证修复结果。"}


def _load_regression_history(root: Path, project_id: str) -> list[dict[str, Any]]:
    history_candidates = [
        root / "platform_outputs" / project_id / "regression_run" / "regression_run_history.json",
        root / "platform_workspace" / project_id / "defect_discovery" / "regression_run_history.json",
    ]
    history = next((_read_json_safe(path, []) for path in history_candidates if path.exists()), [])
    return history if isinstance(history, list) else []


def _regression_summary_title(summary: dict[str, Any]) -> str:
    gate_status = str(summary.get("gate_status") or "").strip().lower()
    failed = int(summary.get("failed_defect_count") or 0)
    pending = int(summary.get("pending_defect_count") or 0)
    if gate_status == "failed":
        return f"最近一次回归发现 {failed} 个缺陷重新失败。"
    if failed > 0:
        return f"最近一次回归发现 {failed} 个缺陷未通过。"
    if pending > 0:
        return f"当前有 {pending} 个缺陷已纳入回归但尚未执行。"
    if int(summary.get("passed_defect_count") or 0) > 0:
        return "最近一次回归已验证部分缺陷不再复现。"
    if int(summary.get("covered_defect_count") or 0) > 0:
        return "缺陷已纳入回归套件，等待首次执行。"
    return "当前还没有与客户缺陷关联的回归结果。"


def _regression_trend_direction(recent_runs: list[dict[str, Any]]) -> tuple[str, str]:
    if len(recent_runs) < 2:
        return "insufficient_history", "需要至少两轮回归后才能判断趋势。"
    latest = recent_runs[0]
    previous = recent_runs[1]
    latest_failed = int(latest.get("failed_count") or 0)
    previous_failed = int(previous.get("failed_count") or 0)
    latest_review = int(latest.get("needs_review_count") or 0)
    previous_review = int(previous.get("needs_review_count") or 0)
    latest_gate = _first_text(latest.get("gate_status"))
    previous_gate = _first_text(previous.get("gate_status"))
    if latest_gate == "passed" and previous_gate != "passed":
        return "improving", "最近一轮回归已经通过，趋势向好。"
    if latest_failed < previous_failed:
        return "improving", f"最近一轮回归失败项从 {previous_failed} 降到 {latest_failed}。"
    if latest_failed > previous_failed:
        return "regressing", f"最近一轮回归失败项从 {previous_failed} 升到 {latest_failed}。"
    if latest_review < previous_review:
        return "improving", f"待人工复核项从 {previous_review} 降到 {latest_review}。"
    if latest_review > previous_review:
        return "regressing", f"待人工复核项从 {previous_review} 升到 {latest_review}。"
    return "stable", "最近两轮回归结果基本持平。"


def _build_regression_validation_summary(
    recent_runs: list[dict[str, Any]],
    history_run_count: int,
    repeated_failure_defect_count: int,
) -> dict[str, Any]:
    double_run_verified = history_run_count >= 2
    if not recent_runs:
        headline = "当前还没有真实回归运行记录。"
    elif not double_run_verified:
        headline = "当前仅有单轮回归，尚未满足最小双轮验真。"
    elif repeated_failure_defect_count > 0:
        headline = f"最近多轮回归显示有 {repeated_failure_defect_count} 个缺陷反复失败。"
    else:
        headline = "当前已满足最小双轮验真，且未发现反复失败缺陷。"
    return {
        "history_run_count": history_run_count,
        "minimum_required_runs": 2,
        "double_run_verified": double_run_verified,
        "repeated_failure_defect_count": repeated_failure_defect_count,
        "latest_to_previous_change": _first_text(recent_runs[0].get("gate_status")) if recent_runs else "",
        "headline": headline,
    }


def _build_regression_release_guidance(summary: dict[str, Any], commercial_assets: dict[str, Any]) -> dict[str, str]:
    latest_run = summary.get("latest_run") if isinstance(summary.get("latest_run"), dict) else {}
    validation_summary = summary.get("validation_summary") if isinstance(summary.get("validation_summary"), dict) else {}
    trend_direction = _first_text(summary.get("trend_direction"))
    gate_status = _first_text(latest_run.get("gate_status"))
    failed_defects = int(summary.get("failed_defect_count") or 0)
    pending_defects = int(summary.get("pending_defect_count") or 0)
    double_run_verified = bool(validation_summary.get("double_run_verified"))
    repeated_failure_defect_count = int(validation_summary.get("repeated_failure_defect_count") or 0)
    delivery_status = _first_text((commercial_assets.get("delivery_package") or {}).get("status"))
    handoff_status = _first_text((commercial_assets.get("commercial_handoff") or {}).get("status"))

    if gate_status == "failed" or failed_defects > 0 or trend_direction == "regressing":
        return {
            "release_recommendation": "block_release",
            "release_recommendation_label": "建议阻断发布",
            "release_recommendation_reason": "最近回归仍有失败缺陷或趋势恶化，继续发布会放大真实业务风险。",
            "customer_delivery_readiness": "blocked",
            "customer_delivery_readiness_label": "暂不进入客户交付",
        }
    if not double_run_verified:
        return {
            "release_recommendation": "continue_regression",
            "release_recommendation_label": "建议继续执行真实回归",
            "release_recommendation_reason": "当前历史轮次不足，尚未满足最小双轮验真，不能把一次通过当成稳定结论。",
            "customer_delivery_readiness": "needs_more_validation",
            "customer_delivery_readiness_label": "继续验真后再决定交付",
        }
    if pending_defects > 0 or repeated_failure_defect_count > 0:
        return {
            "release_recommendation": "hold_for_validation",
            "release_recommendation_label": "建议先完成剩余回归",
            "release_recommendation_reason": "当前仍有待回归或反复失败缺陷，需要先完成复验收口。",
            "customer_delivery_readiness": "validation_in_progress",
            "customer_delivery_readiness_label": "交付验真进行中",
        }
    if gate_status == "passed" and trend_direction == "improving" and delivery_status == "created":
        return {
            "release_recommendation": "candidate_release",
            "release_recommendation_label": "可进入候选发布",
            "release_recommendation_reason": "最近回归通过且趋势向好，交付包已生成，可进入候选发布或客户验收。",
            "customer_delivery_readiness": "ready_for_customer_delivery",
            "customer_delivery_readiness_label": "可进入客户交付",
        }
    if gate_status == "passed" and trend_direction in {"improving", "stable"} and handoff_status:
        return {
            "release_recommendation": "candidate_acceptance",
            "release_recommendation_label": "可进入客户验收",
            "release_recommendation_reason": "最近回归已经稳定，建议结合当前商业交付资产推进客户验收。",
            "customer_delivery_readiness": "ready_for_customer_acceptance",
            "customer_delivery_readiness_label": "可进入客户验收",
        }
    return {
        "release_recommendation": "continue_monitoring",
        "release_recommendation_label": "建议继续观察后续轮次",
        "release_recommendation_reason": "当前缺少足够强的发布/交付信号，建议继续保留持续回归。",
        "customer_delivery_readiness": "monitoring",
        "customer_delivery_readiness_label": "持续观察中",
    }


def _load_regression_projection(root: Path, project_id: str, defects: list[dict[str, Any]]) -> dict[str, Any]:
    suite_candidates = [
        root / "platform_outputs" / project_id / "regression_suite" / "regression_suite.json",
        root / "platform_workspace" / project_id / "defect_discovery" / "regression_suite.json",
    ]
    run_candidates = [
        root / "platform_outputs" / project_id / "regression_run" / "regression_run_result.json",
        root / "platform_workspace" / project_id / "defect_discovery" / "regression_run_result.json",
    ]
    suite = next((_read_json_safe(path, {}) for path in suite_candidates if path.exists()), {})
    run = next((_read_json_safe(path, {}) for path in run_candidates if path.exists()), {})
    run_history = _load_regression_history(root, project_id)

    suite_modes = suite.get("modes") if isinstance(suite, dict) else {}
    suite_index: dict[str, set[str]] = {}
    suite_mode_counts: dict[str, int] = {}
    if isinstance(suite_modes, dict):
        for mode_name, mode_payload in suite_modes.items():
            items = mode_payload.get("items") if isinstance(mode_payload, dict) and isinstance(mode_payload.get("items"), list) else []
            suite_mode_counts[str(mode_name)] = len(items)
            for item in items:
                if not isinstance(item, dict):
                    continue
                lookup_keys = _regression_lookup_keys(item.get("issue_id"), item.get("regression_probe_id"), item.get("path"), item.get("title"))
                for lookup_key in lookup_keys:
                    suite_index.setdefault(lookup_key, set()).add(str(mode_name))

    run_items = run.get("items") if isinstance(run, dict) and isinstance(run.get("items"), list) else []
    run_index: dict[str, dict[str, Any]] = {}
    run_status_counts: dict[str, int] = {}
    for item in run_items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower() or "unknown"
        run_status_counts[status] = run_status_counts.get(status, 0) + 1
        lookup_keys = _regression_lookup_keys(item.get("issue_id"), item.get("regression_probe_id"), item.get("path"), item.get("title"))
        for lookup_key in lookup_keys:
            run_index.setdefault(lookup_key, item)

    issue_history_index: dict[str, list[dict[str, Any]]] = {}
    for history_entry in reversed(run_history):
        if not isinstance(history_entry, dict):
            continue
        history_items = history_entry.get("items") if isinstance(history_entry.get("items"), list) else []
        for history_item in history_items:
            if not isinstance(history_item, dict):
                continue
            history_snapshot = {
                "generated_at": _first_text(history_entry.get("generated_at")),
                "suite_mode": _first_text(history_entry.get("suite_mode")),
                "suite_mode_label": _first_text(history_entry.get("suite_mode_label")),
                "gate_status": _first_text(history_entry.get("gate_status")),
                "ci_message": _first_text(history_entry.get("ci_message")),
                "status": _first_text(history_item.get("status")),
                "status_label": _regression_status_label(_first_text(history_item.get("status"))),
                "reason": _first_text(history_item.get("reason")),
                "regression_probe_id": _first_text(history_item.get("regression_probe_id")),
                "issue_id": _first_text(history_item.get("issue_id")),
                "path": _first_text(history_item.get("path")),
                "method": _first_text(history_item.get("method")),
                "title": _first_text(history_item.get("title")),
                "severity": _first_text(history_item.get("severity")),
            }
            lookup_keys = _regression_lookup_keys(
                history_item.get("issue_id"),
                history_item.get("regression_probe_id"),
                history_item.get("path"),
                history_item.get("title"),
            )
            for lookup_key in lookup_keys:
                issue_history_index.setdefault(lookup_key, []).append(history_snapshot)

    annotated_defects = 0
    covered_defects = 0
    defect_status_counts = {key: 0 for key in ("passed", "failed", "needs_review", "skipped", "pending", "not_covered")}
    lifecycle_counts: dict[str, int] = {}
    repeated_failure_defect_count = 0
    latest_run_summary = run.get("summary") if isinstance(run, dict) and isinstance(run.get("summary"), dict) else {}
    latest_ci_feedback = run.get("ci_feedback") if isinstance(run, dict) and isinstance(run.get("ci_feedback"), dict) else {}

    for defect in defects:
        if not isinstance(defect, dict):
            continue
        annotated_defects += 1
        lookup_keys = _regression_lookup_keys(
            defect.get("id"),
            defect.get("issue_id"),
            defect.get("repro_path"),
            defect.get("title"),
        )
        matched_suite_modes: set[str] = set()
        matched_run_item: dict[str, Any] | None = None
        for lookup_key in lookup_keys:
            matched_suite_modes.update(suite_index.get(lookup_key, set()))
            if matched_run_item is None and lookup_key in run_index:
                matched_run_item = run_index[lookup_key]
        if matched_suite_modes:
            covered_defects += 1
        latest_status = (
            str(matched_run_item.get("status") or "").strip().lower()
            if isinstance(matched_run_item, dict)
            else "pending" if matched_suite_modes else "not_covered"
        ) or "not_covered"
        matched_history: list[dict[str, Any]] = []
        seen_history_keys: set[tuple[str, str, str]] = set()
        for lookup_key in lookup_keys:
            for history_snapshot in issue_history_index.get(lookup_key, []):
                dedupe_key = (
                    _first_text(history_snapshot.get("generated_at")),
                    _first_text(history_snapshot.get("status")),
                    _first_text(history_snapshot.get("regression_probe_id"), history_snapshot.get("path")),
                )
                if dedupe_key in seen_history_keys:
                    continue
                seen_history_keys.add(dedupe_key)
                matched_history.append(history_snapshot)
        matched_history = matched_history[:6]
        failure_count_in_history = len([item for item in matched_history if _first_text(item.get("status")) == "failed"])
        if failure_count_in_history >= 2:
            repeated_failure_defect_count += 1
        # Compute consecutive regression passes for stability tracking
        consecutive_passes = 0
        for history_item in matched_history:
            if _first_text(history_item.get("status")) == "passed":
                consecutive_passes += 1
            else:
                break  # only count consecutive from most recent
        lifecycle = _regression_lifecycle(latest_status, bool(matched_suite_modes), consecutive_passes=consecutive_passes)
        lifecycle_counts[lifecycle["code"]] = lifecycle_counts.get(lifecycle["code"], 0) + 1
        if latest_status not in defect_status_counts:
            defect_status_counts[latest_status] = 0
        defect_status_counts[latest_status] += 1
        defect["regression"] = {
            "included_in_suite": bool(matched_suite_modes),
            "suite_modes": sorted(matched_suite_modes),
            "latest_status": latest_status,
            "latest_status_label": _regression_status_label(latest_status),
            "last_run_at": _first_text(latest_run_summary.get("generated_at")),
            "last_run_mode": _first_text(latest_run_summary.get("suite_mode")),
            "gate_status": _first_text(latest_ci_feedback.get("gate_status")),
            "reason": (
                _first_text(matched_run_item.get("reason"))
                if isinstance(matched_run_item, dict)
                else "该缺陷已纳入回归套件，等待执行。"
                if matched_suite_modes
                else "当前还没有与该缺陷关联的回归探针。"
            ),
            "regression_probe_id": _first_text(matched_run_item.get("regression_probe_id")) if isinstance(matched_run_item, dict) else "",
            "issue_id": _first_text(matched_run_item.get("issue_id"), defect.get("id")) if isinstance(matched_run_item, dict) else _first_text(defect.get("id")),
            "history": matched_history,
            "history_count": len(matched_history),
            "lifecycle_status": lifecycle["code"],
            "lifecycle_label": lifecycle["label"],
            "lifecycle_description": lifecycle["description"],
            "consecutive_passes": consecutive_passes,
            "stable": lifecycle["code"] == "stable",
        }

    gate_status_counts: dict[str, int] = {}
    recent_runs: list[dict[str, Any]] = []
    for history_entry in reversed(run_history):
        if not isinstance(history_entry, dict):
            continue
        gate_status = _first_text(history_entry.get("gate_status")) or "unknown"
        gate_status_counts[gate_status] = gate_status_counts.get(gate_status, 0) + 1
        summary_payload = history_entry.get("summary") if isinstance(history_entry.get("summary"), dict) else {}
        recent_runs.append(
            {
                "generated_at": _first_text(history_entry.get("generated_at")),
                "suite_mode": _first_text(history_entry.get("suite_mode")),
                "suite_mode_label": _first_text(history_entry.get("suite_mode_label")),
                "gate_status": gate_status,
                "ci_message": _first_text(history_entry.get("ci_message")),
                "total_probe_count": int(summary_payload.get("total_probe_count") or 0),
                "executed_count": int(summary_payload.get("executed_count") or 0),
                "passed_count": int(summary_payload.get("passed_count") or 0),
                "failed_count": int(summary_payload.get("failed_count") or 0),
                "needs_review_count": int(summary_payload.get("needs_review_count") or 0),
                "skipped_count": int(summary_payload.get("skipped_count") or 0),
            }
        )
    recent_runs = recent_runs[:5]
    trend_direction, trend_summary = _regression_trend_direction(recent_runs)
    validation_summary = _build_regression_validation_summary(recent_runs, len(run_history), repeated_failure_defect_count)

    summary = {
        "suite_exists": bool(isinstance(suite, dict) and suite),
        "run_exists": bool(isinstance(run, dict) and run),
        "suite": {
            "generated_at": _first_text(suite.get("generated_at") if isinstance(suite, dict) else ""),
            "total_probe_count": int(suite.get("summary", {}).get("total_probe_count") or 0) if isinstance(suite, dict) and isinstance(suite.get("summary"), dict) else 0,
            "smoke_count": int(suite.get("summary", {}).get("smoke_count") or 0) if isinstance(suite, dict) and isinstance(suite.get("summary"), dict) else 0,
            "release_count": int(suite.get("summary", {}).get("release_count") or 0) if isinstance(suite, dict) and isinstance(suite.get("summary"), dict) else 0,
            "full_count": int(suite.get("summary", {}).get("full_count") or 0) if isinstance(suite, dict) and isinstance(suite.get("summary"), dict) else 0,
            "mode_counts": suite_mode_counts,
        },
        "latest_run": {
            "generated_at": _first_text(latest_run_summary.get("generated_at")),
            "suite_mode": _first_text(latest_run_summary.get("suite_mode")),
            "suite_mode_label": _first_text(latest_run_summary.get("suite_mode_label")),
            "gate_status": _first_text(latest_ci_feedback.get("gate_status")),
            "ci_message": _first_text(latest_ci_feedback.get("ci_message")),
            "total_probe_count": int(latest_run_summary.get("total_probe_count") or 0),
            "executed_count": int(latest_run_summary.get("executed_count") or 0),
            "passed_count": int(latest_run_summary.get("passed_count") or 0),
            "failed_count": int(latest_run_summary.get("failed_count") or 0),
            "needs_review_count": int(latest_run_summary.get("needs_review_count") or 0),
            "skipped_count": int(latest_run_summary.get("skipped_count") or 0),
            "run_status_counts": run_status_counts,
            "reopen_issue_ids": list(latest_ci_feedback.get("reopen_issue_ids") or []),
        },
        "history_run_count": len(run_history),
        "recent_runs": recent_runs,
        "gate_status_counts": gate_status_counts,
        "trend_direction": trend_direction,
        "trend_summary": trend_summary,
        "lifecycle_counts": lifecycle_counts,
        "validation_summary": validation_summary,
        "customer_ready_defect_count": annotated_defects,
        "covered_defect_count": covered_defects,
        "passed_defect_count": int(defect_status_counts.get("passed") or 0),
        "failed_defect_count": int(defect_status_counts.get("failed") or 0),
        "needs_review_defect_count": int(defect_status_counts.get("needs_review") or 0),
        "pending_defect_count": int(defect_status_counts.get("pending") or 0),
        "skipped_defect_count": int(defect_status_counts.get("skipped") or 0),
        "not_covered_defect_count": int(defect_status_counts.get("not_covered") or 0),
        "defect_status_counts": defect_status_counts,
    }
    summary["headline"] = _regression_summary_title(summary)
    return summary
