# -*- coding: utf-8 -*-
"""Test DB connection and schema introspection."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DSN = "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"

try:
    import psycopg2
    conn = psycopg2.connect(DSN, connect_timeout=5)
    cur = conn.cursor()
    # List tables
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"DB连接成功! 表数量: {len(tables)}")
    print(f"表: {tables}")
    # For each table, show columns and row count
    for t in tables[:15]:
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (t,))
        cols = [(r[0], r[1]) for r in cur.fetchall()]
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        cnt = cur.fetchone()[0]
        col_names = [c[0] for c in cols]
        print(f"\n  {t} ({cnt}行): {col_names}")
    conn.close()
except ImportError:
    print("psycopg2未安装, 尝试asyncpg...")
    try:
        import asyncio, asyncpg
        async def main():
            conn = await asyncpg.connect(DSN, timeout=5)
            tables = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
            print(f"DB连接成功! 表数量: {len(tables)}")
            print(f"表: {[r['table_name'] for r in tables]}")
            await conn.close()
        asyncio.run(main())
    except ImportError:
        print("asyncpg也未安装. 需要: pip install psycopg2-binary")
except Exception as e:
    print(f"DB连接失败: {e}")
