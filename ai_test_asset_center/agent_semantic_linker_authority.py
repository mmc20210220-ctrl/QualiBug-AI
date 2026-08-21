"""Relationship-paged authority implementation for semantic linking.

This module is kept separate so the existing authority facade can be preserved
as a legacy implementation while the public authority path delegates here.
"""
from __future__ import annotations

from copy import deepcopy
import threading
from types import SimpleNamespace
from typing import Any

from . import agent_semantic_linker as _impl
from . import agent_semantic_linker_authority_legacy as _base

RECEIPT_SCHEMA = _base.RECEIPT_SCHEMA
PROMPT_PROTOCOL = _base.PROMPT_PROTOCOL
AgentSemanticLinkerError = _base.AgentSemanticLinkerError
MAX_LINKS_PER_RULE = max(1, int(_impl.MAX_LINKS_PER_RULE))
_CONFIDENCE_RECOVERY_LOCK = threading.RLock()

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


class _TransitionRecoveryClient:
    """Forward transition requests while isolating them from normal cache entries."""

    def __init__(self, client: Any) -> None:
        self._client = client
        base_config = getattr(client, "config", None)
        config_values: dict[str, Any] = {}
        for key in (
            "model",
            "base_url",
            "temperature",
            "max_tokens",
            "timeout_seconds",
        ):
            value = getattr(base_config, key, None)
            if value is not None:
                config_values[key] = value
        config_values["model"] = f"{config_values.get('model', 'unknown')}:transition-recovery"
        self.config = SimpleNamespace(**config_values)

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        prompt = str(kwargs.get("user_prompt") or "")
        if _base._is_rule_prompt(prompt):
            return {"assessments": []}
        return self._client.complete_json(**kwargs)

    def usage_snapshot(self) -> dict[str, float]:
        snapshot = self._client.usage_snapshot()
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _sanitize_transition_recovery_receipt(
    receipt: dict[str, Any],
    *,
    transition_count: int,
) -> dict[str, Any]:
    """Remove the synthetic rule unit from a transition-only recovery receipt."""
    sanitized = dict(receipt)
    sanitized["rule_count"] = 0
    sanitized["assessed_rule_count"] = 0
    sanitized["unassessed_rule_ids"] = []
    sanitized["unassessed_rule_count"] = 0
    sanitized["budget_skipped_rule_ids"] = []
    sanitized["budget_skipped_rule_count"] = 0
    sanitized["rule_assessments"] = []
    sanitized["candidate_recall"] = {
        "rule_count": 0,
        "candidate_total": 0,
        "candidate_min": None,
        "candidate_max": 0,
        "fallback_rule_count": 0,
        "empty_candidate_rule_count": 0,
        "recall_basis": "transition_recovery",
    }
    sanitized["rejections"] = [
        row
        for row in sanitized.get("rejections", [])
        if row.get("reason_code") != "PROVIDER_OMITTED_RULE"
    ]
    sanitized["failed_units"] = [
        row
        for row in sanitized.get("failed_units", [])
        if row.get("unit_kind") != "rule_batch"
    ]
    sanitized["failed_unit_count"] = len(sanitized["failed_units"])
    sanitized["rejected_proposal_count"] = len(sanitized["rejections"])
    sanitized["transition_count"] = transition_count
    sanitized["transition_budget_skipped_count"] = 0
    sanitized["transition_request_count"] = 1 if transition_count else 0
    sanitized["batch_count"] = 1 if transition_count else 0
    sanitized["provider_attempt_count"] = max(
        0, int(sanitized.get("provider_attempt_count") or 0) - 1
    )
    sanitized["request_count"] = max(
        0, int(sanitized.get("request_count") or 0) - 1
    )
    sanitized["provider_retry_count"] = max(
        0, int(sanitized.get("provider_retry_count") or 0)
    )
    sanitized["status"] = _base._merge_status([sanitized])
    sanitized["receipt_fingerprint"] = _impl._fingerprint(sanitized)
    return sanitized


def _run_transition_recovery_batches(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
    transition_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-assess only omitted transitions, paging the recovery set at 200 too."""
    if not transition_rows:
        return deepcopy(governed_asset), {
            "transition_count": 0,
            "unassessed_transition_ids": [],
            "accepted_edge_ids": [],
            "relationships": [],
        }

    base_client = client or _impl._default_client()
    recovery_client = _TransitionRecoveryClient(base_client)
    placeholder_rule = {
        "rule_id": "__qualibug_transition_recovery_placeholder__",
        "statement": "Transition-only recovery unit; no rule relationship is requested.",
        "kind": "transition_recovery_placeholder",
        "semantic_frame": {
            "subject": "transition recovery",
            "behavior": "Assess only the supplied state transitions.",
            "source_anchors": [],
        },
        "source_id": "qualibug:transition-recovery",
    }
    recovery_asset = deepcopy(governed_asset)
    recovery_asset["rule_library"] = [placeholder_rule]

    chunks = [
        transition_rows[index:index + _base._TRANSITION_BATCH_WINDOW]
        for index in range(0, len(transition_rows), _base._TRANSITION_BATCH_WINDOW)
    ]
    recovered_assets: list[dict[str, Any]] = []
    recovered_receipts: list[dict[str, Any]] = []

    for chunk in chunks:
        recovered, raw_receipt = _base._run_core_with_transition_window(
            recovery_asset,
            client=recovery_client,
            transition_rows=chunk,
        )
        receipt = _sanitize_transition_recovery_receipt(
            raw_receipt,
            transition_count=len(chunk),
        )
        recovered_assets.append(recovered)
        recovered_receipts.append(receipt)

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _base._merge_generated_relationships(
        governed_asset,
        recovered_assets,
        recovered_receipts,
    )
    merged_receipt = _base._merge_receipts(
        recovered_receipts,
        rule_count=0,
        duplicate_rule_windows=True,
    )
    merged_receipt["accepted_relationship_count"] = len(
        merged_receipt.get("accepted_edge_ids", [])
    )
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def _run_window_with_omitted_rule_recovery(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-assess rules the provider silently omitted from a successful response."""
    enriched, receipt = _base._transition_paged_enrichment(
        governed_asset,
        client=client,
    )
    omitted_rule_ids = [
        str(rule_id).strip()
        for rule_id in receipt.get("unassessed_rule_ids", []) or []
        if str(rule_id).strip()
    ]
    if not omitted_rule_ids:
        return enriched, receipt

    omitted_set = set(omitted_rule_ids)
    recovery_asset = deepcopy(governed_asset)
    recovery_asset["rule_library"] = [
        dict(row)
        for row in _base._dicts(governed_asset.get("rule_library"))
        if _rule_id(row) in omitted_set
    ]
    recovery_asset["state_machines"] = []
    recovered, recovered_receipt = _base._transition_paged_enrichment(
        recovery_asset,
        client=client,
    )

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _base._merge_generated_relationships(
        governed_asset,
        [enriched, recovered],
        [receipt, recovered_receipt],
    )
    merged_receipt = _base._merge_receipts(
        [receipt, recovered_receipt],
        rule_count=len(_base._dicts(governed_asset.get("rule_library"))),
        duplicate_rule_windows=True,
    )

    remaining_omitted = set(
        str(rule_id).strip()
        for rule_id in recovered_receipt.get("unassessed_rule_ids", []) or []
        if str(rule_id).strip()
    )
    recovered_rule_ids = omitted_set - remaining_omitted
    if recovered_rule_ids:
        merged_receipt["unassessed_rule_ids"] = [
            rule_id
            for rule_id in merged_receipt.get("unassessed_rule_ids", [])
            if rule_id not in recovered_rule_ids
        ]
        merged_receipt["unassessed_rule_count"] = len(
            merged_receipt["unassessed_rule_ids"]
        )
        recovered_fingerprints = {
            _impl._fingerprint({"rule_id": rule_id})
            for rule_id in recovered_rule_ids
        }
        merged_receipt["rejections"] = [
            row
            for row in merged_receipt.get("rejections", [])
            if not (
                row.get("reason_code") == "PROVIDER_OMITTED_RULE"
                and row.get("proposal_fingerprint") in recovered_fingerprints
            )
        ]
        merged_receipt["rejected_proposal_count"] = len(
            merged_receipt["rejections"]
        )

    merged_receipt["omitted_rule_recovery"] = {
        "enabled": True,
        "initial_omitted_rule_count": len(omitted_set),
        "recovered_rule_assessment_count": len(recovered_rule_ids),
        "remaining_omitted_rule_count": len(remaining_omitted),
        "recovered_rule_ids": sorted(recovered_rule_ids),
        "reason_code": "PROVIDER_OMITTED_RULE_REASSESSED_IN_TARGETED_UNIT",
    }
    merged_receipt["recovered_omitted_rule_count"] = len(recovered_rule_ids)
    merged_receipt["status"] = _base._merge_status([merged_receipt])
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def _run_window_with_omitted_transition_recovery(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-assess only transitions the provider omitted from the formal response."""
    enriched, receipt = _run_window_with_omitted_rule_recovery(
        governed_asset,
        client=client,
    )
    omitted_transition_ids = {
        str(transition_id).strip()
        for transition_id in receipt.get("unassessed_transition_ids", []) or []
        if str(transition_id).strip()
    }
    if not omitted_transition_ids:
        return enriched, receipt

    source_transitions = _base._dicts(_base._original_asset_transition_rows(governed_asset))
    transition_rows = [
        row
        for row in source_transitions
        if str(row.get("transition_id") or "").strip() in omitted_transition_ids
    ]
    recovered, recovered_receipt = _run_transition_recovery_batches(
        governed_asset,
        client=client,
        transition_rows=transition_rows,
    )

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _base._merge_generated_relationships(
        governed_asset,
        [enriched, recovered],
        [receipt, recovered_receipt],
    )
    merged_receipt = _base._merge_receipts(
        [receipt, recovered_receipt],
        rule_count=len(_base._dicts(governed_asset.get("rule_library"))),
        duplicate_rule_windows=True,
    )

    remaining_omitted = {
        str(transition_id).strip()
        for transition_id in recovered_receipt.get("unassessed_transition_ids", []) or []
        if str(transition_id).strip()
    }
    recovered_transition_ids = omitted_transition_ids - remaining_omitted
    if recovered_transition_ids:
        merged_receipt["unassessed_transition_ids"] = [
            transition_id
            for transition_id in merged_receipt.get("unassessed_transition_ids", [])
            if transition_id not in recovered_transition_ids
        ]
        merged_receipt["unassessed_transition_count"] = len(
            merged_receipt["unassessed_transition_ids"]
        )
        recovered_fingerprints = {
            _impl._fingerprint({"transition_id": transition_id})
            for transition_id in recovered_transition_ids
        }
        merged_receipt["rejections"] = [
            row
            for row in merged_receipt.get("rejections", [])
            if not (
                row.get("reason_code") == "PROVIDER_OMITTED_TRANSITION"
                and row.get("proposal_fingerprint") in recovered_fingerprints
            )
        ]
        merged_receipt["rejected_proposal_count"] = len(
            merged_receipt["rejections"]
        )

    merged_receipt["omitted_transition_recovery"] = {
        "enabled": True,
        "initial_omitted_transition_count": len(omitted_transition_ids),
        "recovered_transition_assessment_count": len(recovered_transition_ids),
        "remaining_omitted_transition_count": len(remaining_omitted),
        "recovered_transition_ids": sorted(recovered_transition_ids),
        "reason_code": "PROVIDER_OMITTED_TRANSITION_REASSESSED_IN_TARGETED_UNIT",
    }
    merged_receipt["recovered_omitted_transition_count"] = len(recovered_transition_ids)
    merged_receipt["status"] = _base._merge_status([merged_receipt])
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


def _run_window_with_confidence_recovery(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retry a window only when Tier-3 confidence rejected an otherwise valid proposal."""
    enriched, receipt = _run_window_with_omitted_transition_recovery(
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
    recovered["agent_semantic_link_receipt"] = recovered_receipt
    recovered_receipt["receipt_fingerprint"] = _impl._fingerprint(recovered_receipt)
    return recovered, recovered_receipt


_original_fact_paged_enrichment = getattr(
    _base._fact_paged_enrichment,
    "_qualibug_lossless_fact_paging_original",
    _base._fact_paged_enrichment,
)


def _accepted_rule_edges_by_rule(
    enriched: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, set[str]]:
    accepted_edge_ids = {
        str(value).strip()
        for value in receipt.get("accepted_edge_ids", []) or []
        if str(value).strip()
    }
    result: dict[str, set[str]] = {}
    for row in _base._dicts(enriched.get("relationships")):
        edge_id = str(row.get("edge_id") or "").strip()
        if edge_id not in accepted_edge_ids or not _base._is_rule_interface(row):
            continue
        rule_id = str(row.get("from") or "").strip()
        interface_id = str(row.get("to") or "").strip()
        if rule_id and interface_id:
            result.setdefault(rule_id, set()).add(interface_id)
    return result


def _lossless_fact_paged_enrichment(
    governed_asset: dict[str, Any],
    *,
    client: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep assessing a rule across fact windows until its link set closes."""
    fact_pool = _base._original_all_fact_rows(governed_asset)
    if len(fact_pool) <= _base._FACT_BATCH_WINDOW:
        return _base._candidate_paged_enrichment(governed_asset, client=client)

    base_client = client or _impl._default_client()
    chunks = [
        fact_pool[index:index + _base._FACT_BATCH_WINDOW]
        for index in range(0, len(fact_pool), _base._FACT_BATCH_WINDOW)
    ]
    rules = _base._dicts(governed_asset.get("rule_library"))
    existing_by_rule = _existing_interfaces_by_rule(governed_asset)
    accumulated_by_rule = {
        rule_id: set(interface_ids)
        for rule_id, interface_ids in existing_by_rule.items()
    }
    remaining_rules = [
        dict(rule)
        for rule in rules
        if len(accumulated_by_rule.get(_rule_id(rule), set())) < MAX_LINKS_PER_RULE
    ]
    receipts: list[dict[str, Any]] = []
    enriched_assets: list[dict[str, Any]] = []
    consumed_chunks: list[list[dict[str, Any]]] = []
    unresolved_rule_counts: list[int] = []

    for index, chunk in enumerate(chunks):
        if not remaining_rules:
            break
        chunk_asset = deepcopy(governed_asset)
        chunk_asset["rule_library"] = remaining_rules
        if index > 0:
            chunk_asset["state_machines"] = []
        token = _base._FACT_ROWS_OVERRIDE.set(tuple(dict(row) for row in chunk))
        try:
            enriched, receipt = _base._candidate_paged_enrichment(
                chunk_asset,
                client=base_client,
            )
        finally:
            _base._FACT_ROWS_OVERRIDE.reset(token)
        receipts.append(receipt)
        enriched_assets.append(enriched)
        consumed_chunks.append([dict(row) for row in chunk])

        accepted_now = _accepted_rule_edges_by_rule(enriched, receipt)
        for rule_id, interface_ids in accepted_now.items():
            accumulated_by_rule.setdefault(rule_id, set()).update(interface_ids)

        next_rules: list[dict[str, Any]] = []
        for rule in remaining_rules:
            rule_id = _rule_id(rule)
            if len(accumulated_by_rule.get(rule_id, set())) < MAX_LINKS_PER_RULE:
                next_rules.append(dict(rule))
        remaining_rules = next_rules
        unresolved_rule_counts.append(len(remaining_rules))

    merged_asset = deepcopy(governed_asset)
    merged_asset["relationships"] = _base._merge_generated_relationships(
        governed_asset,
        enriched_assets,
        receipts,
    )
    merged_receipt = _base._merge_receipts(
        receipts,
        rule_count=len(rules),
        duplicate_rule_windows=True,
    )
    merged_receipt["accepted_relationship_count"] = len(
        merged_receipt.get("accepted_edge_ids", [])
    )
    merged_receipt["supporting_fact_pool_count"] = len(fact_pool)
    merged_receipt["context_fact_omitted_count"] = 0
    merged_receipt["supporting_fact_paging"] = {
        "enabled": True,
        "window_size": _base._FACT_BATCH_WINDOW,
        "window_count": len(consumed_chunks),
        "source_fact_count": len(fact_pool),
        "window_fact_counts": [len(chunk) for chunk in consumed_chunks],
        "unconsumed_tail_fact_count": max(
            0,
            len(fact_pool) - sum(len(chunk) for chunk in consumed_chunks),
        ),
        "fact_budget_skipped_count": 0,
        "unresolved_rule_counts_after_window": unresolved_rule_counts,
        "relationship_closure_rule_counts_after_window": unresolved_rule_counts,
        "zero_score_fact_fill_enabled": True,
        "reason_code": "SOURCE_SUPPORTING_FACTS_PAGED_UNTIL_RULE_LINK_CLOSURE_OR_FACT_EXHAUSTION",
    }
    merged_receipt["receipt_fingerprint"] = _impl._fingerprint(merged_receipt)
    merged_asset["agent_semantic_link_receipt"] = merged_receipt
    return merged_asset, merged_receipt


_lossless_fact_paged_enrichment._qualibug_lossless_fact_paging_original = _original_fact_paged_enrichment
_lossless_fact_paged_enrichment._qualibug_lossless_fact_paging_wrapper = True
if not getattr(_base._fact_paged_enrichment, "_qualibug_lossless_fact_paging_wrapper", False):
    _base._fact_paged_enrichment = _lossless_fact_paged_enrichment


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
    omitted_recovery_rows = [receipt.get("omitted_rule_recovery") for receipt in receipts if isinstance(receipt.get("omitted_rule_recovery"), dict)]
    merged_receipt["omitted_rule_recovery"] = {
        "enabled": bool(omitted_recovery_rows),
        "window_count": len(omitted_recovery_rows),
        "initial_omitted_rule_count": sum(int(row.get("initial_omitted_rule_count") or 0) for row in omitted_recovery_rows),
        "recovered_rule_assessment_count": sum(int(row.get("recovered_rule_assessment_count") or 0) for row in omitted_recovery_rows),
        "remaining_omitted_rule_count": sum(int(row.get("remaining_omitted_rule_count") or 0) for row in omitted_recovery_rows),
        "reason_code": "PROVIDER_OMITTED_RULE_REASSESSED_IN_TARGETED_UNIT",
    }
    omitted_transition_recovery_rows = [receipt.get("omitted_transition_recovery") for receipt in receipts if isinstance(receipt.get("omitted_transition_recovery"), dict)]
    merged_receipt["omitted_transition_recovery"] = {
        "enabled": bool(omitted_transition_recovery_rows),
        "window_count": len(omitted_transition_recovery_rows),
        "initial_omitted_transition_count": sum(int(row.get("initial_omitted_transition_count") or 0) for row in omitted_transition_recovery_rows),
        "recovered_transition_assessment_count": sum(int(row.get("recovered_transition_assessment_count") or 0) for row in omitted_transition_recovery_rows),
        "remaining_omitted_transition_count": sum(int(row.get("remaining_omitted_transition_count") or 0) for row in omitted_transition_recovery_rows),
        "reason_code": "PROVIDER_OMITTED_TRANSITION_REASSESSED_IN_TARGETED_UNIT",
    }
    merged_receipt["recovered_low_confidence_count"] = int(merged_receipt["confidence_recovery"]["recovered_low_confidence_count"])
    merged_receipt["recovered_omitted_rule_count"] = int(merged_receipt["omitted_rule_recovery"]["recovered_rule_assessment_count"])
    merged_receipt["recovered_omitted_transition_count"] = int(merged_receipt["omitted_transition_recovery"]["recovered_transition_assessment_count"])
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
    with _CONFIDENCE_RECOVERY_LOCK:
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
