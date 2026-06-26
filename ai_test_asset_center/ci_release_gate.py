from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id
from .release_risk_dashboard import build_release_risk_dashboard

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}

DEFAULT_POLICY = {
    "gate_mode": "balanced",
    "fail_on_review": True,
    "strict_canary": False,
    "max_release_risk_score": 77.0,
    "max_p0_issues": 0,
    "max_p1_issues": 2,
    "min_evidence_completeness_for_blockers": 0.55,
}

CI_STATUS_LABELS = {
    "passed": "允许继续发布",
    "canary_allowed": "允许小流量灰度",
    "manual_approval_required": "需要人工审批",
    "failed": "阻断发布",
}


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_policy_from_env() -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    policy["gate_mode"] = os.environ.get("CI_RELEASE_GATE_MODE") or policy["gate_mode"]
    policy["fail_on_review"] = _bool_env("CI_RELEASE_GATE_FAIL_ON_REVIEW", bool(policy["fail_on_review"]))
    policy["strict_canary"] = _bool_env("CI_RELEASE_GATE_STRICT_CANARY", bool(policy["strict_canary"]))
    for env_name, key in [
        ("CI_RELEASE_GATE_MAX_RISK_SCORE", "max_release_risk_score"),
        ("CI_RELEASE_GATE_MAX_P0", "max_p0_issues"),
        ("CI_RELEASE_GATE_MAX_P1", "max_p1_issues"),
        ("CI_RELEASE_GATE_MIN_EVIDENCE", "min_evidence_completeness_for_blockers"),
    ]:
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        if key.startswith("max_p"):
            policy[key] = _safe_int(raw, int(policy[key]))
        else:
            policy[key] = _safe_float(raw, float(policy[key]))
    return policy


def _load_regression_ci_feedback(project: str, root: Path) -> dict[str, Any]:
    feedback = _read_json(root / "platform_outputs" / project / "regression_run" / "regression_ci_feedback.json", {})
    return feedback if isinstance(feedback, dict) else {}


def _load_or_build_release_dashboard(project: str, root: Path) -> dict[str, Any]:
    p = root / "platform_outputs" / project / "release_risk_dashboard" / "release_risk_dashboard.json"
    dashboard = _read_json(p, {})
    if isinstance(dashboard, dict) and dashboard.get("summary"):
        return dashboard
    try:
        return build_release_risk_dashboard(project, root)
    except Exception as exc:
        return {
            "project_id": project,
            "project_name": project,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": {
                "decision": "hold_for_review",
                "decision_label": "需要人工复核",
                "release_risk_score": 50,
                "issue_count": 0,
                "p0_issue_count": 0,
                "p1_issue_count": 0,
                "p0_p1_issue_count": 0,
                "needs_human_review": 1,
                "evidence_completeness": 0,
                "onboarding_ok": False,
            },
            "top_issues": [],
            "suggested_release_blockers": [],
            "private_leak_check": {"passed": True},
            "ci_gate_input_error": str(exc),
        }


def _gate_rules(dashboard: dict[str, Any], policy: dict[str, Any], regression_feedback: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = dashboard.get("summary") if isinstance(dashboard.get("summary"), dict) else {}
    release_decision = str(summary.get("decision") or "hold_for_review")
    risk_score = _safe_float(summary.get("release_risk_score"))
    p0_count = _safe_int(summary.get("p0_issue_count"))
    p1_count = _safe_int(summary.get("p1_issue_count"))
    blocker_count = _safe_int(summary.get("blocker_issue_count"))
    needs_review = _safe_int(summary.get("needs_human_review"))
    evidence = _safe_float(summary.get("evidence_completeness"))
    leak_ok = bool((dashboard.get("private_leak_check") or {}).get("passed", True))
    input_error = dashboard.get("ci_gate_input_error")

    hard_fail_reasons: list[str] = []
    review_reasons: list[str] = []
    warning_reasons: list[str] = []

    if input_error:
        review_reasons.append("发布风险看板不可用，CI 门禁已降级为人工复核。")
    if not leak_ok:
        hard_fail_reasons.append("私有数据隔离检查未通过。")
    if release_decision == "block_release":
        hard_fail_reasons.append("发布风险看板建议阻断发布。")
    regression_feedback = regression_feedback or {}
    regression_gate_status = str(regression_feedback.get("gate_status") or "")
    if regression_gate_status == "failed":
        hard_fail_reasons.append("回归执行存在 P0/P1 失败，CI 门禁阻断发布。")
    elif regression_gate_status == "manual_approval_required":
        review_reasons.append("回归执行存在 P2 失败或无法自动判定项，需要人工审批。")
    if p0_count > int(policy["max_p0_issues"]):
        hard_fail_reasons.append(f"P0 疑似问题数量 {p0_count} 超过阈值 {policy['max_p0_issues']}。")
    if risk_score > float(policy["max_release_risk_score"]):
        hard_fail_reasons.append(f"发布风险分 {risk_score} 超过阈值 {policy['max_release_risk_score']}。")
    if blocker_count > 0 and evidence >= float(policy["min_evidence_completeness_for_blockers"]):
        hard_fail_reasons.append(f"存在 {blocker_count} 个证据较完整的疑似发布阻断项。")

    if release_decision == "hold_for_review" or needs_review > 0:
        review_reasons.append("存在待 QA 复核的问题，需要人工审批后再发布。")
    if p1_count > int(policy["max_p1_issues"]):
        review_reasons.append(f"P1 疑似问题数量 {p1_count} 超过人工复核阈值 {policy['max_p1_issues']}。")
    if release_decision == "limited_canary":
        warning_reasons.append("发布风险看板仅建议小流量灰度。")

    if hard_fail_reasons:
        ci_status = "failed"
        exit_code = 2
    elif review_reasons:
        ci_status = "manual_approval_required"
        exit_code = 1 if bool(policy.get("fail_on_review")) else 0
    elif release_decision == "limited_canary":
        ci_status = "canary_allowed"
        exit_code = 1 if bool(policy.get("strict_canary")) else 0
    else:
        ci_status = "passed"
        exit_code = 0

    return {
        "ci_status": ci_status,
        "ci_status_label": CI_STATUS_LABELS.get(ci_status, ci_status),
        "exit_code": exit_code,
        "release_decision": release_decision,
        "release_decision_label": summary.get("decision_label") or release_decision,
        "hard_fail_reasons": hard_fail_reasons,
        "manual_review_reasons": review_reasons,
        "warning_reasons": warning_reasons,
        "metrics": {
            "release_risk_score": risk_score,
            "p0_issue_count": p0_count,
            "p1_issue_count": p1_count,
            "p0_p1_issue_count": _safe_int(summary.get("p0_p1_issue_count")),
            "blocker_issue_count": blocker_count,
            "needs_human_review": needs_review,
            "evidence_completeness": evidence,
            "issue_count": _safe_int(summary.get("issue_count")),
            "regression_failure_count": _safe_int((regression_feedback or {}).get("regression_failure_count")),
            "regression_needs_review_count": _safe_int((regression_feedback or {}).get("needs_review_count")),
        },
        "regression_ci_feedback": regression_feedback or {},
    }


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [m for m in PRIVATE_MARKERS if m.lower() in text]
    return {"passed": not leaks, "checked": True}


def _ci_examples(project: str) -> dict[str, str]:
    github = f"""name: AI Quality Release Gate
on: [push]
jobs:
  ai-release-gate:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Run AI release gate
        shell: pwsh
        run: |
          $env:REAL_PROJECT_ID='{project}'
          .\\RUN_CI_RELEASE_GATE.cmd
"""
    gitlab = f"""ai_release_gate:
  stage: test
  tags: [windows]
  script:
    - set REAL_PROJECT_ID={project}
    - .\\RUN_CI_RELEASE_GATE.cmd
  artifacts:
    when: always
    paths:
      - platform_outputs/{project}/ci_release_gate/
"""
    jenkins = f"""pipeline {{
  agent any
  stages {{
    stage('AI Release Gate') {{
      steps {{
        bat 'set REAL_PROJECT_ID={project} && RUN_CI_RELEASE_GATE.cmd'
      }}
    }}
  }}
  post {{
    always {{ archiveArtifacts artifacts: 'platform_outputs/{project}/ci_release_gate/**', allowEmptyArchive: true }}
  }}
}}
"""
    return {"github_actions_example.yml": github, "gitlab_ci_example.yml": gitlab, "jenkins_pipeline_example.groovy": jenkins}


def render_ci_release_gate_html(result: dict[str, Any]) -> str:
    gate = result.get("gate") or {}
    policy = result.get("policy") or {}
    metrics = gate.get("metrics") or {}
    status = str(gate.get("ci_status") or "manual_approval_required")
    cls = "danger" if status == "failed" else "warn" if status in {"manual_approval_required", "canary_allowed"} else "ok"
    reasons = []
    for title, items in [("阻断原因", gate.get("hard_fail_reasons") or []), ("人工复核原因", gate.get("manual_review_reasons") or []), ("警告", gate.get("warning_reasons") or [])]:
        if items:
            reasons.append(f"<h3>{_html_escape(title)}</h3><ul>" + "".join(f"<li>{_html_escape(x)}</li>" for x in items) + "</ul>")
    reason_html = "".join(reasons) or "<p>当前没有阻断原因。</p>"
    metric_cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in metrics.items())
    policy_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in policy.items())
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>CI Release Gate</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.badge{{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700}}.danger{{background:#fee2e2;color:#991b1b}}.warn{{background:#fef3c7;color:#92400e}}.ok{{background:#dcfce7;color:#166534}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{background:#fafafa;border:1px solid #e5e7eb;border-radius:14px;padding:14px}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:22px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<section class='hero'><span class='badge {cls}'>Phase28</span><h1>{_html_escape(result.get('project_name'))} · CI/CD 发布门禁</h1><p>CI 状态：<b>{_html_escape(gate.get('ci_status_label'))}</b> · Exit Code：<code>{_html_escape(gate.get('exit_code'))}</code></p><p>发布看板建议：{_html_escape(gate.get('release_decision_label'))}</p><p>私有数据隔离检查：<b>{_html_escape('passed' if (result.get('private_leak_check') or {}).get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>门禁指标</h2><div class='grid'>{metric_cards}</div></section>
<section class='panel'><h2>门禁结论</h2>{reason_html}</section>
<section class='panel'><h2>CI 策略</h2><table><tbody>{policy_rows}</tbody></table></section>
<section class='panel'><h2>流水线接入</h2><p>在 CI 中执行：<code>set REAL_PROJECT_ID={_html_escape(result.get('project_id'))} && RUN_CI_RELEASE_GATE.cmd</code></p><p>产物目录：<code>platform_outputs\\{_html_escape(result.get('project_id'))}\\ci_release_gate</code></p></section>
</body></html>"""


def build_ci_release_gate(project_id: str = "real_project_demo", root: Path | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    gate_policy = dict(DEFAULT_POLICY)
    gate_policy.update(policy or _load_policy_from_env())
    dashboard = _load_or_build_release_dashboard(project, root)
    regression_feedback = _load_regression_ci_feedback(project, root)
    gate = _gate_rules(dashboard, gate_policy, regression_feedback)
    result = {
        "phase": "phase28_ci_cd_release_gate",
        "project_id": project,
        "project_name": dashboard.get("project_name") or project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gate": gate,
        "policy": gate_policy,
        "release_dashboard_ref": f"platform_outputs/{project}/release_risk_dashboard/release_risk_dashboard.html",
        "regression_ci_feedback_ref": f"platform_outputs/{project}/regression_run/regression_ci_feedback.json" if regression_feedback else "",
        "artifacts": {
            "result_json": f"platform_outputs/{project}/ci_release_gate/ci_release_gate_result.json",
            "report_html": f"platform_outputs/{project}/ci_release_gate/ci_release_gate_report.html",
            "summary_json": f"platform_outputs/{project}/ci_release_gate/ci_release_gate_summary.json",
        },
        "governance": {
            "real_project_mode": True,
            "uses_release_risk_dashboard": True,
            "safe_for_ci": True,
            "does_not_require_benchmark_answers": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    out_dir = root / "platform_outputs" / project / "ci_release_gate"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "ci_release_gate_result.json", result)
    _write_json(out_dir / "ci_release_gate_summary.json", {"project_id": project, "gate": gate, "private_leak_check": result["private_leak_check"]})
    _write_text(out_dir / "ci_release_gate_report.html", render_ci_release_gate_html(result))
    for name, content in _ci_examples(project).items():
        _write_text(out_dir / name, content)
    _write_json(ws_dir / "ci_release_gate_result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    result = build_ci_release_gate(project)
    print(json.dumps({"ok": True, "project_id": result["project_id"], "gate": result.get("gate"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    if _bool_env("CI_RELEASE_GATE_NO_FAIL", False):
        return 0
    return int((result.get("gate") or {}).get("exit_code") or 0)


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
