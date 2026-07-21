# -*- coding: utf-8 -*-
"""Test multi-DB + sharded table support in db_state_audit."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from ai_test_asset_center.db_state_audit import (
    _detect_sharded_tables,
    _build_logical_schema,
    _normalize_dsn_config,
    _shard_union_subquery,
    _table_or_union,
    DataSource,
    LogicalTable,
    run_db_state_audit,
)

print("=" * 60)
print("TEST 1: Shard detection")
print("=" * 60)

# Simulate a schema with sharded tables
mock_schema = {
    "orders_0001": [{"column_name": "id", "data_type": "integer"}, {"column_name": "status", "data_type": "varchar"}],
    "orders_0002": [{"column_name": "id", "data_type": "integer"}, {"column_name": "status", "data_type": "varchar"}],
    "orders_0003": [{"column_name": "id", "data_type": "integer"}, {"column_name": "status", "data_type": "varchar"}],
    "users": [{"column_name": "id", "data_type": "integer"}, {"column_name": "name", "data_type": "varchar"}],
    "payments_01": [{"column_name": "id", "data_type": "integer"}, {"column_name": "amount", "data_type": "numeric"}],
    "payments_02": [{"column_name": "id", "data_type": "integer"}, {"column_name": "amount", "data_type": "numeric"}],
    "products": [{"column_name": "id", "data_type": "integer"}, {"column_name": "price", "data_type": "numeric"}],
    # Not a shard: single table with numeric suffix
    "log_20240101": [{"column_name": "id", "data_type": "integer"}],
    # Inconsistent shards (different schemas) - should NOT be detected
    "events_1": [{"column_name": "id", "data_type": "integer"}, {"column_name": "type", "data_type": "varchar"}],
    "events_2": [{"column_name": "id", "data_type": "integer"}, {"column_name": "category", "data_type": "varchar"}],
}

shard_map = _detect_sharded_tables(mock_schema)
print(f"  Detected shard groups: {list(shard_map.keys())}")
assert "orders" in shard_map, "orders shards not detected"
assert len(shard_map["orders"]) == 3, f"Expected 3 order shards, got {len(shard_map['orders'])}"
assert "payments" in shard_map, "payments shards not detected"
assert len(shard_map["payments"]) == 2, f"Expected 2 payment shards, got {len(shard_map['payments'])}"
assert "log" not in shard_map, "Single table with date suffix should NOT be shard"
assert "events" not in shard_map, "Inconsistent schema shards should NOT be detected"
print("  ✓ Shard detection correct")

print("\n" + "=" * 60)
print("TEST 2: Logical schema building")
print("=" * 60)

logical_schema, shard_map2 = _build_logical_schema(mock_schema)
print(f"  Physical tables: {len(mock_schema)}")
print(f"  Logical tables: {len(logical_schema)}")
print(f"  Logical table names: {sorted(logical_schema.keys())}")
assert "orders" in logical_schema, "Logical 'orders' table missing"
assert "orders_0001" not in logical_schema, "Physical shard should be removed"
assert "payments" in logical_schema, "Logical 'payments' table missing"
assert "users" in logical_schema, "Non-sharded table should remain"
assert "products" in logical_schema, "Non-sharded table should remain"
print("  ✓ Logical schema correct")

print("\n" + "=" * 60)
print("TEST 3: DSN config normalization")
print("=" * 60)

# Single DSN string
sources = _normalize_dsn_config("postgresql://user:pass@localhost:5432/mydb")
assert len(sources) == 1
assert sources[0].module == "default"
assert sources[0].dialect == "postgresql"
print("  ✓ Single DSN string → 1 DataSource")

# List of DSNs
sources = _normalize_dsn_config([
    "postgresql://user:pass@localhost:5432/order_db",
    "postgresql://user:pass@localhost:5433/payment_db",
])
assert len(sources) == 2
assert sources[0].module == "db_0"
assert sources[1].module == "db_1"
print("  ✓ DSN list → 2 DataSources")

# Dict module→DSN
sources = _normalize_dsn_config({
    "order": "postgresql://user:pass@localhost:5432/order_db",
    "payment": "postgresql://user:pass@localhost:5433/payment_db",
    "inventory": "mysql://user:pass@localhost:3306/inv_db",
})
assert len(sources) == 3
modules = {s.module for s in sources}
assert modules == {"order", "payment", "inventory"}
# Check dialect detection
inv_src = next(s for s in sources if s.module == "inventory")
assert inv_src.dialect == "mysql", f"Expected mysql dialect, got {inv_src.dialect}"
print("  ✓ DSN dict → 3 DataSources with correct modules and dialects")

print("\n" + "=" * 60)
print("TEST 4: Shard-aware SQL generation")
print("=" * 60)

# Single table (no shard)
sql = _table_or_union("users", {})
assert sql == '"users"', f"Expected '\"users\"', got '{sql}'"
print(f"  Non-sharded: {sql}")

# Sharded table
shard_map_test = {"orders": ["orders_0001", "orders_0002", "orders_0003"]}
sql = _table_or_union("orders", shard_map_test, "t")
assert "UNION ALL" in sql
assert "orders_0001" in sql
assert "orders_0002" in sql
assert "orders_0003" in sql
assert "AS t" in sql
print(f"  Sharded: {sql[:80]}...")
print("  ✓ SQL generation correct")

# UNION subquery
subq = _shard_union_subquery(["t1", "t2"], ["id", "amount"])
assert "UNION ALL" in subq
assert '"id"' in subq
assert '"amount"' in subq
print(f"  Union subquery: {subq[:80]}...")
print("  ✓ Union subquery correct")

print("\n" + "=" * 60)
print("TEST 5: Backward compatibility - real DB audit (single DSN)")
print("=" * 60)

# Load behavior IR from last scan
import json
from pathlib import Path

scan_result_path = Path(r"d:\QualiBug-AI\QualiBug-AI-main\scan_with_db_audit.json")
if not scan_result_path.exists():
    # Try alternative
    scan_result_path = Path(r"d:\QualiBug-AI\QualiBug-AI-main\last_scan_result.json")

if scan_result_path.exists():
    data = json.loads(scan_result_path.read_text(encoding="utf-8"))
    v12 = data.get("v12", {})
    behavior_ir = v12.get("behavior_ir", data.get("behavior_ir", {}))
else:
    behavior_ir = {}

if behavior_ir:
    dsn = "postgresql://postgres:postgres@localhost:5432/benchmark_mall"
    findings = run_db_state_audit(behavior_ir, dsn)
    print(f"  Single DSN audit: {len(findings)} findings")
    for f in findings[:5]:
        print(f"    - {f['title'][:70]}")
    print("  ✓ Backward compatible with single DSN")

    # Test with dict format (same DB, named module)
    findings2 = run_db_state_audit(behavior_ir, {"benchmark_mall": dsn})
    print(f"  Dict DSN audit: {len(findings2)} findings")
    assert len(findings2) == len(findings), "Dict and string DSN should produce same results"
    print("  ✓ Dict DSN format produces same results")
else:
    print("  ⚠ No behavior_ir available, skipping real DB test")
    print("  (Run a scan first to generate scan_with_db_audit.json)")

print("\n" + "=" * 60)
print("TEST 6: Multi-DB simulation (same DB, different names)")
print("=" * 60)

if behavior_ir:
    # Simulate multi-DB by passing same DSN twice with different module names
    # This tests the multi-DB orchestration path without needing actual separate DBs
    multi_dsn = {
        "module_a": dsn,
        "module_b": dsn,
    }
    findings_multi = run_db_state_audit(behavior_ir, multi_dsn)
    print(f"  Multi-DB (2 modules, same physical DB): {len(findings_multi)} findings")
    # Should produce findings from both modules + cross-DB checks
    # (cross-DB checks may find 0 issues since it's the same data)
    print("  ✓ Multi-DB orchestration path executes without error")
else:
    print("  ⚠ Skipped (no behavior_ir)")

print("\n" + "=" * 60)
print("ALL TESTS PASSED ✓")
print("=" * 60)
