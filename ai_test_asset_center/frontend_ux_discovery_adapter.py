from __future__ import annotations

"""UX-oriented discovery probes for enterprise frontend flows."""

from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
try:
    from .phase105_frontend_interaction_acceptance import run_frontend_interaction_acceptance
except ImportError:
    run_frontend_interaction_acceptance = None
from .real_project_onboarding import ROOT, config_paths


def generate_frontend_ux_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    del openapi, root
    project_name = str(cfg.get("project_name") or project_id)
    return [
        normalize_defect_signal(
            {
                "probe_id": "FRONTEND_UX_0001",
                "title": f"{project_name} 主任务完成路径 UX 检查",
                "defect_family": "uiux",
                "risk_type": "frontend_ux",
                "severity": "P2",
                "source": "frontend_ux_adapter",
                "route": "/",
                "path": "/",
                "expected": "关键任务路径有明确入口、反馈与完成闭环",
                "actual": "待验证主任务路径是否存在断链、缺反馈或误导状态",
                "status": "planned_probe",
                "confidence": 0.32,
                "evidence": {"project_id": project_id},
            },
            signal_kind="probe",
            default_source="frontend_ux_adapter",
            default_status="planned_probe",
            default_confidence=0.32,
        )
    ]


def collect_frontend_ux_issues(
    project_id: str,
    root: Path | None = None,
    *,
    scenario: str = "manufacturing",
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg if isinstance(cfg, dict) else {}
    root = root or ROOT
    paths = config_paths(project_id, root)
    project_output_root = root / "platform_outputs" / project_id
    hub_dir = project_output_root / "phase105_frontend_experience_hub_v2"
    output_dir = project_output_root / "phase105_frontend_interaction_acceptance"
    if paths["output_dir"].exists():
        hub_dir = paths["output_dir"] / "phase105_frontend_experience_hub_v2"
        output_dir = paths["output_dir"] / "phase105_frontend_interaction_acceptance"
    issues: list[dict[str, Any]] = []
    try:
        result = run_frontend_interaction_acceptance(
            hub_dir=hub_dir,
            output_dir=output_dir,
            build_first=True,
            scenario=scenario,
            api_base_url=str(cfg.get("base_url") or "http://127.0.0.1:8790"),
            min_score=int(cfg.get("frontend_interaction_min_score") or 90),
        )
        acceptance = result.get("acceptance") if isinstance(result, dict) else {}
        for check in acceptance.get("checks") or []:
            if not isinstance(check, dict) or check.get("passed"):
                continue
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_FRONTEND_UX_{len(issues)+1:04d}",
                        "title": f"前端交互/体验验收失败：{check.get('key')}",
                        "defect_family": "uiux",
                        "risk_type": "frontend_ux",
                        "severity": "P1" if str(check.get("severity") or "").lower() == "critical" else "P2",
                        "confidence": 0.82,
                        "status": "needs_human_review",
                        "source": "frontend_ux_adapter",
                        "route": "/",
                        "path": "/",
                        "expected": "核心页面应具备导航闭环、关键动作、业务文案和下一步引导",
                        "actual": check.get("detail") or "frontend interaction acceptance failed",
                        "evidence": {"check": check, "hub_dir": str(hub_dir), "score": acceptance.get("score")},
                    },
                    signal_kind="issue",
                    default_source="frontend_ux_adapter",
                )
            )
    except Exception as exc:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_FRONTEND_UX_{len(issues)+1:04d}",
                    "title": "前端交互/体验验收执行失败",
                    "defect_family": "uiux",
                    "risk_type": "frontend_ux",
                    "severity": "P2",
                    "confidence": 0.48,
                    "status": "needs_human_review",
                    "source": "frontend_ux_adapter",
                    "route": "/",
                    "path": "/",
                    "expected": "前端交互/体验验收应可执行并产出可审计报告",
                    "actual": str(exc),
                    "evidence": {"hub_dir": str(hub_dir), "output_dir": str(output_dir)},
                },
                signal_kind="issue",
                default_source="frontend_ux_adapter",
            )
        )
    return issues
