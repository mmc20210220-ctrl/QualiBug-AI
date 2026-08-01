#!/usr/bin/env bash
set -euo pipefail

ROOT=.github/delivery/single-object-complex
sha256sum --check "$ROOT/segments.sha256"

cat "$ROOT"/p01-*.b64 > /tmp/part-01.b64
cat "$ROOT"/p08-*.b64 > /tmp/part-08.b64
echo 'db93ceb61c51b88072be81258867b48b7afaecd7e6d9497b3746becd49c989c1  /tmp/part-01.b64' | sha256sum --check
echo '711dfe7deb1ca9b3b43c9551ff9359454448cfb98eabd1a88af68208a6f6df26  /tmp/part-08.b64' | sha256sum --check

cat \
  "$ROOT/part-00.b64" \
  /tmp/part-01.b64 \
  "$ROOT/part-02.b64" \
  "$ROOT/part-03.b64" \
  "$ROOT/part-04.b64" \
  "$ROOT/part-05.b64" \
  "$ROOT/part-06.b64" \
  "$ROOT/part-07.b64" \
  /tmp/part-08.b64 \
  "$ROOT/part-09.b64" \
  | base64 --decode \
  | gzip --decompress \
  > /tmp/single-object-complex.patch
echo '5e5eb773c380771e0dc3b3d68c2c3147df2b7e167e039324403ae6a70733eeeb  /tmp/single-object-complex.patch' | sha256sum --check

git config user.name 'OpenAI Codex'
git config user.email 'codex@openai.com'
git fetch origin main
git merge --no-edit origin/main
git am -3 --ignore-space-change --ignore-whitespace /tmp/single-object-complex.patch
git diff --check origin/main...HEAD

python -m compileall -q ai_test_asset_center tests
pytest -q \
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

git rm -r "$ROOT"
git rm .github/workflows/apply-single-object-complex-lifecycle.yml
git commit -m 'chore(process): remove single-object delivery assets'
git push origin HEAD:one-shot/single-object-complex-lifecycle
