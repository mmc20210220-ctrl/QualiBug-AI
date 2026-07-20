"""Check CLEANUP_RESTORATION_NOT_PROVEN details."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find experiments with CLEANUP_RESTORATION_NOT_PROVEN
cleanup_blocked = []
for r in results:
    ov = r.get('oracle_verdict', {})
    if not isinstance(ov, dict):
        continue
    activation = ov.get('activation_receipt', {})
    if not isinstance(activation, dict):
        continue
    rcs = activation.get('reason_codes', [])
    if any('CLEANUP_RESTORATION_NOT_PROVEN' in rc for rc in rcs):
        cleanup_blocked.append(r)

print(f'Experiments with CLEANUP_RESTORATION_NOT_PROVEN: {len(cleanup_blocked)}')

# Check cleanup receipt evidence
for r in cleanup_blocked[:3]:
    oid = r.get('obligation_id', '')[:40]
    print(f'\nObligation: {oid}')
    for cr in r.get('contract_evidence_receipts', []):
        if isinstance(cr, dict) and cr.get('kind') == 'cleanup':
            ev = cr.get('evidence', {})
            status = cr.get('status', '')
            print(f'  Cleanup receipt: status={status}')
            if isinstance(ev, dict):
                for key in ['restoration_verified', 'state_unchanged', 'accepted_write_count', 
                           'cleanup_write_count', 'audit_receipt_ids']:
                    print(f'    {key}: {ev.get(key)}')

# Check what operations these are
ops = Counter()
for r in cleanup_blocked:
    for cr in r.get('contract_evidence_receipts', []):
        if isinstance(cr, dict) and cr.get('kind') == 'treatment':
            ev = cr.get('evidence', {})
            if isinstance(ev, dict):
                method = ev.get('method', '')
                path = ev.get('path_template', ev.get('path', ''))
                ops[f'{method} {path}'] += 1

print(f'\nTreatment operations:')
for op, c in ops.most_common(10):
    print(f'  {c:3d}x {op}')
"""Analyze remaining blockers after 104 findings."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

print(f'Total results: {len(results)}')
reason_counts = Counter(r.get('reason_code') for r in results)
print(f'Reason codes: {dict(reason_counts.most_common(15))}')

# Oracle verdicts
verdicts = Counter()
for r in results:
    ov = r.get('oracle_verdict', {})
    if isinstance(ov, dict):
        verdicts[ov.get('verdict', 'none')] += 1
print(f'Oracle verdicts: {dict(verdicts.most_common(10))}')

# Check ORACLE_NOT_VIOLATED blockers
onv = [r for r in results if r.get('reason_code') == 'ORACLE_NOT_VIOLATED']
print(f'\nORACLE_NOT_VIOLATED: {len(onv)}')
blockers = Counter()
for r in onv:
    ov = r.get('oracle_verdict', {})
    if isinstance(ov, dict):
        activation = ov.get('activation_receipt', {})
        if isinstance(activation, dict):
            for rc in activation.get('reason_codes', []):
                blockers[rc] += 1
print(f'Activation blockers:')
for b, c in blockers.most_common(15):
    print(f'  {c:3d}x {b}')

# Check INDETERMINATE verdicts
indet = [r for r in results if isinstance(r.get('oracle_verdict'), dict) and r['oracle_verdict'].get('verdict') == 'indeterminate']
print(f'\nINDETERMINATE verdicts: {len(indet)}')
indet_reasons = Counter()
for r in indet:
    ov = r.get('oracle_verdict', {})
    for a in ov.get('assertions', []):
        if isinstance(a, dict):
            indet_reasons[a.get('reason_code', 'none')] += 1
print(f'Indeterminate assertion reasons:')
for rc, c in indet_reasons.most_common(10):
    print(f'  {c:3d}x {rc}')

# Check HARNESS_FAILED
hf = [r for r in results if isinstance(r.get('oracle_verdict'), dict) and r['oracle_verdict'].get('verdict') == 'harness_failure']
print(f'\nHARNESS_FAILED: {len(hf)}')
hf_reasons = Counter()
for r in hf:
    ov = r.get('oracle_verdict', {})
    activation = ov.get('activation_receipt', {})
    if isinstance(activation, dict):
        for rc in activation.get('reason_codes', []):
            hf_reasons[rc] += 1
print(f'Harness failure reasons:')
for b, c in hf_reasons.most_common(10):
    print(f'  {c:3d}x {b}')

# Findings
findings = d.get('findings', [])
print(f'\nFinal findings: {len(findings)}')
"""Analyze 50 INDETERMINATE verdicts."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find INDETERMINATE verdicts
indet = [r for r in results if isinstance(r.get('oracle_verdict'), dict) 
         and r['oracle_verdict'].get('verdict') == 'indeterminate']
print(f'INDETERMINATE verdicts: {len(indet)}')

# Check assertion reason codes
assertion_reasons = Counter()
assertion_kinds = Counter()
for r in indet:
    ov = r.get('oracle_verdict', {})
    for assertion in ov.get('assertions', []):
        if isinstance(assertion, dict):
            rc = assertion.get('reason_code', 'none')
            kind = assertion.get('kind', 'unknown')
            assertion_reasons[rc] += 1
            assertion_kinds[kind] += 1

print(f'\nAssertion reason codes:')
for rc, c in assertion_reasons.most_common(15):
    print(f'  {c:3d}x {rc}')

print(f'\nAssertion kinds:')
for kind, c in assertion_kinds.most_common(10):
    print(f'  {c:3d}x {kind}')

# Check treatment status codes for INDETERMINATE
treatment_statuses = Counter()
for r in indet:
    for cr in r.get('contract_evidence_receipts', []):
        if isinstance(cr, dict) and cr.get('kind') == 'treatment':
            ev = cr.get('evidence', {})
            if isinstance(ev, dict):
                sc = ev.get('status_code', 0)
                treatment_statuses[sc] += 1

print(f'\nTreatment status codes:')
for sc, c in treatment_statuses.most_common(10):
    print(f'  {c:3d}x HTTP {sc}')
"""Analyze remaining blockers after 58 findings."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

print(f'Total results: {len(results)}')
reason_counts = Counter(r.get('reason_code') for r in results)
print(f'Reason codes: {dict(reason_counts.most_common(15))}')

# Oracle verdicts
verdicts = Counter()
for r in results:
    ov = r.get('oracle_verdict', {})
    if isinstance(ov, dict):
        verdicts[ov.get('verdict', 'none')] += 1
print(f'Oracle verdicts: {dict(verdicts.most_common(10))}')

# Check ORACLE_NOT_VIOLATED blockers
onv = [r for r in results if r.get('reason_code') == 'ORACLE_NOT_VIOLATED']
print(f'\nORACLE_NOT_VIOLATED: {len(onv)}')
blockers = Counter()
for r in onv:
    ov = r.get('oracle_verdict', {})
    if isinstance(ov, dict):
        activation = ov.get('activation_receipt', {})
        if isinstance(activation, dict):
            for rc in activation.get('reason_codes', []):
                blockers[rc] += 1
print(f'Activation blockers:')
for b, c in blockers.most_common(15):
    print(f'  {c:3d}x {b}')

# Check HARNESS_FAILED
hf = [r for r in results if isinstance(r.get('oracle_verdict'), dict) and r['oracle_verdict'].get('verdict') == 'harness_failure']
print(f'\nHARNESS_FAILED: {len(hf)}')
hf_reasons = Counter()
for r in hf:
    ov = r.get('oracle_verdict', {})
    activation = ov.get('activation_receipt', {})
    if isinstance(activation, dict):
        for rc in activation.get('reason_codes', []):
            hf_reasons[rc] += 1
print(f'Harness failure reasons:')
for b, c in hf_reasons.most_common(10):
    print(f'  {c:3d}x {b}')

# Findings count
findings = d.get('findings', [])
print(f'\nFinal findings: {len(findings)}')
"""Check business_effect and control_success blockers."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Check business_effect INDETERMINATE
be_reasons = Counter()
be_control_statuses = Counter()
for r in results:
    ov = r.get('oracle_verdict', {})
    if not isinstance(ov, dict):
        continue
    activation = ov.get('activation_receipt', {})
    if not isinstance(activation, dict):
        continue
    rcs = activation.get('reason_codes', [])
    if 'OBSERVER_RECEIPT_INDETERMINATE:business_effect' not in rcs:
        continue
    for obs_receipt in r.get('observer_receipts', []):
        if isinstance(obs_receipt, dict) and obs_receipt.get('observer_id') == 'business_effect':
            rc = obs_receipt.get('reason_code', 'none')
            be_reasons[rc] += 1
            evidence = obs_receipt.get('evidence', {})
            if isinstance(evidence, dict):
                cs = evidence.get('control_status', evidence.get('status_code', 0))
                be_control_statuses[cs] += 1

print(f'business_effect INDETERMINATE reasons:')
for rc, c in be_reasons.most_common(10):
    print(f'  {c:3d}x {rc}')
print(f'business_effect control statuses:')
for cs, c in be_control_statuses.most_common(10):
    print(f'  {c:3d}x HTTP {cs}')

# Check CONTROL_SUCCESS_NOT_PROVEN
print(f'\n--- CONTROL_SUCCESS_NOT_PROVEN ---')
csp_statuses = Counter()
for r in results:
    ov = r.get('oracle_verdict', {})
    if not isinstance(ov, dict):
        continue
    activation = ov.get('activation_receipt', {})
    if not isinstance(activation, dict):
        continue
    rcs = activation.get('reason_codes', [])
    if not any('CONTROL_SUCCESS_NOT_PROVEN' in rc for rc in rcs):
        continue
    # Check control receipt status code
    for cr in r.get('contract_evidence_receipts', []):
        if isinstance(cr, dict) and cr.get('kind') == 'control':
            ev = cr.get('evidence', {})
            if isinstance(ev, dict):
                sc = ev.get('status_code', 0)
                csp_statuses[sc] += 1

print(f'Control status codes for CONTROL_SUCCESS_NOT_PROVEN:')
for cs, c in csp_statuses.most_common(10):
    print(f'  {c:3d}x HTTP {cs}')
"""Check authorization_comparison INDETERMINATE reason codes."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find experiments with authorization_comparison INDETERMINATE
auth_indeterminate = []
reason_codes = Counter()
control_statuses = Counter()

for r in results:
    ov = r.get('oracle_verdict', {})
    if not isinstance(ov, dict):
        continue
    activation = ov.get('activation_receipt', {})
    if not isinstance(activation, dict):
        continue
    rcs = activation.get('reason_codes', [])
    if 'OBSERVER_RECEIPT_INDETERMINATE:authorization_comparison' not in rcs:
        continue
    auth_indeterminate.append(r)
    
    # Check observer receipts for the reason
    for obs_receipt in r.get('observer_receipts', []):
        if isinstance(obs_receipt, dict) and obs_receipt.get('observer_id') == 'authorization_comparison':
            rc = obs_receipt.get('reason_code', 'none')
            reason_codes[rc] += 1
            evidence = obs_receipt.get('evidence', {})
            if isinstance(evidence, dict):
                cs = evidence.get('control_status', 0)
                control_statuses[cs] += 1

print(f'Experiments with auth_comparison INDETERMINATE: {len(auth_indeterminate)}')
print(f'\nObserver reason codes:')
for rc, c in reason_codes.most_common(10):
    print(f'  {c:3d}x {rc}')
print(f'\nControl status codes:')
for cs, c in control_statuses.most_common(10):
    print(f'  {c:3d}x HTTP {cs}')
"""Analyze remaining ORACLE_NOT_VIOLATED experiments."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find ORACLE_NOT_VIOLATED
onv = [r for r in results if r.get('reason_code') == 'ORACLE_NOT_VIOLATED']
print(f'ORACLE_NOT_VIOLATED: {len(onv)}')

# Check oracle verdict status
verdict_status = Counter()
verdict_verdict = Counter()
blockers = Counter()
for r in onv:
    ov = r.get('oracle_verdict', {})
    if isinstance(ov, dict):
        verdict_status[ov.get('status', 'none')] += 1
        verdict_verdict[ov.get('verdict', 'none')] += 1
        # Check activation receipt blockers
        activation = ov.get('activation_receipt', {})
        if isinstance(activation, dict):
            for rc in activation.get('reason_codes', []):
                blockers[rc] += 1

print(f'Verdict status: {dict(verdict_status)}')
print(f'Verdict verdict: {dict(verdict_verdict)}')
print(f'\nActivation blockers (top 20):')
for b, c in blockers.most_common(20):
    print(f'  {c:3d}x {b}')
