import json
from collections import Counter

path = r'platform_outputs\benchmark_mall_131\discovery_evolution\trace_ledgers\private_default\RUN_a84d282132ebfbe5213d39af.trace-ledger.json'
d = json.load(open(path, 'r', encoding='utf-8'))

print('Schema:', d.get('schema'))
print('Run ID:', d.get('run_id'))
print('Campaign ID:', d.get('campaign_id'))

attempts = d.get('attempts', [])
print(f'\nTotal attempts: {len(attempts)}')

# Terminal status distribution
terminal_counts = Counter(a.get('terminal_status') for a in attempts)
print('\nTerminal status distribution:')
for status, count in terminal_counts.most_common():
    print(f'  {status}: {count}')

# Blocking reasons for BLOCKED attempts
blocked = [a for a in attempts if a.get('terminal_status') == 'BLOCKED']
if blocked:
    reason_counts = Counter()
    for a in blocked:
        stages = a.get('stages', {})
        for stage_name, stage in stages.items():
            if isinstance(stage, dict) and stage.get('status') == 'BLOCKED':
                reason = stage.get('reason_code') or stage.get('reason') or 'unknown'
                reason_counts[f'{stage_name}:{reason}'] += 1
    print('\nBlocking reasons (top 15):')
    for reason, count in reason_counts.most_common(15):
        print(f'  {reason}: {count}')
