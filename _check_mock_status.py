"""Check mock server status."""
import requests
from collections import Counter

r = requests.get('http://localhost:9092/api/v2/tickets', headers={'Authorization': 'Bearer admin-token-001'})
d = r.json()
print(f"Total tickets: {d.get('total', 0)}")
items = d.get('items', [])
statuses = Counter(t.get('ticket_status', '?') for t in items)
print(f"Status distribution: {dict(statuses)}")

# Check placeholder mappings
r2 = requests.get('http://localhost:9092/api/v2/health')
print(f"Server health: {r2.json()}")
