"""Project/root/knowledge asset helpers for the private-pilot service."""
from __future__ import annotations

import os
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
ONBOARD_DOCUMENT_EXTENSIONS = (".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm")


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
    return str(os.environ.get("QUALIBUG_ONBOARD_ALLOW_DEGRADED_DOCUMENT_INTELLIGENCE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def load_project_artifacts(project_id: str, root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    safe = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / safe
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
    "resolve_knowledge_source_type",
    "_onboard_allow_degraded_document_intelligence",
    "load_project_artifacts",
]
