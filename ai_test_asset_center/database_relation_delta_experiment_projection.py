"""Bind explicit cross-table delta rules to exact approved FK phase observers.

A rule is eligible only when both sides explicitly declare a delta operation and
exactly one delta operand is a root-row field while the other is an approved
FK-scoped aggregate. The projection creates BEFORE and AFTER relation aggregate
drafts on the existing phase executor. It never guesses a relation, field, sign,
or empty-collection policy, and Contract Oracle remains the only verdict authority.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .database_relation_numeric_experiment_projection import (
    DRAFT_SCHEMA,
    _aggregate,
    _dict,
    _entity_matches,
    _list,
    _match_child_field,
    _match_root_field,
    _relation_key,
    _stable_id,
    _text,
)
from .database_state_transition_experiment_projection import _draft_pairs

ASSERTION_KIND = "database_relation_delta_conservation"
PROJECTION_SCHEMA = "qualibug.database-relation-delta-experiment-projection.v1"
_BLOCK_REASON = "BLOCKED_DATABASE_RELATION_DELTA_ORACLE_BINDING_AMBIGUOUS"
_SOURCE_KINDS = frozenset(
    {
        "conservation",
        "cross_entity_consistency",
        "field_delta",
        "delta_conservation",
        "relation_delta_conservation",
    }
)
_AGGREGATES = frozenset({"COUNT", "SUM", "MIN", "MAX"})
_OPERATORS = frozenset(
    {
        "EQ", "EQUALS", "==",
        "LTE", "LE", "<=",
        "GTE", "GE", ">=",
        "LT", "<",
        "GT", ">",
        "NEQ", "NE", "!=",
    }
)
_HTTP_STATE_DEPENDENT_KINDS = frozenset(
    {
        "state_transition",
        "forbidden_state_transition",
        "field_delta",
        "conservation",
        "cross_entity_consistency",
        "postcondition",
        "delta_conservation",
        "relation_delta_conservation",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _source_kind(assertion: dict[str, Any]) -> str:
    return _text(assertion.get("kind") or assertion.get("type")).lower()


def _delta_operand(side: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an explicitly wrapped delta operand and wrapper metadata."""
    row = _dict(side)
    marker = _text(row.get("node_type") or row.get("type")).lower()
    function = _text(row.get("function") or row.get("operator")).upper()
    explicit = (
        marker in {"delta", "field_delta", "aggregate_delta"}
        or row.get("delta") is True
        or function == "DELTA"
    )
    if not explicit:
        return {}, {}

    nested = _dict(
        row.get("operand")
        or row.get("value")
        or row.get("expression")
        or row.get("of")
    )
    if nested:
        return nested, row

    if marker in {"field_delta", "aggregate_delta"} or row.get("delta") is True:
        operand = deepcopy(row)
        operand.pop("delta", None)
        for key in ("coefficient", "weight"):
            operand.pop(key, None)
        if marker in {"field_delta", "aggregate_delta"}:
            operand.pop("node_type", None)
            operand.pop("type", None)
            if marker == "aggregate_delta" and not _aggregate(operand):
                return {}, {}
        return operand, row
    return {}, {}


def _expression(
    assertion: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    str,
]:
    expr = _dict(assertion.get("structured_expression"))
    left_raw = _dict(expr.get("left"))
    right_raw = _dict(expr.get("right"))
    left, left_wrapper = _delta_operand(left_raw)
    right, right_wrapper = _delta_operand(right_raw)
    operator = _text(expr.get("operator") or assertion.get("operator")).upper()
    return left, right, left_wrapper, right_wrapper, operator


def _coefficient(wrapper: dict[str, Any], operand: dict[str, Any]) -> Any:
    if "coefficient" in wrapper:
        return wrapper.get("coefficient")
    if "weight" in wrapper:
        return wrapper.get("weight")
    if "coefficient" in operand:
        return operand.get("coefficient")
    if "weight" in operand:
        return operand.get("weight")
    return 1


def _relation_contract_eligible(relation: dict[str, Any]) -> bool:
    return bool(
        _text(relation.get("schema"))
        == "qualibug.database-relation-observer-contract.v1"
        and _text(relation.get("status"))
        == "READY_FOR_RUNTIME_CONNECTION_BINDING"
        and relation.get("runtime_observer_authoritative") is True
        and relation.get("read_only") is True
        and relation.get("mutation_allowed") is False
        and relation.get("write_target_allowed") is False
        and relation.get("oracle_authority_allowed") is False
        and _text(relation.get("database_relationship_id"))
        and _relation_key(relation)
    )


def _candidates(
    assertion: dict[str, Any],
    experiment: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    left, right, left_wrapper, right_wrapper, operator = _expression(assertion)
    recognized = bool(left and right and operator)
    if not recognized:
        return [], False

    left_aggregate = _aggregate(left)
    right_aggregate = _aggregate(right)
    if left_aggregate in _AGGREGATES and not right_aggregate:
        aggregate_side, root_side, aggregate_on_left = left, right, True
        aggregate = left_aggregate
    elif right_aggregate in _AGGREGATES and not left_aggregate:
        aggregate_side, root_side, aggregate_on_left = right, left, False
        aggregate = right_aggregate
    else:
        return [], True
    if operator not in _OPERATORS:
        return [], True

    output: list[dict[str, Any]] = []
    root_pairs = _draft_pairs(experiment)
    for raw_relation in _list(experiment.get("database_relation_observer_contracts")):
        relation = _dict(raw_relation)
        if (
            not _relation_contract_eligible(relation)
            or not _entity_matches(relation, aggregate_side, root_side)
        ):
            continue
        root_ref = _text(relation.get("root_observer_id"))
        for root_pair in root_pairs:
            if _text(root_pair.get("observer_contract_ref")) != root_ref:
                continue
            root = _dict(root_pair.get("contract"))
            root_fields = _match_root_field(root, root_side)
            child_fields = _match_child_field(relation, aggregate_side, aggregate)
            if len(root_fields) != 1 or len(child_fields) != 1:
                continue
            output.append(
                {
                    "relation_contract": relation,
                    "root_pair": root_pair,
                    "root_contract": root,
                    "root_field": root_fields[0],
                    "child_field": child_fields[0],
                    "aggregate": aggregate,
                    "operator": operator,
                    "aggregate_on_left": aggregate_on_left,
                    "left_coefficient": _coefficient(left_wrapper, left),
                    "right_coefficient": _coefficient(right_wrapper, right),
                }
            )

    deduped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in output:
        relation = _dict(row.get("relation_contract"))
        pair = _dict(row.get("root_pair"))
        root_field = _dict(row.get("root_field"))
        child_field = _dict(row.get("child_field"))
        key = (
            _text(relation.get("relation_observer_id")),
            _text(_dict(pair.get("before_draft")).get("draft_id")),
            _text(_dict(pair.get("after_draft")).get("draft_id")),
            _text(root_field.get("field_binding_id")),
            _text(child_field.get("database_field_id")) or "COUNT_ROWS",
        )
        if all(key):
            deduped[key] = row
    return list(deduped.values()), True


def _aggregate_requests(
    aggregate: str,
    child_field: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    primary = {
        "aggregate": aggregate,
        "database_field_id": _text(child_field.get("database_field_id")),
        "database_field_name": _text(child_field.get("database_field_name")),
        "alias": "related_value",
    }
    requests = [primary]
    scope_count_alias = ""
    if aggregate in {"SUM", "MIN", "MAX"}:
        scope_count_alias = "related_scope_count"
        requests.append(
            {
                "aggregate": "COUNT",
                "database_field_id": "",
                "database_field_name": "",
                "alias": scope_count_alias,
            }
        )
    return requests, scope_count_alias


def _relation_draft(
    *,
    relation: dict[str, Any],
    assertion_id: str,
    phase: str,
    requests: list[dict[str, Any]],
    pair_id: str,
) -> dict[str, Any]:
    relation_ref = _text(relation.get("relation_observer_id"))
    target = _text(phase).upper()
    return {
        "schema": DRAFT_SCHEMA,
        "draft_id": _stable_id(
            "database_relation_observer_execution_draft",
            relation_ref,
            assertion_id,
            target,
            pair_id,
        ),
        "relation_pair_id": pair_id,
        "observer_handler_id": "approved_database_relation_aggregate",
        "relation_observer_contract_ref": relation_ref,
        "root_observer_contract_ref": _text(relation.get("root_observer_id")),
        "observation_phase": target,
        "database_relation_observer_contract": deepcopy(relation),
        "aggregate_requests": deepcopy(requests),
        "identity_value_sources": sorted(
            {
                _text(row.get("value_source"))
                for row in _list(relation.get("relation_predicates"))
                if isinstance(row, dict) and _text(row.get("value_source"))
            }
        ),
        "database_connection_ref": "",
        "required": True,
        "runtime_connection_bound": False,
        "query_executed": False,
        "raw_sql_retained": False,
        "predicate_values_retained": False,
        "secret_values_retained": False,
        "oracle_verdict_emitted": False,
        "write_target_allowed": False,
        "mutation_allowed": False,
    }


def _projection(
    assertion: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    relation = _dict(candidate.get("relation_contract"))
    pair = _dict(candidate.get("root_pair"))
    root = _dict(candidate.get("root_contract"))
    root_field = _dict(candidate.get("root_field"))
    child_field = _dict(candidate.get("child_field"))
    before_root = _dict(pair.get("before_draft"))
    after_root = _dict(pair.get("after_draft"))
    relation_ref = _text(relation.get("relation_observer_id"))
    aggregate = _text(candidate.get("aggregate")).upper()
    relation_key = _relation_key(relation)
    requests, scope_count_alias = _aggregate_requests(aggregate, child_field)
    pair_id = _stable_id(
        "database_relation_delta_pair",
        relation_ref,
        assertion.get("assertion_id"),
        before_root.get("draft_id"),
        after_root.get("draft_id"),
        aggregate,
        child_field.get("database_field_id"),
    )
    before_relation = _relation_draft(
        relation=relation,
        assertion_id=_text(assertion.get("assertion_id")),
        phase="BEFORE",
        requests=requests,
        pair_id=pair_id,
    )
    after_relation = _relation_draft(
        relation=relation,
        assertion_id=_text(assertion.get("assertion_id")),
        phase="AFTER",
        requests=requests,
        pair_id=pair_id,
    )
    projected = {
        **deepcopy(assertion),
        "kind": ASSERTION_KIND,
        "source_assertion_kind": _source_kind(assertion),
        "database_relation_observer_ref": relation_ref,
        "database_relationship_id": _text(relation.get("database_relationship_id")),
        "relation_key": relation_key,
        "relation_pair_id": pair_id,
        "relation_before_draft_id": _text(before_relation.get("draft_id")),
        "relation_after_draft_id": _text(after_relation.get("draft_id")),
        "root_observer_contract_ref": _text(pair.get("observer_contract_ref")),
        "root_before_draft_id": _text(before_root.get("draft_id")),
        "root_after_draft_id": _text(after_root.get("draft_id")),
        "root_table_ref": _text(root.get("database_table_id")),
        "root_table_name": _text(root.get("database_table_name")),
        "root_field_binding_id": _text(root_field.get("field_binding_id")),
        "root_database_field_id": _text(root_field.get("database_field_id")),
        "root_database_field_name": _text(root_field.get("database_field_name")),
        "child_table_ref": _text(relation.get("child_table_id")),
        "child_table_name": _text(relation.get("child_table_name")),
        "child_database_field_id": _text(child_field.get("database_field_id")),
        "child_database_field_name": _text(child_field.get("database_field_name")),
        "aggregate": aggregate,
        "aggregate_alias": "related_value",
        "scope_count_alias": scope_count_alias,
        "comparison_operator": _text(candidate.get("operator")).upper(),
        "aggregate_on_left": candidate.get("aggregate_on_left") is True,
        "left_coefficient": candidate.get("left_coefficient", 1),
        "right_coefficient": candidate.get("right_coefficient", 1),
        "comparison_phase_pair": "BEFORE_AFTER",
        "tolerance": (
            assertion.get("tolerance")
            or _dict(assertion.get("structured_expression")).get("tolerance")
        ),
        "database_relation_delta_binding": {
            "schema": PROJECTION_SCHEMA,
            "database_relationship_id": _text(relation.get("database_relationship_id")),
            "relation_mapping_decision_id": _text(relation.get("relation_mapping_decision_id")),
            "relation_key": relation_key,
            "relation_pair_id": pair_id,
            "root_before_draft_id": _text(before_root.get("draft_id")),
            "root_after_draft_id": _text(after_root.get("draft_id")),
            "relation_before_draft_id": _text(before_relation.get("draft_id")),
            "relation_after_draft_id": _text(after_relation.get("draft_id")),
            "root_field_match_basis": _text(root_field.get("match_basis")),
            "child_field_match_basis": _text(child_field.get("match_basis")),
            "empty_sum_zero_requires_scope_count": aggregate == "SUM",
            "automatic_relation_mapping": False,
            "automatic_field_mapping": False,
            "automatic_sign_inference": False,
            "fuzzy_name_matching": False,
            "client_side_filtering": False,
            "observer_oracle_authority": False,
        },
    }
    return projected, [before_relation, after_relation]


def _blocked(
    experiment: dict[str, Any],
    assertion: dict[str, Any],
    matches: list[dict[str, Any]],
) -> dict[str, Any]:
    row = deepcopy(experiment)
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_relation_delta_oracle_detail": {
                "assertion_id": _text(assertion.get("assertion_id")),
                "candidate_count": len(matches),
                "candidate_relation_refs": sorted(
                    _text(_dict(match.get("relation_contract")).get("relation_observer_id"))
                    for match in matches
                ),
                "candidate_root_before_draft_ids": sorted(
                    _text(_dict(_dict(match.get("root_pair")).get("before_draft")).get("draft_id"))
                    for match in matches
                ),
                "candidate_root_after_draft_ids": sorted(
                    _text(_dict(_dict(match.get("root_pair")).get("after_draft")).get("draft_id"))
                    for match in matches
                ),
                "automatic_winner_allowed": False,
            },
        }
    )
    row["compile_receipt"] = receipt
    row["compile_status"] = "BLOCKED"
    row["database_relation_delta_projection_status"] = "BLOCKED"
    return row


def project_database_relation_delta_assertions(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    pack = dict(experiment_pack or {})
    compiled: list[dict[str, Any]] = []
    newly_blocked: list[dict[str, Any]] = []
    projected_count = 0
    incomplete_count = 0

    for raw in _list(pack.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        projected_assertions: list[dict[str, Any]] = []
        relation_drafts = [
            dict(row)
            for row in _list(experiment.get("database_relation_observer_execution_drafts"))
            if isinstance(row, dict)
        ]
        gaps: list[dict[str, Any]] = []
        ambiguous: tuple[dict[str, Any], list[dict[str, Any]]] | None = None

        for raw_assertion in _list(experiment.get("assertions")):
            assertion = _dict(raw_assertion)
            if _source_kind(assertion) not in _SOURCE_KINDS:
                projected_assertions.append(assertion)
                continue
            matches, recognized = _candidates(assertion, experiment)
            if not recognized:
                projected_assertions.append(assertion)
                continue
            if len(matches) > 1:
                ambiguous = (assertion, matches)
                break
            if not matches:
                projected_assertions.append(assertion)
                gaps.append(
                    {
                        "assertion_id": _text(assertion.get("assertion_id")),
                        "reason_code": "DATABASE_RELATION_DELTA_EXACT_BINDING_MISSING",
                        "before_after_root_pair_required": True,
                        "before_after_relation_pair_required": True,
                        "automatic_relation_mapping_allowed": False,
                        "automatic_sign_inference_allowed": False,
                        "fuzzy_matching_allowed": False,
                    }
                )
                incomplete_count += 1
                continue
            projected, drafts = _projection(assertion, matches[0])
            projected_assertions.append(projected)
            relation_drafts.extend(drafts)
            projected_count += 1

        if ambiguous is not None:
            newly_blocked.append(_blocked(experiment, ambiguous[0], ambiguous[1]))
            continue

        experiment["assertions"] = projected_assertions
        unique_drafts = {
            _text(row.get("draft_id")): row
            for row in relation_drafts
            if _text(row.get("draft_id"))
        }
        experiment["database_relation_observer_execution_drafts"] = list(unique_drafts.values())
        bound = [
            row
            for row in projected_assertions
            if _text(row.get("kind")) == ASSERTION_KIND
        ]
        if bound:
            observers = [
                dict(row)
                for row in _list(experiment.get("observers"))
                if isinstance(row, dict)
            ]
            if "approved_database_relation_phase_aggregate" not in {
                _text(row.get("observer_id")) for row in observers
            }:
                observers.append(
                    {
                        "observer_id": "approved_database_relation_phase_aggregate",
                        "adapter": "db_sql",
                    }
                )
            unresolved_http = any(
                _source_kind(row) in _HTTP_STATE_DEPENDENT_KINDS
                for row in projected_assertions
            )
            if not unresolved_http:
                observers = [
                    row
                    for row in observers
                    if _text(row.get("observer_id"))
                    not in {"before_state", "after_state"}
                ]
            experiment["observers"] = observers

            fingerprint = _fingerprint(bound)
            status = "PARTIAL" if gaps else "BOUND"
            receipt = _dict(experiment.get("compile_receipt"))
            receipt.update(
                {
                    "database_relation_delta_projection_status": status,
                    "database_relation_delta_assertion_count": len(bound),
                    "database_relation_delta_assertion_fingerprint": fingerprint,
                    "database_relation_delta_automatic_mapping_used": False,
                    "database_relation_delta_automatic_sign_inference_used": False,
                }
            )
            experiment["compile_receipt"] = receipt
            experiment["database_relation_delta_projection_status"] = status
            experiment["database_relation_delta_assertion_fingerprint"] = fingerprint
            runtime_contract = _dict(experiment.get("field_oracle_runtime_contract"))
            if runtime_contract:
                runtime_contract.update(
                    {
                        "before_observation_contract": "approved_database_phase_aggregate",
                        "after_observation_contract": "approved_database_phase_aggregate",
                        "relation_observation_contract": "approved_database_relation_phase_aggregate",
                        "assertion_kind": ASSERTION_KIND,
                        "database_relation_delta_bound": True,
                    }
                )
                experiment["field_oracle_runtime_contract"] = runtime_contract
        elif gaps:
            experiment["database_relation_delta_projection_status"] = "INCOMPLETE"
        else:
            experiment["database_relation_delta_projection_status"] = "NOT_APPLICABLE"
        if gaps:
            experiment["database_relation_delta_projection_gaps"] = gaps
        compiled.append(experiment)

    existing_blocked = [
        dict(row)
        for row in _list(pack.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    all_blocked = [*existing_blocked, *newly_blocked]
    reason_counts = dict(_dict(pack.get("block_reason_counts")))
    if newly_blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(newly_blocked)
    pack.update(
        {
            "experiments": compiled,
            "blocked_experiments": all_blocked,
            "compiled_count": len(compiled),
            "blocked_count": len(all_blocked),
            "block_reason_counts": reason_counts,
            "database_relation_delta_experiment_projection": {
                "schema": PROJECTION_SCHEMA,
                "status": (
                    "BLOCKED"
                    if newly_blocked
                    else "PARTIAL"
                    if incomplete_count
                    else "PASS"
                ),
                "projected_assertion_count": projected_count,
                "incomplete_assertion_count": incomplete_count,
                "newly_blocked_experiment_count": len(newly_blocked),
                "automatic_relation_mapping_count": 0,
                "automatic_field_mapping_count": 0,
                "automatic_sign_inference_count": 0,
                "fuzzy_name_matching_count": 0,
                "client_side_filter_count": 0,
                "second_oracle_created": False,
                "contract_oracle_remains_authority": True,
            },
        }
    )
    return pack


__all__ = [
    "ASSERTION_KIND",
    "PROJECTION_SCHEMA",
    "project_database_relation_delta_assertions",
]
