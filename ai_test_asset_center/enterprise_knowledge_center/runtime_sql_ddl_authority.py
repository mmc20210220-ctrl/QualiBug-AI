"""Exact runtime SQL DDL technical-fact authority."""
from __future__ import annotations
from typing import Any

from .runtime_sql_ddl_support import AUTHORITY, _stable, _unquote, _qualified, _line_locator, _identifier_list, _evidence, re, quote
from .runtime_sql_ddl_extract import extract_raw_sql_ddl


def parse_runtime_sql_ddl(
    ddl_text: str,
    *,
    source_id: str,
    filename: str = "runtime_schema.sql",
) -> dict[str, Any]:
    """Return exact table/FK/index facts from source DDL, reading zero database rows."""
    text, raw_tables, raw_relationships = extract_raw_sql_ddl(
        ddl_text, source_id=source_id, filename=filename
    )

    tables: list[dict[str, Any]] = []
    for raw in raw_tables:
        schema_name, table_name = raw["schema"], raw["name"]
        qualified = f"{schema_name}.{table_name}" if schema_name else table_name
        table_id = f"table:{qualified}"
        locator = raw["source_locator"]
        declaration_id = _stable("database_table_declaration", source_id, qualified)
        declaration = {
            "schema": "qualibug.database-table-source-declaration.v1",
            "declaration_id": declaration_id,
            "source_id": source_id,
            "source_type": "database_schema",
            "model_kind": "sql_ddl",
            "schema_name": schema_name,
            "table_name": table_name,
            "qualified_name": qualified,
            "columns": [column["name"] for column in raw["columns"]],
            "identity_fields": raw["identity_fields"],
            "source_locator": locator,
            "evidence_address": _evidence(source_id, locator),
            "business_semantics_inferred": False,
        }
        tables.append({
            "table_id": table_id,
            "source_id": source_id,
            "source_type": "database_schema",
            "name": table_name,
            "schema_name": schema_name,
            "qualified_name": qualified,
            "columns": declaration["columns"],
            "identity_fields": raw["identity_fields"],
            "database_model_declarations": [declaration],
            "source_locator": locator,
            "evidence_address": _evidence(source_id, locator),
            "derivation": "database_model_document_ir",
            "business_semantics_inferred": False,
        })

    relationships: list[dict[str, Any]] = []
    for child_schema, child_table, child_columns, parent_schema, parent_table, parent_columns, locator in raw_relationships:
        child_qualified = f"{child_schema}.{child_table}" if child_schema else child_table
        parent_qualified = f"{parent_schema}.{parent_table}" if parent_schema else parent_table
        relationships.append({
            "relationship_id": _stable("database_relationship", source_id, child_qualified, ','.join(child_columns), parent_qualified, ','.join(parent_columns)),
            "source_id": source_id,
            "source_type": "database_schema",
            "child_schema": child_schema,
            "child_table": child_table,
            "child_table_id": f"table:{child_qualified}",
            "child_columns": child_columns,
            "parent_schema": parent_schema,
            "parent_table": parent_table,
            "parent_table_id": f"table:{parent_qualified}",
            "parent_columns": parent_columns,
            "source_locator": locator,
            "evidence_address": _evidence(source_id, locator),
            "contract_authority": AUTHORITY,
            "business_semantics_inferred": False,
        })

    indexes: list[dict[str, Any]] = []
    for match in re.finditer(
        r'''(?is)\bCREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>\S+)\s+ON\s+(?P<table>\S+)\s*(?:USING\s+\w+\s*)?\((?P<columns>[^)]*)\)''',
        text,
    ):
        schema_name, table_name = _qualified(match.group("table"))
        qualified = f"{schema_name}.{table_name}" if schema_name else table_name
        columns = _identifier_list(match.group("columns"))
        locator = _line_locator(text, filename, match.start(), f"index={quote(_unquote(match.group('name')), safe='')}")
        indexes.append({
            "index_id": _stable("database_index", source_id, qualified, _unquote(match.group("name")), ','.join(columns)),
            "source_id": source_id,
            "source_type": "database_schema",
            "name": _unquote(match.group("name")),
            "table_name": table_name,
            "table_id": f"table:{qualified}",
            "columns": columns,
            "unique": bool(match.group("unique")),
            "source_locator": locator,
            "evidence_address": _evidence(source_id, locator),
            "contract_authority": AUTHORITY,
            "business_semantics_inferred": False,
        })

    return {
        "tables": tables,
        "database_model_relationships": relationships,
        "database_model_indexes": indexes,
        "database_model": {
            "source_id": source_id,
            "model_kind": "sql_ddl",
            "table_count": len(tables),
            "relationship_count": len(relationships),
            "index_count": len(indexes),
            "database_rows_read": 0,
            "business_semantics_inferred": False,
        },
    }
