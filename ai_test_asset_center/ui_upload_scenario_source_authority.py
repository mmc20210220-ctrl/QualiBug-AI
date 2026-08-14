"""Bind UI upload scenarios to the canonical enterprise knowledge source registry.

The settings page and discovery asset use ``enterprise_knowledge_center`` source
identities. The chunk-oriented ``enterprise_source_registry`` is a secondary index
and must not become an independent executable authority. This installer replaces
the scenario registry's source lookup with the versioned knowledge-center record
used by Behavior IR and the formal UI source guard.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ui_upload_scenario_registry as _scenarios
from .enterprise_knowledge_center import list_enterprise_knowledge_sources

_INSTALL_MARKER = "_qualibug_upload_scenario_source_authority_installed"
_ORIGINAL_MARKER = "_qualibug_upload_scenario_source_identity_before_authority"


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def install_ui_upload_scenario_source_authority() -> None:
    if getattr(_scenarios, _INSTALL_MARKER, False):
        return
    original = getattr(
        _scenarios,
        _ORIGINAL_MARKER,
        _scenarios._source_identity,
    )
    setattr(_scenarios, _ORIGINAL_MARKER, original)

    def canonical_knowledge_source_identity(
        project: str,
        root: Path,
        source_id: str,
    ) -> dict[str, str]:
        identity = _text(source_id, limit=160)
        payload = list_enterprise_knowledge_sources(
            project,
            root=Path(root),
            include_deleted=True,
        )
        matches = [
            row
            for row in payload.get("sources", [])
            if isinstance(row, dict)
            and (
                _text(row.get("source_id"), limit=160) == identity
                or _text(row.get("canonical_source_id"), limit=160) == identity
            )
        ]
        if len(matches) != 1:
            raise KeyError("enterprise_source_not_found")
        row = matches[0]
        status = _text(row.get("status"), limit=40).lower()
        if status != "active":
            raise RuntimeError("ui_upload_scenario_source_version_changed")
        source_hash = _text(row.get("content_hash"), limit=64).lower()
        version = _text(row.get("version"), limit=80)
        if len(source_hash) != 64 or not version:
            raise RuntimeError("enterprise_source_identity_incomplete")
        return {
            "source_id": identity,
            "source_hash": source_hash,
            "source_version_id": f"knowledge-source:{identity}:v{version}",
            "source_type": _text(row.get("source_type"), limit=80),
        }

    _scenarios._source_identity = canonical_knowledge_source_identity
    setattr(_scenarios, _INSTALL_MARKER, True)


__all__ = ["install_ui_upload_scenario_source_authority"]
