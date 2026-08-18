# -*- coding: utf-8 -*-
"""Content-addressed rescue dedupe for the V1.8 abstract materialization loop.

Measured on the real post-f6 benchmark scan: 508 rescue attempts over only 145
unique obligations (71% exact duplicates; 114 obligations rescued exactly 4
times — once per planning/compile/expansion lifecycle, all with the same
still_blocked reason and no evidence change). Re-running an expensive
materialization resolution + concrete recompile for an obligation whose
blocking evidence is byte-identical produces the same NOT_MATERIALIZED result
every time.

This module provides a process-scoped, content-addressed rescue cache. The
fingerprint covers every input that could change a rescue verdict:

  * semantic obligation identity (obligation_id)
  * the compile/materialization blocker reason
  * Behavior IR evidence (content-addressed model id + required
    operations/actors/fixtures/observers sets)
  * credential availability (which actor roles resolve a token — never token
    values, so no secret leaves this process boundary)
  * observer/adapter capability (implemented status of required observers)
  * the dedupe contract version

The cache stores ONLY negative outcomes (NOT_MATERIALIZED): a successful
rescue is never cached, so a later run under identical evidence re-executes
the (cheap, succeeding) path instead of reusing a concrete experiment across
lifecycles. This satisfies: identical evidence + prior rescued=False +
deterministic blocker -> skip the expensive re-resolution, reuse the failure
receipt, keep the obligation ABSTRACT/BLOCKED with a complete, auditable
receipt, and never fabricate success.

Cross-project / cross-snapshot contamination is impossible by construction:
the fingerprint includes the Behavior IR content identity and the obligation
identity, so a different project or a changed source snapshot misses
automatically.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from ._experiment_runtime_support_mechanics import _index_by_id

RESCUE_DEDUPE_CONTRACT_VERSION = "v1"

_CACHE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_STATS: dict[str, int] = {
    "lookups": 0,
    "hits": 0,
    "stores": 0,
    "reuses": 0,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _unique(values: Any) -> list[str]:
    seen: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return sorted(seen)


def _credential_availability_fingerprint(
    required_actors: list[str],
    actor_tokens: dict[str, str] | None,
) -> str:
    """Which required actors resolve a credential (keys only, never values)."""
    resolved = sorted(
        actor for actor in required_actors if (actor_tokens or {}).get(actor)
    )
    return _sha256(_canonical_json(resolved))


def _observer_capability_fingerprint(
    required_observers: list[str],
    behavior_ir: dict[str, Any],
) -> str:
    try:
        from .observer_contracts_base import OBSERVER_REGISTRY
    except Exception:
        OBSERVER_REGISTRY = {}
    implemented = sorted(
        observer
        for observer in required_observers
        if _dict(_dict(OBSERVER_REGISTRY).get(observer)).get("implemented") is True
    )
    return _sha256(_canonical_json(implemented))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def rescue_evidence_fingerprint(
    *,
    obligation_id: str,
    compile_reason: str,
    obligation: dict[str, Any],
    abstract_experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str] | None = None,
) -> str:
    """Content-addressed identity of a rescue attempt's decision inputs.

    Every component that could change a rescue verdict is included; the
    fingerprint therefore changes exactly when the evidence changes.
    """
    required_capabilities = _dict(
        _dict(abstract_experiment.get("abstract_experiment")).get(
            "required_capabilities"
        )
    )
    operations = _unique(
        required_capabilities.get("operations")
        or obligation.get("required_operations")
    )
    actors = _unique(
        required_capabilities.get("actors") or obligation.get("required_actors")
    )
    fixtures = _unique(
        required_capabilities.get("fixtures") or obligation.get("required_fixtures")
    )
    observers = _unique(
        required_capabilities.get("observers") or obligation.get("required_observers")
    )

    ir_model_id = _text(behavior_ir.get("model_id"))
    ir_ops = _unique(
        row.get("id") or row.get("operation_id")
        for row in _list(behavior_ir.get("operations"))
        if isinstance(row, dict)
    )
    ir_actors = _unique(
        row.get("id") or row.get("role") or row.get("name")
        for row in _list(behavior_ir.get("actors"))
        if isinstance(row, dict)
    )

    components = {
        "contract_version": RESCUE_DEDUPE_CONTRACT_VERSION,
        "obligation_id": _text(obligation_id),
        "compile_reason": _text(compile_reason),
        "behavior_ir_model_id": ir_model_id,
        "behavior_ir_operations": ir_ops,
        "behavior_ir_actors": ir_actors,
        "required_operations": operations,
        "required_actors": actors,
        "required_fixtures": fixtures,
        "required_observers": observers,
        "credential_availability": _credential_availability_fingerprint(
            actors, actor_tokens
        ),
        "observer_capability": _observer_capability_fingerprint(
            observers, behavior_ir
        ),
    }
    return _sha256(_canonical_json(components))


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def rescue_cache_lookup(fingerprint: str) -> dict[str, Any] | None:
    """Return a deep-copied negative outcome for the fingerprint, or None."""
    with _LOCK:
        _STATS["lookups"] += 1
        entry = _CACHE.get(fingerprint)
        if entry is None:
            return None
        _STATS["hits"] += 1
        return json.loads(json.dumps(entry, ensure_ascii=False, default=str))


def rescue_cache_store(
    fingerprint: str,
    *,
    materialization_receipt: dict[str, Any],
    can_recompile: bool,
    still_blocked_reason: list[str],
) -> None:
    """Store a NEGATIVE rescue outcome. Successful rescues are never cached."""
    if can_recompile:
        return
    entry = {
        "fingerprint": fingerprint,
        "rescued": False,
        "can_recompile": False,
        "materialization_receipt": dict(materialization_receipt or {}),
        "still_blocked_reason": list(still_blocked_reason or []),
    }
    with _LOCK:
        _STATS["stores"] += 1
        _CACHE[fingerprint] = entry


def rescue_cache_record_reuse() -> None:
    with _LOCK:
        _STATS["reuses"] += 1


def rescue_cache_clear() -> None:
    with _LOCK:
        _CACHE.clear()
        for key in _STATS:
            _STATS[key] = 0


def rescue_cache_stats() -> dict[str, int]:
    with _LOCK:
        return dict(_STATS)


def rescue_cache_size() -> int:
    with _LOCK:
        return len(_CACHE)


def materialization_unresolved_reasons(receipt: dict[str, Any]) -> list[str]:
    """Sorted unique unresolved-requirement reasons of a materialization receipt."""
    reasons: list[str] = []
    for row in _list(receipt.get("unresolved_requirements")):
        if isinstance(row, dict) and _text(row.get("reason")):
            reasons.append(_text(row["reason"]))
    return sorted(set(reasons))


# ── Compile-time binding rescue (path-placeholder rescue) ───────────────────
# Measured on the real post-f6 benchmark scan: 508 V1.8-rescue log lines came
# from the compile-time path-placeholder rescue
# (experiment_compiler_obligation_core._rescue_binding_for_response_only_family),
# not from the materialization layer. The same obligation is compiled in
# several planning/compile/expansion lifecycles, and each compile re-runs the
# same rescue on byte-identical inputs (same binding_plan, same operation,
# same Behavior IR) with the same NOT-rescuable outcome
# (BODY_PARAMETER_NOT_SOURCE_BOUND / BODY_IDENTITY_RELATION_NOT_SOURCE_DECLARED).
# The compile-time rescue is a pure function of (binding_plan + primary_op +
# behavior_ir); a content-addressed cache keyed on those inputs (plus the
# obligation identity and contract version) skips the expensive resolver /
# fixture / example-binding scan on evidence-identical repeats.

_COMPILE_CACHE: dict[str, dict[str, Any]] = {}
_COMPILE_SEEN: set[str] = set()
_COMPILE_STATS: dict[str, int] = {
    "attempt_count": 0,
    "unique_count": 0,
    "cache_hit_count": 0,
    "reexecuted_count": 0,
}
_COMPILE_LOCK = threading.Lock()


def compile_rescue_evidence_fingerprint(
    *,
    obligation_id: str,
    reason: str,
    binding_plan: list[dict[str, Any]],
    primary_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> str:
    """Content-addressed identity of a compile-time binding rescue decision."""
    components = {
        "contract_version": RESCUE_DEDUPE_CONTRACT_VERSION + ".compile",
        "obligation_id": _text(obligation_id),
        "reason": _text(reason),
        "binding_plan": [dict(row) for row in _list(binding_plan) if isinstance(row, dict)],
        "primary_op": {
            key: value
            for key, value in _dict(primary_op).items()
            if key in {"id", "operation_id", "method", "path", "raw_path"}
        },
        "behavior_ir_model_id": _text(behavior_ir.get("model_id")),
        "behavior_ir_operations": _unique(
            row.get("id") or row.get("operation_id")
            for row in _list(behavior_ir.get("operations"))
            if isinstance(row, dict)
        ),
        "behavior_ir_entities": _unique(
            row.get("id") or row.get("name")
            for row in _list(behavior_ir.get("entities"))
            if isinstance(row, dict)
        ),
    }
    return _sha256(_canonical_json(components))


def compile_rescue_cache_lookup(fingerprint: str) -> dict[str, Any] | None:
    """Return a cached NEGATIVE rescue outcome, or None.

    Only entries stored by ``compile_rescue_cache_store`` (which carry
    ``rescued: False``) count as hits. A fingerprint merely registered as
    "seen" must NOT be treated as a hit — that would skip the rescue entirely
    and turn every outcome into the cached negative (measured: rescued=True
    42 -> 0 on a real scan).
    """
    with _COMPILE_LOCK:
        _COMPILE_STATS["attempt_count"] += 1
        entry = _COMPILE_CACHE.get(fingerprint)
        if entry is None or entry.get("rescued") is not False:
            return None
        _COMPILE_STATS["cache_hit_count"] += 1
        return json.loads(json.dumps(entry, ensure_ascii=False, default=str))


def compile_rescue_cache_register_unique(fingerprint: str) -> None:
    """Record the fingerprint as a distinct attempt (once per unique input).

    This is a visibility counter only; it never populates the lookup cache.
    """
    with _COMPILE_LOCK:
        if fingerprint not in _COMPILE_SEEN:
            _COMPILE_SEEN.add(fingerprint)
            _COMPILE_STATS["unique_count"] += 1


def compile_rescue_cache_store(
    fingerprint: str,
    *,
    rescued: bool,
    still_blocked_reason: list[str],
) -> None:
    """Store a NEGATIVE compile-rescue outcome. Successful rescues are never
    cached (a success mutates the binding_plan, changing the fingerprint)."""
    if rescued:
        return
    with _COMPILE_LOCK:
        _COMPILE_STATS["reexecuted_count"] += 1
        _COMPILE_CACHE[fingerprint] = {
            "fingerprint": fingerprint,
            "rescued": False,
            "still_blocked_reason": list(still_blocked_reason or []),
        }


def compile_rescue_cache_stats() -> dict[str, int]:
    with _COMPILE_LOCK:
        return dict(_COMPILE_STATS)


def compile_rescue_cache_clear() -> None:
    with _COMPILE_LOCK:
        _COMPILE_CACHE.clear()
        _COMPILE_SEEN.clear()
        for key in _COMPILE_STATS:
            _COMPILE_STATS[key] = 0
