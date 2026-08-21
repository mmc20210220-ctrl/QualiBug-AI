"""Govern existing relationship dedupe for the mature agent semantic linker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import agent_semantic_linker as _impl
from .enterprise_knowledge_center._linking import _relationship_is_authoritative

RECEIPT_SCHEMA = _impl.RECEIPT_SCHEMA
PROMPT_PROTOCOL = _impl.PROMPT_PROTOCOL
AgentSemanticLinkerError = _impl.AgentSemanticLinkerError

# The mature linker has an explicit provider window of
# MAX_RULES_PER_REQUEST * MAX_PROVIDER_REQUESTS rules.  That was originally
# treated as a global scan ceiling, which meant rule 321+ was only receipted as
# budget-skipped and was never sent to the semantic linker.  Keep the provider
# window as the bounded unit size, but schedule every source rule through
# lossless windows at this authority boundary.
_RULE_BATCH_WINDOW = max(
    1,
    int(_impl.MAX_RULES_PER_REQUEST) * int(_impl.MAX_PROVIDER_REQUESTS),
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (value or []) if isinstance(row, dict)]


def _is_rule_interface(row: dict[str, Any]) -> bool:
    return str(row.get("relation") or row.get("relation_type") or "").strip() == "rule_to_interface"


def _merge_status(receipts: list[dict[str, Any]]) -> str:
    rejections = any(receipt.get("rejections") for receipt in receipts)
    failed = any(receipt.get("failed_units") for receipt in receipts)
    gaps = any(
        receipt.get("unassessed_rule_count")
        or receipt.get("budget_skipped_rule_count")
        or receipt.get("unassessed_transition_count")
        or receipt.get("failed_units")
        or any(
            row.get("disposition") != "LINKED"
            or row.get("accepted_relationship_count") == 0
            for row in [
                *receipt.get("rule_assessments", []),
                *receipt.get("transition_assessments", []),
            ]
            if isinstance(row, dict)
        )
        for receipt in receipts
    )
    if rejections:
        return "VERIFIED_WITH_REJECTIONS"
    if failed:
        return "VERIFIED_WITH_FAILED_UNITS"
    if gaps:
        return "VERIFIED_WITH_GAPS"
    return "VERIFIED"


def _merge_candidate_recall(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        receipt.get("candidate_recall")
        for receipt in receipts
        if isinstance(receipt.get("candidate_recall"), dict)
    ]
    bases = {
        str(row.get("recall_basis") or "")
        for row in rows
        if str(row.get("recall_basis") or "")
    }
    mins = [row.get("candidate_min") for row in rows if row.get("candidate_min") is not None]
    return {
        "rule_count": sum(int(row.get("rule_count") or 0) for row in rows),
        "candidate_total": sum(int(row.get("candidate_total") or 0) for row in rows),
        "candidate_min": min(mins) if mins else None,
        "candidate_max": max(
            [int(row.get("candidate_max") or 0) for row in rows],
            default=0,
        ),
        "fallback_rule_count": sum(int(row.get("fallback_rule_count") or 0) for row in rows),
        "empty_candidate_rule_count": sum(
            int(row.get("empty_candidate_rule_count") or 0) for row in rows
        ),
        "recall_basis": next(iter(bases)) if len(bases) == 1 else "mixed",
    }


def _merge_receipts(
    receipts: list[dict[str, Any]],
    *,
    rule_count: int,
) -> dict[str, Any]:
    first = receipts[0]
    merged = dict(first)
    merged["rule_count"] = rule_count
    merged["assessed_rule_count"] = sum(
        int(receipt.get("assessed_rule_count") or 0) for receipt in receipts
    )
    merged["unassessed_rule_count"] = sum(
        int(receipt.get("unassessed_rule_count") or 0) for receipt in receipts
    )
    merged["unassessed_rule_ids"] = [
        rule_id
        for receipt in receipts
        for rule_id in receipt.get("unassessed_rule_ids", [])
    ]
    merged["budget_skipped_rule_count"] = sum(
        int(receipt.get("budget_skipped_rule_count") or 0) for receipt in receipts
    )
    merged["budget_skipped_rule_ids"] = [
        rule_id
        for receipt in receipts
        for rule_id in receipt.get("budget_skipped_rule_ids", [])
    ]
    merged["batch_count"] = sum(int(receipt.get("batch_count") or 0) for receipt in receipts)
    merged["request_count"] = sum(int(receipt.get("request_count") or 0) for receipt in receipts)
    merged["transition_request_count"] = sum(
        int(receipt.get("transition_request_count") or 0) for receipt in receipts
    )
    merged["context_fact_count"] = max(
        [int(receipt.get("context_fact_count") or 0) for receipt in receipts],
        default=0,
    )
    merged["context_fact_omitted_count"] = sum(
        int(receipt.get("context_fact_omitted_count") or 0) for receipt in receipts
    )
    merged["supporting_fact_pool_count"] = max(
        [int(receipt.get("supporting_fact_pool_count") or 0) for receipt in receipts],
        default=0,
    )
    merged["candidate_recall"] = _merge_candidate_recall(receipts)

    merged_cache = {
        "hit_count": sum(
            int((receipt.get("cache") or {}).get("hit_count") or 0)
            for receipt in receipts
        ),
        "miss_count": sum(
            int((receipt.get("cache") or {}).get("miss_count") or 0)
            for receipt in receipts
        ),
        "transition_cache_hit": any(
            bool((receipt.get("cache") or {}).get("transition_cache_hit"))
            for receipt in receipts
        ),
        "persistence_failures": sum(
            int((receipt.get("cache") or {}).get("persistence_failures") or 0)
            for receipt in receipts
        ),
        "cache_key_components": list(
            (first.get("cache") or {}).get("cache_key_components") or []
        ),
    }
    merged["cache"] = merged_cache
    merged["failed_unit_count"] = sum(
        int(receipt.get("failed_unit_count") or 0) for receipt in receipts
    )
    merged["failed_units"] = [
        unit
        for receipt in receipts
        for unit in receipt.get("failed_units", [])
    ]
    for key in (
        "proposal_count",
        "accepted_relationship_count",
        "rejected_proposal_count",
        "rejected_low_confidence_count",
        "rejected_invalid_identity_count",
        "rejected_non_candidate_count",
        "rejected_invalid_evidence_count",
        "rejected_duplicate_count",
        "rejected_rule_limit_count",
        "rejected_inconsistent_disposition_count",
        "existing_relationship_count",
        "no_executable_interface_count",
        "ambiguous_rule_count",
        "provider_attempt_count",
        "provider_retry_count",
    ):
        merged[key] = sum(int(receipt.get(key) or 0) for receipt in receipts)
    merged["transition_count"] = int(first.get("transition_count") or 0)
    merged["transition_budget_skipped_count"] = int(
        first.get("transition_budget_skipped_count") or 0
    )
    merged["assessed_transition_count"] = int(first.get("assessed_transition_count") or 0)
    merged["unassessed_transition_count"] = int(first.get("unassessed_transition_count") or 0)
    merged["unassessed_transition_ids"] = list(first.get("unassessed_transition_ids") or [])
    merged["no_executable_transition_count"] = int(
        first.get("no_executable_transition_count") or 0
    )
    merged["ambiguous_transition_count"] = int(first.get("ambiguous_transition_count") or 0)
    merged["transition_assessments"] = list(first.get("transition_assessments") or [])
    merged["rule_assessments"] = [
        row
        for receipt in receipts
        for row in receipt.get("rule_assessments", [])
    ]
    merged["rejections"] = [
        row
        for receipt in receipts
        for row in receipt.get("rejections", [])
    ]
    merged["accepted_edge_ids"] = list(
        dict.fromkeys(
            edge_id
            for receipt in receipts
            for edge_id in receipt.get("accepted_edge_ids", [])
            if edge_id
        )
    )
    usage: dict[str, float] = {}
    for receipt in receipts:
        raw_usage = receipt.get("usage")
        if not isinstance(raw_usage, dict):
            continue
        for key, value in raw_usage.items():
            try:
                usage[key] = usage.get(key, 0.0) + float(value)
            except (TypeError, ValueError):
                continue
    merged["usage"] = usage
    merged["status"] = _merge_status(receipts)
    merged["receipt_fingerprint"] = _impl._fingerprint(merged)
    return merged


def _lossless_rule_enrichment(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the mature linker in bounded windows without dropping later rules.

    The underlying linker remains unchanged and keeps its 320-rule provider
    window. This authority layer turns that window into a paging unit instead
    of a global ceiling. The first window owns the state-transition request;
    later windows are rule-only so transition evidence is not duplicated.
    """
    rules = _dicts(governed_asset.get("rule_library"))
    if len(rules) <= _RULE_BATCH_WINDOW:
        return _impl.enrich_knowledge_asset_with_agent_relationships(
            governed_asset,
            client=client,
        )

    chunks = [
        rules[index:index + _RULE_BATCH_WINDOW]
        for index in range(0, len(rules), _RULE_BATCH_WINDOW)
    ]
    chunk_receipts: list[dict[str, Any]] = []
    generated_relationships: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks):
        chunk_asset = deepcopy(governed_asset)
        chunk_asset["rule_library"] = chunk
        if index > 0:
            chunk_asset["state_machines"] = []
        enriched, receipt = _impl.enrich_knowledge_asset_with_agent_relationships(
            chunk_asset,
            client=client,
        )
        chunk_receipts.append(receipt)
        accepted_edge_ids = {
            str(value).strip()
            for value in receipt.get("accepted_edge_ids") or []
            if str(value).strip()
        }
        generated_relationships.extend(
            dict(row)
            for row in _dicts(enriched.get("relationships"))
            if str(row.get("edge_id") or "").strip() in accepted_edge_ids
        )

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = [
        *[dict(row) for row in _dicts(governed_asset.get("relationships"))],
        *generated_relationships,
    ]
    merged_receipt = _merge_receipts(
        chunk_receipts,
        rule_count=len(rules),
    )
    merged_receipt["lossless_rule_scheduling"] = {
        "enabled": True,
        "window_size": _RULE_BATCH_WINDOW,
        "window_count": len(chunks),
        "budget_skipped_rule_count": merged_receipt["budget_skipped_rule_count"],
        "reason_code": "SOURCE_RULES_PAGED_INSTEAD_OF_GLOBALLY_TRUNCATED",
    }
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def enrich_knowledge_asset_with_agent_relationships(
    knowledge_asset: dict[str, Any],
    *,
    client: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep existing-edge governance and make source-rule scheduling lossless."""
    if not isinstance(knowledge_asset, dict):
        raise AgentSemanticLinkerError("knowledge_asset_not_object")

    original_relationships = _dicts(knowledge_asset.get("relationships"))
    governed_existing: list[dict[str, Any]] = []
    ungoverned_existing: list[dict[str, Any]] = []
    for row in original_relationships:
        if not _is_rule_interface(row) or _relationship_is_authoritative(row):
            governed_existing.append(dict(row))
        else:
            ungoverned_existing.append(dict(row))

    governed_asset = deepcopy(knowledge_asset)
    governed_asset["relationships"] = governed_existing
    enriched, raw_receipt = _lossless_rule_enrichment(
        governed_asset,
        client=client,
    )
    receipt = dict(raw_receipt)
    accepted_edge_ids = {
        str(value).strip()
        for value in receipt.get("accepted_edge_ids") or []
        if str(value).strip()
    }
    generated = [
        dict(row)
        for row in _dicts(enriched.get("relationships"))
        if str(row.get("edge_id") or "").strip() in accepted_edge_ids
    ]
    preserved = [
        dict(row)
        for row in original_relationships
        if not (
            str(row.get("edge_id") or "").strip()
            and str(row.get("edge_id") or "").strip() in accepted_edge_ids
        )
    ]
    enriched["relationships"] = [*preserved, *generated]
    receipt.update(
        {
            "ungoverned_existing_relationship_count": len(ungoverned_existing),
            "ungoverned_existing_relationships_suppressed_from_dedupe": True,
            "existing_relationship_authority_reused": True,
            "parallel_semantic_linker_created": False,
        }
    )
    receipt["receipt_fingerprint"] = _impl._fingerprint(receipt)
    enriched["agent_semantic_link_receipt"] = receipt
    return enriched, receipt


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


__all__ = [
    "RECEIPT_SCHEMA",
    "PROMPT_PROTOCOL",
    "AgentSemanticLinkerError",
    "enrich_knowledge_asset_with_agent_relationships",
]
