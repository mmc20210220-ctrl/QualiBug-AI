"""Produce governed rule candidates from accepted typed business facts.

This module is a candidate producer, not a rule authority and not a parallel Rule IR.
It converts exact slots already accepted by the existing business-fact ledger into the
candidate contract consumed by ``_candidate_validation``. Promotion, conflict handling
and execution admission remain owned by the existing authorities.

Authority boundaries are deliberate:

* operation + condition + outcome semantics stay in the canonical Business Behavior IR
  authority (``enterprise_understanding.behavior_ir_logic_gate``);
* allowed/forbidden state transitions stay in the existing state-machine/Behavior IR
  authority;
* this producer handles only source-backed entailments that those authorities do not
  already represent as the same executable fact: cardinality, formula/conservation and
  explicit idempotency/effect-cardinality rules.

A prose rule with the same source statement is not automatically equivalent to one typed
runtime invariant. If the source rule already carries the same logical form and operator,
the candidate is suppressed. Otherwise the candidate targets that existing rule identity
for an in-place semantic upgrade, so one source rule does not become two executable rules.
A previous implicit projection identity wins over a prose target during the second
composition pass, which keeps one deterministic rule ID inside the same build.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

FACT_DERIVATION_SCHEMA = "qualibug.implicit-rule-fact-entailment.v3"
CANONICAL_BEHAVIOR_OWNED_SLOTS = frozenset(
    {
        "action",
        "conditions",
        "condition_frame",
        "condition_combinator",
        "state_effects",
        "time_window_constraints",
        "permission_decision",
        "expected_effects",
        "data_effects",
    }
)
_IDEMPOTENCY_RE = re.compile(
    r"幂等|不得重复|不能重复|不可重复|禁止重复|只能成功一次|仅成功一次|"
    r"重复提交不得|重复请求不得|重复调用不得|idempoten|exactly once|at most once",
    re.I,
)
_RULE_FAMILIES_BY_LOGICAL_FORM = {
    "CARDINALITY": {"cardinality", "data_integrity", "business_rule"},
    "CONSERVATION_EQUATION": {
        "conservation",
        "data_conservation",
        "business_rule",
    },
    "IDEMPOTENCY": {"idempotency"},
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _statement_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).casefold())


def _stable_id(*parts: Any) -> str:
    raw = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple))
        else _text(part)
        for part in parts
    )
    return "rulecand_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _entity_refs(value: Any) -> list[str]:
    rows = value.get("entity_refs") if isinstance(value, dict) else value
    return sorted({_text(row) for row in _list(rows) if _text(row)})


def _actor_refs(value: Any) -> list[str]:
    rows = value.get("actor_refs") if isinstance(value, dict) else value
    return sorted({_text(row) for row in _list(rows) if _text(row)})


def _source_refs(fact: dict[str, Any]) -> list[dict[str, Any]]:
    fact_id = _text(fact.get("fact_id") or fact.get("id"))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for span in _list(fact.get("source_spans")):
        if not isinstance(span, dict) or not _text(span.get("source_id")):
            continue
        row = {
            "source_id": _text(span.get("source_id")),
            "source_locator": _text(
                span.get("locator") or span.get("source_locator")
            ),
            "kind": "accepted_typed_business_fact",
            "fact_ref": fact_id,
            "document_block_id": span.get("document_block_id"),
            "quote_hash": span.get("quote_hash"),
        }
        identity = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _operation_refs(fact: dict[str, Any]) -> list[str]:
    action = _dict(fact.get("action"))
    refs: list[str] = []
    for container in (fact, action):
        for field in (
            "operation_ref",
            "operation_id",
            "interface_id",
            "source_operation_ref",
            "operation_refs",
        ):
            value = container.get(field)
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = _text(item)
                if text and text not in refs:
                    refs.append(text)
    return refs


def _scope(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        _text(key): value
        for key, value in _dict(fact.get("scope")).items()
        if _text(key) and value not in (None, "", [], {})
    }


def _exceptions(fact: dict[str, Any]) -> list[Any]:
    values = [
        value
        for value in [
            *_list(fact.get("exceptions")),
            *_list(fact.get("exception_scope")),
        ]
        if value not in (None, "", [], {})
    ]
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if identity not in seen:
            seen.add(identity)
            result.append(value)
    return result


def _candidate(
    fact: dict[str, Any],
    *,
    logical_form: str,
    consequent: dict[str, Any],
    risk_type: str,
    observation_requirements: list[str],
    counterexample_plan: dict[str, Any],
    subject_refs: list[str],
    antecedents: list[dict[str, Any]] | None = None,
    actor_refs: list[str] | None = None,
    operation_refs: list[str] | None = None,
    field_refs: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    exceptions: list[Any] | None = None,
    severity: str = "P1",
    binding_readiness: str = "READY_FOR_IR_BINDING",
    execution_capability: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    fact_id = _text(fact.get("fact_id") or fact.get("id"))
    statement = _text(fact.get("raw_statement") or fact.get("statement"))
    source_refs = _source_refs(fact)
    if not fact_id or not statement or not source_refs:
        return None
    sources = sorted({_text(row.get("source_id")) for row in source_refs})
    exception_values = list(exceptions or [])
    return {
        "candidate_id": _stable_id(logical_form, fact_id, consequent, sources),
        "kind": "rule",
        "name": statement,
        "statement": statement,
        "logical_form": logical_form,
        "antecedents": list(
            antecedents
            or [{"subject_refs": subject_refs, "predicate": "observed"}]
        ),
        "consequent": consequent,
        "subject_refs": list(dict.fromkeys(subject_refs)),
        "actor_refs": list(dict.fromkeys(actor_refs or [])),
        "operation_refs": list(dict.fromkeys(operation_refs or [])),
        "table_refs": [],
        "field_refs": list(dict.fromkeys(field_refs or [])),
        "scope": dict(scope or {}),
        "exceptions": exception_values,
        "derivation_basis": ["typed_fact_entailment"],
        "supporting_fact_refs": [fact_id],
        "contradicting_fact_refs": [],
        "source_refs": source_refs,
        "supporting_source_ids": sources,
        "supporting_evidence": source_refs,
        "source_authority": "formal_constraint",
        "falsifiability": "EVALUABLE",
        "binding_readiness": binding_readiness,
        "execution_capability": dict(execution_capability or {}),
        "scope_status": "RESOLVED" if scope else "NOT_APPLICABLE",
        "exception_status": "RESOLVED" if exception_values else "NOT_APPLICABLE",
        "counterexample_plan": counterexample_plan,
        "observation_requirements": observation_requirements,
        "risk_type": risk_type,
        "severity": severity,
        "confidence": min(1.0, max(0.0, float(fact.get("confidence") or 0.9))),
        "status": "CANDIDATE",
        "fact_entailment_schema": FACT_DERIVATION_SCHEMA,
    }


def _rule_source_ids(rule: dict[str, Any]) -> set[str]:
    return {
        value
        for value in [
            _text(rule.get("source_id")),
            *[_text(item) for item in _list(rule.get("source_ids"))],
            *[
                _text(item.get("source_id"))
                for item in _list(rule.get("source_refs"))
                if isinstance(item, dict)
            ],
        ]
        if value and value not in {"multi_source_rule_entailment", "industry_inference"}
    }


def _candidate_source_ids(candidate: dict[str, Any]) -> set[str]:
    return {
        value
        for value in [
            *[_text(item) for item in _list(candidate.get("supporting_source_ids"))],
            *[
                _text(item.get("source_id"))
                for item in _list(candidate.get("source_refs"))
                if isinstance(item, dict)
            ],
        ]
        if value
    }


def _rule_semantic_signature(rule: dict[str, Any]) -> tuple[str, str]:
    structured = _dict(rule.get("structured_expression"))
    consequent = _dict(structured.get("consequent"))
    logical_form = _text(
        rule.get("logical_form") or structured.get("logical_form")
    ).upper()
    operator = _text(
        rule.get("operator")
        or structured.get("operator")
        or consequent.get("operator")
    ).lower()
    return logical_form, operator


def _candidate_semantic_signature(candidate: dict[str, Any]) -> tuple[str, str]:
    consequent = _dict(candidate.get("consequent"))
    return (
        _text(candidate.get("logical_form")).upper(),
        _text(candidate.get("operator") or consequent.get("operator")).lower(),
    )


def _rule_family(rule: dict[str, Any]) -> set[str]:
    return {
        _text(rule.get(field)).lower()
        for field in ("rule_type", "risk_type", "kind")
        if _text(rule.get(field))
    }


def _statement_relation(candidate: dict[str, Any], rule: dict[str, Any]) -> str:
    candidate_key = _statement_key(candidate.get("statement"))
    rule_key = _statement_key(rule.get("statement") or rule.get("expected"))
    if not candidate_key or not rule_key:
        return ""
    if candidate_key == rule_key:
        return "EXACT_STATEMENT"
    if len(rule_key) >= 8 and rule_key in candidate_key:
        logical_form = _text(candidate.get("logical_form")).upper()
        families = _RULE_FAMILIES_BY_LOGICAL_FORM.get(logical_form, set())
        if families.intersection(_rule_family(rule)):
            return "SOURCE_RULE_CLAUSE_WITHIN_TYPED_FACT"
    return ""


def _eligible_existing_rules(
    asset: dict[str, Any], candidate: dict[str, Any]
) -> list[tuple[dict[str, Any], str]]:
    candidate_sources = _candidate_source_ids(candidate)
    rows: list[tuple[dict[str, Any], str]] = []
    for rule in _list(asset.get("rule_library")):
        if not isinstance(rule, dict):
            continue
        if _text(rule.get("source_id")) == "industry_inference" or _text(
            rule.get("source_type")
        ) == "derived_inference":
            continue
        rule_sources = _rule_source_ids(rule)
        if candidate_sources and rule_sources and not candidate_sources.intersection(rule_sources):
            continue
        relation = _statement_relation(candidate, rule)
        if relation:
            rows.append((rule, relation))
    return rows


def _govern_existing_rule_identity(
    asset: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any] | None:
    """Suppress typed equivalents or target one prose rule for in-place upgrade."""

    candidate_signature = _candidate_semantic_signature(candidate)
    matches = _eligible_existing_rules(asset, candidate)
    previous_typed = [
        (rule, relation)
        for rule, relation in matches
        if _text(rule.get("derivation")) == "implicit_rule_entailment"
        and _rule_semantic_signature(rule) == candidate_signature
        and _text(rule.get("rule_id"))
    ]
    if previous_typed:
        selected, relation = sorted(
            previous_typed,
            key=lambda item: _text(item[0].get("rule_id")),
        )[0]
        governed = dict(candidate)
        governed["rule_id"] = _text(selected.get("rule_id"))
        governed["authority_upgrade_target"] = {
            "rule_id": governed["rule_id"],
            "match_kind": "PREVIOUS_TYPED_PROJECTION_IDENTITY",
            "source_statement_relation": relation,
            "source_rule_derivation": "implicit_rule_entailment",
        }
        governed["derivation_basis"] = list(
            dict.fromkeys(
                [*governed.get("derivation_basis", []), "typed_projection_identity_reuse"]
            )
        )
        return governed

    authoritative_typed = [
        rule
        for rule, _relation in matches
        if _text(rule.get("derivation")) != "implicit_rule_entailment"
        and _rule_semantic_signature(rule) == candidate_signature
    ]
    if authoritative_typed:
        return None

    source_targets = [
        (rule, relation)
        for rule, relation in matches
        if _text(rule.get("derivation")) != "implicit_rule_entailment"
        and _text(rule.get("rule_id"))
    ]
    exact_targets = [
        item for item in source_targets if item[1] == "EXACT_STATEMENT"
    ]
    selected_targets = exact_targets or source_targets
    if len(selected_targets) == 1:
        selected, relation = selected_targets[0]
        governed = dict(candidate)
        governed["rule_id"] = _text(selected.get("rule_id"))
        governed["authority_upgrade_target"] = {
            "rule_id": governed["rule_id"],
            "match_kind": "SOURCE_RULE_TYPED_SEMANTIC_UPGRADE",
            "source_statement_relation": relation,
            "source_rule_type": _text(selected.get("rule_type")),
            "source_rule_risk_type": _text(selected.get("risk_type")),
            "source_rule_source_id": _text(selected.get("source_id")),
            "source_rule_source_type": _text(selected.get("source_type")),
            "source_rule_source_locator": _text(selected.get("source_locator")),
        }
        governed["derivation_basis"] = list(
            dict.fromkeys(
                [*governed.get("derivation_basis", []), "typed_semantic_upgrade"]
            )
        )
        return governed
    if len(selected_targets) > 1:
        governed = dict(candidate)
        governed["binding_readiness"] = "BLOCKED_SOURCE_RULE_UPGRADE_AMBIGUOUS"
        governed["execution_capability"] = {
            "status": "BLOCKED_SOURCE_RULE_UPGRADE_AMBIGUOUS",
            "reason_code": "MULTIPLE_SOURCE_RULE_IDENTITIES_MATCH_TYPED_FACT",
            "matching_rule_ids": sorted(
                {_text(rule.get("rule_id")) for rule, _ in selected_targets}
            ),
            "authority": "existing_rule_library_identity",
        }
        return governed
    return candidate


def _cardinality_candidates(
    fact: dict[str, Any], subjects: list[str], objects: list[str]
) -> Iterable[dict[str, Any]]:
    if _text(fact.get("fact_type")) != "CARDINALITY_CONSTRAINT":
        return []
    value = _dict(fact.get("value"))
    if not subjects or not objects or not value:
        return []
    row = _candidate(
        fact,
        logical_form="CARDINALITY",
        consequent={
            "operator": "cardinality",
            "subject_refs": subjects,
            "object_refs": objects,
            **value,
        },
        subject_refs=[*subjects, *objects],
        risk_type="data_integrity",
        observation_requirements=["collection"],
        counterexample_plan={
            "action": "construct_relation_count_outside_declared_cardinality",
            "observe": "collection",
        },
        binding_readiness="BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
        execution_capability={
            "status": "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
            "assertion_kind": "cardinality",
            "missing_observation_key": "collection",
            "authority": "assertion_dsl_kind_to_evidence_contract",
        },
    )
    return [row] if row else []


def _formula_candidates(
    fact: dict[str, Any], subjects: list[str]
) -> Iterable[dict[str, Any]]:
    formulas = [
        dict(row)
        for row in _list(fact.get("formula_constraints"))
        if isinstance(row, dict) and _text(row.get("raw"))
    ]
    if _text(fact.get("fact_type")) != "DERIVED_VALUE" or not formulas:
        return []
    rows: list[dict[str, Any]] = []
    for formula in formulas:
        lhs = _text(formula.get("lhs"))
        rhs = _text(formula.get("rhs"))
        if not lhs or not rhs:
            continue
        row = _candidate(
            fact,
            logical_form="CONSERVATION_EQUATION",
            consequent={
                "operator": "equation_holds",
                "lhs": lhs,
                "rhs": rhs,
                "raw": formula.get("raw"),
            },
            subject_refs=subjects or [lhs],
            field_refs=[lhs, rhs],
            risk_type="conservation",
            observation_requirements=["formula_operands", "formula_result"],
            counterexample_plan={
                "action": "change_one_source_operand",
                "observe": [lhs, rhs],
            },
        )
        if row:
            rows.append(row)
    return rows


def _idempotency_candidates(
    fact: dict[str, Any],
    subjects: list[str],
    actors: list[str],
    operations: list[str],
) -> Iterable[dict[str, Any]]:
    statement = _text(fact.get("raw_statement") or fact.get("statement"))
    action = _dict(fact.get("action"))
    action_name = _text(
        action.get("canonical") or action.get("raw") or fact.get("predicate")
    )
    if not subjects or not action_name or not _IDEMPOTENCY_RE.search(statement):
        return []
    row = _candidate(
        fact,
        logical_form="IDEMPOTENCY",
        antecedents=[
            {
                "entity_refs": subjects,
                "action": action_name,
                "same_business_identity": True,
            }
        ],
        consequent={
            "operator": "business_effect_count",
            "expected_effect_count": 1,
            "action": action_name,
        },
        subject_refs=subjects,
        actor_refs=actors,
        operation_refs=operations,
        scope=_scope(fact),
        exceptions=_exceptions(fact),
        risk_type="idempotency",
        observation_requirements=["business_effect", "http_response"],
        counterexample_plan={
            "action": "repeat_same_business_request",
            "repetitions": 2,
            "observe": "business_effect_count",
        },
    )
    return [row] if row else []


def derive_rule_candidates_from_business_facts(
    asset: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ledger = _dict(asset.get("business_fact_ledger"))
    for fact in _list(ledger.get("items")):
        if not isinstance(fact, dict) or _text(fact.get("status")) != "ACCEPTED":
            continue
        subjects = _entity_refs(fact.get("subject"))
        objects = _entity_refs(fact.get("object"))
        actors = _actor_refs(fact.get("subject"))
        operations = _operation_refs(fact)
        produced: list[dict[str, Any]] = []
        for producer in (
            lambda: _cardinality_candidates(fact, subjects, objects),
            lambda: _formula_candidates(fact, subjects),
            lambda: _idempotency_candidates(fact, subjects, actors, operations),
        ):
            produced.extend(row for row in producer() if isinstance(row, dict))
        for row in produced:
            governed = _govern_existing_rule_identity(asset, row)
            if governed is not None:
                candidates.append(governed)
    return list(
        {
            _text(row.get("candidate_id")): row
            for row in candidates
            if _text(row.get("candidate_id"))
        }.values()
    )


def uncovered_rule_candidate_spans(asset: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = _dict(asset.get("business_fact_candidate_ledger"))
    return [
        {
            "candidate_id": row.get("candidate_id"),
            "span_id": row.get("span_id"),
            "source_id": row.get("source_id"),
            "block_type": row.get("block_type"),
            "critical": bool(row.get("critical")),
            "reason_codes": list(row.get("reason_codes") or []),
            "evidence_address": dict(_dict(row.get("evidence_address"))),
            "quote": row.get("quote"),
        }
        for row in _list(ledger.get("items"))
        if isinstance(row, dict)
        and bool(row.get("contains_candidate_signal"))
        and _text(row.get("status")) == "PENDING_WITH_REASON"
    ]


__all__ = [
    "FACT_DERIVATION_SCHEMA",
    "CANONICAL_BEHAVIOR_OWNED_SLOTS",
    "derive_rule_candidates_from_business_facts",
    "uncovered_rule_candidate_spans",
]
