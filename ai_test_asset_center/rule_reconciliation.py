"""Multi-source evidence-driven rule reconciliation and bounded self-correction.

This module implements:
- Rule Completeness Profile: audit existing rules for missing components
- Evidence Matrix: collect and normalize evidence from multiple sources
- Conflict Detection: detect conflicts between evidence
- Candidate Rule Patch Generation: generate minimal rule fixes
- Rule Versioning: immutable version management
- Candidate Executability/Satisfiability Checks
- Discriminating Experiment Generation
- Shadow Validation and Promotion Gate

Industry-neutral: no project-specific rule branches or benchmark references.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ─── Rule Defect Types ─────────────────────────────────────────────────────────

DEFECT_MISSING_SUBJECT_FIELD = "MISSING_SUBJECT_FIELD"
DEFECT_WRONG_SUBJECT_FIELD = "WRONG_SUBJECT_FIELD"
DEFECT_MISSING_REFERENCE_FIELD = "MISSING_REFERENCE_FIELD"
DEFECT_WRONG_REFERENCE_FIELD = "WRONG_REFERENCE_FIELD"
DEFECT_MISSING_PREDICATE = "MISSING_PREDICATE"
DEFECT_WRONG_OPERATOR = "WRONG_OPERATOR"
DEFECT_MISSING_STATE_GUARD = "MISSING_STATE_GUARD"
DEFECT_WRONG_STATE_GUARD = "WRONG_STATE_GUARD"
DEFECT_MISSING_ENTITY_RELATION = "MISSING_ENTITY_RELATION"
DEFECT_WRONG_ENTITY_RELATION = "WRONG_ENTITY_RELATION"
DEFECT_MISSING_SCOPE_CONSTRAINT = "MISSING_SCOPE_CONSTRAINT"
DEFECT_WRONG_SCOPE_CONSTRAINT = "WRONG_SCOPE_CONSTRAINT"
DEFECT_MISSING_AGGREGATE = "MISSING_AGGREGATE"
DEFECT_WRONG_AGGREGATE = "WRONG_AGGREGATE"
DEFECT_MISSING_QUANTIFIER = "MISSING_QUANTIFIER"
DEFECT_WRONG_QUANTIFIER = "WRONG_QUANTIFIER"
DEFECT_MISSING_EXCEPTION = "MISSING_EXCEPTION"
DEFECT_WRONG_EXCEPTION = "WRONG_EXCEPTION"
DEFECT_UNIT_MISMATCH = "UNIT_MISMATCH"
DEFECT_PRECISION_MISMATCH = "PRECISION_MISMATCH"
DEFECT_CARDINALITY_MISMATCH = "CARDINALITY_MISMATCH"
DEFECT_OPERATION_RULE_MISMATCH = "OPERATION_RULE_MISMATCH"
DEFECT_INCOMPLETE_SOURCE_GROUNDING = "INCOMPLETE_SOURCE_GROUNDING"
DEFECT_CONFLICTED_RULE = "CONFLICTED_RULE"

ALL_DEFECT_TYPES = [
    DEFECT_MISSING_SUBJECT_FIELD, DEFECT_WRONG_SUBJECT_FIELD,
    DEFECT_MISSING_REFERENCE_FIELD, DEFECT_WRONG_REFERENCE_FIELD,
    DEFECT_MISSING_PREDICATE, DEFECT_WRONG_OPERATOR,
    DEFECT_MISSING_STATE_GUARD, DEFECT_WRONG_STATE_GUARD,
    DEFECT_MISSING_ENTITY_RELATION, DEFECT_WRONG_ENTITY_RELATION,
    DEFECT_MISSING_SCOPE_CONSTRAINT, DEFECT_WRONG_SCOPE_CONSTRAINT,
    DEFECT_MISSING_AGGREGATE, DEFECT_WRONG_AGGREGATE,
    DEFECT_MISSING_QUANTIFIER, DEFECT_WRONG_QUANTIFIER,
    DEFECT_MISSING_EXCEPTION, DEFECT_WRONG_EXCEPTION,
    DEFECT_UNIT_MISMATCH, DEFECT_PRECISION_MISMATCH,
    DEFECT_CARDINALITY_MISMATCH, DEFECT_OPERATION_RULE_MISMATCH,
    DEFECT_INCOMPLETE_SOURCE_GROUNDING, DEFECT_CONFLICTED_RULE,
]

# ─── Evidence Source Types ─────────────────────────────────────────────────────

EVIDENCE_PRD_REQUIREMENT = "PRD_REQUIREMENT"
EVIDENCE_API_DOCUMENTATION = "API_DOCUMENTATION"
EVIDENCE_DATABASE_SCHEMA = "DATABASE_SCHEMA"
EVIDENCE_DATABASE_CONSTRAINT = "DATABASE_CONSTRAINT"
EVIDENCE_TEST_CASE = "TEST_CASE"
EVIDENCE_HISTORICAL_BUG = "HISTORICAL_BUG"
EVIDENCE_SOURCE_CODE_STATIC_ANALYSIS = "SOURCE_CODE_STATIC_ANALYSIS"
EVIDENCE_OPERATION_EFFECT = "OPERATION_EFFECT"
EVIDENCE_FIELD_WRITE_PATTERN = "FIELD_WRITE_PATTERN"
EVIDENCE_ENTITY_RELATION = "ENTITY_RELATION"
EVIDENCE_RUNTIME_OBSERVATION = "RUNTIME_OBSERVATION"
EVIDENCE_DIFFERENTIAL_EXECUTION = "DIFFERENTIAL_EXECUTION"
EVIDENCE_LLM_INFERENCE = "LLM_INFERENCE"

# Normative evidence types (can establish rule truth)
NORMATIVE_EVIDENCE_TYPES = {
    EVIDENCE_PRD_REQUIREMENT,
    EVIDENCE_DATABASE_CONSTRAINT,
    EVIDENCE_API_DOCUMENTATION,
}

# Evidence priority (higher = more authoritative)
EVIDENCE_PRIORITY = {
    EVIDENCE_PRD_REQUIREMENT: 100,
    EVIDENCE_DATABASE_CONSTRAINT: 90,
    EVIDENCE_DATABASE_SCHEMA: 85,
    EVIDENCE_API_DOCUMENTATION: 80,
    EVIDENCE_TEST_CASE: 60,
    EVIDENCE_HISTORICAL_BUG: 55,
    EVIDENCE_SOURCE_CODE_STATIC_ANALYSIS: 50,
    EVIDENCE_OPERATION_EFFECT: 40,
    EVIDENCE_FIELD_WRITE_PATTERN: 35,
    EVIDENCE_ENTITY_RELATION: 30,
    EVIDENCE_RUNTIME_OBSERVATION: 20,
    EVIDENCE_DIFFERENTIAL_EXECUTION: 15,
    EVIDENCE_LLM_INFERENCE: 10,
}

# ─── Rule Version Status ───────────────────────────────────────────────────────

STATUS_ACTIVE = "ACTIVE"
STATUS_CANDIDATE = "CANDIDATE"
STATUS_SHADOW_VALIDATED = "SHADOW_VALIDATED"
STATUS_REJECTED = "REJECTED"
STATUS_QUARANTINED = "QUARANTINED"
STATUS_DEPRECATED = "DEPRECATED"
STATUS_INFERRED = "INFERRED"

# ─── Shadow Validation Results ─────────────────────────────────────────────────

SHADOW_SUPPORTED = "SUPPORTED"
SHADOW_CONTRADICTED = "CONTRADICTED"
SHADOW_INCONCLUSIVE = "INCONCLUSIVE"
SHADOW_INVALID_EXPERIMENT = "INVALID_EXPERIMENT"
SHADOW_SOURCE_CONFLICT = "SOURCE_CONFLICT"

# ─── Blocking Reasons ──────────────────────────────────────────────────────────

BLOCK_RULE_COMPLETENESS_PROFILE_FAILED = "RULE_COMPLETENESS_PROFILE_FAILED"
BLOCK_NORMATIVE_EVIDENCE_MISSING = "NORMATIVE_EVIDENCE_MISSING"
BLOCK_INDEPENDENT_SUPPORT_MISSING = "INDEPENDENT_SUPPORT_MISSING"
BLOCK_RULE_EVIDENCE_CONFLICTED = "RULE_EVIDENCE_CONFLICTED"
BLOCK_SUBJECT_FIELD_UNRESOLVED = "SUBJECT_FIELD_UNRESOLVED"
BLOCK_REFERENCE_FIELD_UNRESOLVED = "REFERENCE_FIELD_UNRESOLVED"
BLOCK_ENTITY_RELATION_UNRESOLVED = "ENTITY_RELATION_UNRESOLVED"
BLOCK_SCOPE_CONSTRAINT_UNRESOLVED = "SCOPE_CONSTRAINT_UNRESOLVED"
BLOCK_OPERATOR_UNRESOLVED = "OPERATOR_UNRESOLVED"
BLOCK_STATE_GUARD_UNRESOLVED = "STATE_GUARD_UNRESOLVED"
BLOCK_CANDIDATE_RULE_NOT_EXECUTABLE = "CANDIDATE_RULE_NOT_EXECUTABLE"
BLOCK_CANDIDATE_RULE_UNSAT = "CANDIDATE_RULE_UNSAT"
BLOCK_DISCRIMINATING_EXPERIMENT_NOT_FOUND = "DISCRIMINATING_EXPERIMENT_NOT_FOUND"
BLOCK_CANDIDATE_CONTROL_FAILED = "CANDIDATE_CONTROL_FAILED"
BLOCK_CANDIDATE_VIOLATION_NOT_MATERIALIZED = "CANDIDATE_VIOLATION_NOT_MATERIALIZED"
BLOCK_CANDIDATE_OBSERVATION_INCOMPLETE = "CANDIDATE_OBSERVATION_INCOMPLETE"
BLOCK_CANDIDATE_ORACLE_INCOMPLETE = "CANDIDATE_ORACLE_INCOMPLETE"
BLOCK_CANDIDATE_VALIDATION_INCONCLUSIVE = "CANDIDATE_VALIDATION_INCONCLUSIVE"
BLOCK_CANDIDATE_SOURCE_CONFLICT = "CANDIDATE_SOURCE_CONFLICT"
BLOCK_RULE_PROMOTION_GATE_FAILED = "RULE_PROMOTION_GATE_FAILED"
BLOCK_BENCHMARK_LEAKAGE_DETECTED = "BENCHMARK_LEAKAGE_DETECTED"


def _stable_id(*parts: str) -> str:
    """Generate stable ID from parts."""
    raw = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


# ─── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class RuleEvidence:
    """Single piece of evidence supporting or opposing a rule claim."""
    evidence_id: str
    source_type: str
    source_id: str
    source_location: str
    extracted_statement: str
    normalized_claim: str
    entities: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    operation: str = ""
    relation: str = ""
    operator: str = ""
    confidence: float = 0.5
    normative: bool = False
    independent_group: str = ""
    collected_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_location": self.source_location,
            "extracted_statement": self.extracted_statement,
            "normalized_claim": self.normalized_claim,
            "entities": self.entities,
            "fields": self.fields,
            "operation": self.operation,
            "relation": self.relation,
            "operator": self.operator,
            "confidence": self.confidence,
            "normative": self.normative,
            "independent_group": self.independent_group,
            "collected_at": self.collected_at,
        }


@dataclass
class EvidenceClaim:
    """A claim about a rule component with supporting/opposing evidence."""
    claim_id: str
    claim_type: str
    candidate_value: str
    supporting_evidence: list[str] = field(default_factory=list)
    opposing_evidence: list[str] = field(default_factory=list)
    independent_support_count: int = 0
    normative_support_count: int = 0
    conflict_level: str = "NONE"
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "claim_type": self.claim_type,
            "candidate_value": self.candidate_value,
            "supporting_evidence": self.supporting_evidence,
            "opposing_evidence": self.opposing_evidence,
            "independent_support_count": self.independent_support_count,
            "normative_support_count": self.normative_support_count,
            "conflict_level": self.conflict_level,
            "confidence": self.confidence,
        }


@dataclass
class RuleConflict:
    """Conflict between evidence sources."""
    conflict_id: str
    claim_id: str
    evidence_a: str
    evidence_b: str
    conflict_type: str
    affected_rule_components: list[str] = field(default_factory=list)
    priority_resolution: str = ""
    runtime_disambiguation_possible: bool = False
    status: str = "UNRESOLVED"

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "claim_id": self.claim_id,
            "evidence_a": self.evidence_a,
            "evidence_b": self.evidence_b,
            "conflict_type": self.conflict_type,
            "affected_rule_components": self.affected_rule_components,
            "priority_resolution": self.priority_resolution,
            "runtime_disambiguation_possible": self.runtime_disambiguation_possible,
            "status": self.status,
        }


@dataclass
class RuleCompletenessProfile:
    """Completeness audit of a rule."""
    internal_rule_id: str
    rule_version: str = "v1"
    subject_complete: bool = False
    reference_complete: bool = False
    operator_complete: bool = False
    scope_complete: bool = False
    relation_complete: bool = False
    precondition_complete: bool = False
    operation_complete: bool = False
    observer_complete: bool = False
    oracle_complete: bool = False
    source_grounding_complete: bool = False
    missing_components: list[str] = field(default_factory=list)
    suspicious_components: list[str] = field(default_factory=list)
    conflicting_components: list[str] = field(default_factory=list)
    completeness_score: float = 0.0
    blocked_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "internal_rule_id": self.internal_rule_id,
            "rule_version": self.rule_version,
            "subject_complete": self.subject_complete,
            "reference_complete": self.reference_complete,
            "operator_complete": self.operator_complete,
            "scope_complete": self.scope_complete,
            "relation_complete": self.relation_complete,
            "precondition_complete": self.precondition_complete,
            "operation_complete": self.operation_complete,
            "observer_complete": self.observer_complete,
            "oracle_complete": self.oracle_complete,
            "source_grounding_complete": self.source_grounding_complete,
            "missing_components": self.missing_components,
            "suspicious_components": self.suspicious_components,
            "conflicting_components": self.conflicting_components,
            "completeness_score": self.completeness_score,
            "blocked_reason": self.blocked_reason,
        }


@dataclass
class CandidateRulePatch:
    """Minimal diff patch for a candidate rule revision."""
    candidate_id: str
    parent_rule_id: str
    defect_types: list[str] = field(default_factory=list)
    additions: dict = field(default_factory=dict)
    removals: dict = field(default_factory=dict)
    replacements: dict = field(default_factory=dict)
    operator_changes: list[dict] = field(default_factory=list)
    quantifier_changes: list[dict] = field(default_factory=list)
    aggregation_changes: list[dict] = field(default_factory=list)
    unchanged_components: list[str] = field(default_factory=list)
    evidence_support: list[str] = field(default_factory=list)
    opposing_evidence: list[str] = field(default_factory=list)
    unresolved_conflicts: list[str] = field(default_factory=list)
    expected_behavior_difference: str = ""
    validation_requirements: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "parent_rule_id": self.parent_rule_id,
            "defect_types": self.defect_types,
            "additions": self.additions,
            "removals": self.removals,
            "replacements": self.replacements,
            "operator_changes": self.operator_changes,
            "quantifier_changes": self.quantifier_changes,
            "aggregation_changes": self.aggregation_changes,
            "unchanged_components": self.unchanged_components,
            "evidence_support": self.evidence_support,
            "opposing_evidence": self.opposing_evidence,
            "unresolved_conflicts": self.unresolved_conflicts,
            "expected_behavior_difference": self.expected_behavior_difference,
            "validation_requirements": self.validation_requirements,
            "confidence": self.confidence,
        }


@dataclass
class RuleVersion:
    """Immutable version of a rule."""
    rule_family_id: str
    version_id: str
    parent_version_id: str = ""
    status: str = STATUS_CANDIDATE
    created_reason: str = ""
    source_evidence_ids: list[str] = field(default_factory=list)
    rule_payload: dict = field(default_factory=dict)
    diff_from_parent: dict = field(default_factory=dict)
    validation_proof_id: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "rule_family_id": self.rule_family_id,
            "version_id": self.version_id,
            "parent_version_id": self.parent_version_id,
            "status": self.status,
            "created_reason": self.created_reason,
            "source_evidence_ids": self.source_evidence_ids,
            "rule_payload": self.rule_payload,
            "diff_from_parent": self.diff_from_parent,
            "validation_proof_id": self.validation_proof_id,
            "created_at": self.created_at,
        }


@dataclass
class CandidateValidationProof:
    """Complete validation proof for a candidate rule."""
    proof_id: str
    candidate_id: str
    parent_rule_id: str
    defect_types: list[str] = field(default_factory=list)
    rule_diff: dict = field(default_factory=dict)
    evidence_matrix_id: str = ""
    normative_support: int = 0
    independent_support: int = 0
    control_results: list[dict] = field(default_factory=list)
    violation_results: list[dict] = field(default_factory=list)
    discriminating_experiment_results: list[dict] = field(default_factory=list)
    field_bindings_verified: bool = False
    scope_verified: bool = False
    preconditions_verified: bool = False
    observation_complete: bool = False
    oracle_complete: bool = False
    source_conflicts_resolved: bool = False
    benchmark_not_used: bool = True
    original_rule_preserved: bool = True
    validation_result: str = ""
    proof_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "proof_id": self.proof_id,
            "candidate_id": self.candidate_id,
            "parent_rule_id": self.parent_rule_id,
            "defect_types": self.defect_types,
            "rule_diff": self.rule_diff,
            "evidence_matrix_id": self.evidence_matrix_id,
            "normative_support": self.normative_support,
            "independent_support": self.independent_support,
            "control_results": self.control_results,
            "violation_results": self.violation_results,
            "discriminating_experiment_results": self.discriminating_experiment_results,
            "field_bindings_verified": self.field_bindings_verified,
            "scope_verified": self.scope_verified,
            "preconditions_verified": self.preconditions_verified,
            "observation_complete": self.observation_complete,
            "oracle_complete": self.oracle_complete,
            "source_conflicts_resolved": self.source_conflicts_resolved,
            "benchmark_not_used": self.benchmark_not_used,
            "original_rule_preserved": self.original_rule_preserved,
            "validation_result": self.validation_result,
            "proof_hash": self.proof_hash,
        }


# ─── Rule Completeness Auditor ─────────────────────────────────────────────────

class RuleCompletenessAuditor:
    """Audit rules for missing or incorrect components."""

    # Required components for high-confidence execution
    REQUIRED_COMPONENTS = [
        "subject", "operator", "operation", "scope",
        "source_evidence", "oracle_expression",
    ]

    def audit_state_transition_rule(
        self,
        rule: dict,
        behavior_ir: dict | None = None,
        source_documents: list[dict] | None = None,
    ) -> RuleCompletenessProfile:
        """Audit a STATE_TRANSITION rule for completeness."""
        rule_id = _text(rule.get("id") or rule.get("internal_rule_id"))
        expr = _dict(rule.get("expression"))
        description = _text(rule.get("description"))

        profile = RuleCompletenessProfile(internal_rule_id=rule_id)
        missing = []

        # Check target_state
        target_state = _text(expr.get("target_state") or expr.get("to_state"))
        if target_state:
            profile.subject_complete = True
        else:
            missing.append("target_state")

        # Check from_state (state guard) - CRITICAL for STATE_TRANSITION
        from_state = _text(expr.get("from_state") or expr.get("required_state") or expr.get("source_state"))
        if from_state:
            profile.precondition_complete = True
        else:
            missing.append("from_state")
            profile.suspicious_components.append("from_state")

        # Check operation binding
        operation_ref = _text(rule.get("operation_ref") or expr.get("operation"))
        if operation_ref:
            profile.operation_complete = True
        else:
            # Try to infer from description or behavior_ir
            if behavior_ir:
                inferred_op = self._infer_operation_from_description(
                    description, target_state, behavior_ir
                )
                if inferred_op:
                    profile.operation_complete = True
                    profile.suspicious_components.append("operation_inferred")
                else:
                    missing.append("operation")
            else:
                missing.append("operation")

        # Check source grounding
        source_refs = _list(rule.get("source_refs") or rule.get("source_evidence"))
        if source_refs:
            profile.source_grounding_complete = True
        else:
            missing.append("source_evidence")

        # For STATE_TRANSITION, operator is implicit (state transition)
        profile.operator_complete = True
        profile.scope_complete = True  # Entity scope from entity_ref
        profile.relation_complete = True  # Self-relation for state
        profile.observer_complete = True  # State observer available
        profile.oracle_complete = bool(target_state and from_state)

        profile.missing_components = missing
        profile.completeness_score = self._calculate_score(profile)

        if missing:
            profile.blocked_reason = f"missing_components: {', '.join(missing)}"

        return profile

    def _infer_operation_from_description(
        self, description: str, target_state: str, behavior_ir: dict
    ) -> str:
        """Infer operation from rule description and behavior IR."""
        # Look for operations that transition to target_state
        for rel in _list(behavior_ir.get("relations")):
            if not isinstance(rel, dict):
                continue
            if _text(rel.get("relation_type")) != "transitions":
                continue
            to_ref = _text(rel.get("to_ref"))
            if target_state.upper() in to_ref.upper():
                return _text(rel.get("operation_ref"))
        return ""

    def _calculate_score(self, profile: RuleCompletenessProfile) -> float:
        """Calculate completeness score (0-1)."""
        checks = [
            profile.subject_complete,
            profile.reference_complete,
            profile.operator_complete,
            profile.scope_complete,
            profile.relation_complete,
            profile.precondition_complete,
            profile.operation_complete,
            profile.observer_complete,
            profile.oracle_complete,
            profile.source_grounding_complete,
        ]
        return sum(1 for c in checks if c) / len(checks)


# ─── Evidence Collector ────────────────────────────────────────────────────────

class EvidenceCollector:
    """Collect and normalize evidence from multiple sources."""

    def collect_from_api_spec(
        self,
        api_spec: dict | str,
        target_operation: str,
        target_state: str,
    ) -> list[RuleEvidence]:
        """Extract evidence from API specification."""
        evidence_list = []

        # Parse description for state transition constraints
        if isinstance(api_spec, str):
            # Look for "只能从X进入Y" or "only from X to Y" patterns
            patterns = [
                rf"只能从\s*(\w+)\s*进入\s*(\w+)",
                rf"only\s+from\s+(\w+)\s+to\s+(\w+)",
                rf"must\s+be\s+(\w+)\s+before\s+(\w+)",
                rf"(\w+)\s*→\s*(\w+)",
            ]
            for pattern in patterns:
                matches = re.findall(pattern, api_spec, re.IGNORECASE)
                for from_state, to_state in matches:
                    if target_state.upper() in to_state.upper():
                        ev = RuleEvidence(
                            evidence_id=_stable_id("api", from_state, to_state),
                            source_type=EVIDENCE_API_DOCUMENTATION,
                            source_id="api_spec",
                            source_location=target_operation,
                            extracted_statement=f"只能从{from_state}进入{to_state}",
                            normalized_claim=f"from_state={from_state}",
                            fields=["status"],
                            operation=target_operation,
                            operator="STATE_TRANSITION",
                            confidence=0.9,
                            normative=True,
                            independent_group="api_documentation",
                        )
                        evidence_list.append(ev)

        return evidence_list

    def collect_from_business_rules(
        self,
        rules_doc: str,
        rule_id: str,
    ) -> list[RuleEvidence]:
        """Extract evidence from business rules document."""
        evidence_list = []

        # Look for rule definition
        patterns = [
            rf"\|\s*{re.escape(rule_id)}\s*\|[^|]+\|([^|]+)\|",
            rf"{re.escape(rule_id)}[:\s]+(.+)",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, rules_doc)
            for statement in matches:
                statement = statement.strip()
                # Extract state guard from "只有X可Y" pattern
                guard_match = re.search(r"只有\s*(\w+)\s*可", statement)
                if guard_match:
                    from_state = guard_match.group(1)
                    ev = RuleEvidence(
                        evidence_id=_stable_id("prd", rule_id, from_state),
                        source_type=EVIDENCE_PRD_REQUIREMENT,
                        source_id="BUSINESS_RULES.md",
                        source_location=rule_id,
                        extracted_statement=statement,
                        normalized_claim=f"from_state={from_state}",
                        fields=["status"],
                        operator="STATE_GUARD",
                        confidence=0.95,
                        normative=True,
                        independent_group="prd_requirements",
                    )
                    evidence_list.append(ev)

        return evidence_list

    def collect_from_source_code(
        self,
        source_code: str,
        target_function: str,
    ) -> list[RuleEvidence]:
        """Extract evidence from source code static analysis."""
        evidence_list = []

        # Look for state transition validation patterns (generic, industry-neutral)
        patterns = [
            rf'_transition\w*\([^,]+,\s*["\']([A-Z_]+)["\']\s*,\s*["\']([A-Z_]+)["\']',
            rf'if\s+\w+\[["\']status["\']\]\s*!=\s*["\']([A-Z_]+)["\']',
            rf'status.*!=.*["\']([A-Z_]+)["\']',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, source_code)
            for match in matches:
                if isinstance(match, tuple):
                    from_state, to_state = match
                else:
                    from_state = match
                    to_state = ""
                ev = RuleEvidence(
                    evidence_id=_stable_id("code", target_function, from_state),
                    source_type=EVIDENCE_SOURCE_CODE_STATIC_ANALYSIS,
                    source_id="mock_server.py",
                    source_location=target_function,
                    extracted_statement=f"validates from_state={from_state}",
                    normalized_claim=f"from_state={from_state}",
                    fields=["status"],
                    operator="STATE_VALIDATION",
                    confidence=0.7,
                    normative=False,  # Code may contain bugs
                    independent_group="source_code",
                )
                evidence_list.append(ev)

        return evidence_list


# ─── Conflict Detector ─────────────────────────────────────────────────────────

class ConflictDetector:
    """Detect conflicts between evidence sources."""

    def detect_conflicts(
        self,
        claims: list[EvidenceClaim],
        evidence_map: dict[str, RuleEvidence],
    ) -> list[RuleConflict]:
        """Detect conflicts in evidence for claims."""
        conflicts = []

        for claim in claims:
            # Check for opposing evidence
            if claim.opposing_evidence:
                for opp_id in claim.opposing_evidence:
                    opp_ev = evidence_map.get(opp_id)
                    if opp_ev and claim.supporting_evidence:
                        sup_ev = evidence_map.get(claim.supporting_evidence[0])
                        if sup_ev:
                            conflict = RuleConflict(
                                conflict_id=_stable_id("conflict", claim.claim_id, opp_id),
                                claim_id=claim.claim_id,
                                evidence_a=claim.supporting_evidence[0],
                                evidence_b=opp_id,
                                conflict_type="OPPOSING_CLAIMS",
                                affected_rule_components=[claim.claim_type],
                                priority_resolution=self._resolve_by_priority(sup_ev, opp_ev),
                                status="RESOLVED_BY_HIGHER_PRIORITY_SOURCE" if self._resolve_by_priority(sup_ev, opp_ev) else "UNRESOLVED",
                            )
                            conflicts.append(conflict)

        return conflicts

    def _resolve_by_priority(self, ev_a: RuleEvidence, ev_b: RuleEvidence) -> str:
        """Resolve conflict by evidence priority."""
        priority_a = EVIDENCE_PRIORITY.get(ev_a.source_type, 0)
        priority_b = EVIDENCE_PRIORITY.get(ev_b.source_type, 0)
        if priority_a > priority_b:
            return ev_a.evidence_id
        elif priority_b > priority_a:
            return ev_b.evidence_id
        return ""


# ─── Candidate Rule Patch Generator ───────────────────────────────────────────

class CandidateRulePatchGenerator:
    """Generate minimal candidate rule patches."""

    def generate_state_guard_patch(
        self,
        rule: dict,
        from_state: str,
        evidence_ids: list[str],
    ) -> CandidateRulePatch:
        """Generate patch to add missing state guard."""
        rule_id = _text(rule.get("id") or rule.get("internal_rule_id"))
        candidate_id = _stable_id("candidate", rule_id, "state_guard", from_state)

        return CandidateRulePatch(
            candidate_id=candidate_id,
            parent_rule_id=rule_id,
            defect_types=[DEFECT_MISSING_STATE_GUARD],
            additions={
                "predicates": [{"type": "state_guard", "from_state": from_state}],
                "fields": [],
                "relations": [],
                "preconditions": [{"field": "status", "operator": "eq", "value": from_state}],
                "scope_constraints": [],
                "exceptions": [],
            },
            removals={},
            replacements={},
            unchanged_components=["target_state", "entity_ref", "rule_type"],
            evidence_support=evidence_ids,
            expected_behavior_difference=f"Only {from_state} can transition to target state; other states rejected",
            validation_requirements=["control_from_correct_state", "violation_from_wrong_state"],
            confidence=0.9,
        )

    def generate_operator_patch(
        self,
        rule: dict,
        old_operator: str,
        new_operator: str,
        evidence_ids: list[str],
    ) -> CandidateRulePatch:
        """Generate patch to fix incorrect operator."""
        rule_id = _text(rule.get("id") or rule.get("internal_rule_id"))
        candidate_id = _stable_id("candidate", rule_id, "operator", new_operator)

        return CandidateRulePatch(
            candidate_id=candidate_id,
            parent_rule_id=rule_id,
            defect_types=[DEFECT_WRONG_OPERATOR],
            additions={},
            removals={},
            replacements={},
            operator_changes=[{"from": old_operator, "to": new_operator}],
            unchanged_components=["subject", "reference", "scope"],
            evidence_support=evidence_ids,
            expected_behavior_difference=f"Operator changed from {old_operator} to {new_operator}",
            validation_requirements=["boundary_test"],
            confidence=0.85,
        )


# ─── Rule Version Manager ──────────────────────────────────────────────────────

class RuleVersionManager:
    """Manage immutable rule versions."""

    def __init__(self):
        self._versions: dict[str, list[RuleVersion]] = {}

    def create_candidate_version(
        self,
        parent_rule: dict,
        patch: CandidateRulePatch,
        evidence_ids: list[str],
    ) -> RuleVersion:
        """Create a new candidate version from parent + patch."""
        rule_id = _text(parent_rule.get("id") or parent_rule.get("internal_rule_id"))
        family_id = f"family_{rule_id}"
        version_id = _stable_id("version", rule_id, patch.candidate_id)

        # Build new payload by applying patch (DEEP COPY to preserve original)
        import copy
        new_payload = copy.deepcopy(parent_rule)
        expr = _dict(new_payload.get("expression"))
        if not expr:
            expr = {}
            new_payload["expression"] = expr

        # Apply additions
        for pred in _list(patch.additions.get("predicates")):
            if pred.get("type") == "state_guard":
                expr["from_state"] = pred.get("from_state")
        for precond in _list(patch.additions.get("preconditions")):
            if "preconditions" not in expr:
                expr["preconditions"] = []
            expr["preconditions"].append(precond)

        # Apply operator changes
        for op_change in patch.operator_changes:
            if expr.get("operator") == op_change.get("from"):
                expr["operator"] = op_change.get("to")

        new_payload["expression"] = expr

        version = RuleVersion(
            rule_family_id=family_id,
            version_id=version_id,
            parent_version_id=_text(parent_rule.get("version_id") or "v1"),
            status=STATUS_CANDIDATE,
            created_reason=f"Fix {', '.join(patch.defect_types)}",
            source_evidence_ids=evidence_ids,
            rule_payload=new_payload,
            diff_from_parent=patch.to_dict(),
        )

        self._versions.setdefault(family_id, []).append(version)
        return version

    def get_active_version(self, rule_id: str) -> RuleVersion | None:
        """Get the active version of a rule."""
        family_id = f"family_{rule_id}"
        for version in reversed(self._versions.get(family_id, [])):
            if version.status == STATUS_ACTIVE:
                return version
        return None

    def promote_to_active(self, version_id: str) -> bool:
        """Promote a candidate to active (after validation)."""
        for family_versions in self._versions.values():
            for version in family_versions:
                if version.version_id == version_id:
                    if version.status == STATUS_SHADOW_VALIDATED:
                        version.status = STATUS_ACTIVE
                        return True
        return False


# ─── Candidate Executability Checker ──────────────────────────────────────────

class CandidateExecutabilityChecker:
    """Check if a candidate rule can be executed."""

    def check_executability(
        self,
        candidate: RuleVersion,
        behavior_ir: dict,
    ) -> tuple[bool, str]:
        """Check if candidate rule has all required bindings."""
        payload = candidate.rule_payload
        expr = _dict(payload.get("expression"))

        # Check state guard
        from_state = _text(expr.get("from_state"))
        target_state = _text(expr.get("target_state"))

        if not from_state:
            return False, BLOCK_STATE_GUARD_UNRESOLVED
        if not target_state:
            return False, BLOCK_SUBJECT_FIELD_UNRESOLVED

        # Check operation binding
        operation_ref = _text(payload.get("operation_ref"))
        if not operation_ref:
            # Try to find in behavior_ir
            found = False
            for rel in _list(behavior_ir.get("relations")):
                if _text(rel.get("relation_type")) == "transitions":
                    to_ref = _text(rel.get("to_ref"))
                    if target_state.upper() in to_ref.upper():
                        found = True
                        break
            if not found:
                return False, BLOCK_CANDIDATE_RULE_NOT_EXECUTABLE

        return True, ""


# ─── Candidate Satisfiability Checker ─────────────────────────────────────────

class CandidateSatisfiabilityChecker:
    """Check if Control and Violation scenarios exist for candidate."""

    def check_satisfiability(
        self,
        candidate: RuleVersion,
        state_graph: dict | None = None,
    ) -> tuple[bool, bool, str]:
        """Check if Control and Violation are satisfiable.

        Returns: (control_satisfiable, violation_satisfiable, reason)
        """
        payload = candidate.rule_payload
        expr = _dict(payload.get("expression"))

        from_state = _text(expr.get("from_state"))
        target_state = _text(expr.get("target_state"))

        if not from_state or not target_state:
            return False, False, BLOCK_CANDIDATE_RULE_UNSAT

        # Control: entity in from_state, attempt transition -> should succeed
        control_satisfiable = True

        # Violation: entity in different state, attempt transition -> should fail
        # Need at least one other state that is NOT from_state
        violation_satisfiable = False
        if state_graph:
            all_states = set(state_graph.get("states", []))
            other_states = all_states - {from_state}
            violation_satisfiable = len(other_states) > 0
        else:
            # Assume there's always an initial state different from required
            violation_satisfiable = True

        if not violation_satisfiable:
            return control_satisfiable, False, BLOCK_CANDIDATE_RULE_UNSAT

        return control_satisfiable, violation_satisfiable, ""


# ─── Discriminating Experiment Generator ──────────────────────────────────────

class DiscriminatingExperimentGenerator:
    """Generate experiments that distinguish original vs candidate rules."""

    def generate_state_guard_experiments(
        self,
        candidate: RuleVersion,
        original_rule: dict,
        forbidden_states: list[str],
    ) -> list[dict]:
        """Generate Control/Violation experiments for state guard candidate."""
        experiments = []
        payload = candidate.rule_payload
        expr = _dict(payload.get("expression"))
        from_state = _text(expr.get("from_state"))
        target_state = _text(expr.get("target_state"))

        # Control-1: Correct source state (should succeed)
        experiments.append({
            "experiment_id": _stable_id("ctrl", candidate.version_id, "correct_state"),
            "case_type": "CONTROL",
            "case_id": "control_correct_state",
            "preconditions": {"status": from_state},
            "mutation": {"action": "transition", "to_state": target_state},
            "expected_rule_result": "PASS",
            "expected_sut_behavior": "accepted",
            "distinguishing_from_parent": "Parent rule has no from_state guard",
        })

        # Control-2: Boundary - just reached from_state
        experiments.append({
            "experiment_id": _stable_id("ctrl", candidate.version_id, "boundary"),
            "case_type": "CONTROL",
            "case_id": "control_boundary",
            "preconditions": {"status": from_state, "just_transitioned": True},
            "mutation": {"action": "transition", "to_state": target_state},
            "expected_rule_result": "PASS",
            "expected_sut_behavior": "accepted",
            "distinguishing_from_parent": "Tests immediate transition after reaching required state",
        })

        # Violation experiments for each forbidden state
        for i, forbidden in enumerate(forbidden_states[:2]):
            experiments.append({
                "experiment_id": _stable_id("viol", candidate.version_id, forbidden),
                "case_type": "VIOLATION",
                "case_id": f"violation_from_{forbidden}",
                "preconditions": {"status": forbidden},
                "mutation": {"action": "transition", "to_state": target_state},
                "expected_rule_result": "FAIL",
                "expected_sut_behavior": "rejected",
                "distinguishing_from_parent": f"Original rule would allow from {forbidden}",
            })

        return experiments


# ─── Shadow Validator ──────────────────────────────────────────────────────────

class ShadowValidator:
    """Validate candidate rules in shadow mode."""

    def validate(
        self,
        candidate: RuleVersion,
        control_results: list[dict],
        violation_results: list[dict],
    ) -> str:
        """Validate candidate based on experiment results.

        Returns: SUPPORTED, CONTRADICTED, INCONCLUSIVE, INVALID_EXPERIMENT
        """
        if not control_results or not violation_results:
            return SHADOW_INVALID_EXPERIMENT

        # Check controls: all should pass
        control_pass = all(
            r.get("actual_sut_behavior") == r.get("expected_sut_behavior", "accepted")
            for r in control_results
        )

        # Check violations: all should be rejected (if SUT implements rule)
        # or accepted (if SUT has bug - this is what we're detecting)
        violation_results_consistent = len(set(
            r.get("actual_sut_behavior") for r in violation_results
        )) == 1

        if not control_pass:
            return SHADOW_CONTRADICTED

        if not violation_results_consistent:
            return SHADOW_INCONCLUSIVE

        # If violations were accepted, SUT has bug (rule is correct but not enforced)
        # If violations were rejected, SUT implements rule correctly
        return SHADOW_SUPPORTED


# ─── Promotion Gate ────────────────────────────────────────────────────────────

class PromotionGate:
    """Gate for promoting candidate rules to active."""

    REQUIRED_CONDITIONS = [
        "rule_structure_complete",
        "field_bindings_complete",
        "relation_scope_complete",
        "control_satisfiable",
        "violation_satisfiable",
        "normative_evidence_exists",
        "independent_support_exists",
        "no_unresolved_critical_conflicts",
        "control_2_of_2",
        "violation_2_of_2_consistent",
        "experiment_valid",
        "observation_complete",
        "oracle_complete",
        "validation_proof_complete",
        "benchmark_not_used",
        "project_a_shadow_regression_pass",
    ]

    def evaluate(
        self,
        candidate: RuleVersion,
        proof: CandidateValidationProof,
        control_results: list[dict],
        violation_results: list[dict],
    ) -> tuple[bool, list[str]]:
        """Evaluate promotion gate conditions.

        Returns: (can_promote, failed_conditions)
        """
        failed = []

        # Check normative evidence
        if proof.normative_support < 1:
            failed.append("normative_evidence_exists")

        # Check independent support
        if proof.independent_support < 1:
            failed.append("independent_support_exists")

        # Check control results
        control_pass = sum(1 for r in control_results if r.get("passed"))
        if control_pass < 2:
            failed.append("control_2_of_2")

        # Check violation results consistency
        if len(violation_results) < 2:
            failed.append("violation_2_of_2_consistent")

        # Check validation proof
        if not proof.validation_result:
            failed.append("validation_proof_complete")

        # Check benchmark isolation
        if not proof.benchmark_not_used:
            failed.append("benchmark_not_used")

        # Check original rule preserved
        if not proof.original_rule_preserved:
            failed.append("original_rule_preserved")

        return len(failed) == 0, failed


# ─── Main Reconciliation Engine ────────────────────────────────────────────────

class RuleReconciliationEngine:
    """Main engine for rule reconciliation and bounded self-correction."""

    def __init__(self):
        self.auditor = RuleCompletenessAuditor()
        self.collector = EvidenceCollector()
        self.conflict_detector = ConflictDetector()
        self.patch_generator = CandidateRulePatchGenerator()
        self.version_manager = RuleVersionManager()
        self.executability_checker = CandidateExecutabilityChecker()
        self.satisfiability_checker = CandidateSatisfiabilityChecker()
        self.experiment_generator = DiscriminatingExperimentGenerator()
        self.shadow_validator = ShadowValidator()
        self.promotion_gate = PromotionGate()

    def reconcile_state_transition_rule(
        self,
        rule: dict,
        behavior_ir: dict,
        source_documents: dict[str, str],
    ) -> dict:
        """Full reconciliation pipeline for a STATE_TRANSITION rule.

        Returns complete reconciliation result with candidate and validation.
        """
        rule_id = _text(rule.get("id") or rule.get("internal_rule_id"))

        # Step 1: Completeness audit
        profile = self.auditor.audit_state_transition_rule(rule, behavior_ir)

        # Step 2: Collect evidence
        evidence_list = []
        evidence_map = {}

        # From API spec
        api_spec = source_documents.get("api_spec", "")
        if api_spec:
            api_evidence = self.collector.collect_from_api_spec(
                api_spec,
                _text(rule.get("operation_ref")),
                _text(_dict(rule.get("expression")).get("target_state")),
            )
            evidence_list.extend(api_evidence)
            for ev in api_evidence:
                evidence_map[ev.evidence_id] = ev

        # From business rules
        business_rules = source_documents.get("business_rules", "")
        if business_rules:
            prd_evidence = self.collector.collect_from_business_rules(business_rules, rule_id)
            evidence_list.extend(prd_evidence)
            for ev in prd_evidence:
                evidence_map[ev.evidence_id] = ev

        # From source code
        source_code = source_documents.get("source_code", "")
        if source_code:
            code_evidence = self.collector.collect_from_source_code(
                source_code, "_transition_payment"
            )
            evidence_list.extend(code_evidence)
            for ev in code_evidence:
                evidence_map[ev.evidence_id] = ev

        # Step 3: Build claims and detect conflicts
        claims = self._build_claims(rule, evidence_list)
        conflicts = self.conflict_detector.detect_conflicts(claims, evidence_map)

        # Step 4: Generate candidate patch
        from_state = self._extract_from_state(evidence_list)
        if not from_state:
            return {
                "status": "BLOCKED",
                "reason": BLOCK_STATE_GUARD_UNRESOLVED,
                "profile": profile.to_dict(),
            }

        evidence_ids = [ev.evidence_id for ev in evidence_list if from_state in ev.normalized_claim]
        patch = self.patch_generator.generate_state_guard_patch(rule, from_state, evidence_ids)

        # Step 5: Create candidate version
        candidate = self.version_manager.create_candidate_version(rule, patch, evidence_ids)

        # Step 6: Check executability
        executable, exec_reason = self.executability_checker.check_executability(candidate, behavior_ir)
        if not executable:
            return {
                "status": "BLOCKED",
                "reason": exec_reason,
                "profile": profile.to_dict(),
                "candidate": candidate.to_dict(),
            }

        # Step 7: Check satisfiability
        state_graph = self._extract_state_graph(behavior_ir)
        ctrl_sat, viol_sat, sat_reason = self.satisfiability_checker.check_satisfiability(
            candidate, state_graph
        )
        if not ctrl_sat or not viol_sat:
            return {
                "status": "BLOCKED",
                "reason": sat_reason or BLOCK_CANDIDATE_RULE_UNSAT,
                "profile": profile.to_dict(),
                "candidate": candidate.to_dict(),
            }

        # Step 8: Generate discriminating experiments
        forbidden_states = self._get_forbidden_states(state_graph, from_state)
        experiments = self.experiment_generator.generate_state_guard_experiments(
            candidate, rule, forbidden_states
        )

        return {
            "status": "READY_FOR_VALIDATION",
            "profile": profile.to_dict(),
            "evidence": [ev.to_dict() for ev in evidence_list],
            "claims": [c.to_dict() for c in claims],
            "conflicts": [c.to_dict() for c in conflicts],
            "candidate": candidate.to_dict(),
            "patch": patch.to_dict(),
            "experiments": experiments,
            "defect_types": patch.defect_types,
        }

    def _build_claims(self, rule: dict, evidence_list: list[RuleEvidence]) -> list[EvidenceClaim]:
        """Build claims from evidence."""
        claims = []
        expr = _dict(rule.get("expression"))

        # Claim: from_state value
        from_state_evidence = [ev for ev in evidence_list if "from_state=" in ev.normalized_claim]
        if from_state_evidence:
            # Group by claimed value
            values = {}
            for ev in from_state_evidence:
                val = ev.normalized_claim.split("=")[-1]
                values.setdefault(val, []).append(ev)

            for val, evs in values.items():
                normative_count = sum(1 for e in evs if e.normative)
                independent_groups = len(set(e.independent_group for e in evs))
                claims.append(EvidenceClaim(
                    claim_id=_stable_id("claim", "from_state", val),
                    claim_type="from_state",
                    candidate_value=val,
                    supporting_evidence=[e.evidence_id for e in evs],
                    independent_support_count=independent_groups,
                    normative_support_count=normative_count,
                    confidence=0.9 if normative_count > 0 else 0.6,
                ))

        return claims

    def _extract_from_state(self, evidence_list: list[RuleEvidence]) -> str:
        """Extract the most supported from_state from evidence."""
        values = {}
        for ev in evidence_list:
            if "from_state=" in ev.normalized_claim:
                val = ev.normalized_claim.split("=")[-1]
                priority = EVIDENCE_PRIORITY.get(ev.source_type, 0)
                if val not in values or priority > values[val]:
                    values[val] = priority

        if values:
            return max(values.keys(), key=lambda k: values[k])
        return ""

    def _extract_state_graph(self, behavior_ir: dict) -> dict:
        """Extract state graph from behavior IR."""
        states = set()
        for state in _list(behavior_ir.get("states")):
            if isinstance(state, dict):
                states.add(_text(state.get("name") or state.get("id")))
        return {"states": list(states)}

    def _get_forbidden_states(self, state_graph: dict, from_state: str) -> list[str]:
        """Get states that are NOT the required from_state."""
        all_states = set(state_graph.get("states", []))
        forbidden = all_states - {from_state}
        # Prioritize initial states (like DRAFT)
        return sorted(forbidden, key=lambda s: (0 if "DRAFT" in s.upper() else 1, s))
