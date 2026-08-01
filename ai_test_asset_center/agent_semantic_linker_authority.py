"""Govern existing relationship dedupe for the mature agent semantic linker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import agent_semantic_linker as _impl
from .enterprise_knowledge_center._linking import _relationship_is_authoritative

RECEIPT_SCHEMA = _impl.RECEIPT_SCHEMA
PROMPT_PROTOCOL = _impl.PROMPT_PROTOCOL
AgentSemanticLinkerError = _impl.AgentSemanticLinkerError


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (value or []) if isinstance(row, dict)]


def _is_rule_interface(row: dict[str, Any]) -> bool:
    return str(row.get("relation") or row.get("relation_type") or "").strip() == "rule_to_interface"


def enrich_knowledge_asset_with_agent_relationships(
    knowledge_asset: dict[str, Any],
    *,
    client: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Let only governed existing edges suppress a new source-backed proposal."""
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
    enriched, raw_receipt = _impl.enrich_knowledge_asset_with_agent_relationships(
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
