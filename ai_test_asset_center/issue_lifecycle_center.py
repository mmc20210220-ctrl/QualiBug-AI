"""
Issue Lifecycle Center — unified defect status tracking across the full pipeline.

Integrates: discovery → QA confirmation → export → fix verification → regression → release gate.
Each regression run auto-updates lifecycle states: regression_passed, reopened, needs_review.
Activated by regression_runner.py (Phase 92F) and scan() pipeline (Phase 108R).

Wired into:
  - regression_runner.run_regression_suite() → auto lifecycle update after regression
  - __main__.scan() → auto lifecycle center generation after multi-round convergence
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id, load_real_project_config

def _run_real_project_discovery_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Stub for removed real_project_defect_discovery module."""
    return {"status": "not_available", "reason": "module_retired"}

run_real_project_discovery = _run_real_project_discovery_stub
from .issue_sync_exporter import build_issue_export_bundle
from .fix_verification_loop import run_fix_verification
# NOTE: regression_runner is imported lazily inside _load_regression_run()
# to avoid circular import with regression_runner -> issue_lifecycle_center.

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}

STATE_ORDER = {
    "pending_review": 10,
    "confirmed": 20,
    "exported": 30,
    "fixing": 40,
    "fixed": 50,
    "regression_passed": 60,
    "reopened": 70,
    "rejected": 80,
    "duplicate": 81,
    "low_value": 82,
    "needs_review": 25,
}

STATE_LABELS = {
    "pending_review": "待 QA 评审",
    "confirmed": "已确认有效",
    "exported": "已导出缺陷单",
    "fixing": "研发修复中",
    "fixed": "已修复待回归",
    "regression_passed": "回归通过",
    "reopened": "回归失败已重开",
    "rejected": "已拒绝 / 误报",
    "duplicate": "重复缺陷",
    "low_value": "低价值问题",
    "needs_review": "需要人工复核",
}

TERMINAL_STATES = {"regression_passed", "rejected", "duplicate", "low_value"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_text(value: Any, limit: int = 3000) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:limit]


def _issue_id(issue: dict[str, Any], index: int) -> str:
    raw = str(issue.get("issue_id") or issue.get("id") or issue.get("external_id") or f"ISSUE_{index:04d}")
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw).strip("._:-")
    return safe[:120] or f"ISSUE_{index:04d}"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [m for m in PRIVATE_MARKERS if m.lower() in text]
    return {"passed": not leaks, "checked": True, "leak_count": len(leaks)}


def _load_real_project_issues(project: str, root: Path) -> list[dict[str, Any]]:
    base = root / "platform_outputs" / project / "real_project"
    data = _read_json(base / "real_project_defect_data.json", {})
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return [i for i in data.get("issues") if isinstance(i, dict)]
    discovered = _read_json(base / "discovered_issues.json", {})
    if isinstance(discovered, dict):
        items = discovered.get("items") or discovered.get("issues") or []
        return [i for i in items if isinstance(i, dict)]
    return []


def _ensure_discovery(project: str, root: Path, options: dict[str, Any]) -> list[dict[str, Any]]:
    issues = _load_real_project_issues(project, root)
    if issues or not options.get("auto_generate_missing", True):
        return issues
    try:
        result = run_real_project_discovery(project, root)
        if isinstance(result, dict):
            return [i for i in result.get("issues", []) if isinstance(i, dict)]
    except Exception:
        pass
    return _load_real_project_issues(project, root)


def _load_issue_exports(project: str, root: Path, options: dict[str, Any]) -> list[dict[str, Any]]:
    base = root / "platform_outputs" / project / "issue_sync"
    issues = _read_json(base / "normalized_issue_drafts.json", [])
    if isinstance(issues, dict):
        issues = issues.get("items") or issues.get("issues") or []
    if isinstance(issues, list) and issues:
        return [i for i in issues if isinstance(i, dict)]
    if options.get("auto_generate_missing", True):
        try:
            result = build_issue_export_bundle(project, root=root)
            if isinstance(result, dict):
                drafts = result.get("issue_drafts") or result.get("issues") or []
                if isinstance(drafts, list):
                    return [i for i in drafts if isinstance(i, dict)]
        except Exception:
            pass
    issues = _read_json(base / "normalized_issue_drafts.json", [])
    if isinstance(issues, dict):
        issues = issues.get("items") or issues.get("issues") or []
    return [i for i in issues if isinstance(issues, list) and isinstance(i, dict)] if isinstance(issues, list) else []


def _load_fix_verification(project: str, root: Path, options: dict[str, Any]) -> list[dict[str, Any]]:
    base = root / "platform_outputs" / project / "fix_verification"
    result = _read_json(base / "fix_verification_result.json", {})
    items = result.get("items") if isinstance(result, dict) else []
    if isinstance(items, list) and items:
        return [i for i in items if isinstance(i, dict)]
    if options.get("auto_generate_missing", False):
        try:
            generated = run_fix_verification(project, root=root)
            return [i for i in generated.get("items", []) if isinstance(i, dict)]
        except Exception:
            return []
    return []


def _load_regression_run(project: str, root: Path, options: dict[str, Any]) -> list[dict[str, Any]]:
    base = root / "platform_outputs" / project / "regression_run"
    result = _read_json(base / "regression_run_result.json", {})
    items = result.get("items") if isinstance(result, dict) else []
    if isinstance(items, list) and items:
        return [i for i in items if isinstance(i, dict)]
    if options.get("auto_generate_missing", False):
        try:
            from .regression_runner import run_regression_suite
            generated = run_regression_suite(project, root=root, options={"mode": options.get("regression_mode", "release"), "dry_run": bool(options.get("dry_run", True))})
            return [i for i in generated.get("items", []) if isinstance(i, dict)]
        except Exception:
            return []
    return []


def _load_fix_status(project: str, root: Path) -> dict[str, dict[str, Any]]:
    path = root / "platform_inputs" / project / "fix_verification" / "fix_status.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("issue_id"):
            out[_safe_text(item.get("issue_id"), 120)] = item
    return out


def _load_human_feedback(project: str, root: Path) -> dict[str, dict[str, Any]]:
    candidates = [
        root / "platform_outputs" / project / "human_feedback" / "human_feedback.jsonl",
        root / "benchmark_outputs" / "human_feedback" / "human_feedback.jsonl",
    ]
    out: dict[str, dict[str, Any]] = {}
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            issue_id = item.get("issue_id") or item.get("bug_id") or item.get("defect_id")
            if issue_id:
                out[_safe_text(issue_id, 120)] = item
    return out


def _state_from_feedback(issue: dict[str, Any], feedback: dict[str, Any] | None = None) -> str | None:
    tokens = " ".join(str(x or "") for x in [
        issue.get("qa_feedback_status"), issue.get("status"), issue.get("review_status"),
        (feedback or {}).get("verdict"), (feedback or {}).get("status"), (feedback or {}).get("qa_feedback_status"),
    ]).lower()
    if any(k in tokens for k in ["false_positive", "not_a_bug", "rejected", "误报", "拒绝"]):
        return "rejected"
    if any(k in tokens for k in ["duplicate", "重复"]):
        return "duplicate"
    if any(k in tokens for k in ["low_value", "low value", "低价值"]):
        return "low_value"
    if any(k in tokens for k in ["confirmed", "valid", "accepted", "有效", "已确认"]):
        return "confirmed"
    if any(k in tokens for k in ["needs_review", "pending", "待确认", "待评审"]):
        return "pending_review"
    return None


def _empty_lifecycle(issue_id: str, source: str, issue: dict[str, Any] | None = None) -> dict[str, Any]:
    issue = issue or {}
    return {
        "issue_id": issue_id,
        "title": _safe_text(issue.get("title") or f"缺陷 {issue_id}", 240),
        "risk_type": _safe_text(issue.get("risk_type") or "business_risk", 120),
        "severity": _safe_text(str(issue.get("severity") or "P2").upper(), 20),
        "confidence": float(issue.get("confidence") or 0) if str(issue.get("confidence") or "").replace(".", "", 1).isdigit() else 0.0,
        "module": _safe_text(issue.get("module") or issue.get("risk_type") or "未分类", 120),
        "state": "pending_review",
        "state_label": STATE_LABELS["pending_review"],
        "source": source,
        "created_at": _now(),
        "updated_at": _now(),
        "is_release_blocker": str(issue.get("severity") or "P2").upper() in {"P0", "P1"},
        "qa_feedback_status": _safe_text(issue.get("qa_feedback_status") or issue.get("status") or "pending", 100),
        "exported": False,
        "fix_status": "not_marked",
        "verification_status": "not_run",
        "regression_status": "not_run",
        "reopen_required": False,
        "timeline": [],
        "latest_evidence": issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {},
        "next_action": "QA 评审确认是否为有效缺陷。",
    }


def _add_event(item: dict[str, Any], event_type: str, message: str, source: str, payload: dict[str, Any] | None = None) -> None:
    item.setdefault("timeline", []).append({
        "time": _now(),
        "type": event_type,
        "source": source,
        "message": message,
        "payload": payload or {},
    })
    item["updated_at"] = _now()


def _promote_state(item: dict[str, Any], state: str) -> None:
    current = str(item.get("state") or "pending_review")
    if state in {"rejected", "duplicate", "low_value", "reopened"}:
        item["state"] = state
    elif current not in {"rejected", "duplicate", "low_value", "reopened"} and STATE_ORDER.get(state, 0) >= STATE_ORDER.get(current, 0):
        item["state"] = state
    item["state_label"] = STATE_LABELS.get(item["state"], item["state"])


def _next_action(item: dict[str, Any]) -> str:
    state = item.get("state")
    if state == "pending_review":
        return "QA 评审该问题是否有效、是否需要升级严重等级。"
    if state == "confirmed":
        return "导出到 Jira / 禅道 / GitHub Issues 并分配研发修复。"
    if state == "exported":
        return "研发修复并在 fix_status.jsonl 标记 fixed / still_failing。"
    if state == "fixing":
        return "等待研发修复完成后触发修复验证。"
    if state == "fixed":
        return "运行 release/smoke 回归，确认修复未回归。"
    if state == "regression_passed":
        return "保持在回归套件中，后续发布持续执行。"
    if state == "reopened":
        return "重新打开缺陷并阻断或人工审批发布。"
    if state == "needs_review":
        return "补充业务断言、测试账号或环境数据后重新验证。"
    return "无需继续处理或等待人工确认。"


def _merge_issue(base: dict[str, dict[str, Any]], issue_id: str, source: str, issue: dict[str, Any]) -> dict[str, Any]:
    item = base.setdefault(issue_id, _empty_lifecycle(issue_id, source, issue))
    for key in ["title", "risk_type", "severity", "module", "qa_feedback_status"]:
        if not item.get(key) and issue.get(key):
            item[key] = _safe_text(issue.get(key), 240 if key == "title" else 120)
    if issue.get("confidence") and not item.get("confidence"):
        try:
            item["confidence"] = float(issue.get("confidence") or 0)
        except Exception:
            pass
    if isinstance(issue.get("evidence"), dict):
        item["latest_evidence"] = issue.get("evidence")
    return item


def build_issue_lifecycle_center(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    issues: dict[str, dict[str, Any]] = {}

    human_feedback = _load_human_feedback(project, root)
    real_issues = _ensure_discovery(project, root, options)
    for idx, issue in enumerate(real_issues, start=1):
        issue_id = _issue_id(issue, idx)
        item = _merge_issue(issues, issue_id, "real_project_discovery", issue)
        _add_event(item, "discovered", "真实项目发现疑似高价值缺陷。", "real_project_discovery", {"severity": item.get("severity"), "risk_type": item.get("risk_type")})
        feedback_state = _state_from_feedback(issue, human_feedback.get(issue_id))
        if feedback_state:
            _promote_state(item, feedback_state)
            _add_event(item, "qa_feedback", f"QA 反馈状态：{STATE_LABELS.get(feedback_state, feedback_state)}。", "human_feedback", human_feedback.get(issue_id, {}))

    for idx, issue in enumerate(_load_issue_exports(project, root, options), start=1):
        issue_id = _issue_id(issue, idx)
        item = _merge_issue(issues, issue_id, "issue_sync_export", issue)
        item["exported"] = True
        item["export_targets"] = ["jira_csv", "zentao_csv", "github_issues"]
        _promote_state(item, "exported")
        _add_event(item, "exported", "已生成企业缺陷系统导入草稿。", "issue_sync_export", {"severity": item.get("severity"), "priority": issue.get("priority")})

    for issue_id, fix in _load_fix_status(project, root).items():
        item = issues.setdefault(issue_id, _empty_lifecycle(issue_id, "fix_status"))
        status = _safe_text(fix.get("fix_status") or fix.get("status") or "not_marked", 100).lower()
        item["fix_status"] = status
        item["developer"] = _safe_text(fix.get("developer") or fix.get("owner") or "", 100)
        if status in {"fixed", "resolved", "done", "已修复"}:
            _promote_state(item, "fixed")
            _add_event(item, "fix_marked", "研发已标记修复，等待自动验证和回归。", "fix_status", fix)
        elif status in {"fixing", "in_progress", "doing", "修复中"}:
            _promote_state(item, "fixing")
            _add_event(item, "fixing", "研发修复中。", "fix_status", fix)
        elif status in {"still_failing", "reopened", "failed", "未修复"}:
            item["reopen_required"] = True
            _promote_state(item, "reopened")
            _add_event(item, "reopened", "研发/QA 标记仍未修复或需要重开。", "fix_status", fix)

    for ver in _load_fix_verification(project, root, options):
        issue_id = _safe_text(ver.get("issue_id"), 120) or _issue_id(ver, len(issues) + 1)
        item = _merge_issue(issues, issue_id, "fix_verification", ver)
        status = _safe_text(ver.get("verification_status") or "needs_review", 100)
        item["verification_status"] = status
        item["verification_reason"] = _safe_text(ver.get("reason"), 800)
        if status == "fixed":
            _promote_state(item, "fixed")
            _add_event(item, "verified_fixed", "修复验证显示原缺陷信号未复现。", "fix_verification", ver)
        elif status == "still_failing":
            item["reopen_required"] = True
            _promote_state(item, "reopened")
            _add_event(item, "still_failing", "修复验证仍复现缺陷信号，建议重开缺陷。", "fix_verification", ver)
        else:
            _promote_state(item, "needs_review")
            _add_event(item, "verification_needs_review", "修复验证证据不足，需要 QA 复核。", "fix_verification", ver)

    for reg in _load_regression_run(project, root, options):
        issue_id = _safe_text(reg.get("issue_id"), 120)
        if not issue_id:
            continue
        item = _merge_issue(issues, issue_id, "regression_runner", reg)
        status = _safe_text(reg.get("status") or "needs_review", 100)
        item["regression_status"] = status
        item["regression_reason"] = _safe_text(reg.get("reason"), 800)
        if status == "passed":
            _promote_state(item, "regression_passed")
            _add_event(item, "regression_passed", "回归套件通过，缺陷进入长期防回归资产。", "regression_runner", reg)
        elif status == "failed":
            item["reopen_required"] = True
            _promote_state(item, "reopened")
            _add_event(item, "regression_failed", "回归执行失败，建议重开缺陷并影响发布门禁。", "regression_runner", reg)
        elif status == "needs_review":
            _promote_state(item, "needs_review")
            _add_event(item, "regression_needs_review", "回归结果无法自动判定，需要人工复核。", "regression_runner", reg)

    lifecycle_items = sorted(issues.values(), key=lambda x: (STATE_ORDER.get(str(x.get("state")), 999), str(x.get("severity")), str(x.get("issue_id"))))
    for item in lifecycle_items:
        item["state_label"] = STATE_LABELS.get(str(item.get("state")), str(item.get("state")))
        item["next_action"] = _next_action(item)
        item["timeline"] = item.get("timeline", [])[-20:]
        item["is_terminal"] = item.get("state") in TERMINAL_STATES

    state_counts = _count_by(lifecycle_items, "state")
    reopened_count = state_counts.get("reopened", 0)
    pending_review_count = state_counts.get("pending_review", 0) + state_counts.get("needs_review", 0)
    active_count = sum(1 for i in lifecycle_items if i.get("state") not in TERMINAL_STATES)
    summary = {
        "phase": "phase33_defect_lifecycle_center",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at": _now(),
        "issue_count": len(lifecycle_items),
        "active_issue_count": active_count,
        "pending_review_count": pending_review_count,
        "confirmed_count": state_counts.get("confirmed", 0),
        "exported_count": state_counts.get("exported", 0),
        "fixing_count": state_counts.get("fixing", 0),
        "fixed_count": state_counts.get("fixed", 0),
        "regression_passed_count": state_counts.get("regression_passed", 0),
        "reopened_count": reopened_count,
        "release_blocker_count": sum(1 for i in lifecycle_items if i.get("reopen_required") or (i.get("state") in {"confirmed", "exported", "fixing", "fixed", "needs_review"} and i.get("severity") in {"P0", "P1"})),
        "state_distribution": state_counts,
        "severity_distribution": _count_by(lifecycle_items, "severity"),
        "risk_distribution": _count_by(lifecycle_items, "risk_type"),
        "module_distribution": _count_by(lifecycle_items, "module"),
    }
    result = {
        "phase": "phase33_defect_lifecycle_center",
        "project_id": project,
        "summary": summary,
        "items": lifecycle_items,
        "state_labels": STATE_LABELS,
        "governance": {
            "real_project_mode": True,
            "does_not_require_benchmark_ground_truth": True,
            "unifies_discovery_export_fix_regression_release_gate": True,
        },
    }
    private = _private_leak_check(result)
    result["private_leak_check"] = private
    summary["private_leak_check_passed"] = private["passed"]

    out_dir = root / "platform_outputs" / project / "issue_lifecycle"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "issue_lifecycle.json", result)
    _write_json(out_dir / "issue_lifecycle_summary.json", summary)
    _write_json(out_dir / "issue_lifecycle_board.json", _build_board(result))
    _write_text(out_dir / "issue_lifecycle_dashboard.html", _render_dashboard(result))
    _write_json(ws_dir / "issue_lifecycle.json", result)
    _write_json(ws_dir / "issue_lifecycle_manifest.json", {"summary": summary, "artifacts": {"dashboard_html": str((out_dir / 'issue_lifecycle_dashboard.html').relative_to(root)).replace('\\', '/') }})
    return result


def _build_board(result: dict[str, Any]) -> dict[str, Any]:
    columns = []
    items = result.get("items") or []
    for state in ["pending_review", "confirmed", "exported", "fixing", "fixed", "regression_passed", "reopened", "needs_review", "rejected", "duplicate", "low_value"]:
        state_items = [i for i in items if i.get("state") == state]
        columns.append({
            "state": state,
            "label": STATE_LABELS.get(state, state),
            "count": len(state_items),
            "items": [{
                "issue_id": i.get("issue_id"),
                "title": i.get("title"),
                "severity": i.get("severity"),
                "risk_type": i.get("risk_type"),
                "next_action": i.get("next_action"),
            } for i in state_items[:200]],
        })
    return {"project_id": result.get("project_id"), "summary": result.get("summary"), "columns": columns}


def _render_dashboard(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    cards = "".join(
        f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>"
        for k, v in {
            "缺陷总数": summary.get("issue_count"),
            "待复核": summary.get("pending_review_count"),
            "已导出": summary.get("exported_count"),
            "修复中": summary.get("fixing_count"),
            "回归通过": summary.get("regression_passed_count"),
            "重开": summary.get("reopened_count"),
            "发布阻断": summary.get("release_blocker_count"),
            "私有隔离": summary.get("private_leak_check_passed"),
        }.items()
    )
    state_map: dict[str, list[dict[str, Any]]] = {}
    for item in result.get("items", []):
        state_map.setdefault(str(item.get("state") or "pending_review"), []).append(item)
    columns_html = []
    for state in ["pending_review", "confirmed", "exported", "fixing", "fixed", "regression_passed", "reopened", "needs_review", "rejected"]:
        rows = []
        for item in state_map.get(state, [])[:80]:
            sev = _safe_text(item.get("severity"), 10)
            cls = "p0" if sev == "P0" else "p1" if sev == "P1" else "p2"
            rows.append(
                f"<div class='issue'><div><span class='sev {cls}'>{_html_escape(sev)}</span><b>{_html_escape(item.get('title'))}</b></div>"
                f"<p>{_html_escape(item.get('issue_id'))} · {_html_escape(item.get('risk_type'))}</p>"
                f"<p class='next'>{_html_escape(item.get('next_action'))}</p></div>"
            )
        columns_html.append(
            f"<div class='column'><h3>{_html_escape(STATE_LABELS.get(state, state))} <em>{len(state_map.get(state, []))}</em></h3>{''.join(rows) or '<p class=empty>暂无</p>'}</div>"
        )
    rows = []
    for item in result.get("items", [])[:200]:
        rows.append(
            "<tr>"
            f"<td>{_html_escape(item.get('state_label'))}</td>"
            f"<td>{_html_escape(item.get('severity'))}</td>"
            f"<td>{_html_escape(item.get('issue_id'))}</td>"
            f"<td>{_html_escape(item.get('title'))}</td>"
            f"<td>{_html_escape(item.get('risk_type'))}</td>"
            f"<td>{_html_escape(item.get('next_action'))}</td>"
            "</tr>"
        )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Defect Lifecycle Center</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.badge{{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:6px 12px;font-weight:700}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{background:#fafafa;border:1px solid #e5e7eb;border-radius:14px;padding:14px}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:24px}}.board{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.column{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:16px;padding:14px;min-height:120px}}.column h3{{margin:0 0 12px}}.column em{{font-style:normal;color:#6b7280;font-size:14px}}.issue{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:10px;margin-bottom:10px}}.issue p{{margin:6px 0 0;color:#6b7280;font-size:12px}}.issue .next{{color:#374151}}.sev{{display:inline-block;padding:2px 7px;border-radius:999px;margin-right:6px;font-size:12px}}.p0{{background:#fee2e2;color:#991b1b}}.p1{{background:#ffedd5;color:#9a3412}}.p2{{background:#e0f2fe;color:#075985}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.empty{{color:#9ca3af}}@media(max-width:1100px){{.grid,.board{{grid-template-columns:1fr}}}}</style></head><body>
<section class='hero'><span class='badge'>Phase33 Defect Lifecycle Center</span><h1>{_html_escape(summary.get('project_name'))} · 缺陷生命周期中心</h1><p>统一展示发现、QA 确认、缺陷单导出、研发修复、修复验证、回归执行、重开和发布风险状态。</p><p>生成时间：{_html_escape(summary.get('generated_at'))}</p></section>
<section class='panel'><h2>生命周期概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>看板</h2><div class='board'>{''.join(columns_html)}</div></section>
<section class='panel'><h2>全量清单</h2><table><thead><tr><th>状态</th><th>等级</th><th>Issue ID</th><th>标题</th><th>风险</th><th>下一步</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">暂无缺陷生命周期数据</td></tr>'}</tbody></table></section>
<section class='panel'><h2>治理说明</h2><p>该中心不读取 Benchmark 私有答案，不依赖 ground truth，只汇总真实项目发现证据、QA 反馈、导出、修复验证和回归执行结果。</p></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    auto = os.environ.get("ISSUE_LIFECYCLE_AUTO_GENERATE", "1").lower() not in {"0", "false", "no"}
    result = build_issue_lifecycle_center(project, options={"auto_generate_missing": auto})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
