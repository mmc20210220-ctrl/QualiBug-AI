from __future__ import annotations

"""Deterministic coverage and structured-design projection for Test Intelligence v1."""

from typing import Any

from .designs import project_test_designs
from .obligations import project_test_obligations

ANALYSIS_SCHEMA = "qualibug.test-intelligence.analysis.v1"
COVERAGE_SCHEMA = "qualibug.test-intelligence.coverage.v1"
COVERAGE_QUALITY_CLAIM = (
    "DETERMINISTIC_SUPPORTED_SEMANTIC_OBLIGATION_COVERAGE_NOT_TOTAL_TEST_COMPLETENESS"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def build_coverage_projection(projection: dict[str, Any]) -> dict[str, Any]:
    obligations = [
        dict(item)
        for item in projection.get("obligations", [])
        if isinstance(item, dict)
    ]
    eligible_ids = sorted(
        {
            _text(item)
            for item in projection.get("eligible_source_unit_ids", [])
            if _text(item)
        }
    )
    obligated_ids = sorted(
        {
            _text(item.get("source_unit_id"))
            for item in obligations
            if _text(item.get("source_unit_id"))
        }
    )
    uncovered_ids = sorted(set(eligible_ids) - set(obligated_ids))
    if not eligible_ids:
        status = "NOT_MEASURED"
    elif uncovered_ids:
        status = "PARTIAL"
    else:
        status = "COVERED"

    counts_by_kind = {
        kind: sum(
            1
            for item in obligations
            if _text(item.get("obligation_kind")) == kind
        )
        for kind in (
            "business_rule",
            "lifecycle_transition",
            "authorization",
            "side_effect",
            "requirement_risk",
        )
    }
    return {
        "schema": COVERAGE_SCHEMA,
        "status": status,
        "eligible_supported_semantic_unit_count": len(eligible_ids),
        "obligated_supported_semantic_unit_count": len(obligated_ids),
        "uncovered_supported_semantic_unit_count": len(uncovered_ids),
        "uncovered_supported_semantic_unit_ids": uncovered_ids,
        "counts_by_obligation_kind": counts_by_kind,
        "execution_coverage_status": "NOT_MEASURED",
        "quality_claim": COVERAGE_QUALITY_CLAIM,
    }


def analyze_test_intelligence(asset: dict[str, Any]) -> dict[str, Any]:
    projection = project_test_obligations(asset)
    coverage = build_coverage_projection(projection)
    summary = _dict(asset.get("summary"))
    source_inventory = asset.get("source_inventory")
    source_count = int(
        summary.get("active_source_count")
        or (len(source_inventory) if isinstance(source_inventory, list) else 0)
    )
    obligations = [
        dict(item)
        for item in projection.get("obligations", [])
        if isinstance(item, dict)
    ]
    design_projection = project_test_designs(obligations)
    designs = [
        dict(item)
        for item in design_projection.get("designs", [])
        if isinstance(item, dict)
    ]
    linked_to_requirement_findings = sum(
        1 for item in obligations if item.get("requirement_finding_ids")
    )
    return {
        "schema": ANALYSIS_SCHEMA,
        "product_id": "test_intelligence",
        "project_id": _text(asset.get("project_id")),
        "analysis_status": coverage["status"],
        "summary": {
            "source_count": source_count,
            "obligation_count": len(obligations),
            "eligible_supported_semantic_unit_count": coverage[
                "eligible_supported_semantic_unit_count"
            ],
            "uncovered_supported_semantic_unit_count": coverage[
                "uncovered_supported_semantic_unit_count"
            ],
            "suppressed_without_evidence_count": projection[
                "suppressed_without_evidence_count"
            ],
            "unsupported_formal_behavior_count": projection[
                "unsupported_formal_behavior_count"
            ],
            "requirement_finding_linked_obligation_count": linked_to_requirement_findings,
            "implemented_obligation_kinds": projection[
                "implemented_obligation_kinds"
            ],
            "test_design_count": len(designs),
            "undesigned_obligation_count": design_projection[
                "undesigned_obligation_count"
            ],
            "test_design_status": design_projection["status"],
        },
        "coverage": coverage,
        "obligations": obligations,
        "test_design_projection": {
            key: value
            for key, value in design_projection.items()
            if key != "designs"
        },
        "test_designs": designs,
    }