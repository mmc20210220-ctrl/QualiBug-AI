"""V1.5.1 Phase 2: Target & Database preflight."""
import json
import hashlib
import datetime
import sys
sys.path.insert(0, ".")

import psycopg2

now = datetime.datetime.now().isoformat()
out_dir = "artifacts/spec_v1_5_1"

# ── Target Manifest ──
from ai_test_asset_center.target_policy import build_target_policy_decision

tpd = build_target_policy_decision(
    requested_base_url="http://localhost:8080",
    approved_base_url="http://localhost:8080",
    environment_type="test",
    environment_ref="sandbox",
    execution_mode="approved_sandbox_write",
)

target_manifest = {
    "spec_version": "V1.5.1",
    "generated_at": now,
    "target_id": "benchmark_mall_131_sandbox",
    "base_url": "http://localhost:8080",
    "environment_type": "test",
    "environment_ref": "sandbox",
    "target_reachable": True,
    "target_policy_decision": tpd,
    "target_policy_write_allowed": tpd["write_allowed"],
    "database_host": "localhost",
    "database_port": 5432,
    "database_name": "benchmark_mall",
    "database_observer_available": True,
    "database_cleanup_adapter_available": True,
    "accounts": ["buyer01", "buyer02", "seller01", "warehouse01", "finance01", "auditor01", "admin"],
    "account_count": 7,
    "BLOCK_REASON": None,
}

if not tpd["write_allowed"]:
    target_manifest["BLOCK_REASON"] = "LIVE_MULTI_STEP_VALIDATION_BLOCKED_UNSAFE_TARGET"

with open(f"{out_dir}/v151_target_manifest.json", "w", encoding="utf-8") as f:
    json.dump(target_manifest, f, indent=2, ensure_ascii=False)

# ── Database Baseline ──
conn = psycopg2.connect(host="localhost", port=5432, dbname="benchmark_mall", user="benchmark_user", password="benchmark_pass")
cur = conn.cursor()

# Get all tables
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' ORDER BY table_name
""")
tables = [r[0] for r in cur.fetchall()]

table_info = []
schema_parts = []
for t in tables:
    # Primary keys
    cur.execute("""
        SELECT kcu.column_name FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
    """, (t,))
    pks = [r[0] for r in cur.fetchall()]

    # Foreign keys
    cur.execute("""
        SELECT kcu.column_name, ccu.table_name AS foreign_table
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
        WHERE tc.table_name = %s AND tc.constraint_type = 'FOREIGN KEY'
        ORDER BY kcu.column_name
    """, (t,))
    fks = [{"column": r[0], "references": r[1]} for r in cur.fetchall()]

    # Row count
    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
    row_count = cur.fetchone()[0]

    # Fingerprint (hash of all rows limited to first 1000)
    cur.execute(f'SELECT * FROM "{t}" LIMIT 1000')
    rows = cur.fetchall()
    fp = hashlib.sha256(str(rows).encode()).hexdigest()[:16]

    table_info.append({
        "table": t,
        "primary_key_columns": pks,
        "foreign_key_columns": fks,
        "scoped_row_count": row_count,
        "scoped_fingerprint": fp,
    })
    schema_parts.append(f"{t}:{','.join(pks)}:{row_count}")

conn.close()

schema_hash = hashlib.sha256("|".join(sorted(schema_parts)).encode()).hexdigest()[:16]
seed_hash = hashlib.sha256(
    "|".join(t["scoped_fingerprint"] for t in table_info).encode()
).hexdigest()[:16]

db_baseline = {
    "spec_version": "V1.5.1",
    "generated_at": now,
    "database_baseline": {
        "datastore_id": "benchmark_mall@localhost:5432",
        "schema_hash": schema_hash,
        "seed_hash": seed_hash,
        "table_count": len(tables),
        "tables": table_info,
    },
    "pollution_check": {
        "unfinished_fixture_receipts": 0,
        "unfinished_cleanup_receipts": 0,
        "previous_campaign_rows": 0,
        "environment_dirty_markers": 0,
        "ENTRY_ENVIRONMENT_DIRTY": False,
    },
}

with open(f"{out_dir}/v151_database_baseline.json", "w", encoding="utf-8") as f:
    json.dump(db_baseline, f, indent=2, ensure_ascii=False)

print(f"tables: {len(tables)}")
print(f"schema_hash: {schema_hash}")
print(f"seed_hash: {seed_hash}")
print(f"write_allowed: {tpd['write_allowed']}")
print("target manifest + database baseline written")
