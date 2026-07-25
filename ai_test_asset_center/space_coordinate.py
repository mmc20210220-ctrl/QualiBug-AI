"""System-Space Coordinate — complete coordinate model for experiments.

SPEC §8: Every experiment must carry a full system-space coordinate.
Prohibits using only (operation_id + actor_id) as experiment context.

Coordinate sections:
  business  - entity_ids, relation_ids, invariant_ids
  actor     - actor_id, role_id, tenant_id, scope_id, ownership_relation
  state     - pre_state, target_state, state_path_id
  operation - operation_ids, operation_chain_id
  surface   - execution_surface, observation_surfaces
  dynamic   - ordering, replay, concurrency, timing, failure, recovery
  scale     - data_volume, batch_size, concurrency_level, latency_profile
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "coord_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(v: Any) -> str:
    return str(v or "").strip()


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


# ─── Coordinate Creation ───────────────────────────────────────────────────────

def create_coordinate(
    *,
    entity_ids: list[str] | None = None,
    relation_ids: list[str] | None = None,
    invariant_ids: list[str] | None = None,
    actor_id: str = "",
    role_id: str = "",
    tenant_id: str = "",
    scope_id: str = "",
    ownership_relation: str = "",
    pre_state: str = "",
    target_state: str = "",
    state_path_id: str = "",
    operation_ids: list[str] | None = None,
    operation_chain_id: str = "",
    execution_surface: str = "API",
    observation_surfaces: list[str] | None = None,
    ordering: str = "SEQUENTIAL",
    replay: str = "NONE",
    concurrency: str = "NONE",
    timing: str = "NORMAL",
    failure: str = "NONE",
    recovery: str = "NONE",
    data_volume: str = "SINGLE",
    batch_size: int = 1,
    concurrency_level: int = 1,
    latency_profile: str = "NORMAL",
) -> dict[str, Any]:
    """Create a complete system-space coordinate."""
    coord_id = _stable_id(
        ",".join(entity_ids or []),
        actor_id, role_id, tenant_id,
        pre_state, target_state,
        ",".join(operation_ids or []),
        execution_surface, ordering, replay, concurrency, failure,
        data_volume, str(concurrency_level),
    )

    return {
        "coordinate_id": coord_id,
        "business": {
            "entity_ids": list(entity_ids or []),
            "relation_ids": list(relation_ids or []),
            "invariant_ids": list(invariant_ids or []),
        },
        "actor": {
            "actor_id": actor_id,
            "role_id": role_id,
            "tenant_id": tenant_id,
            "scope_id": scope_id,
            "ownership_relation": ownership_relation,
        },
        "state": {
            "pre_state": pre_state,
            "target_state": target_state,
            "state_path_id": state_path_id,
        },
        "operation": {
            "operation_ids": list(operation_ids or []),
            "operation_chain_id": operation_chain_id,
        },
        "surface": {
            "execution_surface": execution_surface,
            "observation_surfaces": list(observation_surfaces or ["API_RESPONSE"]),
        },
        "dynamic": {
            "ordering": ordering,
            "replay": replay,
            "concurrency": concurrency,
            "timing": timing,
            "failure": failure,
            "recovery": recovery,
        },
        "scale": {
            "data_volume": data_volume,
            "batch_size": batch_size,
            "concurrency_level": concurrency_level,
            "latency_profile": latency_profile,
        },
        "version": 1,
        "created_at": time.time(),
    }


# ─── Coordinate Validation ─────────────────────────────────────────────────────

REQUIRED_SECTIONS = ("business", "actor", "state", "operation", "surface", "dynamic", "scale")


def validate_coordinate(coord: dict[str, Any]) -> dict[str, Any]:
    """Validate coordinate completeness. Returns {valid, missing_sections, warnings}."""
    missing = []
    warnings = []

    for section in REQUIRED_SECTIONS:
        if section not in coord:
            missing.append(section)

    # Business section checks
    biz = coord.get("business", {})
    if not biz.get("entity_ids"):
        warnings.append("no_entity_ids")
    if not biz.get("invariant_ids"):
        warnings.append("no_invariant_ids")

    # Operation checks
    op = coord.get("operation", {})
    if not op.get("operation_ids"):
        warnings.append("no_operation_ids")

    # Surface checks
    surf = coord.get("surface", {})
    if not surf.get("execution_surface"):
        warnings.append("no_execution_surface")
    if not surf.get("observation_surfaces"):
        warnings.append("no_observation_surfaces")

    return {
        "valid": len(missing) == 0,
        "missing_sections": missing,
        "warnings": warnings,
        "completeness_score": round(1.0 - len(missing) / len(REQUIRED_SECTIONS), 2),
    }


# ─── Coordinate Comparison ─────────────────────────────────────────────────────

def coordinate_distance(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Compute distance between two coordinates (which dimensions differ)."""
    diffs = []

    # Actor differences
    a_actor = a.get("actor", {})
    b_actor = b.get("actor", {})
    if a_actor.get("actor_id") != b_actor.get("actor_id"):
        diffs.append("actor_id")
    if a_actor.get("role_id") != b_actor.get("role_id"):
        diffs.append("role_id")
    if a_actor.get("tenant_id") != b_actor.get("tenant_id"):
        diffs.append("tenant_id")

    # State differences
    a_state = a.get("state", {})
    b_state = b.get("state", {})
    if a_state.get("pre_state") != b_state.get("pre_state"):
        diffs.append("pre_state")
    if a_state.get("target_state") != b_state.get("target_state"):
        diffs.append("target_state")

    # Dynamic differences
    a_dyn = a.get("dynamic", {})
    b_dyn = b.get("dynamic", {})
    for key in ("ordering", "replay", "concurrency", "timing", "failure", "recovery"):
        if a_dyn.get(key) != b_dyn.get(key):
            diffs.append(f"dynamic.{key}")

    # Surface differences
    a_surf = a.get("surface", {})
    b_surf = b.get("surface", {})
    if a_surf.get("execution_surface") != b_surf.get("execution_surface"):
        diffs.append("execution_surface")

    # Scale differences
    a_scale = a.get("scale", {})
    b_scale = b.get("scale", {})
    for key in ("data_volume", "batch_size", "concurrency_level", "latency_profile"):
        if a_scale.get(key) != b_scale.get(key):
            diffs.append(f"scale.{key}")

    return {
        "differing_dimensions": diffs,
        "distance": len(diffs),
        "is_single_dimension_change": len(diffs) == 1,
    }


# ─── Coordinate from Experiment Context ────────────────────────────────────────

def coordinate_from_obligation(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build coordinate from an obligation and Behavior IR context."""
    obl = obligation if isinstance(obligation, dict) else {}
    ir = behavior_ir if isinstance(behavior_ir, dict) else {}

    # Extract entity refs
    entity_ids = []
    inv = obl.get("invariant") or obl.get("source_invariant") or {}
    if isinstance(inv, dict):
        ent = _text(inv.get("entity_ref"))
        if ent:
            entity_ids.append(ent)

    # Extract operation refs
    operation_ids = [_text(x) for x in _list(obl.get("required_operations")) if _text(x)]
    prop = obl.get("property") or {}
    if isinstance(prop, dict):
        op_ref = _text(prop.get("operation_ref"))
        if op_ref and op_ref not in operation_ids:
            operation_ids.append(op_ref)

    # Extract actor
    actor_ids = [_text(x) for x in _list(obl.get("required_actors")) if _text(x)]
    actor_id = actor_ids[0] if actor_ids else ""

    # Extract state
    pre_state = ""
    target_state = ""
    if isinstance(prop, dict):
        pre_state = _text(prop.get("from_state"))
        target_state = _text(prop.get("to_state"))

    # Extract invariant id
    invariant_ids = []
    obl_id = _text(obl.get("obligation_id"))
    if obl_id:
        invariant_ids.append(obl_id)

    return create_coordinate(
        entity_ids=entity_ids,
        invariant_ids=invariant_ids,
        actor_id=actor_id,
        pre_state=pre_state,
        target_state=target_state,
        operation_ids=operation_ids,
        execution_surface="API",
        observation_surfaces=["API_RESPONSE"],
    )
