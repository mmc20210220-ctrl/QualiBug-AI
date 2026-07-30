"""Explicit bridge that adds database-model semantics to the canonical source parser.

The native adapter remains the only container decoder. This bridge is installed by the
explicit knowledge composition root before the base compiler runs, then replaces lossy
Markdown-derived database guesses with exact Document IR facts.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

from .document_ir_database_model_semantics import enrich_parsed_database_model_semantics


def install_database_model_semantic_bridge() -> None:
    """Install one idempotent additive bridge on the canonical source parser."""
    from . import _crud
    from . import source_ingestion

    current = _crud.parse_enterprise_source
    if getattr(current, "_qualibug_database_model_semantic_bridge", False):
        return

    original = source_ingestion.parse_enterprise_source
    if getattr(original, "_qualibug_database_model_semantic_bridge", False):
        _crud.parse_enterprise_source = original
        return

    @wraps(original)
    def bridged(
        blob: bytes,
        filename: str,
        source_type: str,
        source_id: str,
    ) -> dict[str, Any]:
        parsed = original(blob, filename, source_type, source_id)
        document_ir = parsed.get("document_ir") or parsed.get("document_structure") or {}
        result = enrich_parsed_database_model_semantics(
            parsed,
            document_ir,
            source_id=source_id,
            source_type=source_type,
        )
        semantic_receipt = dict(result.get("database_model_semantic_receipt") or {})
        if not semantic_receipt:
            return result

        parser_receipt = dict(result.get("parser_receipt") or {})
        outputs = dict(parser_receipt.get("outputs") or {})
        outputs.update(
            {
                "tables": len(result.get("tables") or []),
                "fields": len(result.get("field_dictionary") or []),
                "database_model_relationships": len(
                    result.get("database_model_relationships") or []
                ),
                "database_model_indexes": len(
                    result.get("database_model_indexes") or []
                ),
            }
        )
        parser_receipt["outputs"] = outputs
        parser_receipt["database_model_semantic_receipt"] = semantic_receipt
        parser_receipt["database_model_semantics_use_document_ir"] = True
        parser_receipt["database_model_generic_markdown_guess_replaced"] = (
            str(semantic_receipt.get("status") or "") != "BLOCKED"
        )
        result["parser_receipt"] = parser_receipt
        return result

    bridged._qualibug_database_model_semantic_bridge = True  # type: ignore[attr-defined]
    bridged._qualibug_original_parse_enterprise_source = original  # type: ignore[attr-defined]
    source_ingestion.parse_enterprise_source = bridged
    _crud.parse_enterprise_source = bridged


__all__ = ["install_database_model_semantic_bridge"]
