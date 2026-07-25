"""Unified Binding Ledger — single source of truth for all binding edges.

A binding edge connects a system-space node (Entity, Operation, Field, Relation,
State, Actor, Scope, Fixture, Observer, OracleInput) to a concrete runtime object.

Schema: qualibug.binding-ledger.v1

Design principles:
- Binding is an EDGE, not a new node type
- Extends existing Behavior IR, never replaces it
- Industry-neutral: no project-specific names or values
- All bindings are versioned, evidence-backed, and auditable
"""
from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Any


SCHEMA_VERSION = "qualibug.binding-ledger.v1"

# ─── Binding Types (10 dimensions) ────────────────────────────────────────────

BINDING_TYPES = frozenset({
    "entity",           # Entity → runtime collection/ID path
    "operation",        # Operation → endpoint/method/actor_requirements
    "field",            # Field → request/response field with type classification
    "relation",         # Relation → FK/correlation_key/materialization_operation
    "state",            # State → state_field/raw_values/transition_operations
    "actor",            # Actor → account/credential/role
    "scope",            # Scope → tenant/organization/resource_scope
    "fixture",          # Fixture → create_operation/body_template/cleanup
    "observer",         # Observer → read_operation/fields/scope_keys
    "oracle_input",     # OracleInput → explicit field bindings for oracle evaluation
})


# ─── Binding State Machine ────────────────────────────────────────────────────

class BindingStatus(str, Enum):
    """Formal binding lifecycle states."""
    CANDIDATE = "CANDIDATE"                    # Discovered, not yet validated
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"        # Evidence score ≥ 0.90
    RUNTIME_CONFIRMED = "RUNTIME_CONFIRMED"    # Probe confirmed at runtime
    EXECUTABLE = "EXECUTABLE"                  # Ready for experiment consumption
    CONFLICTED = "CONFLICTED"                  # Multiple sources disagree
    REJECTED = "REJECTED"                      # Evidence disproved or probe failed
    STALE = "STALE"                            # Was valid, source changed

    @classmethod
    def terminal_states(cls) -> frozenset["BindingStatus"]:
        return frozenset({cls.EXECUTABLE, cls.REJECTED})

    @classmethod
    def active_states(cls) -> frozenset["BindingStatus"]:
        return frozenset({cls.CANDIDATE, cls.HIGH_CONFIDENCE, cls.RUNTIME_CONFIRMED})


# Valid state transitions
_VALID_TRANSITIONS: dict[BindingStatus, frozenset[BindingStatus]] = {
    BindingStatus.CANDIDATE: frozenset({
        BindingStatus.HIGH_CONFIDENCE,
        BindingStatus.CONFLICTED,
        BindingStatus.REJECTED,
        BindingStatus.STALE,
    }),
    BindingStatus.HIGH_CONFIDENCE: frozenset({
        BindingStatus.RUNTIME_CONFIRMED,
        BindingStatus.EXECUTABLE,
        BindingStatus.CONFLICTED,
        BindingStatus.REJECTED,
        BindingStatus.STALE,
    }),
    BindingStatus.RUNTIME_CONFIRMED: frozenset({
        BindingStatus.EXECUTABLE,
        BindingStatus.CONFLICTED,
        BindingStatus.STALE,
    }),
    BindingStatus.EXECUTABLE: frozenset({
        BindingStatus.STALE,
        BindingStatus.CONFLICTED,
    }),
    BindingStatus.CONFLICTED: frozenset({
        BindingStatus.HIGH_CONFIDENCE,
        BindingStatus.REJECTED,
        BindingStatus.CANDIDATE,
    }),
    BindingStatus.REJECTED: frozenset({
        BindingStatus.CANDIDATE,  # Can be re-proposed with new evidence
    }),
    BindingStatus.STALE: frozenset({
        BindingStatus.CANDIDATE,  # Can be re-validated
    }),
}


def can_transition(from_status: BindingStatus, to_status: BindingStatus) -> bool:
    """Check if a state transition is valid."""
    return to_status in _VALID_TRANSITIONS.get(from_status, frozenset())


# ─── Confidence Gate ──────────────────────────────────────────────────────────

CONFIDENCE_HIGH = 0.90       # ≥ 0.90: high confidence, can proceed
CONFIDENCE_PROBE = 0.70      # 0.70-0.90: needs runtime probe
CONFIDENCE_UNUSABLE = 0.70   # < 0.70: not usable for execution


def confidence_gate(score: float) -> str:
    """Classify confidence score into action category."""
    if score >= CONFIDENCE_HIGH:
        return "high_confidence"
    if score >= CONFIDENCE_PROBE:
        return "needs_probe"
    return "unusable"


# ─── Binding Edge ─────────────────────────────────────────────────────────────

def _stable_binding_id(
    binding_type: str,
    source_node_id: str,
    target_key: str,
) -> str:
    """Generate a stable, content-addressed binding ID."""
    raw = f"{binding_type}|{source_node_id}|{target_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"bind_{digest}"


def create_binding_edge(
    *,
    binding_type: str,
    source_node_id: str,
    target_key: str,
    source_module: str = "",
    evidence: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new binding edge in CANDIDATE state.

    Args:
        binding_type: One of BINDING_TYPES (entity, operation, field, etc.)
        source_node_id: Behavior IR node ID this binding originates from
        target_key: Runtime target identifier (path, field name, etc.)
        source_module: Module that created this binding
        evidence: Initial evidence entries
        metadata: Additional metadata

    Returns:
        A binding edge dict ready for ledger insertion.
    """
    if binding_type not in BINDING_TYPES:
        raise ValueError(f"invalid_binding_type:{binding_type}")
    if not source_node_id:
        raise ValueError("source_node_id_required")
    if not target_key:
        raise ValueError("target_key_required")

    now = time.time()
    binding_id = _stable_binding_id(binding_type, source_node_id, target_key)

    return {
        "binding_id": binding_id,
        "binding_type": binding_type,
        "source_node_id": source_node_id,
        "target_key": target_key,
        "status": BindingStatus.CANDIDATE.value,
        "confidence": 0.0,
        "evidence": list(evidence or []),
        "source_module": source_module,
        "metadata": dict(metadata or {}),
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "transition_history": [{
            "from_status": None,
            "to_status": BindingStatus.CANDIDATE.value,
            "reason": "initial_creation",
            "timestamp": now,
        }],
        "conflict_info": None,
        "probe_result": None,
    }


def transition_binding(
    binding: dict[str, Any],
    to_status: BindingStatus,
    *,
    reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Transition a binding to a new state.

    Enforces the state machine. Raises ValueError on invalid transition.
    Returns a NEW binding dict (immutable update).
    """
    current = BindingStatus(binding.get("status", "CANDIDATE"))
    if not can_transition(current, to_status):
        raise ValueError(
            f"invalid_transition:{current.value}->{to_status.value}"
            f"|binding={binding.get('binding_id')}"
        )

    now = time.time()
    updated = dict(binding)
    updated["status"] = to_status.value
    updated["version"] = int(binding.get("version", 1)) + 1
    updated["updated_at"] = now

    if confidence is not None:
        updated["confidence"] = max(0.0, min(1.0, confidence))

    if evidence:
        updated["evidence"] = list(binding.get("evidence", [])) + list(evidence)

    history = list(binding.get("transition_history", []))
    history.append({
        "from_status": current.value,
        "to_status": to_status.value,
        "reason": reason or "unspecified",
        "timestamp": now,
    })
    updated["transition_history"] = history

    return updated


# ─── Binding Ledger ───────────────────────────────────────────────────────────

class BindingLedger:
    """Unified binding store with state machine enforcement.

    Thread-safe for single-process usage. All mutations return new dicts.
    """

    def __init__(self, *, project_id: str = ""):
        self._project_id = project_id
        self._bindings: dict[str, dict[str, Any]] = {}
        self._index_by_type: dict[str, set[str]] = {t: set() for t in BINDING_TYPES}
        self._index_by_source: dict[str, set[str]] = {}
        self._index_by_status: dict[str, set[str]] = {s.value: set() for s in BindingStatus}
        self._created_at = time.time()

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def size(self) -> int:
        return len(self._bindings)

    def insert(self, binding: dict[str, Any]) -> str:
        """Insert or update a binding edge. Returns binding_id."""
        binding_id = binding.get("binding_id", "")
        if not binding_id:
            raise ValueError("binding_id_required")

        existing = self._bindings.get(binding_id)
        if existing:
            # Update: remove from old indexes
            self._deindex(existing)

        self._bindings[binding_id] = binding
        self._index(binding)
        return binding_id

    def get(self, binding_id: str) -> dict[str, Any] | None:
        """Get a binding by ID."""
        return self._bindings.get(binding_id)

    def get_by_type(self, binding_type: str) -> list[dict[str, Any]]:
        """Get all bindings of a given type."""
        ids = self._index_by_type.get(binding_type, set())
        return [self._bindings[bid] for bid in ids if bid in self._bindings]

    def get_by_source(self, source_node_id: str) -> list[dict[str, Any]]:
        """Get all bindings originating from a given IR node."""
        ids = self._index_by_source.get(source_node_id, set())
        return [self._bindings[bid] for bid in ids if bid in self._bindings]

    def get_by_status(self, status: BindingStatus) -> list[dict[str, Any]]:
        """Get all bindings in a given state."""
        ids = self._index_by_status.get(status.value, set())
        return [self._bindings[bid] for bid in ids if bid in self._bindings]

    def get_executable(self, binding_type: str = "") -> list[dict[str, Any]]:
        """Get all EXECUTABLE bindings, optionally filtered by type."""
        results = self.get_by_status(BindingStatus.EXECUTABLE)
        if binding_type:
            results = [b for b in results if b.get("binding_type") == binding_type]
        return results

    def find(
        self,
        *,
        binding_type: str = "",
        source_node_id: str = "",
        target_key: str = "",
        status: BindingStatus | None = None,
    ) -> list[dict[str, Any]]:
        """Find bindings matching criteria."""
        results = list(self._bindings.values())
        if binding_type:
            results = [b for b in results if b.get("binding_type") == binding_type]
        if source_node_id:
            results = [b for b in results if b.get("source_node_id") == source_node_id]
        if target_key:
            results = [b for b in results if b.get("target_key") == target_key]
        if status:
            results = [b for b in results if b.get("status") == status.value]
        return results

    def propose(
        self,
        *,
        binding_type: str,
        source_node_id: str,
        target_key: str,
        source_module: str = "",
        evidence: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Propose a new binding or return existing one."""
        binding_id = _stable_binding_id(binding_type, source_node_id, target_key)
        existing = self._bindings.get(binding_id)
        if existing and existing.get("status") not in (
            BindingStatus.REJECTED.value,
            BindingStatus.STALE.value,
        ):
            return existing

        binding = create_binding_edge(
            binding_type=binding_type,
            source_node_id=source_node_id,
            target_key=target_key,
            source_module=source_module,
            evidence=evidence,
            metadata=metadata,
        )
        self.insert(binding)
        return binding

    def promote(
        self,
        binding_id: str,
        to_status: BindingStatus,
        *,
        reason: str = "",
        evidence: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Promote a binding to a new state."""
        binding = self._bindings.get(binding_id)
        if not binding:
            raise ValueError(f"binding_not_found:{binding_id}")

        updated = transition_binding(
            binding, to_status,
            reason=reason,
            evidence=evidence,
            confidence=confidence,
        )
        self._deindex(binding)
        self._bindings[binding_id] = updated
        self._index(updated)
        return updated

    def mark_conflicted(
        self,
        binding_id: str,
        *,
        conflict_info: dict[str, Any],
    ) -> dict[str, Any]:
        """Mark a binding as conflicted with details."""
        binding = self._bindings.get(binding_id)
        if not binding:
            raise ValueError(f"binding_not_found:{binding_id}")

        current = BindingStatus(binding.get("status", "CANDIDATE"))
        if not can_transition(current, BindingStatus.CONFLICTED):
            raise ValueError(f"cannot_conflict_from:{current.value}")

        updated = transition_binding(
            binding, BindingStatus.CONFLICTED,
            reason="conflict_detected",
        )
        updated["conflict_info"] = conflict_info
        self._deindex(binding)
        self._bindings[binding_id] = updated
        self._index(updated)
        return updated

    def coverage_summary(self) -> dict[str, Any]:
        """Return coverage statistics per binding type and status."""
        summary: dict[str, Any] = {}
        for btype in sorted(BINDING_TYPES):
            bindings = self.get_by_type(btype)
            status_counts: dict[str, int] = {}
            for b in bindings:
                st = b.get("status", "UNKNOWN")
                status_counts[st] = status_counts.get(st, 0) + 1
            executable = status_counts.get(BindingStatus.EXECUTABLE.value, 0)
            total = len(bindings)
            summary[btype] = {
                "total": total,
                "executable": executable,
                "coverage_rate": executable / total if total > 0 else 0.0,
                "status_breakdown": status_counts,
            }
        return summary

    def export(self) -> dict[str, Any]:
        """Export full ledger as a serializable dict."""
        return {
            "schema_version": SCHEMA_VERSION,
            "project_id": self._project_id,
            "created_at": self._created_at,
            "exported_at": time.time(),
            "total_bindings": self.size,
            "coverage_summary": self.coverage_summary(),
            "bindings": list(self._bindings.values()),
        }

    def load(self, data: dict[str, Any]) -> None:
        """Load bindings from exported data."""
        for binding in data.get("bindings", []):
            if isinstance(binding, dict) and binding.get("binding_id"):
                self.insert(binding)

    # ─── Internal indexing ────────────────────────────────────────────────

    def _index(self, binding: dict[str, Any]) -> None:
        bid = binding["binding_id"]
        btype = binding.get("binding_type", "")
        source = binding.get("source_node_id", "")
        status = binding.get("status", "")

        if btype in self._index_by_type:
            self._index_by_type[btype].add(bid)
        if source:
            self._index_by_source.setdefault(source, set()).add(bid)
        if status in self._index_by_status:
            self._index_by_status[status].add(bid)

    def _deindex(self, binding: dict[str, Any]) -> None:
        bid = binding["binding_id"]
        btype = binding.get("binding_type", "")
        source = binding.get("source_node_id", "")
        status = binding.get("status", "")

        if btype in self._index_by_type:
            self._index_by_type[btype].discard(bid)
        if source and source in self._index_by_source:
            self._index_by_source[source].discard(bid)
        if status in self._index_by_status:
            self._index_by_status[status].discard(bid)
