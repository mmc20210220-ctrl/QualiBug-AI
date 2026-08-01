#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BASE_COMPILER_BLOB="15a2157e71f0dd9c81eee16eb378f32db7e59475"
BASE_EXTRACTOR_BLOB="fa9bbd906aa5b94abf9ffdd29979fb1d27d1a16c"
PAYLOAD=".github/patches/chinese_explicit_fact_generalization_payload.tar.gz.b64"
WORKDIR="${RUNNER_TEMP:-/tmp}/chinese-explicit-fact-generalization"
QUALITY_GATE_RESTORE_COMMIT="41113338432e09bfbb278fbed459eaa49077425b"
REGISTERED_WORKFLOW_TRIGGER_COMMIT="71ea412b7e66e61e6edf4c2cb2f10d7a6e84e7b4"

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR/payload"

test "$(git hash-object ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/structured_fact_compiler.py)" = "$BASE_COMPILER_BLOB"
test "$(git hash-object ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension_extractor_v1.py)" = "$BASE_EXTRACTOR_BLOB"
echo "83af9c0f6f23dd06fde6f82d33f51fb62204e28dc4568a93d9dbd9513566ff0a  $PAYLOAD" | sha256sum -c -
base64 -d "$PAYLOAD" > "$WORKDIR/payload.tar.gz"
echo "8520879e0f2b2bf6ba6918725d6ded8f25b40ff675565aab7d0c8e0ebf1cd451  $WORKDIR/payload.tar.gz" | sha256sum -c -
tar -xzf "$WORKDIR/payload.tar.gz" -C "$WORKDIR/payload"

python "$WORKDIR/payload/apply.py"
cp "$WORKDIR/payload/test_chinese_explicit_fact_language_mutations.py" tests/test_chinese_explicit_fact_language_mutations.py

git diff --check
cat > "$WORKDIR/expected-files.txt" <<'EOF'
ai_test_asset_center/enterprise_knowledge_center/_chinese_business_comprehension_extractor_v1.py
ai_test_asset_center/enterprise_knowledge_center/enterprise_understanding/structured_fact_compiler.py
tests/test_chinese_explicit_fact_language_mutations.py
EOF
git status --short | awk '{print $2}' | grep -v '^.github/' | sort > "$WORKDIR/actual-files.txt"
sort -o "$WORKDIR/expected-files.txt" "$WORKDIR/expected-files.txt"
diff -u "$WORKDIR/expected-files.txt" "$WORKDIR/actual-files.txt"

python -m compileall -q ai_test_asset_center/enterprise_knowledge_center benchmark_evaluator/enterprise_understanding
python -m pytest -q tests/test_chinese_explicit_fact_language_mutations.py
python -m pytest -q \
  tests/test_chinese_business_comprehension.py \
  tests/test_enterprise_understanding_structure_first_facts.py \
  tests/test_enterprise_understanding_chinese_explicit_fact_baseline.py \
  tests/test_enterprise_understanding_explicit_fact_bug_dependency.py \
  tests/test_enterprise_understanding_explicit_fact_commit_status.py \
  tests/test_enterprise_understanding_ground_truth_quarantine.py \
  tests/test_enterprise_understanding_fact_probe_policy.py \
  tests/test_enterprise_understanding_fact_slot_document.py \
  tests/test_enterprise_understanding_fact_slots.py \
  tests/test_enterprise_understanding_fact_slot_report.py \
  tests/test_enterprise_understanding_post_compile_governance.py \
  tests/test_enterprise_understanding_public_import_contract.py \
  tests/test_enterprise_understanding_typed_fact_authority.py \
  tests/test_enterprise_understanding_typed_relation_projection.py \
  tests/test_chinese_explicit_fact_language_mutations.py

BASELINE_WORKSPACE="$WORKDIR/baseline-workspace"
BASELINE_OUTPUT="$WORKDIR/baseline-output"
rm -rf "$BASELINE_WORKSPACE" "$BASELINE_OUTPUT"
python -m benchmark_evaluator.enterprise_understanding.chinese_explicit_fact_baseline \
  --workspace-root "$BASELINE_WORKSPACE" \
  --output "$BASELINE_OUTPUT"
python - "$BASELINE_OUTPUT/chinese_explicit_fact_baseline_summary.json" <<'PY'
import json
import sys
from pathlib import Path
summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
metrics = summary["metrics"]
assert summary["status"] == "PASS", summary
assert metrics["annotated_fact_count"] == 16, metrics
assert metrics["exact_fact_count"] == 16, metrics
assert metrics["measured_slot_count"] == 71, metrics
assert metrics["exact_slot_count"] == 71, metrics
assert metrics["fact_recall"] == 1.0, metrics
assert metrics["slot_exact_accuracy"] == 1.0, metrics
assert metrics["p0_exact_fact_recall"] == 1.0, metrics
assert metrics["source_locator_exact_accuracy"] == 1.0, metrics
assert metrics["accepted_fact_precision"] == 1.0, metrics
assert metrics["false_accepted_fact_count"] == 0, metrics
PY

git show "$QUALITY_GATE_RESTORE_COMMIT:.github/workflows/quality-gates.yml" > .github/workflows/quality-gates.yml
git show "$REGISTERED_WORKFLOW_TRIGGER_COMMIT^:.github/workflows/chinese-explicit-fact-baseline.yml" > .github/workflows/chinese-explicit-fact-baseline.yml
git checkout -- qualibug_ai.egg-info 2>/dev/null || true
rm -rf .github/delivery/chinese-explicit-fact-generalization
rm -f \
  .github/chinese-explicit-fact-generalization-trigger \
  .github/chinese-explicit-fact-generalization-failure.txt \
  .github/patches/chinese_explicit_fact_generalization_payload.tar.gz.b64 \
  .github/workflows/one-time-chinese-explicit-fact-generalization.yml \
  .github/workflows/one-time-export-current-main.yml \
  .github/source-export-trigger \
  .github/source-export-run-id \
  .github/workflows/one-time-chinese-fact-mutation-probe.yml \
  .github/chinese-fact-mutation-probe-trigger \
  .github/workflows/one-time-chinese-fact-mutation-probe-v2.yml \
  .github/chinese-fact-mutation-probe-v2-trigger \
  .github/chinese_fact_mutation_probe.py \
  .github/chinese-fact-mutation-run-id \
  .github/chinese-fact-mutation-report.json \
  .github/noop-tree-probe \
  .github/noop-tree-probe-2

git config user.name "mmc20210220-ctrl"
git config user.email "mmc20210220@gmail.com"
git add -A
git diff --cached --check
git commit -m "fix(understanding): generalize Chinese explicit fact semantics"

for attempt in 1 2 3 4 5; do
  git fetch origin main
  if ! git merge-base --is-ancestor origin/main HEAD; then
    git rebase origin/main
    python -m pytest -q tests/test_chinese_explicit_fact_language_mutations.py
    rm -rf "$WORKDIR/reverify-workspace" "$WORKDIR/reverify-output"
    python -m benchmark_evaluator.enterprise_understanding.chinese_explicit_fact_baseline \
      --workspace-root "$WORKDIR/reverify-workspace" \
      --output "$WORKDIR/reverify-output"
  fi
  if git push origin HEAD:main; then exit 0; fi
done
exit 1
