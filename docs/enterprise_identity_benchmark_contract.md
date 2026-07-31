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

## 2. Produce external Ground Truth

Every manifest mention must appear in exactly one confirmed cluster, including
singletons. Missing one mention makes the result `NOT_MEASURED`.

```json
{
  "schema": "qualibug.enterprise-identity-ground-truth.v1",
  "benchmark_id": "customer-a-identity-v1",
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

When `enforce` is true:

- missing or incomplete Ground Truth blocks the identity quality Gate;
- measured metrics below thresholds block enterprise identity admission;
- semantic understanding and scenario planning cannot claim readiness through that Gate.

When no policy is configured, measurement remains visible but does not block normal
enterprise ingestion.

## 4. Output metrics

The benchmark publishes:

- pairwise precision, recall and F1;
- exact cluster match rate;
- overmerge and undermerge rates;
- false-positive and false-negative occurrence pairs with source locations;
- uncertainty coverage and silent identity error count;
- a separate quality Gate with every threshold check and actual value.

A benchmark can claim quality only when the annotation universe exactly equals the
product's business-mention universe.
