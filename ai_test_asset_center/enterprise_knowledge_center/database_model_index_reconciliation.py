"""Resolve database index declarations onto exact canonical table identities."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA = (
    "qualibug.database-model-index-reconciliation.v1"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _identity_key(index: dict[str, Any]) -> dict[str, Any]:
    columns = [_text(value) for value in _list(index.get("columns")) if _text(value)]
    return {
        "identity_key_id": f"identity_key:{_text(index.get('index_id'))}",
        "kind": "PRIMARY_INDEX" if bool(index.get("primary")) else "UNIQUE_INDEX",
        "columns": columns,
        "index_id": _text(index.get("index_id")),
        "index_name": _text(index.get("name")),
        "source_id": _text(index.get("source_id")),
        "source_locator": _text(index.get("source_locator")),
        "composite": len(columns) > 1,
        "source_declared": True,
    }


def _merge_identity_key(table: dict[str, Any], key: dict[str, Any]) -> None:
    rows = [
        deepcopy(row)
        for row in _list(table.get("identity_keys"))
        if isinstance(row, dict)
    ]
    identity = _text(key.get("identity_key_id"))
    if identity and not any(_text(row.get("identity_key_id")) == identity for row in rows):
        rows.append(deepcopy(key))
    table["identity_keys"] = rows
    table["identity_fields"] = sorted(
        {
            *[_text(value) for value in _list(table.get("identity_fields")) if _text(value)],
            *[_text(value) for value in _list(key.get("columns")) if _text(value)],
        }
    )


def _promote_field_identity(
    field: dict[str, Any],
    *,
    identity_key: dict[str, Any],
) -> None:
    columns = [_text(value) for value in _list(identity_key.get("columns")) if _text(value)]
    field_name = _text(field.get("field") or field.get("field_name"))
    if field_name not in columns:
        return
    field["identity_component"] = True
    field["identity_key_ids"] = sorted(
        {
            *[
                _text(value)
                for value in _list(field.get("identity_key_ids"))
                if _text(value)
            ],
            _text(identity_key.get("identity_key_id")),
        }
        - {""}
    )
    if len(columns) == 1:
        field["identity"] = True
        field["unique"] = True
        constraints = _dict(field.get("constraints"))
        constraints["unique"] = True
        field["constraints"] = constraints
    else:
        field["composite_identity_member"] = True
        # A member of UNIQUE(a,b) is not individually unique. Keep field.identity
        # unchanged and make the tuple authority explicit through identity_key_ids.


def reconcile_database_model_index_assets(asset: dict[str, Any]) -> dict[str, Any]:
    """Attach schema-less indexes only when one table name resolves unambiguously."""
    result = dict(asset or {})
    tables = [
        deepcopy(row)
        for row in _list(result.get("tables"))
        if isinstance(row, dict)
    ]
    fields = [
        deepcopy(row)
        for row in _list(result.get("field_dictionary"))
        if isinstance(row, dict)
    ]
    indexes = [
        deepcopy(row)
        for row in _list(result.get("database_model_indexes"))
        if isinstance(row, dict)
    ]

    candidates: dict[str, list[dict[str, Any]]] = {}
    tables_by_id: dict[str, dict[str, Any]] = {}
    for table in tables:
        table_name = _text(table.get("name") or table.get("table_name"))
        table_id = _text(table.get("table_id"))
        if table_name and table_id:
            candidates.setdefault(table_name, []).append(table)
            tables_by_id[table_id] = table

    resolved_count = 0
    ambiguous_count = 0
    unresolved_count = 0
    for index in indexes:
        table_name = _text(index.get("table_name"))
        schema_name = _text(index.get("schema_name"))
        current_id = _text(index.get("table_id"))
        if schema_name and current_id and current_id in tables_by_id:
            index["table_resolution_status"] = "SOURCE_DECLARED_SCHEMA_QUALIFIED"
            continue
        matches = candidates.get(table_name, [])
        if len(matches) == 1:
            table = matches[0]
            index["schema_name"] = _text(table.get("schema_name"))
            index["table_id"] = _text(table.get("table_id"))
            index["qualified_table_name"] = _text(
                table.get("qualified_name") or table.get("name")
            )
            index["table_resolution_status"] = "RESOLVED_UNIQUE_TABLE_NAME"
            index["table_resolution_source"] = "canonical_database_model_table_asset"
            resolved_count += 1
        elif len(matches) > 1:
            index["table_resolution_status"] = "AMBIGUOUS_TABLE_NAME"
            index["table_resolution_candidates"] = sorted(
                {
                    _text(table.get("table_id"))
                    for table in matches
                    if _text(table.get("table_id"))
                }
            )
            index["operator_authority_required"] = True
            ambiguous_count += 1
        else:
            index["table_resolution_status"] = "UNRESOLVED_TABLE_NAME"
            index["operator_authority_required"] = True
            unresolved_count += 1

    unique_identity_key_count = 0
    single_column_identity_key_count = 0
    composite_identity_key_count = 0
    for index in indexes:
        if not (bool(index.get("unique")) or bool(index.get("primary"))):
            continue
        if _text(index.get("table_resolution_status")) in {
            "AMBIGUOUS_TABLE_NAME",
            "UNRESOLVED_TABLE_NAME",
        }:
            continue
        table_id = _text(index.get("table_id"))
        table = tables_by_id.get(table_id)
        columns = [_text(value) for value in _list(index.get("columns")) if _text(value)]
        if table is None or not columns:
            continue
        key = _identity_key(index)
        _merge_identity_key(table, key)
        unique_identity_key_count += 1
        if len(columns) == 1:
            single_column_identity_key_count += 1
        else:
            composite_identity_key_count += 1
        for field in fields:
            if _text(field.get("table_id")) == table_id:
                _promote_field_identity(field, identity_key=key)
        nested_fields = [
            deepcopy(row)
            for row in _list(table.get("field_dictionary"))
            if isinstance(row, dict)
        ]
        for field in nested_fields:
            _promote_field_identity(field, identity_key=key)
        if nested_fields:
            table["field_dictionary"] = nested_fields

    result["tables"] = tables
    result["field_dictionary"] = fields
    result["database_model_indexes"] = indexes
    result["database_model_index_reconciliation"] = {
        "schema": DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA,
        "index_count": len(indexes),
        "resolved_schema_less_index_count": resolved_count,
        "ambiguous_index_count": ambiguous_count,
        "unresolved_index_count": unresolved_count,
        "unique_identity_key_count": unique_identity_key_count,
        "single_column_identity_key_count": single_column_identity_key_count,
        "composite_identity_key_count": composite_identity_key_count,
        "automatic_ambiguous_winner_selected": False,
        "table_name_vocabulary_inferred": False,
        "composite_unique_members_are_not_individually_unique": True,
    }

    projection = _dict(result.get("database_model_fact_projection"))
    if projection:
        projection["index_reconciliation"] = deepcopy(
            result["database_model_index_reconciliation"]
        )
        if ambiguous_count or unresolved_count:
            projection["status"] = "PARTIAL"
        result["database_model_fact_projection"] = projection

    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_model_schema_less_indexes_require_unique_table_identity": True,
            "database_model_ambiguous_index_owner_requires_authority": True,
            "database_model_unique_indexes_create_table_identity_keys": True,
            "database_model_composite_unique_members_not_individually_unique": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA",
    "reconcile_database_model_index_assets",
]
