"""Phase77: Business Invariant Evaluator — Deterministic invariant evaluation.

Takes before/after CanonicalStateSnapshots and evaluates ProofObligations
across 8 deterministic invariant types. No LLM — purely rule-based evaluation.

Invariant types:
1. state_unchanged_after_rejection
2. lifecycle_transition
3. numeric_delta
4. conservation
5. cross_view_equal
6. eventually
7. idempotency_replay
8. authorization_non_mutation
"""

from __future__ import annotations

import operator
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .state_observer_registry import CanonicalStateSnapshot, snapshot_diff


# ── Enums ──

class InvariantKind(str, Enum):
    """The 8 deterministic invariant types."""
    STATE_UNCHANGED_AFTER_REJECTION = "state_unchanged_after_rejection"
    LIFECYCLE_TRANSITION = "lifecycle_transition"
    NUMERIC_DELTA = "numeric_delta"
    CONSERVATION = "conservation"
    CROSS_VIEW_EQUAL = "cross_view_equal"
    EVENTUALLY = "eventually"
    IDEMPOTENCY_REPLAY = "idempotency_replay"
    AUTHORIZATION_NON_MUTATION = "authorization_non_mutation"


class Verdict(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNDETERMINED = "UNDETERMINED"


# ── Dataclasses ──

@dataclass
class ProofObligation:
    """A single invariant that must hold between two snapshots.

    The ``kind`` selects which evaluator function to use.  All other fields
    are parameters consumed by that evaluator.

    Field reference syntax for snapshot projections:
        "amounts.total"      → projection["amounts"]["total"]
        "attributes.status"  → projection["attributes"]["status"]
        "lifecycle_state"    → projection["lifecycle_state"]
        "quantities.count"   → projection["quantities"]["count"]
        "relations.order_id" → projection["relations"]["order_id"]

    Cross-view references use a ``source.`` prefix:
        "source.amounts.total"     → look up in ``before`` (or the source snapshot)
        "source.attributes.x"      → (alternative to bare references)
    By default bare field paths are resolved against the ``after`` snapshot.
    """

    obligation_id: str
    kind: str  # one of InvariantKind values
    title: str = ""
    severity: str = "P1"

    # ── Common parameters ──
    # Fields to watch (dot-paths into projection dicts).
    fields: list[str] = field(default_factory=list)

    def __post_init__(self):
        import uuid
        if not self.obligation_id:
            self.obligation_id = f'obl_{uuid.uuid4().hex[:12]}' 

    # ── lifecycle_transition ──
    # allowed_transitions: dict mapping from before-lifecycle-state to a set of
    # valid after-lifecycle-states.  E.g. {"draft": {"submitted", "cancelled"}}.
    allowed_transitions: dict[str, set[str]] = field(default_factory=dict)

    # ── numeric_delta ──
    expected_delta: float = 0.0
    tolerance: float = 1e-6

    # ── conservation ──
    # expression: a simple arithmetic/conservation expression string such as
    #   "A + B == C"
    #   "total - discount == subtotal"
    #   "A - B == C + D"
    # Variable names are resolved as field paths in the after snapshot projection.
    expression: str = ""

    # ── cross_view_equal ──
    # Field path on the *before* snapshot's projection.
    cross_view_before_field: str = ""
    # Field path on the *after* snapshot's projection.
    cross_view_after_field: str = ""

    # ── eventually ──
    # Timeout in seconds for the poll-style check (simulated).
    eventually_timeout: float = 10.0
    # Poll interval in seconds.
    eventually_poll_interval: float = 1.0
    # Predicate field: the field path whose value we wait to satisfy a condition.
    eventually_field: str = ""
    # Expected value or operator constraint (e.g. "> 0", "== approved", "!= null").
    eventually_predicate: str = ""

    # ── idempotency_replay ──
    # No extra configuration needed; uses full snapshot_diff.

    # ── authorization_non_mutation ──
    # No extra configuration needed; uses full snapshot_diff.

    # Arbitrary extra configuration key-value pairs.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class InvariantResult:
    """The deterministic outcome of evaluating a ProofObligation."""

    obligation_id: str
    kind: str
    verdict: str  # "PASSED" | "FAILED" | "UNDETERMINED"
    passed: bool
    detail: str = ""
    failed_fields: list[str] = field(default_factory=list)
    computed: dict[str, Any] = field(default_factory=dict)


# ── Evaluator ──

class BusinessInvariantEvaluator:
    """Deterministic invariant evaluation engine.

    Usage::

        evaluator = BusinessInvariantEvaluator()
        result = evaluator.evaluate(obligation, before_snapshot, after_snapshot)

    All 8 invariant types are evaluated without any LLM call — they are purely
    rule-based, arithmetic, or structural comparisons.
    """

    # Map invariant kind → handler method (populated in __init__).
    _handlers: dict[str, Callable[..., InvariantResult]]

    def __init__(self) -> None:
        self._handlers = {
            InvariantKind.STATE_UNCHANGED_AFTER_REJECTION: self._eval_state_unchanged_after_rejection,
            InvariantKind.LIFECYCLE_TRANSITION: self._eval_lifecycle_transition,
            InvariantKind.NUMERIC_DELTA: self._eval_numeric_delta,
            InvariantKind.CONSERVATION: self._eval_conservation,
            InvariantKind.CROSS_VIEW_EQUAL: self._eval_cross_view_equal,
            InvariantKind.EVENTUALLY: self._eval_eventually,
            InvariantKind.IDEMPOTENCY_REPLAY: self._eval_idempotency_replay,
            InvariantKind.AUTHORIZATION_NON_MUTATION: self._eval_authorization_non_mutation,
        }

    # ── Public API ──

    def evaluate(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any] | None = None,
    ) -> InvariantResult:
        """Evaluate a single proof obligation against two snapshots.

        Args:
            obligation: The invariant definition to check.
            before: The CanonicalStateSnapshot captured *before* the action.
            after:  The CanonicalStateSnapshot captured *after* the action.
            context: Optional extra context (e.g. poll results, replay data).

        Returns:
            An InvariantResult with the verdict, detail, and any computed values.
        """
        handler = self._handlers.get(obligation.kind)
        if handler is None:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail=f"Unknown invariant kind: {obligation.kind!r}",
                failed_fields=[],
                computed={"available_kinds": list(self._handlers.keys())},
            )
        return handler(obligation, before, after, context or {})

    def evaluate_all(
        self,
        obligations: list[ProofObligation],
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any] | None = None,
    ) -> list[InvariantResult]:
        """Evaluate a batch of obligations and return all results."""
        ctx = context or {}
        return [self.evaluate(obligation, before, after, ctx) for obligation in obligations]

    # ── 1. state_unchanged_after_rejection ──

    def _eval_state_unchanged_after_rejection(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """The specified fields MUST show no changes in snapshot_diff.

        A failure of this invariant means a rejected/forbidden action still
        mutated observable business state.
        """
        fields = obligation.fields or None  # None → diff all fields
        diff = snapshot_diff(before, after, fields=fields)

        if not diff:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.PASSED.value,
                passed=True,
                detail="All specified fields unchanged after rejection.",
                failed_fields=[],
                computed={"diff": {}},
            )

        # Remove payload_changed from diff — that's expected after any HTTP round-trip
        changed_fields = [k for k in diff if k != "payload_changed"]

        if not changed_fields:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.PASSED.value,
                passed=True,
                detail="Only payload hash changed (expected); business fields unchanged.",
                failed_fields=[],
                computed={"diff": diff},
            )

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.FAILED.value,
            passed=False,
            detail=f"State mutated after rejection: {len(changed_fields)} field(s) changed.",
            failed_fields=changed_fields,
            computed={"diff": diff},
        )

    # ── 2. lifecycle_transition ──

    def _eval_lifecycle_transition(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """after.lifecycle_state must be in allowed_transitions[before.lifecycle_state]."""
        before_state = before.projection.get("lifecycle_state")
        after_state = after.projection.get("lifecycle_state")

        allowed = obligation.allowed_transitions

        if not allowed:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail="No allowed_transitions configured for lifecycle_transition invariant.",
                failed_fields=[],
                computed={"before_state": before_state, "after_state": after_state},
            )

        before_key = str(before_state) if before_state is not None else "None"

        valid_targets = allowed.get(before_key)
        if valid_targets is None:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.FAILED.value,
                passed=False,
                detail=f"Before lifecycle state {before_key!r} has no entry in allowed_transitions.",
                failed_fields=["lifecycle_state"],
                computed={
                    "before_state": before_state,
                    "after_state": after_state,
                    "allowed_transitions": {k: list(v) for k, v in allowed.items()},
                },
            )

        after_key = str(after_state) if after_state is not None else "None"

        if after_key in valid_targets:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.PASSED.value,
                passed=True,
                detail=f"Lifecycle transition {before_key!r} → {after_key!r} is allowed.",
                failed_fields=[],
                computed={
                    "before_state": before_state,
                    "after_state": after_state,
                    "allowed_from_before": list(valid_targets),
                },
            )

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.FAILED.value,
            passed=False,
            detail=f"Lifecycle transition {before_key!r} → {after_key!r} is NOT allowed. "
                   f"Allowed targets: {sorted(valid_targets)}",
            failed_fields=["lifecycle_state"],
            computed={
                "before_state": before_state,
                "after_state": after_state,
                "allowed_from_before": list(valid_targets),
            },
        )

    # ── 3. numeric_delta ──

    def _eval_numeric_delta(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """abs((after.field - before.field) - expected_delta) <= tolerance."""
        field_path = obligation.fields[0] if (obligation.fields and isinstance(obligation.fields, (list,tuple)) and len(obligation.fields) > 0) else ""

        if not field_path:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail="No field specified for numeric_delta invariant.",
                failed_fields=[],
                computed={},
            )

        before_val = self._resolve_field(before, field_path)
        after_val = self._resolve_field(after, field_path)

        if before_val is None or after_val is None:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail=f"Cannot resolve field {field_path!r} in one or both snapshots.",
                failed_fields=[field_path],
                computed={"before_value": before_val, "after_value": after_val},
            )

        try:
            b = float(before_val)
            a = float(after_val)
        except (TypeError, ValueError):
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail=f"Field {field_path!r} is not numeric "
                       f"(before={before_val!r}, after={after_val!r}).",
                failed_fields=[field_path],
                computed={"before_value": before_val, "after_value": after_val},
            )

        actual_delta = a - b
        expected = obligation.expected_delta
        tolerance = obligation.tolerance
        deviation = abs(actual_delta - expected)

        passed = deviation <= tolerance

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.PASSED.value if passed else Verdict.FAILED.value,
            passed=passed,
            detail=(
                f"Numeric delta on {field_path!r}: actual={actual_delta:.8g}, "
                f"expected={expected}, deviation={deviation:.8g}, tolerance={tolerance:.8g}"
            ),
            failed_fields=[] if passed else [field_path],
            computed={
                "field": field_path,
                "before_value": b,
                "after_value": a,
                "actual_delta": actual_delta,
                "expected_delta": expected,
                "deviation": deviation,
                "tolerance": tolerance,
            },
        )

    # ── 4. conservation ──

    # Allowed operators in conservation expressions.
    _CONSERVATION_OPS: dict[str, Callable[[float, float], float]] = {
        "+": operator.add,
        "-": operator.sub,
    }
    _CONSERVATION_COMPARATORS: dict[str, Callable[[float, float], bool]] = {
        "==": operator.eq,
        "<=": operator.le,
        ">=": operator.ge,
        "<":  operator.lt,
        ">":  operator.gt,
    }

    def _eval_conservation(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """Evaluate a simple conservation expression like "A+B==C" against
        field values resolved from the after snapshot projection.

        Supports expressions of the form:
            <variable> <op> <variable> ... <cmp> <variable> <op> <variable> ...

        Where <op> is + or -, and <cmp> is ==, <=, >=, <, >.

        Variable names are resolved via _resolve_field against the ``after``
        snapshot (the post-action state).
        """
        expression = obligation.expression.strip()
        if not expression:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail="No expression configured for conservation invariant.",
                failed_fields=[],
                computed={},
            )

        # Find the comparator token.
        cmp_match = re.search(r"(==|<=|>=|<|>)", expression)
        if not cmp_match:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail=f"Could not parse comparator in expression: {expression!r}",
                failed_fields=[],
                computed={"expression": expression},
            )

        cmp_op = cmp_match.group(0)
        left_side = expression[:cmp_match.start()].strip()
        right_side = expression[cmp_match.end():].strip()

        try:
            left_val = self._eval_arithmetic_side(left_side, after)
            right_val = self._eval_arithmetic_side(right_side, after)
        except ConservationEvalError as exc:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail=str(exc),
                failed_fields=exc.failed_fields,
                computed={"expression": expression},
            )

        comparator = self._CONSERVATION_COMPARATORS[cmp_op]
        passed = comparator(left_val, right_val)

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.PASSED.value if passed else Verdict.FAILED.value,
            passed=passed,
            detail=(
                f"Conservation {expression!r}: "
                f"LHS={left_val:.8g}, RHS={right_val:.8g}, "
                f"{left_val:.8g} {cmp_op} {right_val:.8g} → {passed}"
            ),
            failed_fields=[] if passed else [expression],
            computed={
                "expression": expression,
                "left_side": left_val,
                "right_side": right_val,
                "comparator": cmp_op,
            },
        )

    def _eval_arithmetic_side(
        self, side_expr: str, snapshot: CanonicalStateSnapshot
    ) -> float:
        """Parse and evaluate one side of a conservation expression.

        Simple left-to-right evaluation of '+' and '-' separated terms.
        Each term is a field name resolved against the snapshot.
        """
        tokens = re.split(r"\s*([+\-])\s*", side_expr)
        # tokens alternates: var, operator, var, operator, var, ...
        if not tokens:
            raise ConservationEvalError("Empty arithmetic side", [])

        total = 0.0
        current_op: Callable[[float, float], float] = operator.add  # implicit +

        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if token in ("+", "-"):
                current_op = self._CONSERVATION_OPS[token]
            else:
                val = self._resolve_numeric_field(snapshot, token)
                if val is None:
                    raise ConservationEvalError(
                        f"Cannot resolve field {token!r} or value is not numeric",
                        [token],
                    )
                total = current_op(total, val)

        return total

    def _resolve_numeric_field(
        self, snapshot: CanonicalStateSnapshot, field_path: str
    ) -> float | None:
        """Resolve a field path to a float, or return None."""
        val = self._resolve_field(snapshot, field_path)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # ── 5. cross_view_equal ──

    def _eval_cross_view_equal(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """A field observed by two different sources must be equal."""
        before_field = obligation.cross_view_before_field or (
            obligation.fields[0] if len(obligation.fields) >= 1 else ""
        )
        after_field = obligation.cross_view_after_field or (
            obligation.fields[1] if len(obligation.fields) >= 2 else before_field
        )

        if not before_field or not after_field:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail="cross_view_equal requires cross_view_before_field and cross_view_after_field "
                       "(or two fields entries).",
                failed_fields=[],
                computed={},
            )

        before_val = self._resolve_field(before, before_field)
        after_val = self._resolve_field(after, after_field)

        if before_val is None and after_val is None:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail=f"Neither field resolved: before.{before_field!r}, after.{after_field!r}",
                failed_fields=[before_field, after_field],
                computed={"before_value": None, "after_value": None},
            )

        passed = before_val == after_val

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.PASSED.value if passed else Verdict.FAILED.value,
            passed=passed,
            detail=(
                f"Cross-view equality {before_field!r} == {after_field!r}: "
                f"before={before_val!r}, after={after_val!r}"
            ),
            failed_fields=[] if passed else [before_field, after_field],
            computed={
                "before_field": before_field,
                "after_field": after_field,
                "before_value": before_val,
                "after_value": after_val,
                "observers": {
                    "before_observer": before.source.get("observer_id"),
                    "after_observer": after.source.get("observer_id"),
                },
            },
        )

    # ── 6. eventually ──

    def _eval_eventually(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """Poll-style check that a condition becomes true within a timeout.

        In this deterministic evaluator, eventually is simulated via a context
        dict that can supply intermediate poll snapshots.  If no poll data is
        provided, we simply check the ``after`` snapshot.

        Context keys used (when available):
            ``polls``: list[CanonicalStateSnapshot] — intermediate snapshots.
            ``poll_times``: list[float] — timestamps of each poll.
        """
        field = obligation.eventually_field
        predicate_str = obligation.eventually_predicate
        timeout = obligation.eventually_timeout
        poll_interval = obligation.eventually_poll_interval

        if not field:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.UNDETERMINED.value,
                passed=False,
                detail="No eventually_field configured.",
                failed_fields=[],
                computed={},
            )

        # Build the list of snapshots to check, in order.
        snapshots_to_check: list[tuple[float, CanonicalStateSnapshot]] = []

        # First, check the before snapshot (time 0).
        snapshots_to_check.append((0.0, before))

        # Add intermediate polls if available.
        polls: list[Any] = context.get("polls", [])
        poll_times: list[float] = context.get("poll_times", [])
        for i, snap in enumerate(polls):
            t = poll_times[i] if i < len(poll_times) else (i + 1) * poll_interval
            snapshots_to_check.append((t, snap))

        # Always include the final after snapshot.
        final_time = timeout if snapshots_to_check else poll_interval
        snapshots_to_check.append((final_time, after))

        # Check each snapshot in temporal order.
        for ts, snap in snapshots_to_check:
            val = self._resolve_field(snap, field)
            if val is None:
                continue
            if self._check_predicate(val, predicate_str):
                return InvariantResult(
                    obligation_id=obligation.obligation_id,
                    kind=obligation.kind,
                    verdict=Verdict.PASSED.value,
                    passed=True,
                    detail=(
                        f"Eventually satisfied at t≈{ts:.2f}s: "
                        f"{field!r} = {val!r} satisfies {predicate_str!r}"
                    ),
                    failed_fields=[],
                    computed={
                        "field": field,
                        "value": val,
                        "time_satisfied": ts,
                        "predicate": predicate_str,
                        "total_snapshots_checked": len(snapshots_to_check),
                    },
                )

        # Not satisfied within the available snapshots.
        last_val = self._resolve_field(after, field)
        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.FAILED.value,
            passed=False,
            detail=(
                f"Eventually not satisfied within timeout={timeout:.2f}s: "
                f"{field!r} = {last_val!r} does not satisfy {predicate_str!r}"
            ),
            failed_fields=[field],
            computed={
                "field": field,
                "last_value": last_val,
                "predicate": predicate_str,
                "timeout": timeout,
                "snapshots_checked": len(snapshots_to_check),
            },
        )

    @staticmethod
    def _check_predicate(value: Any, predicate_str: str) -> bool:
        """Check if a value satisfies a simple predicate string.

        Supported forms:
            "> N"       → value > N (numeric)
            ">= N"      → value >= N
            "< N"       → value < N
            "<= N"      → value <= N
            "== VALUE"  → string equality
            "!= VALUE"  → string inequality
            "!= null"   → value is not None
            "== null"   → value is None
            plain VALUE → string equality (same as "== VALUE")
        """
        predicate_str = predicate_str.strip()
        if not predicate_str:
            return value is not None

        # Numeric comparison operators
        for op_str, op_func in [
            (">=", operator.ge),
            ("<=", operator.le),
            (">",  operator.gt),
            ("<",  operator.lt),
        ]:
            if predicate_str.startswith(op_str):
                rhs = predicate_str[len(op_str):].strip()
                try:
                    return op_func(float(value), float(rhs))
                except (TypeError, ValueError):
                    return False

        # Equality / inequality
        if predicate_str.startswith("=="):
            rhs = predicate_str[2:].strip()
        elif predicate_str.startswith("!="):
            rhs = predicate_str[2:].strip()
            if rhs.lower() == "null":
                return value is not None
            return str(value) != rhs
        else:
            # Plain value → exact match
            return str(value) == predicate_str

        if rhs.lower() == "null":
            return value is None
        return str(value) == rhs

    # ── 7. idempotency_replay ──

    def _eval_idempotency_replay(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """After an idempotency replay, the before/after snapshots must be identical.

        The ``before`` snapshot was captured after the first request; the
        ``after`` snapshot after the replay.  They should match.
        """
        fields = obligation.fields or None
        diff = snapshot_diff(before, after, fields=fields)

        # Remove payload_changed — raw payload hashes may differ due to timestamps
        changed_fields = [k for k in diff if k != "payload_changed"]

        if not changed_fields and not diff.get("payload_changed"):
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.PASSED.value,
                passed=True,
                detail="Idempotency replay: snapshots are identical.",
                failed_fields=[],
                computed={"diff": diff},
            )

        if not changed_fields:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.PASSED.value,
                passed=True,
                detail="Idempotency replay: only raw payload hash differs (acceptable).",
                failed_fields=[],
                computed={"diff": diff},
            )

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.FAILED.value,
            passed=False,
            detail=f"Idempotency replay: {len(changed_fields)} field(s) diverged.",
            failed_fields=changed_fields,
            computed={"diff": diff},
        )

    # ── 8. authorization_non_mutation ──

    def _eval_authorization_non_mutation(
        self,
        obligation: ProofObligation,
        before: CanonicalStateSnapshot,
        after: CanonicalStateSnapshot,
        context: dict[str, Any],
    ) -> InvariantResult:
        """After an unauthorized operation attempt, state must be completely unchanged.

        The ``before`` and ``after`` snapshots must be identical in all
        business-relevant fields.
        """
        fields = obligation.fields or None
        diff = snapshot_diff(before, after, fields=fields)

        changed_fields = [k for k in diff if k != "payload_changed"]

        if not changed_fields:
            return InvariantResult(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                verdict=Verdict.PASSED.value,
                passed=True,
                detail="Authorization non-mutation: no business state changed.",
                failed_fields=[],
                computed={"diff": diff},
            )

        return InvariantResult(
            obligation_id=obligation.obligation_id,
            kind=obligation.kind,
            verdict=Verdict.FAILED.value,
            passed=False,
            detail=f"Authorization non-mutation violated: {len(changed_fields)} field(s) mutated.",
            failed_fields=changed_fields,
            computed={"diff": diff},
        )

    # ── Field resolution helpers ──

    @staticmethod
    def _resolve_field(snapshot: CanonicalStateSnapshot, field_path: str) -> Any:
        """Resolve a dotted field path against a snapshot's projection.

        Supported paths:
            "lifecycle_state"              → snapshot.projection["lifecycle_state"]
            "amounts.total"                → snapshot.projection["amounts"]["total"]
            "attributes.status"            → snapshot.projection["attributes"]["status"]
            "quantities.count"             → snapshot.projection["quantities"]["count"]
            "relations.parent_id"          → snapshot.projection["relations"]["parent_id"]
        """
        path = field_path.strip()
        if not path:
            return None

        parts = path.split(".", 1)

        top_key = parts[0]
        if top_key not in snapshot.projection:
            # Fallback: search amounts/quantities/attributes for bare names
            for sub in ("amounts", "quantities", "attributes", "relations"):
                container = snapshot.projection.get(sub)
                if isinstance(container, dict) and top_key in container:
                    return container[top_key]
            return None

        if len(parts) == 1:
            return snapshot.projection[top_key]

        sub_key = parts[1]
        container = snapshot.projection[top_key]
        if isinstance(container, dict):
            return container.get(sub_key)
        return None


# ── Internal helper ──

class ConservationEvalError(Exception):
    """Raised when a conservation expression cannot be evaluated."""

    def __init__(self, message: str, failed_fields: list[str]) -> None:
        super().__init__(message)
        self.failed_fields = failed_fields
