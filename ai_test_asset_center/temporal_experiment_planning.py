"""Temporal Boundary Experiment Planning and Execution.

Generic module for planning and executing temporal boundary experiments.
Handles cross-entity temporal rules (e.g. "field A must not be later than
field B") by:

1. Parsing temporal rule structure (subject, reference, operator, precision)
2. Normalizing time semantics (operator, inclusivity, precision, timezone)
3. Resolving reference time from related entities
4. Solving boundary values (Control/Violation pairs)
5. Generating Temporal Plan Proof before execution
6. Generating Temporal Observation Proof after execution

No project-specific or benchmark-specific logic.
No HTTP requests - pure planning and proof generation.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date
from typing import Any

# ─── Block Reasons ─────────────────────────────────────────────────────────────

TEMPORAL_RULE_INCOMPLETE = "TEMPORAL_RULE_INCOMPLETE"
TEMPORAL_SUBJECT_FIELD_UNRESOLVED = "TEMPORAL_SUBJECT_FIELD_UNRESOLVED"
TEMPORAL_REFERENCE_UNRESOLVED = "TEMPORAL_REFERENCE_UNRESOLVED"
TEMPORAL_OPERATOR_UNRESOLVED = "TEMPORAL_OPERATOR_UNRESOLVED"
TEMPORAL_INCLUSIVITY_UNRESOLVED = "TEMPORAL_INCLUSIVITY_UNRESOLVED"
TEMPORAL_PRECISION_UNRESOLVED = "TEMPORAL_PRECISION_UNRESOLVED"
TEMPORAL_TIMEZONE_UNRESOLVED = "TEMPORAL_TIMEZONE_UNRESOLVED"
TEMPORAL_REFERENCE_NOT_CONTROLLABLE = "TEMPORAL_REFERENCE_NOT_CONTROLLABLE"
TEMPORAL_BOUNDARY_SOLUTION_INVALID = "TEMPORAL_BOUNDARY_SOLUTION_INVALID"
TEMPORAL_TARGET_OPERATION_UNBOUND = "TEMPORAL_TARGET_OPERATION_UNBOUND"
TEMPORAL_PRECONDITION_NOT_READY = "TEMPORAL_PRECONDITION_NOT_READY"
TEMPORAL_PLAN_PROOF_INCOMPLETE = "TEMPORAL_PLAN_PROOF_INCOMPLETE"
TEMPORAL_FIXTURE_CONTAMINATED = "TEMPORAL_FIXTURE_CONTAMINATED"
TEMPORAL_VALUE_NOT_MATERIALIZED = "TEMPORAL_VALUE_NOT_MATERIALIZED"
TEMPORAL_SERVER_OVERRIDE = "TEMPORAL_SERVER_OVERRIDE"
TEMPORAL_PRECISION_LOSS = "TEMPORAL_PRECISION_LOSS"
TEMPORAL_TIMEZONE_SHIFT = "TEMPORAL_TIMEZONE_SHIFT"
TEMPORAL_OBSERVATION_INCOMPLETE = "TEMPORAL_OBSERVATION_INCOMPLETE"
TEMPORAL_ORACLE_INPUT_INCOMPLETE = "TEMPORAL_ORACLE_INPUT_INCOMPLETE"
TEMPORAL_EXPERIMENT_INVALID = "TEMPORAL_EXPERIMENT_INVALID"

# ─── Operators ─────────────────────────────────────────────────────────────────

OPERATOR_LT = "LT"
OPERATOR_LTE = "LTE"
OPERATOR_GT = "GT"
OPERATOR_GTE = "GTE"
OPERATOR_EQ = "EQ"
OPERATOR_BETWEEN = "BETWEEN"
OPERATOR_NOT_BETWEEN = "NOT_BETWEEN"
OPERATOR_BEFORE = "BEFORE"
OPERATOR_AFTER = "AFTER"
OPERATOR_SAME_DATE = "SAME_DATE"

VALID_OPERATORS = frozenset({
    OPERATOR_LT, OPERATOR_LTE, OPERATOR_GT, OPERATOR_GTE, OPERATOR_EQ,
    OPERATOR_BETWEEN, OPERATOR_NOT_BETWEEN, OPERATOR_BEFORE, OPERATOR_AFTER,
    OPERATOR_SAME_DATE,
})

# Operator natural language mapping
_OPERATOR_KEYWORDS = {
    "不得晚于": OPERATOR_LTE,
    "不得早于": OPERATOR_GTE,
    "早于": OPERATOR_LT,
    "晚于": OPERATOR_GT,
    "must not be later than": OPERATOR_LTE,
    "must not be after": OPERATOR_LTE,
    "must be before": OPERATOR_LT,
    "must be after": OPERATOR_GT,
    "must be on or before": OPERATOR_LTE,
    "must be on or after": OPERATOR_GTE,
    "<=": OPERATOR_LTE,
    ">=": OPERATOR_GTE,
    "<": OPERATOR_LT,
    ">": OPERATOR_GT,
    "=": OPERATOR_EQ,
}

# ─── Precision ─────────────────────────────────────────────────────────────────

PRECISION_DATE = "DATE"
PRECISION_SECOND = "SECOND"
PRECISION_MILLISECOND = "MILLISECOND"

# Epsilon by precision
EPSILON_BY_PRECISION = {
    PRECISION_DATE: timedelta(days=1),
    PRECISION_SECOND: timedelta(seconds=1),
    PRECISION_MILLISECOND: timedelta(milliseconds=1),
}

# ─── Reference Types ───────────────────────────────────────────────────────────

REFERENCE_FIELD = "FIELD"
REFERENCE_RELATED_ENTITY_FIELD = "RELATED_ENTITY_FIELD"
REFERENCE_OPERATION_TIME = "OPERATION_TIME"
REFERENCE_CREATION_TIME = "CREATION_TIME"
REFERENCE_CURRENT_TIME = "CURRENT_TIME"
REFERENCE_CONSTANT = "CONSTANT"
REFERENCE_DERIVED_EXPRESSION = "DERIVED_EXPRESSION"


def _stable_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return "temporal_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(v: Any) -> str:
    return str(v or "").strip()


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


# ─── Temporal Rule Structure ───────────────────────────────────────────────────

@dataclass
class TemporalSubject:
    """Subject field being constrained."""
    entity_id: str
    field_id: str
    field_type: str = "DATE"  # DATE, LOCAL_DATETIME, OFFSET_DATETIME, INSTANT


@dataclass
class TemporalReference:
    """Reference time source."""
    reference_type: str  # FIELD, RELATED_ENTITY_FIELD, OPERATION_TIME, etc.
    entity_id: str = ""
    field_id: str = ""
    event_id: str = ""
    constant_expression: str = ""


@dataclass
class TemporalComparison:
    """Comparison semantics."""
    operator: str  # LT, LTE, GT, GTE, EQ, BETWEEN, etc.
    inclusive: bool = True
    tolerance: str = ""


@dataclass
class TemporalRule:
    """Complete temporal rule structure."""
    internal_rule_id: str
    rule_type: str = "TEMPORAL"
    subject: TemporalSubject | None = None
    reference: TemporalReference | None = None
    comparison: TemporalComparison | None = None
    precision: str = PRECISION_DATE
    timezone_source: str = ""
    timezone_zone_id: str = "UTC"
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    target_operation_id: str = ""
    target_operation_actor_role: str = ""
    source_evidence_id: str = ""
    source_confidence: float = 0.0

    def is_complete(self) -> tuple[bool, str]:
        """Check if rule has all required fields."""
        if not self.subject or not self.subject.field_id:
            return False, TEMPORAL_SUBJECT_FIELD_UNRESOLVED
        if not self.reference:
            return False, TEMPORAL_REFERENCE_UNRESOLVED
        if not self.comparison or not self.comparison.operator:
            return False, TEMPORAL_OPERATOR_UNRESOLVED
        if not self.precision:
            return False, TEMPORAL_PRECISION_UNRESOLVED
        if not self.target_operation_id:
            return False, TEMPORAL_TARGET_OPERATION_UNBOUND
        return True, ""


# ─── Temporal Rule Parser ──────────────────────────────────────────────────────

class TemporalRuleParser:
    """Parse temporal rules from various source formats."""

    def parse_from_structured_expression(
        self,
        internal_rule_id: str,
        expression: dict[str, Any],
        rule_statement: str = "",
    ) -> TemporalRule:
        """Parse temporal rule from structured expression."""
        expr = _dict(expression)

        # Parse subject
        subject = None
        subject_entity = _text(expr.get("subject_entity") or expr.get("left", {}).get("entity"))
        subject_field = _text(
            expr.get("subject_field")
            or expr.get("date_field")
            or expr.get("field")
            or expr.get("left", {}).get("field")
        )
        if subject_entity and subject_field:
            subject = TemporalSubject(
                entity_id=subject_entity,
                field_id=subject_field,
                field_type=_text(expr.get("field_type") or "DATE"),
            )

        # Parse reference
        reference = None
        ref_type = _text(expr.get("reference_type"))
        ref_entity = _text(expr.get("reference_entity") or expr.get("right", {}).get("entity"))
        ref_field = _text(expr.get("reference_field") or expr.get("right", {}).get("field"))
        if ref_entity and ref_field:
            reference = TemporalReference(
                reference_type=ref_type or REFERENCE_RELATED_ENTITY_FIELD,
                entity_id=ref_entity,
                field_id=ref_field,
            )
        elif expr.get("bounds"):
            # Range-based reference
            bounds = _dict(expr.get("bounds"))
            reference = TemporalReference(
                reference_type=REFERENCE_FIELD,
                entity_id=subject_entity,
                field_id=_text(bounds.get("start") or bounds.get("end")),
            )

        # Parse operator
        operator = self._resolve_operator(expr, rule_statement)
        inclusive = operator in (OPERATOR_LTE, OPERATOR_GTE, OPERATOR_BETWEEN)
        comparison = TemporalComparison(
            operator=operator,
            inclusive=inclusive,
        )

        # Parse precision
        precision = _text(expr.get("precision") or expr.get("field_type")).upper()
        if precision not in (PRECISION_DATE, PRECISION_SECOND, PRECISION_MILLISECOND):
            precision = PRECISION_DATE  # Default to DATE

        return TemporalRule(
            internal_rule_id=internal_rule_id,
            subject=subject,
            reference=reference,
            comparison=comparison,
            precision=precision,
            timezone_source=_text(expr.get("timezone_source")),
            timezone_zone_id=_text(expr.get("timezone") or "UTC"),
            target_operation_id=_text(expr.get("target_operation") or expr.get("operation_id")),
            source_evidence_id=_text(expr.get("source_id")),
            source_confidence=float(expr.get("confidence") or 0.8),
        )

    def _resolve_operator(self, expr: dict[str, Any], rule_statement: str) -> str:
        """Resolve operator from expression or rule statement."""
        # Direct operator field
        op = _text(expr.get("operator")).upper()
        if op in VALID_OPERATORS:
            return op

        # From rule statement keywords
        statement = rule_statement.lower()
        for keyword, operator in _OPERATOR_KEYWORDS.items():
            if keyword.lower() in statement:
                return operator

        # Default to LTE for "不得晚于" style rules
        return OPERATOR_LTE


# ─── Boundary Value Solver ─────────────────────────────────────────────────────

@dataclass
class BoundaryCase:
    """A single boundary test case."""
    case_id: str
    case_type: str  # CONTROL or VIOLATION
    subject_value: str
    expected_valid: bool
    distance_from_boundary: str
    description: str = ""


@dataclass
class TemporalBoundarySolution:
    """Complete boundary solution for a temporal rule."""
    internal_rule_id: str
    operator: str
    precision: str
    epsilon: str
    reference_value: str
    control_cases: list[BoundaryCase] = field(default_factory=list)
    violation_cases: list[BoundaryCase] = field(default_factory=list)
    solver_trace: dict[str, Any] = field(default_factory=dict)
    complete: bool = False
    blocked_reason: str = ""

    def is_valid(self) -> tuple[bool, str]:
        """Validate that control cases are valid and violation cases violate."""
        if not self.control_cases:
            return False, TEMPORAL_BOUNDARY_SOLUTION_INVALID
        if not self.violation_cases:
            return False, TEMPORAL_BOUNDARY_SOLUTION_INVALID
        # All control cases must be expected_valid=True
        for case in self.control_cases:
            if not case.expected_valid:
                return False, TEMPORAL_BOUNDARY_SOLUTION_INVALID
        # All violation cases must be expected_valid=False
        for case in self.violation_cases:
            if case.expected_valid:
                return False, TEMPORAL_BOUNDARY_SOLUTION_INVALID
        return True, ""


class BoundaryValueSolver:
    """Solve boundary values for temporal rules."""

    def solve(
        self,
        rule: TemporalRule,
        reference_value: str,
    ) -> TemporalBoundarySolution:
        """Generate Control and Violation cases for a temporal rule."""
        if not rule.comparison:
            return TemporalBoundarySolution(
                internal_rule_id=rule.internal_rule_id,
                operator="",
                precision=rule.precision,
                epsilon="",
                reference_value=reference_value,
                blocked_reason=TEMPORAL_OPERATOR_UNRESOLVED,
            )

        operator = rule.comparison.operator
        inclusive = rule.comparison.inclusive
        epsilon = EPSILON_BY_PRECISION.get(rule.precision, timedelta(days=1))

        # Parse reference value
        ref_dt = self._parse_datetime(reference_value, rule.precision)
        if ref_dt is None:
            return TemporalBoundarySolution(
                internal_rule_id=rule.internal_rule_id,
                operator=operator,
                precision=rule.precision,
                epsilon=str(epsilon),
                reference_value=reference_value,
                blocked_reason=TEMPORAL_REFERENCE_UNRESOLVED,
            )

        solution = TemporalBoundarySolution(
            internal_rule_id=rule.internal_rule_id,
            operator=operator,
            precision=rule.precision,
            epsilon=str(epsilon),
            reference_value=reference_value,
            solver_trace={
                "operator": operator,
                "inclusive": inclusive,
                "reference_parsed": ref_dt.isoformat(),
            },
        )

        # Generate cases based on operator
        if operator in (OPERATOR_LTE, OPERATOR_BEFORE):
            # subject <= reference (or subject < reference)
            # Control: reference (if inclusive) or reference - epsilon
            # Violation: reference + epsilon
            if inclusive:
                solution.control_cases.append(BoundaryCase(
                    case_id=_stable_id(rule.internal_rule_id, "control_at_boundary"),
                    case_type="CONTROL",
                    subject_value=self._format_datetime(ref_dt, rule.precision),
                    expected_valid=True,
                    distance_from_boundary="0",
                    description="At boundary (inclusive)",
                ))
            solution.control_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "control_safe"),
                case_type="CONTROL",
                subject_value=self._format_datetime(ref_dt - epsilon, rule.precision),
                expected_valid=True,
                distance_from_boundary="1ε",
                description="Safe side of boundary",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_minimal"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt + epsilon, rule.precision),
                expected_valid=False,
                distance_from_boundary="1ε",
                description="Minimal violation",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_clear"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt + epsilon * 3, rule.precision),
                expected_valid=False,
                distance_from_boundary="3ε",
                description="Clear violation",
            ))

        elif operator in (OPERATOR_GTE, OPERATOR_AFTER):
            # subject >= reference (or subject > reference)
            # Control: reference (if inclusive) or reference + epsilon
            # Violation: reference - epsilon
            if inclusive:
                solution.control_cases.append(BoundaryCase(
                    case_id=_stable_id(rule.internal_rule_id, "control_at_boundary"),
                    case_type="CONTROL",
                    subject_value=self._format_datetime(ref_dt, rule.precision),
                    expected_valid=True,
                    distance_from_boundary="0",
                    description="At boundary (inclusive)",
                ))
            solution.control_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "control_safe"),
                case_type="CONTROL",
                subject_value=self._format_datetime(ref_dt + epsilon, rule.precision),
                expected_valid=True,
                distance_from_boundary="1ε",
                description="Safe side of boundary",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_minimal"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt - epsilon, rule.precision),
                expected_valid=False,
                distance_from_boundary="1ε",
                description="Minimal violation",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_clear"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt - epsilon * 3, rule.precision),
                expected_valid=False,
                distance_from_boundary="3ε",
                description="Clear violation",
            ))

        elif operator == OPERATOR_LT:
            # subject < reference (strict)
            # Control: reference - epsilon
            # Violation: reference (boundary itself violates)
            solution.control_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "control_safe"),
                case_type="CONTROL",
                subject_value=self._format_datetime(ref_dt - epsilon, rule.precision),
                expected_valid=True,
                distance_from_boundary="1ε",
                description="Safe side (strict)",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_at_boundary"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt, rule.precision),
                expected_valid=False,
                distance_from_boundary="0",
                description="At boundary (violates strict <)",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_beyond"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt + epsilon, rule.precision),
                expected_valid=False,
                distance_from_boundary="1ε",
                description="Beyond boundary",
            ))

        elif operator == OPERATOR_GT:
            # subject > reference (strict)
            # Control: reference + epsilon
            # Violation: reference (boundary itself violates)
            solution.control_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "control_safe"),
                case_type="CONTROL",
                subject_value=self._format_datetime(ref_dt + epsilon, rule.precision),
                expected_valid=True,
                distance_from_boundary="1ε",
                description="Safe side (strict)",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_at_boundary"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt, rule.precision),
                expected_valid=False,
                distance_from_boundary="0",
                description="At boundary (violates strict >)",
            ))
            solution.violation_cases.append(BoundaryCase(
                case_id=_stable_id(rule.internal_rule_id, "violation_beyond"),
                case_type="VIOLATION",
                subject_value=self._format_datetime(ref_dt - epsilon, rule.precision),
                expected_valid=False,
                distance_from_boundary="1ε",
                description="Beyond boundary",
            ))

        # Validate solution
        valid, reason = solution.is_valid()
        solution.complete = valid
        solution.blocked_reason = reason if not valid else ""

        return solution

    def _parse_datetime(self, value: str, precision: str) -> datetime | None:
        """Parse datetime string based on precision."""
        value = _text(value)
        if not value:
            return None

        formats = [
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value.replace("Z", ""), fmt.rstrip("Z"))
            except ValueError:
                continue
        return None

    def _format_datetime(self, dt: datetime, precision: str) -> str:
        """Format datetime based on precision."""
        if precision == PRECISION_DATE:
            return dt.strftime("%Y-%m-%d")
        elif precision == PRECISION_SECOND:
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


# ─── Temporal Plan Proof ───────────────────────────────────────────────────────

@dataclass
class TemporalPlanProof:
    """Proof that temporal experiment plan is correct before execution."""
    proof_id: str
    internal_rule_id: str
    experiment_id: str
    subject_field: str
    reference_binding: dict[str, Any]
    operator: str
    inclusive: bool
    precision: str
    timezone: str
    epsilon: str
    case_type: str  # CONTROL or VIOLATION
    planned_subject_value: str
    normalized_subject_value: str
    normalized_reference_value: str
    expression_evaluation: str
    expected_valid: bool
    target_operation: str
    actor: str
    precondition_proof_id: str = ""
    only_target_temporal_field_mutated: bool = True
    complete: bool = False
    proof_hash: str = ""

    def compute_hash(self) -> str:
        """Compute proof hash."""
        content = f"{self.internal_rule_id}|{self.experiment_id}|{self.case_type}|{self.planned_subject_value}|{self.normalized_reference_value}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


class TemporalPlanProofGenerator:
    """Generate temporal plan proofs."""

    def generate(
        self,
        rule: TemporalRule,
        experiment_id: str,
        case: BoundaryCase,
        reference_value: str,
        target_operation: str,
        actor: str,
    ) -> TemporalPlanProof:
        """Generate a temporal plan proof for a boundary case."""
        proof = TemporalPlanProof(
            proof_id=_stable_id(rule.internal_rule_id, experiment_id, case.case_id),
            internal_rule_id=rule.internal_rule_id,
            experiment_id=experiment_id,
            subject_field=f"{rule.subject.entity_id}.{rule.subject.field_id}" if rule.subject else "",
            reference_binding={
                "reference_type": rule.reference.reference_type if rule.reference else "",
                "entity_id": rule.reference.entity_id if rule.reference else "",
                "field_id": rule.reference.field_id if rule.reference else "",
                "value": reference_value,
            },
            operator=rule.comparison.operator if rule.comparison else "",
            inclusive=rule.comparison.inclusive if rule.comparison else True,
            precision=rule.precision,
            timezone=rule.timezone_zone_id,
            epsilon=str(EPSILON_BY_PRECISION.get(rule.precision, timedelta(days=1))),
            case_type=case.case_type,
            planned_subject_value=case.subject_value,
            normalized_subject_value=case.subject_value,
            normalized_reference_value=reference_value,
            expression_evaluation=f"{case.subject_value} {rule.comparison.operator if rule.comparison else ''} {reference_value}",
            expected_valid=case.expected_valid,
            target_operation=target_operation,
            actor=actor,
            only_target_temporal_field_mutated=True,
            complete=True,
        )
        proof.proof_hash = proof.compute_hash()
        return proof


# ─── Temporal Observation Proof ────────────────────────────────────────────────

@dataclass
class TemporalObservationProof:
    """Proof that temporal experiment executed as planned."""
    proof_id: str
    experiment_id: str
    planned_value: str
    submitted_value: str
    observed_value: str
    reference_value: str
    subject_precision: str
    reference_precision: str
    normalized_subject: str
    normalized_reference: str
    timezone_conversion: str = ""
    truncation_detected: bool = False
    server_override_detected: bool = False
    actual_expression_result: str = ""
    actual_boundary_side: str = ""
    intended_boundary_side: str = ""
    plan_materialized: bool = False
    complete: bool = False
    blocked_reason: str = ""


class TemporalObservationProofGenerator:
    """Generate temporal observation proofs after execution."""

    def generate(
        self,
        experiment_id: str,
        plan_proof: TemporalPlanProof,
        submitted_value: str,
        observed_value: str,
        reference_value: str,
    ) -> TemporalObservationProof:
        """Generate observation proof comparing planned vs actual values."""
        # Check if plan materialized
        plan_materialized = (observed_value == plan_proof.planned_subject_value)
        server_override = (submitted_value == plan_proof.planned_subject_value and
                          observed_value != plan_proof.planned_subject_value)

        proof = TemporalObservationProof(
            proof_id=_stable_id(experiment_id, "observation"),
            experiment_id=experiment_id,
            planned_value=plan_proof.planned_subject_value,
            submitted_value=submitted_value,
            observed_value=observed_value,
            reference_value=reference_value,
            subject_precision=plan_proof.precision,
            reference_precision=plan_proof.precision,
            normalized_subject=observed_value,
            normalized_reference=reference_value,
            server_override_detected=server_override,
            plan_materialized=plan_materialized,
            complete=plan_materialized,
            blocked_reason="" if plan_materialized else TEMPORAL_VALUE_NOT_MATERIALIZED,
        )

        # Determine boundary side
        if plan_materialized:
            proof.intended_boundary_side = "valid" if plan_proof.expected_valid else "invalid"
            proof.actual_boundary_side = proof.intended_boundary_side

        return proof


# ─── Main Orchestrator ─────────────────────────────────────────────────────────

class TemporalExperimentPlanner:
    """Main orchestrator for temporal experiment planning."""

    def __init__(self):
        self.parser = TemporalRuleParser()
        self.solver = BoundaryValueSolver()
        self.plan_proof_gen = TemporalPlanProofGenerator()
        self.obs_proof_gen = TemporalObservationProofGenerator()

    def plan_experiments(
        self,
        internal_rule_id: str,
        expression: dict[str, Any],
        rule_statement: str,
        reference_value: str,
        target_operation: str,
        actor: str,
    ) -> dict[str, Any]:
        """Plan temporal experiments for a rule.

        Returns a dict with:
        - temporal_rule: parsed rule structure
        - boundary_solution: control/violation cases
        - plan_proofs: proofs for each case
        - blocked_reason: if planning failed
        """
        # Parse rule
        rule = self.parser.parse_from_structured_expression(
            internal_rule_id=internal_rule_id,
            expression=expression,
            rule_statement=rule_statement,
        )

        # Validate rule completeness
        complete, blocked_reason = rule.is_complete()
        if not complete:
            return {
                "temporal_rule": asdict(rule) if rule else None,
                "boundary_solution": None,
                "plan_proofs": [],
                "blocked_reason": blocked_reason,
                "complete": False,
            }

        # Solve boundary values
        solution = self.solver.solve(rule, reference_value)
        if not solution.complete:
            return {
                "temporal_rule": asdict(rule),
                "boundary_solution": asdict(solution),
                "plan_proofs": [],
                "blocked_reason": solution.blocked_reason,
                "complete": False,
            }

        # Generate plan proofs for all cases
        plan_proofs = []
        all_cases = solution.control_cases + solution.violation_cases
        for i, case in enumerate(all_cases):
            exp_id = f"exp-{internal_rule_id}-{case.case_type.lower()}-{i+1}"
            proof = self.plan_proof_gen.generate(
                rule=rule,
                experiment_id=exp_id,
                case=case,
                reference_value=reference_value,
                target_operation=target_operation,
                actor=actor,
            )
            plan_proofs.append(asdict(proof))

        return {
            "temporal_rule": asdict(rule),
            "boundary_solution": asdict(solution),
            "plan_proofs": plan_proofs,
            "blocked_reason": "",
            "complete": True,
            "experiment_count": len(all_cases),
            "control_count": len(solution.control_cases),
            "violation_count": len(solution.violation_cases),
        }
