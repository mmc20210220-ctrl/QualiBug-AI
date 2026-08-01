#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import re
from pathlib import Path

root = Path('.github/delivery/role-permission')
workflow = Path('.github/workflows/one-time-role-permission-root-fix.yml').read_text(encoding='utf-8')

def inline_chunk(name: str) -> str:
    marker = f"{name} = ''.join(["
    if marker not in workflow:
        raise SystemExit(f'missing inline chunk: {name}')
    block = workflow.split(marker, 1)[1].split('])', 1)[0]
    values = []
    for line in block.splitlines():
        stripped = line.strip().rstrip(',')
        if stripped.startswith("'") and stripped.endswith("'"):
            values.append(ast.literal_eval(stripped))
    if not values:
        raise SystemExit(f'empty inline chunk: {name}')
    return ''.join(values)

encoded = ''.join([
    inline_chunk('chunk_00'),
    (root / 'chunk-01.b64').read_text(encoding='utf-8'),
    (root / 'chunk-02.b64').read_text(encoding='utf-8'),
    inline_chunk('chunk_03'),
    (root / 'chunk-04.b64').read_text(encoding='utf-8'),
    (root / 'chunk-05.b64').read_text(encoding='utf-8'),
    (root / 'chunk-06.b64').read_text(encoding='utf-8'),
    (root / 'chunk-07.b64').read_text(encoding='utf-8'),
    '\n',
])
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != 'eb56cb0c13b47aceec96643878c0cfe8af709c864f6d8e62a0c08fcd9a4e640a':
    raise SystemExit('role permission encoded patch fingerprint mismatch')
raw = gzip.decompress(base64.b64decode(encoded))
if hashlib.sha256(raw).hexdigest() != '7f64625392005c005f6fd28e9c3ac4aa9fe4d6993aede9a330f238d34c03fde2':
    raise SystemExit('role permission raw patch fingerprint mismatch')
Path('/tmp/role_permission.patch').write_bytes(raw)
PY

git apply --3way --check /tmp/role_permission.patch
git apply --3way /tmp/role_permission.patch
git diff --check

cat > /tmp/expected-role-permission-files.txt <<'EOF'
ai_test_asset_center/authorization_comparison_contract.py
ai_test_asset_center/authorization_delivery_gate.py
ai_test_asset_center/authorization_oracle_causality.py
ai_test_asset_center/behavior_ir.py
ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension_extractor_v1.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/authorization_semantics.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/explicit_fact_semantic_normalization.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/identity_resolution.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/structured_fact_compiler.py
ai_test_asset_center/historical_authorization_quarantine.py
tests/test_enterprise_understanding_actor_permission_contract.py
tests/test_enterprise_understanding_authorization_delivery_gate.py
tests/test_enterprise_understanding_authorization_oracle_causality.py
tests/test_enterprise_understanding_historical_authorization_quarantine.py
tests/test_enterprise_understanding_role_permission_executor_e2e.py
EOF
git diff --name-only | sort > /tmp/actual-role-permission-files.txt
sort -o /tmp/expected-role-permission-files.txt /tmp/expected-role-permission-files.txt
diff -u /tmp/expected-role-permission-files.txt /tmp/actual-role-permission-files.txt

git rev-parse HEAD > /tmp/role-permission-tested-base

python -m py_compile \
  ai_test_asset_center/authorization_comparison_contract.py \
  ai_test_asset_center/authorization_delivery_gate.py \
  ai_test_asset_center/authorization_oracle_causality.py \
  ai_test_asset_center/behavior_ir.py \
  ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension_extractor_v1.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/authorization_semantics.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/explicit_fact_semantic_normalization.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/identity_resolution.py \
  ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/structured_fact_compiler.py \
  ai_test_asset_center/historical_authorization_quarantine.py \
  tests/test_enterprise_understanding_actor_permission_contract.py \
  tests/test_enterprise_understanding_authorization_delivery_gate.py \
  tests/test_enterprise_understanding_authorization_oracle_causality.py \
  tests/test_enterprise_understanding_historical_authorization_quarantine.py \
  tests/test_enterprise_understanding_role_permission_executor_e2e.py

pytest -q \
  tests/test_enterprise_understanding_actor_permission_contract.py \
  tests/test_enterprise_understanding_role_permission_executor_e2e.py \
  tests/test_enterprise_understanding_authorization_oracle_causality.py \
  tests/test_enterprise_understanding_authorization_delivery_gate.py \
  tests/test_enterprise_understanding_historical_authorization_quarantine.py \
  tests/test_chinese_business_comprehension.py
pytest -q tests/test_behavior_ir_obligation_experiment.py -k 'permission or authorization or actor'

git config user.name 'QualiBug Automation'
git config user.email 'automation@qualibug.local'

python - <<'PY'
from pathlib import Path

path = Path('.github/workflows/quality-gates.yml')
text = path.read_text(encoding='utf-8')
start_marker = '  role-permission-direct:\n'
end_marker = '\n  lifecycle-root-dispatch:\n'
if start_marker not in text or end_marker not in text:
    raise SystemExit('quality gates role job boundary missing')
start = text.index(start_marker)
end = text.index(end_marker, start)
path.write_text(text[:start] + text[end + 1:], encoding='utf-8')
PY

git cat-file blob 5f9e44da83210706c5b04a12720657b480952bec > .github/workflows/one-time-e2e-root-authority-pr.yml
rm -f \
  .github/workflows/dispatch-role-permission-root-fix.yml \
  .github/workflows/dispatch-role-permission-root-fix-pr.yml \
  .github/workflows/one-time-role-permission-root-fix.yml \
  .github/role-permission-root-fix-trigger \
  .github/patches/role_permission_root_fix.patch.gz.b64
rm -rf .github/delivery/role-permission

git add -A
git commit \
  -m 'fix(auth): unify role permission authority end to end' \
  -m 'Derive source-backed role permissions once, preserve them through fact identity and Behavior IR, compile only exact actor-action-resource coordinates, and bind collection authorization evidence to content-addressed observer resource proofs without weakening explicit materialization requirements. Add real executor positive and negative end-to-end regressions.'

git fetch origin main
latest_main="$(git rev-parse origin/main)"
tested_base="$(cat /tmp/role-permission-tested-base)"
if [[ "${latest_main}" != "${tested_base}" ]]; then
  git rebase origin/main
  python -m py_compile \
    ai_test_asset_center/authorization_comparison_contract.py \
    ai_test_asset_center/authorization_delivery_gate.py \
    ai_test_asset_center/authorization_oracle_causality.py \
    ai_test_asset_center/behavior_ir.py \
    ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension_extractor_v1.py \
    ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/authorization_semantics.py \
    ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/explicit_fact_semantic_normalization.py \
    ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/identity_resolution.py \
    ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/structured_fact_compiler.py \
    ai_test_asset_center/historical_authorization_quarantine.py
  pytest -q \
    tests/test_enterprise_understanding_actor_permission_contract.py \
    tests/test_enterprise_understanding_role_permission_executor_e2e.py \
    tests/test_enterprise_understanding_authorization_oracle_causality.py \
    tests/test_enterprise_understanding_authorization_delivery_gate.py \
    tests/test_enterprise_understanding_historical_authorization_quarantine.py \
    tests/test_chinese_business_comprehension.py
  pytest -q tests/test_behavior_ir_obligation_experiment.py -k 'permission or authorization or actor'
  git diff --check
fi

git fetch origin main
git rebase origin/main
git push origin HEAD:main
