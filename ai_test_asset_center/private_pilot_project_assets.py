"""Project/root/knowledge asset helpers for the private-pilot service."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _read_json_object
from .real_project_onboarding import ROOT, _safe_project_id

MASKED_CREDENTIAL_VALUE = "********"

KNOWLEDGE_INGEST_SOURCE_TYPES = (
    "prd",
    "mrd",
    "openapi",
    "postman",
    "database_schema",
    "permission_matrix",
    "historical_bug",
    "ticket",
    "feishu_document",
    "confluence_document",
    "collaboration_document",
    "other_document",
)
KNOWLEDGE_INGEST_TEXT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".json",
    ".csv",
    ".sql",
    ".xml",
)
KNOWLEDGE_INGEST_BINARY_EXTENSIONS = (".pdf", ".docx")
KNOWLEDGE_INGEST_EXTENSIONS = KNOWLEDGE_INGEST_TEXT_EXTENSIONS + KNOWLEDGE_INGEST_BINARY_EXTENSIONS
ONBOARD_DOCUMENT_EXTENSIONS = (".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm")
ONBOARD_OPENAPI_EXTENSIONS = (".yaml", ".yml", ".json")


def _is_masked_credential_value(value: Any) -> bool:
    return str(value or "").strip() == MASKED_CREDENTIAL_VALUE


def _credential_update_value(incoming: Any, previous: Any = "") -> str:
    text = str(incoming or "").strip()
    if not text or _is_masked_credential_value(text):
        return str(previous or "")
    return text


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _truthy_env(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _extensions_label(items: tuple[str, ...]) -> str:
    return " ".join(items)


def _extensions_accept(items: tuple[str, ...]) -> str:
    return ",".join(items)


def _root() -> Path:
    configured = os.environ.get("QUALIBUG_PRIVATE_ROOT", "").strip()
    return Path(configured).resolve() if configured else ROOT


def _load_real_project_discovery_payload(root: Path, project_id: str) -> dict[str, Any] | None:
    project = _safe_project_id(project_id)
    candidates = (
        root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "defect_discovery" / "continuous_discovery_state.json",
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = _read_json_object(candidate)
        payload.setdefault(
            "report_source_path",
            candidate.relative_to(root).as_posix() if candidate.is_relative_to(root) else str(candidate),
        )
        return payload
    return None


def _write_env_local(updates: dict[str, str]) -> Path:
    configured = os.environ.get("QUALIBUG_ENV_LOCAL_PATH", "").strip()
    env_path = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1] / ".env.local"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else [
        "# Local-only QualiBug LLM credentials.",
        "# This file is ignored by git. Do not share it.",
        "",
    ]
    keys = set(updates)
    written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        raw = line.strip()
        key = raw.split("=", 1)[0].strip().upper() if "=" in raw and not raw.startswith("#") else ""
        if key in keys:
            new_lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key in sorted(keys - written):
        new_lines.append(f"{key}={updates[key]}")
    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def _known_project_exists(root: Path, project: str) -> bool:
    project = _safe_project_id(project)
    candidates = (
        root / "platform_inputs" / project / "real_project_config.json",
        root / "platform_outputs" / project,
        root / "platform_workspace" / project,
    )
    return any(path.exists() for path in candidates)


def _project_output_dir_for_import(root: Path, project_id: str) -> tuple[str, Path]:
    safe_project_id = _safe_project_id(project_id)
    output_dir = (root / "platform_outputs" / safe_project_id).resolve()
    platform_outputs = (root / "platform_outputs").resolve()
    if platform_outputs not in output_dir.parents and output_dir != platform_outputs:
        raise ValueError("project output path escaped platform_outputs")
    return safe_project_id, output_dir


def _knowledge_asset_sources(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    inventory = asset.get("source_inventory") or asset.get("sources") or []
    if not isinstance(inventory, list):
        return rows
    for item in inventory:
        if not isinstance(item, dict):
            continue
        stored_path = str(item.get("stored_path") or item.get("path") or "")
        path = root / stored_path if stored_path and not Path(stored_path).is_absolute() else Path(stored_path) if stored_path else None
        size = path.stat().st_size if path and path.exists() and path.is_file() else int(item.get("size_bytes") or 0)
        rows.append({
            "source_id": str(item.get("source_id") or item.get("id") or ""),
            "filename": str(item.get("original_name") or item.get("filename") or item.get("name") or ""),
            "source_type": str(item.get("source_type") or item.get("type") or ""),
            "status": str(item.get("status") or "active"),
            "size_bytes": size,
            "uploaded_at": str(item.get("created_at_utc") or item.get("uploaded_at") or item.get("created_at") or ""),
            "version": item.get("version", 1),
            "parse_status": str((item.get("parse") or {}).get("parse_status") or item.get("parse_status") or ""),
        })
    return rows


def _normalize_frontend_page_path(path: str) -> str:
    clean = "/" + str(path or "/").strip().strip("/")
    return {
        "/materials": "/knowledge",
        "/evidence": "/findings",
    }.get(clean, clean)
