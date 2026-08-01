#!/usr/bin/env bash
set -euo pipefail

DELIVERY_BRANCH="automation/lifecycle-root-cause-delivery-20260801"
PATCH_B64=".github/patches/business_lifecycle_binding_root_fix.patch.gz.b64"
PATCH_FILE="/tmp/business-lifecycle-binding.patch"
EXPECTED_ENCODED_SHA="f588561cd56d497e9701be77b532a053a860e27405078db324ed80e0e841b308"
EXPECTED_PATCH_SHA="b54133f0e96cb9a50abd5157a5350960f6b8b40f592e851232b068df13c3a9fe"

if [[ "${GITHUB_HEAD_REF:-}" != "${DELIVERY_BRANCH}" ]]; then
  echo "unexpected lifecycle delivery branch: ${GITHUB_HEAD_REF:-missing}" >&2
  exit 1
fi

printf '%s  %s\n' "${EXPECTED_ENCODED_SHA}" "${PATCH_B64}" | sha256sum -c -
base64 -d "${PATCH_B64}" | gzip -d > "${PATCH_FILE}"
printf '%s  %s\n' "${EXPECTED_PATCH_SHA}" "${PATCH_FILE}" | sha256sum -c -

git apply --3way --check "${PATCH_FILE}"
git apply --3way "${PATCH_FILE}"

cat > /tmp/business-lifecycle-binding-files.txt <<'EOF'
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/binding_identity_projection.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/implementation_binding.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/implementation_binding_governance.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/observer_binding_identity_projection.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/runtime_materialization.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/runtime_plan.py
tests/test_entity_lifecycle_behavior_binding.py
EOF
sort -o /tmp/business-lifecycle-binding-files.txt /tmp/business-lifecycle-binding-files.txt
xargs git add -- < /tmp/business-lifecycle-binding-files.txt
git diff --cached --name-only | sort > /tmp/business-lifecycle-binding-actual.txt
diff -u /tmp/business-lifecycle-binding-files.txt /tmp/business-lifecycle-binding-actual.txt
git diff --cached --check

python -m py_compile \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/implementation_binding.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/implementation_binding_governance.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/binding_identity_projection.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/observer_binding_identity_projection.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/runtime_plan.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/runtime_materialization.py \
  tests/test_entity_lifecycle_behavior_binding.py

python -m pytest -q \
  tests/test_entity_lifecycle_behavior_binding.py \
  tests/test_behavior_implementation_binding_object_identity.py \
  tests/test_behavior_implementation_binding_relationships.py \
  tests/test_runtime_plan_v1.py \
  tests/test_runtime_materialization_v1.py \
  tests/test_binding_identity_projection.py \
  tests/test_event_observer_implementation_mainline.py

python - <<'PY'
from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_governed_behavior_implementation_bindings,
)

asset = build_enterprise_business_knowledge_asset("benchmark_mall")
model = asset.get("enterprise_understanding_model") or {}
behaviors = [
    dict(row)
    for row in model.get("business_behaviors") or []
    if isinstance(row, dict) and row.get("status") == "CONFIRMED"
]
bindings, unknowns, conflicts, gate = (
    build_governed_behavior_implementation_bindings(asset, behaviors)
)
metrics = gate.get("metrics") or {}
rows = [
    row
    for binding in bindings
    for row in binding.get("lifecycle_observer_bindings") or []
    if isinstance(row, dict) and row.get("status") == "BOUND"
]
targets = {str(row.get("observer_interface_id") or "") for row in rows}
strategies = {str(row.get("observation_strategy") or "") for row in rows}
assert not conflicts, conflicts
assert int(metrics.get("scenario_ready_binding_count") or 0) >= 3, metrics
assert int(metrics.get("bound_mandatory_outcome_count") or 0) >= 3, metrics
assert len(rows) >= 3, rows
assert "COLLECTION_DELTA_CREATED_ENTITY" in strategies, strategies
assert "REQUEST_IDENTITY_ABSENT_FROM_COLLECTION" in strategies, strategies
assert any("products" in value for value in targets), targets
assert any("cart/items" in value for value in targets), targets
assert any("users/addresses" in value for value in targets), targets
print(
    {
        "confirmed": len(behaviors),
        "scenario_ready": metrics.get("scenario_ready_binding_count"),
        "bound_outcomes": metrics.get("bound_mandatory_outcome_count"),
        "lifecycle_observers": len(rows),
        "unknowns": len(unknowns),
    }
)
PY

git checkout -- qualibug_ai.egg-info 2>/dev/null || true
git checkout -- .
git clean -fd
git diff --exit-code
git diff --cached --name-only | sort > /tmp/business-lifecycle-binding-after.txt
diff -u /tmp/business-lifecycle-binding-files.txt /tmp/business-lifecycle-binding-after.txt
git diff --cached --check

git config user.name "mmc20210220-ctrl"
git config user.email "mmc20210220@gmail.com"
git commit \
  -m "fix(binding): close entity lifecycle outcome observers" \
  -m "Bind source-declared creation and deletion outcomes to exact collection observers, carry one ObserverBinding identity through Scenario, Execution Contract, Runtime Plan and materialization, and keep HTTP status or collection count alone ineligible as lifecycle proof."

expected_head="$(git ls-remote origin "refs/heads/${DELIVERY_BRANCH}" | awk '{print $1}')"
git push \
  --force-with-lease="refs/heads/${DELIVERY_BRANCH}:${expected_head}" \
  origin "HEAD:${DELIVERY_BRANCH}"
