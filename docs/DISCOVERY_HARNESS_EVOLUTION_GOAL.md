# QualiBug Evidence-Driven Discovery Harness Evolution Goal

## Objective

Turn QualiBug from a static scan pipeline into a governed, evidence-driven Bug
discovery system that can improve its surrounding Harness while the model,
evaluator, safety boundary, and evidence threshold remain fixed.

The detailed implementation architecture, migration sequence, Grok execution
protocol, and audit bundle are defined in
`docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`. This Goal is
the metric and promotion single source of truth; the implementation Spec is
subordinate to these gates and must not weaken them.

`QualiBug_AI_真实Bug发现能力提升完整SPEC_v1.0.docx` is a release export only.
It must be regenerated from the repository documentation and is not an
independently editable source of truth.

## Phase 1 single-mainline checkpoint (2026-07-12)

The approved first architecture-simplification phase is defined in
`docs/superpowers/specs/2026-07-12-discovery-single-mainline-phase1-design.md`.
It converges runtime on one pre-selected authoritative path:

```text
Behavior IR -> Test Obligations -> Executable Experiments
  -> Governed Execution -> Typed Observers/Assertions
  -> Delivery Gate -> Quality Projection -> External Evaluator
```

This checkpoint does not change Gate D, controlled-pilot, or GA thresholds.
It requires one authoritative scheduler and formal path per run, immutable
pre-run authority with no exception fallback, one terminal attempt receipt per
selected obligation, obligation-keyed traceability, healthy execution and
cleanup, exact formal-ID/count consistency, and no external TP/Recall/
Precision/F1 regression from the eligible clean champion. Legacy and LLM
inputs may remain only as source-grounded Obligation adapters after cutover.

Stage-local single-variable verification p50 wall time must improve by at least
60% from a frozen measured engineering baseline. This is an engineering-cycle
checkpoint, not a substitute for externally measured quality. The existing
P1 compile-rate, execution, engine, evidence, write-audit, and cleanup gates
remain in force and are not lowered when incomplete.

### Phase 1 implementation evidence contract

The runtime cutover is implemented, but Phase 1 is not complete until the
clean champion/candidate receipts satisfy every checkpoint above. Runtime
authority is frozen before planning in
`qualibug.discovery-mainline-run.v1`; one run may select either
`legacy_champion` or `experiment_candidate`, and an exception may never switch
that selection. Replay and shadow runs set `customer_outputs_published=false`.
Until paired external non-regression evidence promotes the candidate, the
execution-policy default remains `legacy_champion`. The selected champion is
adapted into the same attempt/formal contracts from actual redacted traces and
runtime findings; this is pre-run authority selection, not an exception-time
fallback.

Completion and product health are derived only from
`qualibug.obligation-attempt-ledger.v1` plus the formal quality projection.
Zero selected obligations and runs whose obligations are all blocked remain
visibly `BLOCKED`; empty findings from those runs never mean that the target is
defect-free. Trace and weakness diagnostics consume
`qualibug.discovery-trace-ledger.v3`, keyed by obligation attempt identity.
V1/V2 artifacts require the explicit offline migration tool; runtime must not
silently reinterpret an older ledger as v3.
An approved follow-up may reopen a terminal campaign only when the prior ledger
proves zero executed target-request receipts and only `BLOCKED`/`DEFERRED`
terminals. Any observed request or write forbids whole-run retry.

Stage-local timing evidence uses immutable
`qualibug.discovery-phase1-timing.v1` receipts produced by
`tools/discovery_phase1_timing.py`. Baseline and candidate each require five
warm samples with matching command, input, Python/runtime, CPU/OS, and
environment fingerprints. A missing receipt, identity mismatch, dirty source
tree, or p50 improvement below 60% leaves this checkpoint incomplete. These
receipts do not alter external quality, benchmark P1, Gate D, controlled-pilot,
or GA status.

## Gate D implementation checkpoint (2026-07-10)

The repository now exposes the following contracts without changing the Gate D
measurement status:

- `target_policy.py` is the single target/environment decision for preflight,
  runtime execution, and governed writes. Hostnames never imply environment
  safety; a write requires an explicit non-production type, environment
  identity, and exact approved URL.
- enterprise material parsing emits versioned `ParserReceipt` records with
  source hash, parser, format, fidelity, output counts, and structured errors;
  one damaged source is isolated and remains visible as a coverage gap.
- selected experiments emit explicit execution receipts and the continuity
  fields `candidate_id → slice_id → obligation_id → experiment_id →
  execution_id → evidence_id → finding_id`. Missing binding, actor, fixture,
  observer, or compensation remains `BLOCKED`.
- `formal_customer_deliverable_count` and the
  `deliverable|candidate|rejected` classification projection are the only
  current-run product defect facts. Historical shelf counts are a separate
  scope and legacy readiness counters are diagnostic only.
- project campaign resources and ground-truth-free evaluation submissions are
  available under `/api/v1`; only an evaluator-owned receipt may change
  `NOT_MEASURED` to `MEASURED`.

These changes establish the engineering path to Gate D. They do not prove Gate
D: the evaluator-owned held-in, three held-out industries, clean target, paired
replay/shadow receipts, and frozen unit-cost baseline are still required.

The two nested loops are:

1. Discovery loop: source-grounded hypothesis → governed execution → runtime evidence → semantic/business verification → formal customer-deliverable defect.
2. Harness evolution loop: immutable execution traces → verifier-grounded weakness clusters → minimal bounded strategy proposals → paired replay and shadow evaluation → non-regressive promotion or rejection → next iteration.

This is a cross-industry platform goal. Customer names, benchmark answers, fixed
endpoint paths, industry-specific workflow names, and hard-coded business rules
are prohibited from reusable discovery behavior.

## Dual-loop module map (real code)

| Loop stage | Module | Status |
|---|---|---|
| Discovery execution / V12 pipeline | `ai_test_asset_center/v12_pipeline.py`, scan path in `__main__.py` | Implemented |
| Formal customer delivery | `customer_delivery_gate.py` | Implemented |
| Non-production write governance | `sandbox_write_executor.py` | Implemented (prod/unknown fail-closed) |
| Trace ledger (redacted) | `discovery_trace_ledger.py` | Implemented; wired post-scan in `__main__.py` |
| Weakness clustering | `discovery_weakness_miner.py` | Implemented; wired post-scan |
| Bounded Harness proposals | `discovery_harness_proposer.py` | Implemented; StrategyBundle-only edits |
| Strategy guardrails | `validate_strategy_guardrails` + `policy_wiring.py` | Implemented |
| Evaluator-private contract | `discovery_evaluation_contract.py` | Implemented |
| External scoring CLI | `tools/discovery_evaluation.py` | Implemented (`inspect` / `evaluate` / `aggregate` / `goal-status`) |
| Observed champion/challenger runner | `discovery_policy_evaluation_runner.py` | Implemented; authenticated one-target replay proven on 2026-07-16, commercial paired shape still missing |
| Promotion gate | `policy_evaluation_gate.py` | Implemented (non-regressive + hard blockers) |
| Evolution orchestration | `autonomous_evolution_orchestrator.py` | Partial: observed promote path exists; default `run_evolution_orchestrated` still stops at `AWAITING_OBSERVED_REPLAY_SHADOW` unless `evaluation_manifest_path` / `QUALIBUG_EVALUATION_MANIFEST` is supplied |
| Absolute Goal gate assessment | `assess_discovery_goal_status` in `discovery_evaluation_contract.py` | Implemented |
| Versioned private commercial GT dataset | evaluator-owned manifest with ≥3 held-out industries | **Missing in-repo** → commercial quality remains `NOT_MEASURED` |

## Current baseline status

**Commercial claim status: `NOT_MEASURED`.**

Gate A/B/C engineering readiness is implemented and locked by tests. Absolute
Gate D / controlled pilot / full-autonomy GA quality remains `NOT_MEASURED`
until an evaluator-private commercial-shape manifest is frozen and four paired
replay/shadow reports are produced by the observed runner.

The repository does not yet contain a complete versioned private manifest with
held-in, three-industry held-out, intentionally clean, replay, and shadow run
receipts. Therefore no commercial discovery-rate, precision, reproduction, or
unit-cost claim may be published from this checkout alone.

### Authenticated 131-Bug checkpoint (2026-07-16)

The evaluator-owned one-target replay `observed-131-agent-20260716-14` completed
against manifest `v0.6-windows-native-stable-131bugs-20260716`. The independent
report (`qualibug.discovery-evaluation-report.v2`), target receipt
(`qualibug.discovery-evaluation-receipt.v3`), and trusted observation pack
(`qualibug.evaluator-trusted-observation-pack.v1`) all passed fingerprint and
HMAC verification. The immutable report fingerprint is
`2c1dcafc31caf88e380926a564acf0996a5f76bf18219cd291c3fa7475eba200`;
the run identity is `RUN_da5b8649335d29996d7920ad`.

This is real held-in diagnostic evidence, not a commercial promotion result:

- hidden-GT outcome: TP `3`, FP `48`, FN `128`, Recall `0.0229`, Precision
  `0.0588`, micro F1 `0.033`;
- evaluator-observed transport: `1518` target requests, `446` writes, `0`
  production requests; cleanup failures, safety incidents, and dirty test
  environments were all `0`;
- execution: `311` obligations selected, `121` executed, `190` blocked,
  execution success `0.7961`, engine success `1.0`, duplicate rate `0.2609`;
- evaluator-private first-loss diagnosis: `104` at hypothesis generation, `23`
  diagnostically ambiguous, `1` at execution, and `3` delivered; these labels
  are post-run evaluator evidence and must never enter product runtime inputs;
- dominant terminal losses: `101` non-reversible writes, `72` missing
  observers, `10` missing bindings, and `7` missing fixtures;
- wall time was `335.625s`; provider cost was not reported, so unit cost remains
  unknown; reproduction success was `0` and pipeline health was `DEGRADED`.

The engineering loop is therefore proven end to end for this target, including
external network attestation and hidden-GT scoring. Its current effect is weak
and must not be described as production-ready discovery quality. Machine gate
assessment passes implementation Gates A-C but keeps Gate D, controlled pilot,
and GA at `NOT_MEASURED`: there is no held-out seeded target, no intentionally
clean target, no three-industry held-out set, no frozen unit-cost baseline, and
no required sequence of paired non-regressive windows.

### Issue #8 single-variable checkpoint (2026-07-18)

The first Issue #8 iteration kept one implementation variable: preserve the
exact executed obligation identity from a selected obligation through blocked,
harness-failed, and delivered execution receipts, then make evaluator network
attestation join on that executed identity. The change is generic identity
lineage; it does not inspect benchmark ground truth, paths, labels, match terms,
or expected defects. Unknown gateway identities now fail with the exact opaque
execution identity so the next mismatch is observable instead of anonymous.

The evaluator-owned one-target replay completed with run
`RUN_3a8176d328ca436b1c3b425a`. HMAC validation passed for report fingerprint
`d1d88ecd9cd0106c95a4d2360f1b11dba30544c5b1b974b10a8e085649e53ded`
and target-receipt fingerprint
`cf38b2f85a1abc912c8a44b1dbd8ef2335f0cc01554d5e0d93fffc958839c1bc`.
The independent execution attestation was `VERIFIED` across `432`
request-bearing attempts, `1741` observed target requests, and `381` writes;
production requests, cleanup failures, safety incidents, and dirty test
environments were all `0`, and the governed fixture cleanup succeeded.

The source-backed semantic linker was also verified in the product run and
accepted `181` bounded rule-to-interface relationships from visible source
identities. That produced `688` terminal obligations, `313` executed
obligations, and `53` formal canonical defects internally. These are diagnostic
counts only. The external report is `NOT_MEASURED` with reason
`obligation_campaign_degraded` because `13` attempts terminated as
`CONTRACT_ORACLE_HARNESS_FAILED`; therefore TP/FP/FN were intentionally omitted.
This iteration is **rejected for policy promotion**. It proves and retains the
identity-integrity repair, but it does not claim a discovery-quality gain and
cannot replace a measured champion/challenger replay-and-shadow comparison.

The older completed `llm_throughput` artifact is also a real single-target
diagnostic run, but it is not a promotion baseline: it contains 91 saved
findings, and a post-run evaluation with the current customer-delivery gate
accepts only 34 after failing closed on missing, failed, not-applicable, or
non-reversible cleanup evidence. Pipeline health is `DEGRADED`; 245 of 249 write
receipts required the governed post-run target reset. These counts diagnose the
funnel and cleanup gap only and must not be used as a commercial capability
claim.

Cleanup engineering note (2026-07-10): sandbox create cleanup now prefers a
source-documented DELETE, then a documented compensating action on the same
collection (for example `POST …/{id}/cancel` from merged OpenAPI+Markdown
catalogs). Verb-terminal action POSTs are classified as `not_required` rather
than irreversible creates. After a proven campaign reset,
`finalize_after_cleanup` clears cleanup-driven `DEGRADED` when no other
degradation signals remain. Re-run a real scan to measure the new incomplete
cleanup rate; do not reuse the 245/249 figure as current capability.

The benchmark runner emits an evaluator submission and reports `NOT_MEASURED`;
it does not open ground truth or calculate quality metrics in the discovery
process. The external evaluator is the only scoring authority.

Machine-check the current Goal posture without inventing numbers:

```powershell
python tools/discovery_evaluation.py goal-status
python tools/discovery_evaluation.py goal-status --report <measured-aggregate-report.json> --baseline-cost-per-true-positive-usd <frozen-baseline> --consecutive-non-regressive-windows <n>
```

### External private GT (diagnostic only — not commercial Gate D)

When a private ground-truth package already exists outside the repo (for
example the desktop enterprise benchmark `hidden_ground_truth/bugs.json`),
score a completed run envelope with the formal matcher. Keep GT paths out of
discovery runtime prompts and context:

```powershell
python -c "from pathlib import Path; import json; from ai_test_asset_center.benchmark_compute import compute_benchmark; print(json.dumps(compute_benchmark(findings=json.loads(Path('_funnel_runs/llm_throughput.evaluation_submission.json').read_text(encoding='utf-8'))['scan_result']['findings'], ground_truth_path=r'<EXTERNAL>/hidden_ground_truth/bugs.json'), ensure_ascii=False, indent=2))"
```

That diagnostic does **not** unlock Gate D. Commercial MEASURED status still
requires a versioned private multi-industry manifest plus aggregate report.

### Build evaluator-private manifest scaffolding (external paths)

Do not copy GT into the repo. Point the builder at external OpenAPI/PRD/GT
paths; GT is written only into `evaluator.ground_truth_ref`:

```powershell
python tools/build_discovery_evaluation_dataset.py `
  --output-root <PRIVATE_OUTPUT> `
  --dataset-id cross-industry-private-evaluation `
  --dataset-version <immutable-version> `
  --environment-type sandbox `
  --reset-method POST --reset-path /__reset --observation-path /__state `
  --external-target "held-in-1|ecommerce|held_in|seeded_defects|<OPENAPI>|<PRD>|<GT_BUGS_JSON>|<BASE_URL>" `
  --external-target "held-out-1|saas|held_out|seeded_defects|<OPENAPI>|<PRD>|<GT>|<BASE_URL>" `
  --external-target "held-out-2|mes|held_out|seeded_defects|<OPENAPI>|<PRD>|<GT>|<BASE_URL>" `
  --external-target "held-out-3|finance|held_out|seeded_defects|<OPENAPI>|<PRD>|<GT>|<BASE_URL>" `
  --external-target "clean-1|ecommerce|held_out|clean|<OPENAPI>|<PRD>||<CLEAN_BASE_URL>"
```

Then inspect (runtime views must omit GT) and, after paired observed
replay/shadow runs produce an aggregate report:

```powershell
python tools/discovery_evaluation.py inspect --manifest <PRIVATE_OUTPUT>/evaluation_manifest.json
python tools/discovery_evaluation.py goal-status --report <aggregate-report.json> --baseline-cost-per-true-positive-usd <frozen-baseline> --consecutive-non-regressive-windows <n>
```

## Evaluation single source of truth

The evaluator-private dataset manifest uses schema
`qualibug.discovery-evaluation-dataset.v1` and separates each target into:

- `runtime`: environment identity plus immutable input, fixture, and context artifact references;
- `evaluator`: hidden ground-truth reference for seeded-defect targets only.

The discovery process receives `build_runtime_view(...)`, which contains no
evaluator object, ground-truth path, or ground-truth fingerprint. Ground truth
is opened only by the external evaluator after a completed run.

Every target must declare a known non-production environment. Production and
unknown environment types are rejected when the manifest is loaded.

A commercial dataset shape requires:

- at least one held-in seeded-defect target;
- at least one held-out seeded-defect target;
- at least one intentionally clean target;
- at least three distinct held-out industries.

Missing ground truth, missing pipeline health, a failed-safe pipeline, missing
receipts, or incomplete target coverage produces `NOT_MEASURED`; it never
produces a zero-Bug or zero-false-positive claim.

## Required run envelope

The external evaluator accepts a completed run envelope. All operational fields
are measured inputs; the evaluator does not invent defaults.
For per-Bug loss diagnosis, supply the redacted immutable discovery Trace Ledger
from the same run with `--trace-ledger` (or embed it as `trace_ledger`). Its run,
policy, target, mode, and redaction contract must match exactly.

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
python tools/discovery_evaluation.py goal-status --report <immutable-report.json> --baseline-cost-per-true-positive-usd <frozen-baseline>
```

The evaluator output deliberately omits the ground-truth source path. When the Trace Ledger is present, `metrics.stage_loss_diagnostics` reports every hidden Bug's first loss stage across hypothesis generation, endpoint binding, selection, execution, Oracle evaluation/resolution, and formal delivery. These diagnostics never change TP/FP/FN scoring.

Product-side Fact→Experiment Phase 1 adds GT-free `qualibug.fact-experimentability-ledger.v1` and `qualibug.fact-first-loss-ledger.v1` (see `docs/FACT_EXPERIMENTABILITY_FIRST_LOSS.md`). Authenticated evaluation may also attach `metrics.fact_first_loss_diagnostics` (`qualibug.evaluator-fact-first-loss-ledger.v1`) by mapping stage-loss rows onto the SPEC first-loss enum; that join remains diagnostic-only and never changes TP/FP/FN.

## North-star metrics (priority order)

Only formal customer-deliverable findings may enter commercial scores. Internal
candidate / confirmed / validated / funnel counts are diagnostic only.

1. **True Bug discovery rate** — held-out micro/macro recall on deliverable findings vs hidden GT.
2. **Effective reproduction rate** — `evidence_quality.reproduction_success_rate`.
3. **False-positive control** — held-out precision + clean-target P0/P1 deliverable FP = 0.
4. **Unit cost** — `operational.cost_per_true_positive_usd` (and Gate D ≥40% improvement vs frozen baseline).

## Promotion rule

Promotion requires four complete reports over the exact same frozen target set:

1. champion replay;
2. challenger replay;
3. champion shadow;
4. challenger shadow.

All input, fixture, context, runtime, environment, and manifest fingerprints
must match. Each target must have immutable champion and challenger run receipts
in both modes.

Hard blockers include:

- missing held-in, held-out, clean, replay, or shadow execution;
- incomplete evaluation or operational metrics;
- fewer than three held-out industries;
- any production HTTP request;
- any safety incident, cleanup failure, dirty test environment, or regression failure;
- any P0/P1 false positive on an intentionally clean target;
- any degraded target pipeline (`pipeline_degraded_targets > 0`).

Quality is non-regressive across held-in recall/precision/F1, held-out
recall/precision/F1, replay and shadow F1, macro/minimum industry recall,
evidence completeness, reproducibility, engine/execution success, duplicate
rate, unit cost, and wall-clock time. At least one measured discovery split must
improve; lower cost alone cannot promote a policy whose discovery ability did
not improve.

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
- `timeout_seconds >= 300`, `max_tokens >= 32768`, `MAX_HYPOTHESES >= 40`, and `max_workers = 4`. The
  hypothesis cap is a **floor**, not a fixed value: narrowing it is a direct code-level discovery
  breadth ceiling regardless of the result of any particular evaluator snapshot. Its
  runtime authority is `policy_wiring._REASONER_MAX_HYPOTHESES_PER_ENGINE`, which is written onto
  `stage_reason_all_v2` at budget-resolution time — that constant, not the module literal, is what
  production runs, and `tests/test_reasoner_static_guardrails.py` cross-checks the two;
- product ports: frontend `5174`, backend `8088`.

## Stage gates

Absolute thresholds are encoded in
`ai_test_asset_center/discovery_evaluation_contract.py` as
`CAPABILITY_BREAKTHROUGH_THRESHOLDS`,
`CONTROLLED_COMMERCIAL_PILOT_THRESHOLDS`, and
`FULL_AUTONOMY_GA_THRESHOLDS`, and assessed by `assess_discovery_goal_status`.

### Gate A — Evaluation integrity

**Status: IMPLEMENTED (engineering readiness).**

- Evaluator-private manifest, runtime redaction, immutable fingerprints, strict
  formal-defect scoring, clean-target scoring, receipts, aggregate reports, and
  paired promotion evidence are implemented and tested.
- Receipt/report/comparison artifacts are HMAC authenticated with explicit key
  rotation. Comparison validation reloads all four replay/shadow reports,
  revalidates their signatures and policy/mainline identities, and recomputes
  paired GT-free evidence and the promotion decision.
- Product scans run in an isolated subprocess with evaluator-secret environment
  variables removed. Real-I/O claims require a separately signed
  `qualibug.evaluator-execution-attestation.v1` assembled only from a trusted
  external observation provider; runtime self-reports cannot satisfy this gate.

### Gate B — Trace and weakness mining

**Status: IMPLEMENTED (engineering readiness).**

- Every candidate has one cross-stage identity from generation through formal accounting.
- Failures are clustered from verifier outcomes and causal trace signatures, not titles alone.
- Every cluster contains example trace receipts, impact, recurrence, preserved-good behaviors, and a proposed editable surface.
- Post-scan wiring persists ledger / weakness / proposal artifacts under `platform_outputs/<project>/discovery_evolution/`.

### Gate C — Bounded proposal and real runner

**Status: IMPLEMENTED (engineering readiness); commercial runs blocked on private dataset.**

- Each proposal is minimal, evidence-bound, versioned, and rejects edits to frozen surfaces.
- Champion/challenger replay and shadow execute automatically on identical frozen targets via `DiscoveryPolicyEvaluationRunner` when a commercial-shape private manifest, governed fixture controller, observed scan executor, and evaluator-owned trusted observation directory are supplied.
- Reject, promote, lineage, rollback, and post-promotion monitoring receipts are persisted by the observed promote path; default orchestrated loop still emits `AWAITING_OBSERVED_REPLAY_SHADOW` when no private manifest is available.

### Phase 3 — Contract Oracle and canonical defect truth

**Status: IMPLEMENTED (engineering architecture); external quality remains NOT_MEASURED.**

- Contract assertions and Oracle verdicts are tri-state; missing evidence and
  harness failures cannot become product defects.
- Every formal occurrence must pass Customer Delivery Gate v2 and bind to the
  obligation attempt ledger, mainline contract and evidence bundle.
- `canonical_defect_registry.py` is the only customer-visible identity authority.
  Canonical defect count and delivery occurrence count are separate namespaces;
  title/history/severity/confidence dedupe paths no longer affect current product
  counts or readiness.
- Command center, customer report, evaluator projection, campaign API and
  persistence consume the same canonical scope and fail closed when registry or
  evidence persistence is missing.
- This status does not claim improved Recall/Precision. Promotion still requires
  fresh externally signed replay/shadow receipts and trusted execution
  attestations.

### Gate D — Capability breakthrough

**Status: NOT_MEASURED until private commercial evaluation completes.**

- Hidden benchmark held-out macro industry Recall >= 30%.
- Held-out Precision >= 50%.
- Reproduction rate >= 90%.
- Held-out minimum industry Recall >= 15%.
- Clean-target P0/P1 false positives = 0.
- Unit cost per true positive improves by at least 40% from the frozen baseline
  (`--baseline-cost-per-true-positive-usd` required; missing baseline → NOT_MEASURED).

### Controlled commercial pilot exit gate

**Status: NOT_MEASURED.**

- Held-out macro industry Recall >= 50%.
- Precision >= 70%.
- Reproduction rate >= 95%.
- Every customer-visible defect has replayable real evidence and an audit receipt.
- Cleanup success = 100%; production writes = 0.
- All claims are generated from the evaluation SSOT and are visibly blocked when measurement is incomplete.

This gate permits a controlled private pilot with human release approval. It
does not permit a general-availability claim of fully autonomous, cross-industry
Bug discovery.

### Full-autonomy GA gate

**Status: NOT_MEASURED.**

- Held-out macro industry Recall >= 70%.
- Precision >= 80%.
- P0/P1 Recall >= 80% (tracked via benchmark high-value recall when measured).
- No held-out industry Recall below 50%.
- Clean-target P0/P1 false positives = 0.
- Reproduction rate >= 97%.
- Execution success and engine success are each >= 95%.
- Evidence completeness, governed write receipt coverage, and cleanup success are each 100%.
- At least three consecutive frozen evaluation windows show no measured regression
  (`--consecutive-non-regressive-windows`).
- Secret leaks, production writes, safety incidents, and dirty environments are all zero.

## Next engineering priorities (impact order)

The former `104/131` first-loss attribution and the precision/reproduction
values below are a historical evaluator checkpoint, not evidence of current
HEAD behavior. They must not be used to rank the current bottleneck until a
fresh authenticated replay binds the current mainline and source snapshot.

1. **Refresh the authenticated loss funnel on current HEAD**, keeping absent or invalid execution evidence `NOT_MEASURED`; do not carry the historical `104/131` attribution forward as a current fact.
2. **Validate the code-level comprehension repairs under replay**: lossless knowledge-world projection, section-fair grounded retrieval, project/root-bound chunk and memory lookup, 40-hypothesis active policy authority, and unique source-process-graph compilation. A coverage improvement claim requires the blocked/uncompiled obligation counts to fall on the fresh run.
3. **Raise single-target execution conversion** by eliminating current-run `BLOCKED_NON_REVERSIBLE_WRITE`, missing-observer, missing-binding, and missing-fixture root causes without benchmark-specific rules.
4. **Reduce externally measured false positives and make reproduction executable**; historical quality values are diagnostic context only, while internal finding counts remain an invalid optimization target.
5. **Instrument provider cost into every evaluation envelope**; wall time and request volume are insufficient when cost keeps unit-cost and cost-improvement gates unknown.
6. **Freeze the commercial external evidence shape and run paired policies**: held-in, at least three held-out industries, and clean targets followed by authenticated champion/challenger replay and shadow reports. Until then Gate D stays `NOT_MEASURED`.

## Non-negotiable product constraints

- Frontend port `5174`, backend port `8088` — never retarget in harness evolution.
- Non-production write probes only; production and unknown environments fail closed.
- No industry/customer business hardcoding in detectors, prompts, UI, or services.
- No fabricated bugs or fabricated evaluation numbers; incomplete measurement must surface as `NOT_MEASURED`.
