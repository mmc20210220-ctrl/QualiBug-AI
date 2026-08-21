"""Relationship-paged authority implementation for semantic linking.

This module is kept separate so the existing authority facade can be preserved
as a legacy implementation while the public authority path delegates here.
"""
from __future__ import annotations

from copy import deepcopy
import threading
from typing import Any

from . import agent_semantic_linker as _impl
from . import agent_semantic_linker_authority_legacy as _base

RECEIPT_SCHEMA = _base.RECEIPT_SCHEMA
PROMPT_PROTOCOL = _base.PROMPT_PROTOCOL
AgentSemanticLinkerError = _base.AgentSemanticLinkerError
MAX_LINKS_PER_RULE = max(1, int(_impl.MAX_LINKS_PER_RULE))
_CONFIDENCE_RECOVERY_LOCK = threading.Lock()

if not hasattr(_base, "_text"):
    _base._text = getattr(_impl, "_text", lambda value: str(value or "").strip())


def _interface_id(row: dict[str, Any]) -> str:
    return str(row.get("interface_id") or row.get("id") or "").strip()


def _rule_id(row: dict[str, Any]) -> str:
    return str(row.get("rule_id") or row.get("id") or "").strip()


def _accepted_interfaces_by_rule(enriched: dict[str, Any], receipt: dict[str, Any]) -> dict[str, set[str]]:
    accepted_edge_ids = {str(value).strip() for value in receipt.get("accepted_edge_ids", []) or [] if str(value).strip()}
    result: dict[str, set[str]] = {}
    for row in _base._dicts(enriched.get("relationships")):
        edge_id = str(row.get("edge_id") or "").strip()
        if not edge_id or edge_id not in accepted_edge_ids or not _base._is_rule_interface(row):
            continue
        rule_id = str(row.get("from") or "").strip()
        interface_id = str(row.get("to") or "").strip()
        if rule_id and interface_id:
            result.setdefault(rule_id, set()).add(interface_id)
    return result


def _existing_interfaces_by_rule(asset: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in _base._dicts(asset.get("relationships")):
        if not _base._is_rule_interface(row) or not _base._relationship_is_authoritative(row):
            continue
        rule_id = str(row.get("from") or "").strip()
        interface_id = str(row.get("to") or "").strip()
        if rule_id and interface_id:
            result.setdefault(rule_id, set()).add(interface_id)
    return result


def _run_window_with_confidence_recovery(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retry a window only when Tier-3 confidence rejected an otherwise valid proposal."""
    enriched, receipt = _base._transition_paged_enrichment(
        governed_asset,
        client=client,
    )
    initial_low_confidence = int(receipt.get("rejected_low_confidence_count") or 0)
    if initial_low_confidence <= 0:
        return enriched, receipt

    with _CONFIDENCE_RECOVERY_LOCK:
        previous_threshold = _impl.MIN_CONFIDENCE
        _impl.MIN_CONFIDENCE = 0.0
        try:
            recovered, recovered_receipt = _base._transition_paged_enrichment(
                governed_asset,
                client=client,
            )
        finally:
            _impl.MIN_CONFIDENCE = previous_threshold

    recovered_count = max(
        0,
        initial_low_confidence
        - int(recovered_receipt.get("rejected_low_confidence_count") or 0),
    )
    recovered_receipt["confidence_recovery"] = {
        "enabled": True,
        "initial_rejected_low_confidence_count": initial_low_confidence,
        "recovered_low_confidence_count": recovered_count,
        "remaining_rejected_low_confidence_count": int(recovered_receipt.get("rejected_low_confidence_count") or 0),
        "threshold_before_recovery": previous_threshold,
        "threshold_during_recovery": 0.0,
        "reason_code": "LOW_CONFIDENCE_REASSESSED_WITH_DETERMINISTIC_CONTRACT_GATES",
    }
    recovered_receipt["recovered_low_confidence_count"] = recovered_count
    recovered_receipt["initial_rejected_low_confidence_count"] = initial_low_confidence
    recovered_receipt["receipt_fingerprint"] = _impl._fingerprint(recovered_receipt)
    recovered["agent_semantic_link_receipt"] = recovered_receipt
    return recovered, recovered_receipt


def _relationship_paged_enrichment(governed_asset: dict[str, Any], *, client: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Turn the four-link validator budget into a bounded provider window."""
    interfaces = _base._dicts(governed_asset.get("interfaces"))
    rules = _base._dicts(governed_asset.get("rule_library"))
    if not interfaces or not rules:
        return _run_window_with_confidence_recovery(governed_asset, client=client)

    original_asset = deepcopy(governed_asset)
    interface_rows = {_interface_id(row): dict(row) for row in interfaces if _interface_id(row)}
    existing_by_rule = _existing_interfaces_by_rule(original_asset)
    accumulated_by_rule: dict[str, set[str]] = {}
    active_rule_ids = {_rule_id(row) for row in rules if _rule_id(row)}
    current_asset = deepcopy(original_asset)
    receipts: list[dict[str, Any]] = []
    enriched_assets: list[dict[str, Any]] = []
    pass_count = 0
    saturated_rule_ids: set[str] = set()
    max_passes = max(1, (len(interface_rows) + MAX_LINKS_PER_RULE - 1) // MAX_LINKS_PER_RULE)

    for _ in range(max_passes):
        enriched, receipt = _run_window_with_confidence_recovery(current_asset, client=client)
        receipts.append(receipt)
        enriched_assets.append(enriched)
        pass_count += 1

        accepted_now = _accepted_interfaces_by_rule(enriched, receipt)
        for rule_id, interface_ids in accepted_now.items():
            accumulated_by_rule.setdefault(rule_id, set()).update(interface_ids)

        next_active: dict[str, set[str]] = {}
        for rule_id in active_rule_ids:
            if len(accepted_now.get(rule_id, set())) < MAX_LINKS_PER_RULE:
                continue
            saturated_rule_ids.add(rule_id)
            linked = set(existing_by_rule.get(rule_id, set()))
            linked.update(accumulated_by_rule.get(rule_id, set()))
            remaining = {interface_id for interface_id in interface_rows if interface_id not in linked}
            if remaining:
                next_active[rule_id] = remaining

        if not next_active:
            break

        union_remaining = set().union(*next_active.values())
        current_asset = deepcopy(original_asset)
        current_asset["rule_library"] = [dict(row) for row in rules if _rule_id(row) in next_active]
        current_asset["interfaces"] = [dict(interface_rows[interface_id]) for interface_id in sorted(union_remaining)]
        current_asset["state_machines"] = []
        active_rule_ids = set(next_active)

    merged_asset = deepcopy(original_asset)
    merged_asset["relationships"] = _base._merge_generated_relationships(original_asset, enriched_assets, receipts)
    merged_receipt = _base._merge_receipts(receipts, rule_count=len(rules), duplicate_rule_windows=True)
    merged_receipt["accepted_relationship_count"] = len(merged_receipt.get("accepted_edge_ids", []))
    relationship_passes = [receipt.get("relationship_paging") for receipt in receipts if isinstance(receipt.get("relationship_paging"), dict)]
    merged_receipt["relationship_paging"] = {
        "enabled": pass_count > 1 or any(bool(item.get("enabled")) for item in relationship_passes),
        "window_size": MAX_LINKS_PER_RULE,
        "pass_count": pass_count,
        "followup_call_count": max(0, pass_count - 1),
        "source_interface_count": len(interface_rows),
        "saturated_rule_count": len(saturated_rule_ids),
        "saturated_rule_ids": sorted(saturated_rule_ids),
        "reason_code": "RULE_LINKS_PAGED_INSTEAD_OF_HARD_RESPONSE_CAP",
    }
    recovery_rows = [receipt.get("confidence_recovery") for receipt in receipts if isinstance(receipt.get("confidence_recovery"), dict)]
    merged_receipt["confidence_recovery"] = {
        "enabled": bool(recovery_rows),
        "window_count": len(recovery_rows),
        "initial_rejected_low_confidence_count": sum(int(row.get("initial_rejected_low_confidence_count") or 0) for row in recovery_rows),
        "recovered_low_confidence_count": sum(int(row.get("recovered_low_confidence_count") or 0) for row in recovery_rows),
        "remaining_rejected_low_confidence_count": sum(int(row.get("remaining_rejected_low_confidence_count") or 0) for row in recovery_rows),
        "reason_code": "LOW_CONFIDENCE_REASSESSED_WITH_DETERMINISTIC_CONTRACT_GATES",
    }
    merged_receipt["recovered_low_confidence_count"] = int(merged_receipt["confidence_recovery"]["recovered_low_confidence_count"])
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


_original_candidate_paged_enrichment = getattr(_base._candidate_paged_enrichment, "_qualibug_relationship_paging_original", _base._candidate_paged_enrichment)


def _candidate_paged_enrichment(governed_asset: dict[str, Any], *, client: Any | None) -> tuple[dict[str, Any], dict[str, Any]]:
    interfaces = _base._dicts(governed_asset.get("interfaces"))
    if len(interfaces) <= _base._CANDIDATE_BATCH_WINDOW:
        return _relationship_paged_enrichment(governed_asset, client=client)

    chunks = [interfaces[index:index + _base._CANDIDATE_BATCH_WINDOW] for index in range(0, len(interfaces), _base._CANDIDATE_BATCH_WINDOW)]
    receipts: list[dict[str, Any]] = []
    enriched_assets: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_asset = deepcopy(governed_asset)
        chunk_asset["interfaces"] = chunk
        if index > 0:
            chunk_asset["state_machines"] = []
        enriched, receipt = _relationship_paged_enrichment(chunk_asset, client=client)
        enriched_assets.append(enriched)
        receipts.append(receipt)

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _base._merge_generated_relationships(governed_asset, enriched_assets, receipts)
    merged_receipt = _base._merge_receipts(receipts, rule_count=len(_base._dicts(governed_asset.get("rule_library"))), duplicate_rule_windows=True)
    merged_receipt["accepted_relationship_count"] = len(merged_receipt.get("accepted_edge_ids", []))
    relationship_passes = [receipt.get("relationship_paging") for receipt in receipts if isinstance(receipt.get("relationship_paging"), dict)]
    merged_receipt["relationship_paging"] = {
        "enabled": any(bool(item.get("enabled")) for item in relationship_passes),
        "window_size": MAX_LINKS_PER_RULE,
        "pass_count": sum(int(item.get("pass_count") or 0) for item in relationship_passes),
        "followup_call_count": sum(int(item.get("followup_call_count") or 0) for item in relationship_passes),
        "source_interface_count": len(interfaces),
        "saturated_rule_count": sum(int(item.get("saturated_rule_count") or 0) for item in relationship_passes),
        "reason_code": "RULE_LINKS_PAGED_INSTEAD_OF_HARD_RESPONSE_CAP",
    }
    merged_receipt["candidate_paging"] = {
        "enabled": True,
        "window_size": _base._CANDIDATE_BATCH_WINDOW,
        "window_count": len(chunks),
        "source_interface_count": len(interfaces),
        "window_interface_counts": [len(chunk) for chunk in chunks],
        "candidate_budget_skipped_count": 0,
        "candidate_window_fill_enabled": True,
        "reason_code": "SOURCE_INTERFACES_PAGED_INSTEAD_OF_TOP_CANDIDATE_TRUNCATION",
    }
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


_candidate_paged_enrichment._qualibug_relationship_paging_original = _original_candidate_paged_enrichment
_candidate_paged_enrichment._qualibug_relationship_paging_wrapper = True
if not getattr(_base._candidate_paged_enrichment, "_qualibug_relationship_paging_wrapper", False):
    _base._candidate_paged_enrichment = _candidate_paged_enrichment


def enrich_knowledge_asset_with_agent_relationships(knowledge_asset: dict[str, Any], *, client: Any | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    return _base.enrich_knowledge_asset_with_agent_relationships(
        knowledge_asset,
        client=client,
    )


def __getattr__(name: str) -> Any:
    try:
        return getattr(_base, name)
    except AttributeError:
        return getattr(_impl, name)


__all__ = ["RECEIPT_SCHEMA", "PROMPT_PROTOCOL", "AgentSemanticLinkerError", "enrich_knowledge_asset_with_agent_relationships"]
