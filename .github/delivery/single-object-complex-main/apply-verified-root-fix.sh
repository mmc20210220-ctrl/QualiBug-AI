#!/usr/bin/env bash
set -euo pipefail

PAYLOAD_REF='one-shot/single-object-complex-lifecycle'
PAYLOAD_ROOT='.github/delivery/single-object-complex'
TESTED_BASE_FILE='/tmp/single-object-tested-base'
VERIFY_SCRIPT='/tmp/verify-single-object-complex.sh'

reassemble_patch() {
  git fetch origin "${PAYLOAD_REF}"
  : > /tmp/part-01.b64
  : > /tmp/part-08.b64
  for seg in 0 1 2 3 4; do
    git show "origin/${PAYLOAD_REF}:${PAYLOAD_ROOT}/p01-${seg}.b64" >> /tmp/part-01.b64
    git show "origin/${PAYLOAD_REF}:${PAYLOAD_ROOT}/p08-${seg}.b64" >> /tmp/part-08.b64
  done
  echo 'db93ceb61c51b88072be81258867b48b7afaecd7e6d9497b3746becd49c989c1  /tmp/part-01.b64' | sha256sum -c -
  echo '711dfe7deb1ca9b3b43c9551ff9359454448cfb98eabd1a88af68208a6f6df26  /tmp/part-08.b64' | sha256sum -c -
  {
    git show "origin/${PAYLOAD_REF}:${PAYLOAD_ROOT}/part-00.b64"
    cat /tmp/part-01.b64
    for part in 02 03 04 05 06 07; do
      git show "origin/${PAYLOAD_REF}:${PAYLOAD_ROOT}/part-${part}.b64"
    done
    cat /tmp/part-08.b64
    git show "origin/${PAYLOAD_REF}:${PAYLOAD_ROOT}/part-09.b64"
  } | base64 --decode | gzip --decompress > /tmp/single-object-complex.patch
  echo '5e5eb773c380771e0dc3b3d68c2c3147df2b7e167e039324403ae6a70733eeeb  /tmp/single-object-complex.patch' | sha256sum -c -
}

verify_scope() {
  cat > /tmp/expected-single-object-files.txt <<'EOF'
ai_test_asset_center/_contract_oracles_mechanics.py
ai_test_asset_center/_experiment_outcome_finalizer_scope_mechanics.py
ai_test_asset_center/compensation_relation_resolver.py
ai_test_asset_center/experiment_compiler_base.py
ai_test_asset_center/experiment_compiler_obligation.py
ai_test_asset_center/experiment_compiler_obligation_core.py
ai_test_asset_center/experiment_lifecycle_runtime.py
ai_test_asset_center/experiment_outcome_finalizer_core.py
ai_test_asset_center/experiment_plan_executor.py
ai_test_asset_center/experiment_plan_step_executor.py
ai_test_asset_center/experiment_runtime_support.py
ai_test_asset_center/multi_step_protocol.py
ai_test_asset_center/process_graph_cleanup_equivalence_core.py
ai_test_asset_center/process_graph_cleanup_executor_core.py
ai_test_asset_center/process_graph_read_runtime.py
ai_test_asset_center/process_graph_runtime.py
ai_test_asset_center/process_graph_write_contract_core.py
ai_test_asset_center/process_step_bundle_audit.py
ai_test_asset_center/process_step_evidence_scope_audit.py
ai_test_asset_center/process_step_execution.py
ai_test_asset_center/process_step_observer.py
ai_test_asset_center/process_step_receipt_scope.py
ai_test_asset_center/process_step_semantic_projection.py
ai_test_asset_center/process_step_semantic_view.py
ai_test_asset_center/registered_observer_evidence_bridge.py
ai_test_asset_center/write_reversibility_contract.py
docs/SINGLE_OBJECT_COMPLEX_FLOW_ROOT_OPTIMIZATION_2026-08-01.md
tests/single_object_complex_flow_support.py
tests/test_process_graph_event_transition_ledger.py
tests/test_process_graph_wait_contract.py
tests/test_process_step_execution_semantics.py
tests/test_process_step_ledger_authority.py
tests/test_process_step_semantic_view.py
tests/test_single_object_complex_flow_compiler.py
tests/test_single_object_complex_flow_real_network_e2e.py
EOF
  git diff --name-only "$(cat "${TESTED_BASE_FILE}")..HEAD" | sort > /tmp/actual-single-object-files.txt
  sort -o /tmp/expected-single-object-files.txt /tmp/expected-single-object-files.txt
  diff -u /tmp/expected-single-object-files.txt /tmp/actual-single-object-files.txt
}

cat > "${VERIFY_SCRIPT}" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=.
python -m compileall -q ai_test_asset_center tests
python -m pytest -q \
  tests/test_single_object_complex_flow_compiler.py \
  tests/test_single_object_complex_flow_real_network_e2e.py \
  tests/test_process_graph_runtime.py \
  tests/test_process_graph_plan_executor.py \
  tests/test_process_graph_wait_contract.py \
  tests/test_process_graph_event_transition_ledger.py \
  tests/test_process_graph_rollback_contract.py \
  tests/test_process_graph_dependency_rollback.py \
  tests/test_process_graph_write_rollback_integration.py \
  tests/test_process_graph_cleanup_equivalence_scope.py \
  tests/test_process_graph_cleanup_bundle_layers.py \
  tests/test_write_reversibility_contract.py \
  tests/test_experiment_lifecycle_fixture_authority.py \
  tests/test_process_step_receipt_scope_runtime.py \
  tests/test_process_step_typed_observer_scope.py \
  tests/test_process_step_semantic_view.py \
  tests/test_process_step_semantic_projection.py \
  tests/test_process_step_execution_semantics.py \
  tests/test_process_step_fact_separation.py \
  tests/test_process_step_ledger_authority.py \
  tests/test_process_step_bundle_receipt_separation.py \
  tests/test_process_step_graph_aggregate_scope.py \
  tests/test_cross_system_process_graph_closure.py \
  tests/test_v150_multi_step_protocol.py
git diff --check
SH
chmod +x "${VERIFY_SCRIPT}"

git config user.name 'QualiBug Automation'
git config user.email 'automation@qualibug.local'
git fetch origin main
git reset --hard origin/main
git rev-parse HEAD > "${TESTED_BASE_FILE}"
reassemble_patch
git am -3 --ignore-space-change --ignore-whitespace /tmp/single-object-complex.patch
verify_scope
"${VERIFY_SCRIPT}"

# Remove all temporary delivery authorities from the final source commit.
python - <<'PY'
from pathlib import Path
path = Path('.github/workflows/quality-gates.yml')
text = path.read_text(encoding='utf-8')
start = '  # BEGIN SINGLE_OBJECT_COMPLEX_DELIVERY\n'
end = '  # END SINGLE_OBJECT_COMPLEX_DELIVERY\n'
if start not in text or end not in text:
    raise SystemExit('single-object quality-gate block missing')
left, rest = text.split(start, 1)
_, right = rest.split(end, 1)
path.write_text(left + right, encoding='utf-8')
PY
rm -f .github/workflows/one-time-single-object-complex-delivery.yml
rm -f .github/single-object-complex-delivery.trigger
rm -rf .github/delivery/single-object-complex-main
git add -A
git commit --amend --no-edit

# Rebase on the execution-time latest main and re-run if main moved.
git fetch origin main
latest_main="$(git rev-parse origin/main)"
if [[ "${latest_main}" != "$(cat "${TESTED_BASE_FILE}")" ]]; then
  git rebase origin/main
  "${VERIFY_SCRIPT}"
fi

git fetch origin main
git rebase origin/main
git push origin HEAD:main
