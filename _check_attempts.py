import json
from collections import Counter

# Check the latest trace ledger
path = r'platform_outputs\benchmark_mall_131\discovery_evolution\trace_ledgers\private_default\RUN_a84d282132ebfbe5213d39af.trace-ledger.json'
d = json.load(open(path, 'r', encoding='utf-8'))

attempts = d.get('attempts', [])
print(f'Total attempts: {len(attempts)}')

# Terminal status distribution
terminal_counts = Counter(a.get('terminal_status') for a in attempts)
print('\nTerminal status distribution:')
for status, count in terminal_counts.most_common():
    print(f'  {status}: {count}')

# Reason code distribution
reason_counts = Counter(a.get('reason_code') for a in attempts)
print('\nReason code distribution (top 15):')
for reason, count in reason_counts.most_common(15):
    print(f'  {reason}: {count}')

# Check execution status distribution
exec_counts = Counter(a.get('execution_status') for a in attempts)
print('\nExecution status distribution:')
for status, count in exec_counts.most_common():
    print(f'  {status}: {count}')

# Check risk family distribution for BLOCKED attempts
blocked = [a for a in attempts if a.get('terminal_status') == 'BLOCKED']
risk_counts = Counter(a.get('risk_family') for a in blocked)
print('\nRisk family distribution (BLOCKED only):')
for risk, count in risk_counts.most_common(10):
    print(f'  {risk}: {count}')
