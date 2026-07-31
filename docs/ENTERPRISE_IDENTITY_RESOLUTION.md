# Enterprise Identity Resolution

## Purpose

Cross-source identity is a first-class authority between source-backed fact extraction and enterprise semantic projection. It is not a string replacement utility.

The authority answers four different questions without collapsing them:

1. Which source mentions denote the same business entity?
2. Which technical artifacts implement, represent, display, or emit that entity?
3. Which proposed merges are ambiguous or contradictory?
4. Which stable identity must be carried into operations, lifecycles, relations, processes, implementation bindings, and runtime planning?

## Mainline position

```text
source-backed enterprise facts and technical inventories
  -> Identity Mention Ledger
  -> Identity Evidence Edges
  -> Identity Clusters and stable Entity Registry
  -> Technical Artifact Bindings
  -> Enterprise Understanding semantic projection
  -> downstream behavior and implementation gates
```

`enterprise_understanding.identity_resolution` is the only formal identity authority. The previous `TERM_ALIAS` map remains a compatibility projection for existing consumers and cannot authorize a formal merge.

## Formal structures

- `qualibug.enterprise-identity-mention.v1`
- `qualibug.enterprise-identity-evidence-edge.v1`
- `qualibug.enterprise-identity-cluster.v1`
- `qualibug.enterprise-identity-binding.v1`
- `qualibug.enterprise-identity-registry.v1`
- `qualibug.enterprise-identity-resolution-gate.v1`
- `qualibug.enterprise-identity-resolution.v1`

## Identity contract

A business entity has a stable `entity_id` and a mutable `canonical_label`. A label change must not change the entity identity when the current source-backed cluster overlaps a persisted registry entry.

Original source mentions are retained in `entity_mentions`. Resolved stable references are projected separately in `resolved_entity_refs`. Formal identity resolution must never erase the original wording.

## Merge authority

Automatic union is allowed only for source-governed hard evidence:

- explicit alias declarations;
- explicit abbreviations;
- explicit rename declarations;
- exact repeated business labels inside the same explicit system/module/version scope.

Definition expressions, formula definitions, fuzzy name similarity, token overlap, document order, filename proximity, embedding similarity, and model confidence cannot authorize a formal merge.

Multi-hop hard identity edges form a transitive identity cluster. Conflicting alias declarations remain separate clusters and emit a formal unresolved conflict.

## Business and technical spaces

Technical artifacts are not business entities. Database tables, API schemas, UI specifications, and events retain their own artifact identities and connect to a business entity through typed bindings such as `IMPLEMENTS_ENTITY`.

An unbound technical artifact emits `CROSS_SOURCE_IDENTITY_UNRESOLVED`. It does not silently become a business object and it does not block unrelated resolved bindings.

## Gates

- `PASS`: no identity conflict and all discovered technical artifacts are explicitly bound.
- `PARTIAL_ENTERPRISE_IDENTITY_BINDING`: business identity is valid, but one or more technical artifacts are unbound. Per-binding admission remains enabled.
- `BLOCKED_ENTERPRISE_IDENTITY_CONFLICT`: a source-backed identity conflict exists. Formal enterprise understanding is blocked until the conflict is resolved.

Identity conflicts carry stable `conflict_id` values so the existing closure and authority-decision governance can preserve them.

## Compatibility

The historical semantic builder remains responsible for objects, operations, relations, lifecycles, and processes. It receives a compatibility projection generated from the identity authority:

- aliases are already resolved by stable clusters;
- `TERM_ALIAS` facts no longer act as a second merge authority;
- technical tables are removed from the business-object projection;
- final business object IDs are replaced by stable enterprise `entity_id` values.

## Validation boundary

The regression suite covers:

- multi-hop alias closure;
- stable entity identity across canonical-label changes;
- definition/formula non-merging;
- explicit table-to-business binding;
- unbound technical assets staying partial;
- conflicting aliases blocking the formal gate;
- preservation of original mentions and stable resolved references.

These tests are structural and do not constitute measured precision or recall. Commercial accuracy claims require an externally labeled cross-source identity benchmark with over-merge and under-merge metrics.
