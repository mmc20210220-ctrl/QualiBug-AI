# Evidence Pipeline — Phase 92A

The evidence pipeline ensures every finding is backed by **verifiable, hash-addressed evidence** with a four-layer state preservation model.

## Four-Layer Evidence Model

1. **Raw Probe Evidence** — HTTP request/response pairs, browser screenshots, DB snapshots
2. **Normalized Runtime Evidence** — Standardized format via `evidence_normalizer.py`
3. **Semantic Verification Evidence** — Business-meaning enrichment via `evidence_enricher_v3.py`
4. **Business Finding Contract** — Final confirmed finding with full evidence chain

## Double Gate Verification

Before a finding is confirmed, it passes through two gates:
- **Runtime Evidence Gate**: Was actual execution performed? Are request/response/assertion/timestamp/target/actor/reproduction_steps all present?
- **Business Evidence Gate**: Does the evidence semantically support the claimed defect?

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| Evidence Normalizer | `evidence_normalizer.py` | Standardize raw probe output |
| Evidence Enricher v3 | `evidence_enricher_v3.py` | Business context enrichment |
| Evidence Bundle Normalizer | `evidence_bundle_normalizer.py` | Bundle creation + SHA-256 |
| Evidence Graph Builder | `evidence_graph_builder.py` | Causality graph construction |
| Evidence Artifact Store | `evidence_artifact_store.py` | File-based persistence + retrieval |
| Discovery Finding Gate | `discovery_finding_gate.py` | Double-gate implementation |
| Business Adversarial Validator | `business_adversarial_validator.py` | Adversarial evidence testing |
| Display-Ready Formatter | `display_ready_formatter.py` | Frontend-zero-compute output (152KB) |

## Truthfulness Guarantee

`has_confirmation_evidence()` requires ALL of:
- request, response, assertion, timestamp, target, actor, reproduction_steps
- Synthetic/simulated results NEVER pass this check
