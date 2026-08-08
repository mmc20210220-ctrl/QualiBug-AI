"""Disposable identity field materialization for governed non-production writes.

Repeated source-bound probes must not reuse customer/demo identity literals such
as documented emails or phone numbers. This module creates deterministic-shaped
but per-call unique values for disposable fixture creation while preserving the
specific field a fuzzer is mutating.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Iterable

_IDENTITY_ANCHOR_TOKENS = (
    "email",
    "phone",
    "mobile",
    "username",
    "login",
    "account",
)


def disposable_identity_nonce(*parts: Any) -> str:
    seed = "|".join(
        [
            *(str(part or "") for part in parts),
            str(time.time_ns()),
            str(os.getpid()),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def has_disposable_identity_anchor(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_l = _normalize_key(str(key))
            if any(token in key_l for token in _IDENTITY_ANCHOR_TOKENS):
                return True
            if has_disposable_identity_anchor(child):
                return True
    if isinstance(value, list):
        return any(has_disposable_identity_anchor(child) for child in value)
    return False


def materialize_disposable_identity_fields(
    value: Any,
    nonce: str,
    *,
    skip_keys: Iterable[str] | None = None,
    prefix: str = "",
) -> tuple[Any, list[str]]:
    """Return ``value`` with reusable identity literals replaced.

    ``skip_keys`` preserves the exact parameter currently under mutation, so the
    fuzzer still tests the requested invalid value while avoiding uniqueness
    collisions in the rest of the disposable identity fixture.
    """

    skipped = {_normalize_path(item) for item in (skip_keys or []) if str(item or "").strip()}
    materialized_fields: list[str] = []
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if _should_skip_materialization(path, str(key), skipped):
                rendered[str(key)] = child
                continue
            replacement = disposable_identity_value_for_key(str(key), child, nonce)
            if replacement is not None:
                rendered[str(key)] = replacement
                materialized_fields.append(path)
                continue
            rendered_child, child_fields = materialize_disposable_identity_fields(
                child,
                nonce,
                skip_keys=skipped,
                prefix=path,
            )
            rendered[str(key)] = rendered_child
            materialized_fields.extend(child_fields)
        return rendered, materialized_fields
    if isinstance(value, list):
        rendered_items: list[Any] = []
        for index, child in enumerate(value):
            rendered_child, child_fields = materialize_disposable_identity_fields(
                child,
                nonce,
                skip_keys=skipped,
                prefix=f"{prefix}[{index}]",
            )
            rendered_items.append(rendered_child)
            materialized_fields.extend(child_fields)
        return rendered_items, materialized_fields
    return value, materialized_fields


def disposable_identity_value_for_key(key: str, value: Any, nonce: str) -> Any:
    if not isinstance(value, str):
        return None
    key_l = _normalize_key(str(key))
    if not key_l:
        return None
    if "email" in key_l:
        return f"qb-auto-{nonce}@qualibug.local"
    if any(token in key_l for token in ("phone", "mobile")):
        return "155" + _numeric_suffix(nonce, 8)
    if any(token in key_l for token in ("username", "login", "account")) and "password" not in key_l:
        return f"qb_auto_{key_l}_{nonce}"
    if key_l in {"name", "displayname", "fullname", "nickname"} or key_l.endswith("name"):
        return f"qb_auto_{key_l}_{nonce}"
    if "password" in key_l or "credential" in key_l or "secret" in key_l:
        return f"QbAuto!{nonce[:8]}1"
    return None


def _numeric_suffix(nonce: str, width: int) -> str:
    try:
        number = int(str(nonce or "0"), 16)
    except ValueError:
        number = 0
    return str(number % (10 ** width)).zfill(width)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_path(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", str(value or "").lower()).strip(".")

# ── Schema-declared unique-key materialization (creation replayability) ──
# The disposable-identity channel covers customer/demo identity literals
# (email/phone/username). A target's DB schema additionally declares its own
# UNIQUE business keys (order_no, payment_no, sku, code, …). A creation write
# that replays a documented example whose unique key already exists (rows left
# by earlier runs) is rejected by the target before the rule under test is
# ever observed. Parsing UNIQUE constraints from the declared database schema
# is industry-neutral — every enterprise target has one — and keeps the
# harness out of customer vocabulary: the field set is data-driven, never a
# hard-coded industry term.

_CREATE_TABLE_BLOCK_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:\w+\.)?(\w+)\s*\((.*?)\)\s*;",
    re.S | re.I,
)
_UNIQUE_COLUMN_RE = re.compile(
    r"^\s*(\w+)\s+(\w+(?:\s*\(\s*\d+\s*\))?)\s+[^\n]*?\bUNIQUE\b",
    re.M | re.I,
)
_TABLE_UNIQUE_RE = re.compile(
    r"\bUNIQUE\s*\(\s*([^)]+?)\s*\)",
    re.I,
)
_INDEX_UNIQUE_RE = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX[^;]*?\bON\b\s+(?:\w+\.)?\w+\s*\(\s*([^)]+?)\s*\)",
    re.I,
)


_CHECK_ENUM_RE = re.compile(
    r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)",
    re.I,
)

# Column-level REFERENCES clause: the column is a foreign key to another
# table's row. Its value must name an EXISTING referenced row — a generated
# literal (unique-key nonce suffix) would fabricate a nonexistent reference
# and break the write with 404/500 before the rule under test is observed.
_FK_COLUMN_RE = re.compile(
    r"^\s*(\w+)\s+.*?\bREFERENCES\b",
    re.M | re.I,
)
_TABLE_FK_RE = re.compile(
    r"\bFOREIGN\s+KEY\s*\(\s*([^)]+?)\s*\)\s*REFERENCES\b",
    re.I,
)


def declared_schema_tables(schema_text: str) -> set[str]:
    """Parse normalized table names from a target DB schema."""
    tables: set[str] = set()
    if not isinstance(schema_text, str) or not schema_text.strip():
        return tables
    for block in _CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _normalize_key(block.group(1))
        if table:
            tables.add(table)
    return tables


def declared_fk_reference_columns(schema_text: str) -> dict[str, set[str]]:
    """Parse FOREIGN KEY reference columns per table from a target DB schema.

    Returns ``{table: {child_fk_column, ...}}``. A column that ``REFERENCES``
    another table is a foreign key (e.g. cart_items.sku -> products.sku): it
    names an existing row and must never be nonce-suffixed as a unique key.
    The referenced (parent) side is unaffected — a products create may still
    generate its own unique sku. Parsing REFERENCES clauses is
    industry-neutral SQL present in every enterprise target's schema.
    """
    columns_by_table: dict[str, set[str]] = {}
    if not isinstance(schema_text, str) or not schema_text.strip():
        return columns_by_table
    for block in _CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _normalize_key(block.group(1))
        if not table:
            continue
        columns: set[str] = set()
        for m in _FK_COLUMN_RE.finditer(block.group(2)):
            columns.add(_normalize_key(m.group(1)))
        for m in _TABLE_FK_RE.finditer(block.group(2)):
            for col in m.group(1).split(","):
                columns.add(_normalize_key(col.strip().strip('"')))
        if columns:
            columns_by_table[table] = columns
    return {t: {c for c in cols if c} for t, cols in columns_by_table.items() if cols}

# Column-level REFERENCES clause: the column is a foreign key to another
# table's row. Its value must name an EXISTING referenced row — a generated
# literal (unique-key nonce suffix) would fabricate a nonexistent reference
# and break the write with 404/500 before the rule under test is observed.
_FK_COLUMN_RE = re.compile(
    r"^\s*(\w+)\s+.*?\bREFERENCES\b",
    re.M | re.I,
)
_TABLE_FK_RE = re.compile(
    r"\bFOREIGN\s+KEY\s*\(\s*([^)]+?)\s*\)\s*REFERENCES\b",
    re.I,
)


def declared_schema_tables(schema_text: str) -> set[str]:
    """Parse normalized table names from a target DB schema."""
    tables: set[str] = set()
    if not isinstance(schema_text, str) or not schema_text.strip():
        return tables
    for block in _CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _normalize_key(block.group(1))
        if table:
            tables.add(table)
    return tables


def declared_fk_reference_columns(schema_text: str) -> dict[str, set[str]]:
    """Parse FOREIGN KEY reference columns per table from a target DB schema.

    Returns ``{table: {child_fk_column, ...}}``. A column that ``REFERENCES``
    another table is a foreign key (e.g. cart_items.sku -> products.sku): it
    names an existing row and must never be nonce-suffixed as a unique key.
    The referenced (parent) side is unaffected — a products create may still
    generate its own unique sku. Parsing REFERENCES clauses is
    industry-neutral SQL present in every enterprise target's schema.
    """
    columns_by_table: dict[str, set[str]] = {}
    if not isinstance(schema_text, str) or not schema_text.strip():
        return columns_by_table
    for block in _CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _normalize_key(block.group(1))
        if not table:
            continue
        columns: set[str] = set()
        for m in _FK_COLUMN_RE.finditer(block.group(2)):
            columns.add(_normalize_key(m.group(1)))
        for m in _TABLE_FK_RE.finditer(block.group(2)):
            for col in m.group(1).split(","):
                columns.add(_normalize_key(col.strip().strip('"')))
        if columns:
            columns_by_table[table] = columns
    return {t: {c for c in cols if c} for t, cols in columns_by_table.items() if cols}


def declared_check_enum_values(schema_text: str) -> dict[str, dict[str, list[str]]]:
    """Parse CHECK (field IN ('a','b')) constraints from a target DB schema.

    Maps table -> constrained field (normalized) -> declared legal values.
    Industry-neutral: CHECK-IN constraints are standard SQL present in every
    enterprise target's schema. Values are lowercased for comparison.
    """
    enums: dict[str, dict[str, list[str]]] = {}
    if not isinstance(schema_text, str) or not schema_text.strip():
        return enums
    for block in _CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        table = _normalize_key(block.group(1))
        if not table:
            continue
        table_enums: dict[str, list[str]] = {}
        for m in _CHECK_ENUM_RE.finditer(block.group(2)):
            field = _normalize_key(m.group(1))
            if not field:
                continue
            values = [
                v.strip().strip("'").strip('"')
                for v in m.group(2).split(",")
                if v.strip()
            ]
            if values:
                table_enums[field] = list(dict.fromkeys(values))
        if table_enums:
            enums[table] = table_enums
    return enums


def align_body_enums_with_declared_schema(
    body: Any,
    enums_by_table: dict[str, dict[str, list[str]]],
    *,
    table_hint: str = "",
) -> tuple[Any, list[str]]:
    """Replace body values that violate the hinted table's CHECK-IN enum.

    A documented create example can drift from the target's schema (e.g. a
    product example carrying the users-table status literal ACTIVE while the
    products status CHECK only allows DRAFT/ON_SALE/OFF_SALE/DELETED). The
    target then rejects the fixture create with 500 before the rule under
    test is observed. Replacing the offending literal with the first legal
    value (verbatim case) of the create's own table keeps the fixture
    constructible. The
    table is derived from the create path's collection segment (the standard
    /api/<table> convention); without a table match the body is left alone
    rather than guessing. Only string values are touched.
    """
    if not enums_by_table or not isinstance(body, dict):
        return body, []
    table_key = _normalize_key(table_hint)
    table_enums = enums_by_table.get(table_key)
    if not table_enums:
        return body, []
    aligned: list[str] = []
    fixed_body = dict(body)
    for key, child in body.items():
        if not isinstance(child, str):
            continue
        key_l = _normalize_key(str(key))
        values = table_enums.get(key_l)
        if not values:
            continue
        lowered = child.lower()
        if lowered in [v.lower() for v in values]:
            continue
        # Constraints are case-sensitive in the target (CHECK IN ('DRAFT',
        # 'ON_SALE', ...)): replace with the first declared literal verbatim.
        fixed_body[key] = values[0]
        aligned.append(str(key))
    return fixed_body, aligned


def _is_identity_anchor_field(field: str) -> bool:
    """True when the unique field is already handled by the disposable channel."""
    key_l = _normalize_key(field)
    if not key_l:
        return True
    if "password" in key_l or "credential" in key_l or "secret" in key_l:
        return True
    return any(token in key_l for token in _IDENTITY_ANCHOR_TOKENS)


def declared_unique_fields(schema_text: str) -> set[str]:
    """Parse UNIQUE business-key columns from a target DB schema.

    Accepts column-level ``UNIQUE`` inside CREATE TABLE blocks, table-level
    ``UNIQUE(col)`` constraints, and ``CREATE UNIQUE INDEX ... ON t(col)``.
    Composite constraints are skipped (a single-column suffix cannot keep a
    multi-column key unique). Identity-anchor fields are excluded because the
    disposable-identity channel already makes them unique per call.
    """
    fields: set[str] = set()
    if not isinstance(schema_text, str) or not schema_text.strip():
        return fields
    for block in _CREATE_TABLE_BLOCK_RE.finditer(schema_text):
        for col in _UNIQUE_COLUMN_RE.finditer(block.group(2)):
            fields.add(_normalize_key(col.group(1)))
    for m in _TABLE_UNIQUE_RE.finditer(schema_text):
        columns = [c.strip().strip('"') for c in m.group(1).split(",")]
        if len(columns) == 1:
            fields.add(_normalize_key(columns[0]))
    for m in _INDEX_UNIQUE_RE.finditer(schema_text):
        columns = [c.strip().strip('"') for c in m.group(1).split(",")]
        if len(columns) == 1:
            fields.add(_normalize_key(columns[0]))
    return {f for f in fields if f and not _is_identity_anchor_field(f)}


def materialize_unique_create_fields(
    value: Any,
    nonce: str,
    unique_fields: set[str],
    *,
    fk_reference_columns: dict[str, set[str]] | set[str] | None = None,
    table_hint: str = "",
    schema_tables: set[str] | None = None,
) -> tuple[Any, list[str]]:
    """Append a per-call nonce to schema-declared unique key literals.

    Applies to creation bodies: single-entity POST bodies whose top-level
    fields are unique keys, and batch-create arrays (``{products: [...]}``)
    whose element fields are unique keys. Only string values are touched —
    numeric unique keys are left alone.

    Foreign-key reference columns are never suffixed: their value must name an
    existing referenced row, so a nonce suffix would fabricate a nonexistent
    reference (404/500) and block the write before the rule under test is
    observed. The exclusion is table-scoped — a create on the referenced
    table itself (e.g. products.sku, the parent side) still gets its own
    unique key suffixed. Nested array keys are only treated as batch-create
    arrays when the key names a declared table (``{products: [...]}``); a
    business-detail array (``items[].sku``) references existing rows and is
    never rewritten. Returns the new body and the list of materialized field
    paths for audit visibility.
    """
    if not unique_fields or not isinstance(value, dict):
        return value, []
    normalized = {_normalize_key(f) for f in unique_fields}
    materialized: list[str] = []

    # Resolve the create's own table from the path hint (first segment) so
    # the child-FK exclusion applies to the right table. No hint match falls
    # back to the flat exclusion set (fail toward not rewriting references).
    fk_flat: set[str] = set()
    fk_by_table: dict[str, set[str]] = {}
    if isinstance(fk_reference_columns, dict):
        for table, cols in fk_reference_columns.items():
            fk_by_table[_normalize_key(str(table))] = {
                _normalize_key(c) for c in cols
            }
            fk_flat |= fk_by_table[_normalize_key(str(table))]
    elif fk_reference_columns:
        fk_flat = {_normalize_key(c) for c in fk_reference_columns}
    hint = _normalize_key(table_hint)
    schema_tables_norm = {
        _normalize_key(t) for t in (schema_tables or set())
    }
    matched_tables = {
        table
        for table in fk_by_table
        if hint and (table == hint or table.startswith(hint) or hint.startswith(table))
    }
    excluded: set[str] = set()
    for table in matched_tables:
        excluded |= fk_by_table[table]
    if not matched_tables:
        # The hint names a declared table with no child-FK columns of its
        # own (products.sku is the referenced parent side): nothing to
        # exclude, the create may generate its own unique key. Only when the
        # hint matches NO declared table is the flat exclusion applied
        # (unidentified collection — fail toward not rewriting references).
        hint_matches_schema_table = any(
            hint and (t == hint or t.startswith(hint) or hint.startswith(t))
            for t in schema_tables_norm
        )
        if not hint_matches_schema_table:
            excluded = fk_flat

    def _excluded_key(key_l: str) -> bool:
        return key_l in excluded

    def _unique_replace(item: dict[str, Any], path: str) -> None:
        for key, child in item.items():
            key_l = _normalize_key(str(key))
            if (
                key_l in normalized
                and not _excluded_key(key_l)
                and isinstance(child, str)
                and child.strip()
            ):
                item[key] = f"{child}-{nonce}"
                materialized.append(f"{path}.{key}")

    for key, child in value.items():
        key_l = _normalize_key(str(key))
        if isinstance(child, list):
            # Batch-create arrays must name their own table ({products: [...]});
            # business-detail arrays (items[].sku) reference existing rows.
            if schema_tables_norm and key_l not in schema_tables_norm:
                continue
            for index, element in enumerate(child):
                if isinstance(element, dict):
                    _unique_replace(element, f"{key}[{index}]")
        elif isinstance(child, str):
            if key_l in normalized and not _excluded_key(key_l) and child.strip():
                value[key] = f"{child}-{nonce}"
                materialized.append(str(key))
    return value, materialized



# ── Schema-declared unique-key materialization (creation replayability) ──
# The disposable-identity channel covers customer/demo identity literals
# (email/phone/username). A target's DB schema additionally declares its own
# UNIQUE business keys (order_no, payment_no, sku, code, …). A creation write
# that replays a documented example whose unique key already exists (rows left
# by earlier runs) is rejected by the target before the rule under test is
# ever observed. Parsing UNIQUE constraints from the declared database schema
# is industry-neutral — every enterprise target has one — and keeps the
# harness out of customer vocabulary: the field set is data-driven, never a
# hard-coded industry term.


def _should_skip_materialization(path: str, key: str, skipped: set[str]) -> bool:
    if not skipped:
        return False
    normalized_path = _normalize_path(path)
    normalized_key = _normalize_path(key)
    return normalized_path in skipped or normalized_key in skipped
