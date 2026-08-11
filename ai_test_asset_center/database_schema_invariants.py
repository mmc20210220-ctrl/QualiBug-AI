"""Project explicit database constraints into the business-rule pipeline.

Database structure is evidence, not permission to invent a business rule.  This
module therefore projects only constraints that the supplied DDL explicitly
declares (currently supported CHECK boundaries and UNIQUE columns).  A missing
CHECK/UNIQUE is a coverage fact, never evidence that the customer intended the
missing constraint.

This boundary is deliberately fail-closed:
- column/table names never imply money, quantity, stock, idempotency, or any
  other business semantic;
- absence of a constraint never creates a rule;
- only parser-produced, source-declared constraint rows are projected;
- every generated rule carries its exact DDL authority.
"""
from __future__ import annotations

from typing import Any

SCHEMA_RULE_SOURCE_KIND = "database_schema"

_NUMERIC_TYPES = frozenset({
    "numeric", "decimal", "int", "integer", "bigint", "smallint",
    "tinyint", "float", "double", "real", "money", "serial",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _numeric_type(column_type: str) -> bool:
    token = _text(column_type).lower()
    # Parsed SQL types may preserve precision, e.g. NUMERIC(12,2).
    base = token.split("(", 1)[0].strip()
    return base in _NUMERIC_TYPES


def _column_type(table: dict[str, Any], column: str) -> str:
    column_types = (
        table.get("column_types")
        if isinstance(table.get("column_types"), dict)
        else {}
    )
    direct = _text(column_types.get(column))
    if direct:
        return direct
    lowered = _text(column).lower()
    for key, value in column_types.items():
        if _text(key).lower() == lowered:
            return _text(value)
    return ""


def _constraint_rule_id(
    source_id: str,
    table: str,
    column: str,
    authority: str,
    operator: str,
) -> str:
    return f"rule:{source_id}:schema:{table}:{column}:{authority}:{operator}"


def _boundary_rule(
    *,
    source_id: str,
    table: str,
    column: str,
    column_type: str,
    operator: str,
    ddl_operator: str,
    ddl_value: str,
) -> dict[str, Any]:
    statement = (
        f"DDL explicitly declares CHECK on `{table}`.`{column}`: "
        f"{column} {ddl_operator} {ddl_value}"
    )
    return {
        "rule_id": _constraint_rule_id(
            source_id, table, column, "check", operator
        ),
        "source_id": source_id,
        "source_type": SCHEMA_RULE_SOURCE_KIND,
        "source_locator": f"table:{table}:column:{column}:check",
        "statement": statement,
        "rule_type": "validation",
        "kind": "numeric_boundary",
        "risk_type": "input_boundary",
        "severity": "P1",
        "tokens": [table, column, column_type, ddl_operator, ddl_value],
        "operands": [{
            "entity_ref": table,
            "field": column,
            "field_id": f"schema:{table}:{column}",
            "semantic_type": "NUMERIC_BOUNDARY",
        }],
        "equation": {
            "operator": operator,
            "terms": [column],
            "declared_operator": ddl_operator,
            "declared_value": ddl_value,
        },
        "constraint_authority": {
            "kind": "DDL_CHECK",
            "table": table,
            "column": column,
            "operator": ddl_operator,
            "value": ddl_value,
        },
        "inference_used": False,
    }


def _explicit_boundary_operator(
    ddl_operator: str,
    ddl_value: str,
) -> str:
    """Map only boundaries the existing assertion protocol can prove exactly."""

    operator = _text(ddl_operator)
    value = _text(ddl_value).lower()
    if value in {"0", "0.0", "0.00"}:
        if operator == ">=":
            return "non_negative"
        if operator == ">":
            return "positive"
    return ""


def _unique_rule(
    *,
    source_id: str,
    table: str,
    column: str,
    column_type: str,
) -> dict[str, Any]:
    return {
        "rule_id": _constraint_rule_id(
            source_id, table, column, "unique", "uniqueness"
        ),
        "source_id": source_id,
        "source_type": SCHEMA_RULE_SOURCE_KIND,
        "source_locator": f"table:{table}:column:{column}:unique",
        "statement": (
            f"DDL explicitly declares `{table}`.`{column}` UNIQUE"
        ),
        "rule_type": "validation",
        "kind": "validation_uniqueness",
        "risk_type": "data_integrity",
        "severity": "P1",
        "tokens": [table, column, column_type, "unique"],
        "operands": [{
            "entity_ref": table,
            "field": column,
            "field_id": f"schema:{table}:{column}",
            "semantic_type": "UNIQUENESS",
        }],
        "constraint_authority": {
            "kind": "DDL_UNIQUE",
            "table": table,
            "column": column,
        },
        "inference_used": False,
    }


def derive_database_constraint_rules(
    data_tables: list[dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    """Project explicit DDL constraints into rule-library entries.

    Missing constraints intentionally produce no rule.  Unsupported explicit
    CHECK shapes also produce no rule rather than being coerced into a nearby
    semantic assertion.  They remain available in the parsed schema for future
    protocol support.
    """

    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in data_tables:
        table = raw if isinstance(raw, dict) else {}
        table_name = _text(table.get("name"))
        if not table_name:
            continue

        for constraint_raw in _list(table.get("check_constraints")):
            constraint = (
                constraint_raw if isinstance(constraint_raw, dict) else {}
            )
            column = _text(constraint.get("column"))
            ddl_operator = _text(constraint.get("operator"))
            ddl_value = _text(constraint.get("value"))
            if not column or not ddl_operator or not ddl_value:
                continue
            column_type = _column_type(table, column)
            if not _numeric_type(column_type):
                continue
            operator = _explicit_boundary_operator(ddl_operator, ddl_value)
            if not operator:
                continue
            rule = _boundary_rule(
                source_id=source_id,
                table=table_name,
                column=column,
                column_type=column_type,
                operator=operator,
                ddl_operator=ddl_operator,
                ddl_value=ddl_value,
            )
            if rule["rule_id"] not in seen:
                seen.add(rule["rule_id"])
                rules.append(rule)

        for raw_column in _list(table.get("unique_columns")):
            column = _text(raw_column)
            if not column:
                continue
            rule = _unique_rule(
                source_id=source_id,
                table=table_name,
                column=column,
                column_type=_column_type(table, column),
            )
            if rule["rule_id"] not in seen:
                seen.add(rule["rule_id"])
                rules.append(rule)

    return rules


__all__ = ["derive_database_constraint_rules"]
