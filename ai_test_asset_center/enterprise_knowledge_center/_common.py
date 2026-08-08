from __future__ import annotations

"""Phase58: Enterprise knowledge unified ingestion.

This module is intentionally small and composable. It does not introduce a
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
- test cases / plans / reports
- Feishu / Confluence exported documents or connector-provided text envelopes

No network crawling is performed. External systems must provide exported
content or a trusted connector payload to keep access control and audit scope
explicit.
"""

import argparse
import csv
import hashlib
import html
import io
import json
import logging
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

logger = logging.getLogger(__name__)

try:
    from ..multi_industry_business_reasoning import infer_multi_industry_business_model
except ImportError:
    logger.warning(
        "multi_industry_business_reasoning unavailable; industry business "
        "understanding falls back to an empty model (silent-empty is a bug, "
        "not a design choice)",
        exc_info=True,
    )

    def infer_multi_industry_business_model(*a: Any, **kw: Any) -> dict[str, Any]:
        return {"summary": {}, "business_objects": [], "roles": [], "state_machines": [],
                "permission_boundaries": [], "data_dependencies": [], "business_rules": [],
                "industry_oracles": [], "risk_domains": [], "recognized_industries": []}
from ..real_project_onboarding import ROOT, _html_escape, _load_json, _safe_project_id, _write_json, config_paths, load_real_project_config
from ..product_ui import _icon, callout, detail_list, empty_state, h, metric_card, product_shell, section, status_badge, table

__all__ = [
    "ROOT", "_html_escape", "_load_json", "_safe_project_id", "_write_json",
    "config_paths", "load_real_project_config",
    "_icon", "callout", "detail_list", "empty_state", "h", "metric_card",
    "product_shell", "section", "status_badge", "table",
    "infer_multi_industry_business_model",
    "logger",
    "PHASE", "PARSER_RECEIPT_SCHEMA", "SOURCE_TYPES",
    "TEXT_SUFFIXES", "SOURCE_CODE_SUFFIXES", "MAX_SOURCE_BYTES", "SAFE_METHODS", "WRITE_METHODS",
    "MARKDOWN_API_ENDPOINT_RE", "SVG_TEXT_RE", "SVG_TAG_ATTR_RE",
    "SVG_TITLE_RE", "SVG_DESC_RE", "ROLE_WORDS", "RISK_TERMS",
    "SECRET_PATTERNS", "SEMANTIC_LEXICON_PATH", "_SEMANTIC_LEXICON_CACHE",
]

PHASE = "phase58_enterprise_knowledge_unified_ingestion"
PARSER_RECEIPT_SCHEMA = "qualibug.parser-receipt.v1"
SOURCE_TYPES = {
    "prd", "mrd", "openapi", "markdown_api", "postman", "har",
    "source_code",
    "application_log", "database_schema", "db_field_dictionary",
    "permission_matrix", "historical_bug", "ticket",
    "test_case", "test_plan", "test_report",
    "uiux_spec", "uiux_svg",
    "db_design", "business_rules", "ui_design", "test_data",
    "config", "deploy",
    "feishu_document", "confluence_document",
    "collaboration_document", "other_document", "other",
    "uiux_requirements",
}
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".html", ".htm", ".yaml", ".yml", ".csv", ".sql", ".json", ".xml", ".svg", ".har", ".log", ".xlsx", ".xls"}
SOURCE_CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp",
    ".cs", ".dart", ".ex", ".exs", ".fs", ".fsx", ".go",
    ".groovy", ".java", ".js", ".jsx", ".kt", ".kts", ".m",
    ".mm", ".php", ".pl", ".pm", ".ps1", ".py", ".rb", ".rs",
    ".scala", ".sc", ".sh", ".swift", ".ts", ".tsx", ".vb", ".vue",
    ".svelte",
}
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
    "data_conservation": [
        "balance",
        "amount",
        "ledger",
        "inventory",
        "stock",
        "quota",
        "available_qty",
        "locked_qty",
        "金额",
        "余额",
        "账本",
        "库存",
        "额度",
        "数量",
        "负数",
        "非负",
        "守恒",
    ],
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
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+|basic\s+)?)([^\s,;]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(token\s*[:=]\s*)([^\s,;]+)"),
]
# The one canonical lexicon, shared with behavior_ir, business_state_graph,
# bug_pattern_library, enterprise_test_knowledge, phase103 and
# runtime_interface_discovery. A second copy used to sit under this package's own
# policies/ directory: the two were read by disjoint module sets, 30 verb keys
# carried conflicting meanings, and each held vocabulary the other lacked. Adding
# a term to one silently left the other half of the product not knowing it.
SEMANTIC_LEXICON_PATH = (
    Path(__file__).resolve().parents[1] / "policies" / "semantic_lexicon.json"
)
_SEMANTIC_LEXICON_CACHE: dict[str, Any] | None = None
