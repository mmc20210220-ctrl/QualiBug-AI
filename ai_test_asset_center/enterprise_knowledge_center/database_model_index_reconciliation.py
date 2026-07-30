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


def reconcile_database_model_index_assets(asset: dict[str, Any]) -> dict[str, Any]:
    """Attach schema-less indexes only when one table name resolves unambiguously."""
    result = dict(asset or {})
    tables = [
        deepcopy(row)
        for row in _list(result.get("tables"))
        if isinstance(row, dict)
    ]
    indexes = [
        deepcopy(row)
        for row in _list(result.get("database_model_indexes"))
        if isinstance(row, dict)
    ]

    candidates: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        table_name = _text(table.get("name") or table.get("table_name"))
        table_id = _text(table.get("table_id"))
        if table_name and table_id:
            candidates.setdefault(table_name, []).append(table)

    resolved_count = 0
    ambiguous_count = 0
    unresolved_count = 0
    for index in indexes:
        table_name = _text(index.get("table_name"))
        schema_name = _text(index.get("schema_name"))
        current_id = _text(index.get("table_id"))
        if schema_name and current_id:
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

    result["database_model_indexes"] = indexes
    result["database_model_index_reconciliation"] = {
        "schema": DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA,
        "index_count": len(indexes),
        "resolved_schema_less_index_count": resolved_count,
        "ambiguous_index_count": ambiguous_count,
        "unresolved_index_count": unresolved_count,
        "automatic_ambiguous_winner_selected": False,
        "table_name_vocabulary_inferred": False,
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
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_MODEL_INDEX_RECONCILIATION_SCHEMA",
    "reconcile_database_model_index_assets",
]
