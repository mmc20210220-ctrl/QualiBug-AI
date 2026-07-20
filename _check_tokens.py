"""Check token expiration."""
import json
import time
import base64

accounts = json.load(open(r'platform_inputs\benchmark_mall_131\test_accounts.json', encoding='utf-8'))
now = time.time()
print(f'Current epoch: {int(now)}')

for a in accounts.get('accounts', []):
    token = a.get('token', '')
    if not token:
        print(f"  {a['name']}: NO TOKEN")
        continue
    try:
        payload = token.split('.')[1]
        # Add padding
        payload += '=' * (4 - len(payload) % 4)
        decoded = json.loads(base64.b64decode(payload))
        exp = decoded.get('exp', 0)
        status = 'OK' if exp > now else 'EXPIRED'
        print(f"  {a['name']}: exp={exp} ({status})")
    except Exception as e:
        print(f"  {a['name']}: ERROR {e}")
