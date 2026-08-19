"""Restricted assertion DSL — no eval, typed operators only."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

from .observer_contracts_base import validate_observer_receipt


ASSERTION_RECEIPT_SCHEMA = "qualibug.assertion-receipt.v1"
ASSERTION_STATUSES = frozenset({"PASS", "VIOLATION", "INDETERMINATE"})
_MISSING = object()

# Family-shaped assertion kind -> evaluator-shaped kind.
#
# Hoisted to module level from inside evaluate_assertion so the kind-to-evidence
# contract resolves a kind exactly the way the evaluator will. Without that,
# _FAMILY_ASSERTION_KIND emits "concurrency" while the dead kind is registered as
# "concurrency_final_invariant", and the compile-time block silently misses it.
#
# Note what this collapses: authorization / isolation / visibility / privacy all
# evaluate as owner_tenant_visibility, and validation evaluates as a bare
# http_status check. Four distinct defect families share one evaluation semantic —
# a depth limitation recorded here rather than hidden inside a function body.
KIND_ALIASES: dict[str, str] = {
    "authorization": "owner_tenant_visibility",
    "isolation": "owner_tenant_visibility",
    "visibility": "owner_tenant_visibility",
    "privacy": "owner_tenant_visibility",
    "validation": "http_status",
    "state": "state_transition",
    "state_integrity": "state_transition",
    "lifecycle": "state_transition",
    "invariant": "state_transition",
    "forbidden_state_transition": "state_transition",
    "idempotency": "idempotency_effect",
    "concurrency": "concurrency_final_invariant",
    "temporal": "eventual_consistency",
    "consistency": "cross_surface_consistency",
}


# ── Kind-to-evidence contract ───────────────────────────────────────────────
#
# Each assertion kind reads specific keys out of the observation dict. When no
# observer produces a required key, the kind is not "sometimes indeterminate" — it
# is PERMANENTLY indeterminate, and that outcome is folded away downstream, so the
# capability appears to exist while never being able to return a verdict.
#
# Three kinds are in that state today. Verified by enumerating every
# ``observations["..."] =`` assignment across ai_test_asset_center (58 distinct keys):
# "collection", "invariant_held" and "surfaces_agree" are written by nothing.
# Consequence worth stating plainly: the concurrency family has never been
# falsifiable — invariant_held is null in every recorded row — so no historical
# concurrency PASS is evidence that a concurrency property holds.
#
# ``temporal_date_boundary`` is the same defect one step earlier: it is compiled as an
# assertion kind (experiment_protocols_base.py:652) but appears in no SUPPORTED_KINDS
# set, so evaluate_assertion raises unsupported_assertion_kind and the experiment
# lands as a harness error.
#
# These are blocked at COMPILE time by experiment_compiler_obligation rather than left
# to die at evaluation, so the gap is visible and countable as a coverage statement
# instead of silent.
#
# To retire an entry: implement an observer that writes the named key into the
# observation dict, then delete the entry. tests/test_assertion_evidence_contract.py
# derives the produced-key set from source and fails if a kind requires a key nothing
# writes, so this cannot silently regrow.
KIND_REQUIRED_OBSERVATION_KEYS: dict[str, tuple[str, ...]] = {
    "cardinality": ("collection",),
    "concurrency_final_invariant": ("invariant_held",),
    "cross_surface_consistency": ("surfaces_agree",),
}

# Blocked at compile time only where executing is PURE WASTE -- the verdict cannot be
# computed AND no working machinery would be lost. That distinction matters because
# executing a write experiment means real mutations against a customer system.
#
# Measured across 296 stored artifacts: "consistency"/cross_surface_consistency
# produced 78 receipts, every one INDETERMINATE with CROSS_SURFACE_EVIDENCE_MISSING,
# zero PASS and zero VIOLATION; cardinality and temporal_date_boundary produced no
# receipt at all. So nothing that works is lost by blocking these three.
#
# concurrency_final_invariant is deliberately NOT here. Its evidence key
# (invariant_held) is equally unproduced, but unlike the others its machinery is real
# and exercised: the barrier protocol releases control and treatment concurrently, and
# the barrier_timeline and final_state observers both have dispatch branches and emit
# receipts. Blocking it would discard working concurrency evidence to suppress a
# missing verdict, and the missing verdict is already visible -- an INDETERMINATE
# oracle becomes a BLOCKED terminal with reason ASSERTION_INDETERMINATE
# (customer_delivery_gate_v2.py:800-801), which is countable in the attempt ledger.
# The correct fix there is to compute invariant_held from the source-declared invariant
# plus the observed before/after values, not to stop collecting the evidence.
UNPRODUCIBLE_ASSERTION_KINDS: dict[str, str] = {
    "cardinality": "collection",
    "cross_surface_consistency": "surfaces_agree",
    # Compiled by experiment_protocols_base but has no evaluator in any facade, so it
    # can only ever land as a harness error.
    "temporal_date_boundary": "<no evaluator registered>",
}

# Evidence keys no observer writes today, recorded for the coverage statement even
# where the kind is still allowed to compile. Retire an entry by implementing a
# producer, not by deleting the entry.
UNPRODUCED_OBSERVATION_KEYS: dict[str, str] = {
    "collection": "cardinality",
    "invariant_held": "concurrency_final_invariant",
    "surfaces_agree": "cross_surface_consistency",
}


# ── Assertion kind registration entry point ─────────────────────────────────
#
# SUPPORTED_KINDS is a literal set and the evaluator is a hardcoded if/elif chain, so a
# new assertion kind previously required editing this module. The facade set-union
# pattern (assertion_dsl.py, assertion_dsl_validation_base.py) can add a NAME to
# SUPPORTED_KINDS but cannot add a dispatch branch -- which is exactly how
# temporal_date_boundary became a compiled kind with no evaluator that raises
# unsupported_assertion_kind.
#
# Registration is ADDITIVE: a registered kind returns its receipt before the built-in
# chain runs, so the chain that produces every real assertion today is untouched.
_REGISTERED_ASSERTION_EVALUATORS: dict[str, Any] = {}
_REGISTERED_KIND_EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {}


def register_assertion_kind(
    kind: str,
    *,
    evaluator: Any,
    required_evidence_keys: "tuple[str, ...] | list[str]" = (),
) -> str:
    """Register an assertion kind and its evaluator. Returns the kind.

    ``evaluator(envelope) -> dict`` must return ``{"passed": True|False|None,
    "expected": Any, "actual": Any, "reason_code": str}``. ``passed`` follows the
    existing contract: True is PASS, False is VIOLATION, None is INDETERMINATE.
    ``envelope`` carries kind, spec and observations.

    ``required_evidence_keys`` names the observation keys the evaluator reads. They are
    checked against what observers declare they produce, so a kind cannot be registered
    into the state that made three built-in kinds permanently indeterminate: present in
    SUPPORTED_KINDS, compiled, executed, and structurally unable to return a verdict.
    """
    normalized = _text(kind)
    if not normalized:
        raise ValueError("register_assertion_kind requires a non-empty kind")
    if not callable(evaluator):
        raise ValueError(f"assertion kind {normalized!r} requires a callable evaluator")
    if normalized in SUPPORTED_KINDS and normalized not in _REGISTERED_ASSERTION_EVALUATORS:
        raise ValueError(
            f"assertion kind {normalized!r} is a built-in with its own dispatch branch; "
            "choose a distinct kind rather than shadowing it"
        )
    if normalized in KIND_ALIASES:
        raise ValueError(
            f"assertion kind {normalized!r} is a family alias for "
            f"{KIND_ALIASES[normalized]!r}; register the evaluator-shaped kind instead"
        )

    keys = tuple(_text(item) for item in required_evidence_keys if _text(item))
    if keys:
        from .observer_contracts_base import observer_produced_evidence_keys

        producible = observer_produced_evidence_keys()
        missing = [key for key in keys if key not in producible]
        if missing:
            raise ValueError(
                f"assertion kind {normalized!r} requires observation keys no registered "
                f"observer declares it produces: {sorted(missing)}. Register the "
                "observer with evidence_keys first, or the kind can never return a "
                "verdict."
            )

    _REGISTERED_ASSERTION_EVALUATORS[normalized] = evaluator
    _REGISTERED_KIND_EVIDENCE_KEYS[normalized] = keys
    SUPPORTED_KINDS.add(normalized)
    return normalized


def registered_assertion_kinds() -> tuple[str, ...]:
    """Assertion kinds added through register_assertion_kind."""
    return tuple(_REGISTERED_ASSERTION_EVALUATORS)


def _evaluate_registered_assertion_kind(
    evaluator: Any,
    *,
    assertion_id: str,
    kind: str,
    effective_kind: str,
    spec: dict[str, Any],
    obs: dict[str, Any],
    observer_receipt_ids: Any,
    source_refs: Any,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Run a registered evaluator and seal its verdict through the shared receipt."""

    error = ""
    harness_error = False
    expected: Any = None
    actual: Any = None
    reason_code = ""
    passed: Any = None

    # Enforce the kind-to-evidence contract BEFORE the evaluator runs.
    #
    # Without this, a registered evaluator that forgets to check for evidence presence
    # reports a VIOLATION from absent observations -- e.g. comparing an expected delta
    # of 1 against a missing value yields False, which seals as VIOLATION. That is the
    # mirror of the false-PASS class fixed elsewhere in this module: unmeasured must
    # never read as violated any more than untested may read as verified. Making it
    # structural means it does not depend on every evaluator author remembering.
    _required = _REGISTERED_KIND_EVIDENCE_KEYS.get(effective_kind) or ()
    _observations = _dict(obs)
    _absent = [key for key in _required if _observations.get(key) in (None, {}, [], "")]
    if _absent:
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="ASSERTION_EVIDENCE_MISSING",
            expected={"required_observation_keys": list(_required)},
            actual={"absent_observation_keys": _absent},
            error="",
            observer_receipt_ids=observer_receipt_ids,
            source_refs=source_refs,
            harness_error=False,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )

    try:
        result = evaluator({
            "kind": kind,
            "effective_kind": effective_kind,
            "spec": _dict(spec),
            "observations": _dict(obs),
        })
        if not isinstance(result, dict):
            raise TypeError(
                f"evaluator returned {type(result).__name__}, expected dict"
            )
        passed = result.get("passed")
        # Identity, not equality. `1 in (True, False, None)` is True in Python because
        # 1 == True, so an equality check accepts 1 and 0 as verdicts. A verdict must be
        # an explicit boolean or an explicit "no verdict", never a value that merely
        # compares equal to one -- the same discipline this module applies to evidence.
        if passed is not True and passed is not False and passed is not None:
            raise ValueError(
                f"evaluator returned passed={passed!r} ({type(passed).__name__}); "
                "must be exactly True, False or None"
            )
        expected = result.get("expected")
        actual = result.get("actual")
        reason_code = _text(result.get("reason_code"))
    except Exception as exc:  # noqa: BLE001 - reported as a harness error, never hidden
        error = f"{type(exc).__name__}: {exc}"
        reason_code = "ASSERTION_EVALUATION_ERROR"
        harness_error = True
        passed = None

    status = "INDETERMINATE" if passed is None else "PASS" if passed else "VIOLATION"
    if status == "INDETERMINATE" and not reason_code:
        reason_code = "ASSERTION_EVIDENCE_MISSING"
    return _assertion_receipt(
        assertion_id=assertion_id,
        kind=kind,
        status=status,
        reason_code=reason_code,
        expected=expected,
        actual=actual,
        error=error,
        observer_receipt_ids=observer_receipt_ids,
        source_refs=source_refs,
        harness_error=harness_error,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )


def unproducible_assertion_evidence(kind: str) -> str:
    """Return the missing evidence key for *kind*, or "" when it is satisfiable.

    Checked both before and after alias resolution, because a protocol may emit the
    family-shaped name ("concurrency") while the dead kind is registered under the
    evaluator-shaped name ("concurrency_final_invariant"). Matching only one of the
    two is how this gate would silently miss the concurrency family entirely.
    """
    normalized = str(kind or "").strip()
    missing = UNPRODUCIBLE_ASSERTION_KINDS.get(normalized, "")
    if missing:
        return missing
    return UNPRODUCIBLE_ASSERTION_KINDS.get(KIND_ALIASES.get(normalized, normalized), "")


SUPPORTED_KINDS = {
    "http_status",
    "http_status_class",
    "json_path_exists",
    "json_path_type",
    "json_path_compare",
    "equality",
    "delta",
    "cardinality",
    "state_transition",
    "postcondition",
    "owner_tenant_visibility",
    "conservation",
    "idempotency_effect",
    "concurrency_final_invariant",
    # Same-experiment concurrent double-write: two writes released at the same
    # moment on the same resource. dual 2xx alone is never a verdict
    # (insufficient_signal: dual_2xx_alone); the boundary comes only from the
    # rule's own declaration (structured comparison, non-negative equation, or
    # the oversell runtime projection) and the concurrent release must be
    # evidenced by the barrier timeline.
    "concurrent_double_write",
    "eventual_consistency",
    "cross_surface_consistency",
    "field_delta",
    "cross_entity_consistency",
    "limit_constraint",
    # Field boundary constraint (available_qty、locked_qty 均不能为负数):
    # after the write the declared fields must not go below zero. Evaluated
    # against the entity_state observer's after_values, same evidence channel
    # as conservation.
    "non_negative",
    # Response-side constraint: a source rule forbids a field in the
    # response (导出结果禁止包含 password) — the field must be absent from
    # the observed body, not merely differ between arms.
    "response_field_absent",
    # Read-side row-state filter: a rule constrains which entity rows a
    # caller may see (用户端不展示下架商品) and the operation's own
    # declaration states the ONLY states it may return (仅返回 ON_SALE) —
    # every row's state field must be within the declared set.
    "response_rows_state_filter",
    # UI/UX page-state consistency: a UI rule constrains a browser page
    # (Only products with status ON_SALE may be rendered) — the rendered DOM
    # text must not carry state vocabulary outside the rule's declared
    # allowed set.
    "ui_state_consistency",
    # Decision-surface idempotency (idempotency on /check /resolve /validate
    # endpoints): the operation's response IS its effect, so the property is
    # that the identical replayed input yields the same decision body.
    "response_consistency",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _state_token(value: Any) -> str:
    """Normalize presentation-only enum differences without changing meaning."""

    if isinstance(value, dict):
        # State snapshots arrive as entity bodies (before/after governance
        # reads), not bare enum strings. Textifying the whole dict would make
        # the token never equal the declared state. Extract the state field by
        # generic cross-industry names first, then any scalar string field as
        # a fail-closed fallback (an unrelated field still yields a mismatch,
        # which stays INDETERMINATE rather than inventing a verdict).
        # Industry field names (order_status, payment_status, …) are never
        # hardcoded — the scalar fallback covers them on any industry schema.
        for key in (
            "status",
            "state",
            "lifecycle_status",
            "lifecycleStatus",
            "approval_status",
            "approvalStatus",
        ):
            if key in value and value[key] is not None:
                value = value[key]
                break
        else:
            for _k, _v in value.items():
                if isinstance(_v, str) and _v.strip():
                    value = _v
                    break

    normalized = _text(value).replace("-", " ").replace("_", " ")
    return "_".join(normalized.split()).casefold()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _to_decimal(value: Any) -> Decimal | None:
    """Safely convert a value to Decimal for precise numeric comparison."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def _compute_aggregate(
    values: list[Any],
    aggregate_op: str,
) -> Decimal | None:
    """Compute an aggregate over a list of numeric values using Decimal."""
    decimals = [d for d in (_to_decimal(v) for v in values) if d is not None]
    if not decimals:
        return None
    op = _text(aggregate_op).upper()
    if op == "SUM":
        return sum(decimals, Decimal("0"))
    if op == "COUNT":
        return Decimal(len(decimals))
    if op == "MIN":
        return min(decimals)
    if op == "MAX":
        return max(decimals)
    if op == "AVG":
        return sum(decimals, Decimal("0")) / Decimal(len(decimals))
    return None


def _resolve_expression_side(
    side: dict[str, Any],
    observations: dict[str, Any],
    phase: str,
) -> Decimal | None:
    """Resolve one side of a structured expression to a Decimal value.

    phase: 'before' or 'after' — determines which snapshot to read from.
    Supports both legacy format (aggregate/entity/field) and resolver format
    (node_type/function/source_entity_name/value_field/field_id/entity_name).
    """
    if not isinstance(side, dict):
        return None

    # Normalize resolver format to legacy keys
    node_type = _text(side.get("node_type"))
    aggregate_op = _text(side.get("aggregate") or side.get("function"))
    entity = _text(side.get("entity") or side.get("source_entity_name") or side.get("entity_name"))
    entity_alias = _text(side.get("source_entity_alias") or side.get("entity_alias"))
    field = _text(side.get("field") or side.get("value_field") or side.get("field_id"))

    # Multi-entity aggregate
    if aggregate_op or node_type == "aggregate":
        if not aggregate_op:
            aggregate_op = "SUM"
        # Try multi_entity_state with entity name
        mes = _dict(observations.get("multi_entity_state"))
        # Try by entity name first, then by alias
        for key in (entity, entity_alias):
            if not key:
                continue
            entity_data = _dict(mes.get(key))
            phase_data = entity_data.get(phase)
            if isinstance(phase_data, list):
                values = [
                    item.get(field)
                    for item in phase_data
                    if isinstance(item, dict) and item.get(field) is not None
                ]
                if values:
                    return _compute_aggregate(values, aggregate_op)
        # Try "related" key (observer stores related entities under "related")
        related_data = _dict(mes.get("related"))
        phase_data = related_data.get(phase)
        if isinstance(phase_data, list):
            values = [
                item.get(field)
                for item in phase_data
                if isinstance(item, dict) and item.get(field) is not None
            ]
            if values:
                return _compute_aggregate(values, aggregate_op)
        # Fallback: try related_entities in observations
        related = _dict(observations.get("related_entities"))
        for key in (entity, entity_alias):
            if not key:
                continue
            rel_data = _dict(related.get(key))
            rel_phase = rel_data.get(phase)
            if isinstance(rel_phase, list):
                values = [
                    item.get(field)
                    for item in rel_phase
                    if isinstance(item, dict) and item.get(field) is not None
                ]
                if values:
                    return _compute_aggregate(values, aggregate_op)
        # Fallback: single value from before/after_values
        bv = observations.get(f"{phase}_values")
        if isinstance(bv, dict) and field in bv:
            return _to_decimal(bv[field])
        return None

    # Single field reference (node_type == "field_ref" or has field)
    if field:
        mes = _dict(observations.get("multi_entity_state"))
        # Try by entity name, alias, or "root"
        for key in (entity, entity_alias, "root"):
            if not key:
                continue
            entity_data = _dict(mes.get(key))
            phase_data = entity_data.get(phase)
            if isinstance(phase_data, dict) and field in phase_data:
                return _to_decimal(phase_data[field])
            if isinstance(phase_data, list) and phase_data:
                first = phase_data[0] if phase_data else {}
                if isinstance(first, dict) and field in first:
                    return _to_decimal(first[field])
        # Fallback: before/after_values
        bv = observations.get(f"{phase}_values")
        if isinstance(bv, dict) and field in bv:
            return _to_decimal(bv[field])
        # Fallback: before/after state body
        state = observations.get(f"{phase}_state")
        if isinstance(state, dict) and field in state:
            return _to_decimal(state[field])
    # Literal value
    if "value" in side:
        return _to_decimal(side["value"])
    return None


def _compare_decimals(
    left: Decimal,
    right: Decimal,
    operator: str,
) -> bool:
    """Compare two Decimal values with the given operator."""
    op = _text(operator).upper()
    if op in ("EQ", "EQUALS", "=="):
        return left == right
    if op in ("LTE", "LE", "<="):
        return left <= right
    if op in ("GTE", "GE", ">="):
        return left >= right
    if op in ("LT", "<"):
        return left < right
    if op in ("GT", ">"):
        return left > right
    if op in ("NEQ", "NE", "!="):
        return left != right
    return False


def _evaluate_structured_expression(
    spec: dict[str, Any],
    obs: dict[str, Any],
) -> tuple[str, Any, Any]:
    """Evaluate a structured expression from the resolver.

    Returns (reason_code, expected, actual).
    reason_code is empty string on successful evaluation (passed or violated).
    """
    expr = _dict(spec.get("structured_expression"))
    expr_type = _text(spec.get("expression_type") or expr.get("type"))
    operator = _text(expr.get("operator") or spec.get("operator"))
    left_spec = _dict(expr.get("left"))
    right_spec = _dict(expr.get("right"))

    # ── State consistency / IMPLIES evaluation ──
    if expr_type in ("state_consistency", "cross_entity_state"):
        return _evaluate_state_implication(spec, obs)

    # ── Compensation (Before/After delta) ──
    if expr_type == "compensation":
        return _evaluate_compensation(spec, obs)

    # ── Numeric comparison (conservation / limit_constraint) ──
    if not left_spec and not right_spec:
        return ("UNRESOLVED_EXPRESSION_STRUCTURE", None, None)

    # For limit_constraint: evaluate after-state only
    # For conservation: compare before vs after
    if expr_type == "limit_constraint" or operator in ("LTE", "GTE", "LT", "GT"):
        left_val = _resolve_expression_side(left_spec, obs, "after")
        right_val = _resolve_expression_side(right_spec, obs, "after")
        if left_val is None or right_val is None:
            # Try before phase as fallback
            left_val = left_val if left_val is not None else _resolve_expression_side(left_spec, obs, "before")
            right_val = right_val if right_val is not None else _resolve_expression_side(right_spec, obs, "before")
        if left_val is None or right_val is None:
            return ("EXPRESSION_VALUES_MISSING", {"operator": operator}, {"left": None, "right": None})
        expected = {"operator": operator, "left": str(left_val), "right": str(right_val)}
        actual = {"left": str(left_val), "right": str(right_val)}
        passed = _compare_decimals(left_val, right_val, operator)
        return ("" if passed else "LIMIT_CONSTRAINT_VIOLATED", expected, actual)

    # Conservation: SUM(left) == SUM(right) or left unchanged
    if operator in ("EQ", "EQUALS", "CONSERVATION"):
        left_before = _resolve_expression_side(left_spec, obs, "before")
        left_after = _resolve_expression_side(left_spec, obs, "after")
        right_before = _resolve_expression_side(right_spec, obs, "before")
        right_after = _resolve_expression_side(right_spec, obs, "after")
        # If both sides resolve in after phase, compare them
        if left_after is not None and right_after is not None:
            expected = {"operator": "EQ", "left": str(left_after), "right": str(right_after)}
            actual = {"left": str(left_after), "right": str(right_after)}
            passed = left_after == right_after
            return ("" if passed else "CONSERVATION_VIOLATED", expected, actual)
        # If only left side resolves, check unchanged
        if left_before is not None and left_after is not None:
            expected = {"operator": "CONSERVATION", "before": str(left_before), "after": str(left_after)}
            actual = {"before": str(left_before), "after": str(left_after)}
            passed = left_before == left_after
            return ("" if passed else "CONSERVATION_VIOLATED", expected, actual)
        return ("EXPRESSION_VALUES_MISSING", {"operator": operator}, {"left_before": str(left_before) if left_before else None, "left_after": str(left_after) if left_after else None})

    return ("UNSUPPORTED_EXPRESSION_OPERATOR", {"operator": operator}, None)


def _evaluate_state_implication(
    spec: dict[str, Any],
    obs: dict[str, Any],
) -> tuple[str, Any, Any]:
    """Evaluate IMPLIES(root.condition → related.constraint)."""
    expr = _dict(spec.get("structured_expression"))
    condition = _dict(expr.get("condition") or expr.get("left"))
    constraint = _dict(expr.get("constraint") or expr.get("right"))

    # Get root entity state (after phase)
    root_entity = _text(spec.get("root_entity") or condition.get("entity"))
    root_field = _text(condition.get("field"))
    root_value = _text(condition.get("value") or condition.get("equals"))

    # Get related entity constraint
    related_entity = _text(constraint.get("entity"))
    related_field = _text(constraint.get("field"))
    related_expected = _text(constraint.get("value") or constraint.get("equals"))
    related_op = _text(constraint.get("operator") or "EQ")

    # Try to get root state from observations
    mes = _dict(obs.get("multi_entity_state"))
    root_data = _dict(mes.get(root_entity))
    root_after = root_data.get("after")

    # Fallback: use after_state directly
    if not root_after:
        after_state = obs.get("after_state")
        if isinstance(after_state, dict):
            root_after = after_state

    if not isinstance(root_after, dict) and not isinstance(root_after, list):
        return ("CROSS_ENTITY_EVIDENCE_MISSING", {"root_entity": root_entity}, None)

    # Check if condition is met
    root_items = root_after if isinstance(root_after, list) else [root_after]
    condition_met = False
    for item in root_items:
        if isinstance(item, dict) and root_field:
            actual_val = _text(item.get(root_field))
            if root_value and _state_token(actual_val) == _state_token(root_value):
                condition_met = True
                break

    if not condition_met:
        # Trigger not observed → the implication was NOT TESTED.
        #
        # An empty reason_code here means PASS, so this branch used to report a green
        # result from zero evidence: "if the root entity reached state X then the
        # related entity must satisfy C" passed whenever X was never observed.
        # Vacuous truth is sound for an invariant evaluated over arbitrary given data,
        # but it is not sound here, because establishing the trigger state is the
        # experiment's OWN job. A failure to reach state X -- an unestablished
        # precondition, a state field read from the wrong key, a write that silently
        # did not apply -- is indistinguishable from "the trigger legitimately did not
        # occur", and the first three are exactly the conditions the experiment exists
        # to detect.
        #
        # Reported as INDETERMINATE with a named reason (no "VIOLATED" substring, so the
        # caller at the cross_entity_consistency branch treats it as missing evidence
        # rather than a violation). Untested must never read as verified.
        return (
            "STATE_IMPLICATION_TRIGGER_NOT_OBSERVED",
            {"implication": "not_tested", "required_root_state": root_value},
            {"condition_met": False, "observed_root_field": root_field},
        )

    # Condition met → check constraint on related entities
    related_data = _dict(mes.get(related_entity))
    related_after = related_data.get("after")
    if not related_after:
        # Try related_entities in observations
        related_obs = _dict(obs.get("related_entities"))
        rel_data = _dict(related_obs.get(related_entity))
        related_after = rel_data.get("after")

    if not related_after:
        return ("CROSS_ENTITY_RELATED_EVIDENCE_MISSING", {"related_entity": related_entity}, {"condition_met": True})

    related_items = related_after if isinstance(related_after, list) else [related_after]
    violations = []
    for item in related_items:
        if not isinstance(item, dict):
            continue
        actual_val = _text(item.get(related_field))
        if related_expected and _state_token(actual_val) != _state_token(related_expected):
            violations.append({"actual": actual_val, "expected": related_expected})

    expected = {"root_condition": f"{root_entity}.{root_field}={root_value}", "related_constraint": f"{related_entity}.{related_field}={related_expected}"}
    actual = {"condition_met": True, "violations": violations}
    if violations:
        return ("CROSS_ENTITY_CONSISTENCY_VIOLATED", expected, actual)
    return ("", expected, actual)


def _evaluate_compensation(
    spec: dict[str, Any],
    obs: dict[str, Any],
) -> tuple[str, Any, Any]:
    """Evaluate Before/After delta compensation (e.g. release reserved amount)."""
    expr = _dict(spec.get("structured_expression"))
    target_field = _text(expr.get("field") or _dict(expr.get("left")).get("field"))
    expected_direction = _text(expr.get("direction") or "increase")
    entity = _text(expr.get("entity") or _dict(expr.get("left")).get("entity") or spec.get("root_entity"))

    # Get before/after values
    before_val = None
    after_val = None

    # Try multi_entity_state
    mes = _dict(obs.get("multi_entity_state"))
    entity_data = _dict(mes.get(entity))
    before_state = entity_data.get("before")
    after_state = entity_data.get("after")
    if isinstance(before_state, dict) and target_field in before_state:
        before_val = _to_decimal(before_state[target_field])
    if isinstance(after_state, dict) and target_field in after_state:
        after_val = _to_decimal(after_state[target_field])

    # Fallback: before/after_values
    if before_val is None:
        bv = obs.get("before_values")
        if isinstance(bv, dict) and target_field in bv:
            before_val = _to_decimal(bv[target_field])
    if after_val is None:
        av = obs.get("after_values")
        if isinstance(av, dict) and target_field in av:
            after_val = _to_decimal(av[target_field])

    if before_val is None or after_val is None:
        return ("COMPENSATION_EVIDENCE_MISSING", {"field": target_field, "entity": entity}, {"before": None, "after": None})

    delta = after_val - before_val
    expected = {"field": target_field, "direction": expected_direction, "before": str(before_val)}
    actual = {"before": str(before_val), "after": str(after_val), "delta": str(delta)}

    if expected_direction in ("increase", "restore", "release"):
        passed = delta > Decimal("0")
    elif expected_direction in ("decrease", "deduct", "reserve"):
        passed = delta < Decimal("0")
    else:
        passed = delta != Decimal("0")

    if passed:
        return ("", expected, actual)
    return ("COMPENSATION_NOT_OBSERVED", expected, actual)


def _compute_invariant_held_from_source(
    expression: dict[str, Any],
    observations: dict[str, Any],
) -> tuple[bool | None, str]:
    """Compute the concurrency final invariant from a source-declared expression.

    Only a structured comparison the source actually declared is computed:
    ``{left} OP {right}`` with OP in GTE/LTE/GT/LT/EQ/NEQ, evaluated over the
    after-state numerics observed by the final_state observer. This is how a
    concurrent lost-update becomes falsifiable: a declared bound such as
    ``available_qty >= 0`` is checked against the observed final value, so a
    race that drives it negative is a VIOLATION. Anything else — natural-language
    invariants, missing fields, missing numerics — returns (None, reason) and the
    caller keeps FINAL_INVARIANT_MISSING. Nothing is inferred; the computed
    verdict is stamped with COMPUTED_FROM_SOURCE_INVARIANT for traceability.
    """
    expr = _dict(expression)
    structured = _dict(expr.get("structured_expression"))
    operator = _text(expr.get("operator") or structured.get("operator")).upper()
    if operator not in {"GTE", "LTE", "GT", "LT", "EQ", "NEQ"}:
        return None, "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    left = _dict(expr.get("left") or structured.get("left"))
    right = _dict(expr.get("right") or structured.get("right"))
    if not left or not right:
        return None, "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    # After-state comparison only. before == after conservation would misreport a
    # legitimate concurrent mutation (a concurrent debit changes the total by
    # design), so unchanged-sum semantics are never applied here.
    left_val = _resolve_expression_side(left, observations, "after")
    right_val = _resolve_expression_side(right, observations, "after")
    if left_val is None or right_val is None:
        return None, "CONCURRENCY_INVARIANT_VALUES_MISSING"
    return (
        _compare_decimals(left_val, right_val, operator),
        "COMPUTED_FROM_SOURCE_INVARIANT",
    )


def _non_negative_boundary_held(
    equation: dict[str, Any],
    observations: dict[str, Any],
) -> tuple[bool | None, str]:
    """Evaluate a source-declared non-negative field boundary over the after state.

    A rule like 库存不能为负 / available_qty 不得为负 declares a field boundary:
    the declared fields must never go below zero. The concurrent double-write
    protocol projects it to the pair: after BOTH concurrent writes the observed
    after-values of every declared term must stay >= 0. All values are the
    observer-captured entity readback; a term below zero after the pair is the
    boundary break. Only the structured ``non_negative`` equation form the IR
    builds is computed — nothing is inferred from prose.
    """
    if not isinstance(equation, dict):
        return None, "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    if _text(equation.get("operator")).lower() != "non_negative":
        return None, "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    terms = [
        _text(item)
        for item in _list(equation.get("terms") or equation.get("fields"))
        if _text(item)
    ]
    if not terms:
        return None, "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    after_values = _dict(observations.get("after_values"))
    if not after_values or any(
        term not in after_values
        or isinstance(after_values[term], bool)
        or not isinstance(after_values[term], (int, float))
        for term in terms
    ):
        return None, "CONCURRENCY_INVARIANT_VALUES_MISSING"
    return (
        all(float(after_values[term]) >= 0 for term in terms),
        "COMPUTED_FROM_NON_NEGATIVE_EQUATION",
    )


def _oversell_projection_held(
    observations: dict[str, Any],
) -> tuple[bool | None, str]:
    """Project the concurrent double-write onto the resource's own readback.

    A rule declaring oversell prohibition (同一个 SKU 在高并发下不得超卖 /
    不得超额 / oversell) binds no field at compile time — the violated quantity is
    whatever the resource readback carries. The projection is a controlled two-arm
    comparison on the SAME resource: every numeric field that was non-negative in
    the before readback and is negative in the after readback after both concurrent
    writes were accepted is evidence of oversell. Fields already negative before
    are excluded (they cannot prove a NEW oversell), so a legitimately negative
    field never fabricates a verdict; at least one common numeric field must exist
    or the projection stays INDETERMINATE (no vacuous pass from an empty field
    set).
    """
    from .observer_contracts_base import _numeric_snapshot_values

    before_nums: dict[str, float] = {}
    after_nums: dict[str, float] = {}
    before_body = observations.get("before_state")
    after_body = observations.get("after_state")
    if isinstance(before_body, (dict, list)) and isinstance(after_body, (dict, list)):
        before_nums = _numeric_snapshot_values(
            before_body, [], allow_unscoped_numeric=True
        )
        after_nums = _numeric_snapshot_values(
            after_body, [], allow_unscoped_numeric=True
        )
    if not before_nums or not after_nums:
        before_nums = {
            _text(key): value
            for key, value in _dict(observations.get("before_values")).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        after_nums = {
            _text(key): value
            for key, value in _dict(observations.get("after_values")).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    common = sorted(set(before_nums) & set(after_nums))
    if not common:
        return None, "CONCURRENCY_INVARIANT_VALUES_MISSING"
    for field in common:
        before_val = _to_decimal(before_nums[field])
        after_val = _to_decimal(after_nums[field])
        if before_val is None or after_val is None:
            continue
        if before_val >= Decimal("0") and after_val < Decimal("0"):
            return False, "COMPUTED_FROM_OVERSELL_PROJECTION"
    return True, "COMPUTED_FROM_OVERSELL_PROJECTION"


def _concurrent_boundary_held(
    spec: dict[str, Any],
    observations: dict[str, Any],
) -> tuple[bool | None, str]:
    """Resolve the concurrent double-write boundary from the rule's own declaration.

    Priority is source-grounded and strict:
    1. a structured comparison (GTE/LTE/GT/LT/EQ/NEQ) the source declared — its
       verdict is final even when evidence is missing (never substituted);
    2. the IR-built non-negative field equation (不能为负 rules);
    3. the oversell runtime projection — ONLY when the protocol flagged the rule
       as oversell-prohibition (超卖/超额/oversell) AND no field boundary exists.
    Anything else stays INDETERMINATE with a named reason — a boundary is never
    invented.
    """
    _prop = _dict(spec.get("property"))
    expression = (
        _dict(spec.get("expression"))
        or _dict(_prop.get("expression"))
        or _dict(_dict(_prop.get("field_rule_binding")).get("typed_expression"))
    )
    # The protocol emits the boundary at assertion level; the legacy compile
    # chain carries it inside the property expression. Both the structured form
    # (structured_expression / left+right) and the IR equation form are accepted.
    _structured = (
        _dict(expression.get("structured_expression"))
        or _dict(spec.get("structured_expression"))
    )
    _operator = _text(
        expression.get("operator") or _structured.get("operator")
    ).upper()
    if _structured or _operator in {"GTE", "LTE", "GT", "LT", "EQ", "NEQ"}:
        if not expression:
            expression = {"structured_expression": _structured}
        return _compute_invariant_held_from_source(expression, observations)
    _equation = _dict(expression.get("equation")) or _dict(spec.get("equation"))
    if _equation:
        return _non_negative_boundary_held(_equation, observations)
    if spec.get("oversell_projection") is True:
        return _oversell_projection_held(observations)
    return None, "CONCURRENCY_INVARIANT_NOT_COMPARABLE"


def _decision_pair_consistency(observations: dict[str, Any]) -> bool | None:
    """Response consistency of the two concurrent decision writes.

    Decision surfaces (/check, /resolve, /validate…) return their decision in
    the response body and have no entity boundary to hold. Both arms must
    have been accepted (2xx) with comparable response bodies; a rejected arm
    means the race was serialized/guarded and is not evidence of divergence.
    """
    control = _dict(observations.get("control_observation"))
    treatment = _dict(observations.get("treatment_observation"))
    ctl_status = control.get("status_code")
    trt_status = treatment.get("status_code")
    try:
        ctl_status_i = int(ctl_status) if ctl_status is not None else 0
        trt_status_i = int(trt_status) if trt_status is not None else 0
    except (TypeError, ValueError):
        return None
    if ctl_status_i <= 0 or trt_status_i <= 0:
        return None
    if not (200 <= ctl_status_i < 300 and 200 <= trt_status_i < 300):
        return None
    control_body = control.get("body")
    treatment_body = treatment.get("body")
    if not isinstance(control_body, (dict, list)) or not isinstance(treatment_body, (dict, list)):
        return None
    return control_body == treatment_body


def _dual_write_statuses(
    observations: dict[str, Any], phase: str
) -> list[int]:
    """Observed status codes of one arm of the concurrent pair, or [].

    ``control_statuses`` / ``treatment_statuses`` are written by the outcome
    finalizer from the executed steps; the per-step observation is the fallback.
    """
    statuses = observations.get(f"{phase}_statuses")
    if isinstance(statuses, list) and statuses:
        parsed: list[int] = []
        for raw in statuses:
            try:
                parsed.append(int(raw))
            except (TypeError, ValueError):
                continue
        if parsed:
            return parsed
    step = observations.get(f"{phase}_observation")
    if isinstance(step, dict) and step.get("status_code") is not None:
        try:
            return [int(step["status_code"])]
        except (TypeError, ValueError):
            return []
    return []


def _assertion_receipt(
    *,
    assertion_id: str,
    kind: str,
    status: str,
    reason_code: str,
    expected: Any,
    actual: Any,
    error: str,
    observer_receipt_ids: list[str],
    source_refs: list[dict[str, Any]],
    harness_error: bool,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    normalized_status = _text(status).upper()
    if normalized_status not in ASSERTION_STATUSES:
        raise ValueError(f"assertion_status_invalid:{normalized_status}")
    payload = {
        "schema_version": ASSERTION_RECEIPT_SCHEMA,
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "assertion_id": _text(assertion_id),
        "kind": _text(kind),
        "status": normalized_status,
        "reason_code": _text(reason_code),
        "passed": (
            True
            if normalized_status == "PASS"
            else False
            if normalized_status == "VIOLATION"
            else None
        ),
        "expected": expected,
        "actual": actual,
        "error": _text(error),
        "observer_receipt_ids": sorted(
            set(_text(item) for item in observer_receipt_ids if _text(item))
        ),
        "source_refs": [
            dict(item) for item in source_refs if isinstance(item, dict)
        ],
        "harness_error": bool(harness_error),
    }
    return {
        **payload,
        "receipt_id": "assert_" + hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()[:24],
    }


def validate_assertion_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    required_fields = {
        "schema_version",
        "receipt_id",
        "campaign_id",
        "execution_id",
        "assertion_id",
        "kind",
        "status",
        "reason_code",
        "passed",
        "expected",
        "actual",
        "error",
        "observer_receipt_ids",
        "source_refs",
        "harness_error",
    }
    # V1.6.1: field_oracle_trace is an optional persisted enrichment on deep
    # assertion kinds. Exact-key equality previously stripped every Trace.
    optional_fields = {"field_oracle_trace"}
    keys = set(row)
    if not required_fields.issubset(keys):
        raise ValueError("assertion_receipt_fields_invalid")
    if keys - required_fields - optional_fields:
        raise ValueError("assertion_receipt_fields_invalid")
    if row.get("schema_version") != ASSERTION_RECEIPT_SCHEMA:
        raise ValueError("assertion_receipt_schema_invalid")
    if not isinstance(row.get("observer_receipt_ids"), list) or not isinstance(
        row.get("source_refs"), list
    ):
        raise ValueError("assertion_receipt_content_invalid")
    expected = _assertion_receipt(
        assertion_id=_text(row.get("assertion_id")),
        kind=_text(row.get("kind")),
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        expected=row.get("expected"),
        actual=row.get("actual"),
        error=_text(row.get("error")),
        observer_receipt_ids=list(row["observer_receipt_ids"]),
        source_refs=[
            dict(item)
            for item in row["source_refs"]
            if isinstance(item, dict)
        ],
        harness_error=bool(row.get("harness_error")),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )
    base_row = {key: row[key] for key in required_fields}
    if base_row != expected:
        raise ValueError("assertion_receipt_fingerprint_invalid")
    out = dict(expected)
    trace = row.get("field_oracle_trace")
    if isinstance(trace, dict):
        out["field_oracle_trace"] = dict(trace)
    return out


def _framework_generic_404_body(obs: dict[str, Any]) -> bool:
    """True when the observation body is a framework-generic 404 marker.

    Starlette/FastAPI return ``{"detail": "Not Found"}`` for unmatched routes;
    business 404s carry specific details (entity names/ids) and never match
    this exact marker. Also accepts the raw-string form for bodies that were
    not JSON-parsed (proxy/gateway variants).
    """
    body = obs.get("body")
    if isinstance(body, dict):
        return bool(body) and set(body) == {"detail"} and body.get("detail") == "Not Found"
    raw = str(body or "")
    if not raw:
        return False
    return '"detail": "Not Found"' in raw or "'detail': 'Not Found'" in raw


def _typed_observer_receipts(
    observations: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    raw_many = observations.get("observer_receipts")
    if raw_many is not None:
        if not isinstance(raw_many, list):
            raise ValueError("observer_receipts_not_list")
        candidates.extend(raw_many)
    for key, value in observations.items():
        if key == "observer_receipts":
            continue
        if key == "observer_receipt" or key.endswith("_observer_receipt"):
            if value not in (None, {}):
                candidates.append(value)
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("observer_receipt_not_object")
        validated = validate_observer_receipt(candidate)
        receipt_id = _text(validated.get("receipt_id"))
        previous = by_id.get(receipt_id)
        if previous is not None and previous != validated:
            raise ValueError("observer_receipt_identity_conflict")
        by_id[receipt_id] = validated
    return [by_id[key] for key in sorted(by_id)]


def _body_contains_field(body: Any, field: str) -> bool:
    """True when the named field appears as a key at any depth of body.

    Used by the response_field_absent assertion: the forbidden field may sit
    at the top level (rows carry password) or nested (data.user.password) —
    the source rule forbids it at any depth.
    """
    key = _text(field).lower()
    if not key:
        return False
    if isinstance(body, dict):
        for name, value in body.items():
            if _text(name).lower() == key:
                return True
            if _body_contains_field(value, field):
                return True
    elif isinstance(body, list):
        return any(_body_contains_field(item, field) for item in body)
    return False


_KEY_MATCHER_IDENTIFIER_COMPOUNDS = (
    "idempotency", "correlation", "primary", "foreign", "reference",
    "unique", "dedupe", "dedup", "event", "transaction_ref", "request_ref",
)


def _body_contains_family_field(body: Any, matchers: list[str]) -> list[str]:
    """Field names carrying a secret-family matcher at any depth of body.

    Used by the response_field_absent family match: a rule like 响应不得返回
    支付密钥 names a generic credential concept (密钥/密码/凭据/secret/…)
    rather than one ASCII identifier, so the evaluator scans response field
    names for the canonical matchers (case-insensitive substring). Matchers
    like "key" skip identifier compounds (idempotency_key, correlation_key)
    that legitimately embed the term without being credential material.
    Returns the matched field names as evidence.
    """
    terms = [str(matcher).lower() for matcher in matchers if str(matcher).strip()]
    if not terms:
        return []
    found: list[str] = []
    if isinstance(body, dict):
        for name, value in body.items():
            lowered = _text(name).lower()
            if any(term in lowered for term in terms) and not any(
                compound in lowered for compound in _KEY_MATCHER_IDENTIFIER_COMPOUNDS
            ):
                found.append(_text(name))
            found.extend(_body_contains_family_field(value, matchers))
    elif isinstance(body, list):
        for item in body:
            found.extend(_body_contains_family_field(item, matchers))
    return list(dict.fromkeys(found))


_ROW_CONTAINER_KEYS = (
    "items", "data", "rows", "records", "results", "content", "list",
    "entries",
)


def _response_rows(body: Any) -> list[Any]:
    """Business rows of an observed response body.

    A collection response is the array itself, or a dict whose container key
    (items/data/rows/records/… — generic contract vocabulary) holds the
    array; a dict without a recognizable container falls back to its first
    array value. Non-collection bodies yield no rows (nothing to assert).
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in _ROW_CONTAINER_KEYS:
            value = body.get(key)
            if isinstance(value, list):
                return value
        for value in body.values():
            if isinstance(value, list):
                return value
    return []


def _row_state_violations(body: Any, allowed: set[str]) -> list[dict[str, Any]]:
    """Rows whose state field value lies outside the allowed state set.

    The state field of a row is recognized generically: the first key whose
    normalized name carries status/state, else the key whose value is one of
    the allowed states. Rows with no recognizable state field are skipped —
    missing structure never fabricates a verdict. Values are compared as
    declared (the source contract's enum literal is the assertion target).
    """
    violations: list[dict[str, Any]] = []
    for row in _response_rows(body):
        if not isinstance(row, dict):
            continue
        state_key: str | None = None
        state_value: Any = None
        for key, value in row.items():
            normalized = re.sub(r"[^a-z0-9]+", "", str(key).lower())
            if "status" in normalized or "state" in normalized:
                state_key, state_value = str(key), value
                break
        if state_key is None:
            for key, value in row.items():
                if isinstance(value, str) and value in allowed:
                    state_key, state_value = str(key), value
                    break
        if state_key is None:
            continue
        if str(state_value) not in allowed:
            violations.append({
                "row_state_key": state_key,
                "row_state_value": str(state_value),
            })
    return violations


def _numeric_safe_compare(left: Any, right: Any, operator: str) -> bool | None:
    """Compare two observed values, coercing string numerics.

    Observed amounts arrive as strings ("300.00") on one side and floats on
    the other; a raw <= would raise and poison the verdict. Coerces only
    when BOTH sides parse as numbers — a non-numeric value keeps the caller's
    fail-closed path (None).
    """
    try:
        left_num = float(left)
        right_num = float(right)
    except (TypeError, ValueError):
        return None
    if operator == "gte":
        return left_num >= right_num
    if operator == "lte":
        return left_num <= right_num
    if operator == "eq":
        return left_num == right_num
    if operator == "neq":
        return left_num != right_num
    return None


def _json_path(data: Any, path: str) -> Any:
    """Minimal JSON path: $.a.b[0] style without eval."""

    if not path or path == "$":
        return data
    cur: Any = data
    token = path[1:] if path.startswith("$") else path
    parts: list[str] = []
    buf = ""
    index = 0
    while index < len(token):
        char = token[index]
        if char == ".":
            if buf:
                parts.append(buf)
                buf = ""
            index += 1
            continue
        if char == "[":
            if buf:
                parts.append(buf)
                buf = ""
            end = token.find("]", index)
            if end < 0:
                raise ValueError("invalid_json_path")
            parts.append(token[index : end + 1])
            index = end + 1
            continue
        buf += char
        index += 1
    if buf:
        parts.append(buf)

    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            item_index = int(part[1:-1])
            if not isinstance(cur, list) or item_index >= len(cur):
                raise KeyError(part)
            cur = cur[item_index]
        else:
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(part)
            cur = cur[part]
    return cur


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Evaluate one typed assertion into a content-addressed tri-state receipt."""

    spec = _dict(assertion)
    kind = _text(spec.get("kind") or spec.get("type"))
    assertion_id = _text(spec.get("assertion_id") or spec.get("id"))
    obs = _dict(observations)
    refs = [
        dict(item)
        for item in list(
            source_refs
            if source_refs is not None
            else spec.get("source_refs") or []
        )
        if isinstance(item, dict)
    ]
    expected: Any = spec.get("expected")
    actual: Any = None
    passed: bool | None = None
    reason_code = ""
    error = ""
    harness_error = bool(obs.get("harness_error"))
    resolved_campaign_id = _text(campaign_id or obs.get("campaign_id"))
    resolved_execution_id = _text(execution_id or obs.get("execution_id"))
    if bool(resolved_campaign_id) != bool(resolved_execution_id):
        harness_error = True

    try:
        observer_receipts = _typed_observer_receipts(obs)
    except Exception as exc:
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="OBSERVER_RECEIPT_INVALID",
            expected=expected,
            actual=actual,
            error=f"{type(exc).__name__}: {exc}",
            observer_receipt_ids=[],
            source_refs=refs,
            harness_error=True,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    observer_ids = [
        _text(item.get("receipt_id")) for item in observer_receipts
    ]
    observer_lineages = {
        (
            _text(item.get("campaign_id")),
            _text(item.get("execution_id")),
        )
        for item in observer_receipts
        if _text(item.get("campaign_id"))
        or _text(item.get("execution_id"))
    }
    if not resolved_campaign_id and len(observer_lineages) == 1:
        resolved_campaign_id, resolved_execution_id = next(
            iter(observer_lineages)
        )
    if (
        len(observer_lineages) > 1
        or any(
            not campaign or not execution
            for campaign, execution in observer_lineages
        )
        or any(
            campaign != resolved_campaign_id
            or execution != resolved_execution_id
            for campaign, execution in observer_lineages
        )
    ):
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="OBSERVER_RECEIPT_LINEAGE_MISMATCH",
            expected=expected,
            actual=actual,
            error="observer receipt campaign/execution lineage mismatch",
            observer_receipt_ids=observer_ids,
            source_refs=refs,
            harness_error=True,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    non_observed = [
        item
        for item in observer_receipts
        if _text(item.get("status")).upper() != "OBSERVED"
    ]
    # V1.7 (mirror of the contract-oracle redundancy rule): when the
    # authorization_comparison observer is OBSERVED with leak_detected=True,
    # the authorization violation is already proven by status/effect
    # comparison. Supplementary business_effect/entity_state receipts that
    # could not observe a rejected write are redundant evidence for
    # owner_tenant_visibility-family assertions — they cannot invalidate the
    # proven leak and must not short-circuit the comparison verdict. The
    # leak_detected=True condition keeps clean-system PASS claims fail-closed
    # (a leak-free comparison still requires full supplementary evidence).
    _comparison_rows = [
        item
        for item in observer_receipts
        if _text(item.get("observer_id")) == "authorization_comparison"
        and _text(item.get("status")).upper() == "OBSERVED"
    ]
    if (
        _comparison_rows
        and _dict(_comparison_rows[0].get("evidence")).get("leak_detected") is True
        and KIND_ALIASES.get(kind, kind) == "owner_tenant_visibility"
    ):
        non_observed = [
            item
            for item in non_observed
            if _text(item.get("observer_id")) not in {"business_effect", "entity_state"}
        ]
    if non_observed:
        first = non_observed[0]
        observer_status = _text(first.get("status")).upper()
        # V1.6.1: Field Oracle kinds must still evaluate and emit Trace even when
        # some typed observers remain INDETERMINATE (e.g. typed_assertion lineage
        # observer). Hard-stopping here produced Trace=0 with silent terminal loss.
        _effective_for_trace = KIND_ALIASES.get(kind, kind)
        _field_oracle_kinds = {
            "conservation",
            "field_delta",
            "postcondition",
            "state_transition",
            "cross_entity_consistency",
        }
        # V1.7: Response-status assertion kinds evaluate against the HTTP
        # response directly. Observer INDETERMINATE (e.g. business_effect or
        # entity_state cannot observe a rejected write) is irrelevant to the
        # assertion outcome — the response IS the evidence.
        _response_status_kinds = {
            "http_status_class",
            "validation_rejection",
            "permitted_operation_invocation",
            "authorization_status_comparison",
        }
        if (
            _effective_for_trace not in _field_oracle_kinds
            and kind not in _field_oracle_kinds
            and kind != "forbidden_state_transition"
            and _effective_for_trace not in _response_status_kinds
            and kind not in _response_status_kinds
        ):
            return _assertion_receipt(
                assertion_id=assertion_id,
                kind=kind,
                status="INDETERMINATE",
                reason_code=f"OBSERVER_EVIDENCE_{observer_status}",
                expected=expected,
                actual=actual,
                error=_text(first.get("reason_code")),
                observer_receipt_ids=observer_ids,
                source_refs=refs,
                harness_error=observer_status in {"FAILED", "UNSUPPORTED"},
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
            )
    if harness_error:
        return _assertion_receipt(
            assertion_id=assertion_id,
            kind=kind,
            status="INDETERMINATE",
            reason_code="HARNESS_ERROR_PRESENT",
            expected=expected,
            actual=actual,
            error=_text(obs.get("harness_error")),
            observer_receipt_ids=observer_ids,
            source_refs=refs,
            harness_error=True,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    effective_kind = KIND_ALIASES.get(kind, kind)

    # Runtime-registered assertion kind. Handled before the built-in chain so the
    # chain needs no re-indentation: a registered kind returns its receipt here, and
    # everything below is untouched. See register_assertion_kind for why this is
    # additive rather than a rewrite of the dispatch.
    _registered_evaluator = _REGISTERED_ASSERTION_EVALUATORS.get(effective_kind)
    if _registered_evaluator is not None:
        return _evaluate_registered_assertion_kind(
            _registered_evaluator,
            assertion_id=assertion_id,
            kind=kind,
            effective_kind=effective_kind,
            spec=spec,
            obs=obs,
            observer_receipt_ids=observer_ids,
            source_refs=refs,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )

    try:
        if effective_kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported_assertion_kind:{kind}")
        if effective_kind == "json_path_compare":
            operator = _text(spec.get("operator") or "eq")
            if operator not in {"eq", "neq", "gte", "lte"}:
                raise ValueError(f"unsupported_operator:{operator}")
        if effective_kind == "conservation":
            # Structured expressions bypass operator validation
            if not _dict(spec.get("structured_expression")):
                conservation_operator = _text(
                    _dict(spec.get("equation")).get("operator")
                    or "unchanged_sum"
                )
                if conservation_operator not in {"eq", "unchanged_sum"}:
                    raise ValueError(
                        f"unsupported_conservation_operator:{conservation_operator}"
                    )
        if effective_kind == "idempotency_effect":
            int(spec.get("expected_effect_count", 1))

        if effective_kind == "http_status":
            expected = spec.get("expected", spec.get("expected_status"))
            if "status_code" not in obs or expected is None:
                reason_code = "HTTP_STATUS_EVIDENCE_MISSING"
            else:
                actual = obs["status_code"]
                passed = actual == expected
        elif effective_kind == "http_status_class":
            expected_value = spec.get(
                "expected",
                spec.get("expected_class"),
            )
            if "status_code" not in obs or expected_value is None:
                reason_code = "HTTP_STATUS_CLASS_EVIDENCE_MISSING"
            else:
                actual = int(obs["status_code"])
                expected = int(expected_value)
                passed = (actual // 100) == expected
                # A 422 (input validation rejection) on a success-expected
                # authorization/validation experiment is the harness's own
                # request being invalid — it proves neither permission nor
                # denial. 401/403 are the authorization signals; 422 is an
                # input-contract gap and must stay INDETERMINATE instead of
                # being reported as an authorization defect.
                if (
                    passed is False
                    and expected == 2
                    and actual == 422
                    and _text(spec.get("authorization_semantics") or spec.get("_authz_semantics"))
                    in {"", "authorization", "permitted_invocation"}
                ):
                    passed = None
                    reason_code = "HTTP_INPUT_REJECTED_INDETERMINATE"
                # A framework-level 404 — the deployed target has no route for
                # this path (Starlette/FastAPI generic {"detail": "Not Found"},
                # or an HTML/"Cannot METHOD" default) — proves nothing about
                # the target's business behavior: the request never reached a
                # handler. Cross-service routing artifacts and interface-drift
                # observations must stay INDETERMINATE, never authorization /
                # validation defects.
                if (
                    passed is False
                    and expected == 2
                    and actual == 404
                    and _framework_generic_404_body(obs)
                ):
                    passed = None
                    reason_code = "HTTP_ROUTE_NOT_FOUND_INDETERMINATE"
                # Soft business reject on an accepted HTTP class must not pass a
                # success-class assertion when the body declares failure.
                if (
                    passed
                    and expected == 2
                    and (
                        obs.get("business_rejected") is True
                        or _dict(obs.get("business_outcome")).get("business_rejected")
                        is True
                    )
                ):
                    passed = False
                    reason_code = "HTTP_SOFT_BUSINESS_REJECTED"
                if (
                    passed
                    and expected == 2
                    and obs.get("zero_effect_on_accepted_write") is True
                    and spec.get("require_nonzero_effect") is True
                ):
                    passed = False
                    reason_code = "HTTP_ACCEPTED_ZERO_EFFECT"
        elif effective_kind == "json_path_exists":
            expected = True
            if "body" not in obs:
                reason_code = "HTTP_BODY_EVIDENCE_MISSING"
            else:
                path = _text(spec.get("path") or "$")
                try:
                    actual = _json_path(obs["body"], path)
                    passed = actual is not None
                except (KeyError, IndexError, TypeError):
                    actual = None
                    passed = False
        elif effective_kind == "response_field_absent":
            # Source rule: the response must NOT carry the named field(s)
            # (导出结果禁止包含 password). The field is forbidden at any
            # depth of the response body. expected=True means "absent".
            # family_match=True turns the fields into credential-family
            # matchers scanned as substrings of field names (响应不得返回
            # 支付密钥 → secret/key).
            expected = True
            if "body" not in obs:
                reason_code = "HTTP_BODY_EVIDENCE_MISSING"
            else:
                fields = [
                    _text(value)
                    for value in _list(spec.get("fields"))
                    if _text(value)
                ] or ([_text(spec.get("field"))] if _text(spec.get("field")) else [])
                if not fields:
                    reason_code = "RESPONSE_FIELD_ABSENT_FIELD_MISSING"
                elif spec.get("family_match"):
                    found = _body_contains_family_field(obs["body"], fields)
                    actual = found
                    passed = not found
                else:
                    found = [
                        field
                        for field in fields
                        if _body_contains_field(obs["body"], field)
                    ]
                    actual = found
                    passed = not found
        elif effective_kind == "json_path_type":
            expected_type = _text(
                spec.get("expected")
                or spec.get("expected_type")
                or spec.get("type_name")
            ).lower()
            type_map = {
                "string": str,
                "str": str,
                "number": (int, float),
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool,
                "object": dict,
                "dict": dict,
                "array": list,
                "list": list,
                "null": type(None),
            }
            py_type = type_map.get(expected_type)
            if py_type is None:
                raise ValueError(
                    f"unsupported_json_path_type:{expected_type}"
                )
            expected = expected_type
            if "body" not in obs:
                reason_code = "HTTP_BODY_EVIDENCE_MISSING"
            else:
                try:
                    value = _json_path(
                        obs["body"],
                        _text(spec.get("path") or "$"),
                    )
                    actual = type(value).__name__
                    passed = isinstance(value, py_type)
                except (KeyError, IndexError, TypeError):
                    actual = None
                    passed = False
        elif effective_kind == "json_path_compare":
            if "body" not in obs or (
                "expected" not in spec and "expected_path" not in spec
            ):
                reason_code = "JSON_COMPARE_EVIDENCE_MISSING"
            else:
                try:
                    actual = _json_path(
                        obs["body"],
                        _text(spec.get("path") or "$"),
                    )
                except (KeyError, IndexError, TypeError):
                    actual = None
                    passed = False
                if passed is None:
                    # expected_path compares two paths from the SAME observed
                    # body (discountAmount ≤ coupon.max_discount echoed by the
                    # target itself) — the expected value is the target's own
                    # observed contract data, never a synthesized literal.
                    if "expected_path" in spec:
                        try:
                            expected = _json_path(
                                obs["body"],
                                _text(spec.get("expected_path")),
                            )
                        except (KeyError, IndexError, TypeError):
                            expected = None
                    if expected is None:
                        passed = False
                        reason_code = "JSON_COMPARE_EXPECTED_PATH_MISSING"
                    elif operator == "eq":
                        passed = actual == expected
                    elif operator == "neq":
                        passed = actual != expected
                    elif operator == "gte":
                        passed = _numeric_safe_compare(actual, expected, "gte")
                    elif operator == "lte":
                        passed = _numeric_safe_compare(actual, expected, "lte")
                    elif passed is None:
                        reason_code = "JSON_COMPARE_OPERATOR_UNSUPPORTED"
        elif effective_kind == "equality":
            if "value" not in obs or "expected" not in spec:
                reason_code = "EQUALITY_EVIDENCE_MISSING"
            else:
                actual = obs["value"]
                passed = actual == expected
        elif effective_kind == "delta":
            if (
                "before" not in obs
                or "after" not in obs
                or "expected" not in spec
            ):
                reason_code = "DELTA_EVIDENCE_MISSING"
            else:
                actual = obs["after"] - obs["before"]
                passed = actual == expected
        elif effective_kind == "cardinality":
            if "collection" not in obs or "expected" not in spec:
                reason_code = "CARDINALITY_EVIDENCE_MISSING"
            else:
                collection = obs["collection"]
                actual = (
                    len(collection)
                    if isinstance(collection, list)
                    else {"observed_type": type(collection).__name__}
                )
                passed = actual == expected
        elif effective_kind == "state_transition":
            if (
                "before_state" not in obs
                or "after_state" not in obs
                or "from_state" not in spec
                or "to_state" not in spec
            ):
                # V1.6.1: lift from/to from expression operands when top-level absent.
                _st_ops = _list(spec.get("operands"))
                _st0 = _st_ops[0] if _st_ops and isinstance(_st_ops[0], dict) else {}
                if "from_state" not in spec and _text(_st0.get("from_state")):
                    spec = {**spec, "from_state": _text(_st0.get("from_state"))}
                if "to_state" not in spec and _text(_st0.get("to_state")):
                    spec = {**spec, "to_state": _text(_st0.get("to_state"))}
            if (
                "before_state" not in obs
                or "after_state" not in obs
                or not _text(spec.get("from_state"))
                or not _text(spec.get("to_state"))
            ):
                reason_code = "STATE_TRANSITION_EVIDENCE_MISSING"
            elif _text(spec.get("from_state")).lower() in {"", "unknown_state", "unknown"}:
                # V1.6.0: unknown_state is not a business transition contract.
                # Refuse fingerprint any-change — that invented PASS/VIOLATION.
                reason_code = "STATE_RULE_PRECONDITION_NOT_ESTABLISHED"
            else:
                actual = {
                    "before": obs["before_state"],
                    "after": obs["after_state"],
                }
                expected = {
                    "before": spec["from_state"],
                    "after": spec["to_state"],
                }
                _forbidden = _text(spec.get("operator")).lower() in {
                    "must_not_transition",
                    "forbidden",
                    "must_not",
                } or _text(spec.get("kind")).lower() == "forbidden_state_transition"
                if _state_token(obs["before_state"]) != _state_token(
                    spec["from_state"]
                ):
                    # A wrong source state means the experiment precondition was
                    # not established. It is not product-defect evidence.
                    reason_code = "STATE_PRECONDITION_NOT_MET"
                elif _forbidden:
                    # Forbidden transition: VIOLATION only when after reaches to_state.
                    reached = _state_token(obs["after_state"]) == _state_token(
                        spec["to_state"]
                    )
                    passed = not reached
                    if reached:
                        reason_code = "FORBIDDEN_STATE_TRANSITION"
                else:
                    # Allowed transition edges declare what MAY happen, not what
                    # MUST. A state-machine edge bound to an operation that does
                    # not perform that step (idempotent re-cancel, unrelated
                    # action rejected, or an operation with no effect on this
                    # entity) leaves the state unchanged — that is a valid
                    # observation, not a violation. Only a change to a state
                    # outside the declared edge is defect evidence.
                    _after_token = _state_token(obs["after_state"])
                    if _after_token == _state_token(obs["before_state"]):
                        passed = True
                    else:
                        passed = _after_token == _state_token(spec["to_state"])
        elif effective_kind == "postcondition":
            # Postcondition assertions verify that a causal rule's expected
            # effect actually materialized after the trigger action executed.
            # Uses entity_state observer evidence (state_change_count, effect_count).
            # must_become: state must have changed (fingerprint difference)
            # must_create: a new entity must appear (identity count increase)
            pc_operator = _text(spec.get("operator"))
            pc_operands = spec.get("operands") or []
            pc_operand = pc_operands[0] if pc_operands and isinstance(pc_operands[0], dict) else {}
            entity_ref = _text(pc_operand.get("entity_ref"))
            field_ref = _text(pc_operand.get("field_id") or pc_operand.get("field"))
            expected_value = pc_operand.get("expected_value")
            must_create = bool(pc_operand.get("must_create"))
            # Gather entity_state evidence from observations
            state_change_count = obs.get("state_change_count")
            effect_count = obs.get("effect_count")
            entity_state_observed = obs.get("entity_state_observed")
            state_windows = obs.get("state_windows") or []
            if entity_state_observed is not True and state_change_count is None:
                reason_code = "POSTCONDITION_ENTITY_STATE_EVIDENCE_MISSING"
            elif must_create:
                # must_create: verify new entity appeared (identity count increase or effect > 0)
                expected = {"entity": entity_ref, "must_create": True}
                identity_increase = any(
                    isinstance(w, dict) and int(w.get("after_identity_count") or 0) > int(w.get("before_identity_count") or 0)
                    for w in state_windows
                )
                actual = {
                    "state_change_count": state_change_count,
                    "effect_count": effect_count,
                    "identity_increase": identity_increase,
                }
                passed = identity_increase or int(effect_count or 0) > 0
                if not passed:
                    reason_code = "POSTCONDITION_ENTITY_NOT_CREATED"
            elif not field_ref and expected_value is None:
                # V1.6.0: refuse "any change" postcondition without bound fields.
                reason_code = "FIELD_LEVEL_RULE_NOT_EXECUTABLE"
                expected = {"entity": entity_ref, "field": field_ref}
                actual = {
                    "state_change_count": state_change_count,
                    "effect_count": effect_count,
                    "detail": "postcondition_requires_bound_field_or_expected_value",
                }
            else:
                # must_become: verify state changed to expected value
                expected = {"entity": entity_ref, "field": field_ref, "must_become": expected_value}
                actual = {
                    "state_change_count": state_change_count,
                    "effect_count": effect_count,
                }
                # ── Phase 2: value-level postcondition verification ──
                # When expected_value and field_ref are declared and observer
                # evidence provides after_values, verify the concrete field value
                # instead of merely checking that *something* changed.
                _pc_value_verified = False
                if expected_value is not None and field_ref:
                    _after_vals = obs.get("after_values")
                    if isinstance(_after_vals, dict) and _after_vals:
                        # Normalize field_ref for lookup (case-insensitive)
                        _norm_field = _state_token(field_ref)
                        _matched_val = None
                        for _ak, _av in _after_vals.items():
                            if _state_token(_ak) == _norm_field:
                                _matched_val = _av
                                break
                        if _matched_val is not None:
                            actual["observed_field_value"] = _matched_val
                            if _state_token(_matched_val) == _state_token(expected_value):
                                passed = True
                                _pc_value_verified = True
                            else:
                                passed = False
                                reason_code = "POSTCONDITION_VALUE_MISMATCH"
                                _pc_value_verified = True
                if not _pc_value_verified:
                    # Field declared but no typed after_values — fail closed.
                    # Do not treat fingerprint/effect_count as field oracle evidence.
                    reason_code = "POSTCONDITION_FIELD_EVIDENCE_MISSING"
                    passed = False
        elif effective_kind == "field_delta":
            # ── P0-5: field-level causal delta verification ──
            # Compares before/after values for specific fields and verifies
            # the observed delta matches the expected delta from the rule.
            _fd_fields = _list(spec.get("fields") or spec.get("operands"))
            _fd_before = obs.get("before_values")
            _fd_after = obs.get("after_values")
            if not isinstance(_fd_before, dict) or not isinstance(_fd_after, dict):
                reason_code = "FIELD_DELTA_EVIDENCE_MISSING"
            elif not _fd_fields:
                reason_code = "FIELD_DELTA_NO_FIELDS_SPECIFIED"
            else:
                _fd_results: list[dict[str, Any]] = []
                _fd_all_passed = True
                for _fd_spec in _fd_fields:
                    if not isinstance(_fd_spec, dict):
                        continue
                    _fd_field = _text(_fd_spec.get("field_id") or _fd_spec.get("field"))
                    if not _fd_field:
                        continue
                    # Case-insensitive field lookup
                    _fd_b_val = None
                    _fd_a_val = None
                    for _bk, _bv in _fd_before.items():
                        if _state_token(_bk) == _state_token(_fd_field):
                            _fd_b_val = _bv
                            break
                    for _ak, _av in _fd_after.items():
                        if _state_token(_ak) == _state_token(_fd_field):
                            _fd_a_val = _av
                            break
                    if _fd_b_val is None or _fd_a_val is None:
                        _fd_results.append({"field": _fd_field, "result": "MISSING"})
                        _fd_all_passed = False
                        continue
                    _fd_expected_delta = _fd_spec.get("expected_delta")
                    _fd_expected_dir = _text(_fd_spec.get("expected_delta_direction"))
                    _fd_expected_value = _fd_spec.get("expected_value")
                    try:
                        _fd_actual_delta = float(_fd_a_val) - float(_fd_b_val)
                    except (TypeError, ValueError):
                        # Non-numeric field: check value equality
                        if _fd_expected_value is not None:
                            _fd_match = _state_token(_fd_a_val) == _state_token(_fd_expected_value)
                            _fd_results.append({
                                "field": _fd_field,
                                "before": _fd_b_val,
                                "after": _fd_a_val,
                                "expected_value": _fd_expected_value,
                                "result": "PASS" if _fd_match else "FAIL",
                            })
                            if not _fd_match:
                                _fd_all_passed = False
                        else:
                            _fd_results.append({"field": _fd_field, "result": "NON_NUMERIC"})
                        continue
                    _fd_field_passed = True
                    if _fd_expected_delta is not None:
                        _fd_field_passed = abs(_fd_actual_delta - float(_fd_expected_delta)) < 0.001
                    elif _fd_expected_dir:
                        if _fd_expected_dir == "increase":
                            _fd_field_passed = _fd_actual_delta > 0
                        elif _fd_expected_dir == "decrease":
                            _fd_field_passed = _fd_actual_delta < 0
                        elif _fd_expected_dir == "unchanged":
                            _fd_field_passed = _fd_actual_delta == 0
                    _fd_results.append({
                        "field": _fd_field,
                        "before": _fd_b_val,
                        "after": _fd_a_val,
                        "actual_delta": _fd_actual_delta,
                        "expected_delta": _fd_expected_delta,
                        "expected_direction": _fd_expected_dir or None,
                        "result": "PASS" if _fd_field_passed else "FAIL",
                    })
                    if not _fd_field_passed:
                        _fd_all_passed = False
                expected = {"fields": _fd_fields}
                actual = {"field_results": _fd_results}
                passed = _fd_all_passed and bool(_fd_results)
                if not passed and not reason_code:
                    reason_code = "FIELD_DELTA_MISMATCH"
        elif effective_kind == "owner_tenant_visibility":
            required_values = (
                obs.get("owner_can_access"),
                obs.get("viewer_can_access"),
                obs.get("leak_detected"),
            )
            expected = {
                "owner_can_access": True,
                "viewer_can_access": False,
                "leak_detected": False,
            }
            if obs.get("control_succeeded") is not True:
                reason_code = "AUTHORIZED_CONTROL_NOT_PROVEN"
            elif not all(
                isinstance(value, bool) for value in required_values
            ):
                reason_code = "AUTHORIZATION_OBSERVATION_MISSING"
            elif (
                spec.get("require_same_resource", True)
                and obs.get("same_resource_proven") is not True
            ):
                reason_code = "SAME_RESOURCE_NOT_PROVEN"
            else:
                actual = {
                    "owner_can_access": obs["owner_can_access"],
                    "viewer_can_access": obs["viewer_can_access"],
                    "leak_detected": obs["leak_detected"],
                }
                passed = actual == expected
        elif effective_kind == "conservation":
            # ── Structured expression evaluation (multi-entity / aggregate) ──
            if _dict(spec.get("structured_expression")):
                _se_rc, _se_expected, _se_actual = _evaluate_structured_expression(spec, obs)
                expected = _se_expected
                actual = _se_actual
                if _se_rc and "VIOLATED" in _se_rc:
                    # Violation detected - test failure
                    passed = False
                    reason_code = _se_rc
                elif _se_rc:
                    # Missing evidence - indeterminate
                    reason_code = _se_rc
                else:
                    # Determine pass/fail from the result
                    if isinstance(_se_actual, dict):
                        # Check for violations in actual
                        _violations = _se_actual.get("violations")
                        if isinstance(_violations, list) and _violations:
                            passed = False
                        elif "left" in _se_actual and "right" in _se_actual:
                            # Numeric comparison already done in helper
                            _left_d = _to_decimal(_se_actual.get("left"))
                            _right_d = _to_decimal(_se_actual.get("right"))
                            _op = _text(_dict(spec.get("structured_expression")).get("operator") or spec.get("operator"))
                            if _left_d is not None and _right_d is not None:
                                passed = _compare_decimals(_left_d, _right_d, _op) if _op else _left_d == _right_d
                            else:
                                passed = _se_actual.get("left") == _se_actual.get("right")
                        elif "before" in _se_actual and "after" in _se_actual:
                            passed = _se_actual.get("before") == _se_actual.get("after")
                        elif "condition_met" in _se_actual:
                            passed = not _se_actual.get("violations")
                        elif "delta" in _se_actual:
                            passed = _se_actual.get("delta") != "0"
                        else:
                            passed = True
                    else:
                        passed = True
            elif _text(spec.get("compile_diagnostic")):
                # ── P0-10: propagate compile-time diagnostic as reason code ──
                reason_code = _text(spec.get("compile_diagnostic"))
                expected = {"compile_diagnostic": reason_code}
            else:
                equation = _dict(spec.get("equation"))
                operator = _text(
                    equation.get("operator")
                    or "unchanged_sum"
                )
                terms = [
                    _text(item)
                    for item in _list(
                        equation.get("terms")
                        or equation.get("fields")
                    )
                    if _text(item)
                ]
                before_values = obs.get("before_values")
                after_values = obs.get("after_values")
                expected = {"operator": operator, "terms": terms}
                if (
                    not isinstance(before_values, dict)
                    or not before_values
                    or not isinstance(after_values, dict)
                    or not after_values
                ):
                    reason_code = "CONSERVATION_VALUES_MISSING"
                elif operator == "eq":
                    actual = {
                        "before": before_values,
                        "after": after_values,
                    }
                    passed = before_values == after_values
                elif operator == "unchanged_sum":
                    # ── Field grounding: empty terms is a compilation failure ──
                    # Never fall back to generic sum of all numeric fields.
                    if not terms:
                        reason_code = "BLOCKED_EMPTY_CONSERVATION_TERMS"
                    elif any(
                        term not in before_values
                        or term not in after_values
                        or isinstance(before_values[term], bool)
                        or isinstance(after_values[term], bool)
                        or not isinstance(
                            before_values[term],
                            (int, float),
                        )
                        or not isinstance(
                            after_values[term],
                            (int, float),
                        )
                        for term in terms
                    ):
                        reason_code = "CONSERVATION_VALUES_MISSING"
                    else:
                        before_sum = sum(
                            float(before_values[term])
                            for term in terms
                        )
                        after_sum = sum(
                            float(after_values[term])
                            for term in terms
                        )
                        actual = {
                            "before_sum": before_sum,
                            "after_sum": after_sum,
                            "before": before_values,
                            "after": after_values,
                        }
                        passed = before_sum == after_sum
                else:
                    raise ValueError(
                        f"unsupported_conservation_operator:{operator}"
                    )
        elif effective_kind == "non_negative":
            # Field boundary: after the write every declared term must be
            # >= 0. Uses the entity_state observer's after_values, the same
            # evidence channel as conservation — never a separate observer.
            equation = _dict(spec.get("equation"))
            terms = [
                _text(item)
                for item in _list(
                    equation.get("terms")
                    or equation.get("fields")
                )
                if _text(item)
            ]
            after_values = obs.get("after_values")
            expected = {"operator": "non_negative", "terms": terms}
            if (
                not isinstance(after_values, dict)
                or not after_values
                or not terms
            ):
                reason_code = "NON_NEGATIVE_VALUES_MISSING"
            elif any(
                term not in after_values
                or isinstance(after_values[term], bool)
                or not isinstance(after_values[term], (int, float))
                for term in terms
            ):
                reason_code = "NON_NEGATIVE_VALUES_MISSING"
            else:
                actual = {"after": after_values}
                passed = all(
                    float(after_values[term]) >= 0 for term in terms
                )
        elif effective_kind == "response_rows_state_filter":
            # Read-side row-state constraint: the response rows a caller may
            # see are limited to the declared state set (业务约束：用户端默认
            # 仅返回 ON_SALE 商品). Every row of the observed body must carry
            # a state field whose value is inside the declared set; any row
            # outside it is defect evidence. Rows without a recognizable
            # state field are not asserted — a missing structure never
            # fabricates a verdict.
            expected = True
            if "body" not in obs:
                reason_code = "HTTP_BODY_EVIDENCE_MISSING"
            else:
                allowed = {
                    _text(value)
                    for value in _list(spec.get("allowed_states"))
                    if _text(value)
                }
                if not allowed:
                    reason_code = "ROW_STATE_FILTER_ALLOWED_STATES_MISSING"
                else:
                    violations = _row_state_violations(obs["body"], allowed)
                    actual = violations
                    passed = not violations
                    if violations:
                        reason_code = "RESPONSE_ROW_STATE_OUTSIDE_ALLOWED"
        elif effective_kind == "ui_state_consistency":
            # UI/UX page-state rule: the rendered page (ui_browser body text)
            # must not carry state vocabulary outside the rule's declared
            # allowed set (Only ON_SALE products may be rendered → DELETED/
            # DRAFT rows on the page are defect evidence). The state tokens,
            # the allowed set and the forbidden set come from the rule's own
            # text — never inferred. Without any allowed or forbidden
            # declaration the assertion stays INDETERMINATE (no fabricated
            # verdicts from unknown states).
            expected = True
            if "body_text" not in obs:
                reason_code = "UI_DOM_EVIDENCE_MISSING"
            else:
                page_text = str(obs.get("body_text") or "")
                states = [
                    _text(value)
                    for value in _list(spec.get("states"))
                    if _text(value)
                ]
                allowed = {
                    _text(value)
                    for value in _list(spec.get("allowed_states"))
                    if _text(value)
                }
                forbidden = {
                    _text(value)
                    for value in _list(spec.get("forbidden_states"))
                    if _text(value)
                }
                surface_checks = [
                    dict(row)
                    for row in _list(spec.get("surface_checks"))
                    if isinstance(row, dict) and row
                ]
                # ── Surface-declared DOM assertions ──
                # The UI surface declaration chain compiles visible UI
                # material into read-only browser-plan expectations
                # (surface_checks): expect_visible / expect_enabled /
                # expect_text require the document's own control vocabulary
                # to be present on the rendered page; expect_hidden /
                # expect_disabled require it to be absent. The locator intent
                # carries the document's declared text — judged word-boundary
                # against the rendered body, never inferred.
                check_violations: list[dict[str, Any]] = []
                for check in surface_checks:
                    action = _text(check.get("action")).lower()
                    intent = _dict(check.get("locator_intent"))
                    declared_text = _text(
                        check.get("text")
                        or intent.get("text")
                        or intent.get("name")
                    )
                    if not declared_text:
                        continue
                    present = bool(
                        re.search(
                            r"\b" + re.escape(declared_text) + r"\b",
                            page_text,
                        )
                    )
                    if action in {"expect_hidden", "expect_disabled"}:
                        if present:
                            check_violations.append({
                                "control": declared_text,
                                "action": action,
                                "expected": "absent",
                                "actual": "present",
                            })
                    elif action in {
                        "expect_visible",
                        "expect_enabled",
                        "expect_text",
                    }:
                        if not present:
                            check_violations.append({
                                "control": declared_text,
                                "action": action,
                                "expected": "present",
                                "actual": "absent",
                            })
                if check_violations:
                    actual = check_violations
                    passed = False
                    reason_code = "UI_SURFACE_CHECK_VIOLATED"
                else:
                    if not allowed and not forbidden and not surface_checks:
                        reason_code = "UI_ALLOWED_STATES_MISSING"
                    elif not allowed and not forbidden:
                        # Only surface checks were declared and they all held.
                        passed = True
                    else:
                        # Verdict candidates: every state the page may NOT
                        # carry — states outside the allowed set, plus
                        # source-declared forbidden states. A plain statement
                        # (state becomes CANCELLED) declares neither, so it
                        # cannot fabricate a verdict. Word-boundary matching
                        # only; a state token inside another word is not
                        # evidence.
                        candidates = sorted(
                            (set(states) - allowed) | (forbidden - allowed)
                        )
                        if not candidates:
                            reason_code = "UI_STATES_MISSING"
                        else:
                            found = [
                                state
                                for state in candidates
                                if re.search(
                                    r"\b" + re.escape(state) + r"\b",
                                    page_text,
                                )
                            ]
                            actual = found
                            passed = not found
                            if found:
                                reason_code = "UI_PAGE_STATE_OUTSIDE_ALLOWED"
        elif effective_kind == "idempotency_effect":
            expected_count = spec.get("expected_effect_count", 1)
            expected = {"effect_count": expected_count}
            actual = {
                "effect_count": obs.get("effect_count"),
                "http_statuses": obs.get("http_statuses"),
            }
            if obs.get("effect_count") is None:
                reason_code = "BUSINESS_EFFECT_MISSING"
            else:
                passed = int(obs["effect_count"]) == int(expected_count)
        elif effective_kind == "response_consistency":
            # Decision-surface idempotency (/check, /resolve, /validate…):
            # the operation's response IS its effect, so the property is that
            # the identical replayed input yields the same decision. Verdict
            # is conservative: only two ACCEPTED calls are compared — a
            # rejected replay (quota/guard) is a valid idempotency control
            # and stays INDETERMINATE, never a violation; missing evidence
            # stays INDETERMINATE.
            control_step = _dict(obs.get("control_observation"))
            treatment_step = _dict(obs.get("treatment_observation"))
            expected = {
                "control_status_class": "2xx",
                "treatment_status_class": "2xx",
                "bodies_equal": True,
            }
            actual = {
                "control_status": control_step.get("status_code"),
                "treatment_status": treatment_step.get("status_code"),
            }
            ctl_status = control_step.get("status_code")
            trt_status = treatment_step.get("status_code")
            try:
                ctl_status_i = int(ctl_status) if ctl_status is not None else 0
                trt_status_i = int(trt_status) if trt_status is not None else 0
            except (TypeError, ValueError):
                reason_code = "RESPONSE_CONSISTENCY_STATUS_INVALID"
            else:
                if ctl_status_i <= 0 or trt_status_i <= 0:
                    reason_code = "RESPONSE_CONSISTENCY_EVIDENCE_MISSING"
                elif not (200 <= ctl_status_i < 300 and 200 <= trt_status_i < 300):
                    # A rejected replay is an enforced idempotency guard; the
                    # decision surface has no verdict to compare.
                    reason_code = "RESPONSE_REPLAY_REJECTED_INDETERMINATE"
                else:
                    control_body = control_step.get("body")
                    treatment_body = treatment_step.get("body")
                    if not isinstance(control_body, (dict, list)) or not isinstance(treatment_body, (dict, list)):
                        reason_code = "RESPONSE_CONSISTENCY_BODY_UNSUPPORTED"
                    else:
                        actual["bodies_equal"] = control_body == treatment_body
                        if control_body != treatment_body:
                            passed = False
                            reason_code = "RESPONSE_BODY_DIVERGED"
                        else:
                            passed = True
        elif effective_kind == "concurrency_final_invariant":
            expected = {"invariant_held": True}
            actual = {
                "final_state": obs.get("final_state"),
                "invariant_held": obs.get("invariant_held"),
                "dual_2xx": obs.get("dual_2xx"),
            }
            # Surface observed entity numerics (e.g. available_qty) when the
            # final_state observer captured them — fingerprint-only actuals hide
            # the concrete invariant breach from delivery/scoring blobs.
            before_values = obs.get("before_values")
            after_values = obs.get("after_values")
            if isinstance(before_values, dict) and before_values:
                actual["before_values"] = dict(before_values)
            if isinstance(after_values, dict) and after_values:
                actual["after_values"] = dict(after_values)
            if not isinstance(obs.get("invariant_held"), bool):
                # The barrier protocol released control and treatment and the
                # final state was observed, but no producer wrote invariant_held.
                # Compute it from the source-declared expression over the observed
                # after-values when the source declared a structured comparison or
                # a non-negative field equation (不能为负 rules); otherwise stay
                # INDETERMINATE (never guess a verdict).
                _prop = _dict(spec.get("property"))
                _expr = (
                    _dict(spec.get("expression"))
                    or _dict(_prop.get("expression"))
                    or _dict(
                        _dict(_prop.get("field_rule_binding")).get("typed_expression")
                    )
                )
                _computed, _compute_reason = _compute_invariant_held_from_source(
                    _expr, obs
                )
                if (
                    not isinstance(_computed, bool)
                    and _compute_reason == "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
                ):
                    # No structured comparison declared: the non-negative field
                    # equation is the only other source-declared boundary shape.
                    _computed, _compute_reason = _non_negative_boundary_held(
                        _dict(_expr.get("equation")), obs
                    )
                if isinstance(_computed, bool):
                    obs["invariant_held"] = _computed
                    actual["invariant_held"] = _computed
                    actual["invariant_held_basis"] = _compute_reason
                    passed = _computed
                    reason_code = ""
                else:
                    reason_code = "FINAL_INVARIANT_MISSING"
                    actual["invariant_held_missing_reason"] = _compute_reason
            else:
                passed = obs["invariant_held"] is True
        elif effective_kind == "concurrent_double_write":
            # Same-experiment concurrent double-write verdict.
            #
            # Evidence gates are fail-closed: without BOTH arms' status codes the
            # pair never happened (INDETERMINATE), and without a released barrier
            # timeline with two participants the writes were not proven to overlap
            # (INDETERMINATE) — a sequential pair is not a concurrency test and
            # must never read as one.
            #
            # Verdict semantics (dual 2xx alone is never a verdict):
            #   * at least one arm rejected → the target serialized or refused the
            #     second write, the race window produced no double acceptance →
            #     PASS, basis CONCURRENT_PAIR_NOT_BOTH_ACCEPTED;
            #   * both arms accepted → the race window existed; the source-
            #     declared boundary decides: held → PASS (conserved), broken →
            #     VIOLATION (oversell / double side effect under concurrency);
            #   * no source boundary and no oversell projection → INDETERMINATE.
            expected = {"dual_2xx": True, "invariant_held": True}
            control_statuses = _dual_write_statuses(obs, "control")
            treatment_statuses = _dual_write_statuses(obs, "treatment")
            actual: dict[str, Any] = {
                "control_statuses": control_statuses,
                "treatment_statuses": treatment_statuses,
                "dual_2xx": obs.get("dual_2xx"),
            }
            if not control_statuses or not treatment_statuses:
                reason_code = "CONCURRENT_DUAL_WRITE_EVIDENCE_MISSING"
            elif obs.get("barrier_released") is not True or int(
                obs.get("participant_count") or 0
            ) < 2:
                reason_code = "CONCURRENT_RELEASE_EVIDENCE_MISSING"
            else:
                dual = (
                    all(200 <= status < 300 for status in control_statuses)
                    and all(200 <= status < 300 for status in treatment_statuses)
                )
                actual["dual_2xx"] = dual
                if not dual:
                    passed = True
                    actual["invariant_held"] = True
                    actual["invariant_held_basis"] = (
                        "CONCURRENT_PAIR_NOT_BOTH_ACCEPTED"
                    )
                else:
                    _held, _basis = _concurrent_boundary_held(spec, obs)
                    if isinstance(_held, bool):
                        obs["invariant_held"] = _held
                        actual["invariant_held"] = _held
                        actual["invariant_held_basis"] = _basis
                        passed = _held
                        if not _held:
                            reason_code = "CONCURRENT_BOUNDARY_VIOLATED"
                    elif _decision_pair_consistency(obs) is not None:
                        # Decision surfaces (/check, /resolve, /validate…): no
                        # entity boundary exists — the property under the race
                        # is that both accepted calls return the same decision.
                        # Divergent decisions under a proven concurrent release
                        # is itself the race defect; equal decisions pass.
                        _consistent = _decision_pair_consistency(obs)
                        obs["invariant_held"] = _consistent
                        actual["invariant_held"] = _consistent
                        actual["invariant_held_basis"] = (
                            "DECISION_RESPONSE_CONSISTENCY"
                        )
                        passed = _consistent
                        if not _consistent:
                            reason_code = "CONCURRENT_DECISION_DIVERGED"
                    else:
                        reason_code = "FINAL_INVARIANT_MISSING"
                        actual["invariant_held_missing_reason"] = _basis
            before_values = obs.get("before_values")
            after_values = obs.get("after_values")
            if isinstance(before_values, dict) and before_values:
                actual["before_values"] = dict(before_values)
            if isinstance(after_values, dict) and after_values:
                actual["after_values"] = dict(after_values)
        elif effective_kind == "eventual_consistency":
            expected = {
                "converged": True,
                "within_window": True,
            }
            if not isinstance(
                obs.get("converged"),
                bool,
            ) or not isinstance(
                obs.get("within_window"),
                bool,
            ):
                reason_code = "EVENTUAL_CONSISTENCY_EVIDENCE_MISSING"
            else:
                actual = {
                    "converged": obs["converged"],
                    "within_window": obs["within_window"],
                }
                passed = actual == expected
        elif effective_kind == "cross_surface_consistency":
            expected = True
            if not isinstance(obs.get("surfaces_agree"), bool):
                reason_code = "CROSS_SURFACE_EVIDENCE_MISSING"
            else:
                actual = obs["surfaces_agree"]
                passed = actual is True
        elif effective_kind == "cross_entity_consistency":
            # ── Cross-entity state consistency via structured expression ──
            _ce_rc, _ce_expected, _ce_actual = _evaluate_structured_expression(spec, obs)
            expected = _ce_expected
            actual = _ce_actual
            if _ce_rc and "VIOLATED" in _ce_rc:
                # Violation detected - this is a test failure, not indeterminate
                passed = False
                reason_code = _ce_rc
            elif _ce_rc:
                # Missing evidence - indeterminate
                reason_code = _ce_rc
            else:
                if isinstance(_ce_actual, dict) and _ce_actual.get("violations"):
                    passed = False
                else:
                    passed = True
        elif effective_kind == "limit_constraint":
            # ── Aggregate limit constraint via structured expression ──
            _lc_rc, _lc_expected, _lc_actual = _evaluate_structured_expression(spec, obs)
            expected = _lc_expected
            actual = _lc_actual
            if _lc_rc and "VIOLATED" in _lc_rc:
                # Violation detected - test failure
                passed = False
                reason_code = _lc_rc
            elif _lc_rc:
                # Missing evidence - indeterminate
                reason_code = _lc_rc
            else:
                if isinstance(_lc_actual, dict) and "left" in _lc_actual and "right" in _lc_actual:
                    _lc_left = _to_decimal(_lc_actual["left"])
                    _lc_right = _to_decimal(_lc_actual["right"])
                    _lc_op = _text(_dict(spec.get("structured_expression")).get("operator") or spec.get("operator"))
                    if _lc_left is not None and _lc_right is not None:
                        passed = _compare_decimals(_lc_left, _lc_right, _lc_op)
                    else:
                        reason_code = "LIMIT_CONSTRAINT_VALUES_MISSING"
                else:
                    passed = True
        else:
            expected = spec.get(
                "expected",
                obs.get("expected", _MISSING),
            )
            actual = obs.get(
                "actual",
                obs.get("treatment_result", _MISSING),
            )
            if expected is _MISSING or actual is _MISSING:
                expected = None if expected is _MISSING else expected
                actual = None if actual is _MISSING else actual
                reason_code = "ASSERTION_EVIDENCE_MISSING"
            else:
                passed = actual == expected
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        reason_code = "ASSERTION_EVALUATION_ERROR"
        harness_error = True
        passed = None

    status = (
        "INDETERMINATE"
        if passed is None
        else "PASS"
        if passed
        else "VIOLATION"
    )
    if status == "INDETERMINATE" and not reason_code:
        reason_code = "ASSERTION_EVIDENCE_MISSING"
    receipt = _assertion_receipt(
        assertion_id=assertion_id,
        kind=kind,
        status=status,
        reason_code=reason_code,
        expected=expected,
        actual=actual,
        error=error,
        observer_receipt_ids=observer_ids,
        source_refs=refs,
        harness_error=harness_error,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    # V1.6.0 P0-15: field oracle trace for deep assertion kinds.
    if effective_kind in {
        "conservation",
        "field_delta",
        "postcondition",
        "state_transition",
        "cross_entity_consistency",
    }:
        receipt["field_oracle_trace"] = {
            "schema_version": "qualibug.field-oracle-trace.v1",
            "assertion_id": assertion_id,
            "kind": effective_kind,
            "rule_id": _text(spec.get("invariant_ref") or spec.get("rule_id")),
            "expected": expected,
            "actual": actual,
            "before_values": obs.get("before_values"),
            "after_values": obs.get("after_values"),
            "status": status,
            "reason_code": reason_code,
        }
    return receipt


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    results = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(
            assertion.get("observer_id")
            or assertion.get("assertion_id")
            or "default"
        )
        obs = _dict(
            observations_by_id.get(obs_key)
            or observations_by_id.get("default")
        )
        results.append(
            evaluate_assertion(
                assertion,
                observations=obs,
                campaign_id=campaign_id,
                execution_id=execution_id,
            )
        )
    return {
        "total": len(results),
        "passed": sum(
            1 for item in results if item.get("status") == "PASS"
        ),
        "violations": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "indeterminate": sum(
            1
            for item in results
            if item.get("status") == "INDETERMINATE"
        ),
        # Compatibility alias: only proven violations are assertion failures.
        "failed": sum(
            1 for item in results if item.get("status") == "VIOLATION"
        ),
        "harness_errors": sum(
            1 for item in results if item.get("harness_error")
        ),
        "results": results,
    }


def materialize_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn compiler templates into executable DSL specs without inventing expected values."""

    spec = dict(_dict(assertion))
    kind = _text(spec.get("kind") or spec.get("type"))
    prop = _dict(spec.get("property"))
    obs = _dict(observations)

    family_map = {
        "authorization": "owner_tenant_visibility",
        "isolation": "owner_tenant_visibility",
        "visibility": "owner_tenant_visibility",
        "privacy": "owner_tenant_visibility",
        "idempotency": "idempotency_effect",
        "concurrency": "concurrency_final_invariant",
        "state": "state_transition",
        "conservation": "conservation",
        "validation": "http_status",
        "causal": "field_delta",
        "causal_postcondition": "field_delta",
    }
    if kind in family_map:
        spec["kind"] = family_map[kind]
        kind = spec["kind"]

    if (
        kind == "http_status"
        and spec.get("expected") is None
        and spec.get("expected_status") is None
    ):
        if prop.get("expected_status") is not None:
            spec["expected"] = prop.get("expected_status")
        elif obs.get("expected_status") is not None:
            spec["expected"] = obs.get("expected_status")

    if kind == "owner_tenant_visibility":
        spec.setdefault("require_control", True)

    if kind == "state_transition":
        if spec.get("from_state") is None and prop.get("from_state") is not None:
            spec["from_state"] = prop.get("from_state")
        if spec.get("to_state") is None and prop.get("to_state") is not None:
            spec["to_state"] = prop.get("to_state")

    if kind == "conservation" and not _dict(spec.get("equation")):
        equation = _dict(prop.get("equation"))
        if equation:
            spec["equation"] = equation

    if (
        kind == "idempotency_effect"
        and spec.get("expected_effect_count") is None
        and prop.get("expected_effect_count") is not None
    ):
        spec["expected_effect_count"] = prop.get(
            "expected_effect_count"
        )

    return spec
