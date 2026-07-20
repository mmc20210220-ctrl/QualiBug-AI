"""Check target system accessibility."""
import requests
import json

# Load token
accounts = json.load(open(r'platform_inputs\benchmark_mall_131\test_accounts.json', encoding='utf-8'))
token = ''
for a in accounts.get('accounts', []):
    if a.get('name') == 'buyer01':
        token = a.get('token', '')
        break

headers = {'Authorization': f'Bearer {token}'} if token else {}

try:
    # Try different endpoints
    for path in ['/api/products', '/api/orders', '/api/auth/login']:
        r = requests.get(f'http://localhost:8080{path}', headers=headers, timeout=10)
        print(f'{path}: {r.status_code} - {r.text[:100]}')
except Exception as e:
    print(f'Error: {e}')
