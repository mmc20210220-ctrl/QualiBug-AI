"""Install enterprise understanding as a first-class knowledge-center stage."""
from __future__ import annotations

from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from .builder import build_enterprise_understanding_model
from .closure import apply_minimum_understanding_closure
from .implementation_binding_projection import project_final_scenario_planning_gate
from .schema import as_dict, as_list, text


def _parsed_sources_for_context(
    asset: dict[str, Any],
    root: Path,
    *,
    parsed_overrides: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Read registered sources through the format-agnostic ingestion pipeline.

    Existing parsers provide a compatibility text projection. Immutable source bytes are
    routed through DocumentAdapter Registry -> Parsing Planner -> IR Merger. The merged
    IR text becomes the source presented to Chinese-first fact extraction, so OCR and
    future supplemental adapters can contribute source-backed business facts. An
    incremental caller may pass the already parsed source row to avoid parsing the same
    changed source twice.
    """
    from .._crud import _record_parse
    from ..document_ingestion import build_document_structure_ir

    overrides = {
        text(source_id): dict(parsed)
        for source_id, parsed in (parsed_overrides or {}).items()
        if text(source_id) and isinstance(parsed, dict)
    }
    parsed_sources: list[dict[str, Any]] = []
    for source in as_list(asset.get("source_inventory")):
        if not isinstance(source, dict) or text(source.get("status")) != "active":
            continue
        stored = root / text(source.get("stored_path"))
        source_id = text(source.get("source_id"))
        parsed = overrides.get(source_id)
        if parsed is None:
            parsed = _record_parse(source, root)
        parser_receipt = as_dict(parsed.get("parser_receipt"))
        filename = text(source.get("original_name") or stored.name)
        document_structure = as_dict(parsed.get("document_structure"))
        structure_error: dict[str, Any] = {}
        if stored.exists():
            try:
                document_structure = build_document_structure_ir(
                    stored.read_bytes(),
                    filename=filename,
                    source_id=text(source.get("source_id")),
                    declared_mime=text(source.get("mime_type") or source.get("content_type")),
                    legacy_text=text(parsed.get("text")),
                )
            except Exception as exc:
                structure_error = {
                    "code": "DOCUMENT_INGESTION_PIPELINE_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                    "operator_action": (
                        "inspect source integrity, adapter registry and parsing-plan receipts"
                    ),
                }
        merged_text = text(document_structure.get("plain_text"))
        parsed_sources.append(
            {
                "source_id": source.get("source_id"),
                "filename": filename,
                "source_locator": parser_receipt.get("source_locator"),
                "text": merged_text or parsed.get("text") or "",
                "legacy_text": parsed.get("text") or "",
                "text_authority": (
                    "merged_document_structure_ir"
                    if merged_text
                    else "legacy_parser_text_projection"
                ),
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
    visual_table_count = sum(
        int(as_dict(row.get("structure_receipt")).get("visual_table_count") or 0)
        for row in rows
    )
    formal_visual_table_count = sum(
        int(as_dict(row.get("structure_receipt")).get("formal_visual_table_count") or 0)
        for row in rows
    )
    visual_table_cell_count = sum(
        int(as_dict(row.get("structure_receipt")).get("visual_table_cell_count") or 0)
        for row in rows
    )
    unresolved_visual_table_region_count = sum(
        int(
            as_dict(row.get("structure_receipt")).get(
                "unresolved_visual_table_region_count"
            )
            or 0
        )
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
    adapter_execution_count = sum(len(as_list(row.get("adapter_receipts"))) for row in rows)
    adapter_names = sorted(
        {
            text(receipt.get("adapter_name"))
            for row in rows
            for receipt in as_list(row.get("adapter_receipts"))
            if isinstance(receipt, dict) and text(receipt.get("adapter_name"))
        }
    )
    plan_status_distribution: dict[str, int] = {}
    for row in rows:
        status = text(as_dict(row.get("parsing_plan")).get("status")) or "UNKNOWN"
        plan_status_distribution[status] = plan_status_distribution.get(status, 0) + 1
    ocr_resolved_pages = sorted(
        {
            int(page)
            for row in rows
            for resolution in as_list(row.get("applied_gap_resolutions"))
            if isinstance(resolution, dict)
            and text(resolution.get("reason_code")) == "SCANNED_PAGE_REQUIRES_OCR"
            for page in as_list(resolution.get("resolved_pages"))
            if str(page).isdigit()
        }
    )
    visual_table_resolved_pages = sorted(
        {
            int(page)
            for row in rows
            for resolution in as_list(row.get("applied_gap_resolutions"))
            if isinstance(resolution, dict)
            and text(resolution.get("reason_code")) == "PDF_TABLE_REGION_NOT_CELL_PARSED"
            for page in as_list(resolution.get("resolved_pages"))
            if str(page).isdigit()
        }
    )
    asset["document_structure_assets"] = {
        "schema": "qualibug.enterprise-document-structure-assets.v1",
        "source_count": len(rows),
        "block_count": block_count,
        "page_count": page_count,
        "scanned_page_count": scanned_page_count,
        "ocr_resolved_page_count": len(ocr_resolved_pages),
        "ocr_resolved_pages": ocr_resolved_pages,
        "image_count": image_count,
        "table_region_count": table_region_count,
        "visual_table_count": visual_table_count,
        "formal_visual_table_count": formal_visual_table_count,
        "visual_table_cell_count": visual_table_cell_count,
        "visual_table_resolved_page_count": len(visual_table_resolved_pages),
        "visual_table_resolved_pages": visual_table_resolved_pages,
        "unresolved_visual_table_region_count": unresolved_visual_table_region_count,
        "multi_column_page_count": multi_column_page_count,
        "unsupported_content_count": unsupported_count,
        "critical_structure_gap_count": critical_structure_gap_count,
        "adapter_execution_count": adapter_execution_count,
        "adapter_names": adapter_names,
        "parsing_plan_status_distribution": plan_status_distribution,
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
    """Attach source context, reconcile facts, derive rules, then compile cognition."""
    source_rows = list(parsed_sources or [])
    if parsed_sources is not None:
        from .._chinese_business_comprehension import build_chinese_first_comprehension
        from .._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
        from .._chinese_document_context import apply_chinese_document_context
        from .._document_ir_context import apply_document_ir_context
        from .._document_ir_fact_evidence import align_business_facts_to_document_ir

        _attach_document_structure_assets(asset, source_rows)
        # Rebuild the formal Chinese coverage/fact ledgers from the best merged IR text.
        # This is what makes OCR or future supplemental adapters visible to enterprise
        # cognition instead of leaving recovered text stranded in a structure receipt.
        asset = build_chinese_first_comprehension(asset, source_rows)
        asset = align_business_facts_to_document_ir(asset, source_rows)
        asset = apply_document_ir_context(asset, source_rows)
        # The legacy text-range stage remains as a lower-fidelity supplement for
        # formats that do not yet emit rich structure blocks.
        asset = apply_chinese_document_context(asset, source_rows)
        # Either context stage may promote previously pending facts. Re-run the
        # existing conflict authority before any derived rule enters the model.
        asset = reconcile_chinese_business_fact_conflicts(asset)

    # This is the only correct rule-entailment boundary: all technical declarations
    # already exist, Chinese source spans are attached, and conflicts have been
    # reconciled, but the Enterprise Understanding Model has not yet been frozen.
    # The projection writes accepted candidates into the existing rule_library. The
    # identity reconciliation immediately merges typed upgrades back into the one
    # source-rule ID before Behavior IR, lifecycle or obligations may consume them.
    from ..implicit_rule_identity_reconciliation import (
        reconcile_implicit_rule_identities,
    )
    from ..implicit_rule_projection import enrich_asset_with_implicit_rule_projection

    asset = enrich_asset_with_implicit_rule_projection(asset)
    asset = reconcile_implicit_rule_identities(asset)

    model = build_enterprise_understanding_model(asset)
    model = apply_minimum_understanding_closure(model, asset)
    project_final_scenario_planning_gate(asset, model)
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
    fact_evidence_receipt = as_dict(asset.get("document_ir_fact_evidence_receipt"))
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
            # Operator-visible "unresolved conflicts" must exclude authority-
            # RESOLVED / SUPERSEDED / DISMISSED rows. Counting raw model.conflicts
            # left the settings receipt showing a non-zero unresolved count after
            # every SELECT_FACT decision.
            "enterprise_understanding_conflict_count": len(
                model_gate.get("unresolved_conflicts") or []
            ),
            "enterprise_understanding_projection": as_dict(model.get("metrics")).get("model_completeness_projection"),
            "enterprise_understanding_projection_contract": "INTERNAL_MODEL_CLOSURE_NOT_RECALL_OR_ACCURACY",
            "document_structure_source_count": int(structure_assets.get("source_count") or 0),
            "document_structure_block_count": int(structure_assets.get("block_count") or 0),
            "document_structure_page_count": int(structure_assets.get("page_count") or 0),
            "document_structure_scanned_page_count": int(
                structure_assets.get("scanned_page_count") or 0
            ),
            "document_structure_ocr_resolved_page_count": int(
                structure_assets.get("ocr_resolved_page_count") or 0
            ),
            "document_structure_image_count": int(structure_assets.get("image_count") or 0),
            "document_structure_table_region_count": int(
                structure_assets.get("table_region_count") or 0
            ),
            "document_structure_visual_table_count": int(
                structure_assets.get("visual_table_count") or 0
            ),
            "document_structure_formal_visual_table_count": int(
                structure_assets.get("formal_visual_table_count") or 0
            ),
            "document_structure_visual_table_cell_count": int(
                structure_assets.get("visual_table_cell_count") or 0
            ),
            "document_structure_visual_table_resolved_page_count": int(
                structure_assets.get("visual_table_resolved_page_count") or 0
            ),
            "document_structure_unresolved_visual_table_region_count": int(
                structure_assets.get("unresolved_visual_table_region_count") or 0
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
            "document_adapter_execution_count": int(
                structure_assets.get("adapter_execution_count") or 0
            ),
            "document_adapter_names": list(structure_assets.get("adapter_names") or []),
            "document_parsing_plan_status_distribution": dict(
                structure_assets.get("parsing_plan_status_distribution") or {}
            ),
            "document_ir_fact_evidence_aligned_count": int(
                fact_evidence_receipt.get("aligned_fact_count") or 0
            ),
            "document_ir_fact_evidence_unresolved_count": int(
                fact_evidence_receipt.get("unresolved_fact_count") or 0
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
            "implicit_rule_projection_runs_after_conflict_reconciliation": True,
            "implicit_rule_projection_runs_before_understanding_model": True,
            "implicit_rule_identity_reconciliation_runs_before_understanding_model": True,
            "implicit_rule_identity_reconciliation_blocks_missing_targets": True,
            "implicit_rules_enter_existing_rule_library": True,
            "implicit_rules_create_parallel_behavior_ir": False,
            "document_adapter_registry_enabled": parsed_sources is not None,
            "document_parsing_planner_enabled": parsed_sources is not None,
            "document_deferred_supplemental_planning_enabled": parsed_sources is not None,
            "document_multi_adapter_merge_enabled": parsed_sources is not None,
            "document_unknown_format_fails_visible": True,
            "document_business_understanding_is_format_agnostic": True,
            "merged_document_ir_text_reenters_chinese_fact_ledger": parsed_sources is not None,
            "document_ir_fact_evidence_alignment_enabled": parsed_sources is not None,
            "ocr_recovered_text_can_create_source_backed_facts": parsed_sources is not None,
            "visual_table_structure_adapter_enabled": parsed_sources is not None,
            "visual_table_cells_are_text_authority_when_formal": True,
            "visual_table_partial_region_success_cannot_clear_page_gap": True,
            "spreadsheet_visual_ocr_downgrade_forbidden": True,
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
    wrapped._qualibug_original_builder = original
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "enrich_asset_with_enterprise_understanding",
    "install_enterprise_understanding_model",
]
