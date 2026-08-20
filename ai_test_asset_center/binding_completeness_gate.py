"""Binding Completeness Gate — 10-dimension gate before planner queue entry.

An experiment obligation may only enter the execution queue when ALL required
binding dimensions are in EXECUTABLE state. The gate produces precise blocking
reasons referencing specific binding IDs.

Source-declared request-schema fields are a special structural authority: their
existence is already proven by the API/Behavior IR source and must not require a
runtime probe merely to prove that the declared field exists. This does not
weaken runtime-sensitive actor, fixture, scope, state, relation, or oracle-input
requirements.

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
    """Check if all required bindings for an obligation are executable.

    Returns:
        {
            "gate_passed": bool,
            "executable_dimensions": list[str],
            "blocked_dimensions": list[{dimension, reason, missing_bindings}],
            "total_required": int,
            "total_executable": int,
            "coverage_rate": float,
        }
    """
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
    """Simple gate check: returns (passed, block_reason).

    If passed is False, block_reason contains precise binding IDs that are missing.
    """
    result = check_binding_completeness(
        ledger, obligation=obligation, behavior_ir=behavior_ir
    )
    if result["gate_passed"]:
        return True, ""

    # Build precise block reason
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
    ledger rows (for example every entity CANDIDATE when the write names none).
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


def _field_binding_matches_ref(
    binding: dict[str, Any],
    ref: str,
    obligation: dict[str, Any],
) -> bool:
    """Match a field by exact schema identity within the obligation's operation.

    Field bindings are keyed as ``<operation path>:<field>`` while their
    ``source_node_id`` is the operation id, so the generic substring matcher is
    unsafe: a field named ``id`` could match ``{id}`` in the path of an unrelated
    field. Use exact builder metadata and require operation ownership when the
    obligation declares operations.
    """
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
    """Whether an exact request-schema field proves structural executability.

    ``binding_builder`` creates these rows directly from a source-declared
    request schema. CANDIDATE/HIGH_CONFIDENCE here means the generic confidence
    scorer lacks enough independent dimensions; it does not mean the field's
    schema identity is unknown. Rejected/conflicted/stale rows remain blocked.
    """
    row = _dict(binding)
    if not _field_binding_matches_ref(row, ref, obligation):
        return False
    if _text(row.get("source_module")) != "binding_builder":
        return False
    status = _text(row.get("status")).upper()
    if status not in {
        BindingStatus.CANDIDATE.value,
        BindingStatus.HIGH_CONFIDENCE.value,
        BindingStatus.RUNTIME_CONFIRMED.value,
        BindingStatus.EXECUTABLE.value,
    }:
        return False
    metadata = _dict(row.get("metadata"))
    return bool(
        _text(metadata.get("operation_ref"))
        and _text(metadata.get("request_path") or metadata.get("field_name"))
    )


def _binding_matches_ref(
    binding: dict[str, Any],
    dimension: str,
    ref: str,
    obligation: dict[str, Any],
) -> bool:
    if dimension == "field":
        return _field_binding_matches_ref(binding, ref, obligation)
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

    # Get all executable bindings for this dimension
    executable = ledger.get_executable(dimension)
    all_of_type = ledger.get_by_type(dimension)

    # Determine what's needed for this obligation
    needed_refs = _get_needed_refs(dimension, obl, ir)

    if not needed_refs:
        # The dimension was selected without obligation-local refs. Unrelated
        # ledger rows for the same type must not become a gate failure.
        return {
            "dimension": dimension,
            "passed": True,
            "reason": "no_specific_refs_required",
        }

    # Check if needed refs have executable bindings
    missing: list[str] = []
    source_authoritative_count = 0
    for ref in needed_refs:
        # Generic fixture roles (owned_resource, disposable_fixture, …) name a
        # capability, not an IR node. Any executable fixture binding on the
        # obligation's entity satisfies them; exact ref matching would block
        # every isolation/authorization probe because the role string never
        # appears in an entity-keyed ledger row.
        if dimension == "fixture" and not _is_ir_node_ref(ref):
            if not executable:
                missing.append(f"{ref}(no_binding)")
            continue
        # Field-level scope parameters (ownership_param like ``userId``) are
        # runtime body bindings, not ledger node identities. Any executable
        # scope binding on the ledger satisfies the dimension; exact matching
        # would block every isolation probe whose ownership field is a body
        # parameter rather than a relation-scoped node.
        if dimension == "scope" and not _is_ir_node_ref(ref):
            if not executable:
                missing.append(f"{ref}(no_binding)")
            continue
        # Business state literals (``PAID``, ``SHIPPED``, …) name the target
        # value of a state transition, not an IR node identity. Exact ref
        # matching would block every state-family probe because the literal
        # never appears as a ``bir_`` node id in the entity-keyed ledger. Any
        # executable state binding satisfies the dimension; reaching the
        # concrete state value is a runtime binding concern validated at
        # execution by the state handler, not a compile-time identity.
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

        # Exact request-schema field identity is already source-proven. Requiring
        # an independent runtime probe to promote the generic binding ledger row
        # would create a circular gate: validation cannot execute until the
        # field is probed, while probes are not invoked before planning.
        if dimension == "field" and any(
            _source_declared_field_binding_is_authoritative(b, ref, obl)
            for b in all_of_type
        ):
            source_authoritative_count += 1
            continue

        # Check if binding exists but not executable
        existing = [
            b for b in all_of_type
            if _binding_matches_ref(b, dimension, ref, obl)
        ]
        if existing:
            missing.append(f"{ref}(status={existing[0].get('status')})")
        else:
            missing.append(f"{ref}(no_binding)")

    if not missing:
        return {
            "dimension": dimension,
            "passed": True,
            "reason": (
                "source_declared_field_identity"
                if dimension == "field" and source_authoritative_count
                else "all_refs_executable"
            ),
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


def _get_needed_refs(
    dimension: str,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[str]:
    """Get the specific IR node refs needed for a dimension."""
    obl = _dict(obligation)
    ir = _dict(behavior_ir)

    if dimension == "entity":
        # Entities referenced by required operations
        ops_by_id = {
            _text(o.get("id")): o
            for o in _list(ir.get("operations"))
            if isinstance(o, dict)
        }
        entity_refs: set[str] = set()
        for op_id in _list(obl.get("required_operations")):
            op = ops_by_id.get(_text(op_id), {})
            # Check entity_ref on operation
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
        # Obligations reference states either by node id (bir_…) or by their
        # declared value name (from_state="CANCELLED"). The binding ledger is
        # keyed by state node identity, so resolve the value name through the
        # Behavior IR state nodes before matching.
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
        # Oracle inputs are needed for invariants referenced by obligation
        return [_text(x) for x in _list(obl.get("required_invariants")) if _text(x)]

    return []
