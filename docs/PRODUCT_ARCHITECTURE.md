# QualiBug Product Architecture Contract

Status: active migration contract

## Product direction

QualiBug remains one repository and one deployable product platform. The repository is no longer organized conceptually around Autonomous Bug Discovery as the only product surface.

The product portfolio is:

1. **Requirement Intelligence** — primary commercial-validation entry.
2. **Test Intelligence** — planned product surface built on shared understanding and evidence.
3. **Bug Discovery** — experimental advanced runtime; feature development is frozen except P0 correctness and convergence work.

The near-term objective is to reuse the existing multi-source enterprise understanding stack to review requirements for conflicts, missing rules, ambiguity, and traceable risk before forcing every finding through executable bug reproduction.

## Dependency direction

The intended dependency direction is:

```text
shared intelligence authorities
            ^
            |
      product domains
            ^
            |
     application/runtime
```

Rules:

- Shared intelligence code must not import product-domain packages.
- Product domains may consume shared authorities through explicit adapters or stable APIs.
- Application/runtime composition may combine multiple product domains.
- Requirement Intelligence must not import Bug Discovery runtime patches, v12 scheduling/execution authority, search-policy patches, experiment execution, observers, oracles, or scan-result repair mechanisms.
- A new product must not create a duplicate ingestion, evidence, canonical identity, or persistence authority.

## Current shared authorities

During migration, `ai_test_asset_center` remains the location of the existing production authorities. We do **not** create a parallel `qualibug_core` implementation merely to make the directory tree look cleaner.

Existing capabilities are classified before migration:

- multi-source ingestion and project assets: shared candidate;
- enterprise knowledge / business understanding: shared candidate;
- Behavior IR and source relationships: shared candidate;
- evidence and source traceability: shared candidate;
- canonical finding/defect identity: shared authority that must remain singular;
- persistence: shared authority that must remain singular;
- reporting/projection: shared where domain-neutral, product-specific where presentation differs;
- search policy, executable experiments, observers, oracles, reproduction: Bug Discovery only.

A capability moves into a future shared-core package only when its callers and authority contract are known. Migration is incremental and must preserve one authoritative implementation.

## Product packages

New product-domain work lives under `products/`.

```text
products/
  requirement_intelligence/
  test_intelligence/          # planned; do not create until needed
  bug_discovery/              # planned boundary; existing runtime is not mass-moved
```

`products/requirement_intelligence` is deliberately thin. It owns Requirement Intelligence semantics and orchestration, not copies of shared storage or evidence models.

### Requirement Intelligence v1 scope

The first validation surface is intentionally bounded to three finding categories:

- requirement conflict;
- missing requirement/rule;
- requirement ambiguity.

Every delivered finding must be traceable to source evidence. Unsupported conclusions must not be promoted to formal findings.

The first product flow is:

```text
enterprise source material
        -> existing ingestion / understanding
        -> requirement-oriented projection
        -> conflict / missing / ambiguity analysis
        -> evidence-backed findings
        -> requirement readiness projection
```

It is explicitly **not**:

```text
requirement analysis
        -> v12 scheduler
        -> experiment execution
        -> observer/oracle
        -> bug confirmation
```

Those execution stages remain an optional Bug Discovery path.

### Requirement Intelligence v1 authority mapping

The product layer does not rediscover or reinterpret upstream truth. The current v1 projections are deliberately narrow:

- **Conflict** consumes active `cross_document_conflicts` and preserves the existing conflict identity, source evidence, operator action, and authority decision.
- **Missing** consumes only source-backed enterprise-understanding lifecycle unknowns currently classified as `LIFECYCLE_FROM_STATE_UNKNOWN`, `LIFECYCLE_TO_STATE_UNKNOWN`, or `LIFECYCLE_DISCONNECTED`.
- Parser failures, document-structure failures, runtime gaps, and test/search coverage gaps are not relabelled as missing requirements.
- `LIFECYCLE_TARGET_CONTRADICTION` remains a conflict-class semantic problem and is not duplicated as a missing finding.
- **Ambiguity** consumes only evidence-backed `PENDING_REVIEW` tasks from the existing enterprise identity structural-review queue. Confirmed, rejected, stale, or unsupported candidates do not remain active product findings.
- Requirement Intelligence never automatically resolves a source authority conflict, invents a missing business fact, or automatically unions ambiguous business identities.

### Requirement Readiness v1

Requirement Readiness is a deterministic gate over currently projected Requirement Findings. It is **not** a model-quality score, document-completeness percentage, recall estimate, or commercial quality claim.

The states are:

- `NOT_READY` — at least one finding is an upstream hard blocker, such as an unresolved requirement conflict or a missing lifecycle definition that already blocks formal enterprise understanding.
- `REVIEW_REQUIRED` — no hard blocker remains, but at least one non-blocking missing definition or identity ambiguity still requires explicit human review.
- `READY` — no currently supported active Requirement Finding remains.

The readiness receipt must expose finding IDs and counts so every gate decision is explainable. It must carry the quality claim `DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL` and must not expose a synthetic completeness/recall percentage.

## Finding and evidence authority

`Finding` is a platform concept; a confirmed bug is one type of finding, but this migration must not prematurely replace the current canonical defect contract.

Near-term rules:

- do not introduce a second persisted finding table for Requirement Intelligence;
- do not invent a second evidence store;
- do not use title/method/path as a new canonical identity mechanism;
- preserve existing canonical defect identity for confirmed bugs;
- introduce a generic canonical finding identity only through a dedicated migration with compatibility tests, not as part of the Requirement Intelligence entry work.

## Bug Discovery freeze

Bug Discovery remains available as an Experimental/Advanced capability. Until Requirement Intelligence validation produces evidence that a deeper refactor is justified:

- no new Bug Discovery feature families;
- P0 correctness fixes are allowed;
- authority convergence is allowed;
- benchmark-backed search-policy work is allowed only under the existing frozen evaluation discipline;
- broad legacy cleanup must not be mixed with Requirement Intelligence feature work.

## Repository migration discipline

For every change:

1. Prefer a short-lived branch and focused PR.
2. Do not mass-move existing modules for aesthetics.
3. New code follows this dependency contract immediately.
4. Existing code is classified as shared/product/legacy/retire before relocation.
5. One capability must have one authoritative implementation and an explicit composition point.
6. Compatibility adapters need an exit condition.
7. Product-facing claims must be backed by measured evidence; Bug Discovery commercial quality remains unproven until measured on an appropriate frozen target set.

## Initial integration sequence

1. Establish and CI-enforce the Requirement Intelligence package boundary.
2. Expose an authenticated product-capability entry from the existing private-pilot service.
3. Add a thin adapter over existing enterprise source ingestion/understanding.
4. Implement evidence-backed conflict analysis.
5. Add missing-rule and ambiguity analysis.
6. Add Requirement Readiness projection.
7. Validate with real enterprise materials before expanding scope.

This document is an architecture constraint, not a request for an immediate repository-wide rewrite.
