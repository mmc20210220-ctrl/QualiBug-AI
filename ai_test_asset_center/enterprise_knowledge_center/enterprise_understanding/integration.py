"""Install enterprise understanding as a first-class knowledge-center stage."""
from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from .builder import build_enterprise_understanding_model
from .closure import apply_minimum_understanding_closure
from .schema import as_dict, as_list, text


def _parsed_sources_for_context(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Re-read registered sources so document hierarchy keeps original character ranges."""
    from .._crud import _record_parse

    parsed_sources: list[dict[str, Any]] = []
    for source in as_list(asset.get("source_inventory")):
        if not isinstance(source, dict) or text(source.get("status")) != "active":
            continue
        parsed = _record_parse(source, root)
        parser_receipt = as_dict(parsed.get("parser_receipt"))
        parsed_sources.append(
            {
                "source_id": source.get("source_id"),
                "filename": source.get("original_name"),
                "source_locator": parser_receipt.get("source_locator"),
                "text": parsed.get("text") or "",
            }
        )
    return parsed_sources


def enrich_asset_with_enterprise_understanding(
    asset: dict[str, Any],
    *,
    parsed_sources: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach document context, refresh fact conflicts, then compile cognition model."""
    if parsed_sources is not None:
        from .._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
        from .._chinese_document_context import apply_chinese_document_context

        asset = apply_chinese_document_context(asset, parsed_sources)
        # Context resolution may promote previously pending facts. Re-run the existing
        # conflict authority before any newly promoted fact enters the understanding model.
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
