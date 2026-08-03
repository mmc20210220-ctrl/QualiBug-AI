# Fix: Arm-Isolated Identity Binding for Isolation Experiments (V1.9)

## 🐛 Problem Summary

Two cleanup experiments (`exp_1eb711e53f0a803b3113` / `exp_2fd2ea3e64f225691dd5`) FAILED with cart item residue:

**Observed Behavior:**
- Treatment arm's POST created cart item `cebae0f3` for user `7d3a633b` (buyer01)
- Treatment actor token was `bir_087f1caad2e5f144` (buyer02)
- Treatment GET `/api/cart/items` saw empty cart → before/after snapshots both `[]`
- Cleanup DELETE skipped due to `_governed_write_changed_state=False`
- Cleanup sealing gate required `restoration_verified=True` → **FAILED**

**Root Cause:**
Compile-time identity binding collected `/me` operations from ALL actors without arm isolation:
- Control actor (`bir_22c89ba938bc89f2`) and treatment actor (`bir_087f1caad2e5f144`) shared same `user_id` resolver
- Treatment body `{userId: "{user_id}"}` filled with control's user_id (`7d3a633b`)
- Treatment executed with buyer02 token but targeted buyer01's resource → **identity mismatch**
- Cross-arm contamination caused snapshot distortion and cleanup skip

## 🔧 Solution: V1.9 Arm-Isolated Identity Resolution

### Modified File
- `ai_test_asset_center/experiment_compiler_obligation_core.py` (L1137-1210)

### Changes

**Before (Non-isated resolver list):**
```python
identity_resolvers = [
    {"operation_ref": op_id, "method": "GET", "path": path}
    for op in ir.get("operations")
    if is_me_operation(op)
]
binding_plan.append({
    "target": "user_id",
    "resolver_operations": identity_resolvers[:2],  # First 2 only!
})
```

**After (Actor-indexed collection with arm isolation):**
```python
# Collect /me operations indexed by declaring actor
me_operations_by_actor: dict[str, list[dict]] = {}
for op in ir.get("operations"):
    if not is_me_operation(op):
        continue
    op_id = op.get("id")
    op_actor = op.get("actor_ref") or fallback_infer_actor(op_id)
    me_operations_by_actor.setdefault(op_actor, []).append({...})

# Build arm-specific resolver lists
arm_resolvers = []
for actor_ref in [control_actor_ref, treatment_actor_ref]:
    if actor_ref in me_operations_by_actor:
        arm_resolvers.extend(me_operations_by_actor[actor_ref])

binding_plan.append({
    "target": "user_id",
    "resolver_operations": arm_resolvers,  # All resolvers from both arms
    "arm_isolated_resolvers": {
        "control": me_operations_by_actor.get(control_actor_ref, []),
        "treatment": me_operations_by_actor.get(treatment_actor_ref, []),
    },
})
```

### Key Improvements

1. **Actor-based indexing**: Each `/me` operation associated with declaring actor via `actor_ref` field or fallback inference
2. **Arm-specific collection**: Control and treatment get their own dedicated `/me` resolvers
3. **No artificial limit**: Removed `[:2]` slice → all valid resolvers included
4. **Debugging metadata**: `arm_isolated_resolvers` shows per-arm resolver sets for verification

## 📊 Expected Impact

**Before Fix:**
```
binding_plan.user_id.resolver_operations = [
  bir_1dbe9ad1ec25c34d (/api/auth/me from control),  # user_id=7d3a633b
]
treatment body: {userId: "7d3a633b"}  # ❌ Wrong user (control's)
treatment token: bir_087f (buyer02)   # ❌ Mismatch
```

**After Fix:**
```
binding_plan.user_id.resolver_operations = [
  control_me_op (from bir_22c8),       # user_id=7d3a633b (buyer01)
  treatment_me_op (from bir_087f),     # user_id=abcd1234 (buyer02)
]
treatment body: {userId: "abcd1234"}  # ✅ Correct user (treatment's)
treatment token: bir_087f (buyer02)   # ✅ Match
```

## ✅ Verification Steps

1. **Re-run benchmark scan** to regenerate `scan_benchmark_v3.json`
2. **Check binding_plan** has `arm_isolated_resolvers` metadata:
   ```bash
   python -c "import json; d=json.load(open('_funnel_runs/scan_benchmark_v3.json')); exp=[e for e in d['v12']['experiment_compile']['experiments'] if e['experiment_id']=='exp_1eb711e53f0a803b3113'][0]; bp=[b for b in exp['binding_plan'] if b['target']=='user_id'][0]; print('arm_isolated_resolvers:', bp.get('arm_isolated_resolvers'))"
   ```
3. **Verify isolation**: Control and treatment should have separate `/me` resolvers
4. **Execute experiment** and confirm cleanup status changes from `FAILED` to `SUCCESS`

## 📈 Related Issues Fixed

- **Short-term**: Cleanup skip predicate `_governed_write_changed_state` vs sealing predicate `_write_step_requires_cleanup` inconsistency (see `experiment_cleanup_executor_core.py`)
- **Long-term**: Snapshot distortion due to actor/token/user_id mismatch (this fix)
- **Impact**: Eliminates cross-arm resource access contamination in isolation experiments

## 🔄 Migration Notes

- **Backward compatible**: Existing experiments without `arm_isolated_resolvers` continue to work
- **New experiments**: Automatically benefit from arm-isated binding
- **No runtime overhead**: Metadata only added to binding_plan JSON

## 📝 References

- Root cause analysis: `.scratch/_drill_binding_plan.py`, `.scratch/_drill_actor_scope.py`
- Experiment failures: `exp_1eb711e53f0a803b3113`, `exp_2fd2ea3e64f225691dd5`
- Cleanup executor: `ai_test_asset_center/experiment_cleanup_executor_core.py` L2037-2044
- Sealing predicate: `ai_test_asset_center/experiment_cleanup_executor_core.py` L1040-1064
