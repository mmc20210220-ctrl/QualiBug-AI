import json
from collections import Counter

path = r'platform_outputs\benchmark_mall_131\discovery_evolution\trace_ledgers\private_default\RUN_a84d282132ebfbe5213d39af.trace-ledger.json'
d = json.load(open(path, 'r', encoding='utf-8'))

attempts = d.get('attempts', [])

# Check structure of first BLOCKED attempt
blocked = [a for a in attempts if a.get('terminal_status') == 'BLOCKED']
if blocked:
    print('First BLOCKED attempt keys:', list(blocked[0].keys()))
    print('\nFirst BLOCKED attempt:')
    print(json.dumps(blocked[0], indent=2, ensure_ascii=False)[:2000])

# Check DEFERRED reasons
deferred = [a for a in attempts if a.get('terminal_status') == 'DEFERRED']
if deferred:
    print('\n\nFirst DEFERRED attempt:')
    print(json.dumps(deferred[0], indent=2, ensure_ascii=False)[:1500])
