# Phase69 Release Notes — Evidence-First LLM Hypothesis Boundary Unification

## Objective

Prevent an online LLM from promoting an unverified semantic suggestion into a
formal enterprise defect. Phase69 focuses on detection precision, evidence
quality and release trust; it does not add a parallel orchestration system, UI
layer or business runtime.

## Change

A shared `compile_unverified_semantic_hypotheses()` adapter now normalizes LLM
output from legacy business reasoning engines. It is applied to counterexample
discovery, event-chain reasoning, business invariants, lifecycle reasoning,
outcome validation, population constraints, reconciliation, Saga compensation,
multisource reasoning and temporal regression reasoning.

Each retained candidate is explicitly marked as:

- `status: unverified_hypothesis`
- `execution_policy: candidate_only`
- `requires_deterministic_replay: true`
- `evidence_strength: llm_inferred`

The adapter caps confidence at `0.60`, stores a potential severity rather than
a formal severity, limits candidate volume, redacts token-like values and email
addresses, and emits an auditable hypothesis fingerprint. All affected engine
results now expose `llm_governance` stating that semantic hypotheses do not
affect finding counts.

## Safety and evidence boundary

LLM output cannot enter formal findings, severity buckets, evidence registries,
learning memory, validation queues or release gates. A candidate can become a
defect only after an existing deterministic, read-only Oracle or replay produces
independent evidence. Production protection, permissions, evidence redaction
and the shared safety boundary are unchanged.

## Release state

The deterministic product path is engineering-validated. Real provider health
remains deployment-specific: this isolated build environment cannot reach the
configured external provider before DNS/network resolution, so Phase69 does not
claim a successful live-model call.
