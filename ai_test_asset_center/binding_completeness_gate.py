"""Binding Completeness Gate — 10-dimension gate before planner queue entry.

An experiment obligation may only enter the execution queue when all required
binding dimensions are proven executable. The gate produces precise blocking
reasons referencing specific binding IDs.

Source-declared structural identities are handled separately from runtime
materialization. Exact request-schema fields and exact source-backed Behavior IR
relations are already proven by customer/source material; requiring a runtime
probe merely to prove those identities exist creates a circular planning gate.
This does not weaken runtime-sensitive actor, fixture, scope, state, observer,
or oracle-input requirements.

Schema: qualibug.binding-completeness-gate.v1
"""
from __future__ import annotations

from typing import Any

from .binding_ledger import BindingLedger, BindingStatus, BINDING_TYPES


SCHEMA_VERSION = "qualibug.binding-completeness-gate.v1"

# Gate dimensions and their requirements
GATE_DIMENSIONS = frozenset(BINDING_TYPES)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def check_binding_completeness(
    ledger: BindingLedger,
    *,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Check if all required bindings for an obligation are executable."""
    obl = _dict(obligation)
    ir = _dict(behavior_ir)

    required_dims = _determine_required_dimensions(obl, ir)
    executable_dims: list[str] = []
    blocked_dims: list[dict[str, Any]] = []

    for dim in sorted(required_dims):
        dim_check = _check_dimension(ledger, dim, obl, ir)
        if dim_check["passed"]:
            executable_dims.append(dim)
        else:
            blocked_dims.append(dim_check)

    total_required = len(required_dims)
    total_executable = len(executable_dims)
    coverage = total_executable / total_required if total_required > 0 else 1.0

    return {
        "schema_version": SCHEMA_VERSION,
        "gate_passed": len(blocked_dims) == 0,
        "executable_dimensions": executable_dims,
        "blocked_dimensions": blocked_dims,
        "total_required": total_required,
        "total_executable": total_executable,
        "coverage_rate": round(coverage, 4),
    }


def gate_or_block(
    ledger: BindingLedger,
    *,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[bool, str]:
    """Simple gate check: returns (passed, block_reason)."""
    result = check_binding_completeness(
        ledger, obligation=obligation, behavior_ir=behavior_ir
    )
    if result["gate_passed"]:
        return True, ""

    reasons = []
    for blocked in result["blocked_dimensions"]:
        dim = blocked.get("dimension", "")
        missing = blocked.get("missing_bindings", [])
        if missing:
            reasons.append(f"{dim}:{','.join(missing[:3])}")
        else:
            reasons.append(f"{dim}:{blocked.get('reason', 'unknown')}")

    return False, "BINDING_GATE_BLOCKED:" + ";".join(reasons[:6])


def _determine_required_dimensions(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> set[str]:
    """Determine which binding dimensions are required for this obligation.

    Only dimensions the obligation actually names may block. Requiring a
    dimension with no obligation-local refs makes the gate fail on unrelated
    ledger rows.
    """
    obl = _dict(obligation)
    ir = _dict(behavior_ir)
    required: set[str] = set()
    prop = _dict(obl.get("property") or obl.get("property_spec"))

    op_refs = [_text(x) for x in _list(obl.get("required_operations")) if _text(x)]
    if op_refs:
        required.add("operation")
        ops_by_id = {
            _text(op.get("id")): op
            for op in _list(ir.get("operations"))
            if isinstance(op, dict) and _text(op.get("id"))
        }
        if any(
            _text(ops_by_id.get(op_id, {}).get("entity_ref"))
            or _list(ops_by_id.get(op_id, {}).get("entity_refs"))
            for op_id in op_refs
        ):
            required.add("entity")

    if _list(obl.get("required_actors")):
        required.add("actor")

    if _list(obl.get("required_fixtures")):
        required.add("fixture")

    # required_observers names observer kinds (http_response, …), not IR
    # binding identities. The experiment compiler already gates those.

    family = _text(obl.get("risk_family"))
    if family == "state" and _text(
        prop.get("state_ref") or prop.get("from_state_ref") or prop.get("from_state")
    ):
        required.add("state")

    if family in ("isolation", "authorization") and _text(
        prop.get("scope_ref") or prop.get("ownership_param")
    ):
        required.add("scope")

    if family in ("validation", "conservation", "causal") and _list(
        prop.get("fields") or prop.get("required_fields")
    ):
        required.add("field")

    if family in ("cross_entity", "conservation") and _text(prop.get("relation_ref")):
        required.add("relation")

    if _text(prop.get("invariant_ref")) or _list(obl.get("relation_refs")):
        required.add("oracle_input")

    return required


def _active_structural_status(binding: dict[str, Any]) -> bool:
    return _text(_dict(binding).get("status")).upper() in {
        BindingStatus.CANDIDATE.value,
        BindingStatus.HIGH_CONFIDENCE.value,
        BindingStatus.RUNTIME_CONFIRMED.value,
        BindingStatus.EXECUTABLE.value,
    }


def _field_binding_matches_ref(
    binding: dict[str, Any],
    ref: str,
    obligation: dict[str, Any],
) -> bool:
    """Match a field by exact schema identity within the obligation operation."""
    row = _dict(binding)
    metadata = _dict(row.get("metadata"))
    required_ops = {
        _text(value)
        for value in _list(_dict(obligation).get("required_operations"))
        if _text(value)
    }
    operation_ref = _text(metadata.get("operation_ref"))
    if required_ops and operation_ref not in required_ops:
        return False

    target_key = _text(row.get("target_key"))
    target_field = target_key.rsplit(":", 1)[-1] if ":" in target_key else ""
    exact_names = {
        _text(metadata.get("field_name")),
        _text(metadata.get("request_path")),
        target_field,
    }
    exact_names.discard("")
    return _text(ref) in exact_names


def _source_declared_field_binding_is_authoritative(
    binding: dict[str, Any],
    ref: str,
    obligation: dict[str, Any],
) -> bool:
    """Whether an exact request-schema field proves structural executability."""
    row = _dict(binding)
    if not _field_binding_matches_ref(row, ref, obligation):
        return False
    if _text(row.get("source_module")) != "binding_builder":
        return False
    if not _active_structural_status(row):
        return False
    metadata = _dict(row.get("metadata"))
    return bool(
        _text(metadata.get("operation_ref"))
        and _text(metadata.get("request_path") or metadata.get("field_name"))
    )


def _relation_binding_matches_ref(binding: dict[str, Any], ref: str) -> bool:
    """Relations are IR nodes: require exact source-node identity, never substring."""
    return _text(_dict(binding).get("source_node_id")) == _text(ref)


def _source_declared_relation_binding_is_authoritative(
    binding: dict[str, Any],
    ref: str,
) -> bool:
    """Whether an exact source-backed Behavior IR relation is structurally proven.

    The builder emits ``source_consistency=0.9`` only when the relation carries
    source refs. A relation without that provenance stays runtime/evidence gated.
    Runtime IDs/correlation values are still resolved by fixture/runtime binding
    machinery; this authority proves only the declared relation identity.
    """
    row = _dict(binding)
    if not _relation_binding_matches_ref(row, ref):
        return False
    if _text(row.get("source_module")) != "binding_builder":
        return False
    if not _active_structural_status(row):
        return False
    source_evidence = [
        _dict(item)
        for item in _list(row.get("evidence"))
        if _text(_dict(item).get("dimension")) == "source_consistency"
    ]
    if not any(float(item.get("score") or 0.0) >= 0.9 for item in source_evidence):
        return False
    metadata = _dict(row.get("metadata"))
    return bool(
        _text(metadata.get("source_entity_ref"))
        and _text(metadata.get("target_entity_ref"))
        and _text(metadata.get("relation_type"))
    )


def _binding_matches_ref(
    binding: dict[str, Any],
    dimension: str,
    ref: str,
    obligation: dict[str, Any],
) -> bool:
    if dimension == "field":
        return _field_binding_matches_ref(binding, ref, obligation)
    if dimension == "relation":
        return _relation_binding_matches_ref(binding, ref)
    return (
        _text(binding.get("source_node_id")) == ref
        or ref in _text(binding.get("target_key"))
    )


def _check_dimension(
    ledger: BindingLedger,
    dimension: str,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Check a single binding dimension for completeness."""
    obl = _dict(obligation)
    ir = _dict(behavior_ir)

    executable = ledger.get_executable(dimension)
    all_of_type = ledger.get_by_type(dimension)
    needed_refs = _get_needed_refs(dimension, obl, ir)

    if not needed_refs:
        return {
            "dimension": dimension,
            "passed": True,
            "reason": "no_specific_refs_required",
        }

    missing: list[str] = []
    source_authoritative_count = 0
    for ref in needed_refs:
        # Generic fixture roles name a capability, not an IR node.
        if dimension == "fixture" and not _is_ir_node_ref(ref):
            if not executable:
                missing.append(f"{ref}(no_binding)")
            continue
        # Field-level scope parameters are runtime body bindings, not ledger IDs.
        if dimension == "scope" and not _is_ir_node_ref(ref):
            if not executable:
                missing.append(f"{ref}(no_binding)")
            continue
        # Business state literals name a target value, not an IR node identity.
        if dimension == "state" and not _is_ir_node_ref(ref):
            if not executable:
                missing.append(f"{ref}(no_binding)")
            continue

        found = any(
            _binding_matches_ref(b, dimension, ref, obl)
            for b in executable
        )
        if found:
            continue

        if dimension == "field" and any(
            _source_declared_field_binding_is_authoritative(b, ref, obl)
            for b in all_of_type
        ):
            source_authoritative_count += 1
            continue

        if dimension == "relation" and any(
            _source_declared_relation_binding_is_authoritative(b, ref)
            for b in all_of_type
        ):
            source_authoritative_count += 1
            continue

        existing = [
            b for b in all_of_type
            if _binding_matches_ref(b, dimension, ref, obl)
        ]
        if existing:
            # Actor dimension authoritative fallback: a CANDIDATE role-level
            # actor backed by a same-role runtime-bound account is planning-
            # executable (credentials declared; runtime proof stays with the
            # executor preflight). Same design family as the field/relation
            # source-declared fallbacks above.
            if (
                dimension == "actor"
                and _actor_role_has_runtime_bound_credentials(ir, ref)
                and _active_structural_status(existing[0])
            ):
                source_authoritative_count += 1
                continue
            missing.append(f"{ref}(status={existing[0].get('status')})")
        else:
            missing.append(f"{ref}(no_binding)")

    if not missing:
        reason = "all_refs_executable"
        if source_authoritative_count:
            if dimension == "field":
                reason = "source_declared_field_identity"
            elif dimension == "relation":
                reason = "source_declared_relation_identity"
        return {
            "dimension": dimension,
            "passed": True,
            "reason": reason,
            "source_authoritative_count": source_authoritative_count,
        }

    return {
        "dimension": dimension,
        "passed": False,
        "reason": "missing_executable_bindings",
        "missing_bindings": missing[:10],
    }


def _is_ir_node_ref(ref: str) -> bool:
    """True when a needed ref names a Behavior IR node (bir_/field:/rel:/…)."""
    lowered = ref.lower()
    return lowered.startswith("bir_") or lowered.startswith("field:") or lowered.startswith("rel:")


def _actor_role_has_runtime_bound_credentials(
    behavior_ir: dict[str, Any],
    actor_ref: str,
) -> bool:
    """True when the ref's role has a runtime-bound (declared-credential) account.

    Permission-matrix rows derive ROLE-level actors without credentials; the
    operator's runtime_actors provide NAMED accounts for those same roles.
    Planning-level executability is satisfied by that declared pairing — the
    runtime credential proof remains the executor preflight's job. Without
    this bridge, every authorization obligation bound to a matrix-derived
    actor is blocked forever even though credentials exist (measured:
    8,689 BLOCKED_MISSING_BINDING in CMP_77d5dfe1 round7, 84% of all blocks).
    """
    ir = _dict(behavior_ir)
    target_role = ""
    for actor in _list(ir.get("actors")):
        if isinstance(actor, dict) and _text(actor.get("id")) == actor_ref:
            target_role = _text(actor.get("role")).lower()
            break
    if not target_role or target_role in {"anonymous", "public"}:
        return False
    for actor in _list(ir.get("actors")):
        if not isinstance(actor, dict):
            continue
        same_role = _text(actor.get("role")).lower() == target_role
        if same_role and actor.get("runtime_bound") is True:
            return True
    return False


def _get_needed_refs(
    dimension: str,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[str]:
    """Get the specific IR node refs needed for a dimension."""
    obl = _dict(obligation)
    ir = _dict(behavior_ir)

    if dimension == "entity":
        ops_by_id = {
            _text(o.get("id")): o
            for o in _list(ir.get("operations"))
            if isinstance(o, dict)
        }
        entity_refs: set[str] = set()
        for op_id in _list(obl.get("required_operations")):
            op = ops_by_id.get(_text(op_id), {})
            entity_ref = _text(op.get("entity_ref"))
            if entity_ref:
                entity_refs.add(entity_ref)
        return list(entity_refs)

    if dimension == "operation":
        return [_text(x) for x in _list(obl.get("required_operations")) if _text(x)]

    if dimension == "actor":
        return [_text(x) for x in _list(obl.get("required_actors")) if _text(x)]

    if dimension == "fixture":
        return [_text(x) for x in _list(obl.get("required_fixtures")) if _text(x)]

    if dimension == "observer":
        return [_text(x) for x in _list(obl.get("required_observers")) if _text(x)]

    if dimension == "state":
        prop = _dict(obl.get("property"))
        state_ref = _text(
            prop.get("state_ref")
            or prop.get("from_state_ref")
            or prop.get("from_state")
        )
        if not state_ref:
            return []
        for st in _list(behavior_ir.get("states")):
            if not isinstance(st, dict):
                continue
            if state_ref in (_text(st.get("id")), _text(st.get("name"))):
                return [_text(st.get("id"))]
        return [state_ref]

    if dimension == "scope":
        prop = _dict(obl.get("property"))
        scope_ref = _text(prop.get("scope_ref") or prop.get("ownership_param"))
        return [scope_ref] if scope_ref else []

    if dimension == "field":
        prop = _dict(obl.get("property"))
        fields = _list(prop.get("fields") or prop.get("required_fields"))
        return [_text(f) for f in fields if _text(f)]

    if dimension == "relation":
        prop = _dict(obl.get("property"))
        rel_ref = _text(prop.get("relation_ref"))
        return [rel_ref] if rel_ref else []

    if dimension == "oracle_input":
        return [_text(x) for x in _list(obl.get("required_invariants")) if _text(x)]

    return []
