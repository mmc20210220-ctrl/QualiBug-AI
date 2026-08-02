"""Project/root/knowledge asset helpers for the private-pilot service.

This module is the canonical owner of private-pilot project identity helpers,
knowledge transport metadata and the small filesystem projections consumed by
the HTTP composition root. Callers import these first-class helpers directly;
there is no runtime patch or compatibility fallback layer.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .enterprise_material_formats import (
    ENTERPRISE_ARCHIVE_TRANSPORT_SUFFIXES,
    ENTERPRISE_BINARY_DOCUMENT_SUFFIXES,
    ENTERPRISE_TEXT_DOCUMENT_SUFFIXES,
)
from .private_pilot_json_io import _read_json_object
from .real_project_onboarding import ROOT, _safe_project_id

MASKED_CREDENTIAL_VALUE = "********"

# Keep the customer ingest boundary aligned with the canonical enterprise knowledge center
# vocabulary. The UI never asks customers to choose one of these values; they are returned
# for diagnostics and accepted only as an exceptional API override for compatibility.
KNOWLEDGE_INGEST_SOURCE_TYPES = (
    "prd",
    "mrd",
    "openapi",
    "markdown_api",
    "postman",
    "har",
    "application_log",
    "database_schema",
    "db_field_dictionary",
    "permission_matrix",
    "historical_bug",
    "ticket",
    "test_case",
    "test_plan",
    "test_report",
    "uiux_spec",
    "uiux_svg",
    "db_design",
    "business_rules",
    "ui_design",
    "test_data",
    "config",
    "deploy",
    "feishu_document",
    "confluence_document",
    "collaboration_document",
    "other_document",
)
KNOWLEDGE_SOURCE_TYPE_ALIASES = {
    "swagger": "openapi",
    "api": "openapi",
    "api_document": "markdown_api",
    "db_schema": "database_schema",
    "schema": "database_schema",
    "permission": "permission_matrix",
    "bugs": "historical_bug",
    "testcase": "test_case",
    "testcases": "test_case",
    "test_case_document": "test_case",
    "testplan": "test_plan",
    "testreport": "test_report",
    "other": "other_document",
}

# Transport acceptance is sourced from one canonical registry. Semantic fidelity remains
# fail-visible in each document parser, normalizer or archive receipt.
KNOWLEDGE_INGEST_TEXT_EXTENSIONS = tuple(sorted(ENTERPRISE_TEXT_DOCUMENT_SUFFIXES))
KNOWLEDGE_INGEST_BINARY_EXTENSIONS = tuple(sorted(ENTERPRISE_BINARY_DOCUMENT_SUFFIXES))
KNOWLEDGE_INGEST_ARCHIVE_EXTENSIONS = tuple(
    sorted(ENTERPRISE_ARCHIVE_TRANSPORT_SUFFIXES)
)
KNOWLEDGE_INGEST_EXTENSIONS = (
    KNOWLEDGE_INGEST_TEXT_EXTENSIONS
    + KNOWLEDGE_INGEST_BINARY_EXTENSIONS
    + KNOWLEDGE_INGEST_ARCHIVE_EXTENSIONS
)
ONBOARD_DOCUMENT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".pdf",
    ".docx",
    ".html",
    ".htm",
)
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
    return Path(configured).expanduser().resolve() if configured else ROOT


def _load_real_project_discovery_payload(
    root: Path,
    project_id: str,
) -> dict[str, Any] | None:
    project = _safe_project_id(project_id)
    candidates = (
        root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "real_project" / "real_project_defect_data.json",
        root / "platform_workspace" / project / "defect_discovery" / "continuous_discovery_state.json",
    )
    resolved_root = root.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved_root != resolved and resolved_root not in resolved.parents:
            raise ValueError("project discovery path escaped private root")
        if not resolved.exists() or not resolved.is_file():
            continue
        payload = _read_json_object(resolved)
        payload.setdefault("report_source_path", resolved.relative_to(resolved_root).as_posix())
        return payload
    return None


def _write_env_local(updates: dict[str, str]) -> Path:
    """Atomically persist deployment-local settings at the configured path.

    Authorization and tenancy are enforced by the HTTP settings authority. This
    helper owns only deterministic, crash-safe file replacement.
    """

    configured = os.environ.get("QUALIBUG_ENV_LOCAL_PATH", "").strip()
    env_path = (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[1] / ".env.local"
    )
    lines = (
        env_path.read_text(encoding="utf-8").splitlines()
        if env_path.exists()
        else [
            "# Local-only QualiBug LLM credentials.",
            "# This file is ignored by git. Do not share it.",
            "",
        ]
    )
    normalized_updates = {
        str(key).strip().upper(): str(value)
        for key, value in updates.items()
        if str(key).strip()
    }
    keys = set(normalized_updates)
    written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        raw = line.strip()
        key = raw.split("=", 1)[0].strip().upper() if "=" in raw and not raw.startswith("#") else ""
        if key in keys:
            new_lines.append(f"{key}={normalized_updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key in sorted(keys - written):
        new_lines.append(f"{key}={normalized_updates[key]}")
    serialized = "\n".join(new_lines).rstrip() + "\n"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=env_path.parent,
            prefix=f".{env_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, env_path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return env_path


def _known_project_exists(root: Path, project: str) -> bool:
    safe_project = _safe_project_id(project)
    candidates = (
        root / "platform_inputs" / safe_project / "real_project_config.json",
        root / "platform_outputs" / safe_project,
        root / "platform_workspace" / safe_project,
    )
    resolved_root = root.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved_root != resolved and resolved_root not in resolved.parents:
            raise ValueError("known-project path escaped private root")
        if resolved.exists():
            return True
    return False


def _project_output_dir_for_import(root: Path, project_id: str) -> tuple[str, Path]:
    safe_project = _safe_project_id(project_id)
    platform_outputs = (root / "platform_outputs").resolve()
    output_dir = (platform_outputs / safe_project).resolve()
    if platform_outputs != output_dir and platform_outputs not in output_dir.parents:
        raise ValueError("project output path escaped platform_outputs")
    return safe_project, output_dir


def _knowledge_asset_sources(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    inventory = asset.get("source_inventory") or asset.get("sources") or []
    if not isinstance(inventory, list):
        return rows
    resolved_root = root.resolve()
    for item in inventory:
        if not isinstance(item, dict):
            continue
        source_ref = str(
            item.get("source_ref") or item.get("external_ref") or ""
        ).strip()
        source_origin = str(item.get("source_origin") or "").strip().upper()
        if source_origin not in {"ONLINE_CONNECTOR", "DOCUMENT_REFERENCE"}:
            source_origin = (
                "ONLINE_CONNECTOR"
                if source_ref.startswith("connector://")
                else "DOCUMENT_REFERENCE"
            )
        raw_permission_scope = item.get("permission_scope")
        permission_scope: dict[str, Any] = {}
        if isinstance(raw_permission_scope, dict):
            for key in (
                "visibility",
                "availability",
                "evidence_status",
                "acl_version",
            ):
                value = str(raw_permission_scope.get(key) or "").strip()[:240]
                if value:
                    permission_scope[key] = value.upper() if key != "acl_version" else value
            for key in ("complete", "propagation_allowed"):
                if key in raw_permission_scope:
                    permission_scope[key] = raw_permission_scope.get(key) is True
            permission_scope["raw_remote_principals_returned"] = False
        stored_path = str(item.get("stored_path") or item.get("path") or "").strip()
        path: Path | None = None
        if stored_path:
            candidate = Path(stored_path)
            path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
            if resolved_root != path and resolved_root not in path.parents:
                path = None
        size = (
            path.stat().st_size
            if path is not None and path.exists() and path.is_file()
            else int(item.get("size_bytes") or 0)
        )
        rows.append(
            {
                "source_id": str(item.get("source_id") or item.get("id") or ""),
                "source_ref": source_ref,
                "source_origin": source_origin,
                "filename": str(
                    item.get("original_name")
                    or item.get("filename")
                    or item.get("name")
                    or ""
                ),
                "source_type": str(item.get("source_type") or item.get("type") or ""),
                "status": str(item.get("status") or "active"),
                "size_bytes": size,
                "uploaded_at": str(
                    item.get("created_at_utc")
                    or item.get("uploaded_at")
                    or item.get("created_at")
                    or ""
                ),
                "version": item.get("version", 1),
                "created_at_utc": str(item.get("created_at_utc") or "")[:80],
                "updated_at_utc": str(
                    item.get("updated_at_utc")
                    or item.get("last_seen_at_utc")
                    or item.get("created_at_utc")
                    or ""
                )[:80],
                "last_seen_at_utc": str(item.get("last_seen_at_utc") or "")[:80],
                "source_updated_at": str(item.get("source_updated_at") or "")[:240],
                "permission_scope": permission_scope,
                "parse_status": str(
                    (item.get("parse") or {}).get("parse_status")
                    or item.get("parse_status")
                    or ""
                ),
            }
        )
    return rows


def _normalize_frontend_page_path(path: str) -> str:
    """Map retired server-rendered aliases onto the React console routes."""

    clean = "/" + str(path or "/").strip().strip("/")
    return {
        "/knowledge": "/materials",
        "/benchmark": "/coverage",
        "/onboard": "/products",
    }.get(clean, clean)


def resolve_knowledge_source_type(
    filename: str,
    extracted_text: str,
    explicit_type: str | None = None,
) -> tuple[str, str]:
    """Delegate source classification to the one enterprise-knowledge authority."""

    from .enterprise_knowledge_center import classify_enterprise_knowledge_source

    requested = str(explicit_type or "").strip().lower()
    requested = KNOWLEDGE_SOURCE_TYPE_ALIASES.get(requested, requested)
    if requested and requested not in KNOWLEDGE_INGEST_SOURCE_TYPES:
        raise ValueError("unsupported source_type")
    detected = classify_enterprise_knowledge_source(
        str(filename or ""),
        str(extracted_text or ""),
        requested or None,
    )
    if detected not in KNOWLEDGE_INGEST_SOURCE_TYPES:
        detected = "other_document"
    return detected, "explicit" if requested else "automatic"


def _onboard_allow_degraded_document_intelligence() -> bool:
    return _truthy_env("QUALIBUG_ONBOARD_ALLOW_DEGRADED_DOCUMENT_INTELLIGENCE")


def load_project_artifacts(project_id: str, root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    safe = _safe_project_id(project_id)
    workspace = (resolved_root / "platform_workspace" / safe).resolve()
    if resolved_root != workspace and resolved_root not in workspace.parents:
        raise ValueError("project workspace escaped private root")
    return {
        "project": _read_json_object(workspace / "private_pilot_project.json"),
        "scan": _read_json_object(workspace / "latest_scan.json"),
        "runs": _read_json_object(workspace / "runs.json"),
        "sut_settings": _read_json_object(workspace / "sut_settings.json"),
        "workspace": workspace,
    }


__all__ = [
    "MASKED_CREDENTIAL_VALUE",
    "KNOWLEDGE_INGEST_SOURCE_TYPES",
    "KNOWLEDGE_SOURCE_TYPE_ALIASES",
    "KNOWLEDGE_INGEST_TEXT_EXTENSIONS",
    "KNOWLEDGE_INGEST_BINARY_EXTENSIONS",
    "KNOWLEDGE_INGEST_ARCHIVE_EXTENSIONS",
    "KNOWLEDGE_INGEST_EXTENSIONS",
    "ONBOARD_DOCUMENT_EXTENSIONS",
    "ONBOARD_OPENAPI_EXTENSIONS",
    "resolve_knowledge_source_type",
    "_is_masked_credential_value",
    "_credential_update_value",
    "_first_text",
    "_truthy_env",
    "_extensions_label",
    "_extensions_accept",
    "_root",
    "_load_real_project_discovery_payload",
    "_write_env_local",
    "_known_project_exists",
    "_project_output_dir_for_import",
    "_knowledge_asset_sources",
    "_normalize_frontend_page_path",
    "_onboard_allow_degraded_document_intelligence",
    "load_project_artifacts",
]
