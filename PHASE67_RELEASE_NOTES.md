# Phase67 Release Notes — Evidence-First LLM Oracle Compilation

## Scope

Phase67 makes the configured OpenAI-compatible model useful for discovering
new business-test directions without allowing model text to become a defect,
a severity decision, a learned pattern, or a release blocker.

This phase also repairs the real self-dogfood P1 found in Phase66: after
health, semantic, and validation enrichment, the private scan endpoint kept
its detailed discovery list but left the executive total stale.

## Capability added

### 1. Schema-grounded LLM Oracle hypotheses

The autonomous pipeline can now ask an LLM to propose up to five business
Oracle candidates from PRD, OpenAPI, and existing deterministic findings.
Every returned candidate is filtered before persistence:

- source and comparison paths must exactly exist in the supplied OpenAPI;
- validation method must be `GET`;
- required field paths and evidence observations must be present;
- family must be one of the existing causality, conservation, reconciliation,
  permission, state, or temporal families;
- confidence is capped at `0.60`;
- status is always `unverified_hypothesis` with
  `requires_deterministic_replay=true` and `execution_policy=candidate_only`.

Candidates are stored separately from `stage2_discovery.findings`. They do
not change finding count, severity distribution, validation queues, pattern
learning, or release gates until a deterministic engine produces replayable
evidence.

### 2. Provider-compatible structured output

`llm_reasoning.py` now supports opt-in provider settings:

```text
LLM_THINKING_MODE=disabled
LLM_RESPONSE_FORMAT=json_object
```

They are intentionally opt-in so existing OpenAI-compatible deployments keep
their prior request payload. For a provider that supports these controls, they
make advisory output bounded and machine-parseable. The shared client is now
also used by LLM impact analysis, avoiding a second hand-built request path.

### 3. Verified health is semantic, not cosmetic

The private `/health` verification now requires a parsable `{"ok":true}`
response. Any HTTP success that returns malformed or non-JSON content is
reported as failed rather than online.

### 4. Cross-view finding-count repair

A single aggregation function now derives all scan totals from the final,
calibrated Stage2 finding list after health, semantic, and validation steps:

- `stage2_discovery.total_findings`
- `stage2_discovery.by_severity`
- `executive_summary.total_bugs_found`
- `executive_summary.critical_bugs`
- `executive_summary.high_priority_bugs`
- `stage3_impact_analysis.total_analyses` and `llm_powered`
- `executive_summary.impact_analyses` and `llm_powered_analyses`

This fixes the verified Phase66 discrepancy where the final list had 18
findings while the executive summary reported 13, and the companion Stage3
impact-analysis drift where executive counters still reported 10 analyses and
5 LLM analyses after the final view had 18 analyses and 0 LLM analyses.

## Boundaries preserved

- `safety_boundary.py` was not changed.
- No destructive test mode, target environment, or production-blocking logic
  was changed.
- No test files or `__init__.py` files were changed.
- The LLM key is not written to source, release notes, manifests, logs, or the
  delivery archive.
- LLM output remains advisory and cannot confirm a bug.

## Provider verification status

The configured DeepSeek endpoint/model combination was checked against the
provider's public API documentation. A real call from this isolated build
runtime could not leave the environment because DNS/network access was refused
before authentication. Therefore Phase67 does **not** claim that the provider
was online, and it contains no fabricated LLM output. The private deployment
must perform its own `/health` verification with the local secret available.
