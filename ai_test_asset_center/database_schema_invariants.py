"""Schema-declared constraint dimension: industry-universal DB invariants.

The database layer of an enterprise system is a first-class business surface.
A schema that declares money and quantity columns without any non-negative
guard, or an idempotency-intent column without a uniqueness constraint, is a
source-declared structural property of the system — the same property in any
industry. This module turns those schema facts into rule-library entries so
they flow through the standard rule → Behavior IR → obligation path.

Everything here is derived from the declared DDL only: table names, column
names, column types, unique/primary declarations and CHECK clauses. Column
semantic classes (money / quantity / uniqueness intent) use generic business
vocabulary (金额/数量/库存/幂等 are industry-neutral words for value, count,
stock and idempotency), never industry terms and never benchmark material.

A column WITH a guard or constraint produces nothing — the absence of the
guard is what the invariant targets, and the invariant asserts the guard the
schema omits. No inference about customer data or business rules beyond the
column's own declaration.
"""
from __future__ import annotations

from typing import Any

# Generic value (money-like) vocabulary — industry-neutral.
_MONEY_TOKENS = (
    "amount", "price", "payable", "total", "discount", "balance",
    "fee", "cost", "charge", "value", "premium",
    "金额", "价格", "应付", "余额", "优惠", "费用", "单价", "总价",
)
# Generic count/stock vocabulary.
_QUANTITY_TOKENS = (
    "qty", "quantity", "count", "stock", "inventory", "capacity", "quota",
    "数量", "库存", "容量", "额度",
)
# Uniqueness-intent vocabulary: the column name itself declares that its
# values are business identities that must not repeat.
_UNIQUENESS_TOKENS = (
    "idempotency", "idempotent", "dedup", "dedupe", "exactly_once",
    "幂等",
)
_NUMERIC_TYPES = frozenset({
    "numeric", "decimal", "int", "integer", "bigint", "smallint",
    "tinyint", "float", "double", "real", "money", "serial",
})
_TEXT_TYPES = frozenset({
    "text", "varchar", "char", "uuid",
})

SCHEMA_RULE_SOURCE_KIND = "database_schema"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _numeric_type(column_type: str) -> bool:
    return _text(column_type).lower() in _NUMERIC_TYPES


def _matches_any(token: str, vocabulary: tuple[str, ...]) -> bool:
    lowered = _text(token).lower()
    return any(word in lowered for word in vocabulary)


def _column_guards(
    table: dict[str, Any],
    column: str,
) -> tuple[bool, bool]:
    """Return (has_non_negative_guard, has_positive_guard) for one column."""
    non_negative = False
    positive = False
    for raw in _list(table.get("check_constraints")):
        constraint = raw if isinstance(raw, dict) else {}
        if _text(constraint.get("column")).lower() != _text(column).lower():
            continue
        operator = _text(constraint.get("operator")).lower()
        value = _text(constraint.get("value")).lower()
        if operator in {">=", ">"} and value == "0":
            non_negative = True
        if operator == ">" and value == "0":
            positive = True
    return non_negative, positive


def _identity_columns(table: dict[str, Any]) -> set[str]:
    return {
        _text(value).lower()
        for value in [
            * _list(table.get("identity_fields")),
            * _list(table.get("unique_columns")),
        ]
        if _text(value)
    }


def _boundary_rule(
    *,
    source_id: str,
    table: str,
    column: str,
    column_type: str,
    operator: str,
    statement: str,
    risk_type: str,
) -> dict[str, Any]:
    rule_id = f"rule:{source_id}:schema:{table}:{column}:{operator}"
    return {
        "rule_id": rule_id,
        "source_id": source_id,
        "source_type": SCHEMA_RULE_SOURCE_KIND,
        "source_locator": f"table:{table}:column:{column}",
        "statement": statement,
        "rule_type": "conservation",
        "kind": "numeric_boundary",
        "risk_type": risk_type,
        "severity": "P1",
        "tokens": [table, column, column_type, operator],
        "operands": [{
            "entity_ref": table,
            "field": column,
            "field_id": f"schema:{table}:{column}",
            "semantic_type": "NUMERIC_BOUNDARY",
        }],
        "equation": {
            "operator": operator,
            "terms": [column],
        },
    }


def derive_database_constraint_rules(
    data_tables: list[dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    """Derive industry-universal constraint invariants from declared DDL.

    Returns rule-library entries. Rules are deterministic (stable rule ids);
    a column with the declared guard produces no rule.
    """
    rules: list[dict[str, Any]] = []
    for raw in data_tables:
        table = raw if isinstance(raw, dict) else {}
        table_name = _text(table.get("name"))
        if not table_name:
            continue
        column_types = table.get("column_types") if isinstance(table.get("column_types"), dict) else {}
        identity = _identity_columns(table)
        for column in _list(table.get("columns")):
            column_name = _text(column)
            if not column_name:
                continue
            column_type = _text(column_types.get(column_name))
            lowered = column_name.lower()

            if _numeric_type(column_type):
                has_non_negative, has_positive = _column_guards(table, column_name)
                is_money = _matches_any(lowered, _MONEY_TOKENS)
                is_quantity = _matches_any(lowered, _QUANTITY_TOKENS)
                if is_money and not has_non_negative:
                    rules.append(_boundary_rule(
                        source_id=source_id,
                        table=table_name,
                        column=column_name,
                        column_type=column_type,
                        operator="non_negative",
                        statement=(
                            f"`{table_name}`.`{column_name}` 必须非负，"
                            f"数值不允许为负 (must not go below zero)"
                        ),
                        risk_type="data_conservation",
                    ))
                if is_quantity:
                    if not has_positive:
                        rules.append(_boundary_rule(
                            source_id=source_id,
                            table=table_name,
                            column=column_name,
                            column_type=column_type,
                            operator="positive",
                            statement=(
                                f"`{table_name}`.`{column_name}` 必须为正数，"
                                f"数量不允许为负 (must stay positive)"
                            ),
                            risk_type="data_conservation",
                        ))
                    if not has_non_negative:
                        rules.append(_boundary_rule(
                            source_id=source_id,
                            table=table_name,
                            column=column_name,
                            column_type=column_type,
                            operator="non_negative",
                            statement=(
                                f"`{table_name}`.`{column_name}` 必须非负，"
                                f"数值不允许为负 (must not go below zero)"
                            ),
                            risk_type="data_conservation",
                        ))

            if _matches_any(lowered, _UNIQUENESS_TOKENS):
                if lowered in identity:
                    continue
                if _text(column_type).lower() not in _TEXT_TYPES and not _numeric_type(column_type):
                    continue
                rules.append({
                    "rule_id": f"rule:{source_id}:schema:{table_name}:{column_name}:uniqueness",
                    "source_id": source_id,
                    "source_type": SCHEMA_RULE_SOURCE_KIND,
                    "source_locator": f"table:{table_name}:column:{column_name}",
                    "statement": (
                        f"`{table_name}`.`{column_name}` 必须唯一，"
                        f"重复值不允许出现 (values must be unique)"
                    ),
                    "rule_type": "validation",
                    "kind": "validation_uniqueness",
                    "risk_type": "idempotency",
                    "severity": "P1",
                    "tokens": [table_name, column_name, "unique"],
                    "operands": [{
                        "entity_ref": table_name,
                        "field": column_name,
                        "field_id": f"schema:{table_name}:{column_name}",
                        "semantic_type": "UNIQUENESS",
                    }],
                })
    return rules


__all__ = ["derive_database_constraint_rules"]
