"""Check experiment compile receipts for Project B scan."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

d = json.load(open('_scan_result_project_b.json', 'r', encoding='utf-8'))
v12 = d.get('v12', {})
ec = v12.get('experiment_compile', {})

print(f"compiled_count: {ec.get('compiled_count')}")
print(f"blocked_count: {ec.get('blocked_count')}")
print(f"block_reason_counts: {json.dumps(ec.get('block_reason_counts', {}), indent=2)}")

blocked = ec.get('blocked_experiments', [])
print(f"\nblocked_experiments list: {len(blocked)}")
print(f"experiments list: {len(ec.get('experiments', []))}")
print(f"all_experiments: {len(ec.get('all_experiments', []))}")

print("\nSample blocked experiments:")
for b in blocked[:3]:
    if isinstance(b, dict):
        cr = b.get('compile_receipt', {})
        print(f"  obligation: {b.get('obligation_id', '?')}")
        print(f"  reason: {cr.get('reason_code')} - {cr.get('detail', '')[:150]}")
        print()

# Check obligation compile_status
to = d.get('test_obligations', {})
obls = to.get('obligations', [])
compile_statuses = {}
for o in obls:
    cs = o.get('compile_status', '?')
    compile_statuses[cs] = compile_statuses.get(cs, 0) + 1
print(f"\nObligation compile_status distribution:")
for k, v in sorted(compile_statuses.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# Check actors in behavior_ir
bir = v12.get('behavior_ir', {})
actors = bir.get('actors', [])
print(f"\nBehavior IR actors: {len(actors)}")
for a in actors[:6]:
    if isinstance(a, dict):
        print(f"  {a.get('id')}: role={a.get('role')}, secret_ref={a.get('credential_secret_ref', a.get('secret_ref', 'NONE'))}")

# Check obligation_plan
op = v12.get('obligation_plan', {})
print(f"\nObligation plan:")
print(f"  budget: {op.get('budget')}")
print(f"  selected_count: {op.get('selected_count')}")
print(f"  pending_count: {op.get('pending_count')}")
print(f"  cold_start_reason: {op.get('cold_start_reason')}")
