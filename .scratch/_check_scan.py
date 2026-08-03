import json
d = json.load(open('platform_outputs/warehouse_e/scan_result.json', encoding='utf-8'))
print(f"success={d.get('success')}, grade={d.get('grade')}, findings={d.get('total_findings')}, status={d.get('execution_status')}")
ledger = d.get('obligation_attempt_ledger', {})
print(f"selected={ledger.get('selected_count')}, terminal={ledger.get('terminal_count')}")
print(f"status_counts={ledger.get('terminal_status_counts')}")

# Check v12 pipeline result
v12 = d.get('v12', {})
if v12:
    phases = v12.get('phases', {})
    exec_phase = phases.get('execution', {})
    print(f"\nv12 execution: {exec_phase.get('status')}")
    print(f"experiments executed: {exec_phase.get('experiments_executed')}")
    print(f"experiments blocked: {exec_phase.get('experiments_blocked')}")
