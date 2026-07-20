"""Reset benchmark mall user accounts to ACTIVE status."""
import psycopg2

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='benchmark_mall',
    user='benchmark_user',
    password='benchmark_pass'
)
cur = conn.cursor()
cur.execute("UPDATE users SET status = 'ACTIVE' WHERE status = 'DISABLED'")
conn.commit()
print(f'Updated {cur.rowcount} rows')
# Re-disable the disabled_buyer account (should remain DISABLED for testing)
cur.execute("UPDATE users SET status = 'DISABLED' WHERE email = 'disabled_buyer@example.com'")
conn.commit()
print(f'Re-disabled disabled_buyer: {cur.rowcount} rows')
cur.execute('SELECT email, status FROM users')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')
conn.close()
