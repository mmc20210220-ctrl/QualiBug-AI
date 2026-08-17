"""Candidate validation and promotion state machine.

Candidates are suggestions, never business facts. Promotion requires an explicit
candidate-kind contract, source-identifiable evidence and a fail-closed authority
decision. The registry is the single extension point for entity/field/relation/
state/actor candidates and for source-derived rule candidates; callers must not
build a second promotion state machine beside this module.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

CandidateValidator = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
CandidatePromoter = Callable[[dict[str, Any]], dict[str, Any]]

__all__ = [
    "validate_and_promote_candidates",
    "candidates_to_behavior_ir_entries",
    "project_validated_candidates_to_asset_spaces",
    "promote_validated_candidates",
    "register_candidate_kind",
    "registered_candidate_kinds",
    "CandidateValidationReceipt",
    "CANDIDATE_STATES",
]

CANDIDATE_STATES = {
    "CANDIDATE",
    "PENDING_VALIDATION",
    "VALIDATED",
    "CONFLICTED",
    "STALE",
    "REJECTED",
}
_TERMINAL_BUCKET_BY_STATUS = {
    "VALIDATED": "validated",
    "CONFLICTED": "conflicted",
    "PENDING_VALIDATION": "pending",
    "REJECTED": "rejected",
    "STALE": "stale",
}
_GENERIC_KINDS = frozenset({"entity", "field", "relation", "state", "actor"})
_TYPED_BINDING_FIELDS = {
    "field": ("owner",),
    "state": ("owner",),
    "relation": ("source_entity", "target_entity"),
}
_RULE_LOGICAL_FORM_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_RULE_FORMAL_AUTHORITIES = frozenset(
    {
        "formal_constraint",
        "database_constraint",
        "api_schema_constraint",
        "operator_approved",
        "runtime_contract_approved",
    }
)
_RULE_READY_BINDING_STATES = frozenset(
    {"READY", "READY_FOR_IR_BINDING", "READY_AUTHORITATIVE_OPERATION_BOUND"}
)
_RULE_ACCEPTED_SCOPE_STATES = frozenset({"RESOLVED", "NOT_APPLICABLE"})
_INVALID_RULE_SOURCE_IDS = frozenset({"unknown", "unspecified", "*"})
_RULE_RUNTIME_KIND_BY_LOGICAL_FORM = {
    "REQUIRED_FIELD": "validation_required",
    "UNIQUENESS": "validation_uniqueness",
    "DOMAIN_MEMBERSHIP": "validation_domain_membership",
    "VALUE_BOUND": "validation_value_bound",
    "REFERENTIAL_INTEGRITY": "data_integrity_reference",
    "PERMISSION_BOUNDARY": "authorization",
    "CARDINALITY": "cardinality",
    "CONSERVATION_EQUATION": "conservation",
    "STATE_PRECONDITION": "state_precondition",
    "FORBIDDEN_STATE_TRANSITION": "forbidden_state_transition",
    "TEMPORAL_WINDOW": "temporal_window",
    "IDEMPOTENCY": "idempotency",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _source_identity(row: dict[str, Any], *, prefix: str = "") -> str:
    source_id = _text(row.get("source_id"))
    if source_id:
        return source_id
    locator = _text(row.get("source_locator"))
    return f"{prefix}:{locator}" if locator else ""


def _candidate_source_ids(candidate: dict[str, Any]) -> list[str]:
    """Compatibility source inventory for generic semantic candidates.

    Generic candidates historically permitted a locator-backed identity. Rule authority
    is stricter and uses ``_explicit_rule_source_ids`` below, so a locator without a
    source document can never authorize a business rule.
    """
    source_ids: set[str] = set()
    direct = _source_identity(candidate, prefix="candidate")
    if direct:
        source_ids.add(direct)
    for field in ("source_refs", "supporting_evidence"):
        for row in _list(candidate.get(field)):
            if not isinstance(row, dict):
                continue
            source = _source_identity(row, prefix=field)
            if source:
                source_ids.add(source)
    for value in _list(candidate.get("supporting_source_ids")):
        if _text(value):
            source_ids.add(_text(value))
    return sorted(source_ids)


def _explicit_rule_source_ids(candidate: dict[str, Any]) -> list[str]:
    """Return only explicit document/contract source identities for rule authority."""
    source_ids: set[str] = set()
    for value in [candidate.get("source_id"), *_list(candidate.get("supporting_source_ids"))]:
        source_id = _text(value)
        if source_id and source_id.lower() not in _INVALID_RULE_SOURCE_IDS:
            source_ids.add(source_id)
    for field in ("source_refs", "supporting_evidence"):
        for row in _list(candidate.get(field)):
            if not isinstance(row, dict):
                continue
            source_id = _text(row.get("source_id"))
            if source_id and source_id.lower() not in _INVALID_RULE_SOURCE_IDS:
                source_ids.add(source_id)
    return sorted(source_ids)


def _independent_match_sources(
    name: str,
    rows: Iterable[dict[str, Any]],
    *,
    text_fields: tuple[str, ...],
    candidate_source: str,
    exact_fields: tuple[str, ...] = (),
) -> list[str]:
    name_lower = name.lower()
    sources: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        exact_match = any(
            _text(row.get(field)).lower() == name_lower for field in exact_fields
        )
        text_match = any(
            name_lower in _text(row.get(field)).lower() for field in text_fields
        )
        if not exact_match and not text_match:
            continue
        source = _source_identity(row, prefix=f"asset-{index}")
        if source and source != candidate_source:
            sources.add(source)
    return sorted(sources)


def _rule_validation(candidate: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Validate a rule candidate without converting confidence into authority.

    A rule is accepted only when every hard gate passes. Formal schema/contract
    constraints may be authoritative from one source; every other derivation needs
    at least two independent source identities. Industry prior and runtime convention
    are proposal evidence only and can never self-promote.
    """
    del context
    logical_form = _text(candidate.get("logical_form")).upper()
    if not _RULE_LOGICAL_FORM_RE.fullmatch(logical_form):
        return {"status": "REJECTED", "reason": "rule_logical_form_invalid"}
    if not _text(candidate.get("statement") or candidate.get("name")):
        return {"status": "REJECTED", "reason": "rule_statement_missing"}
    supporting_fact_refs = sorted(
        {_text(value) for value in _list(candidate.get("supporting_fact_refs")) if _text(value)}
    )
    if not supporting_fact_refs:
        return {"status": "REJECTED", "reason": "rule_supporting_fact_refs_missing"}
    source_ids = _explicit_rule_source_ids(candidate)
    if not source_ids:
        return {"status": "REJECTED", "reason": "rule_source_identity_missing"}

    contradicting = [
        value for value in _list(candidate.get("contradicting_fact_refs")) if _text(value)
    ]
    if contradicting:
        return {
            "status": "CONFLICTED",
            "reason": "rule_counterevidence_present",
            "conflict_sources": sorted({_text(value) for value in contradicting}),
        }

    source_authority = _text(candidate.get("source_authority")).lower()
    derivation_basis = {
        _text(value).lower()
        for value in _list(candidate.get("derivation_basis"))
        if _text(value)
    }
    formal_authority = source_authority in _RULE_FORMAL_AUTHORITIES
    independent_source_gate = formal_authority or len(source_ids) >= 2
    industry_only = bool(derivation_basis) and derivation_basis <= {
        "industry_prior",
        "industry_inference",
    }
    runtime_only = bool(derivation_basis) and derivation_basis <= {
        "runtime_observation",
        "runtime_convention",
    }

    falsifiability = _text(candidate.get("falsifiability")).upper()
    binding_readiness = _text(candidate.get("binding_readiness")).upper()
    scope_status = _text(candidate.get("scope_status") or "RESOLVED").upper()
    exception_status = _text(candidate.get("exception_status") or "RESOLVED").upper()
    counterexample_plan = _dict(candidate.get("counterexample_plan"))

    gate_results = {
        "source_authority_pass": bool(formal_authority or len(source_ids) >= 2),
        "source_independence_pass": bool(independent_source_gate),
        "not_industry_prior_only": not industry_only,
        "not_runtime_convention_only": not runtime_only,
        "falsifiability_pass": falsifiability == "EVALUABLE",
        "counterexample_plan_pass": bool(counterexample_plan),
        "binding_readiness_pass": binding_readiness in _RULE_READY_BINDING_STATES,
        "scope_completeness_pass": scope_status in _RULE_ACCEPTED_SCOPE_STATES,
        "exception_completeness_pass": exception_status in _RULE_ACCEPTED_SCOPE_STATES,
        "contradiction_absent": True,
    }
    failed = [name for name, passed in gate_results.items() if not passed]
    evidence = ["formal_source_constraint"] if formal_authority else ["independent_sources"]
    details = {"rule_support_sources": source_ids}
    if failed:
        return {
            "status": "PENDING_VALIDATION",
            "reason": "rule_authority_gate_incomplete",
            "pending_gates": failed,
            "promotion_evidence": evidence,
            "promotion_evidence_sources": details,
            "authority_gate": gate_results,
        }
    return {
        "status": "VALIDATED",
        "promotion_evidence": evidence,
        "promotion_evidence_sources": details,
        "authority_gate": gate_results,
        "confidence": min(1.0, max(0.0, float(candidate.get("confidence") or 0.8))),
    }


def _runtime_operand(value: Any) -> dict[str, Any]:
    row = dict(value) if isinstance(value, dict) else {"value": value}
    field_ref = _text(row.get("field_ref"))
    if field_ref and not _text(row.get("field_id") or row.get("field")):
        row["field_id"] = field_ref
    target_ref = _text(row.get("target_ref"))
    if target_ref and not _text(row.get("entity_ref")):
        row["entity_ref"] = target_ref
    return row


def _rule_runtime_projection(
    candidate: dict[str, Any],
    structured_expression: dict[str, Any],
) -> dict[str, Any]:
    """Adapt one accepted rule into the existing rule-library runtime contract.

    ``structured_expression`` is the lossless authority representation. The existing
    Behavior IR compiler consumes top-level ``kind/operator/operands/equation`` fields,
    so this adapter projects the same semantics into those compatibility fields instead
    of letting accepted rules collapse back to prose.
    """
    logical_form = _text(
        structured_expression.get("logical_form") or candidate.get("logical_form")
    ).upper()
    consequent = _dict(structured_expression.get("consequent"))
    risk_type = _text(candidate.get("risk_type")).lower()
    kind = _RULE_RUNTIME_KIND_BY_LOGICAL_FORM.get(
        logical_form,
        risk_type or logical_form.lower() or "business_rule",
    )
    if logical_form == "PERMISSION_BOUNDARY" and risk_type:
        kind = risk_type

    raw_operands = _list(candidate.get("operands")) or _list(
        structured_expression.get("operands")
    )
    operands = [_runtime_operand(value) for value in raw_operands]
    equation: dict[str, Any] = _dict(candidate.get("equation"))
    operator = _text(
        candidate.get("operator")
        or structured_expression.get("operator")
        or consequent.get("operator")
        or "must_hold"
    )

    if logical_form == "CONSERVATION_EQUATION":
        lhs = _text(consequent.get("lhs"))
        rhs = _text(consequent.get("rhs"))
        if not operands:
            operands = [
                {"field": value, "field_id": value}
                for value in (lhs, rhs)
                if value
            ]
        if not equation and lhs and rhs:
            equation = {
                "operator": operator,
                "lhs": lhs,
                "rhs": rhs,
                "raw": consequent.get("raw"),
                "terms": [value for value in (lhs, rhs) if value],
            }
    elif not operands and consequent:
        operands = [_runtime_operand(consequent)]

    subject_refs = [
        _text(value)
        for value in _list(candidate.get("subject_refs"))
        if _text(value)
    ]
    entity = subject_refs[0] if subject_refs else ""
    first_source = next(
        (
            row
            for row in _list(candidate.get("source_refs"))
            if isinstance(row, dict) and _text(row.get("source_id"))
        ),
        {},
    )
    return {
        "kind": kind,
        "operator": operator,
        "operands": operands,
        "equation": equation,
        "entity": entity,
        "business_object": entity,
        "source_locator": _text(
            first_source.get("source_locator") or first_source.get("locator")
        ),
    }


def _rule_promoter(candidate: dict[str, Any]) -> dict[str, Any]:
    statement = _text(candidate.get("statement") or candidate.get("name"))
    candidate_id = _text(candidate.get("candidate_id")) or _stable_id(
        "rulecand", candidate.get("logical_form"), statement, candidate.get("source_refs")
    )
    source_ids = _explicit_rule_source_ids(candidate)
    rule_id = _text(candidate.get("rule_id")) or _stable_id(
        "implicit_rule", candidate.get("logical_form"), statement, source_ids
    )
    structured_expression = _dict(candidate.get("structured_expression"))
    if not structured_expression:
        structured_expression = {
            "logical_form": _text(candidate.get("logical_form")).upper(),
            "antecedents": list(candidate.get("antecedents") or []),
            "consequent": dict(candidate.get("consequent") or {}),
            "scope": dict(candidate.get("scope") or {}),
            "exceptions": list(candidate.get("exceptions") or []),
            "temporal_window": candidate.get("temporal_window"),
            "aggregation": candidate.get("aggregation"),
        }
    runtime_projection = _rule_runtime_projection(candidate, structured_expression)
    semantic_contract = dict(candidate.get("semantic_contract") or {})
    semantic_contract.update(
        {
            "status": "ACCEPTED",
            "authority": "validated_rule_candidate",
            "candidate_id": candidate_id,
            "authority_gate": dict(candidate.get("authority_gate") or {}),
            "runtime_projection": {
                "authority": "existing_rule_library_contract",
                "kind": runtime_projection["kind"],
                "operator": runtime_projection["operator"],
                "structured_expression_preserved": True,
            },
        }
    )
    return {
        "rule_id": rule_id,
        "source_id": source_ids[0] if len(source_ids) == 1 else "multi_source_rule_entailment",
        "source_ids": source_ids,
        "source_type": "derived_rule_entailment",
        "source_locator": runtime_projection["source_locator"],
        "statement": statement,
        "expected": statement,
        "kind": runtime_projection["kind"],
        "risk_type": _text(candidate.get("risk_type") or "business_logic"),
        "operator": runtime_projection["operator"],
        "operands": runtime_projection["operands"],
        "equation": runtime_projection["equation"],
        "entity": runtime_projection["entity"],
        "business_object": runtime_projection["business_object"],
        "severity": _text(candidate.get("severity") or "P1"),
        "derivation": "implicit_rule_entailment",
        "confidence": float(candidate.get("confidence") or 0.8),
        "status": "accepted",
        "candidate_id": candidate_id,
        "logical_form": _text(candidate.get("logical_form")).upper(),
        "structured_expression": structured_expression,
        "causal_chain": dict(candidate.get("causal_chain") or {}),
        "subject_refs": list(candidate.get("subject_refs") or []),
        "actor_refs": list(candidate.get("actor_refs") or []),
        "operation_refs": list(candidate.get("operation_refs") or []),
        "table_refs": list(candidate.get("table_refs") or []),
        "field_refs": list(candidate.get("field_refs") or []),
        "scope": dict(candidate.get("scope") or {}),
        "exceptions": list(candidate.get("exceptions") or []),
        "supporting_fact_refs": list(candidate.get("supporting_fact_refs") or []),
        "contradicting_fact_refs": list(candidate.get("contradicting_fact_refs") or []),
        "source_refs": list(candidate.get("source_refs") or []),
        "derivation_basis": list(candidate.get("derivation_basis") or []),
        "counterexample_plan": dict(candidate.get("counterexample_plan") or {}),
        "falsifiability": _text(candidate.get("falsifiability")),
        "observation_requirements": list(candidate.get("observation_requirements") or []),
        "promotion_receipt_id": _text(candidate.get("promotion_receipt_id")),
        "semantic_contract": semantic_contract,
    }


_CANDIDATE_KIND_REGISTRY: dict[str, dict[str, Any]] = {
    kind: {"validator": None, "promoter": None, "builtin": True}
    for kind in _GENERIC_KINDS
}
_CANDIDATE_KIND_REGISTRY["rule"] = {
    "validator": _rule_validation,
    "promoter": _rule_promoter,
    "builtin": True,
}
# Backward-compatible inventory; runtime resolution uses the registry directly.
_ALLOWED_KINDS = frozenset(_CANDIDATE_KIND_REGISTRY)


def register_candidate_kind(
    kind: str,
    *,
    validator: CandidateValidator,
    promoter: CandidatePromoter | None = None,
) -> None:
    """Register one additive candidate type without shadowing built-in authority."""
    global _ALLOWED_KINDS
    normalized = _text(kind).lower()
    if not normalized:
        raise ValueError("candidate_kind_empty")
    if normalized in _CANDIDATE_KIND_REGISTRY:
        raise ValueError(f"candidate_kind_already_registered:{normalized}")
    if not callable(validator):
        raise TypeError("candidate_validator_not_callable")
    if promoter is not None and not callable(promoter):
        raise TypeError("candidate_promoter_not_callable")
    _CANDIDATE_KIND_REGISTRY[normalized] = {
        "validator": validator,
        "promoter": promoter,
        "builtin": False,
    }
    _ALLOWED_KINDS = frozenset(_CANDIDATE_KIND_REGISTRY)


def registered_candidate_kinds() -> tuple[str, ...]:
    return tuple(sorted(_CANDIDATE_KIND_REGISTRY))


class CandidateValidationReceipt:
    """Receipt for candidate validation and promotion."""

    def __init__(self) -> None:
        self.total_candidates: int = 0
        self.validated: list[dict[str, Any]] = []
        self.conflicted: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self.stale: list[dict[str, Any]] = []

    def _per_kind(self) -> dict[str, dict[str, int]]:
        rows: dict[str, Counter[str]] = defaultdict(Counter)
        for status, values in (
            ("validated", self.validated),
            ("conflicted", self.conflicted),
            ("pending", self.pending),
            ("rejected", self.rejected),
            ("stale", self.stale),
        ):
            for value in values:
                if isinstance(value, dict):
                    rows[_text(value.get("kind")) or "unknown"][status] += 1
        return {kind: dict(counter) for kind, counter in sorted(rows.items())}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.candidate-validation-receipt.v3",
            "promotion_contract": (
                "one candidate-kind registry; hard authority gates; independent source "
                "identities required unless a formal constraint is authoritative"
            ),
            "registered_candidate_kinds": list(registered_candidate_kinds()),
            "total_candidates": self.total_candidates,
            "validated_count": len(self.validated),
            "conflicted_count": len(self.conflicted),
            "pending_count": len(self.pending),
            "rejected_count": len(self.rejected),
            "stale_count": len(self.stale),
            "per_kind": self._per_kind(),
            "validated": self.validated,
            "conflicted": self.conflicted,
            "pending": self.pending,
            "rejected": self.rejected,
            "stale": self.stale,
        }


def _append_status(
    receipt: CandidateValidationReceipt,
    row: dict[str, Any],
    status: str,
) -> None:
    bucket_name = _TERMINAL_BUCKET_BY_STATUS.get(status)
    if bucket_name is None:
        raise ValueError(f"candidate_status_invalid:{status}")
    getattr(receipt, bucket_name).append(row)


def _validate_generic_candidate(
    candidate: dict[str, Any],
    *,
    interfaces: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    state_machines: list[dict[str, Any]],
    candidate_sources: dict[tuple[str, str], set[str]],
) -> dict[str, Any]:
    name = _text(candidate.get("name"))
    name_lower = name.lower()
    kind = _text(candidate.get("kind")).lower()
    source = _source_identity(candidate, prefix="candidate")
    evidence: list[str] = []
    details: dict[str, list[str]] = {}
    typed_binding_gaps: list[str] = []
    quote = _text(candidate.get("verbatim_quote"))
    for binding_name in _TYPED_BINDING_FIELDS.get(kind, ()):
        value = _text(candidate.get(binding_name))
        if not value:
            typed_binding_gaps.append(binding_name)
            continue
        if value not in quote:
            return {
                "status": "REJECTED",
                "reason": f"typed_binding_not_in_quote:{binding_name}",
                "typed_binding_status": "REJECTED",
                "typed_binding_gaps": [binding_name],
            }
    typed_binding_decision = {
        "typed_binding_status": (
            "INCOMPLETE" if typed_binding_gaps else "COMPLETE"
        ),
        "typed_binding_gaps": typed_binding_gaps,
    } if kind in _TYPED_BINDING_FIELDS else {}

    for evidence_name, sources in (
        (
            "cross_ref_api_path",
            _independent_match_sources(
                name,
                interfaces,
                text_fields=("path", "summary", "source_excerpt"),
                candidate_source=source,
            ),
        ),
        (
            "cross_ref_table_name",
            _independent_match_sources(
                name,
                tables,
                text_fields=("description",),
                exact_fields=("name",),
                candidate_source=source,
            ),
        ),
        (
            "cross_ref_rule_text",
            _independent_match_sources(
                name,
                rules,
                text_fields=("statement", "expected"),
                candidate_source=source,
            ),
        ),
    ):
        if sources:
            evidence.append(evidence_name)
            details[evidence_name] = sources

    state_rows = [
        {
            **state_machine,
            "_state_text": " ".join(
                [
                    _text(state_machine.get("name")),
                    *[_text(state) for state in state_machine.get("states") or []],
                ]
            ),
        }
        for state_machine in state_machines
    ]
    state_sources = _independent_match_sources(
        name,
        state_rows,
        text_fields=("_state_text",),
        candidate_source=source,
    )
    if state_sources:
        evidence.append("cross_ref_state_machine")
        details["cross_ref_state_machine"] = state_sources

    multi_sources = sorted(candidate_sources.get((name_lower, kind), set()))
    independent_candidate_sources = [
        value for value in multi_sources if value and value != source
    ]
    if independent_candidate_sources:
        evidence.append("multi_source_consistency")
        details["multi_source_consistency"] = independent_candidate_sources

    if evidence:
        return {
            "status": "VALIDATED",
            "promotion_evidence": evidence,
            "promotion_evidence_sources": details,
            "confidence": min(
                1.0, float(candidate.get("confidence") or 0.5) + 0.2
            ),
            **typed_binding_decision,
        }
    return {
        "status": "PENDING_VALIDATION",
        "promotion_evidence": [],
        "promotion_evidence_sources": {},
        "pending_reason": "no_independent_source_evidence",
        **typed_binding_decision,
    }


def validate_and_promote_candidates(
    candidates: list[dict[str, Any]],
    *,
    interfaces: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    rules: list[dict[str, Any]] | None = None,
    state_machines: list[dict[str, Any]] | None = None,
    other_candidates: list[dict[str, Any]] | None = None,
    validation_context: dict[str, Any] | None = None,
) -> CandidateValidationReceipt:
    """Validate candidates through the single registered promotion authority."""
    receipt = CandidateValidationReceipt()
    receipt.total_candidates = len(candidates)
    interfaces = [row for row in (interfaces or []) if isinstance(row, dict)]
    tables = [row for row in (tables or []) if isinstance(row, dict)]
    rules = [row for row in (rules or []) if isinstance(row, dict)]
    state_machines = [row for row in (state_machines or []) if isinstance(row, dict)]
    other_candidates = [row for row in (other_candidates or []) if isinstance(row, dict)]
    context = dict(validation_context or {})
    context.update(
        {
            "interfaces": interfaces,
            "tables": tables,
            "rules": rules,
            "state_machines": state_machines,
            "other_candidates": other_candidates,
        }
    )

    candidate_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    all_candidates = [row for row in [*candidates, *other_candidates] if isinstance(row, dict)]
    for candidate in all_candidates:
        name = _text(candidate.get("name")).lower()
        kind = _text(candidate.get("kind")).lower()
        for source in _candidate_source_ids(candidate):
            if name and kind and source:
                candidate_sources[(name, kind)].add(source)

    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            receipt.rejected.append({"raw": str(candidate)[:100], "reason": "not_a_dict"})
            continue
        kind = _text(candidate.get("kind")).lower()
        descriptor = _CANDIDATE_KIND_REGISTRY.get(kind)
        if descriptor is None:
            receipt.rejected.append(
                {
                    "candidate": candidate,
                    "kind": kind,
                    "reason": "unsupported_candidate_kind",
                    "allowed_kinds": list(registered_candidate_kinds()),
                }
            )
            continue
        name = _text(candidate.get("name") or candidate.get("statement"))
        if not name:
            receipt.rejected.append(
                {"candidate": candidate, "kind": kind, "reason": "empty_name"}
            )
            continue
        row = dict(candidate)
        row["kind"] = kind
        row.setdefault("name", name)
        row.setdefault(
            "candidate_id",
            _stable_id(
                "cand",
                kind,
                name,
                _source_identity(row, prefix="candidate"),
                row.get("source_refs"),
                row.get("verbatim_quote"),
            ),
        )
        normalized.append(row)

    conflicted_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    for candidate in [*normalized, *other_candidates]:
        name = _text(candidate.get("name")).lower()
        kind = _text(candidate.get("kind")).lower()
        source = _source_identity(candidate, prefix="candidate")
        if name and kind and source:
            conflicted_keys[(name, source)].add(kind)

    for candidate in normalized:
        name_lower = _text(candidate.get("name")).lower()
        kind = _text(candidate.get("kind")).lower()
        source = _source_identity(candidate, prefix="candidate")
        source_kinds = conflicted_keys.get((name_lower, source), set())
        if kind != "rule" and len(source_kinds) > 1:
            conflicted = dict(candidate)
            conflicted["status"] = "CONFLICTED"
            conflicted["conflict_reason"] = (
                "same_source_same_name_multiple_kinds:" + ",".join(sorted(source_kinds))
            )
            receipt.conflicted.append(conflicted)
            continue

        descriptor = _CANDIDATE_KIND_REGISTRY[kind]
        validator = descriptor.get("validator")
        try:
            decision = (
                validator(candidate, context)
                if callable(validator)
                else _validate_generic_candidate(
                    candidate,
                    interfaces=interfaces,
                    tables=tables,
                    rules=rules,
                    state_machines=state_machines,
                    candidate_sources=candidate_sources,
                )
            )
        except Exception as exc:  # validation failure is visible data, not a silent drop
            logger.exception("Candidate validator failed for kind=%s", kind)
            decision = {
                "status": "REJECTED",
                "reason": f"candidate_validator_failed:{type(exc).__name__}",
            }
        if not isinstance(decision, dict):
            decision = {"status": "REJECTED", "reason": "candidate_validator_non_object"}
        status = _text(decision.get("status")).upper()
        if status not in _TERMINAL_BUCKET_BY_STATUS:
            status = "REJECTED"
            decision = {"reason": "candidate_validator_status_invalid"}
        decided = {**candidate, **decision, "status": status}
        _append_status(receipt, decided, status)

    # Cross-source same-name/different-kind contradictions remain visible for generic
    # semantic candidates. Rule candidates carry their own explicit counterevidence.
    other_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for other in other_candidates:
        name = _text(other.get("name")).lower()
        if name:
            other_by_name[name].append(other)
    still_validated: list[dict[str, Any]] = []
    for validated in receipt.validated:
        kind = _text(validated.get("kind")).lower()
        if kind == "rule":
            still_validated.append(validated)
            continue
        name_lower = _text(validated.get("name")).lower()
        source = _source_identity(validated, prefix="candidate")
        conflicts = [
            other
            for other in other_by_name.get(name_lower, [])
            if _source_identity(other, prefix="candidate") != source
            and _text(other.get("kind")).lower() != kind
        ]
        if conflicts:
            conflicted = dict(validated)
            conflicted["status"] = "CONFLICTED"
            conflicted["conflict_reason"] = (
                f"same_name_different_kind:{kind}_vs_"
                + ",".join(sorted({_text(other.get('kind')).lower() for other in conflicts}))
            )
            conflicted["conflict_sources"] = sorted(
                {
                    _source_identity(other, prefix="candidate")
                    for other in conflicts
                    if _source_identity(other, prefix="candidate")
                }
            )
            receipt.conflicted.append(conflicted)
        else:
            still_validated.append(validated)
    receipt.validated = still_validated

    logger.info(
        "Candidate validation: %d total → %d validated, %d pending, %d conflicted, %d rejected",
        receipt.total_candidates,
        len(receipt.validated),
        len(receipt.pending),
        len(receipt.conflicted),
        len(receipt.rejected),
    )
    return receipt


def promote_validated_candidates(
    validated: list[dict[str, Any]],
    *,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Project validated candidates through their registered typed adapters."""
    selected_kind = _text(kind).lower()
    promoted: list[dict[str, Any]] = []
    for candidate in validated:
        if not isinstance(candidate, dict):
            continue
        candidate_kind = _text(candidate.get("kind")).lower()
        if selected_kind and candidate_kind != selected_kind:
            continue
        descriptor = _CANDIDATE_KIND_REGISTRY.get(candidate_kind) or {}
        promoter = descriptor.get("promoter")
        if not callable(promoter):
            continue
        row = promoter(candidate)
        if isinstance(row, dict) and row:
            promoted.append(row)
    return promoted


def _diagnostic_entry(
    candidate: dict[str, Any],
    *,
    source: str,
    derivation: str,
    confidence: float,
) -> dict[str, Any]:
    kind = _text(candidate.get("kind")).lower()
    return {
        "candidate_name": candidate.get("name"),
        "semantic_candidate_kind": kind,
        "source": source,
        "evidence": [
            {
                "source_id": candidate.get("source_id"),
                "verbatim_quote": _text(candidate.get("verbatim_quote"))[:200],
                "source_locator": candidate.get("source_locator"),
            }
        ],
        "confidence": confidence,
        "derivation": derivation,
        "promotion_evidence": list(candidate.get("promotion_evidence") or []),
        "promotion_evidence_sources": dict(candidate.get("promotion_evidence_sources") or {}),
    }


def project_validated_candidates_to_asset_spaces(
    validated: list[dict[str, Any]],
    *,
    business_objects: list[dict[str, Any]] | None = None,
    data_tables: list[dict[str, Any]] | None = None,
    roles: list[dict[str, Any]] | None = None,
    state_machines: list[dict[str, Any]] | None = None,
    entity_relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project source-validated typed candidates into existing asset spaces.

    A name alone is not enough authority for a typed binding. Fields and
    states require a source-anchored ``owner``; relations require both
    ``source_entity`` and ``target_entity``. Missing or unknown bindings stay
    visible as coverage gaps and never become guessed objects or edges.
    """
    objects = [dict(row) for row in (business_objects or []) if isinstance(row, dict)]
    tables = [dict(row) for row in (data_tables or []) if isinstance(row, dict)]
    projected_roles = [dict(row) for row in (roles or []) if isinstance(row, dict)]
    machines = [dict(row) for row in (state_machines or []) if isinstance(row, dict)]
    relations = [dict(row) for row in (entity_relations or []) if isinstance(row, dict)]
    gaps: list[dict[str, Any]] = []
    projected_by_kind: Counter[str] = Counter()

    def source_refs(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        refs = [
            dict(row)
            for row in _list(candidate.get("source_refs"))
            if isinstance(row, dict) and row
        ]
        if refs:
            return refs
        source_id = _text(candidate.get("source_id"))
        locator = _text(candidate.get("source_locator"))
        quote = _text(candidate.get("verbatim_quote"))[:500]
        if not source_id and not locator and not quote:
            return []
        return [{
            "source_id": source_id,
            "locator": locator,
            "quote": quote,
        }]

    def candidate_ref(candidate: dict[str, Any]) -> str:
        return _text(candidate.get("candidate_id")) or _stable_id(
            "candidate",
            candidate.get("kind"),
            candidate.get("name"),
            candidate.get("source_id"),
            candidate.get("source_locator"),
        )

    def append_candidate_ref(row: dict[str, Any], ref: str) -> None:
        refs = [
            _text(value)
            for value in _list(row.get("semantic_candidate_refs"))
            if _text(value)
        ]
        if ref not in refs:
            refs.append(ref)
        row["semantic_candidate_refs"] = refs

    def append_candidate_evidence(
        row: dict[str, Any],
        ref: str,
        refs: list[dict[str, Any]],
    ) -> None:
        evidence = [
            dict(value)
            for value in _list(row.get("semantic_candidate_evidence"))
            if isinstance(value, dict)
        ]
        if not any(_text(value.get("candidate_id")) == ref for value in evidence):
            evidence.append({"candidate_id": ref, "source_refs": refs})
        row["semantic_candidate_evidence"] = evidence

    def gap(
        candidate: dict[str, Any],
        code: str,
        missing: list[str],
    ) -> None:
        ref = candidate_ref(candidate)
        gaps.append({
            "gap_id": _stable_id("semantic_gap", ref, code),
            "code": code,
            "candidate_id": ref,
            "candidate_kind": _text(candidate.get("kind")).lower(),
            "candidate_name": _text(candidate.get("name")),
            "missing_bindings": list(missing),
            "source_refs": source_refs(candidate),
            "operator_action": "provide source-grounded typed bindings or independent material evidence",
        })

    def unresolved_typed_bindings(candidate: dict[str, Any]) -> list[str]:
        kind = _text(candidate.get("kind")).lower()
        quote = _text(candidate.get("verbatim_quote"))
        unresolved = [
            field
            for field in _TYPED_BINDING_FIELDS.get(kind, ())
            if (
                not _text(candidate.get(field))
                or _text(candidate.get(field)) not in quote
            )
        ]
        if _text(candidate.get("typed_binding_status")).upper() != "COMPLETE":
            declared_gaps = [
                _text(value)
                for value in _list(candidate.get("typed_binding_gaps"))
                if _text(value)
            ]
            unresolved.extend(
                declared_gaps or _TYPED_BINDING_FIELDS.get(kind, ())
            )
        return sorted(set(unresolved))

    candidates = [
        dict(row)
        for row in validated
        if (
            isinstance(row, dict)
            and _text(row.get("status")).upper() == "VALIDATED"
            and _text(row.get("kind")).lower() in _GENERIC_KINDS
        )
    ]

    object_by_name = {
        _text(row.get("object") or row.get("name")): row
        for row in objects
        if _text(row.get("object") or row.get("name"))
    }
    for candidate in candidates:
        if _text(candidate.get("kind")).lower() != "entity":
            continue
        name = _text(candidate.get("name"))
        if not name:
            continue
        ref = candidate_ref(candidate)
        row = object_by_name.get(name)
        if row is None:
            row = {
                "object": name,
                "source": "semantic_extraction_validated",
                "source_id": _text(candidate.get("source_id")),
                "source_refs": source_refs(candidate),
                "key_business_fields": [],
                "confidence": float(candidate.get("confidence") or 0.7),
                "behavior_ir_promotion_status": "ENTITY_SPACE_ACCEPTED",
            }
            objects.append(row)
            object_by_name[name] = row
        append_candidate_evidence(row, ref, source_refs(candidate))
        append_candidate_ref(row, ref)
        projected_by_kind["entity"] += 1

    known_entity_names = set(object_by_name)
    known_entity_names.update(
        _text(row.get("name")) for row in tables if _text(row.get("name"))
    )

    role_by_name = {
        _text(row.get("role") or row.get("name")): row
        for row in projected_roles
        if _text(row.get("role") or row.get("name"))
    }
    machine_by_owner = {
        _text(row.get("object") or row.get("entity")): row
        for row in machines
        if _text(row.get("object") or row.get("entity"))
    }
    relation_by_key = {
        (
            _text(row.get("from_entity")),
            _text(row.get("to_entity")),
            _text(row.get("relation_type")),
        ): row
        for row in relations
    }

    for candidate in candidates:
        kind = _text(candidate.get("kind")).lower()
        name = _text(candidate.get("name"))
        ref = candidate_ref(candidate)
        refs = source_refs(candidate)
        if kind == "actor":
            row = role_by_name.get(name)
            if row is None:
                row = {
                    "role_id": _stable_id("semantic_role", name, ref),
                    "role": name,
                    "source_id": _text(candidate.get("source_id")),
                    "source_locator": _text(candidate.get("source_locator")),
                    "source_refs": refs,
                    "confidence": float(candidate.get("confidence") or 0.7),
                    "derivation": "validated_semantic_actor",
                }
                projected_roles.append(row)
                role_by_name[name] = row
            append_candidate_evidence(row, ref, refs)
            append_candidate_ref(row, ref)
            projected_by_kind["actor"] += 1
        elif kind == "field":
            unresolved = unresolved_typed_bindings(candidate)
            if unresolved:
                gap(
                    candidate,
                    "SEMANTIC_FIELD_OWNER_UNRESOLVED",
                    unresolved,
                )
                continue
            owner = _text(candidate.get("owner"))
            if not owner or owner not in known_entity_names:
                gap(candidate, "SEMANTIC_FIELD_OWNER_UNRESOLVED", ["owner"])
                continue
            owner_row = object_by_name.get(owner)
            if owner_row is None:
                gap(candidate, "SEMANTIC_FIELD_OWNER_UNRESOLVED", ["business_object"])
                continue
            fields = [
                _text(value)
                for value in _list(owner_row.get("key_business_fields"))
                if _text(value)
            ]
            field_added = name not in fields
            if field_added:
                fields.append(name)
            owner_row["key_business_fields"] = fields
            bindings = [
                dict(row)
                for row in _list(owner_row.get("semantic_field_bindings"))
                if isinstance(row, dict)
            ]
            if not any(
                _text(row.get("field")) == name
                and _text(row.get("candidate_id")) == ref
                for row in bindings
            ):
                bindings.append({
                    "field": name,
                    "candidate_id": ref,
                    "source_refs": refs,
                    "projected_field_added": field_added,
                })
            owner_row["semantic_field_bindings"] = bindings
            append_candidate_evidence(owner_row, ref, refs)
            append_candidate_ref(owner_row, ref)
            projected_by_kind["field"] += 1
        elif kind == "state":
            unresolved = unresolved_typed_bindings(candidate)
            if unresolved:
                gap(
                    candidate,
                    "SEMANTIC_STATE_OWNER_UNRESOLVED",
                    unresolved,
                )
                continue
            owner = _text(candidate.get("owner"))
            if not owner or owner not in known_entity_names:
                gap(candidate, "SEMANTIC_STATE_OWNER_UNRESOLVED", ["owner"])
                continue
            row = machine_by_owner.get(owner)
            if row is None:
                row = {
                    "state_machine_id": _stable_id("semantic_state_machine", owner),
                    "object": owner,
                    "states": [],
                    "transitions": [],
                    "source_id": _text(candidate.get("source_id")),
                    "source_refs": refs,
                    "derivation": "validated_semantic_state",
                }
                machines.append(row)
                machine_by_owner[owner] = row
            states = [_text(value) for value in _list(row.get("states")) if _text(value)]
            state_added = name not in states
            if state_added:
                states.append(name)
            row["states"] = states
            state_bindings = [
                dict(value)
                for value in _list(row.get("semantic_state_bindings"))
                if isinstance(value, dict)
            ]
            if not any(
                _text(value.get("state")) == name
                and _text(value.get("candidate_id")) == ref
                for value in state_bindings
            ):
                state_bindings.append({
                    "state": name,
                    "candidate_id": ref,
                    "source_refs": refs,
                    "projected_state_added": state_added,
                })
            row["semantic_state_bindings"] = state_bindings
            append_candidate_evidence(row, ref, refs)
            append_candidate_ref(row, ref)
            projected_by_kind["state"] += 1
        elif kind == "relation":
            unresolved = unresolved_typed_bindings(candidate)
            if unresolved:
                gap(
                    candidate,
                    "SEMANTIC_RELATION_ENDPOINT_UNRESOLVED",
                    unresolved,
                )
                continue
            source_entity = _text(candidate.get("source_entity"))
            target_entity = _text(candidate.get("target_entity"))
            missing = [
                field
                for field, value in (
                    ("source_entity", source_entity),
                    ("target_entity", target_entity),
                )
                if not value or value not in known_entity_names
            ]
            if missing:
                gap(candidate, "SEMANTIC_RELATION_ENDPOINT_UNRESOLVED", missing)
                continue
            key = (source_entity, target_entity, name)
            row = relation_by_key.get(key)
            if row is None:
                row = {
                    "relation_id": _stable_id("semantic_relation", *key, ref),
                    "from_entity": source_entity,
                    "to_entity": target_entity,
                    "relation_type": name,
                    "source_id": _text(candidate.get("source_id")),
                    "source_refs": refs,
                    "confidence": float(candidate.get("confidence") or 0.7),
                    "derivation": "validated_semantic_relation",
                    "status": "accepted",
                }
                relations.append(row)
                relation_by_key[key] = row
            append_candidate_evidence(row, ref, refs)
            append_candidate_ref(row, ref)
            projected_by_kind["relation"] += 1

    return {
        "business_objects": objects,
        "roles": projected_roles,
        "state_machines": machines,
        "entity_relations": relations,
        "coverage_gaps": gaps,
        "projection_receipt": {
            "schema_version": "qualibug.typed-semantic-candidate-projection.v1",
            "validated_input_count": len(candidates),
            "projected_by_kind": dict(sorted(projected_by_kind.items())),
            "gap_count": len(gaps),
            "gap_codes": dict(sorted(Counter(row["code"] for row in gaps).items())),
            "authority": "source_validated_typed_candidates_only",
        },
    }


def candidates_to_behavior_ir_entries(
    validated: list[dict[str, Any]],
    pending: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Preserve the existing semantic-candidate entity-space adapter.

    Rule candidates use ``promote_validated_candidates(..., kind="rule")`` and enter
    the existing rule library, which the existing Behavior IR compiler consumes as
    invariants. Pending candidates remain diagnostic-only.
    """
    entries: list[dict[str, Any]] = []
    for candidate in validated:
        if not isinstance(candidate, dict):
            continue
        entry = _diagnostic_entry(
            candidate,
            source="semantic_extraction_validated",
            derivation="llm_semantic_validated",
            confidence=float(candidate.get("confidence") or 0.7),
        )
        if _text(candidate.get("kind")).lower() == "entity":
            entry["object"] = candidate.get("name")
            entry["behavior_ir_promotion_status"] = "ENTITY_SPACE_ACCEPTED"
        else:
            entry["behavior_ir_promotion_status"] = "TYPED_ADAPTER_REQUIRED_NOT_ENTITY"
            entry["_cannot_enter_entity_space"] = True
        entries.append(entry)

    for candidate in pending or []:
        if not isinstance(candidate, dict):
            continue
        entry = _diagnostic_entry(
            candidate,
            source="semantic_extraction_pending",
            derivation="llm_semantic_pending_validation",
            confidence=min(0.4, float(candidate.get("confidence") or 0.3)),
        )
        entry.update(
            {
                "behavior_ir_promotion_status": "PENDING_DIAGNOSTIC_ONLY",
                "_low_confidence_marker": True,
                "_cannot_solely_support_finding": True,
                "_cannot_enter_entity_space": True,
            }
        )
        entries.append(entry)
    return entries
