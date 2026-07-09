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


def inject_regression_run(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data is None:
        return payload
    project = _project_from_payload(payload) or _project_from_payload({"data": data})
    result = _load_regression_run(project, Path(root or Path.cwd()))
    compact = compact_regression_run(result)
    if not compact:
        return payload

    data["regression_run"] = compact
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
    data["data_contract"] = contract
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
