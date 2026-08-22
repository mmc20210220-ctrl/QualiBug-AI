"""Binding Conflict Resolver — detect and resolve binding conflicts.

When multiple sources propose different runtime targets for the same
system-space node, a conflict is detected. Resolution follows a strict
evidence priority order:

1. Schema evidence (declared in Behavior IR relations)
2. API consistency (operation path/method alignment)
3. Relation evidence (cross-entity correlation)
4. Runtime Probe (empirical confirmation)

Schema: qualibug.binding-conflict-resolver.v1
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from .binding_ledger import (
    BindingLedger,
    BindingStatus,
    create_binding_edge,
    transition_binding,
)
from .binding_evidence import compute_composite_confidence


SCHEMA_VERSION = "qualibug.binding-conflict-resolver.v1"

# Resolution priority order
RESOLUTION_PRIORITY = (
    "schema_evidence",       # Behavior IR declared relations
    "api_consistency",       # Operation path/method match
    "relation_evidence",     # Cross-entity correlation
    "runtime_probe",         # Empirical runtime confirmation
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def detect_conflicts(ledger: BindingLedger) -> list[dict[str, Any]]:
    """Detect all binding conflicts in the ledger.

    A conflict exists when multiple bindings of the same type from the same
    source node have different target keys and both are in active states.

    Returns:
        List of conflict records with details.
    """
    conflicts: list[dict[str, Any]] = []

    # Group bindings by (type, source_node_id)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for binding_type in ledger._index_by_type:
        for binding in ledger.get_by_type(binding_type):
            status = binding.get("status", "")
            if status in (BindingStatus.REJECTED.value, BindingStatus.STALE.value):
                continue
            key = (binding.get("binding_type", ""), binding.get("source_node_id", ""))
            groups.setdefault(key, []).append(binding)

    for (btype, source_id), bindings in groups.items():
        if len(bindings) <= 1:
            continue

        # Multiple active bindings for same source — check if targets differ
        target_keys = {b.get("target_key", "") for b in bindings}
        if len(target_keys) <= 1:
            continue  # Same target, no conflict

        # Conflict detected
        conflict_id = f"conflict_{btype}_{source_id}_{int(time.time())}"
        conflicts.append({
            "conflict_id": conflict_id,
            "binding_type": btype,
            "source_node_id": source_id,
            "conflicting_bindings": [
                {
                    "binding_id": b.get("binding_id"),
                    "target_key": b.get("target_key"),
                    "status": b.get("status"),
                    "confidence": b.get("confidence", 0.0),
                    "evidence_count": len(b.get("evidence", [])),
                }
                for b in bindings
            ],
            "detected_at": time.time(),
            "resolution": None,
        })

    return conflicts


def resolve_conflict(
    conflict: dict[str, Any],
    ledger: BindingLedger,
    *,
    strategy: str = "evidence_priority",
) -> dict[str, Any]:
    """Resolve a single conflict using the specified strategy.

    Strategies:
    - evidence_priority: Highest composite confidence wins
    - schema_first: Schema evidence dimension score breaks tie
    - runtime_first: Runtime behavior evidence breaks tie
    - newest: Most recently updated wins

    Returns:
        Updated conflict record with resolution details.
    """
    conflicting = conflict.get("conflicting_bindings", [])
    if not conflicting:
        conflict["resolution"] = {"strategy": strategy, "winner": None, "reason": "no_bindings"}
        return conflict

    winner_id = None
    reason = ""

    if strategy == "evidence_priority":
        # Highest confidence wins
        best_score = -1.0
        for entry in conflicting:
            binding = ledger.get(entry.get("binding_id", ""))
            if not binding:
                continue
            evidence = binding.get("evidence", [])
            score = compute_composite_confidence(evidence)
            if score > best_score:
                best_score = score
                winner_id = entry.get("binding_id")
                reason = f"highest_confidence:{score:.4f}"

    elif strategy == "schema_first":
        # Schema relation evidence score breaks tie
        best_schema = -1.0
        for entry in conflicting:
            binding = ledger.get(entry.get("binding_id", ""))
            if not binding:
                continue
            schema_score = _dimension_score(binding, "schema_relation")
            if schema_score > best_schema:
                best_schema = schema_score
                winner_id = entry.get("binding_id")
                reason = f"schema_evidence:{schema_score:.4f}"

    elif strategy == "runtime_first":
        # Runtime behavior evidence breaks tie
        best_runtime = -1.0
        for entry in conflicting:
            binding = ledger.get(entry.get("binding_id", ""))
            if not binding:
                continue
            runtime_score = _dimension_score(binding, "runtime_behavior")
            if runtime_score > best_runtime:
                best_runtime = runtime_score
                winner_id = entry.get("binding_id")
                reason = f"runtime_evidence:{runtime_score:.4f}"

    elif strategy == "newest":
        latest_time = 0.0
        for entry in conflicting:
            binding = ledger.get(entry.get("binding_id", ""))
            if not binding:
                continue
            updated = float(binding.get("updated_at", 0))
            if updated > latest_time:
                latest_time = updated
                winner_id = entry.get("binding_id")
                reason = "most_recently_updated"

    # Apply resolution
    if winner_id:
        # Promote winner, reject losers
        for entry in conflicting:
            bid = entry.get("binding_id", "")
            if not bid:
                continue
            binding = ledger.get(bid)
            if not binding:
                continue
            current_status = BindingStatus(binding.get("status", "CANDIDATE"))
            if bid == winner_id:
                # Promote winner to HIGH_CONFIDENCE if not already
                if current_status in (BindingStatus.CANDIDATE, BindingStatus.CONFLICTED):
                    try:
                        ledger.promote(
                            bid, BindingStatus.HIGH_CONFIDENCE,
                            reason=f"conflict_resolved_winner:{strategy}",
                        )
                    except ValueError:
                        pass
            else:
                # Reject losers
                if current_status not in (BindingStatus.REJECTED,):
                    try:
                        ledger.promote(
                            bid, BindingStatus.REJECTED,
                            reason=f"conflict_resolved_loser:{strategy}",
                        )
                    except ValueError:
                        pass

    conflict["resolution"] = {
        "strategy": strategy,
        "winner": winner_id,
        "reason": reason,
        "resolved_at": time.time(),
    }
    return conflict


def detect_and_resolve_all(
    ledger: BindingLedger,
    *,
    strategy: str = "evidence_priority",
) -> dict[str, Any]:
    """Detect and resolve all conflicts in the ledger.

    Returns:
        Summary of conflicts found and resolutions applied.
    """
    conflicts = detect_conflicts(ledger)
    resolved: list[dict[str, Any]] = []

    for conflict in conflicts:
        resolved_conflict = resolve_conflict(conflict, ledger, strategy=strategy)
        resolved.append(resolved_conflict)

        # Mark conflicted bindings in ledger
        for entry in conflict.get("conflicting_bindings", []):
            bid = entry.get("binding_id", "")
            binding = ledger.get(bid)
            if binding and binding.get("status") != BindingStatus.CONFLICTED.value:
                try:
                    ledger.mark_conflicted(bid, conflict_info={
                        "conflict_id": conflict.get("conflict_id"),
                        "conflicting_targets": [
                            e.get("target_key") for e in conflict.get("conflicting_bindings", [])
                        ],
                    })
                except ValueError as exc:
                    logger.warning("binding transition failed bid=%s: %s", bid, exc)

    return {
        "schema_version": SCHEMA_VERSION,
        "total_conflicts": len(conflicts),
        "resolved": len(resolved),
        "resolutions": resolved,
        "timestamp": time.time(),
    }


def _dimension_score(binding: dict[str, Any], dimension: str) -> float:
    """Get the best evidence score for a specific dimension."""
    best = 0.0
    for entry in binding.get("evidence", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("dimension") == dimension:
            score = float(entry.get("score", 0.0))
            if score > best:
                best = score
    return best
