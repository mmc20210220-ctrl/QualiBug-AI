"""Project exact FK-scoped child collection Observer candidates.

A relation candidate is derived only from a source-declared database foreign key and a
currently runtime-bindable root database Observer contract.  It is never authoritative by
itself: the existing database mapping authority ledger must explicitly approve the relation
for read-only observation.  No table-name, field-name, API-path or business-semantic relation
is inferred here.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Iterable

DATABASE_RELATION_CANDIDATE_SCHEMA = "qualibug.database-relation-observer-candidate.v1"
DATABASE_RELATION_PROJECTION_SCHEMA = "qualibug.database-relation-observer-candidate-projection.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _dedupe(rows: Iterable[Any], identity_field: str) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        identity = _text(raw.get(identity_field))
        if identity:
            output[identity] = deepcopy(raw)
    return list(output.values())


def _lookup(rows: Iterable[Any], identity_field: str) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get(identity_field)): deepcopy(row)
        for row in rows
        if isinstance(row, dict) and _text(row.get(identity_field))
    }


def _ready_root_contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        deepcopy(row)
        for row in _list(asset.get("database_observer_contracts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) == "qualibug.database-observer-contract.v1"
        and _text(row.get("status")) == "READY_FOR_RUNTIME_CONNECTION_BINDING"
        and row.get("runtime_observer_authoritative") is True
        and row.get("read_only") is True
        and row.get("mutation_allowed") is False
        and row.get("write_target_allowed") is False
        and row.get("oracle_authority_allowed") is False
    ]


def _parent_value_sources(
    root_contract: dict[str, Any], parent_columns: list[str]
) -> tuple[list[dict[str, Any]], str]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(root_contract.get("field_bindings")):
        binding = _dict(raw)
        if (
            binding.get("authoritative") is not True
            or binding.get("read_only") is not True
            or binding.get("oracle_authority_allowed") is True
        ):
            continue
        name = _text(binding.get("database_field_name"))
        if name and _text(binding.get("value_source")):
            by_name.setdefault(name, []).append(binding)

    predicates: list[dict[str, Any]] = []
    for column in parent_columns:
        choices = by_name.get(column, [])
        if len(choices) != 1:
            return [], "DATABASE_RELATION_PARENT_VALUE_SOURCE_NOT_UNIQUE"
        chosen = choices[0]
        predicates.append(
            {
                "parent_database_field_name": column,
                "parent_database_field_id": _text(chosen.get("database_field_id")),
                "parent_field_binding_id": _text(chosen.get("field_binding_id")),
                "value_source": _text(chosen.get("value_source")),
            }
        )
    return predicates, ""


def enrich_asset_with_database_relation_observer_candidates(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild non-authoritative child collection candidates from exact FK facts."""
    result = dict(asset or {})
    root_contracts = _ready_root_contracts(result)
    relationships = [
        deepcopy(row)
        for row in _list(result.get("database_model_relationships"))
        if isinstance(row, dict)
        and _text(row.get("contract_authority")) == "DATABASE_MODEL_SOURCE_DECLARATION"
        and _text(row.get("relationship_id"))
    ]
    tables = _lookup(
        [*_list(result.get("data_tables")), *_list(result.get("tables"))],
        "table_id",
    )
    fields_by_table: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(result.get("field_dictionary")):
        field = _dict(raw)
        table_id = _text(field.get("table_id"))
        if table_id and _text(field.get("field_id")) and _text(field.get("field")):
            fields_by_table.setdefault(table_id, []).append(field)

    candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for root in root_contracts:
        parent_table_id = _text(root.get("database_table_id"))
        for relationship in relationships:
            if _text(relationship.get("parent_table_id")) != parent_table_id:
                continue
            child_table_id = _text(relationship.get("child_table_id"))
            child_columns = [_text(value) for value in _list(relationship.get("child_columns")) if _text(value)]
            parent_columns = [_text(value) for value in _list(relationship.get("parent_columns")) if _text(value)]
            child_table = tables.get(child_table_id, {})
            child_fields = sorted(
                [
                    {
                        "database_field_id": _text(field.get("field_id")),
                        "database_field_name": _text(field.get("field")),
                        "database_declared_type": _text(field.get("type")),
                        "nullable": field.get("nullable"),
                        "source_id": _text(field.get("source_id")),
                        "source_locator": _text(field.get("source_locator")),
                    }
                    for field in fields_by_table.get(child_table_id, [])
                ],
                key=lambda row: (row["database_field_name"], row["database_field_id"]),
            )
            parent_sources, parent_source_error = _parent_value_sources(root, parent_columns)
            if not child_columns or len(child_columns) != len(parent_columns):
                status = "BLOCKED_FOREIGN_KEY_COLUMN_PAIR_INCOMPLETE"
                reason = "DATABASE_RELATION_FOREIGN_KEY_COLUMN_PAIR_REQUIRED"
            elif not child_table:
                status = "BLOCKED_CHILD_TABLE_DECLARATION_MISSING"
                reason = "DATABASE_RELATION_CHILD_TABLE_REQUIRED"
            elif not child_fields:
                status = "BLOCKED_CHILD_FIELD_CATALOG_MISSING"
                reason = "DATABASE_RELATION_CHILD_FIELDS_REQUIRED"
            elif parent_source_error:
                status = "BLOCKED_PARENT_VALUE_SOURCE_MISSING"
                reason = parent_source_error
            else:
                status = "PENDING_RELATION_AUTHORITY"
                reason = ""

            predicate_pairs = [
                {
                    "ordinal": index,
                    "child_database_field_name": child_column,
                    "parent_database_field_name": parent_column,
                    **(parent_sources[index] if index < len(parent_sources) else {}),
                }
                for index, (child_column, parent_column) in enumerate(
                    zip(child_columns, parent_columns)
                )
            ]
            candidate_id = _stable_id(
                "database_relation_observer_candidate",
                root.get("observer_id"),
                relationship.get("relationship_id"),
                *child_columns,
                *parent_columns,
            )
            candidate = {
                "schema": DATABASE_RELATION_CANDIDATE_SCHEMA,
                "candidate_id": candidate_id,
                "candidate_kind": "relation",
                "root_observer_id": _text(root.get("observer_id")),
                "operation_schema_binding_id": _text(root.get("operation_schema_binding_id")),
                "interface_id": _text(root.get("interface_id")),
                "method": _text(root.get("method")),
                "path": _text(root.get("path")),
                "database_relationship_id": _text(relationship.get("relationship_id")),
                "parent_table_id": parent_table_id,
                "parent_schema_name": _text(relationship.get("parent_schema")),
                "parent_table_name": _text(relationship.get("parent_table")),
                "parent_columns": parent_columns,
                "child_table_id": child_table_id,
                "child_schema_name": _text(relationship.get("child_schema")),
                "child_table_name": _text(relationship.get("child_table")),
                "child_columns": child_columns,
                "predicate_pairs": predicate_pairs,
                "available_child_fields": child_fields,
                "foreign_key_delete_rule": _text(relationship.get("delete_rule")),
                "foreign_key_update_rule": _text(relationship.get("update_rule")),
                "source_id": _text(relationship.get("source_id")),
                "source_locator": _text(relationship.get("source_locator")),
                "relationship_evidence": deepcopy(_dict(relationship.get("evidence_address"))),
                "root_mapping_decision_refs": sorted(
                    {
                        _text(root.get("table_mapping_decision_id")),
                        *[
                            _text(row.get("mapping_decision_id"))
                            for row in _list(root.get("field_bindings"))
                            if isinstance(row, dict) and _text(row.get("mapping_decision_id"))
                        ],
                    }
                    - {""}
                ),
                "status": status,
                "reason_code": reason,
                "observer_candidate_only": True,
                "observer_authority_allowed": False,
                "relation_mapping_confirmed": False,
                "automatic_relation_mapping_allowed": False,
                "client_side_filter_allowed": False,
                "read_only": True,
                "write_target_allowed": False,
                "oracle_authority_allowed": False,
                "database_rows_read": 0,
                "business_semantics_inferred": False,
            }
            candidates.append(candidate)
            if status.startswith("BLOCKED_"):
                gaps.append(
                    {
                        "kind": "DATABASE_RELATION_OBSERVER_CANDIDATE_BLOCKED",
                        "gap_type": "database_relation_observer_candidate_incomplete",
                        "candidate_id": candidate_id,
                        "root_observer_id": candidate["root_observer_id"],
                        "database_relationship_id": candidate["database_relationship_id"],
                        "status": status,
                        "reason_code": reason,
                        "blocks_database_relation_authority": True,
                    }
                )

    candidates = _dedupe(candidates, "candidate_id")
    retained_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "DATABASE_RELATION_OBSERVER_CANDIDATE_BLOCKED"
    ]
    result["database_relation_observer_candidates"] = candidates
    result["coverage_gaps"] = [*retained_gaps, *gaps]
    pending = sum(1 for row in candidates if _text(row.get("status")) == "PENDING_RELATION_AUTHORITY")
    result["database_relation_observer_candidate_projection"] = {
        "schema": DATABASE_RELATION_PROJECTION_SCHEMA,
        "status": "NOT_APPLICABLE" if not candidates else "PARTIAL" if gaps else "COMPLETE",
        "root_observer_contract_count": len(root_contracts),
        "source_declared_relationship_count": len(relationships),
        "candidate_count": len(candidates),
        "pending_authority_count": pending,
        "blocked_candidate_count": len(gaps),
        "automatic_relation_mapping_count": 0,
        "client_side_filter_count": 0,
        "database_rows_read": 0,
        "write_target_authority_count": 0,
        "oracle_authority_count": 0,
    }
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_relation_candidates_require_source_declared_foreign_key": True,
            "database_relation_candidates_require_runtime_bindable_root_observer": True,
            "database_relation_parent_values_require_approved_root_field_sources": True,
            "database_relation_candidates_require_operator_authority": True,
            "database_relation_candidates_never_use_client_side_filtering": True,
            "database_relation_candidates_do_not_infer_business_semantics": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_RELATION_CANDIDATE_SCHEMA",
    "DATABASE_RELATION_PROJECTION_SCHEMA",
    "enrich_asset_with_database_relation_observer_candidates",
]
