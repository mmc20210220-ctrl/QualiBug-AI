from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
from .enterprise_testops_control_plane import load_enterprise_testops_control_plane
from .real_project_onboarding import ROOT

_SENSITIVE_PATH_RE = re.compile(
    r"/(?:admin|internal|tenant|org|user|account|customer|patient|employee|member|profile|identity|auth|token|secret|audit|export|download|report|finance|payment|refund|invoice)",
    re.I,
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|session|api[_-]?key|access[_-]?token|refresh[_-]?token|email|phone|mobile|idcard|id_card|bank|card|ssn|身份证|手机号|邮箱|银行卡|密码|令牌|密钥)",
    re.I,
)
_AUDIT_PATH_RE = re.compile(r"(?:audit|log|trace|history|approval|export)", re.I)


def _iter_json_schemas(op: dict[str, Any]) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    request_body = op.get("requestBody") if isinstance(op.get("requestBody"), dict) else {}
    request_content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
    for item in request_content.values():
        if isinstance(item, dict) and isinstance(item.get("schema"), dict):
            schemas.append(item["schema"])
    responses = op.get("responses") if isinstance(op.get("responses"), dict) else {}
    for response in responses.values():
        if not isinstance(response, dict):
            continue
        content = response.get("content") if isinstance(response.get("content"), dict) else {}
        for item in content.values():
            if isinstance(item, dict) and isinstance(item.get("schema"), dict):
                schemas.append(item["schema"])
    return schemas


def _iter_sensitive_fields(schema: Any) -> list[str]:
    fields: list[str] = []
    if isinstance(schema, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for name, child in properties.items():
            if _SENSITIVE_FIELD_RE.search(str(name)):
                fields.append(str(name))
            fields.extend(_iter_sensitive_fields(child))
        for key in ("allOf", "anyOf", "oneOf"):
            for child in schema.get(key) or []:
                fields.extend(_iter_sensitive_fields(child))
        if isinstance(schema.get("items"), dict):
            fields.extend(_iter_sensitive_fields(schema.get("items")))
    return fields[:12]


def _operation_sensitive_fields(op: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for schema in _iter_json_schemas(op):
        fields.extend(_iter_sensitive_fields(schema))
    deduped: list[str] = []
    seen: set[str] = set()
    for field in fields:
        key = field.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(field)
    return deduped[:12]


def generate_privacy_compliance_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
    *,
    enterprise_testops_control_plane: dict[str, Any] | None = None,
    max_count: int = 12,
) -> list[dict[str, Any]]:
    del cfg
    root = root or ROOT
    control = enterprise_testops_control_plane if isinstance(enterprise_testops_control_plane, dict) else load_enterprise_testops_control_plane(project_id, root)
    paths = openapi.get("paths") if isinstance(openapi, dict) and isinstance(openapi.get("paths"), dict) else {}
    probes: list[dict[str, Any]] = []
    saw_sensitive_operation = False
    for path, methods in paths.items():
        if len(probes) >= max_count:
            break
        if not isinstance(methods, dict):
            continue
        path_str = str(path or "")
        for method, op in methods.items():
            if len(probes) >= max_count:
                break
            if not isinstance(op, dict):
                continue
            method_upper = str(method or "").upper()
            if method_upper not in {"GET", "HEAD"}:
                continue
            sensitive_fields = _operation_sensitive_fields(op)
            if not sensitive_fields and not _SENSITIVE_PATH_RE.search(path_str):
                continue
            saw_sensitive_operation = True
            probes.append(
                normalize_defect_signal(
                    {
                        "probe_id": f"PRIVACY_PROBE_{len(probes)+1:04d}",
                        "title": f"敏感数据最小暴露与脱敏检查：{method_upper} {path_str}",
                        "defect_family": "privacy_compliance",
                        "risk_type": "audit_privacy_probe",
                        "severity": "P1" if sensitive_fields else "P2",
                        "source": "audit_privacy_probe",
                        "method": method_upper,
                        "path": path_str,
                        "expected": "最小权限读取下，不应返回未脱敏敏感字段；若返回敏感信息，必须具备明确授权和审计依据",
                        "actual": "待验证敏感字段暴露、脱敏策略与审计链留痕是否一致",
                        "status": "planned_probe",
                        "confidence": 0.42,
                        "evidence": {"project_id": project_id, "sensitive_fields": sensitive_fields},
                    },
                    signal_kind="probe",
                    default_source="audit_privacy_probe",
                    default_status="planned_probe",
                    default_confidence=0.42,
                )
            )
    if saw_sensitive_operation:
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": f"PRIVACY_PROBE_{len(probes)+1:04d}",
                    "title": "OpenAPI 隐私与脱敏静态治理检查",
                    "defect_family": "privacy_compliance",
                    "risk_type": "privacy_compliance",
                    "severity": "P2",
                    "source": "openapi_static_security_scan",
                    "method": "GET",
                    "path": "/openapi",
                    "expected": "敏感路径、导出接口与响应 schema 应满足最小暴露、脱敏与审计留痕约束",
                    "actual": "待检查 OpenAPI 中的隐私合规缺口",
                    "status": "planned_probe",
                    "confidence": 0.36,
                    "evidence": {"project_id": project_id},
                },
                signal_kind="probe",
                default_source="openapi_static_security_scan",
                default_status="planned_probe",
                default_confidence=0.36,
            )
        )
    security = (control.get("security_audit_report") or {}) if isinstance(control, dict) else {}
    if security:
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": f"PRIVACY_PROBE_{len(probes)+1:04d}",
                    "title": "Enterprise TestOps 审计链与隐私治理检查",
                    "defect_family": "privacy_compliance",
                    "risk_type": "privacy_compliance",
                    "severity": "P2",
                    "source": "enterprise_testops_control_plane",
                    "method": "GET",
                    "path": "/enterprise-testops/security",
                    "expected": "关键风险操作应生成完整审计链，敏感数据处理策略应可验证",
                    "actual": "待检查 security_audit_report 中的审计链与敏感数据治理状态",
                    "status": "planned_probe",
                    "confidence": 0.38,
                    "evidence": {"audit_event_count": len(security.get("audit_events") or [])},
                },
                signal_kind="probe",
                default_source="enterprise_testops_control_plane",
                default_status="planned_probe",
                default_confidence=0.38,
            )
        )
    return probes[:max_count]


def collect_privacy_compliance_issues(
    openapi: dict[str, Any],
    *,
    project_id: str,
    root: Path | None = None,
    enterprise_testops_control_plane: dict[str, Any] | None = None,
    max_count: int = 40,
) -> list[dict[str, Any]]:
    root = root or ROOT
    control = enterprise_testops_control_plane if isinstance(enterprise_testops_control_plane, dict) else load_enterprise_testops_control_plane(project_id, root)
    paths = openapi.get("paths") if isinstance(openapi, dict) and isinstance(openapi.get("paths"), dict) else {}
    issues: list[dict[str, Any]] = []
    for path, methods in paths.items():
        if len(issues) >= max_count:
            break
        if not isinstance(methods, dict):
            continue
        path_str = str(path or "")
        for method, op in methods.items():
            if len(issues) >= max_count:
                break
            if not isinstance(op, dict):
                continue
            method_upper = str(method or "").upper()
            if method_upper not in {"GET", "HEAD"}:
                continue
            sensitive_fields = _operation_sensitive_fields(op)
            if not sensitive_fields:
                continue
            title = f"敏感字段暴露风险：{method_upper} {path_str}"
            expected = "只读接口返回中不应暴露未脱敏敏感字段；如确需返回，必须与角色、审计和脱敏策略一致"
            actual = "OpenAPI 响应 schema 中检测到敏感字段，存在未最小化暴露或脱敏失效风险"
            severity = "P1"
            if _AUDIT_PATH_RE.search(path_str):
                title = f"导出/审计接口敏感信息暴露风险：{method_upper} {path_str}"
                actual = "导出或审计相关接口的响应 schema 中包含敏感字段，存在批量暴露和二次传播风险"
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_PRIVACY_{len(issues)+1:04d}",
                        "title": title,
                        "defect_family": "privacy_compliance",
                        "risk_type": "sensitive_field_leak",
                        "severity": severity,
                        "confidence": 0.8,
                        "status": "needs_human_review",
                        "source": "openapi_static_security_scan",
                        "method": method_upper,
                        "path": path_str,
                        "expected": expected,
                        "actual": actual,
                        "evidence": {
                            "sensitive_fields": sensitive_fields,
                            "operationId": str(op.get("operationId") or ""),
                            "summary": str(op.get("summary") or ""),
                        },
                    },
                    signal_kind="issue",
                    default_source="openapi_static_security_scan",
                )
            )
    security = (control.get("security_audit_report") or {}) if isinstance(control, dict) else {}
    audit_chain = security.get("audit_chain_integrity") if isinstance(security.get("audit_chain_integrity"), dict) else {}
    audit_events = security.get("audit_events") if isinstance(security.get("audit_events"), list) else []
    risk_operations = security.get("risk_operations") if isinstance(security.get("risk_operations"), list) else []
    credential_policy = security.get("credential_policy") if isinstance(security.get("credential_policy"), dict) else {}
    if len(issues) < max_count and audit_chain and not bool(audit_chain.get("passed")):
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_PRIVACY_{len(issues)+1:04d}",
                    "title": "审计链完整性失败",
                    "defect_family": "privacy_compliance",
                    "risk_type": "audit_log_missing",
                    "severity": "P1",
                    "confidence": 0.84,
                    "status": "needs_human_review",
                    "source": "enterprise_testops_control_plane",
                    "method": "GET",
                    "path": "/enterprise-testops/security",
                    "expected": "审计事件链应可验证 previous_hash/event_hash，确保敏感操作可追溯",
                    "actual": "security_audit_report 显示审计链校验失败，关键操作的追溯性不足",
                    "evidence": {"audit_chain_integrity": audit_chain, "audit_event_count": len(audit_events)},
                },
                signal_kind="issue",
                default_source="enterprise_testops_control_plane",
            )
        )
    if len(issues) < max_count and risk_operations and not audit_events:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_PRIVACY_{len(issues)+1:04d}",
                    "title": "高风险操作缺少审计留痕",
                    "defect_family": "privacy_compliance",
                    "risk_type": "audit_log_missing",
                    "severity": "P1",
                    "confidence": 0.78,
                    "status": "needs_human_review",
                    "source": "enterprise_testops_control_plane",
                    "method": "GET",
                    "path": "/enterprise-testops/security",
                    "expected": "涉及敏感数据和写操作的流程应生成可验证审计日志",
                    "actual": "security_audit_report 中存在风险操作，但未发现对应 audit_events",
                    "evidence": {"risk_operations": risk_operations, "audit_event_count": 0},
                },
                signal_kind="issue",
                default_source="enterprise_testops_control_plane",
            )
        )
    if len(issues) < max_count and bool(credential_policy.get("plaintext_credentials_persisted")):
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_PRIVACY_{len(issues)+1:04d}",
                    "title": "凭据脱敏策略失效",
                    "defect_family": "privacy_compliance",
                    "risk_type": "desensitization_failure",
                    "severity": "P0",
                    "confidence": 0.86,
                    "status": "needs_human_review",
                    "source": "enterprise_testops_control_plane",
                    "method": "GET",
                    "path": "/enterprise-testops/security",
                    "expected": "凭据应仅以引用形式存在，派生报告与测试资产必须默认脱敏",
                    "actual": "security_audit_report 显示存在明文凭据持久化风险",
                    "evidence": {"credential_policy": credential_policy},
                },
                signal_kind="issue",
                default_source="enterprise_testops_control_plane",
            )
        )
    return issues[:max_count]
