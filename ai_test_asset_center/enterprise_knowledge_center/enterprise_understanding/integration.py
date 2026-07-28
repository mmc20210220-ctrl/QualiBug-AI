"""Install enterprise understanding as a first-class knowledge-center stage."""
from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from .builder import build_enterprise_understanding_model
from .closure import apply_minimum_understanding_closure
from .schema import as_dict, as_list, text


def _parsed_sources_for_context(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Re-read registered sources and attach source-preserving structure IR.

    Existing parsers remain the source of extracted business facts. DOCX and PDF
    immutable source bytes are additionally parsed into Document Structure IR so
    native headings, page coordinates, list levels, tables and reading-order evidence
    are available to context resolution and completeness gates.
    """
    from .._crud import _record_parse

    parsed_sources: list[dict[str, Any]] = []
    for source in as_list(asset.get("source_inventory")):
        if not isinstance(source, dict) or text(source.get("status")) != "active":
            continue
        stored = root / text(source.get("stored_path"))
        parsed = _record_parse(source, root)
        parser_receipt = as_dict(parsed.get("parser_receipt"))
        filename = text(source.get("original_name") or stored.name)
        document_structure = as_dict(parsed.get("document_structure"))
        structure_error: dict[str, Any] = {}
        suffix = stored.suffix.lower() if stored.exists() else Path(filename).suffix.lower()
        if stored.exists() and suffix == ".docx":
            try:
                from .._document_structure_ir_normalizer import (
                    extract_normalized_docx_document_ir,
                )

                document_structure = extract_normalized_docx_document_ir(
                    stored.read_bytes(), filename=filename
                )
            except Exception as exc:
                structure_error = {
                    "code": "DOCX_DOCUMENT_STRUCTURE_IR_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "operator_action": "inspect DOCX integrity and python-docx compatibility",
                }
        elif stored.exists() and suffix == ".pdf":
            try:
                from .._pdf_document_structure_ir import extract_pdf_document_ir

                document_structure = extract_pdf_document_ir(
                    stored.read_bytes(), filename=filename
                )
            except Exception as exc:
                structure_error = {
                    "code": "PDF_DOCUMENT_STRUCTURE_IR_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "operator_action": (
                        "inspect PDF integrity, encryption and pypdf layout compatibility"
                    ),
                }
        parsed_sources.append(
            {
                "source_id": source.get("source_id"),
                "filename": filename,
                "source_locator": parser_receipt.get("source_locator"),
                # Keep the parser's original text for the legacy text-range context
                # stage. IR context uses document_structure blocks directly.
                "text": parsed.get("text") or "",
                "document_structure": document_structure,
                "document_structure_error": structure_error,
            }
        )
    return parsed_sources


def _attach_document_structure_assets(
    asset: dict[str, Any], parsed_sources: Iterable[dict[str, Any]]
) -> None:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for source in parsed_sources:
        if not isinstance(source, dict):
            continue
        structure = as_dict(source.get("document_structure"))
        if structure:
            rows.append(
                {
                    "source_id": source.get("source_id"),
                    "filename": source.get("filename"),
                    **structure,
                }
            )
        error = as_dict(source.get("document_structure_error"))
        if error:
            errors.append(
                {
                    "source_id": source.get("source_id"),
                    "filename": source.get("filename"),
                    **error,
                }
            )
    block_count = sum(len(as_list(row.get("blocks"))) for row in rows)
    unsupported_count = sum(
        int(as_dict(row.get("structure_receipt")).get("unsupported_content_count") or 0)
        for row in rows
    )
    page_count = sum(
        int(as_dict(row.get("structure_receipt")).get("page_count") or 0)
        for row in rows
    )
    scanned_page_count = sum(
        int(as_dict(row.get("structure_receipt")).get("scanned_page_count") or 0)
        for row in rows
    )
    image_count = sum(
        int(as_dict(row.get("structure_receipt")).get("image_count") or 0)
        for row in rows
    )
    table_region_count = sum(
        int(as_dict(row.get("structure_receipt")).get("table_region_count") or 0)
        for row in rows
    )
    multi_column_page_count = sum(
        int(as_dict(row.get("structure_receipt")).get("multi_column_page_count") or 0)
        for row in rows
    )
    critical_structure_gap_count = sum(
        1
        for row in rows
        for gap in as_list(row.get("unsupported_content"))
        if isinstance(gap, dict)
        and int(gap.get("count") or 0) > 0
        and bool(gap.get("blocks_formal_understanding"))
    )
    asset["document_structure_assets"] = {
        "schema": "qualibug.enterprise-document-structure-assets.v1",
        "source_count": len(rows),
        "block_count": block_count,
        "page_count": page_count,
        "scanned_page_count": scanned_page_count,
        "image_count": image_count,
        "table_region_count": table_region_count,
        "multi_column_page_count": multi_column_page_count,
        "unsupported_content_count": unsupported_count,
        "critical_structure_gap_count": critical_structure_gap_count,
        "items": rows,
        "errors": errors,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }


def enrich_asset_with_enterprise_understanding(
    asset: dict[str, Any],
    *,
    parsed_sources: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach document structure/context, reconcile conflicts, then compile cognition."""
    source_rows = list(parsed_sources or [])
    if parsed_sources is not None:
        from .._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
        from .._chinese_document_context import apply_chinese_document_context
        from .._document_ir_context import apply_document_ir_context

        _attach_document_structure_assets(asset, source_rows)
        asset = apply_document_ir_context(asset, source_rows)
        # The legacy text-range stage remains as a lower-fidelity supplement for
        # formats that do not yet emit rich structure blocks.
        asset = apply_chinese_document_context(asset, source_rows)
        # Either context stage may promote previously pending facts. Re-run the
        # existing conflict authority before promoted facts enter the model.
        asset = reconcile_chinese_business_fact_conflicts(asset)

    model = build_enterprise_understanding_model(asset)
    model = apply_minimum_understanding_closure(model, asset)
    model_gate = as_dict(model.get("gate"))
    asset["enterprise_understanding_model"] = model

    comprehension_gate = as_dict(asset.get("enterprise_comprehension_gate"))
    prior_status = text(comprehension_gate.get("status")) or "UNKNOWN"
    prior_ready = bool(comprehension_gate.get("entry_allowed", True))
    comprehension_gate["understanding_model"] = model_gate
    comprehension_gate["entry_allowed"] = prior_ready and bool(model_gate.get("entry_allowed"))
    if prior_ready and not bool(model_gate.get("entry_allowed")):
        comprehension_gate["status"] = text(model_gate.get("status")) or "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INCOMPLETE"
        comprehension_gate["required_operator_action"] = model_gate.get("required_operator_action")
    else:
        comprehension_gate["upstream_status_before_understanding_model"] = prior_status
    asset["enterprise_comprehension_gate"] = comprehension_gate

    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and text(row.get("kind"))
        not in {
            "ENTERPRISE_UNDERSTANDING_MODEL_PARTIAL",
            "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INCOMPLETE",
        }
    ]
    model_status = text(model_gate.get("status"))
    if model_status != "PASS":
        blocked = model_status.startswith("BLOCKED")
        gaps.append(
            {
                "kind": (
                    "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INCOMPLETE"
                    if blocked
                    else "ENTERPRISE_UNDERSTANDING_MODEL_PARTIAL"
                ),
                "gap_type": "enterprise_understanding_model_not_closed",
                "source_id": "*",
                "model_id": model.get("model_id"),
                "model_status": model_status,
                "blocking_reasons": model_gate.get("blocking_reasons") or [],
                "critical_unknown_count": len(model_gate.get("critical_unknowns") or []),
                "unresolved_conflict_count": len(model_gate.get("unresolved_conflicts") or []),
                "operator_action": model_gate.get("required_operator_action"),
            }
        )
    asset["coverage_gaps"] = gaps

    structure_assets = as_dict(asset.get("document_structure_assets"))
    ir_receipt = as_dict(asset.get("document_ir_context_resolution_receipt"))
    summary = as_dict(asset.get("summary"))
    summary.update(
        {
            "enterprise_understanding_model_id": model.get("model_id"),
            "enterprise_understanding_status": model_status,
            "enterprise_understanding_ready": bool(model_gate.get("entry_allowed")),
            "understood_business_object_count": len(model.get("business_objects") or []),
            "understood_actor_count": len(model.get("actors") or []),
            "understood_operation_count": len(model.get("operations") or []),
            "understood_object_relation_count": len(model.get("object_relations") or []),
            "understood_lifecycle_count": len(model.get("lifecycles") or []),
            "understood_process_count": len(model.get("processes") or []),
            "enterprise_understanding_unknown_count": len(model.get("unknowns") or []),
            "enterprise_understanding_conflict_count": len(model.get("conflicts") or []),
            "enterprise_understanding_projection": as_dict(model.get("metrics")).get("model_completeness_projection"),
            "enterprise_understanding_projection_contract": "INTERNAL_MODEL_CLOSURE_NOT_RECALL_OR_ACCURACY",
            "document_structure_source_count": int(structure_assets.get("source_count") or 0),
            "document_structure_block_count": int(structure_assets.get("block_count") or 0),
            "document_structure_page_count": int(structure_assets.get("page_count") or 0),
            "document_structure_scanned_page_count": int(
                structure_assets.get("scanned_page_count") or 0
            ),
            "document_structure_image_count": int(structure_assets.get("image_count") or 0),
            "document_structure_table_region_count": int(
                structure_assets.get("table_region_count") or 0
            ),
            "document_structure_multi_column_page_count": int(
                structure_assets.get("multi_column_page_count") or 0
            ),
            "document_structure_critical_gap_count": int(
                structure_assets.get("critical_structure_gap_count") or 0
            ),
            "document_structure_unsupported_content_count": int(
                structure_assets.get("unsupported_content_count") or 0
            ),
            "document_ir_context_resolved_fact_count": int(
                ir_receipt.get("resolved_fact_count") or 0
            ),
            "document_ir_context_unresolved_fact_count": int(
                ir_receipt.get("unresolved_fact_count") or 0
            ),
        }
    )
    asset["summary"] = summary

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "enterprise_understanding_model_is_first_class": True,
            "enterprise_understanding_source_authority": "original_chinese_source_span",
            "enterprise_understanding_does_not_infer_from_document_order": True,
            "enterprise_understanding_does_not_infer_from_token_similarity": True,
            "enterprise_understanding_unknowns_fail_visible": True,
            "enterprise_understanding_projection_is_not_recall": True,
            "field_or_entity_inventory_alone_cannot_pass_understanding_gate": True,
            "document_context_resolves_before_understanding_model": parsed_sources is not None,
            "document_context_promotions_are_conflict_reconciled": parsed_sources is not None,
            "docx_native_structure_ir_enabled": parsed_sources is not None,
            "pdf_page_layout_structure_ir_enabled": parsed_sources is not None,
            "pdf_scanned_pages_fail_closed": True,
            "pdf_text_coordinates_are_formal_evidence": True,
            "pdf_multi_column_reading_order_is_projection": True,
            "document_ir_context_precedes_text_context": parsed_sources is not None,
            "document_ir_filename_context_forbidden": True,
            "document_ir_order_is_not_business_flow": True,
            "headers_and_footers_excluded_from_business_fact_flow": True,
        }
    )
    asset["governance"] = governance
    return asset


def _persist(asset: dict[str, Any], *, project_id: str, root: Path) -> None:
    from .. import _api
    from .._common import _write_json
    from .._utils import _paths

    paths = _paths(project_id, root)
    for key in ("asset", "asset_copy"):
        path = paths.get(key)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, asset)
    report = paths.get("report")
    if report:
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(_api.render_enterprise_business_knowledge_report(asset), encoding="utf-8")
    center_page = paths.get("center_page")
    if center_page:
        Path(center_page).parent.mkdir(parents=True, exist_ok=True)
        Path(center_page).write_text(
            _api.render_enterprise_business_knowledge_center(project_id, root, asset=asset),
            encoding="utf-8",
        )


def install_enterprise_understanding_model():
    """Wrap the current build authority after Chinese fact conflict reconciliation."""
    from .. import _api
    from .._common import ROOT, _safe_project_id

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, "_qualibug_enterprise_understanding_model", False):
        return current
    original = current

    @wraps(original)
    def wrapped(
        project_id: str = "real_project_demo",
        root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        asset = original(project, resolved_root, options or {})
        parsed_sources = _parsed_sources_for_context(asset, resolved_root)
        enriched = enrich_asset_with_enterprise_understanding(
            asset,
            parsed_sources=parsed_sources,
        )
        _persist(enriched, project_id=project, root=resolved_root)
        return enriched

    wrapped._qualibug_enterprise_understanding_model = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "enrich_asset_with_enterprise_understanding",
    "install_enterprise_understanding_model",
]
