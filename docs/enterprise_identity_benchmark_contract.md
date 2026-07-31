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
→ snapshot prior durable input
→ persist Ground Truth
→ rebuild through the canonical composition root
→ require MEASURED benchmark output
→ append audit receipt
```

If rebuilding fails, the prior Ground Truth file is restored. Concurrent knowledge
mutations return HTTP `409 IDENTITY_BENCHMARK_TRANSACTION_BUSY` rather than racing.

## 3. Configure the quality Gate

Thresholds are explicit policy, not hard-coded benchmark assumptions.

```json
{
  "schema": "qualibug.enterprise-identity-quality-policy.v1",
  "enforce": true,
  "thresholds": {
    "minimum_pairwise_precision": 0.98,
    "minimum_pairwise_recall": 0.95,
    "minimum_pairwise_f1": 0.96,
    "minimum_exact_cluster_match_rate": 0.90,
    "maximum_overmerge_rate": 0.02,
    "maximum_undermerge_rate": 0.05,
    "minimum_identity_error_unknown_coverage_rate": 0.90,
    "maximum_silent_identity_error_count": 0
  }
}
```

Save through:

```text
POST /api/v1/projects/{project}/identity-benchmark/quality-policy
```

```json
{
  "quality_policy": {
    "schema": "qualibug.enterprise-identity-quality-policy.v1",
    "enforce": true,
    "thresholds": {
      "minimum_pairwise_precision": 0.98,
      "minimum_pairwise_recall": 0.95,
      "maximum_overmerge_rate": 0.02,
      "maximum_undermerge_rate": 0.05,
      "maximum_silent_identity_error_count": 0
    }
  }
}
```

When `enforce` is true:

- missing or incomplete Ground Truth blocks the identity quality Gate;
- measured metrics below thresholds block enterprise identity admission;
- semantic understanding and scenario planning cannot claim readiness through that Gate.

When no policy is configured, measurement remains visible but does not block normal
enterprise ingestion.

## 4. Read the complete workspace

```text
GET /api/v1/projects/{project}/identity-benchmark
```

The response contains only backend-produced state:

- blind annotation manifest;
- benchmark and source-occurrence error pairs;
- identity and quality Gates;
- persisted quality policy;
- Ground Truth summary and fingerprint, not an inferred browser copy;
- bounded audit history.

The browser downloads and uploads JSON, but does not calculate identity clusters or
quality metrics.

## 5. Composition and persistence authority

Ground Truth and quality policy are stored under the existing project workspace and
loaded once by the explicit enterprise knowledge composition root before the first
enterprise-understanding pass. The API does not edit the finalized model directly and
does not provide a second evaluation pipeline.

## 6. Output metrics

The benchmark publishes:

- pairwise precision, recall and F1;
- exact cluster match rate;
- overmerge and undermerge rates;
- false-positive and false-negative occurrence pairs with source locations;
- uncertainty coverage and silent identity error count;
- a separate quality Gate with every threshold check and actual value.

A benchmark can claim quality only when the annotation universe exactly equals the
product's business-mention universe.
