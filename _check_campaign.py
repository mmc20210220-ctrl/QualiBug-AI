import json

d = json.load(open('platform_workspace/benchmark_mall_131/defect_discovery/campaigns/CMP_e3f2654c5cd161c4ac4f5b3b.json', 'r', encoding='utf-8'))
print('campaign_status:', d.get('campaign_status'))
print('run_count:', d.get('run_count'))
print('obligation_attempt_selected_count:', d.get('obligation_attempt_selected_count'))
print('obligation_attempt_terminal_count:', d.get('obligation_attempt_terminal_count'))
print('audit_events count:', len(d.get('audit_events', [])))

events = d.get('audit_events', [])
print('\nLast 5 audit events:')
for e in events[-5:]:
    print(f"  {e.get('event_type')}: {str(e.get('reason') or e.get('detail') or '')[:100]}")
