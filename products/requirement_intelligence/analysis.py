from __future__ import annotations

"""Pure Requirement Intelligence projection over the existing knowledge asset.

Conflict detection and source authority remain owned by the enterprise knowledge
layer. This module only translates already-detected, evidence-backed unresolved
conflicts into a product-facing Requirement Finding projection.
"""

from typing import Any

ANALYSIS_SCHEMA = "qualibug.requirement-intelligence.analysis.v1"

_TITLE_BY_CONFLICT_KIND = {
    "BUSINESS_MODALITY_CONTRADICTION": "业务规则约束冲突",
    "STATE_TRANSITION_TARGET_CONTRADICTION": "状态流转目标冲突",
    "TERM_ALIAS_IDENTITY_CONFLICT": "术语定义冲突",
    "OBJECT_DECLARATION_ALIAS_CONFLICT": "业务对象定义冲突",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _evidence_row(value: dict[str, Any]) -> dict[str, Any] | None:
    nested = value.get("evidence")
    nested_row = dict(nested) if isinstance(nested, dict) else {}
    source_id = _text(nested_row.get("source_id") or value.get("source_id"))
    locator = _text(
        nested_row.get("source_locator")
        or nested_row.get("locator")
        or value.get("source_locator")
        or value.get("locator")
    )
    quote = _text(
        nested_row.get("quote")
        or nested_row.get("normalized_evidence")
        or value.get("quote")
        or value.get("normalized_evidence")
        or value.get("statement")
    )
    fact_id = _text(nested_row.get("fact_id") or value.get("fact_id"))
    quote_hash = _text(nested_row.get("quote_hash") or value.get("quote_hash"))

    if not any((source_id, locator, quote, fact_id)):
        return None
    return {
        "source_id": source_id,
        "source_locator": locator,
        "quote": quote,
        "quote_hash": quote_hash,
        "fact_id": fact_id,
    }


def _conflict_evidence(conflict: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(_rows(conflict.get("evidence")))
    candidates.extend(_rows(conflict.get("facts")))
    candidates.extend(_rows(conflict.get("object_declaration_participants")))

    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        normalized = _evidence_row(candidate)
        if normalized is None:
            continue
        key = (
            _text(normalized.get("source_id")),
            _text(normalized.get("source_locator")),
            _text(normalized.get("quote_hash")),
            _text(normalized.get("fact_id")),
        )
        if key in seen:
            continue
        seen.add(key)
        evidence.append(normalized)
    return evidence


def _active_conflict(conflict: dict[str, Any]) -> bool:
    status = _text(conflict.get("status")).upper()
    if status in {"RESOLVED", "SUPERSEDED", "DISMISSED"}:
        return False
    return True


def _requirement_conflict_finding(conflict: dict[str, Any]) -> dict[str, Any] | None:
    conflict_id = _text(conflict.get("conflict_id") or conflict.get("id"))
    if not conflict_id or not _active_conflict(conflict):
        return None

    evidence = _conflict_evidence(conflict)
    if not evidence:
        # Requirement Intelligence is evidence-first: an unsupported conflict stays
        # internal and is not promoted to a customer-facing finding.
        return None

    conflict_kind = _text(
        conflict.get("kind") or conflict.get("conflict_type") or "CROSS_SOURCE_CONFLICT"
    ).upper()
    source_ids = sorted(
        {
            _text(item.get("source_id"))
            for item in evidence
            if _text(item.get("source_id"))
        }
    )
    description = _text(conflict.get("reason") or conflict.get("message"))
    if not description:
        description = "多个企业资料对同一业务事实给出了无法自动兼容的定义。"

    return {
        "finding_id": f"requirement:{conflict_id}",
        "finding_type": "requirement_conflict",
        "source_conflict_id": conflict_id,
        "conflict_kind": conflict_kind,
        "title": _TITLE_BY_CONFLICT_KIND.get(conflict_kind, "跨资料需求冲突"),
        "description": description,
        "status": "open",
        "blocking": True,
        "evidence": evidence,
        "source_ids": source_ids,
        "operator_action": _text(
            conflict.get("operator_action")
            or conflict.get("required_operator_action")
        ),
        "authority_decision": (
            dict(conflict.get("authority_decision"))
            if isinstance(conflict.get("authority_decision"), dict)
            else {}
        ),
    }


def analyze_knowledge_asset(asset: dict[str, Any]) -> dict[str, Any]:
    """Project current evidence-backed requirement conflicts from one knowledge asset."""

    conflicts = _rows(asset.get("cross_document_conflicts"))
    findings: list[dict[str, Any]] = []
    suppressed_without_evidence = 0
    resolved_conflicts = 0

    for conflict in conflicts:
        if not _active_conflict(conflict):
            resolved_conflicts += 1
            continue
        finding = _requirement_conflict_finding(conflict)
        if finding is None:
            suppressed_without_evidence += 1
            continue
        findings.append(finding)

    findings.sort(key=lambda item: _text(item.get("finding_id")))
    summary = asset.get("summary") if isinstance(asset.get("summary"), dict) else {}
    source_count = int(summary.get("active_source_count") or len(asset.get("source_inventory") or []))

    return {
        "schema": ANALYSIS_SCHEMA,
        "product_id": "requirement_intelligence",
        "analysis_status": "BLOCKED_BY_REQUIREMENT_CONFLICTS" if findings else "READY_FOR_REVIEW",
        "project_id": _text(asset.get("project_id")),
        "summary": {
            "source_count": source_count,
            "requirement_conflict_count": len(findings),
            "resolved_conflict_count": resolved_conflicts,
            "suppressed_without_evidence_count": suppressed_without_evidence,
            "blocking_finding_count": len(findings),
            "implemented_finding_types": ["requirement_conflict"],
        },
        "findings": findings,
    }
