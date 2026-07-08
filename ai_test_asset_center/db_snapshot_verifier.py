"""
QualiBug DB Snapshot Verifier — 通用数据库前后快照对比验证引擎。

作为 scan() 统一管道的一部分，接受任意 DB 连接配置，
在测试动作前后抓取表快照，对比差异并生成硬证据。

支持 12 种数据库：
  关系型: PostgreSQL / MySQL / MariaDB / SQL Server / Oracle / IBM DB2 / ClickHouse
  非关系型: MongoDB / Redis / Elasticsearch / Cassandra / Neo4j
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DBSnapshot:
    """单次数据库快照"""
    table: str
    row_count: int
    rows: list[dict[str, Any]]
    checksum: str
    captured_at: str

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "row_count": self.row_count,
            "row_sample": self.rows[:5],
            "checksum": self.checksum,
            "captured_at": self.captured_at,
        }


@dataclass
class DBDiff:
    """前后快照差异"""
    table: str
    before_count: int
    after_count: int
    added_rows: int
    removed_rows: int
    modified_rows: int
    checksum_changed: bool
    detail: str

    def is_anomaly(self) -> bool:
        return self.added_rows > 0 or self.removed_rows > 0 or self.modified_rows > 0


@dataclass
class DBSnapshotResult:
    ok: bool
    db_type: str
    tables_checked: int
    before_snapshots: list[dict]
    after_snapshots: list[dict]
    diffs: list[dict]
    findings: list[dict]
    duration_ms: int


class DBSnapshotVerifier:
    """通用数据库快照验证器 — 支持 SQLite / PostgreSQL / MySQL / Oracle / SQL Server。

    配置方式 (任选其一):
      1. QUALIBUG_DB_DSN="postgresql://user:pass@host:5432/db"
      2. QUALIBUG_DB_TYPE=postgresql + QUALIBUG_DB_HOST/NAME/USER/PASS

    用法:
        v = DBSnapshotVerifier()
        v.snapshot_before(["orders", "inventory"])
        # ... 执行测试动作 ...
        v.snapshot_after(["orders", "inventory"])
        result = v.verify()
    """

    def __init__(self, dsn: str = ""):
        self.dsn = dsn or self._dsn_from_env()
        self._before: dict[str, DBSnapshot] = {}
        self._after: dict[str, DBSnapshot] = {}
        self._conn = None
        self._db_type = "unknown"

    @staticmethod
    def _dsn_from_env() -> str:
        dsn = os.environ.get("QUALIBUG_DB_DSN", "")
        if dsn:
            return dsn

        db_type = os.environ.get("QUALIBUG_DB_TYPE", "").lower()
        host = os.environ.get("QUALIBUG_DB_HOST", "localhost")
        port = os.environ.get("QUALIBUG_DB_PORT", "")
        name = os.environ.get("QUALIBUG_DB_NAME", "")
        user = os.environ.get("QUALIBUG_DB_USER", "")
        password = os.environ.get("QUALIBUG_DB_PASS", "")

        if not name:
            return ""

        # ── 关系型 ──
        if db_type in ("postgresql", "postgres", "pg"):
            return f"postgresql://{user}:{password}@{host}:{port or 5432}/{name}"
        if db_type in ("mysql",):
            return f"mysql+pymysql://{user}:{password}@{host}:{port or 3306}/{name}"
        if db_type == "mariadb":
            return f"mariadb+pymysql://{user}:{password}@{host}:{port or 3306}/{name}"
        if db_type in ("sqlserver", "mssql"):
            return f"mssql+pymssql://{user}:{password}@{host}:{port or 1433}/{name}"
        if db_type in ("oracle", "oracledb"):
            return f"oracle://{user}:{password}@{host}:{port or 1521}/{name}"
        if db_type == "db2":
            return f"db2+ibm_db://{user}:{password}@{host}:{port or 50000}/{name}"
        if db_type == "sqlite":
            return f"sqlite:///{name}"
        if db_type == "clickhouse":
            return f"clickhouse://{user}:{password}@{host}:{port or 8123}/{name}"

        # ── 非关系型 ──
        if db_type == "mongodb":
            return f"mongodb://{user}:{password}@{host}:{port or 27017}/{name}"
        if db_type == "redis":
            return f"redis://:{password}@{host}:{port or 6379}/0"
        if db_type == "elasticsearch":
            return f"http://{user}:{password}@{host}:{port or 9200}"
        if db_type == "cassandra":
            return f"cassandra://{user}:{password}@{host}:{port or 9042}/{name}"
        if db_type == "neo4j":
            return f"bolt://{user}:{password}@{host}:{port or 7687}"

        return ""

    @property
    def configured(self) -> bool:
        return bool(self.dsn)

    def _connect(self):
        """Auto-detect DB driver from DSN prefix and connect. Supports 12 database types."""
        if self._conn:
            return

        dsn = self.dsn

        # SQLite
        if dsn.startswith("sqlite:///") or dsn.endswith(".db") or dsn.endswith(".sqlite3"):
            import sqlite3
            path = dsn.replace("sqlite:///", "") if dsn.startswith("sqlite:///") else dsn
            if not os.path.isabs(path):
                path = str(Path(path).resolve())
            self._conn = sqlite3.connect(path)
            self._conn.row_factory = sqlite3.Row
            self._db_type = "sqlite3"
            return

        # PostgreSQL
        if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
            import psycopg2
            self._conn = psycopg2.connect(dsn)
            self._db_type = "postgresql"
            return

        # MySQL
        if dsn.startswith("mysql://"):
            import pymysql
            self._conn = pymysql.connect(dsn)
            self._db_type = "mysql"
            return

        # MariaDB (same driver as MySQL)
        if dsn.startswith("mariadb://"):
            import pymysql
            self._conn = pymysql.connect(dsn)
            self._db_type = "mariadb"
            return

        # SQL Server (pymssql → pyodbc)
        if dsn.startswith("mssql://"):
            try:
                import pymssql
                self._conn = pymssql.connect(dsn)
                self._db_type = "mssql"
                return
            except ImportError:
                pass
            import pyodbc
            self._conn = pyodbc.connect(dsn)
            self._db_type = "mssql"
            return

        # Oracle
        if dsn.startswith("oracle://"):
            import oracledb
            self._conn = oracledb.connect(dsn)
            self._db_type = "oracle"
            return

        # IBM DB2
        if dsn.startswith("db2://"):
            import ibm_db
            self._conn = ibm_db.connect(dsn, "", "")
            self._db_type = "db2"
            return

        # ClickHouse (HTTP + native SQL protocol)
        if dsn.startswith("clickhouse://"):
            import clickhouse_driver
            self._conn = clickhouse_driver.Client.from_url(dsn)
            self._db_type = "clickhouse"
            return

        # MongoDB
        if dsn.startswith("mongodb://"):
            import pymongo
            client = pymongo.MongoClient(dsn)
            db_name = dsn.rsplit("/", 1)[-1].split("?")[0] or "admin"
            self._conn = client[db_name]
            self._db_type = "mongodb"
            return

        # Redis
        if dsn.startswith("redis://"):
            import redis
            self._conn = redis.from_url(dsn)
            self._db_type = "redis"
            return

        # Elasticsearch
        if dsn.startswith("http://") or dsn.startswith("https://"):
            # Only treat as ES if explicitly typed; URLs could be APIs
            if "9200" not in dsn and "elastic" not in dsn.lower():
                raise RuntimeError(f"Unknown HTTP DSN — set QUALIBUG_DB_TYPE explicitly. DSN: {dsn[:60]}")
            import urllib.request, base64
            self._conn = dsn  # store URL for REST queries
            self._db_type = "elasticsearch"
            return

        # Cassandra (CQL)
        if dsn.startswith("cassandra://"):
            import cassandra.cluster
            from cassandra.auth import PlainTextAuthProvider
            parts = dsn.replace("cassandra://", "").split("@")
            auth = parts[0].split(":") if ":" in parts[0] else ["", ""]
            host_port = parts[1].split("/")[0] if len(parts) > 1 else "localhost:9042"
            hp = host_port.split(":")
            auth_provider = PlainTextAuthProvider(username=auth[0], password=auth[1]) if auth[0] else None
            cluster = cassandra.cluster.Cluster([hp[0]], port=int(hp[1]) if len(hp) > 1 else 9042,
                                               auth_provider=auth_provider)
            session = cluster.connect(parts[1].split("/")[1] if "/" in parts[1] else None)
            self._conn = session
            self._db_type = "cassandra"
            return

        # Neo4j (Bolt protocol)
        if dsn.startswith("bolt://"):
            import neo4j
            parts = dsn.replace("bolt://", "")
            auth_section, host_section = parts.split("@") if "@" in parts else ("", parts)
            user, pwd = auth_section.split(":") if ":" in auth_section else ("neo4j", "")
            self._conn = neo4j.GraphDatabase.driver(f"bolt://{host_section}", auth=(user, pwd))
            self._db_type = "neo4j"
            return

        raise RuntimeError(
            f"Unsupported database type. Install the appropriate driver:\n"
            f"  PostgreSQL:   pip install psycopg2-binary\n"
            f"  MySQL:        pip install pymysql\n"
            f"  SQL Server:   pip install pymssql\n"
            f"  Oracle:       pip install oracledb\n"
            f"  DB2:          pip install ibm-db\n"
            f"  ClickHouse:   pip install clickhouse-driver\n"
            f"  MongoDB:      pip install pymongo\n"
            f"  Redis:        pip install redis\n"
            f"  Elasticsearch:pip install elasticsearch\n"
            f"  Cassandra:    pip install cassandra-driver\n"
            f"  Neo4j:        pip install neo4j\n"
            f"  SQLite:       内置支持\n"
            f"DSN: {dsn[:80]}..."
        )

    def snapshot(self, tables: list[str], label: str) -> dict[str, DBSnapshot]:
        if not self.configured:
            return {}

        try:
            self._connect()
        except Exception as e:
            print(f"  [WARN] DB snapshot failed ({label}): {e}", flush=True)
            return {}

        snapshots: dict[str, DBSnapshot] = {}
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        import hashlib

        for table in tables:
            try:
                rows = self._query_all(f"SELECT * FROM {table}")
            except Exception:
                continue

            sorted_json = json.dumps(
                sorted([dict(r) for r in rows], key=lambda x: json.dumps(x, sort_keys=True, default=str)),
                sort_keys=True, default=str,
            )
            checksum = hashlib.sha256(sorted_json.encode()).hexdigest()[:16]

            snapshots[table] = DBSnapshot(
                table=table,
                row_count=len(rows),
                rows=[dict(r) for r in rows],
                checksum=checksum,
                captured_at=timestamp,
            )

        # The captured rows now live in memory (verify() never touches the DB
        # again), so release the connection immediately. Without this, a scan that
        # snapshots hundreds of write probes leaks one connection per probe and
        # exhausts the server's connection limit — which then surfaces as an opaque
        # locale-encoded driver error, not as "too many connections".
        self.close()
        return snapshots

    def close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        finally:
            self._conn = None

    def snapshot_before(self, tables: list[str]) -> None:
        self._before = self.snapshot(tables, "before")

    def snapshot_after(self, tables: list[str]) -> None:
        self._after = self.snapshot(tables, "after")

    def verify(self) -> DBSnapshotResult:
        t0 = time.time()

        if not self._before and not self._after:
            return DBSnapshotResult(
                ok=True, db_type=self._db_type, tables_checked=0,
                before_snapshots=[], after_snapshots=[], diffs=[], findings=[],
                duration_ms=0,
            )

        all_tables = set(self._before.keys()) | set(self._after.keys())
        diffs: list[DBDiff] = []
        findings: list[dict] = []

        for table in sorted(all_tables):
            before = self._before.get(table)
            after = self._after.get(table)

            if not before and after:
                diffs.append(DBDiff(table, 0, after.row_count,
                    after.row_count, 0, 0, True,
                    f"table {table} appeared with {after.row_count} rows after action"))
                continue

            if before and not after:
                diffs.append(DBDiff(table, before.row_count, 0,
                    0, before.row_count, 0, True,
                    f"table {table} was emptied ({before.row_count} -> 0)"))
                findings.append({
                    "severity": "P0",
                    "title": f"DB anomaly: table {table} was emptied",
                    "category": "data_integrity",
                    "evidence": f"before={before.row_count} rows, after=0",
                    "source": "db_snapshot_verifier",
                })
                continue

            if not before or not after:
                continue

            added = removed = modified = 0
            checksum_changed = before.checksum != after.checksum

            if checksum_changed:
                before_rows = {json.dumps(dict(r), sort_keys=True, default=str): dict(r)
                              for r in before.rows}
                after_rows = {json.dumps(dict(r), sort_keys=True, default=str): dict(r)
                             for r in after.rows}
                bk = set(before_rows.keys())
                ak = set(after_rows.keys())
                added = len(ak - bk)
                removed = len(bk - ak)
                modified = len([k for k in (bk & ak) if before_rows[k] != after_rows[k]])

            diff = DBDiff(table, before.row_count, after.row_count,
                         added, removed, modified, checksum_changed,
                         f"{table}: {before.row_count}->{after.row_count} (+{added} -{removed} ~{modified})")
            diffs.append(diff)

            if diff.is_anomaly():
                findings.append({
                    "severity": "P1",
                    "title": f"DB data change: {diff.detail}",
                    "category": "data_integrity",
                    "evidence": {
                        "table": table,
                        "before_count": before.row_count,
                        "after_count": after.row_count,
                        "added": added, "removed": removed, "modified": modified,
                        "before_checksum": before.checksum,
                        "after_checksum": after.checksum,
                    },
                    "source": "db_snapshot_verifier",
                })

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass

        return DBSnapshotResult(
            ok=True, db_type=self._db_type, tables_checked=len(all_tables),
            before_snapshots=[s.to_dict() for s in self._before.values()],
            after_snapshots=[s.to_dict() for s in self._after.values()],
            diffs=[d.__dict__ for d in diffs],
            findings=findings,
            duration_ms=int((time.time() - t0) * 1000),
        )

    def _query_all(self, sql_or_collection: str) -> list:
        """Execute query or collection scan, depending on DB type."""
        if self._db_type == "sqlite3":
            return [dict(r) for r in self._conn.execute(sql_or_collection).fetchall()]

        if self._db_type in ("postgresql", "mysql", "mariadb", "mssql", "oracle", "db2"):
            cur = self._conn.cursor()
            cur.execute(sql_or_collection)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return rows

        if self._db_type == "clickhouse":
            result = self._conn.execute(sql_or_collection)
            columns = [col[0] for col in (result.columns_with_types if hasattr(result, 'columns_with_types') else [])]
            rows = [dict(zip(columns, row)) for row in result] if columns else list(result)
            return rows

        if self._db_type == "mongodb":
            collection = self._conn[sql_or_collection]
            return list(collection.find({}).limit(200))

        if self._db_type == "cassandra":
            rows = self._conn.execute(f"SELECT * FROM {sql_or_collection} LIMIT 200")
            return [dict(row._asdict()) if hasattr(row, '_asdict') else dict(zip(row._fields, row))
                   for row in rows]

        if self._db_type == "neo4j":
            label = sql_or_collection.capitalize()
            result = self._conn.session().run(f"MATCH (n:{label}) RETURN n LIMIT 200")
            return [dict(r["n"]) for r in result]

        if self._db_type == "redis":
            keys = self._conn.keys(f"{sql_or_collection}:*")[:100] if hasattr(self._conn, 'keys') else []
            rows = []
            for k in keys:
                k_str = k.decode() if isinstance(k, bytes) else str(k)
                try:
                    v = self._conn.get(k_str)
                    rows.append({"key": k_str, "value": v.decode() if isinstance(v, bytes) else str(v)})
                except Exception:
                    rows.append({"key": k_str, "value": "?"})
            return rows

        if self._db_type == "elasticsearch":
            import urllib.request, json as _j
            url = f"{self._conn}/{sql_or_collection}/_search?size=200"
            req = urllib.request.Request(url, data=_j.dumps({"query": {"match_all": {}}}).encode(),
                                        headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = _j.loads(resp.read())
            return [hit["_source"] for hit in data.get("hits", {}).get("hits", [])]

        return []

    def list_tables(self) -> list[str]:
        """Return base table/collection names for the connected store, generically.

        Used so the executor can snapshot every table and let the diff itself
        reveal which one changed — no per-project table hardcoding required.
        """
        if not self.configured:
            return []
        try:
            self._connect()
        except Exception:
            return []
        try:
            if self._db_type == "sqlite3":
                rows = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                return [str(r[0]) for r in rows]
            if self._db_type == "postgresql":
                rows = self._query_all(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
                )
                return [str(r.get("table_name")) for r in rows if r.get("table_name")]
            if self._db_type in ("mysql", "mariadb"):
                rows = self._query_all(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_type='BASE TABLE'"
                )
                return [str(r.get("table_name") or r.get("TABLE_NAME")) for r in rows if (r.get("table_name") or r.get("TABLE_NAME"))]
            if self._db_type in ("mssql", "oracle", "db2"):
                rows = self._query_all(
                    "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE'"
                )
                return [str(r.get("table_name") or r.get("TABLE_NAME")) for r in rows if (r.get("table_name") or r.get("TABLE_NAME"))]
        except Exception:
            return []
        return []


if __name__ == "__main__":
    import tempfile, sqlite3 as _sq
    db_path = Path(tempfile.mktemp(suffix=".db"))
    conn = _sq.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount REAL)")
    conn.execute("INSERT INTO orders VALUES (1, 'pending', 100.0)")
    conn.execute("INSERT INTO orders VALUES (2, 'pending', 200.0)")
    conn.commit(); conn.close()

    os.environ["QUALIBUG_DB_DSN"] = str(db_path)
    v = DBSnapshotVerifier()
    v.snapshot_before(["orders"])
    conn = _sq.connect(str(db_path))
    conn.execute("UPDATE orders SET status='completed' WHERE id=1")
    conn.execute("INSERT INTO orders VALUES (3, 'cancelled', 50.0)")
    conn.commit(); conn.close()
    v.snapshot_after(["orders"])

    r = v.verify()
    print(f"DB type: {r.db_type}, tables: {r.tables_checked}")
    for d in r.diffs:
        print(f"  {d['detail']}")
    for f in r.findings:
        print(f"  [{f['severity']}] {f['title']}")

    db_path.unlink(missing_ok=True)
    print("OK")
