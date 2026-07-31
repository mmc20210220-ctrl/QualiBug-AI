"""Produce rule candidates from accepted typed business facts.

This is not an authority or a parallel Rule IR. It only converts exact, accepted
cardinality and formula facts from the existing business-fact ledger into candidate
shape. Promotion remains owned by ``_candidate_validation``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

FACT_DERIVATION_SCHEMA = "qualibug.implicit-rule-fact-entailment.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _source_refs(fact: dict[str, Any]) -> list[dict[str, Any]]:
    fact_id = _text(fact.get("fact_id") or fact.get("id"))
    result: list[dict[str, Any]] = []
    for span in _list(fact.get("source_spans")):
        if not isinstance(span, dict) or not _text(span.get("source_id")):
            continue
        result.append(
            {
                "source_id": _text(span.get("source_id")),
                "source_locator": _text(
                    span.get("locator") or span.get("source_locator")
                ),
                "kind": "accepted_typed_business_fact",
                "fact_ref": fact_id,
                "document_block_id": span.get("document_block_id"),
            }
        )
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
) -> dict[str, Any] | None:
    fact_id = _text(fact.get("fact_id") or fact.get("id"))
    statement = _text(fact.get("raw_statement") or fact.get("statement"))
    source_refs = _source_refs(fact)
    if not fact_id or not statement or not source_refs:
        return None
    sources = sorted({_text(row.get("source_id")) for row in source_refs})
    return {
        "candidate_id": _stable_id(logical_form, fact_id, consequent, sources),
        "kind": "rule",
        "name": statement,
        "statement": statement,
        "logical_form": logical_form,
        "antecedents": [{"subject_refs": subject_refs, "predicate": "observed"}],
        "consequent": consequent,
        "subject_refs": subject_refs,
        "actor_refs": [],
        "operation_refs": [],
        "table_refs": [],
        "field_refs": [],
        "scope": {},
        "exceptions": [],
        "derivation_basis": ["typed_fact_entailment"],
        "supporting_fact_refs": [fact_id],
        "contradicting_fact_refs": [],
        "source_refs": source_refs,
        "supporting_source_ids": sources,
        "supporting_evidence": source_refs,
        "source_authority": "formal_constraint",
        "falsifiability": "EVALUABLE",
        "binding_readiness": "READY_FOR_IR_BINDING",
        "scope_status": "RESOLVED",
        "exception_status": "NOT_APPLICABLE",
        "counterexample_plan": counterexample_plan,
        "observation_requirements": observation_requirements,
        "risk_type": risk_type,
        "severity": "P1",
        "confidence": min(1.0, max(0.0, float(fact.get("confidence") or 0.9))),
        "status": "CANDIDATE",
        "fact_entailment_schema": FACT_DERIVATION_SCHEMA,
    }


def derive_rule_candidates_from_business_facts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ledger = _dict(asset.get("business_fact_ledger"))
    for fact in _list(ledger.get("items")):
        if not isinstance(fact, dict) or _text(fact.get("status")) != "ACCEPTED":
            continue
        subjects = _entity_refs(fact.get("subject"))
        objects = _entity_refs(fact.get("object"))
        if _text(fact.get("fact_type")) == "CARDINALITY_CONSTRAINT":
            value = _dict(fact.get("value"))
            if subjects and objects and value:
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
                    observation_requirements=["related_collection"],
                    counterexample_plan={
                        "action": "construct_relation_count_outside_declared_cardinality",
                        "observe": "related_collection",
                    },
                )
                if row:
                    candidates.append(row)
        formulas = [
            dict(row)
            for row in _list(fact.get("formula_constraints"))
            if isinstance(row, dict) and _text(row.get("raw"))
        ]
        if _text(fact.get("fact_type")) == "DERIVED_VALUE" and formulas:
            formula = formulas[0]
            lhs = _text(formula.get("lhs"))
            rhs = _text(formula.get("rhs"))
            if lhs and rhs:
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
                    risk_type="conservation",
                    observation_requirements=["formula_operands", "formula_result"],
                    counterexample_plan={
                        "action": "change_one_source_operand",
                        "observe": [lhs, rhs],
                    },
                )
                if row:
                    candidates.append(row)
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
    "derive_rule_candidates_from_business_facts",
    "uncovered_rule_candidate_spans",
]
