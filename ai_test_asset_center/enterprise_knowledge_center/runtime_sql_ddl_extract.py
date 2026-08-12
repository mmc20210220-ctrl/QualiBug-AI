"""Extract source-declared runtime SQL DDL tables and foreign keys."""
from __future__ import annotations
from typing import Any

from .runtime_sql_ddl_support import (
    _unquote, _qualified, _line_locator, _matching_paren, _identifier_list,
    _reference, _sql_column_type, _sql_table_body_declarations, re, quote,
)

def extract_raw_sql_ddl(
    ddl_text: str,
    *,
    source_id: str,
    filename: str = "runtime_schema.sql",
) -> dict[str, Any]:
    """Return exact table/FK/index facts from source DDL, reading zero database rows."""
    text = str(ddl_text or "")
    table_pattern = re.compile(
        r'''(?is)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*))?)\s*\('''
    )
    raw_tables: list[dict[str, Any]] = []
    raw_relationships: list[dict[str, Any]] = []
    for table_match in table_pattern.finditer(text):
        close = _matching_paren(text, table_match.end() - 1)
        if close < 0:
            continue
        schema_name, table_name = _qualified(table_match.group("name"))
        if not table_name:
            continue
        body = text[table_match.end():close]
        body_start = table_match.end()
        columns: list[dict[str, Any]] = []
        identity_fields: set[str] = set()
        for declaration in _sql_table_body_declarations(body):
            clean = declaration.strip().strip(',')
            if not clean:
                continue
            rel_offset = body.find(declaration)
            offset = body_start + max(0, rel_offset)
            table_fk = re.match(
                r'''(?is)^(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\((?P<child>[^)]*)\)\s*(?P<rest>REFERENCES\s+.+)$''',
                clean,
            )
            if table_fk:
                ref = _reference(table_fk.group("rest"))
                if ref:
                    parent_schema, parent_table, parent_columns = ref
                    child_columns = _identifier_list(table_fk.group("child"))
                    locator = _line_locator(text, filename, offset, f"foreign-key={quote(table_name, safe='')}.{quote(','.join(child_columns), safe='')}")
                    raw_relationships.append((schema_name, table_name, child_columns, parent_schema, parent_table, parent_columns, locator))
                continue
            table_pk = re.match(r'''(?is)^(?:CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY\s*\((?P<columns>[^)]*)\)''', clean)
            if table_pk:
                identity_fields.update(_identifier_list(table_pk.group("columns")))
                continue
            table_unique = re.match(r'''(?is)^(?:CONSTRAINT\s+\S+\s+)?UNIQUE(?:\s+\S+)?\s*\((?P<columns>[^)]*)\)''', clean)
            if table_unique:
                unique_columns = _identifier_list(table_unique.group("columns"))
                if len(unique_columns) == 1:
                    identity_fields.add(unique_columns[0])
                continue
            column_match = re.match(
                r'''(?is)^(?P<name>"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)\s+(?P<rest>.+)$''',
                clean,
            )
            if not column_match:
                continue
            column_name = _unquote(column_match.group("name"))
            rest = column_match.group("rest")
            primary = bool(re.search(r"(?is)\bPRIMARY\s+KEY\b", rest))
            unique = bool(re.search(r"(?is)\bUNIQUE\b", rest))
            if primary or unique:
                identity_fields.add(column_name)
            columns.append({
                "name": column_name,
                "type": _sql_column_type(rest),
                "primary_key": primary,
                "unique": unique,
            })
            ref = _reference(rest)
            if ref:
                parent_schema, parent_table, parent_columns = ref
                locator = _line_locator(text, filename, offset, f"foreign-key={quote(table_name, safe='')}.{quote(column_name, safe='')}")
                raw_relationships.append((schema_name, table_name, [column_name], parent_schema, parent_table, parent_columns, locator))
        table_locator = _line_locator(text, filename, table_match.start(), f"table={quote(table_name, safe='')}")
        raw_tables.append({
            "schema": schema_name,
            "name": table_name,
            "columns": columns,
            "identity_fields": sorted(identity_fields),
            "source_locator": table_locator,
        })
    return text, raw_tables, raw_relationships
