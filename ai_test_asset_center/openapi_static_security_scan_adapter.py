from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal


_SENSITIVE_PATH_RE = re.compile(r"/(?:admin|internal|tenant|org|user|account|role|permission|auth|login|token|secret|key|audit|finance|payment|refund|invoice|settle)", re.I)
_PUBLIC_PATH_RE = re.compile(r"/(?:health|ready|readiness|status|ping|metrics|public|docs?|swagger)", re.I)
_SENSITIVE_FIELD_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session|idcard|bank|银行卡|身份证|密码|密钥|令牌)", re.I)


def _op_security(openapi: dict[str, Any], op: dict[str, Any]) -> Any:
    security = op.get("security")
    if security is None:
        security = openapi.get("security")
    return security


def _has_request_body(op: dict[str, Any]) -> bool:
    body = op.get("requestBody") if isinstance(op.get("requestBody"), dict) else {}
    content = body.get("content") if isinstance(body.get("content"), dict) else {}
    return bool(content)


def _iter_sensitive_fields(schema: Any) -> list[str]:
    fields: list[str] = []
    if isinstance(schema, dict):
        if schema.get("properties") and isinstance(schema.get("properties"), dict):
            for name, child in schema["properties"].items():
                if _SENSITIVE_FIELD_RE.search(str(name)):
                    fields.append(str(name))
                fields.extend(_iter_sensitive_fields(child))
        for key in ("allOf", "anyOf", "oneOf"):
            if isinstance(schema.get(key), list):
                for child in schema[key]:
                    fields.extend(_iter_sensitive_fields(child))
    if isinstance(schema, list):
        for item in schema:
            fields.extend(_iter_sensitive_fields(item))
    return fields[:12]


def generate_openapi_static_security_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    del openapi, cfg, root
    return [
        normalize_defect_signal(
            {
                "probe_id": "OPENAPI_SAST_0001",
                "title": "OpenAPI 静态安全扫描（无代码依赖）",
                "defect_family": "security_boundary",
                "risk_type": "openapi_security_static_scan",
                "severity": "P2",
                "source": "openapi_static_security_scan",
                "method": "GET",
                "path": "/openapi",
                "expected": "敏感接口应声明安全方案；GET 不应携带敏感请求体；敏感字段应走安全传输与最小暴露",
                "actual": "待扫描 OpenAPI 中潜在的安全边界缺口",
                "status": "planned_probe",
                "confidence": 0.35,
                "evidence": {"project_id": project_id},
            },
            signal_kind="probe",
            default_source="openapi_static_security_scan",
            default_status="planned_probe",
            default_confidence=0.35,
        )
    ]


def collect_openapi_static_security_issues(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(openapi, dict):
        return []
    paths = openapi.get("paths") if isinstance(openapi.get("paths"), dict) else {}
    issues: list[dict[str, Any]] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        if len(issues) >= 60:
            break
        path_str = str(path or "")
        public = bool(_PUBLIC_PATH_RE.search(path_str))
        sensitive = bool(_SENSITIVE_PATH_RE.search(path_str))
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            if len(issues) >= 60:
                break
            method_upper = str(method or "").upper()
            security = _op_security(openapi, op)
            has_security = bool(security) and isinstance(security, (list, dict))
            op_id = str(op.get("operationId") or "")
            summary = str(op.get("summary") or "")
            description = str(op.get("description") or "")
            op_text = " ".join([path_str, op_id, summary, description])
            op_sensitive = sensitive or bool(_SENSITIVE_PATH_RE.search(op_text)) or bool(_SENSITIVE_FIELD_RE.search(op_text))
            if op_sensitive and not public and not has_security:
                issues.append(
                    normalize_defect_signal(
                        {
                            "issue_id": f"ISSUE_OPENAPI_SEC_{len(issues)+1:04d}",
                            "title": f"安全方案缺失：{method_upper} {path_str}",
                            "defect_family": "security_boundary",
                            "risk_type": "openapi_security_static_scan",
                            "severity": "P1",
                            "confidence": 0.78,
                            "status": "needs_human_review",
                            "source": "openapi_static_security_scan",
                            "method": method_upper,
                            "path": path_str,
                            "expected": "敏感接口应显式声明 security 方案（如 bearerAuth/oauth2/apiKey）并走统一鉴权/审计",
                            "actual": "OpenAPI 未发现 security 声明，可能导致匿名访问或越权路径被遗漏",
                            "evidence": {"operationId": op_id, "summary": summary, "has_global_security": bool(openapi.get('security'))},
                        },
                        signal_kind="issue",
                        default_source="openapi_static_security_scan",
                    )
                )
            if method_upper in {"GET", "HEAD"} and _has_request_body(op):
                issues.append(
                    normalize_defect_signal(
                        {
                            "issue_id": f"ISSUE_OPENAPI_SEC_{len(issues)+1:04d}",
                            "title": f"GET 请求体风险：{method_upper} {path_str}",
                            "defect_family": "api_contract",
                            "risk_type": "api_contract",
                            "severity": "P2",
                            "confidence": 0.72,
                            "status": "needs_human_review",
                            "source": "openapi_static_security_scan",
                            "method": method_upper,
                            "path": path_str,
                            "expected": "GET/HEAD 应避免 requestBody，以免缓存/代理/签名/审计链路出现不一致",
                            "actual": "OpenAPI 发现 requestBody，可能导致运行时行为与网关/代理不一致",
                            "evidence": {"operationId": op_id, "summary": summary},
                        },
                        signal_kind="issue",
                        default_source="openapi_static_security_scan",
                    )
                )
            request_body = op.get("requestBody") if isinstance(op.get("requestBody"), dict) else {}
            content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
            schema = None
            if isinstance(content.get("application/json"), dict):
                schema = (content.get("application/json") or {}).get("schema")
            sensitive_fields = _iter_sensitive_fields(schema)
            if sensitive_fields and method_upper in {"GET", "HEAD"}:
                issues.append(
                    normalize_defect_signal(
                        {
                            "issue_id": f"ISSUE_OPENAPI_SEC_{len(issues)+1:04d}",
                            "title": f"敏感字段出现在只读接口契约中：{method_upper} {path_str}",
                            "defect_family": "security_boundary",
                            "risk_type": "openapi_security_static_scan",
                            "severity": "P2",
                            "confidence": 0.7,
                            "status": "needs_human_review",
                            "source": "openapi_static_security_scan",
                            "method": method_upper,
                            "path": path_str,
                            "expected": "敏感字段不应作为只读请求负载或应通过专用安全通道处理",
                            "actual": "OpenAPI schema 出现敏感字段，可能导致日志/代理侧泄露或误用",
                            "evidence": {"operationId": op_id, "sensitive_fields": sensitive_fields},
                        },
                        signal_kind="issue",
                        default_source="openapi_static_security_scan",
                    )
                )
    return issues
