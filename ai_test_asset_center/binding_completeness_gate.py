"""Binding Completeness Gate — 10-dimension gate before planner queue entry.

An experiment obligation may only enter the execution queue when ALL required
binding dimensions are in EXECUTABLE state. The gate produces precise blocking
reasons referencing specific binding IDs.

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
    """Check if all required bindings for an obligation are EXECUTABLE.

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
    """Determine which binding dimensions are required for this obligation."""
    obl = _dict(obligation)
    required: set[str] = set()

    # Entity binding always required if operations reference entities
    if _list(obl.get("required_operations")):
        required.add("entity")
        required.add("operation")

    # Actor binding required if actors specified
    if _list(obl.get("required_actors")):
        required.add("actor")

    # Fixture binding required if fixtures specified
    if _list(obl.get("required_fixtures")):
        required.add("fixture")

    # Observer binding required if observers specified
    if _list(obl.get("required_observers")):
        required.add("observer")

    # State binding for state family
    family = _text(obl.get("risk_family"))
    if family == "state":
        required.add("state")

    # Scope binding for isolation family
    if family in ("isolation", "authorization"):
        required.add("scope")

    # Field binding for validation/conservation
    if family in ("validation", "conservation", "causal"):
        required.add("field")

    # Relation binding for cross-entity
    if family in ("cross_entity", "conservation"):
        required.add("relation")

    # Oracle input for all write operations
    prop = _dict(obl.get("property"))
    if prop.get("operation_ref") or _list(obl.get("required_operations")):
        required.add("oracle_input")

    # Minimum: entity + operation for any obligation
    required.add("entity")
    required.add("operation")

    return required


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
        # No specific requirement — pass if any executable binding exists
        if executable:
            return {"dimension": dimension, "passed": True, "reason": "any_executable_available"}
        # If no bindings at all exist for this dimension, check if it's truly needed
        if not all_of_type:
            return {"dimension": dimension, "passed": True, "reason": "no_bindings_required"}
        return {
            "dimension": dimension,
            "passed": False,
            "reason": "no_executable_bindings",
            "missing_bindings": [b.get("binding_id", "?") for b in all_of_type[:3]],
        }

    # Check if needed refs have executable bindings
    missing: list[str] = []
    for ref in needed_refs:
        found = any(
            b.get("source_node_id") == ref or ref in _text(b.get("target_key"))
            for b in executable
        )
        if not found:
            # Check if binding exists but not executable
            existing = [
                b for b in all_of_type
                if b.get("source_node_id") == ref or ref in _text(b.get("target_key"))
            ]
            if existing:
                missing.append(f"{ref}(status={existing[0].get('status')})")
            else:
                missing.append(f"{ref}(no_binding)")

    if not missing:
        return {"dimension": dimension, "passed": True, "reason": "all_refs_executable"}

    return {
        "dimension": dimension,
        "passed": False,
        "reason": "missing_executable_bindings",
        "missing_bindings": missing[:10],
    }


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
        state_ref = _text(prop.get("state_ref") or prop.get("from_state"))
        return [state_ref] if state_ref else []

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
