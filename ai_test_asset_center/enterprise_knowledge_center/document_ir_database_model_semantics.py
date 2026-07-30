"""Project native database-model Document IR into canonical database facts."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Iterable

DATABASE_MODEL_SEMANTIC_SCHEMA = "qualibug.database-model-semantic-projection.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _tokens(*values: Any) -> list[str]:
    rendered = " ".join(_text(value) for value in values)
    return sorted({token.casefold() for token in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,80}", rendered)})[:200]


def _artifact(document_ir: dict[str, Any]) -> dict[str, Any]:
    return _dict(document_ir.get("artifact_structure"))


def _artifact_kind(document_ir: dict[str, Any]) -> str:
    artifact = _artifact(document_ir)
    receipt = _dict(document_ir.get("structure_receipt"))
    return _text(artifact.get("artifact_kind") or receipt.get("artifact_kind")).lower()


def _qualified(schema_name: Any, table_name: Any) -> str:
    schema = _text(schema_name)
    table = _text(table_name)
    return f"{schema}.{table}" if schema and table else table or schema


def _table_id(schema_name: Any, table_name: Any) -> str:
    return f"table:{_qualified(schema_name, table_name)}"


def _evidence(*, source_id: str, source_locator: Any, block_id: Any = "") -> dict[str, Any]:
    locator = _text(source_locator)
    return {
        "source_id": source_id,
        "source_locator": locator,
        "document_ir_block_id": _text(block_id),
        "address_kind": "EXACT_SOURCE_LOCATOR",
        "exact": bool(locator),
    }


def _relationship_targets(relationships: Iterable[Any]) -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], list[str]] = {}
    for raw in relationships:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        key = (_text(row.get("child_schema")), _text(row.get("child_table")))
        target = _qualified(row.get("parent_schema"), row.get("parent_table"))
        if target and target not in result.setdefault(key, []):
            result[key].append(target)
    return result


def _index_rows_by_table(indexes: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in indexes:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        table = _text(row.get("table"))
        if table:
            result.setdefault(table, []).append(row)
    return result


def project_database_model_semantics(
    document_ir: dict[str, Any],
    *,
    source_id: str,
    source_type: str = "",
) -> dict[str, Any]:
    """Translate one database-model Document IR into exact source-declared facts."""
    artifact = _artifact(document_ir)
    if _artifact_kind(document_ir) != "database_model":
        return {
            "schema": DATABASE_MODEL_SEMANTIC_SCHEMA,
            "status": "NOT_APPLICABLE",
            "source_id": source_id,
            "source_type": source_type,
            "tables": [],
            "field_dictionary": [],
            "relationships": [],
            "indexes": [],
        }

    receipt = _dict(document_ir.get("structure_receipt"))
    formal_status = _text(receipt.get("status")) or _text(artifact.get("status")) or "UNKNOWN"
    model_kind = _text(artifact.get("database_model_kind"))
    database_family = _text(artifact.get("database_family"))
    model_name = _text(artifact.get("model_name"))

    raw_relationships = [dict(row) for row in _list(artifact.get("relationships")) if isinstance(row, dict)]
    raw_indexes = [dict(row) for row in _list(artifact.get("indexes")) if isinstance(row, dict)]
    targets = _relationship_targets(raw_relationships)
    indexes_by_table = _index_rows_by_table(raw_indexes)

    fields: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    exact_fact_count = 0
    total_fact_count = 0

    for raw_table in _list(artifact.get("tables")):
        if not isinstance(raw_table, dict):
            continue
        table = deepcopy(raw_table)
        schema_name = _text(table.get("schema"))
        table_name = _text(table.get("name") or table.get("code"))
        if not table_name:
            continue
        qualified_name = _qualified(schema_name, table_name)
        table_identity = _table_id(schema_name, table_name)
        table_locator = _text(table.get("source_locator"))
        table_evidence = _evidence(source_id=source_id, source_locator=table_locator, block_id=table.get("block_id"))
        total_fact_count += 1
        exact_fact_count += 1 if table_evidence["exact"] else 0

        table_fields: list[dict[str, Any]] = []
        identity_fields: list[str] = []
        for raw_column in _list(table.get("columns")):
            if not isinstance(raw_column, dict):
                continue
            column = deepcopy(raw_column)
            column_name = _text(column.get("name") or column.get("code"))
            if not column_name:
                continue
            column_locator = _text(column.get("source_locator"))
            column_evidence = _evidence(source_id=source_id, source_locator=column_locator, block_id=column.get("block_id"))
            total_fact_count += 1
            exact_fact_count += 1 if column_evidence["exact"] else 0
            primary_key = bool(column.get("primary_key"))
            unique = bool(column.get("unique"))
            nullable = bool(column.get("nullable", True))
            if primary_key or unique:
                identity_fields.append(column_name)
            constraints = {
                "primary_key": primary_key,
                "unique": unique,
                "nullable": nullable,
                "auto_increment": bool(column.get("auto_increment")),
                "default": _text(column.get("default")),
                "length": _text(column.get("length")),
                "precision": _text(column.get("precision")),
                "scale": _text(column.get("scale")),
                "hidden": column.get("hidden"),
            }
            constraints = {key: value for key, value in constraints.items() if value not in ("", None, False) or key == "nullable"}
            field = {
                "field_id": _stable_id("database_field", source_id, qualified_name, column_name),
                "source_id": source_id,
                "source_type": source_type or "database_schema",
                "table": qualified_name,
                "table_name": table_name,
                "schema_name": schema_name,
                "table_id": table_identity,
                "field": column_name,
                "field_path": column_name,
                "type": _text(column.get("data_type")),
                "required": not nullable,
                "nullable": nullable,
                "identity": primary_key or unique,
                "primary_key": primary_key,
                "unique": unique,
                "auto_increment": bool(column.get("auto_increment")),
                "default": _text(column.get("default")),
                "description": _text(column.get("comment") or column.get("description"))[:1000],
                "ordinal_position": column.get("ordinal_position"),
                "constraints": constraints,
                "source_locator": column_locator,
                "evidence_address": column_evidence,
                "evidence_kind": "SOURCE_DECLARED_DATABASE_MODEL",
                "evidence_derivation": "database_model_document_ir",
                "derivation": "database_model_document_ir",
                "business_semantics_inferred": False,
                "tokens": _tokens(schema_name, table_name, column_name, column.get("data_type"), column.get("comment")),
            }
            fields.append(field)
            table_fields.append(field)

        table_indexes = [deepcopy(row) for row in indexes_by_table.get(table_name, []) if isinstance(row, dict)]
        declaration = {
            "schema": "qualibug.database-table-source-declaration.v1",
            "declaration_id": _stable_id("database_table_declaration", source_id, qualified_name),
            "source_id": source_id,
            "source_type": source_type or "database_schema",
            "model_kind": model_kind,
            "database_family": database_family,
            "model_name": model_name,
            "schema_name": schema_name,
            "table_name": table_name,
            "qualified_name": qualified_name,
            "table_kind": _text(table.get("kind") or "table"),
            "columns": [row["field"] for row in table_fields],
            "identity_fields": sorted(set(identity_fields)),
            "foreign_keys": sorted(set(targets.get((schema_name, table_name), []))),
            "indexes": [
                {
                    "name": _text(row.get("name")),
                    "columns": [_text(value) for value in _list(row.get("columns")) if _text(value)],
                    "unique": bool(row.get("unique")),
                    "partial": bool(row.get("partial")),
                }
                for row in table_indexes
            ],
            "source_locator": table_locator,
            "evidence_address": table_evidence,
            "business_semantics_inferred": False,
        }
        tables.append(
            {
                "table_id": table_identity,
                "source_id": source_id,
                "source_type": source_type or "database_schema",
                "name": table_name,
                "schema_name": schema_name,
                "qualified_name": qualified_name,
                "table_kind": _text(table.get("kind") or "table"),
                "description": _text(table.get("comment"))[:1000],
                "columns": [row["field"] for row in table_fields],
                "identity_fields": sorted(set(identity_fields)),
                "foreign_keys": sorted(set(targets.get((schema_name, table_name), []))),
                "field_dictionary": table_fields,
                "database_model_declarations": [declaration],
                "source_locator": table_locator,
                "evidence_address": table_evidence,
                "derivation": "database_model_document_ir",
                "business_semantics_inferred": False,
                "tokens": _tokens(schema_name, table_name, table.get("comment"), " ".join(row["field"] for row in table_fields)),
            }
        )

    relationships: list[dict[str, Any]] = []
    for raw in raw_relationships:
        child_schema = _text(raw.get("child_schema"))
        child_table = _text(raw.get("child_table"))
        parent_schema = _text(raw.get("parent_schema"))
        parent_table = _text(raw.get("parent_table"))
        locator = _text(raw.get("source_locator"))
        evidence = _evidence(source_id=source_id, source_locator=locator, block_id=raw.get("block_id"))
        total_fact_count += 1
        exact_fact_count += 1 if evidence["exact"] else 0
        relationships.append(
            {
                "relationship_id": _stable_id(
                    "database_relationship",
                    source_id,
                    child_schema,
                    child_table,
                    ",".join(_text(value) for value in _list(raw.get("child_columns"))),
                    parent_schema,
                    parent_table,
                    ",".join(_text(value) for value in _list(raw.get("parent_columns"))),
                ),
                "source_id": source_id,
                "source_type": source_type or "database_schema",
                "name": _text(raw.get("name")),
                "child_schema": child_schema,
                "child_table": child_table,
                "child_table_id": _table_id(child_schema, child_table),
                "child_columns": [_text(value) for value in _list(raw.get("child_columns")) if _text(value)],
                "parent_schema": parent_schema,
                "parent_table": parent_table,
                "parent_table_id": _table_id(parent_schema, parent_table),
                "parent_columns": [_text(value) for value in _list(raw.get("parent_columns")) if _text(value)],
                "delete_rule": _text(raw.get("delete_rule")),
                "update_rule": _text(raw.get("update_rule")),
                "match": _text(raw.get("match")),
                "source_locator": locator,
                "evidence_address": evidence,
                "contract_authority": "DATABASE_MODEL_SOURCE_DECLARATION",
                "business_semantics_inferred": False,
            }
        )

    indexes: list[dict[str, Any]] = []
    for raw in raw_indexes:
        table_name = _text(raw.get("table"))
        schema_name = _text(raw.get("schema"))
        locator = _text(raw.get("source_locator"))
        evidence = _evidence(source_id=source_id, source_locator=locator, block_id=raw.get("block_id"))
        total_fact_count += 1
        exact_fact_count += 1 if evidence["exact"] else 0
        indexes.append(
            {
                "index_id": _stable_id(
                    "database_index",
                    source_id,
                    schema_name,
                    table_name,
                    raw.get("name"),
                    ",".join(_text(value) for value in _list(raw.get("columns"))),
                ),
                "source_id": source_id,
                "source_type": source_type or "database_schema",
                "name": _text(raw.get("name")),
                "schema_name": schema_name,
                "table_name": table_name,
                "table_id": _table_id(schema_name, table_name),
                "columns": [_text(value) for value in _list(raw.get("columns")) if _text(value)],
                "unique": bool(raw.get("unique")),
                "primary": bool(raw.get("primary") or raw.get("is_primary")),
                "partial": bool(raw.get("partial")),
                "origin": _text(raw.get("origin")),
                "source_locator": locator,
                "evidence_address": evidence,
                "contract_authority": "DATABASE_MODEL_SOURCE_DECLARATION",
                "business_semantics_inferred": False,
            }
        )

    return {
        "schema": DATABASE_MODEL_SEMANTIC_SCHEMA,
        "status": formal_status,
        "source_id": source_id,
        "source_type": source_type,
        "database_model": {
            "model_kind": model_kind,
            "model_name": model_name,
            "database_family": database_family,
            "source_id": source_id,
            "source_type": source_type or "database_schema",
            "formal_status": formal_status,
            "schema_names": [
                _text(row.get("name"))
                for row in _list(artifact.get("schemas"))
                if isinstance(row, dict) and _text(row.get("name"))
            ],
            "business_semantics_inferred": False,
        },
        "tables": tables,
        "field_dictionary": fields,
        "relationships": relationships,
        "indexes": indexes,
        "table_count": len(tables),
        "field_count": len(fields),
        "relationship_count": len(relationships),
        "index_count": len(indexes),
        "identity_field_count": sum(len(row.get("identity_fields") or []) for row in tables),
        "exact_fact_count": exact_fact_count,
        "total_fact_count": total_fact_count,
        "exact_fact_rate": round(exact_fact_count / total_fact_count, 4) if total_fact_count else 1.0,
        "source_declared_structure_only": True,
        "database_rows_read": 0,
        "container_reparse_performed": False,
        "business_semantics_inferred": False,
    }


def enrich_parsed_database_model_semantics(
    parsed: dict[str, Any],
    document_ir: dict[str, Any],
    *,
    source_id: str,
    source_type: str = "",
) -> dict[str, Any]:
    """Replace lossy generic table guesses with exact database-model declarations."""
    result = dict(parsed or {})
    projection = project_database_model_semantics(document_ir, source_id=source_id, source_type=source_type)
    if projection.get("status") == "NOT_APPLICABLE":
        return result

    result["database_model"] = deepcopy(_dict(projection.get("database_model")))
    result["database_model_relationships"] = deepcopy(_list(projection.get("relationships")))
    result["database_model_indexes"] = deepcopy(_list(projection.get("indexes")))
    result["database_model_semantic_receipt"] = {
        key: deepcopy(value)
        for key, value in projection.items()
        if key not in {"tables", "field_dictionary", "relationships", "indexes", "database_model"}
    }
    if _text(projection.get("status")) != "BLOCKED":
        result["tables"] = deepcopy(_list(projection.get("tables")))
        result["field_dictionary"] = deepcopy(_list(projection.get("field_dictionary")))
    return result


__all__ = [
    "DATABASE_MODEL_SEMANTIC_SCHEMA",
    "project_database_model_semantics",
    "enrich_parsed_database_model_semantics",
]
