from __future__ import annotations

"""Compatibility discovery adapters for enterprise Web/API systems."""

from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
try:
    from .phase105_frontend_preview_acceptance import run_frontend_preview_acceptance
except ImportError:
    run_frontend_preview_acceptance = None
from .real_project_onboarding import ROOT, config_paths


def generate_compatibility_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    del openapi, root
    environment_class = str(cfg.get("environment_class") or "sandbox")
    deployment_mode = str(cfg.get("deployment_mode") or "private_deployment")
    return [
        normalize_defect_signal(
            {
                "probe_id": "COMPAT_0001",
                "title": "环境与部署模式兼容性检查",
                "defect_family": "compatibility",
                "risk_type": "compatibility",
                "severity": "P2",
                "source": "compatibility_adapter",
                "expected": "不同部署模式与环境等级下的关键契约应保持兼容",
                "actual": "待验证环境差异与配置兼容性",
                "status": "planned_probe",
                "confidence": 0.3,
                "evidence": {"environment_class": environment_class, "deployment_mode": deployment_mode, "project_id": project_id},
            },
            signal_kind="probe",
            default_source="compatibility_adapter",
            default_status="planned_probe",
            default_confidence=0.3,
        ),
        normalize_defect_signal(
            {
                "probe_id": "COMPAT_0002",
                "title": "时区/地区化与 schema 版本兼容检查",
                "defect_family": "compatibility",
                "risk_type": "api_backward_compatibility",
                "severity": "P2",
                "source": "compatibility_adapter",
                "expected": "时区、locale 与 schema 版本变化不应破坏关键业务行为",
                "actual": "待验证",
                "status": "planned_probe",
                "confidence": 0.28,
                "evidence": {"project_id": project_id},
            },
            signal_kind="probe",
            default_source="compatibility_adapter",
            default_status="planned_probe",
            default_confidence=0.28,
        ),
    ]


def collect_compatibility_issues(
    cfg: dict[str, Any],
    *,
    openapi: dict[str, Any] | None = None,
    project_id: str = "",
    root: Path | None = None,
    scenario: str = "",
) -> list[dict[str, Any]]:
    # Industry-agnostic compatibility checks — driven only by the declared target
    # configuration and source material, never by a hardcoded industry scenario.
    # The demo-harness frontend-preview branch below runs only when a concrete
    # project bundle is explicitly declared; it is never assumed.
    issues: list[dict[str, Any]] = []
    version = str((openapi or {}).get("openapi") or "")
    if version and version not in {"3.0.0", "3.0.3"}:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": "ISSUE_COMPAT_0001",
                    "title": "OpenAPI 版本偏离主兼容基线",
                    "defect_family": "compatibility",
                    "risk_type": "api_backward_compatibility",
                    "severity": "P2",
                    "confidence": 0.66,
                    "status": "needs_human_review",
                    "source": "compatibility_adapter",
                    "expected": "OpenAPI 版本应落在当前导出与验收链支持范围内",
                    "actual": f"当前版本={version}",
                    "evidence": {"openapi_version": version},
                },
                signal_kind="issue",
                default_source="compatibility_adapter",
            )
        )
    if str(cfg.get("environment_class") or "").lower() == "prod_like" and str(cfg.get("deployment_mode") or "").lower() == "public_saas":
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_COMPAT_{len(issues)+1:04d}",
                    "title": "生产相似环境需要显式兼容性矩阵保护",
                    "defect_family": "compatibility",
                    "risk_type": "compatibility",
                    "severity": "P2",
                    "confidence": 0.58,
                    "status": "needs_human_review",
                    "source": "compatibility_adapter",
                    "expected": "prod_like/public_saas 组合下应有更严格的兼容性基线与环境矩阵",
                    "actual": "当前仅发现环境组合，未发现兼容性矩阵证据",
                    "evidence": {"deployment_mode": cfg.get("deployment_mode"), "environment_class": cfg.get("environment_class")},
                },
                signal_kind="issue",
                default_source="compatibility_adapter",
            )
        )
    if not project_id:
        return issues  # no declared demo bundle; generic checks already recorded
    root = root or ROOT
    paths = config_paths(project_id, root)
    project_output_root = root / "platform_outputs" / project_id
    bundle_dir = project_output_root / "phase105_frontend_preview_bundle"
    output_dir = project_output_root / "phase105_frontend_preview_acceptance"
    if paths["output_dir"].exists():
        bundle_dir = paths["output_dir"] / "phase105_frontend_preview_bundle"
        output_dir = paths["output_dir"] / "phase105_frontend_preview_acceptance"
    try:
        report = run_frontend_preview_acceptance(
            bundle_dir=bundle_dir,
            output_dir=output_dir,
            scenario=scenario,
            build_first=True,
            min_score=int(cfg.get("frontend_preview_min_score") or 90),
            host=str(cfg.get("preview_host") or "127.0.0.1"),
            port=int(cfg.get("preview_port") or 8795),
        ).to_dict()
        for check in report.get("checks") or []:
            if not isinstance(check, dict) or check.get("passed"):
                continue
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_COMPAT_{len(issues)+1:04d}",
                        "title": f"预览站点/兼容验收失败：{check.get('key')}",
                        "defect_family": "compatibility",
                        "risk_type": "compatibility",
                        "severity": "P1" if str(check.get("severity") or "").lower() == "critical" else "P2",
                        "confidence": 0.8,
                        "status": "needs_human_review",
                        "source": "compatibility_adapter",
                        "expected": "预览站点、只读 API、静态页面与交付物在当前环境下应保持一致可访问",
                        "actual": check.get("detail") or "frontend preview acceptance failed",
                        "evidence": {"check": check, "bundle_dir": str(bundle_dir), "artifacts": report.get("artifacts") or {}},
                    },
                    signal_kind="issue",
                    default_source="compatibility_adapter",
                )
            )
    except Exception as exc:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_COMPAT_{len(issues)+1:04d}",
                    "title": "预览站点/兼容验收执行失败",
                    "defect_family": "compatibility",
                    "risk_type": "compatibility",
                    "severity": "P2",
                    "confidence": 0.5,
                    "status": "needs_human_review",
                    "source": "compatibility_adapter",
                    "expected": "预览站点兼容验收应可执行并返回完整报告",
                    "actual": str(exc),
                    "evidence": {"bundle_dir": str(bundle_dir), "output_dir": str(output_dir)},
                },
                signal_kind="issue",
                default_source="compatibility_adapter",
            )
        )
    return issues
