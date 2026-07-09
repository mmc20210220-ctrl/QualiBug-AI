from __future__ import annotations

"""Expose the latest regression run verdict in the command center.

Regression suite refresh tells the customer which confirmed bugs became durable
regression obligations.  This patch exposes the next step: after the customer
runs regression, the command center shows whether the latest run passed, failed,
or still requires manual review.

It is visibility-only:
- it never executes regression;
- it never changes probe verdicts;
- it only lifts the persisted regression run result into the customer envelope.
"""

import json
from pathlib import Path
from typing import Any

PATCH_SOURCE = "ai_test_asset_center.private_pilot_regression_run_visibility_patch"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_project(value: str) -> str:
    return str(value or "").replace("/", "_").strip() or "unscoped"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _project_from_payload(payload: dict[str, Any]) -> str:
    data = _as_dict(payload.get("data"))
    for value in (
        data.get("project_id"), data.get("project"),
        payload.get("project_id"), payload.get("project"),
    ):
        text = str(value or "").strip()
        if text:
            return _safe_project(text)
    return ""


def _load_regression_run(project: str, root: Path) -> dict[str, Any]:
    if not project:
        return {}
    return _read_json(root / "platform_outputs" / _safe_project(project) / "regression_run" / "regression_run_result.json")


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    execution = _as_dict(item.get("execution"))
    oracle = _as_dict(item.get("regression_oracle"))
    return {
        "regression_probe_id": str(item.get("regression_probe_id") or ""),
        "issue_id": str(item.get("issue_id") or ""),
        "title": str(item.get("title") or "")[:260],
        "severity": str(item.get("severity") or ""),
        "module": str(item.get("module") or ""),
        "risk_type": str(item.get("risk_type") or ""),
        "method": str(item.get("method") or ""),
        "path": str(item.get("path") or "")[:500],
        "status": str(item.get("status") or ""),
        "passed": bool(item.get("passed")),
        "reason": str(item.get("reason") or "")[:1000],
        "status_code": execution.get("status_code"),
        "oracle": oracle if oracle else {},
        "production_data_blocked": bool(item.get("production_data_blocked")),
    }


def compact_regression_run(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    summary = _as_dict(result.get("summary"))
    ci_feedback = _as_dict(result.get("ci_feedback"))
    failures = [item for item in (result.get("failures") or []) if isinstance(item, dict)]
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    needs_review = [item for item in items if str(item.get("status") or "") == "needs_review"]
    passed = [item for item in items if str(item.get("status") or "") == "passed"]
    failed = [item for item in items if str(item.get("status") or "") == "failed"]
    skipped = [item for item in items if str(item.get("status") or "") == "skipped"]
    gate_status = str(ci_feedback.get("gate_status") or "")
    if not gate_status:
        gate_status = "failed" if failures else "manual_approval_required" if needs_review else "passed" if passed else "not_run"
    return {
        "status": "available",
        "gate_status": gate_status,
        "ci_message": str(ci_feedback.get("ci_message") or ""),
        "exit_code": int(ci_feedback.get("exit_code") or 0) if str(ci_feedback.get("exit_code") or "").isdigit() else ci_feedback.get("exit_code"),
        "generated_at": str(summary.get("generated_at") or ""),
        "suite_mode": str(summary.get("suite_mode") or ""),
        "suite_mode_label": str(summary.get("suite_mode_label") or ""),
        "dry_run": bool(summary.get("dry_run")),
        "total_probe_count": int(summary.get("total_probe_count") or len(items) or 0),
        "executed_count": int(summary.get("executed_count") or 0),
        "passed_count": int(summary.get("passed_count") or len(passed) or 0),
        "failed_count": int(summary.get("failed_count") or len(failed) or 0),
        "needs_review_count": int(summary.get("needs_review_count") or len(needs_review) or 0),
        "skipped_count": int(summary.get("skipped_count") or len(skipped) or 0),
        "p0_p1_failed_count": int(summary.get("p0_p1_failed_count") or 0),
        "production_data_blocked_count": int(summary.get("production_data_blocked_count") or 0),
        "reverification": _as_dict(summary.get("reverification")),
        "regression_suite_ref": str(result.get("regression_suite_ref") or ""),
        "history_ref": str(result.get("history_ref") or ""),
        "history_size": int(result.get("history_size") or 0),
        "lifecycle_summary": _as_dict(result.get("lifecycle_summary")),
        "failures": [_compact_item(item) for item in failures[:10]],
        "needs_review": [_compact_item(item) for item in needs_review[:10]],
        "honesty_rule": "Regression run visibility reports the latest persisted execution result; it does not re-run probes or change verdicts.",
    }


def _dashboard_regression_summary(compact: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """Mirror latest regression_run into the legacy Dashboard regression_summary."""
    if not compact:
        return existing
    gate = str(compact.get("gate_status") or "")
    failed = int(compact.get("failed_count") or 0)
    passed = int(compact.get("passed_count") or 0)
    needs_review = int(compact.get("needs_review_count") or 0)
    skipped = int(compact.get("skipped_count") or 0)
    total = int(compact.get("total_probe_count") or passed + failed + needs_review + skipped)
    pending = needs_review + skipped
    latest_run = {
        "generated_at": compact.get("generated_at"),
        "suite_mode": compact.get("suite_mode"),
        "suite_mode_label": compact.get("suite_mode_label"),
        "gate_status": gate,
        "failed_count": failed,
        "passed_count": passed,
        "needs_review_count": needs_review,
        "skipped_count": skipped,
        "ci_message": compact.get("ci_message"),
    }
    if gate == "failed":
        headline = f"最近回归失败：{failed} 个探针失败，需要继续修复或复核。"
        trend_direction = "regressing"
        release_recommendation = "block_release"
        release_label = "建议阻断发布"
        release_reason = "最新回归仍存在失败项，不能声明缺陷已修复。"
        readiness = "回归失败，暂不建议交付"
    elif gate == "passed":
        headline = f"最近回归通过：{passed} 个探针通过。"
        trend_direction = "stable"
        release_recommendation = "candidate_release"
        release_label = "可进入候选发布"
        release_reason = "最新回归没有失败项，但仍需结合覆盖范围和验收要求。"
        readiness = "最新回归通过"
    else:
        headline = f"最近回归需要复核：{needs_review} 个探针缺少强自动判定。"
        trend_direction = "stable"
        release_recommendation = "hold_for_validation"
        release_label = "建议先完成剩余回归"
        release_reason = "最新回归仍有需人工确认项，不能直接声明通过。"
        readiness = "需要人工复核"
    merged = dict(existing)
    merged.update({
        "suite_exists": True,
        "covered_defect_count": total,
        "passed_defect_count": passed,
        "failed_defect_count": failed,
        "pending_defect_count": pending,
        "latest_run": latest_run,
        "headline": headline,
        "trend_direction": trend_direction,
        "trend_summary": str(compact.get("ci_message") or headline),
        "history_run_count": int(compact.get("history_size") or existing.get("history_run_count") or 0),
        "recent_runs": [latest_run],
        "release_recommendation": release_recommendation,
        "release_recommendation_label": release_label,
        "release_recommendation_reason": release_reason,
        "customer_delivery_readiness": readiness,
        "customer_delivery_readiness_label": readiness,
        "lifecycle_counts": _as_dict(compact.get("lifecycle_summary")) or _as_dict(existing.get("lifecycle_counts")),
        "validation_summary": {
            **_as_dict(existing.get("validation_summary")),
            "double_run_verified": int(compact.get("history_size") or 0) >= 2,
            "minimum_required_runs": 2,
            "repeated_failure_defect_count": failed if int(compact.get("history_size") or 0) >= 2 else 0,
            "headline": headline,
        },
        "latest_regression_run_source": "regression_run_visibility_patch",
        "honesty_rule": "Dashboard regression_summary is a compatibility mirror of latest regression_run_result; it does not re-run probes or change verdicts.",
    })
    return merged


def _normalized_release_check(value: Any) -> dict[str, Any] | None:
    row = _as_dict(value)
    name = str(row.get("name") or "").strip()
    status = str(row.get("status") or "").strip()
    if not name or status not in {"pass", "fail", "pending"}:
        return None
    return {
        "name": name,
        "status": status,
        "detail": str(row.get("detail") or row.get("reason") or "后端发布门禁未提供详情。"),
        "source": str(row.get("source") or "existing_release_gate"),
    }


def _dedupe_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in checks:
        normalized = _normalized_release_check(item)
        if not normalized:
            continue
        key = normalized["name"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _regression_release_check(data: dict[str, Any], compact: dict[str, Any]) -> dict[str, Any] | None:
    gate = str(compact.get("gate_status") or "") if compact else ""
    if gate == "failed":
        return {
            "name": "修复后回归 Gate",
            "status": "fail",
            "detail": f"最近一次回归失败：{int(compact.get('failed_count') or 0)} 个探针失败，{int(compact.get('needs_review_count') or 0)} 个需复核。发布前必须先修复或复核失败项。",
            "source": "regression_run",
        }
    if gate == "manual_approval_required":
        return {
            "name": "修复后回归 Gate",
            "status": "pending",
            "detail": f"最近一次回归仍需人工复核：{int(compact.get('needs_review_count') or 0)} 个探针缺少强自动判定，不能直接放行发布。",
            "source": "regression_run",
        }
    if gate == "passed":
        return {
            "name": "修复后回归 Gate",
            "status": "pass",
            "detail": f"最近一次回归通过：{int(compact.get('passed_count') or 0)} 个探针通过。该结论仅代表最近一次持久化回归结果，不扩大到未覆盖范围。",
            "source": "regression_run",
        }
    refresh = _as_dict(data.get("regression_suite_refresh"))
    suite = _as_dict(data.get("regression_suite"))
    summary = _as_dict(refresh.get("summary"))
    total = int(suite.get("total_probe_count") or summary.get("total_probe_count") or 0)
    confirmed = int(suite.get("confirmed_ledger_probe_count") or summary.get("confirmed_ledger_probe_count") or 0)
    if str(refresh.get("status") or "") == "refreshed" and total > 0:
        return {
            "name": "修复后回归 Gate",
            "status": "pending",
            "detail": f"已自动生成 {total} 个回归探针，其中 {confirmed} 个来自 confirmed bug ledger；发布前必须先执行 Smoke 或 Release 回归。",
            "source": "regression_suite_refresh",
        }
    return None


def _release_gate_from(data: dict[str, Any], compact: dict[str, Any]) -> dict[str, Any]:
    """Build an API-visible release gate without overwriting existing checks."""
    existing = _as_dict(data.get("release_gate"))
    existing_checks = [item for item in (existing.get("checks") or []) if isinstance(item, dict)]
    regression_check = _regression_release_check(data, compact)
    checks = _dedupe_checks(([regression_check] if regression_check else []) + existing_checks)
    if not checks:
        return {}
    overall = "fail" if any(item.get("status") == "fail" for item in checks) else "pending" if any(item.get("status") == "pending" for item in checks) else "pass"
    return {
        **existing,
        "overall_status": overall,
        "checks": checks,
        "blocking_check_count": sum(1 for item in checks if item.get("status") == "fail"),
        "pending_check_count": sum(1 for item in checks if item.get("status") == "pending"),
        "pass_check_count": sum(1 for item in checks if item.get("status") == "pass"),
        "release_recommendation": "block_release" if overall == "fail" else "hold_for_validation" if overall == "pending" else "candidate_release",
        "source": PATCH_SOURCE,
        "honesty_rule": "Release gate reports existing release checks plus the latest persisted regression state; it does not execute regression or prove untested scope is safe.",
    }


def _blocked_commercial_status(overall: str) -> str:
    return "blocked_by_release_gate" if overall == "fail" else "hold_for_validation" if overall == "pending" else "release_gate_passed"


def _load_customer_delivery_guard(project: str, root: Path) -> dict[str, Any]:
    if not project:
        return {}
    base = root / "platform_outputs" / _safe_project(project)
    for path in (
        base / "customer_delivery_guard.json",
        base / "pipeline_reports" / "customer_delivery_guard.json",
    ):
        guard = _read_json(path)
        if guard:
            return guard
    return {}


def _inject_customer_delivery_guard(data: dict[str, Any], guard: dict[str, Any]) -> None:
    if not guard:
        return
    data["customer_delivery_guard"] = guard

    commercial_assets = _as_dict(data.get("commercial_assets"))
    guard_assets = _as_dict(guard.get("commercial_assets"))
    if guard_assets:
        commercial_assets.update(guard_assets)
    commercial_assets["customer_delivery_guard"] = {
        "status": guard.get("status"),
        "customer_deliverable": bool(guard.get("customer_deliverable")),
        "safe_for_customer": bool(guard.get("safe_for_customer")),
        "block_reasons": guard.get("block_reasons") if isinstance(guard.get("block_reasons"), list) else [],
        "honesty_rule": guard.get("honesty_rule"),
    }
    commercial_assets["customer_deliverable"] = bool(guard.get("customer_deliverable"))
    commercial_assets["customer_delivery_status"] = str(guard.get("status") or "")
    commercial_assets["safe_for_customer"] = bool(guard.get("safe_for_customer"))
    data["commercial_assets"] = commercial_assets

    delivery_tracks = _as_dict(data.get("delivery_tracks"))
    delivery_tracks["customer_delivery_guard"] = {
        "status": guard.get("status"),
        "customer_deliverable": bool(guard.get("customer_deliverable")),
        "tracker_payload_status": guard.get("tracker_payload_status"),
        "release_gate_overall_status": guard.get("release_gate_overall_status"),
        "guard_ref": "platform_outputs/<project>/customer_delivery_guard.json",
    }
    delivery_tracks["customer_delivery_status"] = str(guard.get("status") or "")
    delivery_tracks["customer_deliverable"] = bool(guard.get("customer_deliverable"))
    delivery_tracks["tracker_payload_status"] = str(guard.get("tracker_payload_status") or "")
    data["delivery_tracks"] = delivery_tracks

    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics["customer_delivery_guard_status"] = str(guard.get("status") or "")
    value_metrics["customer_deliverable"] = bool(guard.get("customer_deliverable"))
    value_metrics["safe_for_customer"] = bool(guard.get("safe_for_customer"))
    data["value_metrics"] = value_metrics

    executive = _as_dict(data.get("executive_summary"))
    if guard.get("customer_deliverable") is True:
        executive["customer_delivery_guard_label"] = "客户交付已放行：门禁通过且 Handoff 明确安全"
    else:
        executive["customer_delivery_guard_label"] = f"客户交付未放行：{str(guard.get('status') or 'guard_blocked')}"
    data["executive_summary"] = executive

    contract = _as_dict(data.get("data_contract"))
    contract["customer_delivery_guard"] = {
        "display_key": "customer_delivery_guard",
        "source": "platform_outputs/<project>/customer_delivery_guard.json",
        "honesty_rule": str(guard.get("honesty_rule") or "Customer delivery guard is the source of truth for tracker payload and delivery package status."),
        "customer_meaning": "Machine-readable delivery decision. External tracker payload and delivery package status should not bypass this guard.",
    }
    data["data_contract"] = contract


def _inject_release_gate(data: dict[str, Any], compact: dict[str, Any]) -> None:
    release_gate = _release_gate_from(data, compact)
    if not release_gate:
        return
    data["release_gate"] = release_gate
    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics["release_gate_overall_status"] = release_gate["overall_status"]
    value_metrics["release_gate_blocking_check_count"] = release_gate["blocking_check_count"]
    value_metrics["release_gate_pending_check_count"] = release_gate["pending_check_count"]
    data["value_metrics"] = value_metrics

    delivery_tracks = _as_dict(data.get("delivery_tracks"))
    delivery_tracks["release_gate"] = release_gate
    delivery_tracks["release_gate_overall_status"] = release_gate["overall_status"]
    delivery_tracks["release_gate_blocking_check_count"] = release_gate["blocking_check_count"]
    delivery_tracks["release_gate_pending_check_count"] = release_gate["pending_check_count"]
    delivery_tracks["release_recommendation"] = release_gate["release_recommendation"]
    data["delivery_tracks"] = delivery_tracks

    overall = str(release_gate.get("overall_status") or "")
    commercial_assets = _as_dict(data.get("commercial_assets"))
    commercial_assets["release_gate"] = release_gate
    commercial_assets["release_gate_overall_status"] = overall
    commercial_assets["release_recommendation"] = release_gate["release_recommendation"]
    commercial_assets["release_gate_honesty_rule"] = release_gate["honesty_rule"]
    handoff = _as_dict(commercial_assets.get("commercial_handoff"))
    tracker = _as_dict(commercial_assets.get("tracker_sync"))
    delivery_package = _as_dict(commercial_assets.get("delivery_package"))
    handoff["release_gate_status"] = overall
    tracker["payload_gate_status"] = overall
    tracker["release_gate_overall_status"] = overall
    delivery_package["release_verdict"] = overall
    delivery_package["release_recommendation"] = release_gate["release_recommendation"]
    delivery_package["release_gate_overall_status"] = overall
    if overall in {"fail", "pending"}:
        blocked_status = _blocked_commercial_status(overall)
        handoff["safe_for_customer"] = False
        handoff["acceptance_status"] = blocked_status
        tracker["payload_status"] = blocked_status
        delivery_package["release_gate_blocked"] = True
        delivery_package["release_gate_block_reason"] = release_gate["checks"][0]["detail"] if release_gate.get("checks") else release_gate["honesty_rule"]
    commercial_assets["commercial_handoff"] = handoff
    commercial_assets["tracker_sync"] = tracker
    commercial_assets["delivery_package"] = delivery_package
    data["commercial_assets"] = commercial_assets

    executive = _as_dict(data.get("executive_summary"))
    if overall == "fail":
        executive["release_gate_label"] = "发布门禁阻塞：存在失败门禁项"
    elif overall == "pending":
        executive["release_gate_label"] = "发布门禁待处理：回归未执行或需复核"
    elif overall == "pass":
        executive["release_gate_label"] = "发布门禁通过：最近回归通过"
    data["executive_summary"] = executive

    contract = _as_dict(data.get("data_contract"))
    contract["release_gate"] = {
        "display_key": "release_gate",
        "source": "existing release_gate plus regression_run/regression_suite_refresh command-center contract",
        "honesty_rule": release_gate["honesty_rule"],
        "customer_meaning": "API-visible release verdict derived from existing release checks and the latest regression run or pending regression obligations.",
    }
    data["data_contract"] = contract


def inject_regression_run(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data is None:
        return payload
    project = _project_from_payload(payload) or _project_from_payload({"data": data})
    root_path = Path(root or Path.cwd())
    result = _load_regression_run(project, root_path)
    compact = compact_regression_run(result)

    if compact:
        data["regression_run"] = compact
        data["regression_summary"] = _dashboard_regression_summary(compact, _as_dict(data.get("regression_summary")))
        scan_meta = _as_dict(data.get("scan_meta"))
        scan_meta["regression_run"] = compact
        data["scan_meta"] = scan_meta

        value_metrics = _as_dict(data.get("value_metrics"))
        value_metrics["regression_last_gate_status"] = compact["gate_status"]
        value_metrics["regression_last_failed_count"] = compact["failed_count"]
        value_metrics["regression_last_needs_review_count"] = compact["needs_review_count"]
        value_metrics["regression_last_passed_count"] = compact["passed_count"]
        data["value_metrics"] = value_metrics

        executive = _as_dict(data.get("executive_summary"))
        gate = str(compact.get("gate_status") or "")
        if gate == "passed":
            executive["regression_run_label"] = f"最近回归通过：{compact['passed_count']} 个探针通过"
        elif gate == "failed":
            executive["regression_run_label"] = f"最近回归失败：{compact['failed_count']} 个探针失败"
        elif gate:
            executive["regression_run_label"] = f"最近回归需复核：{compact['needs_review_count']} 个探针待确认"
        data["executive_summary"] = executive

        contract = _as_dict(data.get("data_contract"))
        contract["regression_run"] = {
            "display_key": "regression_run",
            "source": "platform_outputs/<project>/regression_run/regression_run_result.json",
            "honesty_rule": compact["honesty_rule"],
            "customer_meaning": "Shows the latest persisted regression execution verdict after the customer runs regression.",
        }
        contract["regression_summary"] = {
            "display_key": "regression_summary",
            "source": "regression_run compatibility mirror",
            "honesty_rule": data["regression_summary"]["honesty_rule"],
            "customer_meaning": "Dashboard-compatible mirror so the primary customer overview can render the latest regression verdict.",
        }
        data["data_contract"] = contract

    _inject_release_gate(data, compact)
    _inject_customer_delivery_guard(data, _load_customer_delivery_guard(project, root_path))
    payload["data"] = data
    return payload


def install_regression_run_visibility_patch(*, patch_source: str = PATCH_SOURCE, root: Path | None = None) -> None:
    from ai_test_asset_center import private_pilot_service as service

    if getattr(service, "_REGRESSION_RUN_VISIBILITY_PATCHED", False):
        return

    original_normalizer = getattr(service, "_normalize_command_center_envelope", None)

    def _normalize_with_regression_run(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = original_normalizer(payload) if callable(original_normalizer) else payload
        try:
            return inject_regression_run(normalized, root=root or service._root())
        except Exception:
            return normalized

    service._ORIGINAL_REGRESSION_RUN_VISIBILITY_NORMALIZER = original_normalizer  # type: ignore[attr-defined]
    service._normalize_command_center_envelope = _normalize_with_regression_run  # type: ignore[attr-defined]
    service._REGRESSION_RUN_VISIBILITY_PATCHED = True  # type: ignore[attr-defined]
    service._REGRESSION_RUN_VISIBILITY_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_regression_run_visibility_patch() -> None:
    from ai_test_asset_center import private_pilot_service as service

    original = getattr(service, "_ORIGINAL_REGRESSION_RUN_VISIBILITY_NORMALIZER", None)
    if callable(original):
        service._normalize_command_center_envelope = original  # type: ignore[attr-defined]
    service._REGRESSION_RUN_VISIBILITY_PATCHED = False  # type: ignore[attr-defined]
