"""Bind structured cross-table numeric rules to exact approved FK relation observers."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

ASSERTION_KIND = "database_relation_conservation"
PROJECTION_SCHEMA = "qualibug.database-relation-numeric-experiment-projection.v1"
DRAFT_SCHEMA = "qualibug.database-relation-observer-execution-draft.v1"
_BLOCK_REASON = "BLOCKED_DATABASE_RELATION_ORACLE_BINDING_AMBIGUOUS"
_SOURCE_KINDS = frozenset({"conservation", "limit_constraint", "cross_entity_consistency"})
_AGGREGATES = frozenset({"COUNT", "SUM", "MIN", "MAX"})
_OPERATORS = frozenset({"EQ", "EQUALS", "==", "LTE", "LE", "<=", "GTE", "GE", ">=", "LT", "<", "GT", ">", "NEQ", "NE", "!="})


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _name(value: Any) -> str:
    text = _text(value)
    if text.startswith("$."):
        text = text[2:]
    text = text.replace("[]", "").strip(".")
    return text.rsplit(".", 1)[-1].casefold() if text else ""


def _identifier(value: Any) -> bool:
    text = _text(value)
    return bool(text.startswith("#/") or ":" in text or text.startswith(("field_", "binding_")))


def _side_field(side: dict[str, Any]) -> tuple[str, str]:
    raw = side.get("field_id") or side.get("database_field_id") or side.get("value_field") or side.get("field")
    return (_text(raw), "ID") if _identifier(raw) else (_name(raw), "NAME")


def _side_entity(side: dict[str, Any]) -> str:
    return _name(side.get("entity") or side.get("entity_name") or side.get("source_entity_name") or side.get("source_entity_alias"))


def _aggregate(side: dict[str, Any]) -> str:
    return _text(side.get("aggregate") or side.get("function")).upper()


def _expression(assertion: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    expr = _dict(assertion.get("structured_expression"))
    return _dict(expr.get("left")), _dict(expr.get("right")), _text(expr.get("operator") or assertion.get("operator")).upper()


def _root_contracts(experiment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(experiment.get("database_observer_execution_drafts")):
        row = _dict(raw)
        contract = _dict(row.get("database_observer_contract"))
        ref = _text(row.get("observer_contract_ref"))
        if ref and contract:
            result[ref] = contract
    return result


def _match_root_field(contract: dict[str, Any], side: dict[str, Any]) -> list[dict[str, Any]]:
    token, basis = _side_field(side)
    if not token:
        return []
    matches: list[dict[str, Any]] = []
    for raw in _list(contract.get("field_bindings")):
        row = _dict(raw)
        if row.get("authoritative") is not True or row.get("read_only") is not True:
            continue
        identities = {
            _text(row.get("field_binding_id")),
            _text(row.get("api_field_id")),
            _text(row.get("database_field_id")),
        }
        names = {
            _name(row.get("api_field_name")),
            _name(row.get("database_field_name")),
        }
        if (basis == "ID" and token in identities) or (basis == "NAME" and token in names):
            matches.append({**row, "match_basis": f"EXACT_FIELD_{basis}"})
    return matches


def _match_child_field(contract: dict[str, Any], side: dict[str, Any], aggregate: str) -> list[dict[str, Any]]:
    token, basis = _side_field(side)
    if aggregate == "COUNT" and not token:
        return [{"database_field_id": "", "database_field_name": "", "match_basis": "COUNT_ROWS"}]
    if not token:
        return []
    matches: list[dict[str, Any]] = []
    for raw in _list(contract.get("allowed_child_fields")):
        row = _dict(raw)
        identity = _text(row.get("database_field_id"))
        name = _name(row.get("database_field_name"))
        if (basis == "ID" and token == identity) or (basis == "NAME" and token == name):
            matches.append({**row, "match_basis": f"EXACT_FIELD_{basis}"})
    return matches


def _entity_matches(contract: dict[str, Any], aggregate_side: dict[str, Any], root_side: dict[str, Any]) -> bool:
    child_entity = _side_entity(aggregate_side)
    parent_entity = _side_entity(root_side)
    if child_entity and child_entity not in {
        _name(contract.get("child_table_name")),
        _name(contract.get("child_table_id")),
    }:
        return False
    if parent_entity and parent_entity not in {
        _name(contract.get("parent_table_name")),
        _name(contract.get("parent_table_id")),
    }:
        return False
    return True


def _candidates(assertion: dict[str, Any], experiment: dict[str, Any]) -> list[dict[str, Any]]:
    left, right, operator = _expression(assertion)
    left_aggregate = _aggregate(left)
    right_aggregate = _aggregate(right)
    if left_aggregate in _AGGREGATES and not right_aggregate:
        aggregate_side, root_side, aggregate_on_left = left, right, True
        aggregate = left_aggregate
    elif right_aggregate in _AGGREGATES and not left_aggregate:
        aggregate_side, root_side, aggregate_on_left = right, left, False
        aggregate = right_aggregate
    else:
        return []
    if operator not in _OPERATORS:
        return []

    roots = _root_contracts(experiment)
    output: list[dict[str, Any]] = []
    for raw in _list(experiment.get("database_relation_observer_contracts")):
        relation = _dict(raw)
        if (
            _text(relation.get("schema")) != "qualibug.database-relation-observer-contract.v1"
            or _text(relation.get("status")) != "READY_FOR_RUNTIME_CONNECTION_BINDING"
            or relation.get("runtime_observer_authoritative") is not True
            or not _entity_matches(relation, aggregate_side, root_side)
        ):
            continue
        root = roots.get(_text(relation.get("root_observer_id")))
        if not root:
            continue
        root_fields = _match_root_field(root, root_side)
        child_fields = _match_child_field(relation, aggregate_side, aggregate)
        if len(root_fields) == 1 and len(child_fields) == 1:
            output.append(
                {
                    "relation_contract": relation,
                    "root_contract": root,
                    "root_field": root_fields[0],
                    "child_field": child_fields[0],
                    "aggregate": aggregate,
                    "operator": operator,
                    "aggregate_on_left": aggregate_on_left,
                }
            )
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in output:
        key = (
            _text(_dict(row.get("relation_contract")).get("relation_observer_id")),
            _text(_dict(row.get("root_field")).get("field_binding_id")),
            _text(_dict(row.get("child_field")).get("database_field_id")) or "COUNT_ROWS",
        )
        deduped[key] = row
    return list(deduped.values())


def _projection(assertion: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    relation = _dict(candidate.get("relation_contract"))
    root = _dict(candidate.get("root_contract"))
    root_field = _dict(candidate.get("root_field"))
    child_field = _dict(candidate.get("child_field"))
    relation_ref = _text(relation.get("relation_observer_id"))
    aggregate = _text(candidate.get("aggregate"))
    request = {
        "aggregate": aggregate,
        "database_field_id": _text(child_field.get("database_field_id")),
        "database_field_name": _text(child_field.get("database_field_name")),
        "alias": "related_value",
    }
    draft = {
        "schema": DRAFT_SCHEMA,
        "draft_id": _stable_id(
            "database_relation_observer_execution_draft",
            relation_ref,
            assertion.get("assertion_id"),
            "AFTER",
        ),
        "observer_handler_id": "approved_database_relation_aggregate",
        "relation_observer_contract_ref": relation_ref,
        "root_observer_contract_ref": _text(relation.get("root_observer_id")),
        "observation_phase": "AFTER",
        "database_relation_observer_contract": deepcopy(relation),
        "aggregate_requests": [request],
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
    projected = {
        **deepcopy(assertion),
        "kind": ASSERTION_KIND,
        "source_assertion_kind": _text(assertion.get("kind") or assertion.get("type")),
        "database_relation_observer_ref": relation_ref,
        "database_relation_draft_id": draft["draft_id"],
        "root_observer_contract_ref": _text(relation.get("root_observer_id")),
        "root_table_ref": _text(root.get("database_table_id")),
        "root_field_binding_id": _text(root_field.get("field_binding_id")),
        "root_database_field_id": _text(root_field.get("database_field_id")),
        "root_database_field_name": _text(root_field.get("database_field_name")),
        "child_table_ref": _text(relation.get("child_table_id")),
        "child_database_field_id": _text(child_field.get("database_field_id")),
        "child_database_field_name": _text(child_field.get("database_field_name")),
        "aggregate": aggregate,
        "aggregate_alias": "related_value",
        "comparison_operator": _text(candidate.get("operator")),
        "aggregate_on_left": candidate.get("aggregate_on_left") is True,
        "comparison_phase": "AFTER",
        "tolerance": assertion.get("tolerance") or _dict(assertion.get("structured_expression")).get("tolerance"),
        "database_relation_binding": {
            "schema": PROJECTION_SCHEMA,
            "database_relationship_id": _text(relation.get("database_relationship_id")),
            "relation_mapping_decision_id": _text(relation.get("relation_mapping_decision_id")),
            "root_field_match_basis": _text(root_field.get("match_basis")),
            "child_field_match_basis": _text(child_field.get("match_basis")),
            "automatic_relation_mapping": False,
            "automatic_field_mapping": False,
            "fuzzy_name_matching": False,
            "client_side_filtering": False,
            "observer_oracle_authority": False,
        },
    }
    return projected, draft


def _blocked(experiment: dict[str, Any], assertion: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    row = deepcopy(experiment)
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_relation_oracle_detail": {
                "assertion_id": _text(assertion.get("assertion_id")),
                "candidate_count": len(matches),
                "candidate_relation_refs": sorted(
                    _text(_dict(match.get("relation_contract")).get("relation_observer_id"))
                    for match in matches
                ),
                "automatic_winner_allowed": False,
            },
        }
    )
    row["compile_receipt"] = receipt
    row["compile_status"] = "BLOCKED"
    return row


def project_database_relation_numeric_assertions(experiment_pack: dict[str, Any]) -> dict[str, Any]:
    pack = dict(experiment_pack or {})
    compiled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    projected_count = 0
    incomplete_count = 0

    for raw in _list(pack.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        assertions: list[dict[str, Any]] = []
        drafts = [
            dict(row)
            for row in _list(experiment.get("database_relation_observer_execution_drafts"))
            if isinstance(row, dict)
        ]
        gaps: list[dict[str, Any]] = []
        ambiguity: tuple[dict[str, Any], list[dict[str, Any]]] | None = None
        for raw_assertion in _list(experiment.get("assertions")):
            assertion = _dict(raw_assertion)
            source_kind = _text(assertion.get("kind") or assertion.get("type")).lower()
            if source_kind not in _SOURCE_KINDS or not _dict(assertion.get("structured_expression")):
                assertions.append(assertion)
                continue
            matches = _candidates(assertion, experiment)
            if len(matches) > 1:
                ambiguity = (assertion, matches)
                break
            if not matches:
                assertions.append(assertion)
                gaps.append(
                    {
                        "assertion_id": _text(assertion.get("assertion_id")),
                        "reason_code": "DATABASE_RELATION_EXACT_AGGREGATE_BINDING_MISSING",
                        "automatic_relation_mapping_allowed": False,
                        "fuzzy_matching_allowed": False,
                    }
                )
                incomplete_count += 1
                continue
            projected, draft = _projection(assertion, matches[0])
            assertions.append(projected)
            drafts.append(draft)
            projected_count += 1

        if ambiguity is not None:
            blocked.append(_blocked(experiment, ambiguity[0], ambiguity[1]))
            continue
        experiment["assertions"] = assertions
        unique_drafts = {
            _text(row.get("draft_id")): row for row in drafts if _text(row.get("draft_id"))
        }
        experiment["database_relation_observer_execution_drafts"] = list(unique_drafts.values())
        relation_assertions = [row for row in assertions if _text(row.get("kind")) == ASSERTION_KIND]
        if relation_assertions:
            observers = [
                dict(row) for row in _list(experiment.get("observers")) if isinstance(row, dict)
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
            remaining_legacy = any(
                _text(row.get("kind") or row.get("type")).lower() in _SOURCE_KINDS
                for row in assertions
            )
            if not remaining_legacy:
                observers = [
                    row
                    for row in observers
                    if _text(row.get("observer_id")) not in {"before_state", "after_state"}
                ]
            experiment["observers"] = observers
            fingerprint = _fingerprint(relation_assertions)
            receipt = _dict(experiment.get("compile_receipt"))
            receipt.update(
                {
                    "database_relation_numeric_projection_status": "PARTIAL" if gaps else "BOUND",
                    "database_relation_numeric_assertion_count": len(relation_assertions),
                    "database_relation_numeric_assertion_fingerprint": fingerprint,
                    "database_relation_automatic_mapping_used": False,
                    "database_relation_client_side_filter_used": False,
                }
            )
            experiment["compile_receipt"] = receipt
            experiment["database_relation_numeric_projection_status"] = "PARTIAL" if gaps else "BOUND"
            experiment["database_relation_numeric_assertion_fingerprint"] = fingerprint
        elif gaps:
            experiment["database_relation_numeric_projection_status"] = "INCOMPLETE"
        else:
            experiment["database_relation_numeric_projection_status"] = "NOT_APPLICABLE"
        if gaps:
            experiment["database_relation_numeric_projection_gaps"] = gaps
        compiled.append(experiment)

    existing_blocked = [
        dict(row) for row in _list(pack.get("blocked_experiments")) if isinstance(row, dict)
    ]
    all_blocked = [*existing_blocked, *blocked]
    reason_counts = dict(_dict(pack.get("block_reason_counts")))
    if blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(blocked)
    pack.update(
        {
            "experiments": compiled,
            "blocked_experiments": all_blocked,
            "compiled_count": len(compiled),
            "blocked_count": len(all_blocked),
            "block_reason_counts": reason_counts,
            "database_relation_numeric_experiment_projection": {
                "schema": PROJECTION_SCHEMA,
                "status": "BLOCKED" if blocked else "PARTIAL" if incomplete_count else "PASS",
                "projected_assertion_count": projected_count,
                "incomplete_assertion_count": incomplete_count,
                "newly_blocked_experiment_count": len(blocked),
                "automatic_relation_mapping_count": 0,
                "automatic_field_mapping_count": 0,
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
    "DRAFT_SCHEMA",
    "PROJECTION_SCHEMA",
    "project_database_relation_numeric_assertions",
]
