from __future__ import annotations

"""Adapters that turn contract acceptance outputs into discovery signals."""

from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
try:
    from .phase104_api_contract_acceptance import validate_contract_artifacts
except ImportError:
    validate_contract_artifacts = None
from .real_project_onboarding import ROOT, _load_json, config_paths


def generate_api_contract_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
    max_count: int = 6,
) -> list[dict[str, Any]]:
    paths = openapi.get("paths") if isinstance(openapi, dict) else {}
    version = str(openapi.get("openapi") or "") if isinstance(openapi, dict) else ""
    probes: list[dict[str, Any]] = []
    if not isinstance(paths, dict):
        return probes
    for idx, (path, methods) in enumerate(paths.items(), start=1):
        if len(probes) >= max_count:
            break
        method_names = [str(name).upper() for name in methods.keys()] if isinstance(methods, dict) else ["GET"]
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": f"API_CONTRACT_{idx:04d}",
                    "title": f"接口契约一致性检查：{path}",
                    "defect_family": "api_contract",
                    "risk_type": "api_contract",
                    "severity": "P1",
                    "source": "api_contract_acceptance",
                    "method": method_names[0] if method_names else "GET",
                    "path": str(path),
                    "expected": f"OpenAPI {version or 'contract'} 与导出 client / runtime 行为保持一致",
                    "actual": "待验证接口契约、导出客户端和运行时 envelope 是否一致",
                    "status": "planned_probe",
                    "confidence": 0.4,
                    "evidence": {"openapi_version": version, "method_count": len(method_names), "project_id": project_id},
                },
                signal_kind="probe",
                default_source="api_contract_acceptance",
                default_status="planned_probe",
                default_confidence=0.4,
            )
        )
    if not probes:
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": "API_CONTRACT_0000",
                    "title": "接口契约导出与运行时一致性检查",
                    "defect_family": "api_contract",
                    "risk_type": "api_contract",
                    "severity": "P1",
                    "source": "api_contract_acceptance",
                    "status": "planned_probe",
                    "confidence": 0.35,
                    "expected": "导出的 OpenAPI、文档、前端 client 与本地运行时保持一致",
                    "actual": "待验证",
                    "evidence": {"project_id": project_id},
                },
                signal_kind="probe",
                default_source="api_contract_acceptance",
                default_status="planned_probe",
                default_confidence=0.35,
            )
        )
    return probes


def _candidate_contract_dirs(project_id: str, root: Path | None = None) -> list[Path]:
    root = root or ROOT
    paths = config_paths(project_id, root)
    project_output_root = root / "platform_outputs" / project_id
    candidates = [
        project_output_root / "phase104_api_contract",
        project_output_root / "phase104_contract",
        paths["output_dir"] / "phase104_api_contract",
        paths["workspace_dir"] / "phase104_api_contract",
    ]
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            ordered.append(path)
            seen.add(key)
    return ordered


def collect_api_contract_issues(
    project_id: str,
    root: Path | None = None,
    *,
    scenario: str = "",
) -> list[dict[str, Any]]:
    root = root or ROOT
    issues: list[dict[str, Any]] = []
    for contract_dir in _candidate_contract_dirs(project_id, root):
        if not contract_dir.exists():
            continue
        try:
            report = validate_contract_artifacts(contract_dir, scenario=scenario, live_smoke=False).to_dict()
        except Exception as exc:
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_API_CONTRACT_{len(issues)+1:04d}",
                        "title": "接口契约验收执行失败",
                        "defect_family": "api_contract",
                        "risk_type": "api_contract",
                        "severity": "P2",
                        "confidence": 0.45,
                        "status": "needs_human_review",
                        "source": "api_contract_acceptance",
                        "expected": "接口契约验收应可执行并生成完整报告",
                        "actual": str(exc),
                        "evidence": {"contract_dir": str(contract_dir)},
                    },
                    signal_kind="issue",
                    default_source="api_contract_acceptance",
                )
            )
            continue
        for check in report.get("failed_checks") or []:
            if not isinstance(check, dict):
                continue
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_API_CONTRACT_{len(issues)+1:04d}",
                        "title": f"接口契约验收失败：{check.get('title')}",
                        "defect_family": "api_contract",
                        "risk_type": "api_contract",
                        "severity": "P1" if str(check.get("severity") or "critical") == "critical" else "P2",
                        "confidence": 0.82,
                        "status": "needs_human_review",
                        "source": "api_contract_acceptance",
                        "expected": "API 合同、OpenAPI、frontend client 与 manifest 校验通过",
                        "actual": check.get("detail") or check.get("suggested_action") or "contract acceptance failed",
                        "evidence": {
                            "contract_dir": str(contract_dir),
                            "check": check,
                            "artifacts": report.get("artifacts") or {},
                        },
                    },
                    signal_kind="issue",
                    default_source="api_contract_acceptance",
                )
            )
        if issues:
            return issues
    fallback = _load_json(config_paths(project_id, root)["input_dir"] / "openapi.json", {})
    if isinstance(fallback, dict) and fallback.get("paths"):
        return []
    return issues

