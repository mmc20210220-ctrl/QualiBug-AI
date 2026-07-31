"""Read-only ingestion and evidence measurements inside the existing evaluator.

This module does not parse documents, create a second Document IR, or treat product
receipts as human Ground Truth. It only measures the persisted
``document_structure_assets`` already produced by the product mainline and exposes
where source coverage, structure closure, or evidence addressing is incomplete.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

INGESTION_EVIDENCE_SCHEMA = (
    "qualibug.enterprise-understanding-ingestion-evidence-measurement.v1"
)

_FORMAL_TEXT_BLOCK_TYPES = {
    "HEADING",
    "PARAGRAPH",
    "LIST_ITEM",
    "TABLE_CELL",
    "KEY_VALUE",
    "NOTE",
    "CAPTION",
    "FORMULA",
}
_PASS_STATUSES = {"PASS", "COMPLETE", "READY"}
_PARTIAL_STATUSES = {"PARTIAL", "DEGRADED"}
_BLOCKED_STATUSES = {"BLOCKED", "FAILED", "ERROR", "UNSUPPORTED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _structure_assets(product_asset: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(product_asset.get("document_structure_assets"))
    if direct:
        return direct
    model = _dict(product_asset.get("enterprise_understanding_model"))
    return _dict(model.get("document_structure_assets"))


def _active_source_ids(product_asset: dict[str, Any]) -> set[str]:
    inventory = _rows(product_asset.get("source_inventory"))
    if inventory:
        return {
            _text(row.get("source_id"))
            for row in inventory
            if _text(row.get("source_id"))
            and _text(row.get("status") or "active").lower() == "active"
        }
    return {
        _text(row.get("source_id"))
        for row in _rows(product_asset.get("sources"))
        if _text(row.get("source_id"))
    }


def _formal_block_count(item: dict[str, Any], evidence: dict[str, Any]) -> int:
    declared = _integer(evidence.get("formal_authority_block_count"))
    if declared:
        return declared
    return sum(
        1
        for row in _rows(item.get("blocks"))
        if _text(row.get("type")) in _FORMAL_TEXT_BLOCK_TYPES
        and _text(row.get("text"))
        and _text(row.get("region")) in {"", "body"}
        and not row.get("excluded_from_main_flow")
        and not row.get("excluded_from_plain_text_projection")
    )


def _gap_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(item.get("unsupported_content"))


def _source_measurement(item: dict[str, Any]) -> dict[str, Any]:
    structure = _dict(item.get("structure_receipt"))
    evidence = _dict(item.get("evidence_closure_receipt"))
    ingestion = _dict(item.get("ingestion_pipeline_receipt"))
    parsing_plan = _dict(item.get("parsing_plan"))
    gaps = _gap_rows(item)

    formal = _formal_block_count(item, evidence)
    traceable = _integer(evidence.get("traceable_authority_block_count"))
    exact = _integer(evidence.get("exact_address_authority_block_count"))
    if not evidence and formal:
        traceable = sum(
            1
            for row in _rows(item.get("blocks"))
            if _text(_dict(row.get("evidence_address")).get("source_locator"))
        )
        exact = sum(
            1
            for row in _rows(item.get("blocks"))
            if _text(_dict(row.get("evidence_address")).get("address_kind"))
            in {
                "PAGE_BBOX",
                "SPREADSHEET_CELL",
                "PRESENTATION_SHAPE",
                "EXACT_SOURCE_LOCATOR",
            }
        )

    untraceable = _integer(
        evidence.get("untraceable_authority_block_count")
        if evidence
        else max(0, formal - traceable)
    )
    weak = _integer(
        evidence.get("weak_address_authority_block_count")
        if evidence
        else max(0, traceable - exact)
    )
    conflicts = _integer(evidence.get("locator_conflict_count"))
    critical_gaps = sum(
        max(1, _integer(row.get("count")))
        for row in gaps
        if bool(row.get("blocks_formal_understanding"))
    )
    unsupported_count = sum(max(1, _integer(row.get("count"))) for row in gaps)

    structure_status = _text(structure.get("status") or "UNKNOWN").upper()
    evidence_status = _text(evidence.get("status") or "UNKNOWN").upper()
    ingestion_status = _text(ingestion.get("status") or "UNKNOWN").upper()
    accepted = bool(item) and structure_status not in _BLOCKED_STATUSES
    closure_pass = evidence_status in _PASS_STATUSES
    exact_measurable = formal > 0

    silent_loss_reasons: list[str] = []
    if structure_status in _PASS_STATUSES and critical_gaps:
        silent_loss_reasons.append("STRUCTURE_PASS_WITH_CRITICAL_GAPS")
    if closure_pass and untraceable:
        silent_loss_reasons.append("EVIDENCE_PASS_WITH_UNTRACEABLE_BLOCKS")
    if closure_pass and conflicts:
        silent_loss_reasons.append("EVIDENCE_PASS_WITH_LOCATOR_CONFLICTS")
    source_hash_bound = _integer(evidence.get("source_hash_bound_block_count"))
    if formal and source_hash_bound and source_hash_bound != formal:
        silent_loss_reasons.append("FORMAL_BLOCKS_NOT_ALL_SOURCE_HASH_BOUND")
    if formal and not evidence:
        silent_loss_reasons.append("FORMAL_BLOCKS_WITHOUT_EVIDENCE_CLOSURE_RECEIPT")

    adapters = sorted(
        {
            _text(row.get("adapter_name"))
            for row in _rows(item.get("adapter_receipts"))
            if _text(row.get("adapter_name"))
        }
    )
    source_id = _text(item.get("source_id") or evidence.get("source_id"))
    filename = _text(item.get("filename") or evidence.get("filename"))
    detected_format = _text(
        item.get("format")
        or structure.get("detected_format")
        or ingestion.get("detected_format")
        or "UNKNOWN"
    ).lower()

    return {
        "source_id": source_id,
        "filename": filename,
        "detected_format": detected_format,
        "structure_status": structure_status,
        "evidence_closure_status": evidence_status,
        "ingestion_pipeline_status": ingestion_status,
        "parsing_plan_status": _text(parsing_plan.get("status") or "UNKNOWN").upper(),
        "adapter_names": adapters,
        "formal_authority_block_count": formal,
        "traceable_authority_block_count": traceable,
        "exact_address_authority_block_count": exact,
        "untraceable_authority_block_count": untraceable,
        "weak_address_authority_block_count": weak,
        "locator_conflict_count": conflicts,
        "unsupported_content_count": unsupported_count,
        "critical_structure_gap_count": critical_gaps,
        "source_traceability_rate": _ratio(traceable, formal),
        "exact_address_rate": _ratio(exact, formal),
        "ingestion_accepted": accepted,
        "evidence_closure_pass": closure_pass,
        "exact_address_measurable": exact_measurable,
        "all_formal_blocks_exactly_addressed": bool(exact_measurable and exact == formal),
        "silent_loss_risk": bool(silent_loss_reasons),
        "silent_loss_reasons": silent_loss_reasons,
        "gap_reason_codes": sorted(
            {
                _text(row.get("reason_code") or row.get("kind"))
                for row in gaps
                if _text(row.get("reason_code") or row.get("kind"))
            }
        ),
    }


def _highest_impact_gap(
    *,
    active_source_ids: set[str],
    measured_ids: set[str],
    sources: list[dict[str, Any]],
) -> str:
    if active_source_ids - measured_ids:
        return "DOCUMENT_SOURCE_STRUCTURE_MISSING"
    if not sources:
        return "DOCUMENT_STRUCTURE_ASSET_MISSING"
    if any(row["structure_status"] in _BLOCKED_STATUSES for row in sources):
        return "DOCUMENT_STRUCTURE_BLOCKED"
    if any(row["untraceable_authority_block_count"] for row in sources):
        return "DOCUMENT_EVIDENCE_CHAIN_INCOMPLETE"
    if any(row["locator_conflict_count"] for row in sources):
        return "DOCUMENT_EVIDENCE_LOCATOR_CONFLICT"
    if any(row["weak_address_authority_block_count"] for row in sources):
        return "DOCUMENT_EVIDENCE_ADDRESS_WEAK"
    if any(row["critical_structure_gap_count"] for row in sources):
        return "DOCUMENT_STRUCTURE_CRITICAL_CONTENT_UNRESOLVED"
    if any(row["unsupported_content_count"] for row in sources):
        return "DOCUMENT_STRUCTURE_UNSUPPORTED_CONTENT"
    if any(row["silent_loss_risk"] for row in sources):
        return "DOCUMENT_INGESTION_RECEIPT_INCONSISTENT"
    return "NONE"


def measure_ingestion_evidence(product_asset: dict[str, Any]) -> dict[str, Any]:
    """Measure persisted product ingestion/evidence receipts without self-certifying recall."""
    assets = _structure_assets(product_asset)
    source_rows = [_source_measurement(row) for row in _rows(assets.get("items"))]
    active_source_ids = _active_source_ids(product_asset)
    measured_ids = {row["source_id"] for row in source_rows if row["source_id"]}
    missing_source_ids = sorted(active_source_ids - measured_ids)

    formal = sum(row["formal_authority_block_count"] for row in source_rows)
    traceable = sum(row["traceable_authority_block_count"] for row in source_rows)
    exact = sum(row["exact_address_authority_block_count"] for row in source_rows)
    accepted = sum(bool(row["ingestion_accepted"]) for row in source_rows)
    complete = sum(row["structure_status"] in _PASS_STATUSES for row in source_rows)
    partial = sum(row["structure_status"] in _PARTIAL_STATUSES for row in source_rows)
    blocked = sum(row["structure_status"] in _BLOCKED_STATUSES for row in source_rows)
    closure_pass = sum(bool(row["evidence_closure_pass"]) for row in source_rows)
    exact_sources = sum(bool(row["all_formal_blocks_exactly_addressed"]) for row in source_rows)
    silent_risk_sources = [row for row in source_rows if row["silent_loss_risk"]]

    expected_source_count = len(active_source_ids) or _integer(assets.get("source_count"))
    denominator = expected_source_count or len(source_rows)
    highest_gap = _highest_impact_gap(
        active_source_ids=active_source_ids,
        measured_ids=measured_ids,
        sources=source_rows,
    )

    gap_counts: Counter[str] = Counter()
    affected_sources: defaultdict[str, set[str]] = defaultdict(set)
    for row in source_rows:
        for reason in row["gap_reason_codes"]:
            gap_counts[reason] += 1
            affected_sources[reason].add(row["source_id"] or row["filename"])
    structure_loss = {
        "highest_impact_gap": highest_gap,
        "missing_document_structure_source_ids": missing_source_ids,
        "critical_gap_source_count": sum(
            bool(row["critical_structure_gap_count"]) for row in source_rows
        ),
        "unsupported_content_source_count": sum(
            bool(row["unsupported_content_count"]) for row in source_rows
        ),
        "silent_loss_risk_source_count": len(silent_risk_sources),
        "silent_loss_risk_sources": [
            {
                "source_id": row["source_id"],
                "filename": row["filename"],
                "reasons": row["silent_loss_reasons"],
            }
            for row in silent_risk_sources
        ],
        "gap_distribution": [
            {
                "reason_code": reason,
                "occurrence_source_count": gap_counts[reason],
                "affected_source_ids": sorted(value for value in affected_sources[reason] if value),
            }
            for reason in sorted(gap_counts)
        ],
    }

    format_groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        format_groups[row["detected_format"] or "unknown"].append(row)
    format_analysis = {
        "format_count": len(format_groups),
        "formats": [
            {
                "format": name,
                "source_count": len(rows),
                "accepted_source_count": sum(bool(row["ingestion_accepted"]) for row in rows),
                "blocked_source_count": sum(
                    row["structure_status"] in _BLOCKED_STATUSES for row in rows
                ),
                "formal_authority_block_count": sum(
                    row["formal_authority_block_count"] for row in rows
                ),
                "source_traceability_rate": _ratio(
                    sum(row["traceable_authority_block_count"] for row in rows),
                    sum(row["formal_authority_block_count"] for row in rows),
                ),
                "exact_address_rate": _ratio(
                    sum(row["exact_address_authority_block_count"] for row in rows),
                    sum(row["formal_authority_block_count"] for row in rows),
                ),
                "adapter_names": sorted(
                    {
                        adapter
                        for row in rows
                        for adapter in row["adapter_names"]
                        if adapter
                    }
                ),
            }
            for name, rows in sorted(format_groups.items())
        ],
        "cross_industry_or_universal_format_coverage_proven": False,
        "coverage_claim_requires_declared_human_corpus": True,
    }

    evidence_analysis = {
        "formal_authority_block_count": formal,
        "traceable_authority_block_count": traceable,
        "exact_address_authority_block_count": exact,
        "untraceable_authority_block_count": sum(
            row["untraceable_authority_block_count"] for row in source_rows
        ),
        "weak_address_authority_block_count": sum(
            row["weak_address_authority_block_count"] for row in source_rows
        ),
        "locator_conflict_count": sum(row["locator_conflict_count"] for row in source_rows),
        "source_traceability_rate": _ratio(traceable, formal),
        "exact_address_rate": _ratio(exact, formal),
        "evidence_closure_pass_source_count": closure_pass,
        "evidence_closure_pass_rate": _ratio(closure_pass, len(source_rows)),
        "all_formal_blocks_exactly_addressed_source_count": exact_sources,
        "all_formal_blocks_exactly_addressed_source_rate": _ratio(
            exact_sources, len(source_rows)
        ),
        "sources": source_rows,
    }

    receipt_integrity_pass = bool(
        source_rows
        and not missing_source_ids
        and accepted == len(source_rows)
        and closure_pass == len(source_rows)
        and exact == formal
        and not structure_loss["critical_gap_source_count"]
        and not structure_loss["silent_loss_risk_source_count"]
    )
    summary = {
        "declared_active_source_count": len(active_source_ids),
        "document_structure_source_count": len(source_rows),
        "source_structure_coverage_rate": _ratio(len(measured_ids & active_source_ids), len(active_source_ids))
        if active_source_ids
        else _ratio(len(source_rows), denominator),
        "ingestion_accepted_source_count": accepted,
        "ingestion_acceptance_rate": _ratio(accepted, denominator),
        "structure_complete_source_count": complete,
        "structure_partial_source_count": partial,
        "structure_blocked_source_count": blocked,
        "structure_complete_rate": _ratio(complete, denominator),
        "source_traceability_rate": _ratio(traceable, formal),
        "exact_address_rate": _ratio(exact, formal),
        "critical_structure_gap_count": sum(
            row["critical_structure_gap_count"] for row in source_rows
        ),
        "unsupported_content_count": sum(row["unsupported_content_count"] for row in source_rows),
        "highest_impact_gap": highest_gap,
        "receipt_integrity_gate_pass": receipt_integrity_pass,
        "five_of_five_readiness_status": (
            "RECEIPT_INTEGRITY_PASS_GROUND_TRUTH_RECALL_NOT_MEASURED"
            if receipt_integrity_pass
            else "BLOCKED_BY_INGESTION_OR_EVIDENCE_GAPS"
        ),
        "human_structure_ground_truth_required_for_recall": True,
        "structure_block_recall_measured": False,
        "reading_order_accuracy_measured": False,
        "table_reconstruction_recall_measured": False,
        "visual_content_recall_measured": False,
        "product_receipts_are_not_ground_truth": True,
    }

    return {
        "schema": INGESTION_EVIDENCE_SCHEMA,
        "status": "PASS" if receipt_integrity_pass else "PARTIAL",
        "summary": summary,
        "evidence_address_analysis": evidence_analysis,
        "structure_loss_analysis": structure_loss,
        "format_coverage_analysis": format_analysis,
        "measurement_authority": "EVALUATOR_READ_ONLY_PRODUCT_RECEIPTS",
        "ground_truth_recall_authority": "HUMAN_ANNOTATED_CORPUS_REQUIRED",
        "model_writeback_allowed": False,
        "product_asset_rewritten": False,
    }


__all__ = ["INGESTION_EVIDENCE_SCHEMA", "measure_ingestion_evidence"]
