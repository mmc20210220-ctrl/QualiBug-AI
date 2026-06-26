# Phase91 Cognitive Memory Graph Schema

## System of record

`platform_workspace/<project_id>/cognitive_memory_graph.sqlite3` is the only writable system of record for Phase91 graph data. Markdown exports are redacted, one-way projections and are never read by the discovery engine.

## Isolation boundary

Every node and edge is partitioned by:

```text
project_id + environment_id
```

Cross-project or cross-environment edges are rejected. Production environments retain the existing zero-HTTP safety boundary.

## Node types

```text
Project, Environment, SourceDocument, BusinessFact, Entity, Field, API,
Role, Permission, TenantBoundary, State, StateTransition, Invariant, Flow,
Observer, Event, Evidence, Hypothesis, Finding, RegressionGuard, CoverageGap,
Policy, PolicyEvaluation, Decision, HumanFactProposal, CleanupRecord, ChangeSet
```

## Edge types

```text
reads, mutates, requires, can_execute, has_field, has_state, transitions_to,
constrains, validated_by, uses, produces, observes, violates, targets,
has_evidence, has_disproof, guarded_by, belongs_to, prioritizes, impacts,
proposes_change_to, cleans, related_to, derived_from, refutes, covers
```

## Mandatory provenance fields

Every node and edge carries source/provenance data:

```text
id, project_id, environment_id, source, source_ref, confidence,
approval_status, created_at, updated_at, valid_from, valid_to,
run_id, policy_version, evidence_refs
```

Confidence is one of `confirmed`, `evidenced`, `inferred`, `disputed`, or `rejected`. High-risk write context excludes `inferred` and `disputed` facts.

## Fact update rules

- Confirmed facts cannot be overwritten by weak LLM inference.
- Conflicting facts create `disputed` records rather than silently replacing evidence.
- Human input enters `HumanFactProposal` first and changes the graph only after explicit approval.
- Revocation preserves the history and marks impacted facts for re-evaluation.

## Discovery integration

```text
Context Artifact
→ Graph fact sync
→ Risk Frontier selection
→ Graph Context Evidence Pack
→ Reasoner (shadow or explicitly enabled active mode)
→ Flow / Verifier / Adversarial Gate
→ Finding, Evidence, Cleanup graph update
→ next Frontier computation
```

## Markdown export boundary

`qualibug export-knowledge-vault` creates a redacted Markdown/YAML/`[[link]]` view. It contains no credentials, raw sensitive payloads, or automatic write-back path.
