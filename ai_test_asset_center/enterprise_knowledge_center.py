from __future__ import annotations

"""Phase58: Enterprise knowledge unified ingestion.

This module is intentionally small and composable.  It does not introduce a
second business-rule engine: it normalizes enterprise-provided materials into a
versioned, traceable knowledge asset and reuses the existing Phase57 industry
reasoning, Phase56 assurance coverage, probe planner and reporting chain.

Supported local/imported materials:
- PRD / MRD / requirement documents
- OpenAPI / Swagger JSON
- Postman collections
- SQL / database schema exports
- permission matrices (CSV/JSON/text)
- historical bugs / tickets (CSV/JSON/text)
- Feishu / Confluence exported documents or connector-provided text envelopes

No network crawling is performed.  External systems must provide exported
content or a trusted connector payload to keep access control and audit scope
explicit.
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .multi_industry_business_reasoning import infer_multi_industry_business_model
from .real_project_onboarding import ROOT, _html_escape, _load_json, _safe_project_id, _write_json, config_paths, load_real_project_config
from .product_ui import _icon, callout, detail_list, empty_state, h, metric_card, product_shell, section, status_badge, table

PHASE = "phase58_enterprise_knowledge_unified_ingestion"
SOURCE_TYPES = {
    "prd", "mrd", "openapi", "markdown_api", "postman", "har",
    "application_log", "database_schema", "db_field_dictionary",
    "permission_matrix", "historical_bug", "ticket",
    "uiux_spec", "uiux_svg",
    "db_design", "business_rules", "ui_design", "test_data",
    "config", "deploy",
    "feishu_document", "confluence_document",
    "collaboration_document", "other_document", "other",
}
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".html", ".htm", ".yaml", ".yml", ".csv", ".sql", ".json", ".xml", ".svg", ".har", ".log"}
MAX_SOURCE_BYTES = 20 * 1024 * 1024
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MARKDOWN_API_ENDPOINT_RE = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(?P<methods>(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)(?:\s*/\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS))*)\s+`?(?P<path>/[^\s`]+)`?\s*$"
)
SVG_TEXT_RE = re.compile(r"(?is)<text\b[^>]*>(.*?)</text>")
SVG_TAG_ATTR_RE = re.compile(r'(?i)\b(?:id|data-name|aria-label)="([^"]+)"')
SVG_TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title>")
SVG_DESC_RE = re.compile(r"(?is)<desc\b[^>]*>(.*?)</desc>")
ROLE_WORDS = {
    "admin": ["admin", "administrator", "管理员", "系统管理员"],
    "operator": ["operator", "运营", "操作员", "客服"],
    "approver": ["approver", "approve", "审批人", "审核人", "复核人"],
    "finance": ["finance", "accountant", "财务", "会计", "出纳"],
    "owner": ["owner", "负责人", "归属人", "客户经理"],
    "tenant_user": ["tenant", "workspace", "组织成员", "租户用户"],
    "doctor": ["doctor", "physician", "医生", "医师"],
    "teacher": ["teacher", "instructor", "老师", "教师"],
    "student": ["student", "learner", "学生", "学员"],
}
RISK_TERMS = {
    "permission_boundary": ["permission", "role", "access", "权限", "越权", "仅能", "只能", "tenant", "租户", "所属"],
    "state_machine": ["status", "state", "transition", "状态", "流转", "终态", "撤销", "取消", "审批"],
    "data_conservation": ["balance", "amount", "ledger", "inventory", "stock", "quota", "金额", "余额", "账本", "库存", "额度", "数量"],
    "data_reconciliation": ["reconcile", "match", "consistency", "对账", "一致", "匹配", "同步"],
    "idempotency": ["idempotent", "duplicate", "retry", "幂等", "重复", "重试"],
    "async_event": [
        "callback", "webhook", "event", "message", "notify", "queue", "sms",
        "back_in_stock", "restock", "inventory_sync", "inventory_restore",
        "回调", "事件", "消息", "通知", "短信", "异步",
        "到货提醒", "补货提醒", "库存同步", "库存恢复", "恢复库存", "库存回补",
    ],
    "sensitive_data": ["medical", "patient", "pii", "personal", "敏感", "病历", "隐私", "身份证"],
    "historical_regression": ["bug", "defect", "incident", "issue", "缺陷", "故障", "工单", "事故"],
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(token\s*[:=]\s*)([^\s,;]+)"),
]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _short_hash(value: Any, size: int = 16) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:size]


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    text = _norm(value)
    out = {part for part in re.split(r"[\s_\-]+", text) if len(part) >= 2}
    # Preserve Chinese semantic chunks without bringing in an NLP dependency.
    out.update(re.findall(r"[\u4e00-\u9fff]{2,8}", text))
    return out


ENGLISH_STATE_TOKENS = {
    "active",
    "applied",
    "approved",
    "archived",
    "cancelled",
    "canceled",
    "closed",
    "complete",
    "completed",
    "confirmed",
    "created",
    "deleted",
    "delivered",
    "disabled",
    "done",
    "draft",
    "enabled",
    "expired",
    "failed",
    "finished",
    "inactive",
    "init",
    "new",
    "paid",
    "pending",
    "processing",
    "received",
    "refunded",
    "refunding",
    "rejected",
    "returned",
    "returning",
    "settled",
    "shipped",
    "submitted",
    "success",
    "void",
    "wait_return",
}
CHINESE_STATE_HINTS = (
    "待",
    "已",
    "审核",
    "审批",
    "通过",
    "拒绝",
    "驳回",
    "支付",
    "付款",
    "发货",
    "收货",
    "完成",
    "取消",
    "关闭",
    "退款",
    "退货",
    "归档",
    "成功",
    "失败",
    "处理中",
    "创建",
    "新建",
    "提交",
    "受理",
    "配送",
    "草稿",
    "启用",
    "停用",
)


def _normalize_state_token(value: Any) -> str:
    token = str(value or "").strip().strip("`\"'[](){}<>.:;，。")
    if not token or len(token) > 24 or any(ch.isspace() for ch in token):
        return ""
    low = token.lower()
    if any(marker in low for marker in ("http", "www", ".com", "/", "\\", "px", "rem", "em", "%")):
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", low):
        return ""
    if re.search(r"\d", token) and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", token):
        return ""
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", token):
        return token
    if re.fullmatch(r"[a-z][a-z0-9_]{1,23}", low):
        return token if low in ENGLISH_STATE_TOKENS else ""
    if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", token):
        return token if any(marker in token for marker in CHINESE_STATE_HINTS) else ""
    return ""


def _safe_slug(name: str, limit: int = 72) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "document")).strip("._")
    return (value or "document")[:limit]


def _redact_text(text: Any, limit: int = 1200) -> str:
    out = str(text or "")
    for pattern in SECRET_PATTERNS:
        out = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]", out)
    out = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{10,}", "Bearer [REDACTED]", out)
    out = re.sub(r"(?i)(https?://[^\s?#]+)\?([^\s]+)", r"\1?[REDACTED_QUERY]", out)
    return out[:limit]


def _safe_actor(actor: dict[str, Any] | None) -> dict[str, str]:
    actor = actor if isinstance(actor, dict) else {}
    return {
        "name": str(actor.get("name") or actor.get("actor") or "knowledge_operator")[:120],
        "role": str(actor.get("role") or "knowledge_admin")[:64],
    }


def _require_manage_actor(actor: dict[str, Any] | None) -> dict[str, str]:
    clean = _safe_actor(actor)
    if clean["role"] not in {"knowledge_admin", "project_owner", "qa_lead", "admin"}:
        raise PermissionError("enterprise knowledge source changes require knowledge_admin, project_owner, qa_lead, or admin")
    return clean


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "enterprise_knowledge_center"
    defect_workspace = root / "platform_workspace" / project / "defect_discovery"
    output = root / "platform_outputs" / project / "enterprise_knowledge_center"
    return {
        "workspace": workspace,
        "source_dir": workspace / "sources",
        "registry": workspace / "source_registry.json",
        "asset": defect_workspace / "enterprise_business_knowledge_asset.json",
        "probe_catalog": defect_workspace / "enterprise_business_knowledge_probe_catalog.json",
        "evidence_bundle": defect_workspace / "enterprise_business_knowledge_evidence_bundle.json",
        "output": output,
        "asset_copy": output / "enterprise_business_knowledge_asset.json",
        "report": output / "enterprise_business_knowledge_report.html",
        "center_page": output / "enterprise_business_knowledge_center.html",
    }


def _registry_default(project: str) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "project_id": project,
        "created_at_utc": _now(),
        "updated_at_utc": _now(),
        "sources": [],
        "audit_events": [],
        "governance": {
            "raw_sources_kept_in_project_scoped_storage": True,
            "derived_assets_use_redacted_excerpts": True,
            "network_fetch_disabled": True,
            "source_changes_require_privileged_actor": True,
        },
    }


def _load_registry(project: str, root: Path) -> dict[str, Any]:
    paths = _paths(project, root)
    registry = _load_json(paths["registry"], {})
    if not isinstance(registry, dict):
        registry = {}
    default = _registry_default(project)
    default.update(registry)
    default["sources"] = [x for x in default.get("sources") or [] if isinstance(x, dict)]
    default["audit_events"] = [x for x in default.get("audit_events") or [] if isinstance(x, dict)]
    return default


def _save_registry(project: str, root: Path, registry: dict[str, Any]) -> None:
    paths = _paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    registry["updated_at_utc"] = _now()
    _write_json(paths["registry"], registry)


def _decode_docx(blob: bytes) -> str:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(blob)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        text = re.sub(r"</w:p>", "\n", xml)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return ""


def _decode_pdf(path: Path, blob: bytes) -> str:
    # Optional local extractor; no online OCR or third-party service is used.
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(__import__("io").BytesIO(blob))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        pass
    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.pdf"
            target = Path(tmp) / "source.txt"
            source.write_bytes(blob)
            try:
                subprocess.run(["pdftotext", "-layout", str(source), str(target)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                return target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            except Exception:
                return ""
    return ""


def _read_source_bytes(path: Path | None, text: str | None = None) -> tuple[bytes, str, str]:
    if text is not None:
        return text.encode("utf-8"), str(path.name if path else "inline_document.txt"), text
    if path is None or not path.exists() or not path.is_file():
        raise FileNotFoundError(f"source file not found: {path}")
    blob = path.read_bytes()
    if len(blob) > MAX_SOURCE_BYTES:
        raise ValueError(f"source file exceeds {MAX_SOURCE_BYTES // (1024 * 1024)}MB limit")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        extracted = _decode_docx(blob)
    elif suffix == ".pdf":
        extracted = _decode_pdf(path, blob)
    elif suffix in TEXT_SUFFIXES or not suffix:
        extracted = blob.decode("utf-8", errors="replace")
    else:
        extracted = ""
    return blob, path.name, extracted


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _contains_markdown_api_sections(text: str) -> bool:
    return bool(MARKDOWN_API_ENDPOINT_RE.search(text or ""))


def _looks_like_field_dictionary(name: str, text: str, payload: Any = None) -> bool:
    name_low = _norm(name)
    low = _norm(text[:8000])
    name_markers = (
        "field_dictionary",
        "data_dictionary",
        "schema_dictionary",
        "字段字典",
        "字段说明",
        "数据字典",
        "表结构说明",
        "字段清单",
    )
    content_markers = (
        "字段字典",
        "数据字典",
        "field dictionary",
        "data dictionary",
        "column description",
        "字段说明",
        "column name",
    )
    if any(token in name_low for token in name_markers):
        return True
    if any(token in low for token in content_markers):
        return True
    rows = payload if isinstance(payload, list) else payload.get("fields") if isinstance(payload, dict) else None
    if isinstance(rows, list):
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            keys = {_norm(key) for key in row}
            if {"table", "field"} <= keys or {"tablename", "fieldname"} <= keys:
                return True
    sample_rows = _csv_rows(text)
    if sample_rows:
        keys = {_norm(key) for key in sample_rows[0]}
        if {"table", "field"} <= keys or {"tablename", "fieldname"} <= keys:
            return True
    return False


def _looks_like_uiux_spec(name: str, text: str) -> bool:
    name_low = _norm(name)
    low = _norm(text[:12000])
    if name.lower().endswith(".svg") or "<svg" in str(text or "").lower():
        return True
    name_markers = (
        "uiux",
        "ui_ux",
        "wireframe",
        "prototype",
        "mockup",
        "设计稿",
        "原型",
        "交互",
        "界面",
    )
    content_markers = (
        "页面",
        "按钮",
        "弹窗",
        "空状态",
        "错误态",
        "加载态",
        "design spec",
        "wireframe",
        "prototype",
        "user flow",
        "component",
    )
    return any(token in name_low for token in name_markers) or sum(1 for token in content_markers if token in low) >= 2


def _clean_markup_text(value: str, limit: int = 200) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _doc_bool(value: Any) -> bool:
    normalized = _norm(value)
    if not normalized:
        return False
    if normalized in {"yes", "true", "required", "必填", "是", "y"}:
        return True
    if normalized in {"no", "false", "nullable", "optional", "否", "非必填", "n"}:
        return False
    return False


def _classify_source(name: str, text: str, explicit: str | None = None) -> str:
    explicit = str(explicit or "").strip().lower()
    name_low = _norm(name)
    low = _norm(f"{name} {text[:5000]}")
    data = _json_or_none(text)
    suffix = Path(name).suffix.lower()
    if suffix == ".har" or (suffix == ".json" and isinstance(data, dict) and "log" in data):
        return "har"
    if suffix == ".log" or (suffix == ".txt" and any(token in name_low for token in ("log", "日志", "access", "error"))):
        return "application_log"
    if suffix == ".svg" or "<svg" in str(text or "").lower():
        return "uiux_svg"
    if any(token in name_low for token in ("permission", "permissions", "matrix", "权限矩阵", "rbac", "acl")):
        return "permission_matrix"
    if any(token in name_low for token in ("historical_bug", "historical-bug", "bugs", "bug", "defect", "缺陷")):
        return "historical_bug"
    if any(token in name_low for token in ("ticket", "issue", "jira", "zentao", "工单")):
        return "ticket"
    if "postman" in name_low:
        return "postman"
    if any(token in name_low for token in ("confluence",)):
        return "confluence_document"
    if any(token in name_low for token in ("feishu", "lark", "飞书")):
        return "feishu_document"
    if isinstance(data, dict):
        if isinstance(data.get("paths"), dict) and (data.get("openapi") or data.get("swagger")):
            return "openapi"
        if isinstance(data.get("item"), list) and (data.get("info") or {}).get("schema", "").lower().find("postman") >= 0:
            return "postman"
    if name.lower().endswith(".sql") or "create table" in low or "alter table" in low:
        return "database_schema"
    if _looks_like_field_dictionary(name, text, data):
        return "db_field_dictionary"
    if "mrd" in name_low or re.search(r"\bMRD\b", name, flags=re.I) or "市场需求" in low:
        return "mrd"
    if "prd" in name_low or re.search(r"\bPRD\b", name, flags=re.I) or "产品需求" in low or "需求说明" in low:
        return "prd"
    if "postman" in low and ("collection" in low or '"item"' in low):
        return "postman"
    if _contains_markdown_api_sections(text) or (suffix in {".md", ".txt", ".rst"} and any(token in name_low for token in ("api", "接口")) and any(token in low for token in ("请求参数", "响应参数", "response", "request", "curl", "header"))):
        return "markdown_api"
    if _looks_like_uiux_spec(name, text):
        return "uiux_spec"
    if any(token in low for token in ["openapi", "swagger", "api contract"]):
        return "openapi"
    if any(token in low for token in ["权限矩阵", "permission matrix", "role matrix", "rbac", "acl"]):
        return "permission_matrix"
    if any(token in low for token in ["历史缺陷", "historical bug", "defect list", "bug list", "缺陷列表"]):
        return "historical_bug"
    if any(token in low for token in ["jira", "禅道", "工单", "ticket", "incident"]):
        return "ticket"
    if any(token in low for token in ["confluence"]):
        return "confluence_document"
    if any(token in low for token in ["飞书", "feishu", "lark"]):
        return "feishu_document"
    # Fall back to explicit type when auto-detection cannot determine a specific type
    if explicit in SOURCE_TYPES:
        return explicit
    return "collaboration_document"


def _openapi_operations(openapi: dict[str, Any], source_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} or not isinstance(operation, dict):
                continue
            parameters = operation.get("parameters") or []
            parameter_names = [str(row.get("name")) for row in parameters if isinstance(row, dict) and row.get("name")]
            tags = [str(x) for x in operation.get("tags") or []]
            summary = str(operation.get("summary") or operation.get("description") or "")
            operation_id = str(operation.get("operationId") or f"{method.lower()}_{str(path).strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}")
            interface_id = f"api:{method_u}:{path}"
            rows.append({
                "interface_id": interface_id,
                "source_id": source_id,
                "source_kind": "openapi",
                "method": method_u,
                "path": str(path),
                "operation_id": operation_id,
                "summary": summary,
                "tags": tags,
                "parameters": parameter_names,
                "tokens": sorted(_tokens(f"{path} {operation_id} {summary} {' '.join(tags)} {' '.join(parameter_names)}")),
            })
    return rows


def _postman_operations(payload: Any, source_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    def walk(items: Any) -> None:
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("item"), list):
                walk(item.get("item"))
            request = item.get("request") if isinstance(item.get("request"), dict) else {}
            if not request:
                continue
            method = str(request.get("method") or "GET").upper()
            url = request.get("url") or ""
            if isinstance(url, dict):
                path_values = url.get("path")
                if isinstance(path_values, list):
                    path = "/" + "/".join(str(x) for x in path_values)
                else:
                    path = str(url.get("raw") or "")
            else:
                path = str(url)
            path = re.sub(r"^https?://[^/]+", "", path) or "/"
            name = str(item.get("name") or "Postman request")
            interface_id = f"postman:{method}:{path}"
            rows.append({
                "interface_id": interface_id,
                "source_id": source_id,
                "source_kind": "postman",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(name, 64),
                "summary": name,
                "tags": ["postman"],
                "parameters": [],
                "tokens": sorted(_tokens(f"{path} {name}")),
            })
    root_items = payload.get("item") if isinstance(payload, dict) else []
    walk(root_items)
    return rows


def _json_blocks(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r"```json\s*(.*?)```", text or "", re.I | re.S):
        body = str(match.group(1) or "").strip()
        try:
            value = json.loads(body)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _flatten_json_field_names(value: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 3:
        return []
    if isinstance(value, dict):
        fields: list[str] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.append(path)
            fields.extend(_flatten_json_field_names(child, path, depth + 1))
        return fields
    if isinstance(value, list) and value:
        return _flatten_json_field_names(value[0], f"{prefix}[]".rstrip("."), depth + 1)
    return []


def _markdown_table_blocks(text: str) -> list[list[dict[str, str]]]:
    blocks: list[list[dict[str, str]]] = []
    current: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            current.append(stripped)
            continue
        if len(current) >= 2:
            blocks.append(current[:])
        current = []
    if len(current) >= 2:
        blocks.append(current[:])
    tables: list[list[dict[str, str]]] = []
    for block in blocks:
        headers = [part.strip() for part in block[0].strip("|").split("|")]
        if not headers or not any(headers):
            continue
        rows: list[dict[str, str]] = []
        for line in block[2:] if len(block) >= 2 and re.fullmatch(r"[\|\-\:\s]+", block[1]) else block[1:]:
            values = [part.strip() for part in line.strip("|").split("|")]
            if len(values) != len(headers):
                continue
            rows.append({str(headers[idx]): values[idx] for idx in range(len(headers))})
        if rows:
            tables.append(rows)
    return tables


def _pick_first(item: dict[str, Any], keys: Iterable[str]) -> str:
    norm_map = {_norm(key): key for key in item}
    for key in keys:
        actual = norm_map.get(_norm(key))
        if actual:
            return str(item.get(actual) or "").strip()
    return ""


def _infer_field_rows_from_markdown(text: str, source_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_table = ""
    lines = str(text or "").splitlines()
    for line in lines:
        heading = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
        if heading:
            label = heading.group(1).strip()
            table_match = re.search(r"(?i)(?:table|表|数据表)\s*[:：]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", label)
            current_table = table_match.group(1) if table_match else label
        inline = re.search(r"(?i)(?:table|表|数据表)\s*[:：=]\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", line)
        if inline:
            current_table = inline.group(1)
    for block in _markdown_table_blocks(text):
        for row in block:
            table_name = _pick_first(row, ("table", "table_name", "table name", "表", "数据表")) or current_table
            field_name = _pick_first(row, ("field", "field_name", "field name", "column", "column_name", "字段", "列名", "属性"))
            if not field_name:
                continue
            field_type = _pick_first(row, ("type", "data_type", "datatype", "字段类型", "类型"))
            description = _pick_first(row, ("description", "desc", "comment", "说明", "描述", "备注"))
            required = _pick_first(row, ("required", "nullable", "必填", "是否必填"))
            rows.append({
                "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
                "source_id": source_id,
                "table": table_name or "default",
                "table_id": f"table:{table_name or 'default'}",
                "field": field_name,
                "field_path": field_name,
                "type": field_type,
                "required": _doc_bool(required),
                "description": _redact_text(description, 320),
                "tokens": sorted(_tokens(f"{table_name} {field_name} {field_type} {description}")),
            })
    return rows


def _field_dictionary_entries(text: str, payload: Any, source_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("fields", "items", "columns", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend([item for item in value if isinstance(item, dict)])
        tables = payload.get("tables")
        if isinstance(tables, list):
            for table in tables:
                if not isinstance(table, dict):
                    continue
                table_name = str(table.get("name") or table.get("table") or "")
                for field in table.get("fields") or table.get("columns") or []:
                    if isinstance(field, dict):
                        item = dict(field)
                        item.setdefault("table", table_name)
                        candidates.append(item)
    elif isinstance(payload, list):
        candidates.extend([item for item in payload if isinstance(item, dict)])
    candidates.extend(_csv_rows(text))
    for item in candidates:
        table_name = _pick_first(item, ("table", "table_name", "tableName", "表", "数据表"))
        field_name = _pick_first(item, ("field", "field_name", "fieldName", "column", "column_name", "字段", "列名", "name"))
        if not field_name:
            continue
        field_type = _pick_first(item, ("type", "data_type", "dataType", "字段类型", "类型"))
        description = _pick_first(item, ("description", "desc", "comment", "说明", "描述", "remark", "备注"))
        required = _pick_first(item, ("required", "nullable", "必填", "is_required"))
        rows.append({
            "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
            "source_id": source_id,
            "table": table_name or "default",
            "table_id": f"table:{table_name or 'default'}",
            "field": field_name,
            "field_path": field_name,
            "type": field_type,
            "required": _doc_bool(required),
            "description": _redact_text(description, 320),
            "tokens": sorted(_tokens(f"{table_name} {field_name} {field_type} {description}")),
        })
    rows.extend(_infer_field_rows_from_markdown(text, source_id))
    return _dedupe_by_id(rows, "field_id")


def _field_dictionary_tables(entries: list[dict[str, Any]], source_id: str = "") -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        if isinstance(row, dict):
            grouped[str(row.get("table") or "default")].append(row)
    tables: list[dict[str, Any]] = []
    for table_name, items in grouped.items():
        columns = sorted({str(item.get("field") or "") for item in items if str(item.get("field") or "")})
        tables.append({
            "table_id": f"table:{table_name}",
            "source_id": source_id,
            "name": table_name,
            "columns": columns,
            "foreign_keys": [],
            "field_dictionary": items,
            "tokens": sorted(_tokens(f"{table_name} {' '.join(columns)} {' '.join(str(item.get('description') or '') for item in items[:12])}")),
        })
    return tables


def _markdown_api_operations(text: str, source_id: str = "") -> list[dict[str, Any]]:
    matches = list(MARKDOWN_API_ENDPOINT_RE.finditer(text or ""))
    rows: list[dict[str, Any]] = []
    if not matches:
        return rows
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text or "")
        section = str(text or "")[start:end]
        methods = [part.strip().upper() for part in re.split(r"\s*/\s*", match.group("methods")) if part.strip()]
        json_examples = _json_blocks(section)
        example_fields = sorted({name for sample in json_examples[:2] for name in _flatten_json_field_names(sample)})
        table_fields = [str(row.get("field") or "") for row in _field_dictionary_entries(section, None, source_id)]
        all_fields = sorted({field for field in [*example_fields, *table_fields] if field})
        summary_line = next((line.strip(" #-*") for line in section.splitlines() if line.strip() and not line.strip().startswith("|")), "")
        tag_candidates = re.findall(r"`([A-Za-z0-9_\-]{2,40})`", section[:600])
        for method in methods:
            path = str(match.group("path") or "/")
            rows.append({
                "interface_id": f"markdown_api:{method}:{path}",
                "source_id": source_id,
                "source_kind": "markdown_api",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}", 64),
                "summary": summary_line or f"{method} {path}",
                "tags": sorted(set(tag_candidates[:8])),
                "parameters": [field.split(".", 1)[0].replace("[]", "") for field in all_fields[:24]],
                "field_dictionary": all_fields[:40],
                "source_excerpt": _redact_text((match.group(0) + "\n" + section[:900]).strip(), 900),
                "tokens": sorted(_tokens(f"{path} {summary_line} {' '.join(all_fields)} {' '.join(tag_candidates[:8])}")),
            })
    return rows


def _sql_tables(text: str, source_id: str = "") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for match in re.finditer(r"(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?\s*\((.*?)\)\s*;", text):
        name, body = match.group(1), match.group(2)
        columns: list[str] = []
        foreign_keys: list[str] = []
        for line in body.splitlines():
            clean = line.strip().strip(",")
            if not clean:
                continue
            ref = re.search(r"(?i)references\s+[`\"\[]?([a-zA-Z0-9_]+)", clean)
            if ref:
                foreign_keys.append(ref.group(1))
            col = re.match(r"[`\"\[]?([a-zA-Z_][a-zA-Z0-9_]*)[`\"\]]?\s+", clean)
            if col and col.group(1).lower() not in {"primary", "foreign", "constraint", "unique", "key", "index"}:
                columns.append(col.group(1))
        tables.append({
            "table_id": f"table:{name}",
            "source_id": source_id,
            "name": name,
            "columns": sorted(set(columns)),
            "foreign_keys": sorted(set(foreign_keys)),
            "tokens": sorted(_tokens(f"{name} {' '.join(columns)} {' '.join(foreign_keys)}")),
        })
    return tables


def _json_schema_tables(payload: Any, source_id: str = "") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    candidates: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        schemas = ((payload.get("components") or {}).get("schemas") if isinstance(payload.get("components"), dict) else None) or payload.get("schemas") or payload.get("tables")
        if isinstance(schemas, dict):
            candidates = list(schemas.items())
        elif isinstance(schemas, list):
            candidates = [(str(item.get("name") or item.get("table") or f"table_{idx+1}"), item) for idx, item in enumerate(schemas) if isinstance(item, dict)]
    for name, body in candidates:
        props = body.get("properties") if isinstance(body, dict) else {}
        columns = list(props.keys()) if isinstance(props, dict) else list(body.get("columns") or []) if isinstance(body, dict) else []
        foreign_keys = [str(x) for x in (body.get("foreign_keys") or body.get("relations") or [])] if isinstance(body, dict) else []
        tables.append({
            "table_id": f"table:{name}", "source_id": source_id, "name": str(name),
            "columns": [str(x) for x in columns], "foreign_keys": foreign_keys,
            "tokens": sorted(_tokens(f"{name} {' '.join(str(x) for x in columns)} {' '.join(foreign_keys)}")),
        })
    return tables


def _uiux_specs_from_text(text: str, source_id: str, source_type: str, filename: str) -> list[dict[str, Any]]:
    if source_type not in {"uiux_spec", "uiux_svg"}:
        return []
    specs: list[dict[str, Any]] = []
    title = _clean_markup_text(next(iter(SVG_TITLE_RE.findall(text or "")), "")) if source_type == "uiux_svg" else ""
    description = _clean_markup_text(next(iter(SVG_DESC_RE.findall(text or "")), ""))
    text_labels = [_clean_markup_text(item, 80) for item in SVG_TEXT_RE.findall(text or "")]
    attr_labels = [_clean_markup_text(item, 80) for item in SVG_TAG_ATTR_RE.findall(text or "")]
    labels = [label for label in [*text_labels, *attr_labels] if label]
    component_keywords = re.findall(r"(?im)(?:component|组件|控件|button|input|table|modal|drawer|chart|card)\s*[:：-]?\s*([A-Za-z0-9_\-\u4e00-\u9fff ]{2,60})", text or "")
    state_keywords = re.findall(r"(?im)^\s*(?:state|states|状态)\s*[:：-]\s*([A-Za-z0-9_\-\u4e00-\u9fff、, /]{2,120})\s*$", text or "")
    components = sorted({label for label in [*component_keywords, *labels[:20]] if label})[:24]
    states: list[str] = []
    for item in state_keywords:
        states.extend([part.strip() for part in re.split(r"[,/|、]", item) if part.strip()])
    known_state_labels = ("Loading", "Error", "Empty", "Success", "加载", "错误", "空状态", "成功")
    for label in labels:
        for token in known_state_labels:
            if _norm(token) in _norm(label):
                states.append(label)
                break
    if not states:
        low = _norm(text[:8000])
        for token in ("loading", "error", "empty", "success", "加载", "错误", "空状态", "成功"):
            if _norm(token) in low:
                states.append(token)
    name = title or Path(filename).stem
    specs.append({
        "ui_spec_id": f"ui:{source_id}:{_short_hash({'name': name, 'type': source_type})}",
        "source_id": source_id,
        "source_type": source_type,
        "name": name,
        "description": _redact_text(description or " ".join(labels[:6]), 320),
        "components": components,
        "states": sorted(set(states))[:12],
        "text_labels": labels[:30],
        "tokens": sorted(_tokens(f"{name} {description} {' '.join(components)} {' '.join(labels[:20])}")),
    })
    return specs


def _csv_rows(text: str) -> list[dict[str, str]]:
    try:
        return [dict(row) for row in csv.DictReader(text.splitlines()) if isinstance(row, dict)]
    except Exception:
        return []


def _permission_entries(text: str, payload: Any, source_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("permissions", "matrix", "roles", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend([item for item in value if isinstance(item, dict)])
    elif isinstance(payload, list):
        candidates.extend([item for item in payload if isinstance(item, dict)])
    candidates.extend(_csv_rows(text))
    for idx, item in enumerate(candidates):
        role = str(item.get("role") or item.get("actor") or item.get("user_role") or "").strip()
        resource = str(item.get("resource") or item.get("module") or item.get("object") or item.get("path") or "").strip()
        actions = item.get("actions") or item.get("action") or item.get("permissions") or item.get("operation") or ""
        if isinstance(actions, list):
            actions_list = [str(x) for x in actions]
        else:
            actions_list = [x.strip() for x in re.split(r"[,/|;]", str(actions)) if x.strip()]
        scope = str(item.get("scope") or item.get("data_scope") or item.get("tenant_scope") or "").strip()
        if role and resource:
            rows.append({"permission_id": f"perm:{source_id}:{idx+1}", "source_id": source_id, "role": role, "resource": resource, "actions": actions_list or ["read"], "scope": scope or "unspecified", "evidence": _redact_text(str(item), 280)})
    if rows:
        return rows
    for idx, line in enumerate(text.splitlines()):
        normalized = _norm(line)
        if not normalized or not any(marker in normalized for marker in ("权限", "permission", "role", "访问", "只能", "tenant")):
            continue
        role_match = re.search(r"(?i)(?:role|角色|用户)\s*[:：=]\s*([^,;，；]+)", line)
        resource_match = re.search(r"(?i)(?:resource|资源|模块|对象|接口)\s*[:：=]\s*([^,;，；]+)", line)
        if role_match or resource_match:
            rows.append({"permission_id": f"perm:{source_id}:line:{idx+1}", "source_id": source_id, "role": (role_match.group(1).strip() if role_match else "unspecified_role"), "resource": (resource_match.group(1).strip() if resource_match else "unspecified_resource"), "actions": ["read"], "scope": "document_declared", "evidence": _redact_text(line, 280)})
    return rows


def _ticket_rows(text: str, payload: Any, source_id: str, source_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("issues", "bugs", "tickets", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        if not candidates and any(key in payload for key in ("title", "summary", "description")):
            candidates.append(payload)
    elif isinstance(payload, list):
        candidates.extend(payload)
    candidates.extend(_csv_rows(text))
    for idx, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("summary") or item.get("name") or item.get("description") or "").strip()
        if not title:
            continue
        severity = str(item.get("severity") or item.get("priority") or "P2").upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            severity = "P2"
        rows.append({
            "risk_id": f"history:{source_id}:{idx+1}",
            "source_id": source_id,
            "source_type": source_type,
            "title": _redact_text(title, 320),
            "severity": severity,
            "status": str(item.get("status") or "historical"),
            "risk_type": _risk_type_from_text(title),
            "evidence": _redact_text(str(item.get("description") or title), 600),
        })
    if rows:
        return rows
    for idx, line in enumerate(text.splitlines()):
        if any(marker in _norm(line) for marker in ("缺陷", "bug", "故障", "incident", "越权", "重复", "金额")):
            rows.append({"risk_id": f"history:{source_id}:line:{idx+1}", "source_id": source_id, "source_type": source_type, "title": _redact_text(line, 320), "severity": "P1" if any(x in _norm(line) for x in ("p0", "p1", "严重", "资金", "越权")) else "P2", "status": "historical", "risk_type": _risk_type_from_text(line), "evidence": _redact_text(line, 600)})
    return rows


def _rule_type_from_text(text: str) -> str:
    norm = _norm(text)
    if any(term in norm for term in RISK_TERMS["permission_boundary"]):
        return "permission"
    if any(term in norm for term in RISK_TERMS["async_event"]):
        return "async_event"
    if any(term in norm for term in RISK_TERMS["data_conservation"]):
        return "conservation"
    if any(term in norm for term in RISK_TERMS["data_reconciliation"]):
        return "reconciliation"
    if any(term in norm for term in RISK_TERMS["state_machine"]):
        return "state_transition"
    if any(term in norm for term in RISK_TERMS["idempotency"]):
        return "idempotency"
    return "business_rule"


def _risk_type_from_text(text: str) -> str:
    norm = _norm(text)
    if any(term in norm for term in RISK_TERMS["async_event"]):
        return "async_event"
    if any(term in norm for term in RISK_TERMS["idempotency"]):
        return "idempotency"
    for name, terms in RISK_TERMS.items():
        if name in {"async_event", "idempotency"}:
            continue
        if any(_norm(term) in norm for term in terms):
            return name
    return "business_rule"


def _rules_from_text(text: str, source_id: str, source_type: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    allow_relaxed_async_rules = source_type == "collaboration_document"
    seen_statements: set[str] | None = set() if source_type == "collaboration_document" else None
    lines = [line.strip(" -•\t") for line in re.split(r"[\n。.!?；;]", text) if line.strip()]
    for idx, line in enumerate(lines):
        norm = _norm(line)
        if len(norm) < 8:
            continue
        indicator = any(marker in norm for marker in ("必须", "不得", "只能", "禁止", "应当", "should", "must", "only", "not allowed", "cannot", "require", "一致", "守恒", "审批"))
        rule_type = _rule_type_from_text(line)
        if not indicator and not (allow_relaxed_async_rules and rule_type in {"idempotency", "async_event"}):
            continue
        statement = _redact_text(line, 720)
        if seen_statements is not None:
            key = _norm(statement)
            if key in seen_statements:
                continue
            seen_statements.add(key)
        rules.append({
            "rule_id": f"rule:{source_id}:{idx+1}",
            "source_id": source_id,
            "source_type": source_type,
            "statement": statement,
            "rule_type": rule_type,
            "risk_type": _risk_type_from_text(line),
            "severity": "P0" if rule_type in {"conservation", "permission"} and any(x in norm for x in ("资金", "余额", "账本", "payment", "balance", "tenant", "租户", "病历")) else "P1" if rule_type in {"conservation", "permission", "reconciliation"} else "P2",
            "tokens": sorted(_tokens(line)),
        })
    return rules[:180]


def _roles_from_text(text: str, source_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lower = _norm(text)
    for role, words in ROLE_WORDS.items():
        evidence = next((word for word in words if _norm(word) in lower), "")
        if evidence:
            out.append({"role_id": f"role:{source_id}:{role}", "source_id": source_id, "role": role, "evidence": evidence})
    return out


def _state_machines_from_text(text: str, source_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    transitions: list[tuple[str, str]] = []
    for match in re.finditer(r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,32})\s*(?:->|→|到|至)\s*([A-Za-z0-9_\-\u4e00-\u9fff]{2,32})", text):
        src = _normalize_state_token(match.group(1))
        dst = _normalize_state_token(match.group(2))
        if src and dst and _norm(src) != _norm(dst):
            transitions.append((src, dst))
    if transitions:
        states = []
        for src, dst in transitions:
            if src not in states:
                states.append(src)
            if dst not in states:
                states.append(dst)
        out.append({"state_machine_id": f"state:{source_id}:1", "source_id": source_id, "object": "document_workflow", "states": states[:24], "transitions": [{"from": s, "to": t} for s, t in transitions[:40]], "evidence": _redact_text("; ".join(f"{s}->{t}" for s, t in transitions), 700)})
    return out


def _parse_source(blob: bytes, filename: str, source_type: str, source_id: str) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        text = _decode_docx(blob)
    elif suffix == ".pdf":
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / filename
            fake.write_bytes(blob)
            text = _decode_pdf(fake, blob)
    else:
        text = blob.decode("utf-8", errors="replace")
    payload = _json_or_none(text) if suffix == ".json" or source_type in {"openapi", "postman", "permission_matrix", "historical_bug", "ticket"} else None
    # HAR: parse JSON and extract operations
    har_errors: list[dict[str, Any]] = []
    if source_type == "har":
        try:
            from .har_importer import import_har_endpoints, extract_har_error_patterns
            har_file = Path(filename)
            if suffix == ".har":
                # Write blob to temp file for HAR parser
                import tempfile as _tmp
                with _tmp.NamedTemporaryFile(suffix=".har", delete=False) as tf:
                    tf.write(blob)
                    tf.flush()
                    har_endpoints = import_har_endpoints(tf.name)
                    har_errors_raw = extract_har_error_patterns(tf.name)
                try:
                    Path(tf.name).unlink()
                except OSError:
                    pass
                operations.extend([
                    {"path": ep["path"], "method": ep["method"], "capability": ep["capability_code"],
                     "source": "har_traffic", "summary": ep["summary"]}
                    for ep in har_endpoints
                ])
                har_errors = [
                    {"endpoint": e.endpoint, "method": e.method, "status": e.status,
                     "message": e.error_message, "count": e.count}
                    for e in har_errors_raw
                ]
        except Exception as har_err:
            print(f"  [WARN] HAR parsing failed for {filename}: {har_err}", flush=True, file=sys.stderr)
    # Application logs: run log analysis
    log_errors: list[dict[str, Any]] = []
    if source_type == "application_log":
        try:
            from .log_analyzer import analyze_logs
            import tempfile as _tmp2
            with _tmp2.NamedTemporaryFile(suffix=".log", delete=False) as tf:
                tf.write(blob)
                tf.flush()
                log_result = analyze_logs(tf.name)
            try:
                Path(tf.name).unlink()
            except OSError:
                pass
            log_errors = [
                {"error_type": c.error_type, "count": c.count,
                 "message": c.message_pattern, "severity": c.severity}
                for c in log_result.get("error_clusters", [])
            ]
            # Also add slow endpoints as operations
            for s in log_result.get("slow_endpoints", []):
                operations.append({
                    "path": s.path, "method": s.method,
                    "capability": "read", "source": "access_log",
                    "summary": f"P95={s.p95_ms:.0f}ms, err_rate={s.error_rate:.1%}",
                })
        except Exception as log_err:
            print(f"  [WARN] Log analysis failed for {filename}: {log_err}", flush=True, file=sys.stderr)
    openapi = payload if source_type == "openapi" and isinstance(payload, dict) else {}
    postman = payload if source_type == "postman" and isinstance(payload, dict) else {}
    operations = _openapi_operations(openapi, source_id) + _postman_operations(postman, source_id)
    if source_type == "markdown_api":
        operations.extend(_markdown_api_operations(text, source_id))
    tables = _sql_tables(text, source_id) if source_type == "database_schema" else []
    tables += _json_schema_tables(payload, source_id) if source_type in {"database_schema", "openapi"} else []
    field_dictionary = _field_dictionary_entries(text, payload, source_id) if source_type in {"db_field_dictionary", "database_schema"} else []
    if source_type == "db_field_dictionary":
        tables += _field_dictionary_tables(field_dictionary, source_id)
    ui_specs = _uiux_specs_from_text(text, source_id, source_type, filename)
    permissions = _permission_entries(text, payload, source_id) if source_type == "permission_matrix" else []
    tickets = _ticket_rows(text, payload, source_id, source_type) if source_type in {"historical_bug", "ticket"} else []
    return {
        "text": text,
        "payload": payload,
        "openapi": openapi,
        "operations": operations,
        "tables": tables,
        "field_dictionary": field_dictionary,
        "ui_specs": ui_specs,
        "permissions": permissions,
        "tickets": tickets,
        "har_errors": har_errors,
        "log_errors": log_errors,
        "rules": _rules_from_text(text, source_id, source_type),
        "roles": _roles_from_text(text, source_id),
        "state_machines": _state_machines_from_text(text, source_id),
        "parse_status": "parsed" if text.strip() else "metadata_only",
        "parser": "json" if payload is not None else suffix.lstrip(".") or "text",
        "text_hash": _hash_bytes(text.encode("utf-8")),
        "text_length": len(text),
    }


def _record_parse(record: dict[str, Any], root: Path) -> dict[str, Any]:
    stored = root / str(record.get("stored_path") or "")
    if not stored.exists():
        return {"text": "", "payload": None, "openapi": {}, "operations": [], "tables": [], "field_dictionary": [], "ui_specs": [], "permissions": [], "tickets": [], "har_errors": [], "log_errors": [], "rules": [], "roles": [], "state_machines": [], "parse_status": "missing_source", "parser": "none", "text_hash": "", "text_length": 0}
    return _parse_source(stored.read_bytes(), str(record.get("original_name") or stored.name), str(record.get("source_type") or "other_document"), str(record.get("source_id") or ""))


def _logical_key(name: str, source_type: str) -> str:
    return f"{source_type}:{_safe_slug(Path(name).stem, 72).lower()}"


def ingest_enterprise_knowledge_documents(
    project_id: str,
    documents: list[dict[str, Any]],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest path or text envelopes into project-scoped versioned source storage.

    Accepted envelope fields: file_path, text, filename/name, source_type,
    tags and external_ref.  external_ref is metadata only; this function never
    fetches remote Feishu/Confluence content by URL.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    paths = _paths(project, root)
    paths["source_dir"].mkdir(parents=True, exist_ok=True)
    registry = _load_registry(project, root)
    active = [row for row in registry["sources"] if row.get("status") == "active"]
    created: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, doc in enumerate(documents or []):
        if not isinstance(doc, dict):
            errors.append({"index": index, "error": "document envelope must be object"})
            continue
        try:
            file_path = Path(str(doc.get("file_path"))) if doc.get("file_path") else None
            inline_text = str(doc.get("text")) if doc.get("text") is not None else None
            blob, detected_name, raw_text = _read_source_bytes(file_path, inline_text)
            filename = str(doc.get("filename") or doc.get("name") or detected_name)
            source_type = _classify_source(filename, raw_text, str(doc.get("source_type") or ""))
            content_hash = _hash_bytes(blob)
            duplicate = next((row for row in registry["sources"] if row.get("status") != "deleted" and row.get("content_hash") == content_hash), None)
            if duplicate:
                duplicates.append({"filename": filename, "source_id": duplicate.get("source_id"), "reason": "same_content_hash", "source_type": source_type})
                continue
            logical_key = _logical_key(filename, source_type)
            versions = [int(row.get("version") or 0) for row in registry["sources"] if row.get("logical_key") == logical_key]
            version = max(versions, default=0) + 1
            source_id = f"src_{_short_hash({'project': project, 'hash': content_hash, 'logical_key': logical_key, 'version': version})}"
            for previous in [row for row in active if row.get("logical_key") == logical_key]:
                previous["status"] = "superseded"
                previous["superseded_at_utc"] = _now()
                previous["superseded_by"] = source_id
            active = [row for row in active if row.get("status") == "active"]
            storage_name = f"{source_id}_v{version}_{_safe_slug(filename)}"
            stored = paths["source_dir"] / storage_name
            stored.write_bytes(blob)
            parsed = _parse_source(blob, filename, source_type, source_id)
            record = {
                "source_id": source_id,
                "logical_key": logical_key,
                "original_name": filename,
                "source_type": source_type,
                "version": version,
                "content_hash": content_hash,
                "status": "active",
                "tags": [str(x)[:80] for x in (doc.get("tags") or []) if str(x).strip()][:20],
                "external_ref": str(doc.get("external_ref") or "")[:500],
                "stored_path": str(stored.relative_to(root)).replace("\\", "/"),
                "created_at_utc": _now(),
                "created_by": clean_actor,
                "parse": {
                    "parser": parsed["parser"],
                    "parse_status": parsed["parse_status"],
                    "text_hash": parsed["text_hash"],
                    "text_length": parsed["text_length"],
                    "operation_count": len(parsed["operations"]),
                    "table_count": len(parsed["tables"]),
                    "field_count": len(parsed["field_dictionary"]),
                    "ui_spec_count": len(parsed["ui_specs"]),
                    "permission_count": len(parsed["permissions"]),
                    "rule_count": len(parsed["rules"]),
                    "ticket_count": len(parsed["tickets"]),
                },
            }
            registry["sources"].append(record)
            active.append(record)
            created.append(record)
        except Exception as exc:
            errors.append({"index": index, "filename": str(doc.get("filename") or doc.get("name") or ""), "error": str(exc)[:500]})
    registry["audit_events"].append({"event": "ingest", "at_utc": _now(), "actor": clean_actor, "created_source_ids": [x["source_id"] for x in created], "duplicate_count": len(duplicates), "error_count": len(errors)})
    _save_registry(project, root, registry)
    return {"ok": not errors, "phase": PHASE, "project_id": project, "created": created, "duplicates": duplicates, "errors": errors, "source_count": len([x for x in registry["sources"] if x.get("status") == "active"]), "rebuild_recommended": bool(created)}


def ingest_enterprise_knowledge_files(project_id: str, file_paths: Iterable[str | Path], root: Path | None = None, actor: dict[str, Any] | None = None, source_type_hints: dict[str, str] | None = None) -> dict[str, Any]:
    source_type_hints = source_type_hints or {}
    documents = [{"file_path": str(path), "source_type": source_type_hints.get(str(path))} for path in file_paths]
    return ingest_enterprise_knowledge_documents(project_id, documents, root=root, actor=actor)


def list_enterprise_knowledge_sources(project_id: str, root: Path | None = None, include_deleted: bool = False) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    registry = _load_registry(project, root)
    sources = registry["sources"] if include_deleted else [x for x in registry["sources"] if x.get("status") == "active"]
    return {"phase": PHASE, "project_id": project, "sources": sources, "summary": {"active_source_count": len([x for x in registry["sources"] if x.get("status") == "active"]), "superseded_source_count": len([x for x in registry["sources"] if x.get("status") == "superseded"]), "deleted_source_count": len([x for x in registry["sources"] if x.get("status") == "deleted"]), "source_type_distribution": dict(Counter(str(x.get("source_type") or "unknown") for x in sources))}, "governance": registry.get("governance") or {}}


def update_enterprise_knowledge_source(project_id: str, source_id: str, patch: dict[str, Any], root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, root)
    record = next((row for row in registry["sources"] if row.get("source_id") == source_id and row.get("status") == "active"), None)
    if not record:
        raise KeyError(f"active source not found: {source_id}")
    if "tags" in patch:
        record["tags"] = [str(x)[:80] for x in (patch.get("tags") or []) if str(x).strip()][:20]
    if "source_type" in patch:
        source_type = str(patch.get("source_type") or "").lower()
        if source_type not in SOURCE_TYPES:
            raise ValueError("unsupported source_type")
        record["source_type"] = source_type
        record["logical_key"] = _logical_key(str(record.get("original_name") or "document"), source_type)
    if "external_ref" in patch:
        record["external_ref"] = str(patch.get("external_ref") or "")[:500]
    record["updated_at_utc"] = _now()
    record["updated_by"] = clean_actor
    registry["audit_events"].append({"event": "update_metadata", "at_utc": _now(), "actor": clean_actor, "source_id": source_id, "fields": sorted(set(patch).intersection({"tags", "source_type", "external_ref"}))})
    _save_registry(project, root, registry)
    return {"ok": True, "source": record, "rebuild_recommended": True}


def delete_enterprise_knowledge_source(project_id: str, source_id: str, root: Path | None = None, actor: dict[str, Any] | None = None, purge_bytes: bool = False) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(actor)
    registry = _load_registry(project, root)
    record_index = next((index for index, row in enumerate(registry["sources"]) if row.get("source_id") == source_id and row.get("status") == "active"), None)
    record = registry["sources"][record_index] if record_index is not None else None
    if not record:
        raise KeyError(f"active source not found: {source_id}")
    removed_paths: list[str] = []
    original_name = str(record.get("original_name") or "")
    candidate_paths: list[Path] = []
    stored_path = str(record.get("stored_path") or "")
    if stored_path:
        candidate_paths.append(root / stored_path)
    if original_name:
        candidate_paths.append(root / "platform_workspace" / project / "input" / original_name)
    seen_paths: set[str] = set()
    for candidate in candidate_paths:
        resolved = str(candidate.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            if candidate.exists() and candidate.is_file():
                candidate.unlink()
                removed_paths.append(str(candidate.relative_to(root)).replace("\\", "/"))
        except Exception:
            continue
    registry["sources"].pop(record_index)
    registry["audit_events"].append({
        "event": "delete",
        "at_utc": _now(),
        "actor": clean_actor,
        "source_id": source_id,
        "original_name": original_name,
        "removed_paths": removed_paths,
        "physical_delete": True,
    })
    _save_registry(project, root, registry)
    return {
        "ok": True,
        "source_id": source_id,
        "original_name": original_name,
        "purged_bytes": bool(removed_paths),
        "removed_paths": removed_paths,
        "rebuild_recommended": True,
    }


def operate_enterprise_knowledge_center(
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Small controller facade for the local knowledge-center page/API.

    It intentionally delegates to the existing ingestion and governance functions
    instead of introducing a second web service or persistence model.
    """
    payload = payload if isinstance(payload, dict) else {}
    root = root or ROOT
    project = _safe_project_id(project_id)
    action = str(action or "view").strip().lower()
    if action in {"view", "list"}:
        asset = load_enterprise_business_knowledge_asset(project, root)
        return {
            "ok": True,
            "action": "view",
            "inventory": list_enterprise_knowledge_sources(project, root, include_deleted=bool(payload.get("include_deleted"))),
            "asset": asset or {},
        }
    if action in {"upload", "ingest"}:
        docs = payload.get("documents") if isinstance(payload.get("documents"), list) else []
        result = ingest_enterprise_knowledge_documents(project, docs, root=root, actor=actor)
        return {"ok": bool(result.get("ok")), "action": "upload", "result": result}
    if action in {"edit", "update"}:
        source_id = str(payload.get("source_id") or "")
        return {"ok": True, "action": "edit", "result": update_enterprise_knowledge_source(project, source_id, payload.get("patch") or {}, root=root, actor=actor)}
    if action in {"delete", "remove"}:
        source_id = str(payload.get("source_id") or "")
        return {"ok": True, "action": "delete", "result": delete_enterprise_knowledge_source(project, source_id, root=root, actor=actor, purge_bytes=bool(payload.get("purge_bytes")))}
    if action in {"rebuild", "build"}:
        asset = build_enterprise_business_knowledge_asset(project, root, options=payload.get("options") if isinstance(payload.get("options"), dict) else None)
        return {"ok": True, "action": "rebuild", "asset": asset}
    raise ValueError("unsupported knowledge center action; use view, upload, edit, delete or rebuild")


def _merge_openapi(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"openapi": "3.0.3", "info": {"title": "Enterprise Knowledge Unified API", "version": "derived"}, "paths": {}, "components": {"schemas": {}}}
    for item in parts:
        if not isinstance(item, dict):
            continue
        for path, methods in (item.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            target = merged["paths"].setdefault(str(path), {})
            for method, spec in methods.items():
                if str(method).lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                target[str(method).lower()] = spec
        schemas = ((item.get("components") or {}).get("schemas") if isinstance(item.get("components"), dict) else {}) or {}
        if isinstance(schemas, dict):
            merged["components"]["schemas"].update(schemas)
    return merged


def _dedupe_by_id(rows: Iterable[dict[str, Any]], id_field: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get(id_field) or _short_hash(row))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _links_by_overlap(left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]], left_id: str, right_id: str, min_overlap: int = 1, relation: str = "related_to") -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for a in left:
        at = set(a.get("tokens") or _tokens(a.get("statement") or a.get("title") or a.get("name") or ""))
        if not at:
            continue
        best: list[tuple[int, dict[str, Any]]] = []
        for b in right:
            bt = set(b.get("tokens") or _tokens(f"{b.get('path') or ''} {b.get('summary') or ''} {b.get('name') or ''} {' '.join(b.get('columns') or [])}"))
            overlap = len(at & bt)
            if overlap >= min_overlap:
                best.append((overlap, b))
        for overlap, b in sorted(best, key=lambda x: (-x[0], str(x[1].get(right_id))))[:3]:
            edges.append({"edge_id": f"edge:{_short_hash({'a': a.get(left_id), 'b': b.get(right_id), 'relation': relation})}", "from": a.get(left_id), "to": b.get(right_id), "relation": relation, "confidence": round(min(0.95, 0.45 + overlap * 0.13), 3), "evidence": {"token_overlap": sorted(at & set(b.get("tokens") or _tokens(str(b))))[:10]}})
    return _dedupe_by_id(edges, "edge_id")


def _module_tree(interfaces: list[dict[str, Any]], rules: list[dict[str, Any]], tables: list[dict[str, Any]], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for interface in interfaces:
        path = str(interface.get("path") or "/").strip("/")
        module = path.split("/")[0] or "root"
        row = modules.setdefault(module, {"module_id": f"module:{module}", "name": module, "interfaces": [], "objects": set(), "rules": set(), "tables": set()})
        row["interfaces"].append(interface.get("interface_id"))
        for token in interface.get("tokens") or []:
            if token in {str(x.get("object") or "") for x in objects}:
                row["objects"].add(token)
    object_names = {str(x.get("object") or x.get("name") or "") for x in objects}
    for rule in rules:
        targets = _tokens(rule.get("statement") or "")
        candidates = [name for name in object_names if name and (name in targets or name in str(rule.get("statement") or "").lower())]
        module = candidates[0] if candidates else "business_rules"
        row = modules.setdefault(module, {"module_id": f"module:{module}", "name": module, "interfaces": [], "objects": set(), "rules": set(), "tables": set()})
        row["rules"].add(rule.get("rule_id"))
    for table in tables:
        name = str(table.get("name") or "table")
        module = name.split("_")[0] or "data"
        row = modules.setdefault(module, {"module_id": f"module:{module}", "name": module, "interfaces": [], "objects": set(), "rules": set(), "tables": set()})
        row["tables"].add(table.get("table_id"))
    result = []
    for row in modules.values():
        result.append({"module_id": row["module_id"], "name": row["name"], "interfaces": sorted(x for x in row["interfaces"] if x), "objects": sorted(x for x in row["objects"] if x), "rules": sorted(x for x in row["rules"] if x), "tables": sorted(x for x in row["tables"] if x)})
    return sorted(result, key=lambda x: (-len(x["interfaces"]) - len(x["rules"]), x["name"]))


def _risk_domains(rules: list[dict[str, Any]], tickets: list[dict[str, Any]], industry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule in rules:
        risk_type = str(rule.get("risk_type") or _risk_type_from_text(rule.get("statement") or ""))
        rows.append({"risk_id": f"risk:{rule.get('rule_id')}", "source_rule_id": rule.get("rule_id"), "source_id": rule.get("source_id"), "risk_type": risk_type, "severity": rule.get("severity") or "P2", "title": f"企业知识规则风险：{rule.get('statement')}", "expected": rule.get("statement"), "oracle_family": _oracle_family(risk_type), "evidence": [rule.get("source_id")]})
    rows.extend(tickets)
    for risk in industry.get("risk_domains") or []:
        if not isinstance(risk, dict):
            continue
        rows.append({"risk_id": f"industry:{risk.get('risk_id') or _short_hash(risk)}", "source_rule_id": risk.get("rule_id"), "source_id": "industry_inference", "risk_type": risk.get("risk_type") or "industry_business_rule", "severity": risk.get("severity") or "P1", "title": risk.get("title"), "expected": risk.get("expected"), "oracle_family": risk.get("oracle_family") or "industry_oracle", "evidence": ["industry_inference"]})
    return _dedupe_by_id(rows, "risk_id")[:240]


def _oracle_family(risk_type: str) -> str:
    return {
        "permission_boundary": "authorization_boundary_oracle",
        "state_machine": "state_transition_oracle",
        "data_conservation": "conservation_oracle",
        "data_reconciliation": "reconciliation_oracle",
        "idempotency": "idempotency_oracle",
        "sensitive_data": "sensitive_data_scope_oracle",
        "historical_regression": "historical_regression_oracle",
    }.get(str(risk_type), "business_rule_oracle")


def _oracle_library(rules: list[dict[str, Any]], industry_oracles: list[dict[str, Any]], relation_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    related_interfaces: dict[str, list[str]] = defaultdict(list)
    related_tables: dict[str, list[str]] = defaultdict(list)
    for edge in relation_edges:
        if edge.get("relation") == "rule_to_interface":
            related_interfaces[str(edge.get("from"))].append(str(edge.get("to")))
        elif edge.get("relation") == "rule_to_table":
            related_tables[str(edge.get("from"))].append(str(edge.get("to")))
    result: list[dict[str, Any]] = []
    for rule in rules:
        risk_type = str(rule.get("risk_type") or "business_rule")
        rid = str(rule.get("rule_id"))
        result.append({"oracle_id": f"oracle:{rid}", "rule_id": rid, "family": _oracle_family(risk_type), "assertion": rule.get("statement"), "linked_interfaces": sorted(set(related_interfaces.get(rid, []))), "linked_tables": sorted(set(related_tables.get(rid, []))), "execution_policy": "read_only_evidence_or_sandbox", "evidence_requirements": ["source_document_version", "interface_contract", "response_or_data_snapshot"]})
    for row in industry_oracles:
        if isinstance(row, dict):
            result.append({"oracle_id": f"industry_oracle:{row.get('oracle_id') or _short_hash(row)}", "rule_id": row.get("rule_id"), "family": row.get("oracle_family") or "industry_oracle", "assertion": row.get("expected") or row.get("assertion"), "linked_interfaces": [], "linked_tables": [], "execution_policy": "read_only_evidence_or_sandbox", "evidence_requirements": ["industry_evidence", "interface_contract", "response_or_data_snapshot"]})
    return _dedupe_by_id(result, "oracle_id")[:260]


def _probes_from_asset(asset: dict[str, Any], max_count: int = 140) -> list[dict[str, Any]]:
    interfaces = {str(row.get("interface_id")): row for row in asset.get("interfaces") or [] if isinstance(row, dict)}
    interface_edges: dict[str, list[str]] = defaultdict(list)
    for edge in asset.get("relationships") or []:
        if isinstance(edge, dict) and edge.get("relation") == "rule_to_interface":
            interface_edges[str(edge.get("from"))].append(str(edge.get("to")))
    probes: list[dict[str, Any]] = []
    for risk in asset.get("risk_domains") or []:
        if not isinstance(risk, dict):
            continue
        rule_id = str(risk.get("source_rule_id") or "")
        candidate_ids = interface_edges.get(rule_id) or list(interfaces)[:1]
        for interface_id in candidate_ids[:2]:
            operation = interfaces.get(interface_id)
            if not operation:
                continue
            method = str(operation.get("method") or "GET").upper()
            risk_type = str(risk.get("risk_type") or "business_rule")
            destructive = method in WRITE_METHODS or risk_type in {"data_conservation", "state_machine", "idempotency", "data_reconciliation"}
            execution_policy = "sandbox_required" if destructive else "candidate_only"
            probe_id = f"RP_KNOWLEDGE_{len(probes)+1:04d}"
            probes.append({
                "probe_id": probe_id,
                "source": "enterprise_business_knowledge_asset",
                "knowledge_asset_id": asset.get("asset_id"),
                "risk_type": f"enterprise_knowledge_{risk_type}",
                "knowledge_risk_type": risk_type,
                "severity": risk.get("severity") or "P1",
                "title": risk.get("title") or "企业知识规则验证",
                "method": method,
                "path": operation.get("path") or "/",
                "operation_id": operation.get("operation_id") or "",
                "actor": "secondary_identity_required" if risk_type in {"permission_boundary", "sensitive_data"} else "normal_user",
                "expected": risk.get("expected") or "业务规则必须由服务端与数据事实共同满足。",
                "bug_signal": "接口、数据表、状态机或权限事实与企业资料归纳的业务规则不一致。",
                "oracle_family": risk.get("oracle_family") or _oracle_family(risk_type),
                "oracle_assertion": risk.get("expected"),
                "destructive": destructive,
                "execution_policy": execution_policy,
                "knowledge_lineage": {"risk_id": risk.get("risk_id"), "rule_id": rule_id, "source_ids": risk.get("evidence") or [], "interface_id": interface_id},
                "evidence_requirements": ["enterprise_knowledge_asset", "source_document_version", "interface_contract", "runtime_evidence_or_sandbox_replay"],
            })
            if len(probes) >= max_count:
                return probes
    return probes


def _evidence_bundle(asset: dict[str, Any], probes: list[dict[str, Any]]) -> dict[str, Any]:
    source_versions = []
    for source in asset.get("source_inventory") or []:
        if isinstance(source, dict):
            source_versions.append({"source_id": source.get("source_id"), "source_type": source.get("source_type"), "version": source.get("version"), "content_hash": source.get("content_hash"), "parse_status": (source.get("parse") or {}).get("parse_status")})
    return {
        "phase": PHASE,
        "asset_id": asset.get("asset_id"),
        "generated_at_utc": _now(),
        "source_versions": source_versions,
        "rule_oracle_trace_count": len(asset.get("oracle_library") or []),
        "probe_trace_count": len(probes),
        "evidence_policy": {"raw_source_payload_not_embedded": True, "secret_redaction_applied_to_excerpts": True, "writes_require_sandbox": True},
        "probe_lineage": [{"probe_id": p.get("probe_id"), "risk_type": p.get("risk_type"), "lineage": p.get("knowledge_lineage"), "execution_policy": p.get("execution_policy")} for p in probes],
    }


def build_enterprise_business_knowledge_asset(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = options or {}
    registry = _load_registry(project, root)
    active = [row for row in registry.get("sources") or [] if isinstance(row, dict) and row.get("status") == "active"]
    parsed_rows = [(source, _record_parse(source, root)) for source in active]
    source_texts = {f"{source.get('source_type')}:{source.get('original_name')}": parsed.get("text") or "" for source, parsed in parsed_rows if parsed.get("text")}
    openapi_parts = [parsed.get("openapi") for _, parsed in parsed_rows if isinstance(parsed.get("openapi"), dict) and parsed.get("openapi")]
    merged_openapi = _merge_openapi(openapi_parts)
    interfaces = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("operations") or []], "interface_id")
    tables = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("tables") or []], "table_id")
    field_dictionary = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("field_dictionary") or []], "field_id")
    ui_specs = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("ui_specs") or []], "ui_spec_id")
    known_tables = {str(row.get("table_id") or "") for row in tables}
    for row in field_dictionary:
        table_id = str(row.get("table_id") or "")
        table_name = str(row.get("table") or "default")
        if table_id and table_id not in known_tables:
            grouped_fields = [item for item in field_dictionary if str(item.get("table_id") or "") == table_id]
            tables.append({
                "table_id": table_id,
                "source_id": row.get("source_id"),
                "name": table_name,
                "columns": sorted({str(item.get("field") or "") for item in grouped_fields if str(item.get("field") or "")}),
                "foreign_keys": [],
                "field_dictionary": grouped_fields,
                "tokens": sorted(_tokens(f"{table_name} {' '.join(str(item.get('field') or '') for item in grouped_fields)}")),
            })
            known_tables.add(table_id)
    permissions = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("permissions") or []], "permission_id")
    rules = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("rules") or []], "rule_id")
    roles = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("roles") or []], "role_id")
    source_states = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("state_machines") or []], "state_machine_id")
    tickets = _dedupe_by_id([row for _, parsed in parsed_rows for row in parsed.get("tickets") or []], "risk_id")
    cfg = load_real_project_config(project, root)
    industry = infer_multi_industry_business_model(source_texts, merged_openapi, cfg, project) if source_texts or interfaces else {"summary": {}, "business_objects": [], "roles": [], "state_machines": [], "permission_boundaries": [], "data_dependencies": [], "business_rules": [], "industry_oracles": [], "risk_domains": [], "recognized_industries": []}
    # The enterprise documents remain the source of truth. Inferred rows are appended
    # only as explicitly marked derived entries, never overwrite user material.
    for row in industry.get("business_rules") or []:
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        copied["rule_id"] = f"industry:{row.get('rule_id') or _short_hash(row)}"
        copied["source_id"] = "industry_inference"
        copied["source_type"] = "derived_inference"
        copied["statement"] = _redact_text(row.get("expected") or row.get("statement") or "", 720)
        copied["tokens"] = sorted(_tokens(copied["statement"]))
        copied["risk_type"] = copied.get("kind") or _risk_type_from_text(copied["statement"])
        copied["severity"] = copied.get("severity") or "P1"
        rules.append(copied)
    rules = _dedupe_by_id(rules, "rule_id")
    objects = list(industry.get("business_objects") or [])
    object_names = {str(row.get("object") or "") for row in objects if isinstance(row, dict)}
    for table in tables:
        name = str(table.get("name") or "")
        if name and name not in object_names:
            objects.append({"object": name, "source": "database_schema", "evidence": [{"source_id": table.get("source_id"), "table_id": table.get("table_id")}], "confidence": 0.62})
            object_names.add(name)
    for row in industry.get("roles") or []:
        if isinstance(row, dict):
            copied = dict(row)
            copied["role_id"] = f"industry_role:{row.get('role') or _short_hash(row)}"
            copied["source_id"] = "industry_inference"
            roles.append(copied)
    roles = _dedupe_by_id(roles, "role_id")
    derived_state_machines = [
        dict(
            row,
            state_machine_id=f"industry_state:{row.get('state_machine_id') or _short_hash(row)}",
            source_id="industry_inference",
        )
        for row in industry.get("state_machines") or []
        if isinstance(row, dict)
    ]
    state_machines = _dedupe_by_id([*source_states, *derived_state_machines], "state_machine_id")
    dependencies: list[dict[str, Any]] = []
    for table in tables:
        for target in table.get("foreign_keys") or []:
            dependencies.append({"dependency_id": f"dbdep:{_short_hash({'from': table.get('name'), 'to': target})}", "source_id": table.get("source_id"), "from": table.get("table_id"), "to": f"table:{target}", "relation": "foreign_key"})
    for row in industry.get("data_dependencies") or []:
        if isinstance(row, dict):
            dependencies.append({"dependency_id": f"industrydep:{_short_hash(row)}", "source_id": "industry_inference", "from": row.get("from") or row.get("source"), "to": row.get("to") or row.get("target"), "relation": row.get("relation") or "business_dependency"})
    dependencies = _dedupe_by_id(dependencies, "dependency_id")
    relation_edges = [
        *_links_by_overlap(rules, interfaces, "rule_id", "interface_id", relation="rule_to_interface"),
        *_links_by_overlap(rules, tables, "rule_id", "table_id", relation="rule_to_table"),
        *_links_by_overlap(interfaces, tables, "interface_id", "table_id", relation="interface_to_table"),
        *_links_by_overlap(ui_specs, interfaces, "ui_spec_id", "interface_id", relation="ui_to_interface"),
    ]
    for source in active:
        sid = str(source.get("source_id"))
        for row in [*rules, *interfaces, *tables, *field_dictionary, *ui_specs, *permissions, *state_machines]:
            if str(row.get("source_id") or "") == sid:
                node_id = row.get("rule_id") or row.get("interface_id") or row.get("table_id") or row.get("field_id") or row.get("ui_spec_id") or row.get("permission_id") or row.get("state_machine_id")
                if node_id:
                    relation_edges.append({"edge_id": f"edge:{_short_hash({'source': sid, 'node': node_id})}", "from": f"source:{sid}", "to": node_id, "relation": "source_to_asset", "confidence": 1.0, "evidence": {"source_version": source.get("version")}})
    relation_edges = _dedupe_by_id(relation_edges, "edge_id")
    oracles = _oracle_library(rules, list(industry.get("industry_oracles") or []), relation_edges)
    risks = _risk_domains(rules, tickets, industry)
    asset = {
        "phase": PHASE,
        "asset_id": f"knowledge_asset:{project}:{_short_hash({'sources': [(x.get('source_id'), x.get('content_hash'), x.get('version')) for x in active]})}",
        "project_id": project,
        "generated_at_utc": _now(),
        "source_inventory": active,
        "module_tree": _module_tree(interfaces, rules, tables, objects),
        "business_objects": objects,
        "roles": roles,
        "state_machines": state_machines,
        "interfaces": interfaces,
        "data_fields": [{"table_id": table.get("table_id"), "table": table.get("name"), "fields": table.get("columns") or [], "source_id": table.get("source_id")} for table in tables],
        "field_dictionary": field_dictionary,
        "data_tables": tables,
        "ui_design_specs": ui_specs,
        "rule_library": rules,
        "permission_matrix": permissions,
        "data_dependencies": dependencies,
        "risk_domains": risks,
        "industry_business_understanding": {"summary": industry.get("summary") or {}, "recognized_industries": industry.get("recognized_industries") or [], "risk_domains": industry.get("risk_domains") or []},
        "relationships": relation_edges,
        "oracle_library": oracles,
        "governance": {
            "no_manual_customer_industry_pack_required": True,
            "source_version_traceable": True,
            "source_deduplication_by_content_hash": True,
            "remote_fetch_disabled_without_connector": True,
            "raw_sources_not_embedded_in_report_or_evidence_bundle": True,
            "safe_live_policy": "Unknown IDs, cross-user checks, writes, replays and state transitions are planned for sandbox or human-confirmed runtime evidence.",
        },
    }
    probes = _probes_from_asset(asset, int(options.get("probe_limit") or 140))
    for probe in probes:
        lineage = probe.get("knowledge_lineage") or {}
        risk_id = lineage.get("risk_id")
        if risk_id:
            relation_edges.append({"edge_id": f"edge:{_short_hash({'risk': risk_id, 'probe': probe.get('probe_id')})}", "from": risk_id, "to": f"probe:{probe.get('probe_id')}", "relation": "risk_to_probe", "confidence": 1.0, "evidence": {"execution_policy": probe.get("execution_policy")}})
    asset["relationships"] = _dedupe_by_id(relation_edges, "edge_id")
    asset["summary"] = {
        "active_source_count": len(active),
        "source_type_distribution": dict(Counter(str(x.get("source_type") or "unknown") for x in active)),
        "module_count": len(asset["module_tree"]),
        "business_object_count": len(asset["business_objects"]),
        "role_count": len(asset["roles"]),
        "state_machine_count": len(asset["state_machines"]),
        "interface_count": len(asset["interfaces"]),
        "field_dictionary_count": len(asset["field_dictionary"]),
        "data_table_count": len(asset["data_tables"]),
        "ui_design_spec_count": len(asset["ui_design_specs"]),
        "rule_count": len(asset["rule_library"]),
        "permission_matrix_count": len(asset["permission_matrix"]),
        "data_dependency_count": len(asset["data_dependencies"]),
        "risk_domain_count": len(asset["risk_domains"]),
        "oracle_count": len(asset["oracle_library"]),
        "generated_probe_count": len(probes),
        "relationship_count": len(asset["relationships"]),
        "knowledge_ready": bool(active and (rules or interfaces or tables)),
        "claim_guard": {"absolute_understanding_allowed": False, "approved_product_language": "平台将企业资料归并为可追溯业务知识资产，并把规则、接口、数据依赖和高价值风险转化为可审计的 Bug 验证计划。", "prohibited_product_language": ["上传资料后自动完全理解所有业务", "不需要人工复核即可保证零缺陷", "覆盖全部业务 Bug"]},
    }
    bundle = _evidence_bundle(asset, probes)
    paths = _paths(project, root)
    paths["asset"].parent.mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["asset"], asset)
    _write_json(paths["probe_catalog"], {"phase": PHASE, "asset_id": asset["asset_id"], "count": len(probes), "items": probes})
    _write_json(paths["evidence_bundle"], bundle)
    _write_json(paths["asset_copy"], asset)
    paths["report"].write_text(render_enterprise_business_knowledge_report(asset), encoding="utf-8")
    paths["center_page"].write_text(render_enterprise_business_knowledge_center(project, root, asset=asset), encoding="utf-8")
    registry["audit_events"].append({"event": "rebuild_asset", "at_utc": _now(), "actor": {"name": "system", "role": "knowledge_builder"}, "asset_id": asset["asset_id"], "source_count": len(active), "probe_count": len(probes)})
    _save_registry(project, root, registry)
    return asset


def load_enterprise_business_knowledge_asset(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_paths(project, root)["asset"], {})
    return data if isinstance(data, dict) and data else None


def generate_enterprise_business_knowledge_probes(openapi: dict[str, Any], cfg: dict[str, Any] | None = None, project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    catalog = _load_json(_paths(project, root)["probe_catalog"], {})
    items = catalog.get("items") if isinstance(catalog, dict) else []
    probes = [dict(item) for item in items if isinstance(item, dict)]
    # If callers supply a fresher OpenAPI object than the asset, only retain probes
    # that still map to an available endpoint; generated contracts stay traceable.
    if isinstance(openapi, dict) and (openapi.get("paths") or {}):
        current = {(row["method"], row["path"]) for row in _openapi_operations(openapi)}
        probes = [p for p in probes if (str(p.get("method") or "GET"), str(p.get("path") or "/")) in current or p.get("path") == "/"]
    limit = int(max_count or (cfg or {}).get("max_probe_count") or 120)
    return probes[:limit]


def build_enterprise_knowledge_evidence_bundle(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_paths(project, root)["evidence_bundle"], {})
    if isinstance(data, dict) and data:
        return data
    asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    probes = generate_enterprise_business_knowledge_probes({}, {}, project, root)
    return _evidence_bundle(asset, probes)


def render_enterprise_business_knowledge_report(asset: dict[str, Any]) -> str:
    """Render a shareable, read-only business-knowledge asset report."""
    summary = asset.get("summary") or {}
    sources = list(asset.get("source_inventory") or [])
    modules = list(asset.get("module_tree") or [])
    rules = list(asset.get("rule_library") or [])
    risks = list(asset.get("risk_domains") or [])
    edges = list(asset.get("relationships") or [])
    cards = "".join([
        metric_card("资料版本", summary.get("active_source_count", len(sources)), "已去重并保留来源版本", "default", "knowledge"),
        metric_card("业务模块", summary.get("module_count", len(modules)), "由资料、接口与对象自动归并", "default", "overview"),
        metric_card("业务规则", summary.get("rule_count", len(rules)), "每条规则可反向追溯来源", "success", "assets"),
        metric_card("Oracle", summary.get("oracle_count", 0), "服务于高价值业务 Bug 验证", "success", "risk"),
    ])
    source_rows = [[
        h(row.get("source_type") or "-"), h(row.get("original_name") or "-"), h(row.get("version") or "-"),
        status_badge((row.get("parse") or {}).get("parse_status") or "unknown"), f"<code>{h(str(row.get('content_hash') or '')[:12])}</code>",
    ] for row in sources[:100]]
    module_rows = [[
        h(row.get("name") or "-"), h(len(row.get("interfaces") or [])), h("、".join(row.get("objects") or []) or "-"),
        h(len(row.get("rules") or [])), h("、".join(row.get("tables") or []) or "-"),
    ] for row in modules[:80]]
    rule_rows = [[
        status_badge(row.get("severity") or "-"), h(row.get("rule_type") or "-"), h(row.get("statement") or "-"), f"<code>{h(row.get('source_id') or '-')}</code>",
    ] for row in rules[:120]]
    risk_rows = [[
        status_badge(row.get("severity") or "-"), h(row.get("risk_type") or "-"), h(row.get("title") or "-"), h(row.get("oracle_family") or "-"),
    ] for row in risks[:120]]
    edge_rows = [[h(row.get("relation") or "-"), f"<code>{h(row.get('from') or '-')}</code>", f"<code>{h(row.get('to') or '-')}</code>", h(row.get("confidence") or "-")] for row in edges[:120]]
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("资产边界", "资料被整理为可追溯测试资产，而不是把原始文档复制到报告中。", callout("资产 ID", str(asset.get("asset_id") or "尚未生成"), "info", "knowledge"), section_id="overview")
        + section("资料版本", "每份来源保留类型、版本、解析状态和内容指纹。", table(["类型", "资料", "版本", "解析", "内容指纹"], source_rows, "暂无资料。"), section_id="knowledge")
        + section("模块与对象", "模块树连接接口、业务对象、规则和数据表。", table(["模块", "接口", "对象", "规则", "数据表"], module_rows, "暂无模块信息。"), section_id="assets")
        + section("规则库", "规则会进入 Oracle 和 Probe 生成链路，并保留资料来源。", table(["等级", "类型", "规则", "来源"], rule_rows, "暂无规则。"), section_id="risk")
        + section("风险域与 Oracle", "只沉淀能服务于高价值业务缺陷验证的风险与 Oracle。", table(["等级", "风险", "业务影响", "Oracle"], risk_rows, "暂无风险域。"), section_id="release")
        + section("资料到验证的关系", "关系图谱支持从规则回溯接口、数据表和测试探针。", table(["关系", "起点", "终点", "置信度"], edge_rows, "暂无关联关系。"), section_id="runtime")
    )
    return product_shell(
        title="企业业务知识资产报告",
        project_id=str(asset.get("project_id") or "real_project_demo"),
        active="knowledge",
        eyebrow="Enterprise knowledge asset",
        headline="把企业资料转化为可追溯、可验证的业务质量资产。",
        description="规则、状态机、权限、接口与数据依赖会统一进入 Oracle、Probe、证据与发布决策链路。",
        body=body,
        payload=asset,
        environment_label="知识资产只读视图",
        page_hint="企业业务知识资产报告",
    )

def render_enterprise_business_knowledge_center(project_id: str, root: Path | None = None, asset: dict[str, Any] | None = None) -> str:
    root = root or ROOT
    project = _safe_project_id(project_id)
    inventory = list_enterprise_knowledge_sources(project, root, include_deleted=False)
    asset = asset or load_enterprise_business_knowledge_asset(project, root) or {}
    sources = list(inventory.get("sources") or [])
    summary = asset.get("summary") or {}
    source_rows = [[
        h(source.get("source_type") or "-"), h(source.get("original_name") or "-"), h(source.get("version") or "-"),
        status_badge(source.get("status") or "unknown"), h("、".join(source.get("tags") or []) or "-"),
        f"<code>{h(source.get('source_id') or '-')}</code>",
        f"<button class='btn-delete' onclick=\"deleteSource('{h(source.get('source_id') or '')}','{h(source.get('original_name') or '')}')\" title='删除'>×</button>"
        f"<button class='btn-preview' onclick=\"previewFile('{h(source.get('source_id') or '')}','{h(source.get('original_name') or '')}','{h(source.get('source_type') or '')}','/api/knowledge/preview?source_id={h(source.get('source_id') or '')}&project={project}')\" title='预览'>👁</button>",
    ] for source in sources]
    cards = "".join([
        metric_card("已接入资料", summary.get("active_source_count", len(sources)), "PRD、OpenAPI、表结构、权限、历史 Bug 等", "default", "knowledge"),
        metric_card("业务规则", summary.get("rule_count", 0), "自动转化为可审计验证规则", "success", "assets"),
        metric_card("风险域", summary.get("risk_domain_count", 0), "优先服务高价值业务 Bug 挖掘", "warning", "risk"),
        metric_card("生成 Probe", summary.get("generated_probe_count", 0), "通过来源、规则和 Oracle 反向可解释", "default", "runtime"),
    ])
    governance = (
        "<div class='two-col'>"
        "<div class='subtle-card'><h3>资料治理</h3>" + detail_list([
            ("内容去重", "内容哈希"),
            ("版本策略", "逻辑资料名版本化"),
            ("原始资料", "项目级受控存储"),
            ("报告内容", "仅展示脱敏摘要与关系"),
        ]) + "</div>"
        "<div class='subtle-card'><h3>测试资产边界</h3>" + detail_list([
            ("高风险写入", "隔离沙箱 / 人工确认"),
            ("跨账号验证", "安全策略约束"),
            ("生产类环境", "默认禁止破坏性执行"),
            ("无效资料", "不进入风险资产"),
        ]) + "</div></div>"
    )
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("资料接入与资产重建", "企业资料会经分类、去重、版本化和关联解析，形成可解释知识资产。",
            "<div class='upload-zone' id='upload-zone'>"
            "<div class='upload-inner'>"
            "<i>" + _icon("assets") + "</i>"
            "<strong>拖拽文件到此处，或点击上传</strong>"
            "<p>全格式兼容。Office/PDF/图片(PSD/AI/RAW等)/流程图(Visio/DrawIO/BPMN等)/思维导图(XMind/FreeMind)/CAD/代码/压缩包/数据库导出 — 任意企业文件拖入即解析</p>"
            "<input type='file' id='file-input' accept='*' multiple hidden>"
            "<button class='btn btn-primary' onclick=\"document.getElementById('file-input').click()\">选择文件</button>"
            "</div>"
            "<div class='upload-status' id='upload-status'></div>"
            "</div>"
            + callout("操作需要项目知识管理员权限。", "导入后的资料会进入版本化受控存储，原始资料不会直接进入风险报告。", "info", "security"),
            section_id="overview")
        + section("已接入资料", "可查看资料类型、版本、状态、标签与资产 ID。", table(["类型", "资料", "版本", "状态", "标签", "资料 ID", "", ""], source_rows, "尚未接入资料。"), section_id="knowledge")
        + section("资产治理与安全", "知识中心不替代业务测试；它只负责把资料变成能生成 Oracle 和高价值 Probe 的可追溯输入。", governance + "<div style='margin-top:16px'><button class='btn btn-secondary' onclick='reanalyze()'><i>" + _icon("refresh") + "</i> 重新分析所有资料</button></div>", section_id="release")
    )
    return product_shell(
        title="企业业务知识中心",
        project_id=project,
        active="knowledge",
        eyebrow="Enterprise knowledge center",
        headline="让企业资料自动沉淀为可追溯的业务质量知识资产。",
        description="通过统一分类、版本、关系和来源证据，把 PRD、接口、数据、权限与历史缺陷连接到高价值 Bug 验证。",
        body=body,
        payload={"asset": asset, "inventory": inventory},
        environment_label="资料受控接入模式",
        page_hint="企业业务知识中心",
    )

def run_enterprise_knowledge_demo() -> dict[str, Any]:
    """Create a self-contained multi-source demo without external services."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        project = "knowledge_demo"
        inputs = root / "fixtures"
        inputs.mkdir(parents=True, exist_ok=True)
        (inputs / "PRD_finance.md").write_text("""# 资金结算 PRD\n租户只能访问本租户账户和账本。交易状态 initiated -> pending -> settled 或 reversed。余额、账本和交易金额必须守恒；重复回调不得重复入账。""", encoding="utf-8")
        (inputs / "api.openapi.json").write_text(json.dumps({"openapi": "3.0.3", "info": {"title": "Tenant Ledger", "version": "1"}, "paths": {"/tenants/{tenant_id}/accounts/{account_id}": {"get": {"summary": "Get tenant account balance and ledger", "responses": {"200": {"description": "ok"}}}}, "/transactions": {"post": {"summary": "Create transfer transaction", "responses": {"201": {"description": "created"}}}, "get": {"summary": "List transaction ledger", "responses": {"200": {"description": "ok"}}}}}}, ensure_ascii=False), encoding="utf-8")
        (inputs / "schema.sql").write_text("""CREATE TABLE accounts (account_id varchar(64) primary key, tenant_id varchar(64), balance decimal(18,2));\nCREATE TABLE ledger_entries (entry_id varchar(64) primary key, account_id varchar(64), amount decimal(18,2), FOREIGN KEY(account_id) REFERENCES accounts(account_id));\nCREATE TABLE transactions (transaction_id varchar(64) primary key, account_id varchar(64), status varchar(32), amount decimal(18,2), FOREIGN KEY(account_id) REFERENCES accounts(account_id));""", encoding="utf-8")
        (inputs / "permission_matrix.csv").write_text("role,resource,actions,scope\ntenant_user,account,read,own_tenant\nrisk_officer,transaction,approve,assigned_tenant\nadmin,ledger,read,all_tenants\n", encoding="utf-8")
        (inputs / "historical_bugs.json").write_text(json.dumps({"bugs": [{"title": "重复回调导致账本重复入账", "severity": "P0", "status": "fixed"}, {"title": "跨租户读取账户余额", "severity": "P0", "status": "fixed"}]}, ensure_ascii=False), encoding="utf-8")
        ingest = ingest_enterprise_knowledge_files(project, list(inputs.iterdir()), root=root, actor={"name": "demo_owner", "role": "project_owner"})
        asset = build_enterprise_business_knowledge_asset(project, root)
        probes = generate_enterprise_business_knowledge_probes({}, {}, project, root)
        return {"phase": PHASE, "ingest": {"created": len(ingest.get("created") or []), "duplicates": len(ingest.get("duplicates") or [])}, "summary": asset.get("summary"), "probe_count": len(probes), "risk_types": sorted({str(p.get("risk_type")) for p in probes}), "passed": bool(asset.get("summary", {}).get("knowledge_ready") and probes and asset.get("interfaces") and asset.get("data_tables"))}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Enterprise knowledge unified ingestion")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--ingest", nargs="*", default=[])
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--render-center", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    if args.demo:
        print(json.dumps(run_enterprise_knowledge_demo(), ensure_ascii=False, indent=2))
        return 0
    result: dict[str, Any] = {}
    if args.ingest:
        result["ingest"] = ingest_enterprise_knowledge_files(args.project, args.ingest, root=root, actor={"name": "cli", "role": "knowledge_admin"})
    if args.rebuild or args.ingest:
        result["asset"] = build_enterprise_business_knowledge_asset(args.project, root).get("summary")
    if args.render_center:
        asset = load_enterprise_business_knowledge_asset(args.project, root) or build_enterprise_business_knowledge_asset(args.project, root)
        path = _paths(args.project, root)["center_page"]
        path.write_text(render_enterprise_business_knowledge_center(args.project, root, asset), encoding="utf-8")
        result["center_page"] = str(path)
    if not result:
        result = list_enterprise_knowledge_sources(args.project, root, include_deleted=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
