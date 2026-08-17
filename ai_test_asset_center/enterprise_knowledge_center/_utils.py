"""Low-level utilities: lexicon, hashing, file decoding, format detection."""
from __future__ import annotations

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
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

try:
    import docx2txt
except ImportError:
    docx2txt = None

from ._common import *  # noqa: F401,F403
from ._common import _safe_project_id, _load_json, _write_json  # explicit: underscore names not exported by *
from ._common import _SEMANTIC_LEXICON_CACHE  # noqa: F401

# Ensure underscore-prefixed helpers are exported via `from ._utils import *`
__all__ = [
    "_clean_markup_text", "_contains_markdown_api_sections", "_csv_rows",
    "_decode_docx", "_decode_pdf", "_dedupe_by_id",
    "_detected_source_format", "_hash_bytes", "_json_or_none", "_lexicon_dict", "_lexicon_list",
    "_load_registry", "_looks_like_field_dictionary", "_looks_like_uiux_spec", "_norm",
    "_normalize_state_token", "_now", "_parser_receipt", "_paths", "_read_source_bytes",
    "_redact_text", "_registry_default", "_require_manage_actor", "_safe_actor", "_safe_slug",
    "_save_registry", "_semantic_lexicon", "_short_hash", "_tokens",
    "_safe_project_id", "_load_json", "_write_json",
    "ENGLISH_STATE_TOKENS", "CHINESE_STATE_HINTS",
]


def _semantic_lexicon() -> dict[str, Any]:
    """Load the semantic lexicon, reporting loudly when it is absent or empty.

    The file used to be missing entirely, and ``_load_json(path, {})`` made that
    indistinguishable from a lexicon that had nothing to say: all 19 ``_lexicon_*`` call
    sites across _parsing.py, _utils.py, business_state_graph.py and
    supplementary_behavior_slices.py received an empty list or dict, so a whole
    policy-driven comprehension layer was inert with no signal anywhere. Two of its
    failure modes are worse than a gap -- a FORBIDDEN state transition was classified as
    an ALLOWED one, and the typed-constraint extractor returned nothing unconditionally.

    Logged at error level rather than raised: the callers are spread across every parsing
    path, and a raise would take down comprehension entirely for a deployment whose
    lexicon is genuinely absent. What was missing was the SIGNAL, not the tolerance.
    """
    global _SEMANTIC_LEXICON_CACHE
    if _SEMANTIC_LEXICON_CACHE is None:
        exists = False
        try:
            exists = SEMANTIC_LEXICON_PATH.is_file()
        except OSError:
            exists = False
        data = _load_json(SEMANTIC_LEXICON_PATH, {})
        _SEMANTIC_LEXICON_CACHE = data if isinstance(data, dict) else {}
        if not exists:
            logging.getLogger(__name__).error(
                "semantic lexicon file is MISSING at %s -- enterprise-material "
                "comprehension is degraded: state-machine sections, forbidden vs allowed "
                "transitions, permission grant/deny, ownership scope, typed constraints "
                "and entity aliasing all fall back to empty vocabulary. If this is an "
                "installed copy, the packaging is not shipping "
                "enterprise_knowledge_center/policies/*.json.",
                SEMANTIC_LEXICON_PATH,
            )
        elif not _SEMANTIC_LEXICON_CACHE:
            logging.getLogger(__name__).error(
                "semantic lexicon at %s loaded EMPTY -- comprehension is degraded the "
                "same way an absent file degrades it.",
                SEMANTIC_LEXICON_PATH,
            )
    return _SEMANTIC_LEXICON_CACHE


def _lexicon_dict(name: str) -> dict[str, list[str]]:
    raw = _semantic_lexicon().get(name)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, values in raw.items():
        if isinstance(values, list):
            out[str(key)] = [str(item) for item in values if str(item)]
    return out


def _lexicon_list(name: str) -> list[str]:
    raw = _semantic_lexicon().get(name)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _detected_source_format(filename: str, source_type: str, text: str, payload: Any) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix in {"docx", "pdf", "har", "csv", "sql", "svg", "log", "xml", "html", "htm", "xlsx", "xls"}:
        return suffix
    if payload is not None:
        return "json"
    if suffix in {"yaml", "yml"}:
        return "yaml"
    if source_type == "markdown_api" or re.search(r"(?m)^#{1,6}\s+", text or ""):
        return "markdown"
    return suffix or "text"


def _parser_receipt(
    *,
    source_id: str,
    filename: str,
    source_type: str,
    parser: str,
    detected_format: str,
    text_hash: str,
    text_length: int,
    outputs: dict[str, int],
    errors: list[dict[str, Any]],
    parse_status: str,
    started_at_utc: str,
    extraction_outcome: str = "",
) -> dict[str, Any]:
    # decode_fidelity: whether the file was successfully decoded
    decode_fidelity = "full"
    if parse_status == "metadata_only":
        decode_fidelity = "metadata_only"
    elif errors:
        decode_fidelity = "degraded"
    # extraction_outcome: whether structured extraction succeeded
    if not extraction_outcome:
        structured_count = sum(
            int(outputs.get(k) or 0)
            for k in ("tables", "fields", "permissions")
        )
        if structured_count > 0:
            extraction_outcome = "EXTRACTED"
        elif parse_status == "metadata_only":
            extraction_outcome = "SKIPPED_NOT_APPLICABLE"
        else:
            extraction_outcome = "EMPTY_NO_STRUCTURE_FOUND"
    receipt_key = {
        "source_id": source_id,
        "text_hash": text_hash,
        "parser": parser,
        "detected_format": detected_format,
        "parse_status": parse_status,
        "errors": errors,
    }
    return {
        "schema_version": PARSER_RECEIPT_SCHEMA,
        "receipt_id": "parser_" + _short_hash(receipt_key, 20),
        "source_id": source_id,
        "source_type": source_type,
        "source_locator": filename,
        "detected_format": detected_format,
        "parser": parser,
        "parser_status": "degraded" if errors and parse_status != "failed" else parse_status,
        "decode_fidelity": decode_fidelity,
        "fidelity": decode_fidelity,  # backward compat alias
        "extraction_outcome": extraction_outcome,
        "text_hash": text_hash,
        "text_length": int(text_length),
        "outputs": dict(outputs),
        "errors": list(errors),
        "started_at_utc": started_at_utc,
        "completed_at_utc": _now(),
    }


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _short_hash(value: Any, size: int = 16) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:size]


@lru_cache(maxsize=32768)
def _norm_text(text: str) -> str:
    """Normalize one immutable text value; bounded memoization cannot go stale."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm(value: Any) -> str:
    return _norm_text(str(value or ""))


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
    # Wildcards in a state machine ("CLOSED -> 任意状态" = may not reach ANY
    # state) are NOT states; keeping them pollutes the state set and every
    # downstream enumeration.
    if token in {"任意状态", "任何状态", "任意"} or low in {"any_state", "any", "anywhere"}:
        return ""
    if any(marker in low for marker in ("http", "www", ".com", "/", "\\", "px", "rem", "em", "%")):
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", low):
        return ""
    if re.search(r"\d", token) and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", token):
        return ""
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,23}", token):
        return token
    state_tokens = {item.lower() for item in _lexicon_list("state_tokens")} or ENGLISH_STATE_TOKENS
    if re.fullmatch(r"[a-z][a-z0-9_]{1,23}", low):
        # Normalize English state names to UPPER_SNAKE so created/CREATED and
        # paid/PAID collapse into one canonical state instead of polluting the
        # state set with case-duplicates that break enumeration and cleanup.
        if low in state_tokens:
            return low.upper()
        return ""
    if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", token):
        state_hints = _lexicon_list("state_hints") or list(CHINESE_STATE_HINTS)
        return token if any(marker in token for marker in state_hints) else ""
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
        "reasoner_reuse": defect_workspace / "mainline_reasoner_reuse_state.json",
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
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
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
        reader = pypdf.PdfReader(io.BytesIO(blob))
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


def _csv_rows(text: str) -> list[dict[str, str]]:
    try:
        return [dict(row) for row in csv.DictReader(text.splitlines()) if isinstance(row, dict)]
    except Exception:
        return []


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
