# Enterprise Identity Benchmark Contract

## Purpose

Measure whether cross-source business-object mentions are merged into the correct
enterprise identities. The benchmark evaluates exact source occurrences, not names.
Two occurrences both named `订单` remain different annotation units.

## 1. Generate the blind annotation manifest

Run the normal enterprise-understanding build and read:

```text
enterprise_identity_annotation_manifest
```

Each row contains only:

- `mention_ref`
- original label
- source id and locator
- source role and declared scope

It intentionally contains no predicted entity id, cluster, canonical name or accepted
identity edge. The manifest is annotation input and is never Ground Truth by itself.

The Settings page exposes the same blind manifest through **跨资料身份融合质量校准**.
It can also be fetched from:

```text
GET /api/v1/projects/{project}/identity-benchmark/manifest
```

## 2. Produce external Ground Truth

Every manifest mention must appear in exactly one confirmed cluster, including
singletons. Missing one mention makes the result `NOT_MEASURED`.

The Ground Truth must carry the exact current `manifest_id`. Importing an older
manifest after sources or mentions change fails closed with
`identity_ground_truth_manifest_stale`.

```json
{
  "schema": "qualibug.enterprise-identity-ground-truth.v1",
  "benchmark_id": "customer-a-identity-v1",
  "manifest_id": "enterprise_identity_annotation_manifest:current",
  "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
  "ground_truth_generated_from_product_output": false,
  "clusters": [
    {
      "cluster_ref": "truth:sales-order",
      "annotation_status": "CONFIRMED",
      "member_refs": [
        "mention:prd:section-2:销售订单",
        "mention:api:post-orders:订单"
      ]
    },
    {
      "cluster_ref": "truth:crm-order",
      "annotation_status": "CONFIRMED",
      "member_refs": [
        "mention:crm:section-7:订单"
      ]
    }
  ]
}
```

Rules:

1. A mention may occur in only one cluster.
2. Singletons must be explicitly retained.
3. Do not copy the product's predicted clusters into Ground Truth.
4. Do not align by fuzzy name similarity or LLM judgment during evaluation.
5. Annotation disagreements must be resolved externally or remain unmeasured.

Import through:

```text
POST /api/v1/projects/{project}/identity-benchmark/ground-truth
```

```json
{
  "manifest_id": "enterprise_identity_annotation_manifest:current",
  "ground_truth": {
    "schema": "qualibug.enterprise-identity-ground-truth.v1",
    "manifest_id": "enterprise_identity_annotation_manifest:current",
    "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
    "ground_truth_generated_from_product_output": false,
    "clusters": []
  }
}
```

The POST operation is one project-scoped knowledge transaction:

```text
acquire existing knowledge lease
→ validate current manifest and closed-world universe
→ snapshot prior Ground Truth and benchmark history files
→ persist Ground Truth
→ rebuild through the canonical composition root
→ require MEASURED benchmark output
→ append the first measurement snapshot
→ append audit receipt
```

If rebuilding or snapshot persistence fails, both the prior Ground Truth and benchmark
history files are restored. Concurrent knowledge mutations return HTTP
`409 IDENTITY_BENCHMARK_TRANSACTION_BUSY` rather than racing.

## 3. Configure the combined quality Gate

Absolute thresholds and version-regression thresholds are explicit policy. They are not
hard-coded benchmark assumptions and they feed the same final identity quality Gate.

```json
{
  "schema": "qualibug.enterprise-identity-quality-policy.v1",
  "enforce": true,
  "enforce_regression": true,
  "thresholds": {
    "minimum_pairwise_precision": 0.98,
    "minimum_pairwise_recall": 0.95,
    "minimum_pairwise_f1": 0.96,
    "minimum_exact_cluster_match_rate": 0.90,
    "maximum_overmerge_rate": 0.02,
    "maximum_undermerge_rate": 0.05,
    "minimum_identity_error_unknown_coverage_rate": 0.90,
    "maximum_silent_identity_error_count": 0
  },
  "regression_thresholds": {
    "maximum_pairwise_precision_drop": 0.01,
    "maximum_pairwise_recall_drop": 0.01,
    "maximum_pairwise_f1_drop": 0.01,
    "maximum_exact_cluster_match_rate_drop": 0.02,
    "maximum_overmerge_rate_increase": 0.01,
    "maximum_undermerge_rate_increase": 0.01,
    "maximum_identity_error_unknown_coverage_drop": 0.02,
    "maximum_silent_identity_error_increase": 0
  }
}
```

Save through:

```text
POST /api/v1/projects/{project}/identity-benchmark/quality-policy
```

When `enforce` is true, absolute measured quality below the configured thresholds blocks
enterprise identity admission. When `enforce_regression` is true, a comparable later
snapshot that regresses beyond a configured delta also blocks the same final Gate.
Semantic understanding and scenario planning cannot claim readiness through a blocked
identity Gate.

A regression baseline is comparable only when both values are identical:

```text
manifest_id
external Ground Truth fingerprint
```

Changing source occurrences, relabeling the closed-world universe or replacing Ground
Truth yields `NOT_COMPARABLE`; it is never reported as model degradation.

## 4. Remeasure and record a versioned snapshot

After Ground Truth exists, run:

```text
POST /api/v1/projects/{project}/identity-benchmark/run
```

The endpoint rebuilds through the same enterprise knowledge composition root, requires
a measured result, records an immutable measurement event and then returns the complete
workspace. Each explicit run is retained even when its result fingerprint equals the
prior run. Result equality and measurement-event identity are separate concepts.

History is stored under the existing project workspace:

```text
enterprise_identity_benchmark_history.json
```

The bounded ledger retains the latest 500 measurement events. Every snapshot includes:

- manifest and Ground Truth fingerprints;
- benchmark result fingerprint;
- metrics and combined quality Gate;
- regression result and baseline snapshot reference;
- exact occurrence-level error rows;
- actor, trigger and recording time.

## 5. Exact identity error queue

False-positive pairs become **overmerge** errors and false-negative pairs become
**undermerge** errors. Their stable error identity is derived only from:

```text
error type + exact left mention_ref + exact right mention_ref
```

The queue projects these lifecycle states against the comparable baseline:

- `NEW`
- `PERSISTING`
- `RESOLVED`

It does not use label similarity, fuzzy matching or an LLM to invent a root cause. Source
IDs, source locators, mention roles and declared scopes remain attached to each error.

## 6. Read the complete workspace

```text
GET /api/v1/projects/{project}/identity-benchmark
```

The response contains only backend-produced state:

- blind annotation manifest;
- benchmark and exact source-occurrence error pairs;
- identity and combined quality Gates;
- current regression result and metric deltas;
- persisted quality policy;
- Ground Truth summary and fingerprint;
- bounded snapshot summaries and current error queue;
- bounded audit history.

The browser downloads and uploads JSON, triggers the backend workflow and renders its
results. It does not calculate identity clusters, quality metrics or regression deltas.

## 7. Composition and persistence authority

Ground Truth, quality policy and prior history are stored under the existing project
workspace and loaded once by the explicit enterprise knowledge composition root before
the first enterprise-understanding pass. The identity benchmark runs first; regression
then extends that benchmark's quality Gate before the legacy semantic projection. The
API does not edit the finalized model directly and does not provide a second evaluation
pipeline.

## 8. Output metrics

The benchmark publishes:

- pairwise precision, recall and F1;
- exact cluster match rate;
- overmerge and undermerge rates;
- false-positive and false-negative occurrence pairs with source locations;
- uncertainty coverage and silent identity error count;
- static threshold checks;
- comparable-baseline metric deltas and regression checks;
- one final combined identity quality Gate.

A benchmark can claim quality only when the annotation universe exactly equals the
product's business-mention universe. A regression claim additionally requires the same
manifest and external Ground Truth fingerprint as its baseline.
