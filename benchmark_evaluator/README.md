# QualiBug Benchmark Evaluator

This directory is evaluator-side infrastructure. It may invoke the external benchmark target
and its scorer, but none of its hidden-ground-truth inputs may enter
`ai_test_asset_center` runtime context.

## 1. Produce a funnel run

Run the existing funnel benchmark against one QualiBug checkout:

```bash
python -m benchmark_evaluator.funnel_benchmark \
  --root /path/to/QualiBug-AI \
  --project qualibug_131 \
  --base-url http://127.0.0.1:8000 \
  --mode candidate \
  --environment-ref benchmark-nonprod \
  --manifest-path /path/to/QualiBug-Enterprise-Benchmark/BENCHMARK_MANIFEST.json \
  --target-fingerprint <approved-target-fingerprint>
```

The runner writes evaluator inputs under:

```text
platform_outputs/<project>/_funnel_runs/
```

For each mode, retain at least:

```text
<mode>.result.json
<mode>.evaluation_submission.json
```

The product report remains `NOT_MEASURED` until the external target scorer runs. This is
intentional: product findings cannot label themselves true or false.

## 2. Produce baseline and candidate runs

Use the same:

- enterprise source snapshot;
- benchmark target checkout;
- target fingerprint;
- ground-truth policy fingerprint;
- environment and accounts;
- evaluator policy except for the intentional code/policy change under comparison.

Generate one pair of result/submission files from the baseline checkout and another pair from
the candidate checkout. Do not copy the benchmark target's hidden registry into either product
workspace.

## 3. Score and compare in one evaluator workflow

The external benchmark repository exposes:

```text
scripts/score_qualibug_output.py
```

Its CLI accepts one evaluation submission and prints aggregate JSON. Run both scores through
one immutable benchmark checkout and one scorer script fingerprint:

```bash
python -m benchmark_evaluator.run_external_scored_comparison \
  --benchmark-repo /path/to/QualiBug-Enterprise-Benchmark \
  --baseline-result /artifacts/baseline.result.json \
  --baseline-submission /artifacts/baseline.evaluation_submission.json \
  --candidate-result /artifacts/candidate.result.json \
  --candidate-submission /artifacts/candidate.evaluation_submission.json \
  --baseline-label <baseline-commit-sha> \
  --candidate-label <candidate-commit-sha> \
  --output-dir /artifacts/scored-comparison
```

The workflow emits:

```text
baseline.external_score.json
candidate.external_score.json
baseline.scored_snapshot.json
candidate.scored_snapshot.json
comparison.json
comparison.md
workflow_receipt.json
```

## Fail-closed comparison rules

A quality delta is emitted only when the two runs prove the same immutable target or
ground-truth fingerprint. A readable target name alone is insufficient.

The workflow blocks when:

- benchmark target or ground-truth fingerprints disagree;
- ground-truth totals disagree;
- score TP/FP/FN identities are inconsistent;
- the scorer script is outside the benchmark repository;
- the scorer script changes between baseline and candidate scoring;
- the benchmark repository HEAD changes during the comparison;
- the external scorer fails or returns non-JSON output.

The comparison derives precision, recall and F1 only from the external scorer's aggregate
TP/FP/FN. The comparator never opens the hidden answer registry.

## What this workflow does not prove

A successful comparison proves only the externally scored change on the declared target and
source snapshot. It does not prove universal cross-industry recall, production safety or
coverage of defect classes absent from the benchmark.

UI, event and performance obligations are reported separately in the loss/family deltas. Their
presence is not automatically a true positive; only the external scorer may establish that.
