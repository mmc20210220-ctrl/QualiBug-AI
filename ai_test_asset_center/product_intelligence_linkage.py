from __future__ import annotations

"""Application-layer linkage between Requirement Findings and Test Intelligence.

The product packages remain independent authorities. This module receives their
already-projected analyses and adds only deterministic cross-product references.
It never performs fuzzy matching, semantic similarity, source-name matching, or
runtime execution inference.
"""

from copy import deepcopy
from typing import Any

LINKAGE_SCHEMA = "qualibug.requirement-test-linkage.v1"
LINKAGE_QUALITY_CLAIM = (
    "DETERMINISTIC_EXACT_REQUIREMENT_TEST_LINKAGE_NOT_SEMANTIC_SIMILARITY_OR_COMPLETENESS"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _strings(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_text(item) for item in value if _text(item)}


def _fact_ids(value: Any) -> set[str]:
    return {
        _text(row.get("fact_id"))
        for row in _rows(value)
        if _text(row.get("fact_id"))
    }


def _obligation_fact_ids(obligation: dict[str, Any]) -> set[str]:
    return _strings(obligation.get("source_refs")) | _fact_ids(obligation.get("evidence"))


def _link_proof(
    finding: dict[str, Any],
    obligation: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    finding_type = _text(finding.get("finding_type"))

    shared_fact_ids = sorted(
        _fact_ids(finding.get("evidence")) & _obligation_fact_ids(obligation)
    )
    if shared_fact_ids:
        return "SHARED_SOURCE_FACT_ID", {"shared_fact_ids": shared_fact_ids}

    obligation_objects = _strings(obligation.get("object_refs"))

    if finding_type == "requirement_ambiguity":
        shared_objects = sorted(
            _strings(finding.get("candidate_entity_ids")) & obligation_objects
        )
        if shared_objects:
            return "EXACT_AMBIGUOUS_OBJECT_REF", {
                "shared_object_refs": shared_objects,
            }

    if (
        finding_type == "requirement_missing"
        and _text(obligation.get("obligation_kind")) == "lifecycle_transition"
    ):
        shared_objects = sorted(
            _strings(finding.get("related_object_refs")) & obligation_objects
        )
        operation = _text(obligation.get("operation_ref"))
        operations = _strings(finding.get("related_operation_refs"))
        if shared_objects and operation and operation in operations:
            return "EXACT_LIFECYCLE_OBJECT_OPERATION", {
                "shared_object_refs": shared_objects,
                "operation_ref": operation,
            }

    return None


def compose_requirement_test_linkage(
    requirement_analysis: dict[str, Any],
    test_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Return Test Intelligence analysis with exact Requirement Finding links.

    The input product analyses are not mutated. Unproven relationships are emitted
    as explicit unlinked receipts so callers can distinguish "not linked" from
    "not evaluated". Test Design inherits only the links already proven against
    its source Test Obligation; no additional matching is performed at design level.
    """

    if _text(requirement_analysis.get("product_id")) != "requirement_intelligence":
        raise ValueError("requirement_analysis_product_mismatch")
    if _text(test_analysis.get("product_id")) != "test_intelligence":
        raise ValueError("test_analysis_product_mismatch")

    requirement_project = _text(requirement_analysis.get("project_id"))
    test_project = _text(test_analysis.get("project_id"))
    if requirement_project and test_project and requirement_project != test_project:
        raise ValueError("requirement_test_project_mismatch")

    composed = deepcopy(test_analysis)
    obligations = _rows(composed.get("obligations"))
    designs = _rows(composed.get("test_designs"))
    findings = _rows(requirement_analysis.get("findings"))

    links: list[dict[str, Any]] = []
    links_by_obligation: dict[str, set[str]] = {}
    linked_finding_ids: set[str] = set()
    linked_obligation_ids: set[str] = set()

    for finding in findings:
        finding_id = _text(finding.get("finding_id"))
        finding_type = _text(finding.get("finding_type"))
        if not finding_id:
            continue
        for obligation in obligations:
            obligation_id = _text(obligation.get("obligation_id"))
            obligation_kind = _text(obligation.get("obligation_kind"))
            if not obligation_id:
                continue
            proof = _link_proof(finding, obligation)
            if proof is None:
                continue
            reason_code, proof_payload = proof
            links.append(
                {
                    "finding_id": finding_id,
                    "finding_type": finding_type,
                    "obligation_id": obligation_id,
                    "obligation_kind": obligation_kind,
                    "reason_code": reason_code,
                    "proof": proof_payload,
                }
            )
            links_by_obligation.setdefault(obligation_id, set()).add(finding_id)
            linked_finding_ids.add(finding_id)
            linked_obligation_ids.add(obligation_id)

    links.sort(key=lambda item: (_text(item.get("finding_id")), _text(item.get("obligation_id"))))
    finding_ids = {
        _text(item.get("finding_id"))
        for item in findings
        if _text(item.get("finding_id"))
    }
    unlinked_finding_ids = sorted(finding_ids - linked_finding_ids)
    finding_type_by_id = {
        _text(item.get("finding_id")): _text(item.get("finding_type"))
        for item in findings
        if _text(item.get("finding_id"))
    }

    for obligation in obligations:
        obligation_id = _text(obligation.get("obligation_id"))
        obligation["requirement_finding_ids"] = sorted(
            links_by_obligation.get(obligation_id, set())
        )

    linked_design_ids: set[str] = set()
    for design in designs:
        design_id = _text(design.get("design_id"))
        source_obligation_id = _text(design.get("source_obligation_id"))
        requirement_finding_ids = sorted(
            links_by_obligation.get(source_obligation_id, set())
        )
        design["requirement_finding_ids"] = requirement_finding_ids
        if design_id and requirement_finding_ids:
            linked_design_ids.add(design_id)

    composed["obligations"] = obligations
    if "test_designs" in composed:
        composed["test_designs"] = designs

    summary = dict(composed.get("summary")) if isinstance(composed.get("summary"), dict) else {}
    summary["requirement_finding_linked_obligation_count"] = len(linked_obligation_ids)
    summary["requirement_finding_linked_design_count"] = len(linked_design_ids)
    summary["linked_requirement_finding_count"] = len(linked_finding_ids)
    summary["unlinked_requirement_finding_count"] = len(unlinked_finding_ids)
    summary["requirement_finding_link_count"] = len(links)
    composed["summary"] = summary
    composed["requirement_linkage"] = {
        "schema": LINKAGE_SCHEMA,
        "quality_claim": LINKAGE_QUALITY_CLAIM,
        "requirement_finding_count": len(finding_ids),
        "linked_requirement_finding_count": len(linked_finding_ids),
        "unlinked_requirement_finding_count": len(unlinked_finding_ids),
        "linked_test_obligation_count": len(linked_obligation_ids),
        "linked_test_design_count": len(linked_design_ids),
        "link_count": len(links),
        "links": links,
        "unlinked_findings": [
            {
                "finding_id": finding_id,
                "finding_type": finding_type_by_id.get(finding_id, ""),
                "reason_code": "NO_EXACT_LINKAGE_PROOF",
            }
            for finding_id in unlinked_finding_ids
        ],
    }
    return composed