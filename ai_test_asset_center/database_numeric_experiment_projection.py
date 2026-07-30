"""Bind numeric business rules to exact approved database fields.

This projection runs after approved database Observer drafts and the database
state-transition projection have been attached to an Experiment.  It replaces
only source ``field_delta`` assertions and simple ``unchanged_sum`` conservation
rules whose every term resolves to exactly one approved database field binding.
No fuzzy matching, automatic conflict winner, cross-contract relation guess, or
observer-authored verdict is permitted.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .database_numeric_oracle import (
    DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
    DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
)
from .database_state_transition_experiment_projection import (
    _binding_identity_values,
    _binding_name_values,
    _dedupe,
    _dict,
    _draft_pairs,
    _fingerprint,
    _name_token,
    _text,
)

PROJECTION_SCHEMA = "qualibug.database-numeric-experiment-projection.v1"
_BLOCK_REASON = "BLOCKED_DATABASE_NUMERIC_ORACLE_BINDING_AMBIGUOUS"
_SOURCE_KINDS = frozenset({"field_delta", "conservation"})
_HTTP_STATE_DEPENDENT_KINDS = frozenset(
    {
        "state_transition",
        "forbidden_state_transition",
        "field_delta",
        "conservation",
        "postcondition",
    }
)
_ID_KEYS = frozenset(
    {
        "field_id",
        "field_ref",
        "api_field_id",
        "database_field_id",
        "field_binding_id",
    }
)
_NAME_KEYS = frozenset(
    {
        "field",
        "field_name",
        "api_field_name",
        "database_field_name",
        "json_path",
        "name",
    }
)


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _source_kind(assertion: dict[str, Any]) -> str:
    return _text(assertion.get("kind") or assertion.get("type")).lower()


def _walk_tokens(value: Any, ids: set[str], names: set[str], literals: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _text(key).lower()
            if normalized in _ID_KEYS and _text(child):
                ids.add(_text(child))
            elif normalized in _NAME_KEYS and _name_token(child):
                names.add(_name_token(child))
            if isinstance(child, (dict, list)):
                _walk_tokens(child, ids, names, literals)
    elif isinstance(value, list):
        for child in value:
            _walk_tokens(child, ids, names, literals)
    elif isinstance(value, str) and _text(value):
        literals.add(_text(value))


def _term_tokens(raw: Any) -> tuple[set[str], set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()
    literals: set[str] = set()
    if isinstance(raw, str):
        literals.add(_text(raw))
    else:
        _walk_tokens(raw, ids, names, literals)
    return ids, names, literals


def _approved_field_candidates(raw_term: Any, experiment: dict[str, Any]) -> list[dict[str, Any]]:
    explicit_ids, explicit_names, literals = _term_tokens(raw_term)
    id_matches: list[dict[str, Any]] = []
    name_matches: list[dict[str, Any]] = []
    for pair in _draft_pairs(experiment):
        contract = _dict(pair.get("contract"))
        for raw_binding in _list(contract.get("field_bindings")):
            binding = _dict(raw_binding)
            if not binding or binding.get("authoritative") is not True:
                continue
            if binding.get("read_only") is not True:
                continue
            if binding.get("oracle_authority_allowed") is True:
                continue
            identities = _binding_identity_values(binding)
            names = _binding_name_values(binding)
            candidate = {**pair, "field_binding": binding}
            if explicit_ids.intersection(identities) or literals.intersection(identities):
                candidate["match_basis"] = "EXACT_FIELD_ID"
                id_matches.append(candidate)
            elif explicit_names.intersection(names) or {
                _name_token(value) for value in literals if _name_token(value)
            }.intersection(names):
                candidate["match_basis"] = "EXACT_FIELD_NAME"
                name_matches.append(candidate)

    # Explicit identifiers and literal values that exactly equal a binding identity
    # dominate presentation names.  A failed explicit id must never downgrade to name.
    selected = id_matches if explicit_ids or id_matches else name_matches
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in selected:
        key = (
            _text(row.get("observer_contract_ref")),
            _text(_dict(row.get("field_binding")).get("field_binding_id")),
        )
        if all(key):
            deduped[key] = row
    return list(deduped.values())


def _delta_terms(assertion: dict[str, Any]) -> list[Any]:
    terms = _list(assertion.get("fields") or assertion.get("operands"))
    if not terms:
        return []
    if len(terms) == 1 and isinstance(terms[0], dict):
        term = dict(terms[0])
        for key in (
            "expected_delta",
            "expected_delta_direction",
            "expected_value",
            "tolerance",
        ):
            if key not in term and key in assertion:
                term[key] = assertion.get(key)
        return [term]
    return terms


def _conservation_terms(assertion: dict[str, Any]) -> tuple[list[Any], str]:
    if _dict(assertion.get("structured_expression")):
        return [], "DATABASE_NUMERIC_STRUCTURED_EXPRESSION_REQUIRES_RELATION_OBSERVER"
    equation = _dict(assertion.get("equation"))
    operator = _text(equation.get("operator") or "unchanged_sum").lower()
    if operator not in {"unchanged_sum", "conservation"}:
        return [], "DATABASE_NUMERIC_CONSERVATION_OPERATOR_UNSUPPORTED"
    terms = _list(equation.get("terms") or equation.get("fields"))
    if not terms:
        return [], "DATABASE_NUMERIC_CONSERVATION_TERMS_MISSING"
    return terms, ""


def _term_projection(
    raw_term: Any,
    candidate: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    source = _dict(raw_term)
    binding = _dict(candidate.get("field_binding"))
    contract = _dict(candidate.get("contract"))
    before = _dict(candidate.get("before_draft"))
    after = _dict(candidate.get("after_draft"))
    evidence = [
        dict(row)
        for row in _list(binding.get("evidence"))
        if isinstance(row, dict)
    ]
    return {
        "term_id": _text(source.get("term_id"))
        or f"numeric-term:{index}:{_text(binding.get('field_binding_id'))}",
        "database_observer_contract_ref": _text(candidate.get("observer_contract_ref")),
        "before_draft_id": _text(before.get("draft_id")),
        "after_draft_id": _text(after.get("draft_id")),
        "database_table_ref": _text(contract.get("database_table_id")),
        "database_table_name": _text(contract.get("database_table_name")),
        "database_field_id": _text(binding.get("database_field_id")),
        "database_field_name": _text(binding.get("database_field_name")),
        "api_field_id": _text(binding.get("api_field_id")),
        "api_field_name": _text(binding.get("api_field_name")),
        "field_binding_id": _text(binding.get("field_binding_id")),
        "mapping_decision_id": _text(binding.get("mapping_decision_id")),
        "match_basis": _text(candidate.get("match_basis")),
        "expected_delta": source.get("expected_delta"),
        "expected_delta_direction": _text(source.get("expected_delta_direction")),
        "expected_value": source.get("expected_value"),
        "tolerance": source.get("tolerance"),
        "coefficient": source.get("coefficient", 1),
        "source_evidence": evidence,
        "automatic_field_inference": False,
        "fuzzy_name_matching": False,
        "observer_oracle_authority": False,
    }


def _project_assertion(
    assertion: dict[str, Any],
    projected_terms: list[dict[str, Any]],
) -> dict[str, Any]:
    source_kind = _source_kind(assertion)
    if source_kind == "field_delta":
        kind = DATABASE_NUMERIC_DELTA_ASSERTION_KIND
        policy = "FIELD_DELTA"
    else:
        kind = DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND
        policy = "UNCHANGED_WEIGHTED_SUM"
    return {
        **deepcopy(assertion),
        "kind": kind,
        "source_assertion_kind": source_kind,
        "numeric_policy": policy,
        "numeric_terms": projected_terms,
        "database_numeric_binding": {
            "schema": PROJECTION_SCHEMA,
            "numeric_policy": policy,
            "term_count": len(projected_terms),
            "observer_contract_refs": sorted(
                {
                    _text(row.get("database_observer_contract_ref"))
                    for row in projected_terms
                    if _text(row.get("database_observer_contract_ref"))
                }
            ),
            "automatic_field_mapping": False,
            "automatic_relation_mapping": False,
            "fuzzy_name_matching": False,
            "observer_oracle_authority": False,
        },
    }


def _blocked(experiment: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(experiment)
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_numeric_oracle_detail": detail,
        }
    )
    row.update(
        {
            "compile_receipt": receipt,
            "compile_status": "BLOCKED",
            "database_numeric_projection_status": "BLOCKED",
        }
    )
    return row


def _source_requires_http_state(assertion: dict[str, Any]) -> bool:
    return _source_kind(assertion) in _HTTP_STATE_DEPENDENT_KINDS


def project_database_numeric_assertions(experiment_pack: dict[str, Any]) -> dict[str, Any]:
    """Project exact numeric assertions and preserve unsupported cases visibly."""
    pack = dict(experiment_pack or {})
    compiled: list[dict[str, Any]] = []
    newly_blocked: list[dict[str, Any]] = []
    projected_count = 0
    incomplete_count = 0

    for raw in _list(pack.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        projected: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        ambiguous: dict[str, Any] | None = None

        for assertion in [
            dict(row)
            for row in _list(experiment.get("assertions"))
            if isinstance(row, dict)
        ]:
            source_kind = _source_kind(assertion)
            if source_kind not in _SOURCE_KINDS:
                projected.append(assertion)
                continue
            if source_kind == "field_delta":
                raw_terms = _delta_terms(assertion)
                term_error = "" if raw_terms else "DATABASE_NUMERIC_DELTA_TERMS_MISSING"
            else:
                raw_terms, term_error = _conservation_terms(assertion)
            if term_error:
                gaps.append(
                    {
                        "assertion_id": _text(assertion.get("assertion_id")),
                        "reason_code": term_error,
                    }
                )
                projected.append(assertion)
                incomplete_count += 1
                continue

            terms: list[dict[str, Any]] = []
            missing_term: dict[str, Any] | None = None
            for index, raw_term in enumerate(raw_terms):
                matches = _approved_field_candidates(raw_term, experiment)
                if len(matches) > 1:
                    ambiguous = {
                        "assertion_id": _text(assertion.get("assertion_id")),
                        "term_index": index,
                        "candidate_count": len(matches),
                        "candidate_refs": [
                            {
                                "observer_contract_ref": _text(row.get("observer_contract_ref")),
                                "field_binding_id": _text(
                                    _dict(row.get("field_binding")).get("field_binding_id")
                                ),
                                "database_field_id": _text(
                                    _dict(row.get("field_binding")).get("database_field_id")
                                ),
                            }
                            for row in matches
                        ],
                        "automatic_winner_allowed": False,
                    }
                    break
                if not matches:
                    ids, names, literals = _term_tokens(raw_term)
                    missing_term = {
                        "assertion_id": _text(assertion.get("assertion_id")),
                        "term_index": index,
                        "reason_code": "DATABASE_NUMERIC_EXACT_FIELD_BINDING_MISSING",
                        "explicit_field_ids": sorted(ids),
                        "explicit_field_names": sorted(names),
                        "literal_terms": sorted(literals),
                        "fuzzy_matching_allowed": False,
                    }
                    break
                terms.append(_term_projection(raw_term, matches[0], index=index))
            if ambiguous is not None:
                break
            if missing_term is not None:
                gaps.append(missing_term)
                projected.append(assertion)
                incomplete_count += 1
                continue

            if source_kind == "conservation":
                contract_refs = {
                    _text(row.get("database_observer_contract_ref")) for row in terms
                }
                if len(contract_refs) != 1:
                    gaps.append(
                        {
                            "assertion_id": _text(assertion.get("assertion_id")),
                            "reason_code": "DATABASE_NUMERIC_CROSS_CONTRACT_SCOPE_UNPROVEN",
                            "observer_contract_refs": sorted(contract_refs),
                            "automatic_relation_mapping_allowed": False,
                        }
                    )
                    projected.append(assertion)
                    incomplete_count += 1
                    continue

            projected.append(_project_assertion(assertion, terms))
            projected_count += 1

        if ambiguous is not None:
            newly_blocked.append(_blocked(experiment, ambiguous))
            continue

        experiment["assertions"] = projected
        numeric_rows = [
            row
            for row in projected
            if _source_kind(row)
            in {
                DATABASE_NUMERIC_DELTA_ASSERTION_KIND,
                DATABASE_NUMERIC_CONSERVATION_ASSERTION_KIND,
            }
        ]
        if numeric_rows:
            remaining_http_state = any(_source_requires_http_state(row) for row in projected)
            if not remaining_http_state:
                experiment["observers"] = [
                    dict(row)
                    for row in _list(experiment.get("observers"))
                    if isinstance(row, dict)
                    and _text(row.get("observer_id")) not in {"before_state", "after_state"}
                ]
            fingerprint = _fingerprint(numeric_rows)
            status = "PARTIAL" if gaps else "BOUND"
            receipt = _dict(experiment.get("compile_receipt"))
            receipt.update(
                {
                    "database_numeric_projection_status": status,
                    "database_numeric_assertion_fingerprint": fingerprint,
                    "database_numeric_assertion_count": len(numeric_rows),
                    "database_numeric_fuzzy_matching_used": False,
                    "database_numeric_automatic_relation_mapping_used": False,
                    "database_numeric_http_observers_removed": not remaining_http_state,
                }
            )
            experiment["compile_receipt"] = receipt
            experiment["database_numeric_projection_status"] = status
            experiment["database_numeric_assertion_fingerprint"] = fingerprint
            runtime_contract = _dict(experiment.get("field_oracle_runtime_contract"))
            if runtime_contract:
                kinds = {
                    _text(value)
                    for value in _list(runtime_contract.get("assertion_kinds"))
                    if _text(value)
                }
                if _text(runtime_contract.get("assertion_kind")):
                    kinds.add(_text(runtime_contract.get("assertion_kind")))
                kinds.update(_source_kind(row) for row in numeric_rows)
                runtime_contract.update(
                    {
                        "database_numeric_bound": True,
                        "assertion_kinds": sorted(kinds),
                    }
                )
                experiment["field_oracle_runtime_contract"] = runtime_contract
        elif gaps:
            experiment["database_numeric_projection_status"] = "INCOMPLETE"
        else:
            experiment["database_numeric_projection_status"] = "NOT_APPLICABLE"
        if gaps:
            experiment["database_numeric_projection_gaps"] = gaps
        compiled.append(experiment)

    existing_blocked = [
        dict(row)
        for row in _list(pack.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    blocked = [*existing_blocked, *newly_blocked]
    reason_counts = dict(_dict(pack.get("block_reason_counts")))
    if newly_blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(newly_blocked)
    pack.update(
        {
            "experiments": compiled,
            "blocked_experiments": blocked,
            "compiled_count": len(compiled),
            "blocked_count": len(blocked),
            "block_reason_counts": reason_counts,
            "database_numeric_experiment_projection": {
                "schema": PROJECTION_SCHEMA,
                "status": "BLOCKED"
                if newly_blocked
                else "PARTIAL"
                if incomplete_count
                else "PASS",
                "projected_assertion_count": projected_count,
                "incomplete_assertion_count": incomplete_count,
                "newly_blocked_experiment_count": len(newly_blocked),
                "automatic_field_mapping_count": 0,
                "fuzzy_name_matching_count": 0,
                "automatic_relation_mapping_count": 0,
                "automatic_conflict_winner_count": 0,
                "second_oracle_created": False,
                "contract_oracle_remains_authority": True,
            },
        }
    )
    return pack


__all__ = ["PROJECTION_SCHEMA", "project_database_numeric_assertions"]
