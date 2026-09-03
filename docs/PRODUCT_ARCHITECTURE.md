# QualiBug Product Architecture Contract

Status: active migration contract

## Product direction

QualiBug remains one repository and one deployable product platform. The repository is no longer organized conceptually around Autonomous Bug Discovery as the only product surface.

The product portfolio is:

1. **Requirement Intelligence** — primary commercial-validation entry.
2. **Test Intelligence** — experimental validation surface that derives evidence-backed Test Obligations, structured Test Design, and supported-semantic coverage from shared enterprise understanding.
3. **Bug Discovery** — experimental advanced runtime; feature development is frozen except P0 correctness and convergence work.

The current objective is to validate whether the existing multi-source enterprise understanding, Requirement Intelligence, Test Obligation, and Test Design outputs create repeatable customer value. Capability expansion beyond Test Design is frozen for this validation phase.

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
- Product domains may consume shared authorities through explicit adapters or stable asset contracts.
- Application/runtime composition may combine multiple product domains.
- Requirement Intelligence and Test Intelligence must not import Bug Discovery runtime patches, v12 scheduling/execution authority, search-policy patches, experiment execution, observers, oracles, or scan-result repair mechanisms.
- Test Intelligence must not import Requirement Intelligence merely to obtain findings; cross-product composition belongs in an application layer or a future domain-neutral shared contract.
- A new product must not create a duplicate ingestion, evidence, canonical identity, or persistence authority.

## Current shared authorities

During migration, `ai_test_asset_center` remains the location of the existing production authorities. We do **not** create a parallel `qualibug_core` implementation merely to make the directory tree look cleaner.

Existing capabilities are classified before migration:

- multi-source ingestion and project assets: shared candidate;
- enterprise knowledge / business understanding: shared candidate;
- Business Behavior IR, Behavior IR, lifecycle models, and source relationships: shared candidates;
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
  test_intelligence/
  bug_discovery/              # planned boundary; existing runtime is not mass-moved
```

Product packages are deliberately thin. They own product semantics and projections, not copies of shared storage, ingestion, evidence, or runtime engines.

## Requirement Intelligence v1

The first validation surface is intentionally bounded to three finding categories:

- requirement conflict;
- missing requirement/rule;
- requirement ambiguity.

Every delivered finding must be traceable to source evidence. Unsupported conclusions must not be promoted to formal findings.

The product flow is:

```text
enterprise source material
        -> existing ingestion / understanding
        -> requirement-oriented projection
        -> conflict / missing / ambiguity
        -> evidence-backed findings
        -> requirement readiness
```

It is explicitly **not**:

```text
requirement analysis
        -> v12 scheduler
        -> experiment execution
        -> observer/oracle
        -> bug confirmation
```

Those execution stages remain an existing optional Bug Discovery path and are not part of the current capability-expansion plan.

### Requirement Intelligence v1 authority mapping

The product layer does not rediscover or reinterpret upstream truth. The current v1 projections are deliberately narrow:

- **Conflict** consumes active `cross_document_conflicts` and preserves the existing conflict identity, source evidence, operator action, and authority decision.
- **Missing** consumes only source-backed enterprise-understanding lifecycle unknowns currently classified as `LIFECYCLE_FROM_STATE_UNKNOWN`, `LIFECYCLE_TO_STATE_UNKNOWN`, or `LIFECYCLE_DISCONNECTED`.
- Parser failures, document-structure failures, runtime gaps, and test/search coverage gaps are not relabelled as missing requirements.
- `LIFECYCLE_TARGET_CONTRADICTION` remains a conflict-class semantic problem and is not duplicated as a missing finding.
- **Ambiguity** consumes only evidence-backed `PENDING_REVIEW` tasks from the existing enterprise identity structural-review queue.
- Requirement Intelligence never automatically resolves a source authority conflict, invents a missing business fact, or automatically unions ambiguous business identities.

### Requirement Readiness v1

Requirement Readiness is a deterministic gate over currently projected Requirement Findings. It is **not** a model-quality score, document-completeness percentage, recall estimate, or commercial quality claim.

The states are:

- `NOT_READY` — at least one finding is an upstream hard blocker.
- `REVIEW_REQUIRED` — no hard blocker remains, but at least one non-blocking missing definition or identity ambiguity still requires explicit human review.
- `READY` — no currently supported active Requirement Finding remains.

The readiness receipt must carry the quality claim `DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL`.

## Test Intelligence v1

Test Intelligence answers two upstream-of-runtime questions:

> Given the source-backed business semantics the platform already understands, what must be verified?

> Given one evidence-backed Test Obligation, what semantic setup, action, observations, and Oracle are required to verify it?

It does **not** generate arbitrary prose test cases and it does **not** execute the target system.

The current flow is:

```text
enterprise source material
        -> existing enterprise understanding
        -> confirmed Business Behavior IR / lifecycle truth
        -> Test Obligation projection
        -> supported-semantic obligation coverage
        -> structured Test Design
        -> STOP
```

The current API surface is:

```text
GET /api/v1/projects/{project}/test-intelligence
```

### Test Obligation authority mapping

V1 consumes the already-built `enterprise_understanding_model` and does not import enterprise-understanding builders.

Implemented obligation kinds are:

- **business_rule** — confirmed formal business behavior with an explicit source modality when no more specific implemented semantic applies;
- **authorization** — confirmed formal business behavior whose existing authorization authority is explicit, resolved, and yields `ALLOW` or `DENY`;
- **side_effect** — confirmed formal business behavior with source-backed expected effects, data effects, or compensations;
- **lifecycle_transition** — complete lifecycle transitions already classified upstream as `ALLOWED` or `FORBIDDEN`.

`requirement_risk` remains a supported-but-unimplemented historical placeholder in the v1 manifest. It is not a current expansion target. Test Intelligence does not import Requirement Intelligence to create it. Requirement Finding linkage is composed above both product packages in the application layer and does not create new obligations.

Every delivered Test Obligation requires source-backed evidence. Candidate-only behaviors, incomplete lifecycle transitions, unresolved semantics, and evidence-less units are not promoted to customer-facing obligations.

Test Obligation IDs are stable projection identities derived from upstream semantic units. They are not a new persisted canonical test/finding identity.

### Test Design v1

Test Design is a deterministic projection from a Test Obligation. It structures the semantic verification contract and is the current end of Test Intelligence capability expansion.

The design receipt uses:

`DETERMINISTIC_OBLIGATION_DERIVED_TEST_DESIGN_NOT_RUNTIME_GROUNDING_OR_EXECUTION`

A Test Design may contain only information already represented by its source Obligation:

- source-derived preconditions and test-data requirements;
- actor, business object, and semantic operation references;
- semantic observation targets derived from expected outcomes;
- source-derived Oracle assertions;
- business constraints, Requirement Finding links, and source evidence.

Test Design v1 must **not** invent:

- HTTP/API paths or request payload bindings;
- UI selectors, click sequences, or browser steps;
- concrete test accounts, IDs, amounts, timestamps, or fixture values not present in the source semantics;
- target environment selection;
- Observer or Oracle runtime bindings;
- execution success, verification, or reproducibility claims.

The truth states are explicit:

- `design_status = STRUCTURED_DESIGN_ONLY`;
- `action.execution_surface = NOT_SELECTED`;
- `action.binding_status = NOT_GROUNDED`;
- `test_data_materialization_status = NOT_MATERIALIZED`;
- `environment_status = NOT_SELECTED`;
- `observer_binding_status = NOT_GROUNDED`;
- `oracle_binding_status = NOT_GROUNDED`;
- `runtime_handoff_status = NOT_REQUESTED`;
- `execution_status = NOT_EXECUTED`;
- `safety_review_status = NOT_ASSESSED`.

A Test Obligation itself remains `design_status = OBLIGATION_ONLY`. The existence of a separate Test Design does not mutate the semantic meaning of the source Obligation.

Test Design IDs are stable projection identities derived from Test Obligation IDs. They are not a new persisted canonical test-case identity.

### Runtime boundary and feature freeze

Test Intelligence owns obligation semantics and structured Test Design, and stops here:

```text
Test Obligation
        -> structured Test Design
        -> STOP
```

During the current validation phase, the repository must not add a new Grounding Assessment or execution-binding product layer. Specifically, do not add capability for:

- API or UI automatic binding;
- test-data automatic materialization;
- Observer or Oracle automatic binding;
- environment-selection intelligence;
- automated safety/runtime approval;
- new Executable Experiment generation from Test Design;
- new Bug Discovery execution behavior to consume Test Design.

Existing Bug Discovery runtime code may remain available as experimental legacy/advanced capability, but Test Intelligence is not being extended toward it until a future explicit product decision reverses this freeze based on customer evidence.

### Test Intelligence coverage v1

Coverage is a deterministic projection over the semantic units supported by the current implementation. It is not total test completeness and it is not execution coverage.

The coverage receipt uses:

`DETERMINISTIC_SUPPORTED_SEMANTIC_OBLIGATION_COVERAGE_NOT_TOTAL_TEST_COMPLETENESS`

It reports:

- eligible supported semantic units;
- units successfully projected to evidence-backed obligations;
- uncovered supported semantic unit IDs;
- counts by obligation kind;
- `execution_coverage_status = NOT_MEASURED`.

When there are no eligible supported semantic units, coverage is `NOT_MEASURED`, never a healthy-looking 100%.

Test Design projection is reported separately as `NOT_MEASURED`, `PARTIAL`, or `DESIGNED`. `DESIGNED` means every current Test Obligation has a structured Test Design; it does not mean any design is grounded, executable, or verified.

### Requirement Finding linkage v1

Requirement-to-Test linkage is application composition over the two independent product projections. It does not make either product package import the other and it does not create a second finding, obligation, evidence, or persistence authority.

The linkage receipt uses:

`DETERMINISTIC_EXACT_REQUIREMENT_TEST_LINKAGE_NOT_SEMANTIC_SIMILARITY_OR_COMPLETENESS`

A link is emitted only when one of these exact proofs exists:

- **shared source fact identity** — a Requirement Finding evidence `fact_id` exactly matches a Test Obligation source/evidence fact identity;
- **exact ambiguous object identity** — a pending identity-ambiguity Finding candidate entity ID exactly matches an Obligation `object_ref`;
- **exact lifecycle coordinates** — a lifecycle-missing Finding and lifecycle-transition Obligation share both an exact object reference and exact operation reference.

The linkage layer explicitly does **not** use text similarity, shared filenames/source IDs, recency, model confidence, nearby operations, or broad object-name resemblance as proof. Findings without an exact proof remain visible as unlinked rather than being force-attached to an Obligation.

Linkage updates `requirement_finding_ids` on the Test Obligation API projection. A Test Design inherits only the already-proven links of its exact source Obligation; the design layer performs no second matching pass. The linkage receipt reports linked Obligation and Design counts. Linkage does not imply runtime verification.

## Finding, obligation, design, and evidence authority

`Finding`, `Test Obligation`, and `Test Design` are different platform concepts:

- a Finding says a problem/risk was detected;
- a Test Obligation says a source-backed semantic must be verified;
- a Test Design structures how that Obligation should be verified while remaining non-executable.

A confirmed bug remains one type of Finding. Neither a Test Obligation nor a Test Design is evidence that execution occurred.

Near-term rules:

- do not introduce a second persisted finding, obligation, or test-design table merely for the new product surfaces;
- do not invent a second evidence store;
- do not use title/method/path as a new canonical identity mechanism;
- preserve existing canonical defect identity for confirmed bugs;
- introduce generic persisted identities only through dedicated migrations with compatibility tests.

## Product validation freeze

Requirement Intelligence and Test Intelligence are now in a product-quality validation phase.

Until current outputs are reviewed on frozen enterprise-shaped samples and then on real customer materials, feature work is limited to:

- P0 correctness fixes;
- output-quality fixes supported by a concrete audit miss/noise example;
- UX defects that block review of existing Findings, Obligations, Designs, or Evidence;
- evaluation/capture infrastructure that does not alter product inference.

Do not add another capability layer because it is architecturally adjacent. A missing or weak current output must first be recorded as an audit result. The next engineering decision must be driven by repeated evidence of the same quality bottleneck, not by an architecture diagram.

The current audit contract is documented in `docs/PRODUCT_QUALITY_AUDIT_20260903.md`.

## Bug Discovery freeze

Bug Discovery remains available as an Experimental/Advanced capability. Until upstream product validation produces evidence that a deeper refactor is justified:

- no new Bug Discovery feature families;
- P0 correctness fixes are allowed;
- authority convergence is allowed;
- benchmark-backed search-policy work is allowed only under the existing frozen evaluation discipline;
- broad legacy cleanup must not be mixed with Requirement/Test Intelligence validation work.

Bug Discovery is an existing optional downstream execution authority, not the place where Requirement/Test Intelligence re-implement their semantics and not the current product-development focus.

## Repository migration discipline

For every change:

1. Prefer a short-lived branch and focused PR.
2. Do not mass-move existing modules for aesthetics.
3. New code follows this dependency contract immediately.
4. Existing code is classified as shared/product/legacy/retire before relocation.
5. One capability must have one authoritative implementation and an explicit composition point.
6. Compatibility adapters need an exit condition.
7. Product-facing claims must be backed by measured evidence.

## Current integration sequence

Completed:

1. Establish and CI-enforce the Requirement Intelligence package boundary.
2. Expose authenticated product-capability APIs from the existing private-pilot service.
3. Implement evidence-backed Requirement Conflict / Missing / Ambiguity.
4. Add deterministic Requirement Readiness.
5. Add the Requirement Intelligence frontend workspace.
6. Establish the Test Intelligence package boundary.
7. Project evidence-backed Test Obligations from existing Business Behavior IR and lifecycle truth.
8. Add deterministic supported-semantic coverage.
9. Expose the authenticated project Test Intelligence API and frontend workspace.
10. Add exact application-layer Requirement Finding → Test Obligation linkage without introducing product-to-product imports.
11. Add deterministic Test Obligation → structured Test Design projection without runtime grounding or execution claims.
12. Freeze capability expansion and establish a source-backed product-quality audit baseline over existing repository samples.

Current validation sequence:

1. Capture Requirement Finding, Requirement Readiness, Test Obligation, Test Design, linkage, and Evidence outputs on the frozen `object_source_conflict`, `benchmark_mall`, and `warehouse_e` samples.
2. Human-review the externally authored source-backed anchors as `USEFUL`, `TOO_GENERIC`, `NOISY`, `MISSED`, `UNSUPPORTED_CLAIM`, or `NEEDS_SOURCE_CLARIFICATION`.
3. Group repeated misses/noise by the existing intelligence stage that caused them; do not create new layers while diagnosing.
4. Fix only demonstrated high-value quality bottlenecks or review-blocking UX defects.
5. Validate the same frozen baseline again, then move to real customer materials if output quality is credible.

Grounding Assessment, execution binding, and new runtime handoff work are explicitly not part of this sequence.

This document is an architecture constraint, not a request for an immediate repository-wide rewrite.
