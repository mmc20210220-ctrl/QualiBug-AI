# QualiBug Evidence-Driven Discovery Harness Evolution Goal

## Objective

Turn QualiBug from a static scan pipeline into a governed, evidence-driven Bug discovery system that can improve its surrounding Harness while the model, evaluator, safety boundary, and evidence threshold remain fixed.

The two nested loops are:

1. Discovery loop: source-grounded hypothesis → governed execution → runtime evidence → semantic/business verification → formal customer-deliverable defect.
2. Harness evolution loop: immutable execution traces → verifier-grounded weakness clusters → minimal bounded strategy proposals → paired replay and shadow evaluation → non-regressive promotion or rejection → next iteration.

This is a cross-industry platform goal. Customer names, benchmark answers, fixed endpoint paths, industry-specific workflow names, and hard-coded business rules are prohibited from reusable discovery behavior.

## Current baseline status

The repository does not yet contain a complete versioned private manifest with
held-in, three-industry held-out, intentionally clean, replay, and shadow run
receipts. Therefore the current commercial claim status is `NOT_MEASURED`.

A completed historical `llm_throughput` artifact is a real single-target
diagnostic run, but it is not a promotion baseline: it contains 46 saved
findings, and the current customer-delivery gate accepts only 15 after failing
closed on missing, failed, or non-reversible cleanup evidence. Historical raw
single-target Recall/Precision/F1 values in that artifact score findings that do
not all pass the current formal delivery gate, so they must not be used as a
commercial capability claim.

The benchmark runner now emits an evaluator submission and reports
`NOT_MEASURED`; it does not open ground truth or calculate quality metrics in
the discovery process. The external evaluator is the only scoring authority.

A champion/challenger baseline becomes measured only after the external
evaluator produces complete replay and shadow reports over the frozen manifest
defined below.

## Evaluation single source of truth

The evaluator-private dataset manifest uses schema `qualibug.discovery-evaluation-dataset.v1` and separates each target into:

- `runtime`: environment identity plus immutable input, fixture, and context artifact references;
- `evaluator`: hidden ground-truth reference for seeded-defect targets only.

The discovery process receives `build_runtime_view(...)`, which contains no evaluator object, ground-truth path, or ground-truth fingerprint. Ground truth is opened only by the external evaluator after a completed run.

Every target must declare a known non-production environment. Production and unknown environment types are rejected when the manifest is loaded.

A commercial dataset shape requires:

- at least one held-in seeded-defect target;
- at least one held-out seeded-defect target;
- at least one intentionally clean target;
- at least three distinct held-out industries.

Missing ground truth, missing pipeline health, a failed-safe pipeline, missing receipts, or incomplete target coverage produces `NOT_MEASURED`; it never produces a zero-Bug or zero-false-positive claim.

## Required run envelope

The external evaluator accepts a completed run envelope. All operational fields are measured inputs; the evaluator does not invent defaults.
For per-Bug loss diagnosis, supply the redacted immutable discovery Trace Ledger from the same run with `--trace-ledger` (or embed it as `trace_ledger`). Its run, policy, target, mode, and redaction contract must match exactly.

```json
{
  "run_id": "immutable-run-id",
  "policy_id": "policy-version-id",
  "evaluation_mode": "replay",
  "pipeline_health": {"status": "OK"},
  "operational_metrics": {
    "wall_clock_seconds": 0,
    "estimated_cost_usd": 0,
    "request_count": 0,
    "production_http_requests": 0,
    "cleanup_failures": 0,
    "safety_incidents": 0,
    "dirty_test_environments": 0,
    "execution_success_rate": 0,
    "engine_success_rate": 0,
    "duplicate_rate": 0
  },
  "scan_result": {
    "findings": [],
    "candidate_findings": []
  }
}
```

Use the external CLI:

```powershell
python tools/discovery_evaluation.py inspect --manifest <private-manifest>
python tools/discovery_evaluation.py evaluate --manifest <private-manifest> --target-id <target> --run-envelope <run-envelope> --trace-ledger <trace-ledger> --output-root <private-receipt-root>
python tools/discovery_evaluation.py aggregate --manifest <private-manifest> --receipt-dir <policy-mode-receipts> --output <immutable-report.json>
```

The evaluator output deliberately omits the ground-truth source path. When the Trace Ledger is present, `metrics.stage_loss_diagnostics` reports every hidden Bug's first loss stage across hypothesis generation, endpoint binding, selection, execution, Oracle evaluation/resolution, and formal delivery. These diagnostics never change TP/FP/FN scoring.

## Promotion rule

Promotion requires four complete reports over the exact same frozen target set:

1. champion replay;
2. challenger replay;
3. champion shadow;
4. challenger shadow.

All input, fixture, context, runtime, environment, and manifest fingerprints must match. Each target must have immutable champion and challenger run receipts in both modes.

Hard blockers include:

- missing held-in, held-out, clean, replay, or shadow execution;
- incomplete evaluation or operational metrics;
- fewer than three held-out industries;
- any production HTTP request;
- any safety incident, cleanup failure, dirty test environment, or regression failure;
- any P0/P1 false positive on an intentionally clean target.

Quality is non-regressive across held-in recall/precision/F1, held-out recall/precision/F1, replay and shadow F1, macro/minimum industry recall, evidence completeness, reproducibility, engine/execution success, duplicate rate, unit cost, and wall-clock time. At least one measured discovery split must improve; lower cost alone cannot promote a policy whose discovery ability did not improve.

## Bounded editable Harness surfaces

Allowed candidate changes:

- reasoner prompt fragments and engine weights;
- candidate ranking and budget allocation;
- source-to-endpoint binding strategy;
- probe composition and tool policies;
- evidence collection order, observers, and bounded async windows;
- retry, stopping, and recovery policies;
- verifier orchestration without lowering evidence requirements.

Frozen surfaces:

- evaluator code, hidden answers, dataset split, and target fixtures during a comparison;
- formal customer-delivery evidence threshold;
- production write boundary and governed sandbox executor;
- before/after/cleanup/audit receipt requirements;
- one governed receipt per actual write, reverse-order compensation for partial multi-write setup, and no whole-scenario write retry;
- `timeout_seconds >= 300`, `max_tokens >= 32768`, `MAX_HYPOTHESES = 15`, and `max_workers = 4`;
- product ports: frontend `5174`, backend `8088`.

## Stage gates

### Gate A — Evaluation integrity

- Evaluator-private manifest, runtime redaction, immutable fingerprints, strict formal-defect scoring, clean-target scoring, receipts, aggregate reports, and paired promotion evidence are implemented and tested.

### Gate B — Trace and weakness mining

- Every candidate has one cross-stage identity from generation through formal accounting.
- Failures are clustered from verifier outcomes and causal trace signatures, not titles alone.
- Every cluster contains example trace receipts, impact, recurrence, preserved-good behaviors, and a proposed editable surface.

### Gate C — Bounded proposal and real runner

- Each proposal is minimal, evidence-bound, versioned, and rejects edits to frozen surfaces.
- Champion/challenger replay and shadow execute automatically on identical frozen targets.
- Reject, promote, lineage, rollback, and post-promotion monitoring receipts are persisted.

### Gate D — Capability breakthrough

- Hidden benchmark Recall >= 30%.
- Precision >= 50%.
- Reproduction rate >= 90%.
- Held-out macro industry Recall >= 25%, with no industry below 15%.
- Clean-target P0/P1 false positives = 0.
- Unit cost per true positive improves by at least 40% from the frozen baseline.

### Commercial exit gate

- Held-out macro industry Recall >= 50%.
- Precision >= 70%.
- Reproduction rate >= 95%.
- Every customer-visible defect has replayable real evidence and an audit receipt.
- Cleanup success = 100%; production writes = 0.
- All claims are generated from the evaluation SSOT and are visibly blocked when measurement is incomplete.
