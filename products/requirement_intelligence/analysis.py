from __future__ import annotations

"""Pure Requirement Intelligence projection over the existing knowledge asset.

Enterprise knowledge/understanding remains the authority for source facts, conflict
detection, business-semantic unknowns, and identity review candidates. This module
only translates those existing, source-backed authorities into product-facing
Requirement Findings and a deterministic readiness gate.
"""

from typing import Any

ANALYSIS_SCHEMA = "qualibug.requirement-intelligence.analysis.v1"
READINESS_SCHEMA = "qualibug.requirement-readiness.v1"

_TITLE_BY_CONFLICT_KIND = {
    "BUSINESS_MODALITY_CONTRADICTION": "业务规则约束冲突",
    "STATE_TRANSITION_TARGET_CONTRADICTION": "状态流转目标冲突",
    "TERM_ALIAS_IDENTITY_CONFLICT": "术语定义冲突",
    "OBJECT_DECLARATION_ALIAS_CONFLICT": "业务对象定义冲突",
}

_MISSING_REASON_CODES = frozenset(
    {
        "LIFECYCLE_FROM_STATE_UNKNOWN",
        "LIFECYCLE_TO_STATE_UNKNOWN",
        "LIFECYCLE_DISCONNECTED",
    }
)

_TITLE_BY_MISSING_REASON = {
    "LIFECYCLE_FROM_STATE_UNKNOWN": "生命周期起始状态定义缺失",
    "LIFECYCLE_TO_STATE_UNKNOWN": "生命周期目标状态定义缺失",
    "LIFECYCLE_DISCONNECTED": "生命周期衔接定义缺失",
}

_INACTIVE_STATUSES = frozenset(
    {
        "RESOLVED",
        "SUPERSEDED",
        "DISMISSED",
        "REJECTED",
        "CLOSED",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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
    asset_ref = _text(nested_row.get("asset_ref") or value.get("asset_ref"))
    document_block_id = _text(
        nested_row.get("document_block_id") or value.get("document_block_id")
    )
    document_node_id = _text(
        nested_row.get("document_node_id") or value.get("document_node_id")
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
    derivation = _text(
        nested_row.get("derivation")
        or nested_row.get("evidence_derivation")
        or value.get("derivation")
        or value.get("evidence_derivation")
    )

    if not any(
        (
            source_id,
            locator,
            asset_ref,
            document_block_id,
            document_node_id,
            quote,
            fact_id,
            quote_hash,
        )
    ):
        return None

    row = {
        "source_id": source_id,
        "source_locator": locator,
        "asset_ref": asset_ref,
        "document_block_id": document_block_id,
        "document_node_id": document_node_id,
        "quote": quote,
        "quote_hash": quote_hash,
        "fact_id": fact_id,
        "derivation": derivation,
    }
    return {key: value for key, value in row.items() if value}


def _dedupe_evidence(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        normalized = _evidence_row(candidate)
        if normalized is None:
            continue
        key = (
            _text(normalized.get("source_id")),
            _text(normalized.get("source_locator")),
            _text(normalized.get("asset_ref")),
            _text(normalized.get("document_block_id")),
            _text(normalized.get("document_node_id")),
            _text(normalized.get("quote_hash")),
            _text(normalized.get("fact_id")),
            _text(normalized.get("quote")),
        )
        if key in seen:
            continue
        seen.add(key)
        evidence.append(normalized)
    return evidence


def _is_source_backed_evidence(evidence: dict[str, Any]) -> bool:
    """Mirror the enterprise-understanding source-traceability contract.

    Kept local deliberately: importing enterprise_knowledge_center package helpers
    would execute that package's compatibility composition side effects.
    """

    source_identity = _text(evidence.get("source_id"))
    source_anchor = _text(
        evidence.get("source_locator")
        or evidence.get("asset_ref")
        or evidence.get("document_block_id")
        or evidence.get("document_node_id")
    )
    exact_content = _text(evidence.get("quote") or evidence.get("quote_hash"))
    return bool(source_identity and source_anchor and exact_content)


def _source_backed_evidence(value: Any) -> list[dict[str, Any]]:
    return [
        row
        for row in _dedupe_evidence(_rows(value))
        if _is_source_backed_evidence(row)
    ]


def _source_ids(evidence: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            _text(item.get("source_id"))
            for item in evidence
            if _text(item.get("source_id"))
        }
    )


def _conflict_evidence(conflict: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(_rows(conflict.get("evidence")))
    candidates.extend(_rows(conflict.get("facts")))
    candidates.extend(_rows(conflict.get("object_declaration_participants")))
    return _dedupe_evidence(candidates)


def _active_conflict(conflict: dict[str, Any]) -> bool:
    return _text(conflict.get("status")).upper() not in _INACTIVE_STATUSES


def _requirement_conflict_finding(
    conflict: dict[str, Any],
) -> dict[str, Any] | None:
    conflict_id = _text(conflict.get("conflict_id") or conflict.get("id"))
    if not conflict_id or not _active_conflict(conflict):
        return None

    evidence = _conflict_evidence(conflict)
    if not evidence:
        # Preserve the existing conflict projection contract: unsupported internal
        # candidates never become customer-facing Requirement Findings.
        return None

    conflict_kind = _text(
        conflict.get("kind")
        or conflict.get("conflict_type")
        or "CROSS_SOURCE_CONFLICT"
    ).upper()
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
        "readiness_requires_review": True,
        "evidence": evidence,
        "source_ids": _source_ids(evidence),
        "operator_action": _text(
            conflict.get("operator_action")
            or conflict.get("required_operator_action")
        ),
        "authority_decision": _dict(conflict.get("authority_decision")),
    }


def _enterprise_understanding_unknowns(
    asset: dict[str, Any],
) -> list[dict[str, Any]]:
    model = _dict(asset.get("enterprise_understanding_model"))
    return _rows(model.get("unknowns"))


def _active_unknown(unknown: dict[str, Any]) -> bool:
    return _text(unknown.get("resolution_status")).upper() not in _INACTIVE_STATUSES


def _requirement_missing_finding(
    unknown: dict[str, Any],
) -> dict[str, Any] | None:
    unknown_id = _text(unknown.get("unknown_id"))
    reason_code = _text(unknown.get("reason_code") or unknown.get("kind")).upper()
    if (
        not unknown_id
        or reason_code not in _MISSING_REASON_CODES
        or not _active_unknown(unknown)
    ):
        return None

    evidence = _source_backed_evidence(unknown.get("evidence"))
    if not evidence:
        # Missing requirements are only customer-facing when the upstream
        # enterprise-understanding authority can trace the gap to source material.
        return None

    blocking = bool(unknown.get("blocks_formal_understanding"))
    return {
        "finding_id": f"requirement:{unknown_id}",
        "finding_type": "requirement_missing",
        "source_unknown_id": unknown_id,
        "missing_kind": reason_code,
        "title": _TITLE_BY_MISSING_REASON.get(reason_code, "业务定义缺失"),
        "description": _text(unknown.get("question"))
        or "企业资料尚未完整定义该业务语义。",
        "status": "open",
        "severity": _text(unknown.get("severity")),
        "blocking": blocking,
        "readiness_requires_review": True,
        "blocks_formal_understanding": blocking,
        "related_object_refs": list(
            unknown.get("related_object_refs")
            if isinstance(unknown.get("related_object_refs"), list)
            else []
        ),
        "related_operation_refs": list(
            unknown.get("related_operation_refs")
            if isinstance(unknown.get("related_operation_refs"), list)
            else []
        ),
        "details": _dict(unknown.get("details")),
        "evidence": evidence,
        "source_ids": _source_ids(evidence),
        "operator_action": (
            "请补充或确认对应生命周期定义；系统不会自动推断缺失的业务事实。"
        ),
        "automatic_inference_allowed": bool(
            unknown.get("automatic_inference_allowed")
        ),
    }


def _identity_review_tasks(asset: dict[str, Any]) -> list[dict[str, Any]]:
    queue = _dict(
        asset.get("enterprise_identity_structural_review_queue")
        or _dict(asset.get("enterprise_understanding_model")).get(
            "identity_structural_review_queue"
        )
    )
    return _rows(queue.get("tasks"))


def _requirement_ambiguity_finding(
    task: dict[str, Any],
) -> dict[str, Any] | None:
    review_task_id = _text(task.get("review_task_id"))
    if (
        not review_task_id
        or _text(task.get("review_status")).upper() != "PENDING_REVIEW"
    ):
        return None

    evidence = _source_backed_evidence(task.get("evidence"))
    if not evidence:
        return None

    candidate_entity_ids = sorted(
        {
            _text(value)
            for value in (
                task.get("candidate_entity_ids")
                if isinstance(task.get("candidate_entity_ids"), list)
                else []
            )
            if _text(value)
        }
    )
    labels = {
        _text(key): _text(value)
        for key, value in _dict(task.get("canonical_labels")).items()
        if _text(key) and _text(value)
    }
    displayed_labels = sorted(
        {_text(value) for value in labels.values() if _text(value)}
    )
    subject = " / ".join(displayed_labels or candidate_entity_ids)
    description = (
        f"企业资料中的业务对象“{subject}”存在结构身份歧义，需要人工确认是否为同一对象。"
        if subject
        else "企业资料中存在结构身份歧义，需要人工确认是否为同一业务对象。"
    )

    return {
        "finding_id": f"requirement:{review_task_id}",
        "finding_type": "requirement_ambiguity",
        "source_review_task_id": review_task_id,
        "candidate_id": _text(task.get("candidate_id")),
        "title": "业务对象身份存在歧义",
        "description": description,
        "status": "open",
        # Upstream intentionally preserves current stable identities rather than
        # blocking them. Product readiness still requires explicit review.
        "blocking": False,
        "readiness_requires_review": True,
        "review_status": "PENDING_REVIEW",
        "candidate_entity_ids": candidate_entity_ids,
        "canonical_labels": labels,
        "matched_dimensions": list(
            task.get("matched_dimensions")
            if isinstance(task.get("matched_dimensions"), list)
            else []
        ),
        "evidence": evidence,
        "source_ids": _source_ids(evidence),
        "operator_action": (
            "请显式确认规范业务对象或拒绝该身份合并候选；系统不会自动合并。"
        ),
        "automatic_resolution_allowed": bool(
            task.get("automatic_resolution_allowed")
        ),
        "automatic_entity_union_allowed": bool(
            task.get("automatic_entity_union_allowed")
        ),
    }


def _readiness_projection(findings: list[dict[str, Any]]) -> dict[str, Any]:
    blocking = [item for item in findings if bool(item.get("blocking"))]
    review_required = [
        item
        for item in findings
        if bool(item.get("readiness_requires_review"))
        and not bool(item.get("blocking"))
    ]
    counts_by_type = {
        finding_type: sum(
            1
            for item in findings
            if _text(item.get("finding_type")) == finding_type
        )
        for finding_type in (
            "requirement_conflict",
            "requirement_missing",
            "requirement_ambiguity",
        )
    }
    if blocking:
        status = "NOT_READY"
    elif review_required:
        status = "REVIEW_REQUIRED"
    else:
        status = "READY"

    return {
        "schema": READINESS_SCHEMA,
        "status": status,
        "ready": status == "READY",
        "finding_count": len(findings),
        "blocking_finding_count": len(blocking),
        "blocking_finding_ids": sorted(
            _text(item.get("finding_id"))
            for item in blocking
            if _text(item.get("finding_id"))
        ),
        "review_required_finding_count": len(review_required),
        "review_required_finding_ids": sorted(
            _text(item.get("finding_id"))
            for item in review_required
            if _text(item.get("finding_id"))
        ),
        "counts_by_type": counts_by_type,
        "quality_claim": "DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL",
    }


def analyze_knowledge_asset(asset: dict[str, Any]) -> dict[str, Any]:
    """Project evidence-backed requirement findings from one knowledge asset."""

    findings: list[dict[str, Any]] = []

    conflicts = _rows(asset.get("cross_document_conflicts"))
    resolved_conflicts = 0
    suppressed_conflicts_without_evidence = 0
    for conflict in conflicts:
        if not _active_conflict(conflict):
            resolved_conflicts += 1
            continue
        finding = _requirement_conflict_finding(conflict)
        if finding is None:
            suppressed_conflicts_without_evidence += 1
            continue
        findings.append(finding)

    missing_candidates = [
        unknown
        for unknown in _enterprise_understanding_unknowns(asset)
        if _text(unknown.get("reason_code") or unknown.get("kind")).upper()
        in _MISSING_REASON_CODES
    ]
    resolved_missing = 0
    suppressed_missing_without_evidence = 0
    for unknown in missing_candidates:
        if not _active_unknown(unknown):
            resolved_missing += 1
            continue
        finding = _requirement_missing_finding(unknown)
        if finding is None:
            suppressed_missing_without_evidence += 1
            continue
        findings.append(finding)

    ambiguity_tasks = _identity_review_tasks(asset)
    inactive_ambiguities = 0
    suppressed_ambiguities_without_evidence = 0
    for task in ambiguity_tasks:
        if _text(task.get("review_status")).upper() != "PENDING_REVIEW":
            inactive_ambiguities += 1
            continue
        finding = _requirement_ambiguity_finding(task)
        if finding is None:
            suppressed_ambiguities_without_evidence += 1
            continue
        findings.append(finding)

    findings.sort(
        key=lambda item: (
            _text(item.get("finding_type")),
            _text(item.get("finding_id")),
        )
    )
    readiness = _readiness_projection(findings)
    summary = asset.get("summary") if isinstance(asset.get("summary"), dict) else {}
    source_count = int(
        summary.get("active_source_count") or len(asset.get("source_inventory") or [])
    )
    suppressed_without_evidence = (
        suppressed_conflicts_without_evidence
        + suppressed_missing_without_evidence
        + suppressed_ambiguities_without_evidence
    )

    return {
        "schema": ANALYSIS_SCHEMA,
        "product_id": "requirement_intelligence",
        "analysis_status": readiness["status"],
        "project_id": _text(asset.get("project_id")),
        "summary": {
            "source_count": source_count,
            "requirement_conflict_count": readiness["counts_by_type"][
                "requirement_conflict"
            ],
            "requirement_missing_count": readiness["counts_by_type"][
                "requirement_missing"
            ],
            "requirement_ambiguity_count": readiness["counts_by_type"][
                "requirement_ambiguity"
            ],
            "resolved_conflict_count": resolved_conflicts,
            "resolved_missing_count": resolved_missing,
            "inactive_ambiguity_count": inactive_ambiguities,
            "suppressed_without_evidence_count": suppressed_without_evidence,
            "suppressed_conflict_without_evidence_count": (
                suppressed_conflicts_without_evidence
            ),
            "suppressed_missing_without_evidence_count": (
                suppressed_missing_without_evidence
            ),
            "suppressed_ambiguity_without_evidence_count": (
                suppressed_ambiguities_without_evidence
            ),
            "blocking_finding_count": readiness["blocking_finding_count"],
            "review_required_finding_count": readiness[
                "review_required_finding_count"
            ],
            "implemented_finding_types": [
                "requirement_conflict",
                "requirement_missing",
                "requirement_ambiguity",
            ],
        },
        "readiness": readiness,
        "findings": findings,
    }
