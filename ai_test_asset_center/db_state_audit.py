"""IR-driven database state audit.

Generic module that:
1. Reads Behavior IR (entities, states, invariants) from any project
2. Introspects the target database schema (multi-database + sharded tables)
3. Maps IR entities → DB tables via naming conventions (not hardcoding)
4. Runs universal invariant checks:
   a. State enumeration: IR states → valid values → check status columns
   b. Non-negative: numeric columns with qty/amount/price/balance → >= 0
   c. Referential integrity: _id columns → check referenced rows exist
   d. Cross-table amount consistency: parent.total vs sum(children.line_amount)
   e. Cross-database referential integrity (application-level JOIN)
   f. Sharded-table-aware aggregation (UNION ALL across shards)
5. Produces findings using IR invariant descriptions (project's own terminology)

Enterprise topology support:
- Multiple databases (one module per DB): pass dsn_config dict/list
- Sharded tables (orders_0001, orders_0002): auto-detected, merged logically
- Cross-database FK: discovered by column-name + type matching, verified in Python
- No project-specific SQL, table names, or keywords are hardcoded.
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Naming convention helpers (generic, not project-specific) ──

_PLURAL_IRREGULAR = {
    "inventory": "inventory",
    "fulfillment": "fulfillments",
    "sku": "skus",
}

_STATUS_COLUMN_CANDIDATES = ("status", "state", "order_status", "payment_status")

_NON_NEGATIVE_PATTERNS = re.compile(
    r"(qty|quantity|amount|price|balance|stock|count|total|discount|payable|cost|fee|limit)",
    re.I,
)

_ID_COLUMN_RE = re.compile(r"^(.+)_id$")

# Shard suffix patterns: _0001, _01, _1, _00001, _shard1, _p0, _part_01
_SHARD_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)(?:_shard|_part|_partition)?[_](?P<idx>\d{1,6})$", re.I
)


# ── Multi-DB data source abstraction ──


@dataclass
class DataSource:
    """Represents one physical database in a potentially multi-DB topology."""
    dsn: str
    module: str = ""  # logical module name (e.g., 'order', 'payment')
    dialect: str = ""  # auto-detected from DSN if empty
    schema: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    # logical_tables maps logical_name → list of physical shard table names
    logical_tables: dict[str, list[str]] = field(default_factory=dict)
    conn: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.dialect:
            self.dialect = _detect_dialect(self.dsn)


def _detect_dialect(dsn: str) -> str:
    d = dsn.lower().strip()
    if d.startswith(("postgresql://", "postgres://")):
        return "postgresql"
    if d.startswith(("mysql://", "mariadb://")):
        return "mysql"
    if d.startswith("sqlite"):
        return "sqlite"
    return "unknown"


@dataclass
class LogicalTable:
    """A logical table that may span multiple physical shards or databases."""
    logical_name: str
    # Each entry: (data_source_index, physical_table_name)
    physical_locations: list[tuple[int, str]] = field(default_factory=list)
    columns: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_sharded(self) -> bool:
        return len(self.physical_locations) > 1


def _pluralize(name: str) -> str:
    """Generic English pluralization for entity→table mapping."""
    name = name.strip().lower()
    if name in _PLURAL_IRREGULAR:
        return _PLURAL_IRREGULAR[name]
    if name.endswith("s") or name.endswith("x") or name.endswith("z"):
        return name + "es"
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── Schema introspection ──


def _introspect_schema(cur: Any, dialect: str) -> dict[str, list[dict[str, str]]]:
    """Return {table_name: [{column_name, data_type}]}."""
    schema: dict[str, list[dict[str, str]]] = {}
    if dialect == "postgresql":
        cur.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY table_name, ordinal_position"
        )
    elif dialect in ("mysql", "mariadb"):
        cur.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema=DATABASE() ORDER BY table_name, ordinal_position"
        )
    else:
        cur.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns ORDER BY table_name, ordinal_position"
        )
    for row in cur.fetchall():
        if isinstance(row, dict):
            tbl = str(row.get("table_name") or "")
            col = str(row.get("column_name") or "")
            dtype = str(row.get("data_type") or "")
        else:
            tbl, col, dtype = str(row[0]), str(row[1]), str(row[2])
        if tbl and col:
            schema.setdefault(tbl, []).append({"column_name": col, "data_type": dtype})
    return schema


def _is_numeric_type(dtype: str) -> bool:
    dtype = dtype.lower()
    return any(t in dtype for t in ("int", "numeric", "decimal", "float", "double", "real", "money"))


# ── Sharded table detection & logical merging ──


def _detect_sharded_tables(
    schema: dict[str, list[dict[str, str]]],
) -> dict[str, list[str]]:
    """Detect sharded tables and group them by logical name.

    Patterns recognized (generic, not project-specific):
    - orders_0001, orders_0002 → logical 'orders'
    - user_01, user_02 → logical 'user'
    - payment_shard_1, payment_shard_2 → logical 'payment'
    - log_part_001, log_part_002 → logical 'log'

    Returns {logical_name: [physical_table_1, physical_table_2, ...]}.
    Only groups with 2+ physical tables are returned.
    """
    groups: dict[str, list[str]] = {}
    for table_name in schema:
        m = _SHARD_SUFFIX_RE.match(table_name)
        if not m:
            continue
        base = m.group("base").rstrip("_")
        if not base:
            continue
        groups.setdefault(base, []).append(table_name)

    # Only keep groups with 2+ shards (single table with numeric suffix is not a shard)
    result: dict[str, list[str]] = {}
    for base, tables in groups.items():
        if len(tables) >= 2:
            # Verify all shards have same column structure (signature check)
            sigs = set()
            for t in tables:
                cols = schema.get(t, [])
                sig = tuple(sorted((c["column_name"], c["data_type"]) for c in cols))
                sigs.add(sig)
            if len(sigs) == 1:
                # All shards have identical schema → confirmed shard group
                result[base] = sorted(tables)
            else:
                logger.debug(
                    "Shard candidate '%s' has inconsistent schemas (%d variants), skipping",
                    base, len(sigs),
                )
    return result


def _build_logical_schema(
    schema: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[str]]]:
    """Build a logical schema where sharded tables are merged.

    Returns:
        (logical_schema, shard_map)
        - logical_schema: {logical_table_name: columns} — shards replaced by base name
        - shard_map: {logical_name: [physical_table_1, ...]}
    """
    shard_map = _detect_sharded_tables(schema)
    if not shard_map:
        return schema, {}

    # Tables that are physical shards (to be removed from logical schema)
    shard_physical = set()
    for tables in shard_map.values():
        shard_physical.update(tables)

    logical_schema: dict[str, list[dict[str, str]]] = {}
    for table_name, cols in schema.items():
        if table_name in shard_physical:
            continue  # will be represented by logical name
        logical_schema[table_name] = cols

    # Add logical tables (use first shard's columns as representative)
    for logical_name, physical_tables in shard_map.items():
        if physical_tables:
            logical_schema[logical_name] = schema[physical_tables[0]]

    logger.info(
        "Shard detection: %d logical tables from %d physical shards",
        len(shard_map), sum(len(v) for v in shard_map.values()),
    )
    return logical_schema, shard_map


def _shard_union_subquery(
    physical_tables: list[str],
    columns_needed: list[str] | None = None,
) -> str:
    """Build a UNION ALL subquery across shard tables.

    If columns_needed is provided, only select those columns.
    Otherwise SELECT *.
    """
    if len(physical_tables) == 1:
        t = physical_tables[0]
        if columns_needed:
            cols = ", ".join(f'"{ c}"' for c in columns_needed)
            return f'(SELECT {cols} FROM "{t}")'
        return f'"{t}"'

    parts = []
    for t in physical_tables:
        if columns_needed:
            cols = ", ".join(f'"{ c}"' for c in columns_needed)
            parts.append(f'SELECT {cols} FROM "{t}"')
        else:
            parts.append(f'SELECT * FROM "{t}"')
    return "(" + " UNION ALL ".join(parts) + ") AS _sharded"


def _shard_aware_count(
    cur: Any,
    physical_tables: list[str],
    where_clause: str,
    params: list[Any] | None = None,
) -> int:
    """COUNT across all shards of a logical table."""
    if len(physical_tables) == 1:
        sql = f'SELECT COUNT(*) FROM "{physical_tables[0]}" WHERE {where_clause}'
    else:
        union = " UNION ALL ".join(
            f'SELECT * FROM "{t}"' for t in physical_tables
        )
        sql = f'SELECT COUNT(*) FROM ({union}) AS _s WHERE {where_clause}'
    try:
        cur.execute(sql, params or [])
        return int(cur.fetchone()[0])
    except Exception as exc:
        logger.debug("shard_aware_count failed: %s", exc)
        return 0


def _shard_aware_group_count(
    cur: Any,
    physical_tables: list[str],
    group_col: str,
    where_clause: str = "1=1",
    params: list[Any] | None = None,
    limit: int = 5,
) -> list[tuple[Any, int]]:
    """GROUP BY count across all shards."""
    if len(physical_tables) == 1:
        sql = (
            f'SELECT "{group_col}", COUNT(*) FROM "{physical_tables[0]}" '
            f'WHERE {where_clause} GROUP BY "{group_col}" '
            f'ORDER BY COUNT(*) DESC LIMIT {limit}'
        )
    else:
        union = " UNION ALL ".join(
            f'SELECT * FROM "{t}"' for t in physical_tables
        )
        sql = (
            f'SELECT "{group_col}", COUNT(*) FROM ({union}) AS _s '
            f'WHERE {where_clause} GROUP BY "{group_col}" '
            f'ORDER BY COUNT(*) DESC LIMIT {limit}'
        )
    try:
        cur.execute(sql, params or [])
        return [(r[0], int(r[1])) for r in cur.fetchall()]
    except Exception as exc:
        logger.debug("shard_aware_group_count failed: %s", exc)
        return []


# ── Multi-DB connection management ──


def _connect_data_source(ds: DataSource) -> bool:
    """Connect a DataSource and introspect its schema. Returns True on success."""
    try:
        import psycopg2
        ds.conn = psycopg2.connect(ds.dsn, connect_timeout=10)
    except ImportError:
        logger.warning("psycopg2 not installed; cannot connect to %s", ds.module or ds.dsn[:40])
        return False
    except Exception as exc:
        logger.warning("DB connection failed for %s: %s", ds.module or ds.dsn[:40], exc)
        return False

    try:
        cur = ds.conn.cursor()
        ds.schema = _introspect_schema(cur, ds.dialect)
        # Detect and record sharded tables
        shard_map = _detect_sharded_tables(ds.schema)
        ds.logical_tables = shard_map
        cur.close()
        return True
    except Exception as exc:
        logger.warning("Schema introspection failed for %s: %s", ds.module or ds.dsn[:40], exc)
        return False


def _close_data_source(ds: DataSource) -> None:
    """Safely close a DataSource connection."""
    try:
        if ds.conn:
            ds.conn.close()
    except Exception:
        pass
    ds.conn = None


def _normalize_dsn_config(
    dsn: "str | list[str] | dict[str, str]",
) -> list[DataSource]:
    """Normalize various DSN input formats into a list of DataSource.

    Supported formats:
    - Single DSN string: "postgresql://..."
    - List of DSNs: ["postgresql://db1", "postgresql://db2"]
    - Dict module→DSN: {"order": "postgresql://order_db", "payment": "postgresql://pay_db"}
    """
    if isinstance(dsn, str):
        return [DataSource(dsn=dsn, module="default")]
    if isinstance(dsn, dict):
        return [
            DataSource(dsn=v, module=k)
            for k, v in dsn.items()
            if isinstance(v, str) and v.strip()
        ]
    if isinstance(dsn, (list, tuple)):
        sources = []
        for i, item in enumerate(dsn):
            if isinstance(item, str):
                sources.append(DataSource(dsn=item, module=f"db_{i}"))
            elif isinstance(item, DataSource):
                sources.append(item)
        return sources
    return []


def _merged_schema_from_sources(
    sources: list[DataSource],
) -> tuple[dict[str, list[dict[str, str]]], dict[str, int]]:
    """Merge schemas from multiple data sources into one unified view.

    Returns:
        (merged_schema, table_source_map)
        - merged_schema: {table_name: columns} across all DBs
        - table_source_map: {table_name: source_index} for routing queries
    """
    merged: dict[str, list[dict[str, str]]] = {}
    table_source: dict[str, int] = {}

    for idx, ds in enumerate(sources):
        logical_schema, _ = _build_logical_schema(ds.schema)
        for table, cols in logical_schema.items():
            if table not in merged:
                merged[table] = cols
                table_source[table] = idx
            else:
                # Table exists in multiple DBs — keep both, mark as cross-db
                # Use module-qualified name to disambiguate
                qualified = f"{ds.module}.{table}" if ds.module else f"db{idx}.{table}"
                merged[qualified] = cols
                table_source[qualified] = idx
                logger.info(
                    "Table '%s' exists in multiple databases; added as '%s'",
                    table, qualified,
                )
    return merged, table_source


# ── Entity→Table mapping ──


def _map_entities_to_tables(
    entities: list[dict[str, Any]],
    schema: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    """Map IR entity names to DB table names via naming conventions."""
    table_set = set(schema.keys())
    mapping: dict[str, str] = {}
    for ent in entities:
        name = str(ent.get("name") or "").strip().lower()
        if not name:
            continue
        # Try exact, plural, and source_entity_names
        candidates = [name, _pluralize(name)]
        for src in ent.get("source_entity_names") or []:
            s = str(src).strip().lower()
            if s:
                candidates.extend([s, _pluralize(s)])
        for cand in candidates:
            if cand in table_set:
                mapping[name] = cand
                break
    return mapping


def _find_status_column(columns: list[dict[str, str]]) -> str:
    """Find the status/state column in a table."""
    col_names = [c["column_name"].lower() for c in columns]
    for candidate in _STATUS_COLUMN_CANDIDATES:
        if candidate in col_names:
            return candidate
    return ""


# ── Check runners ──


def _check_state_enumeration(
    cur: Any,
    entity_name: str,
    table: str,
    valid_states: list[str],
    columns: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Check for records with states not in the IR-declared valid set."""
    status_col = _find_status_column(columns)
    if not status_col or not valid_states:
        return []

    # Normalize: keep uppercase versions (DB typically stores uppercase enums)
    upper_states = sorted({s.upper() for s in valid_states if s})
    if not upper_states:
        return []

    placeholders = ",".join(["%s"] * len(upper_states))
    try:
        cur.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE UPPER("{status_col}") NOT IN ({placeholders})',
            upper_states,
        )
        count = cur.fetchone()[0]
    except Exception as exc:
        logger.debug("state enumeration check failed for %s: %s", table, exc)
        return []

    if not count:
        return []

    # Get sample violating records
    try:
        cur.execute(
            f'SELECT "{status_col}", COUNT(*) FROM "{table}" '
            f'WHERE UPPER("{status_col}") NOT IN ({placeholders}) '
            f'GROUP BY "{status_col}" ORDER BY COUNT(*) DESC LIMIT 5',
            upper_states,
        )
        samples = cur.fetchall()
    except Exception:
        samples = []

    sample_desc = "; ".join(
        f"{str(r[0])}({r[1]}条)" for r in samples
    ) if samples else f"{count}条记录"

    return [{
        "title": f"{entity_name}状态枚举违反: {count}条记录状态不在合法集合中",
        "description": (
            f"数据库审计发现{table}中{count}条记录的{status_col}值"
            f"不在Behavior IR声明的合法状态集合{upper_states}中。"
            f"违反状态: {sample_desc}。"
            f"状态机约束要求{entity_name}只能处于已声明的合法状态。"
        ),
        "summary": f"{entity_name}存在非法状态值,违反状态机约束",
        "category": "state_machine_violation",
        "defect_family": "state_transition",
        "risk_type": "business_logic",
        "expected": f"{entity_name}.{status_col}必须在合法状态集合{upper_states}中",
        "actual": f"{count}条记录状态非法: {sample_desc}",
        "severity": "high",
        "confidence": "high",
        "reproduction": {"method": "DB_AUDIT", "path": f"/{table}"},
        "evidence_source": "db_state_audit",
        "observed_at": _now_iso(),
        "gate_passed": True,
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
    }]


def _check_non_negative(
    cur: Any,
    entity_name: str,
    table: str,
    columns: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Check numeric columns with quantity/amount semantics for negative values."""
    findings = []
    for col_info in columns:
        col = col_info["column_name"]
        dtype = col_info["data_type"]
        if not _is_numeric_type(dtype):
            continue
        if not _NON_NEGATIVE_PATTERNS.search(col):
            continue
        # Skip columns that are semantically allowed to be negative (e.g., adjustment)
        if any(skip in col.lower() for skip in ("adjust", "delta", "change", "diff")):
            continue
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" < 0')
            count = cur.fetchone()[0]
        except Exception:
            continue
        if not count:
            continue
        try:
            cur.execute(
                f'SELECT "{col}", COUNT(*) FROM "{table}" WHERE "{col}" < 0 '
                f'GROUP BY "{col}" ORDER BY "{col}" ASC LIMIT 3'
            )
            samples = cur.fetchall()
            sample_desc = "; ".join(f"{col}={r[0]}({r[1]}条)" for r in samples)
        except Exception:
            sample_desc = f"{count}条记录{col}<0"

        findings.append({
            "title": f"{entity_name}守恒违反: {col}为负数({count}条)",
            "description": (
                f"数据库审计发现{table}中{count}条记录的{col}字段为负数。"
                f"{sample_desc}。"
                f"数据守恒约束要求{col}(数量/金额)不能为负数。"
            ),
            "summary": f"{entity_name}.{col}为负数,违反非负守恒约束",
            "category": "conservation_violation",
            "defect_family": "inventory",
            "risk_type": "business_logic",
            "expected": f"{entity_name}.{col}必须≥0(数量/金额非负守恒)",
            "actual": f"{count}条记录{col}<0: {sample_desc}",
            "severity": "high",
            "confidence": "high",
            "reproduction": {"method": "DB_AUDIT", "path": f"/{table}"},
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
            "gate_passed": True,
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
        })
    return findings


def _check_referential_integrity(
    cur: Any,
    entity_name: str,
    table: str,
    columns: list[dict[str, str]],
    schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Check _id columns reference existing rows in parent tables."""
    findings = []
    table_set = set(schema.keys())
    for col_info in columns:
        col = col_info["column_name"]
        m = _ID_COLUMN_RE.match(col.lower())
        if not m:
            continue
        ref_base = m.group(1)  # e.g., "order" from "order_id"
        # Try to find referenced table
        ref_table = None
        for candidate in [ref_base, _pluralize(ref_base), ref_base + "es"]:
            if candidate in table_set and candidate != table:
                ref_table = candidate
                break
        if not ref_table:
            continue
        try:
            cur.execute(
                f'SELECT COUNT(*) FROM "{table}" t '
                f'WHERE t."{col}" IS NOT NULL '
                f'AND NOT EXISTS (SELECT 1 FROM "{ref_table}" p WHERE p."id" = t."{col}")'
            )
            count = cur.fetchone()[0]
        except Exception:
            continue
        if not count:
            continue
        findings.append({
            "title": f"{entity_name}引用完整性违反: {col}引用不存在的{ref_base}({count}条)",
            "description": (
                f"数据库审计发现{table}中{count}条记录的{col}"
                f"引用了不存在的{ref_base}记录。"
                f"引用完整性约束要求{col}必须指向有效的{ref_base}。"
            ),
            "summary": f"{entity_name}.{col}引用完整性违反",
            "category": "referential_integrity",
            "defect_family": "data_integrity",
            "risk_type": "data_integrity",
            "expected": f"{entity_name}.{col}必须引用有效的{ref_base}记录",
            "actual": f"{count}条记录引用不存在的{ref_base}",
            "severity": "medium",
            "confidence": "high",
            "reproduction": {"method": "DB_AUDIT", "path": f"/{table}"},
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
            "gate_passed": True,
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
        })
    return findings


def _check_cross_table_amount(
    cur: Any,
    schema: dict[str, list[dict[str, str]]],
    entity_table_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Check parent.total_amount == sum(children.line_amount) patterns."""
    findings = []
    table_set = set(schema.keys())

    for parent_table, parent_cols in schema.items():
        parent_col_names = [c["column_name"].lower() for c in parent_cols]
        total_col = None
        for tc in ("total_amount", "total", "grand_total"):
            if tc in parent_col_names:
                total_col = tc
                break
        if not total_col:
            continue

        parent_singular = parent_table.rstrip("s")
        for child_table, child_cols in schema.items():
            if child_table == parent_table:
                continue
            # Only apply SUM check to line-item tables (have qty columns)
            if not _is_line_item_table(child_table, schema):
                continue
            child_col_names = [c["column_name"].lower() for c in child_cols]
            fk_col = None
            for fc in (f"{parent_singular}_id", f"{parent_table}_id"):
                if fc in child_col_names:
                    fk_col = fc
                    break
            if not fk_col:
                continue
            line_col = None
            for lc in ("line_amount", "amount", "subtotal", "line_total"):
                if lc in child_col_names:
                    line_col = lc
                    break
            if not line_col:
                continue

            try:
                cur.execute(
                    f'SELECT COUNT(*) FROM ('
                    f'  SELECT p."id" FROM "{parent_table}" p '
                    f'  LEFT JOIN "{child_table}" c ON p."id" = c."{fk_col}" '
                    f'  GROUP BY p."id", p."{total_col}" '
                    f'  HAVING p."{total_col}" != COALESCE(SUM(c."{line_col}"), 0)'
                    f') mismatches'
                )
                count = cur.fetchone()[0]
            except Exception:
                continue
            if not count:
                continue
            findings.append({
                "title": f"金额守恒违反: {parent_table}.{total_col}≠sum({child_table}.{line_col})({count}条)",
                "description": (
                    f"数据库审计发现{count}条{parent_table}记录的{total_col}"
                    f"不等于关联{child_table}的{line_col}之和。"
                    f"金额守恒约束要求总额必须等于明细行金额之和。"
                ),
                "summary": f"{parent_table}总额与{child_table}明细金额不一致",
                "category": "conservation_violation",
                "defect_family": "inventory",
                "risk_type": "business_logic",
                "expected": f"{parent_table}.{total_col}=sum({child_table}.{line_col})",
                "actual": f"{count}条记录金额不一致",
                "severity": "high",
                "confidence": "high",
                "reproduction": {"method": "DB_AUDIT", "path": f"/{parent_table}"},
                "evidence_source": "db_state_audit",
                "observed_at": _now_iso(),
                "gate_passed": True,
                "confirmation_status": "confirmed",
                "customer_delivery_status": "defect",
            })
    return findings


# ── FK graph discovery (generic, schema-driven) ──


def _discover_fk_edges(
    schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    """Discover FK relationships from _id column naming conventions.

    Returns list of {child_table, fk_col, parent_table, parent_singular}.
    """
    table_set = set(schema.keys())
    edges: list[dict[str, str]] = []
    for child_table, child_cols in schema.items():
        for col_info in child_cols:
            col = col_info["column_name"].lower()
            m = _ID_COLUMN_RE.match(col)
            if not m:
                continue
            ref_base = m.group(1)
            # Resolve parent table
            parent_table = None
            for candidate in (ref_base, _pluralize(ref_base), ref_base + "es"):
                if candidate in table_set and candidate != child_table:
                    parent_table = candidate
                    break
            if parent_table:
                edges.append({
                    "child_table": child_table,
                    "fk_col": col,
                    "parent_table": parent_table,
                    "parent_singular": ref_base,
                })
    return edges


def _col_names(table: str, schema: dict[str, list[dict[str, str]]]) -> list[str]:
    return [c["column_name"].lower() for c in schema.get(table, [])]


_LIMIT_COL_RE = re.compile(r"(user_limit|global_limit|max_\w+|_limit$|limit$)", re.I)
_AMOUNT_COL_RE = re.compile(r"(amount|price|payable|total|cost|fee)", re.I)
# Line-item tables have qty + FK → their per-row amounts should NOT be compared
# 1:1 with parent totals (that's what _check_cross_table_amount does via SUM).
_LINE_ITEM_QTY_RE = re.compile(r"(qty|quantity|count)", re.I)


def _is_line_item_table(child: str, schema: dict[str, list[dict[str, str]]]) -> bool:
    """Detect line-item tables (have qty-like columns → per-row amounts ≠ parent totals)."""
    cols = _col_names(child, schema)
    return any(_LINE_ITEM_QTY_RE.search(c) for c in cols)


def _check_fk_amount_equality(
    cur: Any,
    fk_edges: list[dict[str, str]],
    schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """For each FK edge, check if child.amount matches parent.payable/total.

    Only compares columns with matching semantics (same base name).
    Skips line-item tables and transaction tables (with status columns,
    where partial amounts are valid — e.g., refunds, payments).
    """
    findings = []
    for edge in fk_edges:
        child, parent, fk = edge["child_table"], edge["parent_table"], edge["fk_col"]
        # Skip line-item tables: their per-row amounts != parent totals
        if _is_line_item_table(child, schema):
            continue
        # Skip transaction tables (have status → partial amounts are valid)
        child_cols_raw = _col_names(child, schema)
        if _find_status_column([{"column_name": c} for c in child_cols_raw]):
            continue
        child_cols = _col_names(child, schema)
        parent_cols = _col_names(parent, schema)

        child_amount_cols = [c for c in child_cols if _AMOUNT_COL_RE.search(c) and c != fk]
        parent_amount_cols = [c for c in parent_cols if _AMOUNT_COL_RE.search(c)]
        if not child_amount_cols or not parent_amount_cols:
            continue

        for ca in child_amount_cols:
            # Only compare with parent column of the SAME semantic name
            # e.g., payments.amount ↔ orders.payable_amount (both "amount" semantics)
            # but NOT order_items.price ↔ orders.payable_amount
            pa = None
            ca_base = ca.replace("_amount", "").replace("_", "")
            for prefer in ("payable_amount", "total_amount", "amount"):
                if prefer in parent_amount_cols:
                    pa_base = prefer.replace("_amount", "").replace("_", "")
                    # Accept if bases overlap or child is generic "amount"
                    if ca_base in pa_base or pa_base in ca_base or ca == "amount":
                        pa = prefer
                        break
            if not pa or pa == ca:
                continue

            # Check: child records where amount != parent amount (via FK join)
            try:
                cur.execute(
                    f'SELECT COUNT(*) FROM "{child}" c '
                    f'JOIN "{parent}" p ON c."{fk}" = p."id" '
                    f'WHERE c."{ca}" IS NOT NULL AND p."{pa}" IS NOT NULL '
                    f'AND c."{ca}" != p."{pa}" '
                    f'AND c."{ca}" = c."{ca}"'  # exclude NaN (NaN != NaN is false in PG numeric)
                )
                count = cur.fetchone()[0]
            except Exception:
                continue
            if not count:
                continue

            # Also check for NaN in child amount
            try:
                cur.execute(
                    f'SELECT COUNT(*) FROM "{child}" c '
                    f'JOIN "{parent}" p ON c."{fk}" = p."id" '
                    f'WHERE c."{ca}" != c."{ca}"'  # NaN detection for numeric
                )
                nan_count = cur.fetchone()[0]
            except Exception:
                nan_count = 0

            desc_parts = [f"{count}条{child}.{ca}≠{parent}.{pa}"]
            if nan_count:
                desc_parts.append(f"{nan_count}条{child}.{ca}为NaN")

            findings.append({
                "title": f"跨表金额不一致: {child}.{ca}≠{parent}.{pa}({count}条)",
                "description": (
                    f"数据库联合审计发现{count}条{child}记录的{ca}"
                    f"不等于关联{parent}的{pa}。"
                    + (f"另有{nan_count}条{ca}为NaN(非数字)。" if nan_count else "")
                    + f"跨表金额守恒约束要求{child}.{ca}必须等于{parent}.{pa}。"
                ),
                "summary": f"{child}.{ca}与{parent}.{pa}不一致",
                "category": "conservation_violation",
                "defect_family": "inventory",
                "risk_type": "business_logic",
                "expected": f"{child}.{ca}={parent}.{pa}(跨表金额守恒)",
                "actual": "; ".join(desc_parts),
                "severity": "high",
                "confidence": "high",
                "reproduction": {"method": "DB_AUDIT", "path": f"/{child} JOIN /{parent}"},
                "evidence_source": "db_state_audit",
                "observed_at": _now_iso(),
                "gate_passed": True,
                "confirmation_status": "confirmed",
                "customer_delivery_status": "defect",
            })
    return findings


def _check_fk_count_limit(
    cur: Any,
    fk_edges: list[dict[str, str]],
    schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """For each FK edge, check COUNT(child) <= parent.limit_column.

    Generic pattern: coupon_usage count vs coupons.user_limit/global_limit.
    """
    findings = []
    for edge in fk_edges:
        child, parent, fk = edge["child_table"], edge["parent_table"], edge["fk_col"]
        parent_cols = _col_names(parent, schema)

        # Find limit-like columns in parent
        limit_cols = [c for c in parent_cols if _LIMIT_COL_RE.search(c)]
        if not limit_cols:
            continue

        for lc in limit_cols:
            try:
                cur.execute(
                    f'SELECT p."id", p."{lc}", COUNT(c."id") as usage_count '
                    f'FROM "{parent}" p '
                    f'JOIN "{child}" c ON c."{fk}" = p."id" '
                    f'WHERE p."{lc}" IS NOT NULL AND p."{lc}" > 0 '
                    f'GROUP BY p."id", p."{lc}" '
                    f'HAVING COUNT(c."id") > p."{lc}" '
                    f'LIMIT 5'
                )
                rows = cur.fetchall()
            except Exception:
                continue
            if not rows:
                continue

            sample = rows[0]
            findings.append({
                "title": f"跨表限制违反: {child}使用次数超过{parent}.{lc}({len(rows)}+条)",
                "description": (
                    f"数据库联合审计发现{child}关联{parent}的记录数"
                    f"超过{parent}.{lc}限制。"
                    f"示例: {parent}_id={sample[0]},{lc}={sample[1]},实际使用{sample[2]}次。"
                    f"跨表业务规则约束要求{child}使用次数不能超过{parent}.{lc}。"
                ),
                "summary": f"{child}使用次数超过{parent}.{lc}限制",
                "category": "business_rule_violation",
                "defect_family": "coupon",
                "risk_type": "business_logic",
                "expected": f"COUNT({child})<={parent}.{lc}(跨表次数限制)",
                "actual": f"{len(rows)}+条{parent}的{child}使用次数超限",
                "severity": "medium",
                "confidence": "high",
                "reproduction": {"method": "DB_AUDIT", "path": f"/{child} JOIN /{parent}"},
                "evidence_source": "db_state_audit",
                "observed_at": _now_iso(),
                "gate_passed": True,
                "confirmation_status": "confirmed",
                "customer_delivery_status": "defect",
            })
    return findings


def _check_fk_state_existence(
    cur: Any,
    fk_edges: list[dict[str, str]],
    schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """For each FK edge where both tables have status, check state-dependent existence.

    Generic patterns:
    - Parent in 'active' state but has NO child records → missing child
    - Parent in 'terminal' state but child still 'active' → stale child
    """
    findings = []
    # Terminal/active state heuristics (generic, not project-specific)
    terminal_states = {"CANCELLED", "COMPLETED", "REFUNDED", "CLOSED", "TERMINATED", "EXPIRED", "REJECTED"}
    active_child_states = {"ACTIVE", "PENDING", "LOCKED", "PROCESSING", "IN_PROGRESS", "OPEN"}

    for edge in fk_edges:
        child, parent, fk = edge["child_table"], edge["parent_table"], edge["fk_col"]
        parent_cols = _col_names(parent, schema)
        child_cols = _col_names(child, schema)

        parent_status = _find_status_column(
            [{"column_name": c} for c in parent_cols]
        )
        child_status = _find_status_column(
            [{"column_name": c} for c in child_cols]
        )
        if not parent_status:
            continue

        # Pattern 1: Parent in post-action state but NO child records exist
        # Only flag when the VAST MAJORITY (>80%) of records in that state
        # DO have children — the missing few are anomalies, not the norm.
        try:
            cur.execute(
                f'SELECT p."{parent_status}", COUNT(DISTINCT p."id") as total, '
                f'COUNT(DISTINCT CASE WHEN c."id" IS NOT NULL THEN p."id" END) as with_child '
                f'FROM "{parent}" p '
                f'LEFT JOIN "{child}" c ON c."{fk}" = p."id" '
                f'GROUP BY p."{parent_status}" '
                f'HAVING COUNT(DISTINCT p."id") > 0 '
                f'AND COUNT(DISTINCT CASE WHEN c."id" IS NOT NULL THEN p."id" END) '
                f'< COUNT(DISTINCT p."id")'
            )
            gaps = cur.fetchall()
        except Exception:
            gaps = []

        for gap in gaps:
            p_state = str(gap[0]).upper()
            total = int(gap[1])
            with_child = int(gap[2])
            missing = total - with_child
            if missing <= 0 or total < 2:
                continue
            # Only flag when >80% have children (missing are true anomalies)
            if with_child / total >= 0.8 and missing > 0:
                findings.append({
                    "title": f"跨表状态一致性: {parent}状态{gap[0]}有{missing}条缺少{child}记录",
                    "description": (
                        f"数据库联合审计发现{parent}中状态为{gap[0]}的{total}条记录中,"
                        f"{missing}条没有关联的{child}记录(其余{with_child}条有)。"
                        f"跨表状态一致性约束要求该状态的{parent}应有对应{child}记录。"
                    ),
                    "summary": f"{parent}状态{gap[0]}缺少{child}关联记录",
                    "category": "state_consistency",
                    "defect_family": "state_transition",
                    "risk_type": "business_logic",
                    "expected": f"{parent}.{parent_status}={gap[0]}时应有{child}记录",
                    "actual": f"{missing}/{total}条{parent}({gap[0]})缺少{child}",
                    "severity": "high",
                    "confidence": "medium",
                    "reproduction": {"method": "DB_AUDIT", "path": f"/{parent} LEFT JOIN /{child}"},
                    "evidence_source": "db_state_audit",
                    "observed_at": _now_iso(),
                    "gate_passed": True,
                    "confirmation_status": "confirmed",
                    "customer_delivery_status": "defect",
                })

        # Pattern 2: Parent in terminal state but child still active
        if not child_status:
            continue
        try:
            terminal_list = list(terminal_states)
            active_list = list(active_child_states)
            t_ph = ",".join(["%s"] * len(terminal_list))
            a_ph = ",".join(["%s"] * len(active_list))
            cur.execute(
                f'SELECT COUNT(*) FROM "{child}" c '
                f'JOIN "{parent}" p ON c."{fk}" = p."id" '
                f'WHERE UPPER(p."{parent_status}") IN ({t_ph}) '
                f'AND UPPER(c."{child_status}") IN ({a_ph})',
                terminal_list + active_list,
            )
            stale_count = cur.fetchone()[0]
        except Exception:
            stale_count = 0
        if stale_count:
            findings.append({
                "title": f"跨表状态矛盾: {parent}已终态但{child}仍活跃({stale_count}条)",
                "description": (
                    f"数据库联合审计发现{stale_count}条{child}记录仍处于活跃状态,"
                    f"但关联的{parent}已处于终态(如CANCELLED/COMPLETED/REFUNDED)。"
                    f"跨表状态一致性约束要求{parent}终态时{child}不应仍活跃。"
                ),
                "summary": f"{parent}终态但{child}仍活跃,状态矛盾",
                "category": "state_consistency",
                "defect_family": "state_transition",
                "risk_type": "business_logic",
                "expected": f"{parent}终态时{child}不应处于活跃状态",
                "actual": f"{stale_count}条{child}在{parent}终态时仍活跃",
                "severity": "medium",
                "confidence": "medium",
                "reproduction": {"method": "DB_AUDIT", "path": f"/{child} JOIN /{parent}"},
                "evidence_source": "db_state_audit",
                "observed_at": _now_iso(),
                "gate_passed": True,
                "confirmation_status": "confirmed",
                "customer_delivery_status": "defect",
            })
    return findings


def _check_fk_amount_bound(
    cur: Any,
    fk_edges: list[dict[str, str]],
    schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """For each FK edge, check child.amount <= parent.amount (upper bound).

    Only for non-line-item tables with matching amount semantics.
    Generic pattern: refund.amount <= order.payable_amount.
    """
    findings = []
    for edge in fk_edges:
        child, parent, fk = edge["child_table"], edge["parent_table"], edge["fk_col"]
        if _is_line_item_table(child, schema):
            continue
        child_cols = _col_names(child, schema)
        parent_cols = _col_names(parent, schema)

        child_amount_cols = [c for c in child_cols if _AMOUNT_COL_RE.search(c) and c != fk]
        parent_amount_cols = [c for c in parent_cols if _AMOUNT_COL_RE.search(c)]
        if not child_amount_cols or not parent_amount_cols:
            continue

        for ca in child_amount_cols:
            pa = None
            ca_base = ca.replace("_amount", "").replace("_", "")
            for prefer in ("payable_amount", "total_amount", "amount"):
                if prefer in parent_amount_cols:
                    pa_base = prefer.replace("_amount", "").replace("_", "")
                    if ca_base in pa_base or pa_base in ca_base or ca == "amount":
                        pa = prefer
                        break
            if not pa or pa == ca:
                continue
            try:
                cur.execute(
                    f'SELECT COUNT(*) FROM "{child}" c '
                    f'JOIN "{parent}" p ON c."{fk}" = p."id" '
                    f'WHERE c."{ca}" > p."{pa}" '
                    f'AND c."{ca}" = c."{ca}" AND p."{pa}" = p."{pa}"'
                )
                count = cur.fetchone()[0]
            except Exception:
                continue
            if not count:
                continue
            findings.append({
                "title": f"跨表金额越界: {child}.{ca}>{parent}.{pa}({count}条)",
                "description": (
                    f"数据库联合审计发现{count}条{child}记录的{ca}"
                    f"超过关联{parent}的{pa}上限。"
                    f"跨表金额约束要求{child}.{ca}不能超过{parent}.{pa}。"
                ),
                "summary": f"{child}.{ca}超过{parent}.{pa}上限",
                "category": "conservation_violation",
                "defect_family": "inventory",
                "risk_type": "business_logic",
                "expected": f"{child}.{ca}<={parent}.{pa}(跨表金额上限)",
                "actual": f"{count}条{child}.{ca}>{parent}.{pa}",
                "severity": "high",
                "confidence": "high",
                "reproduction": {"method": "DB_AUDIT", "path": f"/{child} JOIN /{parent}"},
                "evidence_source": "db_state_audit",
                "observed_at": _now_iso(),
                "gate_passed": True,
                "confirmation_status": "confirmed",
                "customer_delivery_status": "defect",
            })
    return findings


# ── Cross-database checks (application-level JOIN) ──


def _discover_cross_db_fk_edges(
    sources: list[DataSource],
    merged_schema: dict[str, list[dict[str, str]]],
    table_source_map: dict[str, int],
) -> list[dict[str, Any]]:
    """Discover FK relationships that span multiple databases.

    Uses column-name + type matching: if table A in DB1 has 'order_id' (integer)
    and table 'orders' with 'id' (integer) exists in DB2, that's a cross-DB FK.

    Returns list of {child_table, fk_col, parent_table, child_src, parent_src}.
    """
    edges: list[dict[str, Any]] = []
    table_set = set(merged_schema.keys())

    for child_table, child_cols in merged_schema.items():
        child_src = table_source_map.get(child_table, 0)
        for col_info in child_cols:
            col = col_info["column_name"].lower()
            m = _ID_COLUMN_RE.match(col)
            if not m:
                continue
            ref_base = m.group(1)
            # Find parent table in a DIFFERENT database
            for candidate in (ref_base, _pluralize(ref_base), ref_base + "es"):
                if candidate not in table_set or candidate == child_table:
                    continue
                parent_src = table_source_map.get(candidate, 0)
                if parent_src == child_src:
                    continue  # same DB — handled by intra-DB checks
                # Verify type compatibility (id column should be same type)
                parent_cols = merged_schema.get(candidate, [])
                parent_id_type = ""
                for pc in parent_cols:
                    if pc["column_name"].lower() == "id":
                        parent_id_type = pc["data_type"]
                        break
                if parent_id_type and _is_numeric_type(col_info["data_type"]) and _is_numeric_type(parent_id_type):
                    edges.append({
                        "child_table": child_table,
                        "fk_col": col,
                        "parent_table": candidate,
                        "child_src": child_src,
                        "parent_src": parent_src,
                    })
                    break
    return edges


def _check_cross_db_referential(
    sources: list[DataSource],
    cross_edges: list[dict[str, Any]],
    merged_schema: dict[str, list[dict[str, str]]],
    batch_size: int = 5000,
) -> list[dict[str, Any]]:
    """Check referential integrity across databases via application-level JOIN.

    Fetches FK values from child DB, then checks existence in parent DB.
    Uses batched queries to avoid memory issues on large tables.
    """
    findings = []
    for edge in cross_edges:
        child_table = edge["child_table"]
        fk_col = edge["fk_col"]
        parent_table = edge["parent_table"]
        child_src_idx = edge["child_src"]
        parent_src_idx = edge["parent_src"]

        child_ds = sources[child_src_idx]
        parent_ds = sources[parent_src_idx]
        if not child_ds.conn or not parent_ds.conn:
            continue

        # Resolve physical tables for sharded child
        child_shards = child_ds.logical_tables.get(child_table, [child_table])
        # Resolve physical tables for sharded parent
        parent_shards = parent_ds.logical_tables.get(parent_table, [parent_table])

        try:
            child_cur = child_ds.conn.cursor()
            parent_cur = parent_ds.conn.cursor()

            # Fetch all non-null FK values from child (batched)
            if len(child_shards) == 1:
                child_cur.execute(
                    f'SELECT DISTINCT "{fk_col}" FROM "{child_shards[0]}" '
                    f'WHERE "{fk_col}" IS NOT NULL'
                )
            else:
                union = " UNION ".join(
                    f'SELECT DISTINCT "{fk_col}" FROM "{t}" WHERE "{fk_col}" IS NOT NULL'
                    for t in child_shards
                )
                child_cur.execute(f'SELECT DISTINCT "{fk_col}" FROM ({union}) AS _u')

            fk_values = [row[0] for row in child_cur.fetchall()]
            if not fk_values:
                continue

            # Check existence in parent DB (batched)
            missing_count = 0
            for i in range(0, len(fk_values), batch_size):
                batch = fk_values[i:i + batch_size]
                placeholders = ",".join(["%s"] * len(batch))
                if len(parent_shards) == 1:
                    parent_cur.execute(
                        f'SELECT COUNT(DISTINCT "id") FROM "{parent_shards[0]}" '
                        f'WHERE "id" IN ({placeholders})',
                        batch,
                    )
                else:
                    union = " UNION ALL ".join(
                        f'SELECT "id" FROM "{t}"' for t in parent_shards
                    )
                    parent_cur.execute(
                        f'SELECT COUNT(DISTINCT "id") FROM ({union}) AS _p '
                        f'WHERE "id" IN ({placeholders})',
                        batch,
                    )
                found = int(parent_cur.fetchone()[0])
                missing_count += len(batch) - found

            if missing_count > 0:
                findings.append({
                    "title": f"跨库引用完整性违反: {child_table}.{fk_col}引用不存在的{parent_table}({missing_count}条)",
                    "description": (
                        f"跨库数据库审计发现{child_table}(库:{child_ds.module})中{missing_count}个"
                        f"{fk_col}值在{parent_table}(库:{parent_ds.module})中不存在。"
                        f"跨库引用完整性约束要求{fk_col}必须指向有效的{parent_table}记录。"
                    ),
                    "summary": f"{child_table}.{fk_col}跨库引用完整性违反",
                    "category": "referential_integrity",
                    "defect_family": "data_integrity",
                    "risk_type": "data_integrity",
                    "expected": f"{child_table}.{fk_col}必须在{parent_table}(库:{parent_ds.module})中存在",
                    "actual": f"{missing_count}个{fk_col}值在目标库中无对应记录",
                    "severity": "high",
                    "confidence": "high",
                    "reproduction": {"method": "DB_AUDIT", "path": f"/{child_table} → /{parent_table}(cross-db)"},
                    "evidence_source": "db_state_audit",
                    "observed_at": _now_iso(),
                    "gate_passed": True,
                    "confirmation_status": "confirmed",
                    "customer_delivery_status": "defect",
                })
            child_cur.close()
            parent_cur.close()
        except Exception as exc:
            logger.debug("Cross-DB referential check failed for %s→%s: %s", child_table, parent_table, exc)
            continue
    return findings


def _check_cross_db_amount_consistency(
    sources: list[DataSource],
    cross_edges: list[dict[str, Any]],
    merged_schema: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Check amount consistency across databases (application-level JOIN).

    For cross-DB FK edges where both sides have amount columns,
    fetch and compare in Python.
    """
    findings = []
    for edge in cross_edges:
        child_table = edge["child_table"]
        fk_col = edge["fk_col"]
        parent_table = edge["parent_table"]
        child_src_idx = edge["child_src"]
        parent_src_idx = edge["parent_src"]

        child_ds = sources[child_src_idx]
        parent_ds = sources[parent_src_idx]
        if not child_ds.conn or not parent_ds.conn:
            continue

        child_cols = [c["column_name"].lower() for c in merged_schema.get(child_table, [])]
        parent_cols = [c["column_name"].lower() for c in merged_schema.get(parent_table, [])]

        # Skip line-item or transaction tables
        if any(_LINE_ITEM_QTY_RE.search(c) for c in child_cols):
            continue
        if _find_status_column([{"column_name": c} for c in child_cols]):
            continue

        child_amount_cols = [c for c in child_cols if _AMOUNT_COL_RE.search(c) and c != fk_col]
        parent_amount_cols = [c for c in parent_cols if _AMOUNT_COL_RE.search(c)]
        if not child_amount_cols or not parent_amount_cols:
            continue

        # Find semantically matching pair
        ca, pa = None, None
        for c_cand in child_amount_cols:
            c_base = c_cand.replace("_amount", "").replace("_", "")
            for p_cand in ("payable_amount", "total_amount", "amount"):
                if p_cand in parent_amount_cols:
                    p_base = p_cand.replace("_amount", "").replace("_", "")
                    if c_base in p_base or p_base in c_base or c_cand == "amount":
                        ca, pa = c_cand, p_cand
                        break
            if ca:
                break
        if not ca or not pa:
            continue

        try:
            child_cur = child_ds.conn.cursor()
            parent_cur = parent_ds.conn.cursor()

            # Fetch child: {fk_value: amount}
            child_shards = child_ds.logical_tables.get(child_table, [child_table])
            if len(child_shards) == 1:
                child_cur.execute(
                    f'SELECT "{fk_col}", "{ca}" FROM "{child_shards[0]}" '
                    f'WHERE "{fk_col}" IS NOT NULL AND "{ca}" IS NOT NULL'
                )
            else:
                union = " UNION ALL ".join(
                    f'SELECT "{fk_col}", "{ca}" FROM "{t}" '
                    f'WHERE "{fk_col}" IS NOT NULL AND "{ca}" IS NOT NULL'
                    for t in child_shards
                )
                child_cur.execute(f"SELECT * FROM ({union}) AS _c")
            child_rows = {row[0]: row[1] for row in child_cur.fetchall()}

            # Fetch parent: {id: amount}
            parent_shards = parent_ds.logical_tables.get(parent_table, [parent_table])
            if len(parent_shards) == 1:
                parent_cur.execute(
                    f'SELECT "id", "{pa}" FROM "{parent_shards[0]}" WHERE "id" IS NOT NULL'
                )
            else:
                union = " UNION ALL ".join(
                    f'SELECT "id", "{pa}" FROM "{t}" WHERE "id" IS NOT NULL'
                    for t in parent_shards
                )
                parent_cur.execute(f"SELECT * FROM ({union}) AS _p")
            parent_rows = {row[0]: row[1] for row in parent_cur.fetchall()}

            # Compare in Python
            mismatch_count = 0
            for fk_val, child_amt in child_rows.items():
                parent_amt = parent_rows.get(fk_val)
                if parent_amt is None:
                    continue  # referential issue handled elsewhere
                try:
                    if float(child_amt) != float(parent_amt):
                        mismatch_count += 1
                except (TypeError, ValueError):
                    mismatch_count += 1

            if mismatch_count > 0:
                findings.append({
                    "title": f"跨库金额不一致: {child_table}.{ca}≠{parent_table}.{pa}({mismatch_count}条)",
                    "description": (
                        f"跨库数据库审计发现{mismatch_count}条{child_table}(库:{child_ds.module})"
                        f"的{ca}不等于{parent_table}(库:{parent_ds.module})的{pa}。"
                        f"跨库金额守恒约束要求两者必须一致。"
                    ),
                    "summary": f"{child_table}.{ca}与{parent_table}.{pa}跨库不一致",
                    "category": "conservation_violation",
                    "defect_family": "inventory",
                    "risk_type": "business_logic",
                    "expected": f"{child_table}.{ca}={parent_table}.{pa}(跨库金额守恒)",
                    "actual": f"{mismatch_count}条记录跨库金额不一致",
                    "severity": "high",
                    "confidence": "high",
                    "reproduction": {"method": "DB_AUDIT", "path": f"/{child_table} → /{parent_table}(cross-db)"},
                    "evidence_source": "db_state_audit",
                    "observed_at": _now_iso(),
                    "gate_passed": True,
                    "confirmation_status": "confirmed",
                    "customer_delivery_status": "defect",
                })
            child_cur.close()
            parent_cur.close()
        except Exception as exc:
            logger.debug("Cross-DB amount check failed for %s→%s: %s", child_table, parent_table, exc)
    return findings


# ── Main entry point ──


def run_db_state_audit(
    behavior_ir: dict[str, Any],
    dsn: "str | list[str] | dict[str, str]",
    *,
    max_findings: int = 50,
) -> list[dict[str, Any]]:
    """Run IR-driven DB state audit and return findings.

    Supports enterprise database topologies:
    - Single database: dsn="postgresql://..."
    - Multiple databases (分库): dsn=["postgresql://order_db", "postgresql://pay_db"]
    - Module-named databases: dsn={"order": "postgresql://order_db", "payment": "..."}
    - Sharded tables (分表): auto-detected (orders_0001, orders_0002 → logical 'orders')

    Args:
        behavior_ir: The Behavior IR dict from the scan pipeline.
        dsn: Database connection string(s). Supports str, list[str], or dict[str,str].
        max_findings: Cap on total findings returned.

    Returns:
        List of finding dicts in QualiBug format.
    """
    if not dsn or not behavior_ir:
        return []

    # Normalize DSN config to list of DataSource
    sources = _normalize_dsn_config(dsn)
    if not sources:
        return []

    # Connect all data sources
    connected: list[DataSource] = []
    for ds in sources:
        if _connect_data_source(ds):
            connected.append(ds)
        else:
            logger.warning("Skipping unreachable data source: %s", ds.module or ds.dsn[:40])

    if not connected:
        return []

    try:
        findings: list[dict[str, Any]] = []

        # ── Phase A: Per-database intra-DB checks (shard-aware) ──
        for ds in connected:
            findings.extend(
                _run_intra_db_checks(ds, behavior_ir)
            )

        # ── Phase B: Cross-database checks (if multiple DBs connected) ──
        if len(connected) > 1:
            merged_schema, table_source_map = _merged_schema_from_sources(connected)
            cross_edges = _discover_cross_db_fk_edges(connected, merged_schema, table_source_map)
            if cross_edges:
                logger.info("Discovered %d cross-DB FK edges", len(cross_edges))
                findings.extend(
                    _check_cross_db_referential(connected, cross_edges, merged_schema)
                )
                findings.extend(
                    _check_cross_db_amount_consistency(connected, cross_edges, merged_schema)
                )

        return findings[:max_findings]

    finally:
        for ds in connected:
            _close_data_source(ds)


def _run_intra_db_checks(
    ds: DataSource,
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run all intra-database checks on a single DataSource (shard-aware).

    This handles:
    - Sharded tables: queries use UNION ALL across physical shards
    - Standard checks: state enumeration, non-negative, referential integrity
    - FK-driven cross-table checks within the same database
    """
    if not ds.conn or not ds.schema:
        return []

    cur = ds.conn.cursor()
    # Build logical schema (shards merged)
    logical_schema, shard_map = _build_logical_schema(ds.schema)
    if not logical_schema:
        return []

    entities = behavior_ir.get("entities") or []
    states = behavior_ir.get("states") or []
    entity_table_map = _map_entities_to_tables(entities, logical_schema)

    # Build entity→valid_states from IR
    entity_valid_states: dict[str, list[str]] = {}
    for s in states:
        ent = str(s.get("entity_ref") or "").strip().lower()
        name = str(s.get("name") or "").strip()
        if ent and name:
            entity_valid_states.setdefault(ent, []).append(name)

    findings: list[dict[str, Any]] = []
    module_prefix = f"[{ds.module}]" if ds.module and ds.module != "default" else ""

    for entity_name, table in entity_table_map.items():
        if table not in logical_schema:
            continue
        columns = logical_schema[table]
        # Resolve physical tables (shards)
        physical_tables = shard_map.get(table, [table])

        # 1. State enumeration check (shard-aware)
        valid = entity_valid_states.get(entity_name, [])
        if valid:
            findings.extend(
                _check_state_enumeration_sharded(
                    cur, entity_name, table, valid, columns, physical_tables, module_prefix
                )
            )

        # 2. Non-negative check (shard-aware)
        findings.extend(
            _check_non_negative_sharded(
                cur, entity_name, table, columns, physical_tables, module_prefix
            )
        )

        # 3. Referential integrity check (shard-aware)
        findings.extend(
            _check_referential_integrity_sharded(
                cur, entity_name, table, columns, logical_schema, shard_map, module_prefix
            )
        )

    # 4. Cross-table amount consistency (shard-aware)
    findings.extend(
        _check_cross_table_amount_sharded(cur, logical_schema, shard_map, entity_table_map, module_prefix)
    )

    # 5. FK-driven cross-table checks (shard-aware)
    fk_edges = _discover_fk_edges(logical_schema)
    if fk_edges:
        findings.extend(
            _check_fk_checks_sharded(cur, fk_edges, logical_schema, shard_map, module_prefix)
        )

    cur.close()
    return findings


# ── Shard-aware check wrappers ──


def _table_or_union(
    table: str,
    shard_map: dict[str, list[str]],
    alias: str = "",
) -> str:
    """Return SQL fragment: either '"table"' or '(SELECT * FROM t1 UNION ALL ...) AS alias'."""
    physical = shard_map.get(table, [table])
    if len(physical) == 1:
        return f'"{physical[0]}"' + (f' AS {alias}' if alias else "")
    union = " UNION ALL ".join(f'SELECT * FROM "{t}"' for t in physical)
    a = alias or "_sharded"
    return f'({union}) AS {a}'


def _check_state_enumeration_sharded(
    cur: Any,
    entity_name: str,
    table: str,
    valid_states: list[str],
    columns: list[dict[str, str]],
    physical_tables: list[str],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Shard-aware state enumeration check."""
    status_col = _find_status_column(columns)
    if not status_col or not valid_states:
        return []

    upper_states = sorted({s.upper() for s in valid_states if s})
    if not upper_states:
        return []

    placeholders = ",".join(["%s"] * len(upper_states))
    where = f'UPPER("{status_col}") NOT IN ({placeholders})'
    count = _shard_aware_count(cur, physical_tables, where, upper_states)
    if not count:
        return []

    samples = _shard_aware_group_count(
        cur, physical_tables, status_col,
        where_clause=where, params=upper_states, limit=5,
    )
    sample_desc = "; ".join(f"{str(r[0])}({r[1]}条)" for r in samples) if samples else f"{count}条记录"

    return [{
        "title": f"{module_prefix}{entity_name}状态枚举违反: {count}条记录状态不在合法集合中",
        "description": (
            f"数据库审计发现{table}中{count}条记录的{status_col}值"
            f"不在Behavior IR声明的合法状态集合{upper_states}中。"
            f"违反状态: {sample_desc}。"
            f"状态机约束要求{entity_name}只能处于已声明的合法状态。"
            + (f"[分表: {len(physical_tables)}个分片]" if len(physical_tables) > 1 else "")
        ),
        "summary": f"{entity_name}存在非法状态值,违反状态机约束",
        "category": "state_machine_violation",
        "defect_family": "state_transition",
        "risk_type": "business_logic",
        "expected": f"{entity_name}.{status_col}必须在合法状态集合{upper_states}中",
        "actual": f"{count}条记录状态非法: {sample_desc}",
        "severity": "high",
        "confidence": "high",
        "reproduction": {"method": "DB_AUDIT", "path": f"/{table}"},
        "evidence_source": "db_state_audit",
        "observed_at": _now_iso(),
        # Evaluator gate fields: DB-observed violations are confirmed defects
        "gate_passed": True,
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
    }]


def _check_non_negative_sharded(
    cur: Any,
    entity_name: str,
    table: str,
    columns: list[dict[str, str]],
    physical_tables: list[str],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Shard-aware non-negative check."""
    findings = []
    for col_info in columns:
        col = col_info["column_name"]
        dtype = col_info["data_type"]
        if not _is_numeric_type(dtype):
            continue
        if not _NON_NEGATIVE_PATTERNS.search(col):
            continue
        if any(skip in col.lower() for skip in ("adjust", "delta", "change", "diff")):
            continue

        count = _shard_aware_count(cur, physical_tables, f'"{col}" < 0')
        if not count:
            continue

        samples = _shard_aware_group_count(
            cur, physical_tables, col,
            where_clause=f'"{col}" < 0', limit=3,
        )
        sample_desc = "; ".join(f"{col}={r[0]}({r[1]}条)" for r in samples) if samples else f"{count}条记录{col}<0"

        findings.append({
            "title": f"{module_prefix}{entity_name}守恒违反: {col}为负数({count}条)",
            "description": (
                f"数据库审计发现{table}中{count}条记录的{col}字段为负数。"
                f"{sample_desc}。"
                f"数据守恒约束要求{col}(数量/金额)不能为负数。"
                + (f"[分表: {len(physical_tables)}个分片]" if len(physical_tables) > 1 else "")
            ),
            "summary": f"{entity_name}.{col}为负数,违反非负守恒约束",
            "category": "conservation_violation",
            "defect_family": "inventory",
            "risk_type": "business_logic",
            "expected": f"{entity_name}.{col}必须≥0(数量/金额非负守恒)",
            "actual": f"{count}条记录{col}<0: {sample_desc}",
            "severity": "high",
            "confidence": "high",
            "reproduction": {"method": "DB_AUDIT", "path": f"/{table}"},
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
            "gate_passed": True,
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
        })
    return findings


def _check_referential_integrity_sharded(
    cur: Any,
    entity_name: str,
    table: str,
    columns: list[dict[str, str]],
    logical_schema: dict[str, list[dict[str, str]]],
    shard_map: dict[str, list[str]],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Shard-aware referential integrity check."""
    findings = []
    table_set = set(logical_schema.keys())
    for col_info in columns:
        col = col_info["column_name"]
        m = _ID_COLUMN_RE.match(col.lower())
        if not m:
            continue
        ref_base = m.group(1)
        ref_table = None
        for candidate in [ref_base, _pluralize(ref_base), ref_base + "es"]:
            if candidate in table_set and candidate != table:
                ref_table = candidate
                break
        if not ref_table:
            continue

        # Build shard-aware SQL
        child_physical = shard_map.get(table, [table])
        parent_physical = shard_map.get(ref_table, [ref_table])
        child_src = _table_or_union(table, shard_map, "t")
        parent_src = _table_or_union(ref_table, shard_map, "p")

        try:
            sql = (
                f'SELECT COUNT(*) FROM {child_src} '
                f'WHERE t."{col}" IS NOT NULL '
                f'AND NOT EXISTS (SELECT 1 FROM {parent_src} WHERE p."id" = t."{col}")'
            )
            cur.execute(sql)
            count = cur.fetchone()[0]
        except Exception:
            continue
        if not count:
            continue
        findings.append({
            "title": f"{module_prefix}{entity_name}引用完整性违反: {col}引用不存在的{ref_base}({count}条)",
            "description": (
                f"数据库审计发现{table}中{count}条记录的{col}"
                f"引用了不存在的{ref_base}记录。"
                f"引用完整性约束要求{col}必须指向有效的{ref_base}。"
            ),
            "summary": f"{entity_name}.{col}引用完整性违反",
            "category": "referential_integrity",
            "defect_family": "data_integrity",
            "risk_type": "data_integrity",
            "expected": f"{entity_name}.{col}必须引用有效的{ref_base}记录",
            "actual": f"{count}条记录引用不存在的{ref_base}",
            "severity": "medium",
            "confidence": "high",
            "reproduction": {"method": "DB_AUDIT", "path": f"/{table}"},
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
            "gate_passed": True,
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
        })
    return findings


def _check_cross_table_amount_sharded(
    cur: Any,
    logical_schema: dict[str, list[dict[str, str]]],
    shard_map: dict[str, list[str]],
    entity_table_map: dict[str, str],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Shard-aware cross-table amount consistency check."""
    findings = []
    for parent_table, parent_cols in logical_schema.items():
        parent_col_names = [c["column_name"].lower() for c in parent_cols]
        total_col = None
        for tc in ("total_amount", "total", "grand_total"):
            if tc in parent_col_names:
                total_col = tc
                break
        if not total_col:
            continue

        parent_singular = parent_table.rstrip("s")
        for child_table, child_cols in logical_schema.items():
            if child_table == parent_table:
                continue
            if not _is_line_item_table(child_table, logical_schema):
                continue
            child_col_names = [c["column_name"].lower() for c in child_cols]
            fk_col = None
            for fc in (f"{parent_singular}_id", f"{parent_table}_id"):
                if fc in child_col_names:
                    fk_col = fc
                    break
            if not fk_col:
                continue
            line_col = None
            for lc in ("line_amount", "amount", "subtotal", "line_total"):
                if lc in child_col_names:
                    line_col = lc
                    break
            if not line_col:
                continue

            # Shard-aware JOIN
            p_src = _table_or_union(parent_table, shard_map, "p")
            c_src = _table_or_union(child_table, shard_map, "c")
            try:
                cur.execute(
                    f'SELECT COUNT(*) FROM ('
                    f'  SELECT p."id" FROM {p_src} '
                    f'  LEFT JOIN {c_src} ON p."id" = c."{fk_col}" '
                    f'  GROUP BY p."id", p."{total_col}" '
                    f'  HAVING p."{total_col}" != COALESCE(SUM(c."{line_col}"), 0)'
                    f') mismatches'
                )
                count = cur.fetchone()[0]
            except Exception:
                continue
            if not count:
                continue
            findings.append({
                "title": f"{module_prefix}金额守恒违反: {parent_table}.{total_col}≠sum({child_table}.{line_col})({count}条)",
                "description": (
                    f"数据库审计发现{count}条{parent_table}记录的{total_col}"
                    f"不等于关联{child_table}的{line_col}之和。"
                    f"金额守恒约束要求总额必须等于明细行金额之和。"
                ),
                "summary": f"{parent_table}总额与{child_table}明细金额不一致",
                "category": "conservation_violation",
                "defect_family": "inventory",
                "risk_type": "business_logic",
                "expected": f"{parent_table}.{total_col}=sum({child_table}.{line_col})",
                "actual": f"{count}条记录金额不一致",
                "severity": "high",
                "confidence": "high",
                "reproduction": {"method": "DB_AUDIT", "path": f"/{parent_table}"},
                "evidence_source": "db_state_audit",
                "observed_at": _now_iso(),
                "gate_passed": True,
                "confirmation_status": "confirmed",
                "customer_delivery_status": "defect",
            })
    return findings


def _check_fk_checks_sharded(
    cur: Any,
    fk_edges: list[dict[str, str]],
    logical_schema: dict[str, list[dict[str, str]]],
    shard_map: dict[str, list[str]],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Run all FK-driven checks with shard awareness.

    Delegates to original check functions but replaces table references
    with shard-aware subqueries where needed.
    """
    # For FK checks, if no shards involved, use original fast path
    has_shards = bool(shard_map)
    if not has_shards:
        findings = []
        findings.extend(_check_fk_amount_equality(cur, fk_edges, logical_schema))
        findings.extend(_check_fk_count_limit(cur, fk_edges, logical_schema))
        findings.extend(_check_fk_state_existence(cur, fk_edges, logical_schema))
        findings.extend(_check_fk_amount_bound(cur, fk_edges, logical_schema))
        return findings

    # With shards: filter edges to only those involving sharded tables
    # and run with UNION ALL subqueries
    findings = []
    for edge in fk_edges:
        child, parent = edge["child_table"], edge["parent_table"]
        child_shards = shard_map.get(child, [child])
        parent_shards = shard_map.get(parent, [parent])
        is_sharded_edge = len(child_shards) > 1 or len(parent_shards) > 1

        if not is_sharded_edge:
            # Non-sharded edge: use original single-table logic
            findings.extend(_check_fk_amount_equality(cur, [edge], logical_schema))
            findings.extend(_check_fk_count_limit(cur, [edge], logical_schema))
            findings.extend(_check_fk_state_existence(cur, [edge], logical_schema))
            findings.extend(_check_fk_amount_bound(cur, [edge], logical_schema))
        else:
            # Sharded edge: use UNION ALL approach
            findings.extend(
                _check_fk_amount_equality_sharded(cur, edge, logical_schema, shard_map, module_prefix)
            )
            findings.extend(
                _check_fk_count_limit_sharded(cur, edge, logical_schema, shard_map, module_prefix)
            )
    return findings


def _check_fk_amount_equality_sharded(
    cur: Any,
    edge: dict[str, str],
    schema: dict[str, list[dict[str, str]]],
    shard_map: dict[str, list[str]],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Shard-aware FK amount equality check."""
    child, parent, fk = edge["child_table"], edge["parent_table"], edge["fk_col"]
    if _is_line_item_table(child, schema):
        return []
    child_cols_raw = _col_names(child, schema)
    if _find_status_column([{"column_name": c} for c in child_cols_raw]):
        return []

    child_cols = _col_names(child, schema)
    parent_cols = _col_names(parent, schema)
    child_amount_cols = [c for c in child_cols if _AMOUNT_COL_RE.search(c) and c != fk]
    parent_amount_cols = [c for c in parent_cols if _AMOUNT_COL_RE.search(c)]
    if not child_amount_cols or not parent_amount_cols:
        return []

    findings = []
    for ca in child_amount_cols:
        pa = None
        ca_base = ca.replace("_amount", "").replace("_", "")
        for prefer in ("payable_amount", "total_amount", "amount"):
            if prefer in parent_amount_cols:
                pa_base = prefer.replace("_amount", "").replace("_", "")
                if ca_base in pa_base or pa_base in ca_base or ca == "amount":
                    pa = prefer
                    break
        if not pa or pa == ca:
            continue

        c_src = _table_or_union(child, shard_map, "c")
        p_src = _table_or_union(parent, shard_map, "p")
        try:
            cur.execute(
                f'SELECT COUNT(*) FROM {c_src} '
                f'JOIN {p_src} ON c."{fk}" = p."id" '
                f'WHERE c."{ca}" IS NOT NULL AND p."{pa}" IS NOT NULL '
                f'AND c."{ca}" != p."{pa}" '
                f'AND c."{ca}" = c."{ca}"'
            )
            count = cur.fetchone()[0]
        except Exception:
            continue
        if not count:
            continue
        findings.append({
            "title": f"{module_prefix}跨表金额不一致: {child}.{ca}≠{parent}.{pa}({count}条)",
            "description": (
                f"数据库联合审计发现{count}条{child}记录的{ca}"
                f"不等于关联{parent}的{pa}。"
                f"跨表金额守恒约束要求{child}.{ca}必须等于{parent}.{pa}。"
            ),
            "summary": f"{child}.{ca}与{parent}.{pa}不一致",
            "category": "conservation_violation",
            "defect_family": "inventory",
            "risk_type": "business_logic",
            "expected": f"{child}.{ca}={parent}.{pa}(跨表金额守恒)",
            "actual": f"{count}条记录金额不一致",
            "severity": "high",
            "confidence": "high",
            "reproduction": {"method": "DB_AUDIT", "path": f"/{child} JOIN /{parent}"},
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
            "gate_passed": True,
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
        })
    return findings


def _check_fk_count_limit_sharded(
    cur: Any,
    edge: dict[str, str],
    schema: dict[str, list[dict[str, str]]],
    shard_map: dict[str, list[str]],
    module_prefix: str = "",
) -> list[dict[str, Any]]:
    """Shard-aware FK count limit check."""
    child, parent, fk = edge["child_table"], edge["parent_table"], edge["fk_col"]
    parent_cols = _col_names(parent, schema)
    limit_cols = [c for c in parent_cols if _LIMIT_COL_RE.search(c)]
    if not limit_cols:
        return []

    findings = []
    p_src = _table_or_union(parent, shard_map, "p")
    c_src = _table_or_union(child, shard_map, "c")
    for lc in limit_cols:
        try:
            cur.execute(
                f'SELECT p."id", p."{lc}", COUNT(c."id") as usage_count '
                f'FROM {p_src} '
                f'JOIN {c_src} ON c."{fk}" = p."id" '
                f'WHERE p."{lc}" IS NOT NULL AND p."{lc}" > 0 '
                f'GROUP BY p."id", p."{lc}" '
                f'HAVING COUNT(c."id") > p."{lc}" '
                f'LIMIT 5'
            )
            rows = cur.fetchall()
        except Exception:
            continue
        if not rows:
            continue
        sample = rows[0]
        findings.append({
            "title": f"{module_prefix}跨表限制违反: {child}使用次数超过{parent}.{lc}({len(rows)}+条)",
            "description": (
                f"数据库联合审计发现{child}关联{parent}的记录数"
                f"超过{parent}.{lc}限制。"
                f"示例: {parent}_id={sample[0]},{lc}={sample[1]},实际使用{sample[2]}次。"
            ),
            "summary": f"{child}使用次数超过{parent}.{lc}限制",
            "category": "business_rule_violation",
            "defect_family": "coupon",
            "risk_type": "business_logic",
            "expected": f"COUNT({child})<={parent}.{lc}(跨表次数限制)",
            "actual": f"{len(rows)}+条{parent}的{child}使用次数超限",
            "severity": "medium",
            "confidence": "high",
            "reproduction": {"method": "DB_AUDIT", "path": f"/{child} JOIN /{parent}"},
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
            "gate_passed": True,
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
        })
    return findings
