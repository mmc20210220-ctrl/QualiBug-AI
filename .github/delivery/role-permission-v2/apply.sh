#!/usr/bin/env bash
set -euo pipefail

root=.github/delivery/role-permission-v2
cat "$root"/chunk-00.b64 "$root"/chunk-01.b64 "$root"/chunk-02.b64 "$root"/chunk-03.b64 \
    "$root"/chunk-04.b64 "$root"/chunk-05.b64 "$root"/chunk-06.b64 "$root"/chunk-07.b64 \
    > /tmp/role-permission-v2.patch.gz.b64

echo '1cc451ffc4cef2b46327f7230c6b9b8e37498eed4186bd840d2834b0b1689550  /tmp/role-permission-v2.patch.gz.b64' | sha256sum -c -
base64 -d /tmp/role-permission-v2.patch.gz.b64 | gzip -d > /tmp/role-permission-v2.patch
echo '905676ec69a644521f39ed364788e6947b5df782ed10b79976947f60ba74981f  /tmp/role-permission-v2.patch' | sha256sum -c -

git apply --3way --check /tmp/role-permission-v2.patch
git apply --3way /tmp/role-permission-v2.patch
git diff --check

cat > /tmp/expected-role-permission-files <<'EOF'
ai_test_asset_center/authorization_comparison_contract.py
ai_test_asset_center/authorization_delivery_gate.py
ai_test_asset_center/authorization_oracle_causality.py
ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension/__init__.py
ai_test_asset_center/enterprise_knowledge_center/composition.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/authorization_semantics.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/explicit_fact_semantic_normalization.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/fact_permission_matrix.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/identity_resolution.py
ai_test_asset_center/historical_authorization_quarantine.py
tests/test_enterprise_understanding_actor_permission_contract.py
tests/test_enterprise_understanding_authorization_delivery_gate.py
tests/test_enterprise_understanding_authorization_oracle_causality.py
tests/test_enterprise_understanding_historical_authorization_quarantine.py
tests/test_enterprise_understanding_role_permission_executor_e2e.py
EOF
git diff --name-only | sort > /tmp/actual-role-permission-files
sort -o /tmp/expected-role-permission-files /tmp/expected-role-permission-files
diff -u /tmp/expected-role-permission-files /tmp/actual-role-permission-files

python -m py_compile \
  ai_test_asset_center/authorization_comparison_contract.py \
  ai_test_asset_center/authorization_delivery_gate.py \
  ai_test_asset_center/authorization_oracle_causality.py \
  ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension/__init__.py \
  ai_test_asset_center/enterprise_knowledge_center/composition.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/authorization_semantics.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/explicit_fact_semantic_normalization.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/fact_permission_matrix.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/identity_resolution.py \
  ai_test_asset_center/historical_authorization_quarantine.py \
  tests/test_enterprise_understanding_actor_permission_contract.py \
  tests/test_enterprise_understanding_authorization_delivery_gate.py \
  tests/test_enterprise_understanding_authorization_oracle_causality.py \
  tests/test_enterprise_understanding_historical_authorization_quarantine.py \
  tests/test_enterprise_understanding_role_permission_executor_e2e.py

pytest -q \
  tests/test_enterprise_understanding_actor_permission_contract.py \
  tests/test_enterprise_understanding_authorization_coordinate_edge.py \
  tests/test_enterprise_understanding_authorization_unknown_propagation.py \
  tests/test_enterprise_understanding_credential_identity_coordinates.py \
  tests/test_enterprise_understanding_experiment_actor_credential_identity.py \
  tests/test_enterprise_understanding_responsibility_authorization_boundary.py \
  tests/test_enterprise_understanding_role_permission_executor_e2e.py \
  tests/test_enterprise_understanding_authorization_oracle_causality.py \
  tests/test_enterprise_understanding_authorization_delivery_gate.py \
  tests/test_enterprise_understanding_historical_authorization_quarantine.py \
  tests/test_authorization_comparison_identity_symmetry.py
pytest -q tests/test_behavior_ir_obligation_experiment.py -k 'permission or authorization or actor'

# Remove only temporary role-permission delivery assets. Preserve unrelated lifecycle delivery.
python - <<'PY'
from pathlib import Path
p = Path('.github/workflows/quality-gates.yml')
if p.exists():
    text = p.read_text(encoding='utf-8')
    start = '  role-permission-direct:\n'
    end = '\n  lifecycle-root-dispatch:\n'
    if start in text and end in text:
        i = text.index(start)
        j = text.index(end, i)
        text = text[:i] + text[j + 1:]
        p.write_text(text, encoding='utf-8')
PY

git cat-file blob 5f9e44da83210706c5b04a12720657b480952bec > .github/workflows/one-time-e2e-root-authority-pr.yml
rm -f \
  .github/workflows/dispatch-role-permission-root-fix.yml \
  .github/workflows/dispatch-role-permission-root-fix-pr.yml \
  .github/workflows/one-time-role-permission-root-fix.yml \
  .github/workflows/role-permission-main-delivery.yml \
  .github/role-permission-root-fix-trigger \
  .github/role-permission-main-delivery.trigger \
  .github/patches/role_permission_root_fix.patch.gz.b64
rm -rf .github/delivery/role-permission .github/delivery/role-permission-v2

git config user.name 'QualiBug Automation'
git config user.email 'automation@qualibug.local'
git add -A
git commit \
  -m 'fix(auth): unify role permission authority end to end' \
  -m 'Project source-backed role permissions into the existing permission-matrix authority, preserve authorization identity through the enterprise model, and bind collection authorization evidence to content-addressed observer resource proofs without weakening explicit materialization requirements. Add real executor positive and negative end-to-end regressions.'

git fetch origin main
git rebase origin/main
git push origin HEAD:main
