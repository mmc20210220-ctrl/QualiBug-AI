# Enterprise Identity Blind Annotation Workflow

## Goal

Create trustworthy closed-world identity Ground Truth without copying QualiBug's
predicted entities, clusters, accepted identity edges or similarity candidates.

The workflow is available in Settings under **人工盲标任务**.

## 1. Export a task package

```text
GET /api/v1/projects/{project}/identity-benchmark/annotation-package
```

The task package contains:

- exact `mention_ref` values from the current identity manifest;
- original labels, source IDs, source locators, roles and declared scope;
- up to three source-evidence excerpts per Mention, redacted by the existing knowledge-center secret policy and bounded to 800 characters;
- deterministic batches, task and batch progress fields, and a blank `submission_template`;
- no product cluster suggestion, predicted entity ID or similarity candidate.

The package is tied to the current `manifest_id` and `task_package_id`. A submission
created from an older package is rejected after the enterprise Mention universe changes.

## 2. Complete one annotation submission

Copy `submission_template` into a separate JSON file. Fill the annotator and every
annotation row:

```json
{
  "schema": "qualibug.enterprise-identity-annotation-submission.v1",
  "task_package_id": "current-task-package-id",
  "manifest_id": "current-manifest-id",
  "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
  "generated_from_product_output": false,
  "annotator": {
    "name": "annotator-a",
    "role": "ANNOTATOR"
  },
  "annotations": [
    {
      "mention_ref": "mention:a",
      "annotation_status": "CONFIRMED",
      "annotation_cluster_ref": "sales-order"
    },
    {
      "mention_ref": "mention:b",
      "annotation_status": "CONFIRMED",
      "annotation_cluster_ref": "sales-order"
    },
    {
      "mention_ref": "mention:c",
      "annotation_status": "CONFIRMED",
      "annotation_cluster_ref": "purchase-order"
    }
  ]
}
```

Rules:

1. Every task Mention must occur exactly once.
2. Every row must be `CONFIRMED` and carry a non-empty local cluster reference.
3. Singleton entities must still receive their own cluster reference.
4. Local cluster names are annotation aids only; final Ground Truth cluster IDs are generated deterministically.
5. Product output and nested product-prediction fields cannot be used in a submission.
6. Ordinary primary and secondary submissions may not use role `ADJUDICATOR`.

## 3. Single-annotator import

Upload one completed ordinary submission in Settings, or call:

```text
POST /api/v1/projects/{project}/identity-benchmark/annotation-compile
```

```json
{
  "primary_submission": {}
}
```

A complete valid submission is compiled into Ground Truth and delegated to the existing
transactional Ground Truth import workflow. That workflow remains the only authority
that persists Ground Truth, rebuilds enterprise understanding, evaluates identity
quality and records the first benchmark snapshot.

## 4. Double-blind review

Upload two independently completed ordinary submissions:

```json
{
  "primary_submission": {},
  "secondary_submission": {}
}
```

The two annotators must have different names. Agreement compares the partition of
Mention members, not the annotators' local cluster names. These assignments therefore
agree:

```text
annotator A: mention:a, mention:b -> A1
annotator B: mention:a, mention:b -> B9
```

When partitions agree, Ground Truth is compiled and imported with review status
`DOUBLE_BLIND_AGREED`.

When partitions differ, the response is:

```text
REVIEW_REQUIRED
```

No Ground Truth is imported. The response lists exact affected Mention references and
the two conflicting member sets, while progress becomes `AWAITING_ADJUDICATION`.

## 5. Adjudication

Resolve every disagreement in a third complete submission:

```json
{
  "primary_submission": {},
  "secondary_submission": {},
  "adjudication_submission": {
    "annotator": {
      "name": "reviewer",
      "role": "ADJUDICATOR"
    }
  }
}
```

The adjudicator must be independent from both original annotators and must explicitly
use role `ADJUDICATOR`. An adjudication submission is rejected when the two original
partitions already agree.

In Settings, file order is irrelevant. The browser automatically identifies the one
optional adjudication file by `annotator.role=ADJUDICATOR`; the remaining one or two
files are ordinary annotator submissions.

The adjudication submission is also closed-world and complete. Its resolved partition is
compiled with review status `ADJUDICATED`, then passed into the same transactional
Ground Truth import authority.

## 6. Fail-closed conditions

Compilation or import is rejected when:

- the task package or manifest is stale;
- a Mention is missing, duplicated or unknown;
- any annotation is unconfirmed or has no cluster reference;
- a submission claims it was generated from product output or carries nested prediction fields;
- double-blind annotators use the same identity;
- an adjudicator is not independent or lacks the explicit adjudicator role;
- adjudication is supplied without two ordinary submissions or when no disagreement exists;
- the compiled Ground Truth no longer matches the current product Mention universe.

A review-required result is not an error, but it never mutates Ground Truth.

## 7. Authority boundary

```text
current enterprise identity manifest
→ prediction-free, redacted task package
→ human single/double-blind submissions
→ partition agreement or independent adjudication
→ deterministic Ground Truth compilation
→ existing transactional Ground Truth import
→ canonical Composition Root rebuild
→ measured benchmark + quality Gate + version snapshot
```

The annotation package and browser never calculate product identity predictions or
benchmark metrics.