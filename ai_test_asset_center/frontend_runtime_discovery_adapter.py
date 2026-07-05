from __future__ import annotations

"""Adapters that surface frontend runtime artifacts as discovery signals."""

from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
try:
    from .phase104_frontend_runtime_smoke import run_frontend_runtime_smoke
except ImportError:
    run_frontend_runtime_smoke = None
from .real_project_onboarding import ROOT, config_paths


def generate_frontend_runtime_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    scenario = str(cfg.get("scenario") or cfg.get("project_name") or project_id)
    return [
        normalize_defect_signal(
            {
                "probe_id": "FRONTEND_RUNTIME_0001",
                "title": "前端执行运行态与风险证据回流检查",
                "defect_family": "ui",
                "risk_type": "frontend_execution_runtime",
                "severity": "P1",
                "source": "frontend_runtime_smoke",
                "route": "/dashboard",
                "path": "/dashboard",
                "expected": "当前 Vite 前端主页面与关键读链路可被完整装配并连通后端契约",
                "actual": "待验证 dashboard/readiness/report 等当前主链路完整性",
                "status": "planned_probe",
                "confidence": 0.4,
                "evidence": {"scenario": scenario, "project_id": project_id},
            },
            signal_kind="probe",
            default_source="frontend_runtime_smoke",
            default_status="planned_probe",
            default_confidence=0.4,
        ),
        normalize_defect_signal(
            {
                "probe_id": "FRONTEND_RUNTIME_0002",
                "title": "前端交互反馈与证据跳转 UX 检查",
                "defect_family": "uiux",
                "risk_type": "frontend_ux",
                "severity": "P2",
                "source": "frontend_runtime_smoke",
                "route": "/findings",
                "path": "/findings",
                "expected": "风险信号、证据跳转与用户反馈在当前前端主页面中完整且可理解",
                "actual": "待验证 UX 反馈完整性与主任务可达性",
                "status": "planned_probe",
                "confidence": 0.35,
                "evidence": {"scenario": scenario, "project_id": project_id},
            },
            signal_kind="probe",
            default_source="frontend_runtime_smoke",
            default_status="planned_probe",
            default_confidence=0.35,
        ),
    ]


def _runtime_workspace_and_output(project_id: str, root: Path | None = None) -> tuple[Path, Path]:
    root = root or ROOT
    paths = config_paths(project_id, root)
    project_output_root = root / "platform_outputs" / project_id
    workspace_dir = project_output_root / "phase104_frontend_workspace"
    output_dir = project_output_root / "phase104_frontend_runtime_smoke"
    if paths["workspace_dir"].exists():
        workspace_dir = paths["workspace_dir"] / "phase104_frontend_workspace"
    return workspace_dir, output_dir


def collect_frontend_runtime_issues(
    project_id: str,
    root: Path | None = None,
    *,
    scenario: str = "manufacturing",
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg if isinstance(cfg, dict) else {}
    issues: list[dict[str, Any]] = []
    workspace_dir, output_dir = _runtime_workspace_and_output(project_id, root)
    try:
        smoke_report = run_frontend_runtime_smoke(
            workspace_dir=workspace_dir,
            output_dir=output_dir,
            scenario=scenario,
            api_base_url=str(cfg.get("base_url") or "http://127.0.0.1:8790"),
            build_workspace=True,
        ).to_dict()
        for step in smoke_report.get("steps") or []:
            if not isinstance(step, dict) or step.get("passed"):
                continue
            defect_family = "uiux" if str(step.get("key") or "") in {"create_project", "generate_test_plan", "generate_report"} else "ui"
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_FRONTEND_RUNTIME_{len(issues)+1:04d}",
                        "title": f"浏览器式运行时链路失败：{step.get('key')}",
                        "defect_family": defect_family,
                        "risk_type": "frontend_ux" if defect_family == "uiux" else "frontend_execution_runtime",
                        "severity": "P1" if str(step.get("status") or 0) in {"500", "502", "503"} else "P2",
                        "confidence": 0.84,
                        "status": "needs_human_review",
                        "source": "frontend_runtime_smoke",
                        "route": str(step.get("path") or "/"),
                        "path": str(step.get("path") or "/"),
                        "expected": "浏览器式运行时主链路应可顺利联通并返回安全 envelope",
                        "actual": step.get("detail") or "frontend runtime smoke failed",
                        "evidence": {"step": step, "report": {"score": smoke_report.get("score"), "redaction_status": smoke_report.get("redaction_status"), "failed_step_count": smoke_report.get("failed_step_count")}},
                    },
                    signal_kind="issue",
                    default_source="frontend_runtime_smoke",
                )
            )
    except Exception as exc:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_FRONTEND_RUNTIME_{len(issues)+1:04d}",
                    "title": "浏览器式前端运行时 smoke 执行失败",
                    "defect_family": "ui",
                    "risk_type": "frontend_execution_runtime",
                    "severity": "P2",
                    "confidence": 0.5,
                    "status": "needs_human_review",
                    "source": "frontend_runtime_smoke",
                    "route": "/",
                    "path": "/",
                    "expected": "浏览器式前端运行时 smoke 应可执行并返回完整报告",
                    "actual": str(exc),
                    "evidence": {"workspace_dir": str(workspace_dir), "output_dir": str(output_dir)},
                },
                signal_kind="issue",
                default_source="frontend_runtime_smoke",
            )
        )
    return issues
