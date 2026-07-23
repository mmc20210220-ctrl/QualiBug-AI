"""Check mock server state and ticket count."""
import urllib.request
import json

r = urllib.request.urlopen("http://localhost:9090/api/v2/tickets", timeout=3)
data = json.loads(r.read())
items = data.get("items", [])
print(f"Active tickets: {len(items)}")
for t in items[:10]:
    print(f"  {t['ticket_ref']}: eq={t['equipment_ref']}, status={t['ticket_status']}")
