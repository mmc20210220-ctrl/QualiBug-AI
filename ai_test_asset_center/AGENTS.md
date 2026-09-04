# ai_test_asset_center — Module Contract

Module-level SSOT for package boundaries, hook registration conventions, and
risk points. Global conventions (launch authority statement, ports 5174/8088,
Critical Configuration Guardrails, non-production execution contract,
discovery-harness evaluation/commercial rules, brand direction) remain in the
root `AGENTS.md`. Do not duplicate those rules here; this file owns only
implementation-level facts inside this package.

## Package Boundary

`ai_test_asset_center` is the backend core: private-pilot HTTP service,
connector authorities, discovery pipeline (Behavior IR → obligations →
experiments → oracles → delivery gate), and evaluation/projection helpers.
Frontend lives in `frontend/`; evaluator-private surfaces live in
`benchmark_evaluator/` and `_private_eval/`; CLI tooling lives in `tools/`.

## Launch Authority & Service Composition

The launch authority declaration itself lives in the root `AGENTS.md`
(`ai_test_asset_center.private_pilot_entrypoint:run_server`, including
`qualibug-server`). This section owns only the composition facts:
`private_pilot_service` is the composition root and compatibility re-export
surface for the private pilot HTTP handler, not a direct launch path.

Handler behavior lives in mixins: `HttpRoutingMixin`,
`CredentialsHandlerMixin`, `IngestHandlersMixin`, `ScanHandlersMixin`,
`ContinuousHandlersMixin`, `OpsHandlersMixin`, `PageRenderMixin`,
`FindingUtilsMixin`, `LlmHealthMixin`, `ReportLoadingMixin`,
`CommandCenterBuilderMixin`, `AgentTaskHandlersMixin`, and `AuthScopeMixin`.
`AgentTaskHandlersMixin` is composed before the generic `HttpRoutingMixin` so
project-scoped Agent Task routes are first-class service behavior rather than
runtime method replacement. Support modules remain importable via the service
facade: `private_pilot_regression_projection.py`,
`private_pilot_defect_summaries.py`,
`private_pilot_command_center_helpers.py`, `private_pilot_scan_prep.py`
(includes ingest auto-scan), `private_pilot_continuous.py`,
`private_pilot_command_center_envelope.py`, `private_pilot_json_io.py`,
`private_pilot_debug_client.py`, `private_pilot_project_assets.py`,
`private_pilot_tenant_auth.py` (owns `PROJECT_SCOPE_HEADER`),
`private_pilot_campaign_projection.py`, and
`private_pilot_scan_aggregates.py`.

## Agent Task & Grounding Boundary

- `agent_task_store.py` owns the persistent project/tenant-scoped `AgentTask`
  lifecycle and Event Ledger. A task goal is orchestration context, never
  execution authority. `execution_run_id` remains empty until a later explicit
  binding to the existing execution mainline.
- `agent_task_grounding.py` owns read-only Agent Task grounding. It may consume
  only an already-materialized persisted Test Intelligence projection; the
  Agent Task path must never call the analyzer/build path to reparse documents,
  rebuild enterprise understanding, or invoke an LLM. Missing or stale
  understanding is a visible grounding blocker.
- Agent grounding reuses `ScanHandlersMixin._handle_scan_preflight` as the
  runtime-environment authority. Do not create a second Agent-specific
  credentials/source/base-url/environment/target-policy preflight.
- A selected Test Target is runtime-bound only when its real execution surface,
  Action binding, Observer binding, and Oracle binding are all grounded. Scan
  Preflight being ready does not make an ungrounded Test Target executable.
- `agent_task_grounding_store.py` persists pinned understanding identity,
  selected target snapshots, grounding blockers/summary and factual events.
  Grounding events are observable receipts, not hidden reasoning. Do not emit
  synthetic `EXECUTION_STARTED`, Observation, Oracle, Finding or Decision events
  before the corresponding existing runtime authority actually produces them.
- `analyze_requirements` tasks do not invoke runtime Preflight or Scan. They may
  pin an existing understanding snapshot and remain analysis-only.
- Re-grounding is idempotent for an unchanged grounding result and must not
  inflate the Event Ledger with duplicate receipts.

## Hook Registration Conventions

Enrichment and compatibility behavior registers through named hooks — never
by replacing host methods:

- Envelope enrichments register through `register_envelope_post_hook`
  (`private_pilot_command_center_envelope.py`) instead of replacing
  `_normalize_command_center_envelope`; the service keeps a thin dispatcher
  for call sites.
- Scan result repair, regression-suite refresh, and browser UI smoke register
  through `scan_post_hooks.register_scan_post_hook` on the public `scan()`
  wrapper (core body is `_scan_impl`) instead of replacing `__main__.scan`.
- Credential masking/encryption-key checks are first-class in
  `CredentialsHandlerMixin`; scan campaign-context binding is first-class in
  `scan()`, continuous discovery, and `ScanHandlersMixin`; deployment health
  uses `build_private_pilot_health_payload` in `HttpRoutingMixin`; customer
  report HTML is first-class in `PageRenderMixin`; display-ready
  no-fix-advice stripping is first-class inside `display_ready_formatter`
  (`_finalize_*_for_customer`).
- Compatibility installers for credentials, scan-campaign-context,
  deployment, customer-report, scan-result-repair,
  regression-suite-refresh, browser-ui-smoke, and display-ready
  no-fix-advice only record runtime-support status or register named
  post-hooks — they must not replace handler or formatter methods.
- Orphan product-path wrappers `p3_benchmark_scan_patch` and
  `scan_runtime_gate_patch` are removed; runtime-scenario blocking lives in
  `scan()` / `runtime_scenario_contract_gate`, and seed-bug scoring stays
  evaluator-owned.
- Chain-aware pilot discovery binds via
  `enterprise_pilot_runtime.set_real_project_discovery_runner`, not by
  replacing `run_real_project_discovery`. Customer delivery classification
  in that core imports `customer_delivery_gate.split_customer_delivery_tracks`
  directly; runtime patch installation must never be required for
  correctness.
- `run_v12_pipeline` binds System Behavior Space project/root context
  first-class via `system_behavior_space_context`; coverage learning reorder
  registers through `v12_legacy_schedule.register_slice_reorder_hook`.
  System Behavior Space enrichment registers through first-class hooks on
  `business_state_graph`, `semantic_scenario_generator`, `oracle_engine`,
  `v12_legacy_oracle_findings`, and `regression_runner` — not by replacing
  those modules' methods. Neither path may replace
  `v12_pipeline.run_v12_pipeline`.
- Finding enrichment registers through `register_finding_enricher`, not by
  replacing `run_v12_pipeline` symbols. BSG/SSG/Oracle/regression installers
  register the same first-class hook pattern (`register_bsg_*_hook`,
  `register_scenario_enricher`, `register_oracle_*_hook`,
  `register_*_hook` on `regression_runner`). Structured regression oracles
  register through `register_probe_oracle_enricher` and
  `register_structured_oracle_judge_hook`.
- Structured cleanup/compensation process tracing is first-class in
  `experiment_cleanup_executor_core.py`: `[cleanup-trace]` events
  (trigger / decision / failure / recovery / result) carry the stable
  campaign / slice / obligation / experiment identity so one cleanup failure
  can be reconstructed from logs alone.
- Actor-token catalog resolution (`experiment_runtime_credentials.load_actor_tokens`)
  is run-level idempotent: within one scan process the catalog resolves once
  per (root, project, base_url, login-env, input-file fingerprint, TTL bucket)
  and is reused by every caller (batch executors, fixture materialization,
  observers). Rationale: CMP_77d5dfe1 (2026-08-22) measured 450 full-stale
  re-resolutions (~3150 `[STALE]` prints + repeated md-fallback login probes)
  in a single run — minutes of redundant file/HTTP churn between governed
  writes. File-fingerprint invalidation keeps operator edits authoritative;
  the resolver's own token-refresh persist maps its post-write fingerprint
  back to the resolving entry so it never self-invalidates;
  `QUALIBUG_ACTOR_TOKEN_CACHE_DISABLED=1` bypasses and
  `QUALIBUG_ACTOR_TOKEN_CACHE_TTL_SECONDS` (default 300) bounds reuse.
  `[STALE]` prints and the `declared_actor_tokens_expired` warning are deduped
  per (root, project, role/account / role-set) with a TIME WINDOW
  (`QUALIBUG_ACTOR_TOKEN_DEDUP_WINDOW_SECONDS`, default 600): repeats within
  the window are downgraded to debug, but each new scan past the window warns
  again — a long-lived backend must not lose the signal that a later scan
  also ran on stale credentials.
- Credential base_url authority (`_effective_target_base_url`): an omitted
  `base_url` argument falls back to `QUALIBUG_TARGET_BASE_URL`, then to the
  project's declared runtime config (`approved_base_url`/`base_url`). The
  same run's root cause was 450 base_url-less calls silently skipping the
  entire live-login branch (`if base_url:`) while every declared actor
  snapshot was expired — the whole campaign executed without credentials
  (mass `BLOCKED_MISSING_*`). Explicit caller values keep precedence; only
  operator-declared configuration is consulted, nothing inferred.
- Undeclared login endpoint stays undeclared:
  `project_runtime_config.load_real_project_config` defaults `login_api` to
  empty (previously it fabricated `"/auth/login"`, contradicting the
  credentials layer and reading as an operator declaration). Consumers that
  must discover an undeclared login path probe the shared candidate list
  `enterprise_credential_manager.COMMON_LOGIN_PATH_CANDIDATES` (single
  authority; outcome validation reuses it instead of skipping).
- Accepted-residue degradation (declared non-production targets only): a
  write without a source-declared compensator is no longer held hostage by
  cleanup guarantees. The compiler emits `accepted_residue` cleanup-plan
  entries; the disposable-fixture construction path mirrors the same
  ladder — `_auto_fixture_create_for_binding_target` attaches an
  `accepted_residue` marker only when the experiment's baked
  `environment_type` passes `is_nonproduction_environment`,
  `validated_fixture_setup` preserves the marker instead of refusing the
  setup, and the fixture-cleanup phase emits a `RESIDUE_ACCEPTED` contract
  receipt (a registered `_CONTRACT_EVIDENCE_STATUSES` value) with zero
  cleanup writes and a visible `residue_notice`. The delivery gate
  short-circuits all-residue cleanup contracts to `DELIVERABLE`. Residue
  is always declared and observable — never disguised as a real cleanup —
  and production/undeclared environments stay fail-closed at every layer.
- Subject selection is construct-first / reuse-fallback
  (`prefer_constructed_data`, wired in
  `experiment_fixture_materializer_core.py`): when a binding can be
  satisfied by a run-constructed fixture (declared or auto-discovered
  create with a compensator or accepted residue), construction is
  attempted before any list read, because a disposable subject can never
  damage data the customer depends on. Existing test-system data is the
  documented fallback — used when no construct option exists or the create
  fails — and the binding receipt records `data_subject_source`
  (`run_constructed` vs `existing_test_system_data`) with the disposable
  flag. Owner-identity, observed-body, state-scoped, and internal `__`
  targets keep their dedicated semantics and never take this path.

## Connector Event & Credential Boundaries

- Generic connector webhook callbacks are first-class event boundaries in
  `connector_webhook_events.py`: manifest-declared HMAC policy, encrypted
  `webhook_secret`, bounded fingerprint-only event ledger,
  duplicate/out-of-order suppression, visible calibration after sequence
  gaps, and invocation of the existing managed sync authority with `RETAIN`.
  Webhook delivery must never persist raw bodies, signatures, plaintext
  event IDs, credentials, source content, or cursors, and a generic HTTP
  route alone does not establish provider webhook support.
- Generic connector OAuth is first-class in `connector_oauth_authority.py`:
  a Manifest-declared authorization-code flow binds exact redirect URI,
  actor, profile reference, Manifest version, state hash, and S256 PKCE; the
  existing encrypted profile authority stores access/refresh tokens;
  callback replay, scope insufficiency, and provider failures remain visible
  without mutating source material, source identity, or checkpoints. OAuth
  routes must never persist authorization codes, raw state, PKCE verifiers,
  token values, or infer remote deletion from authorization loss. Connectors
  without a source-backed `oauth_schema` remain `NOT_SUPPORTED`.
- Generic OAuth automatic refresh is part of the same managed connector
  mainline: when a Manifest-declared access token is `EXPIRING` or
  `EXPIRED`, `grant_type=refresh_token` uses the encrypted profile
  authority, preserves source identity and checkpoints, rotates only
  encrypted credentials, and projects refresh failure or reauthorization
  without raw token values.
- Source-first onboarding is Manifest-driven: a connector may declare
  `quick_connect_schema` with a URL input and a required scope field, plus
  `entrypoint_evidence` containing only declared content types, structural
  document shapes, host suffixes, or path suffixes. The Materials page first
  uses the project-scoped read-only
  `POST /api/v1/projects/{project_id}/knowledge-connectors/source-preflight`
  to offer evidence-backed candidates, then requires confirmation when
  evidence is ambiguous; it must not infer providers, business rules,
  credentials, request bodies, or SSRF permissions. The preflight returns no
  source bytes, credentials, writes, or cursors and never configures a
  connector. The existing configure/test/managed-sync authority remains the
  only execution path; unsupported or ambiguous source inputs stay visibly
  failed or blocked.
- The generic knowledge-connector configuration route requires an explicit
  registered `connector_type` and always calls `configure_managed_connector`;
  missing type fails visibly with `connector_type_required`. Feishu
  configuration facades remain compatibility surfaces only and must never be
  an implicit default for unfamiliar sources.
- The source occurrence and resource projections expose only bounded
  provenance metadata: occurrence version, observation/update timestamps, a
  source-update marker when the connector declares one, an origin
  classification, and ACL scope/evidence status. Connector-backed identities
  are represented to ordinary users by a short fingerprint; raw remote
  resource IDs, source URLs, ACL principals, credentials, and cursors remain
  excluded. Materials must render these fields so users can distinguish
  source provenance, freshness, and permission gaps without opening raw
  connector payloads.
- Credential field labels are also Manifest metadata (`display_name`); UI
  formatting is only a generic fallback for missing labels and must never
  change credential storage keys or expose credential values.
- Operator authority-decision GET/POST routes are first-class behavior in
  `HttpRoutingMixin`. They must preserve tenant, project-scope, known-project,
  and mutation-role checks; runtime replacement of `do_GET` or `do_POST` is
  not an accepted route-integration mechanism.

## Implementation SSOT Registry

| Authority | Module |
|---|---|
| Runtime artifact lifecycle (content-addressed store, Run Manifests, evidence artifactization, reference GC, run-manifest post hook, unified Run retention) | `artifact_store.py` (ArtifactRef/LocalArtifactStore, canonical JSON, zstd, atomic writes, streaming put_file, sidecar metadata) + `run_manifest.py` (RunManifestStore, SPEC §15 commit ordering, failed-run policy, pinning, count retention) + `evidence_artifactization.py` (fine-grained evidence split + Dual-Read hydration) + `trace_artifactization.py` (Phase 4: trace ledger → TRACE_EVENT payload refs + TRACE_LEDGER metadata ref; hydration + Dual-Read round loader) + `intelligence_report_artifactization.py` (Phase 6: report = finding summary + artifact_refs; heavy payloads stored once; redaction parity) + `artifact_gc.py` (Phase 7: mark-and-sweep with reference-container expansion — EVIDENCE_BUNDLE_MANIFEST parts / TRACE_LEDGER attempt_refs / INTELLIGENCE_REPORT artifact_refs; dry-run default, real delete behind `QUALIBUG_ARTIFACT_GC_ENABLE=true`; re-verified liveness at delete) + `run_retention_manager.py` (Phase 8: RunRetentionManager — unified owner of run retention `QUALIBUG_RUN_RETAIN`/`QUALIBUG_FAILED_RUN_RETAIN`, GC orchestration, quota `QUALIBUG_ARTIFACT_MAX_GB`, scratch TTL `QUALIBUG_SCRATCH_TTL_HOURS`; never deletes artifacts by mtime, never touches knowledge.db) + `run_manifest_hook.py` (registered via `scan_post_hooks`; commits manifest then runs RunRetentionManager). `scan_result_retention.py` is the DEPRECATED legacy fallback used only in the store-disabled mode (SPEC §32) |
| Agent Task orchestration + read-only grounding | `agent_task_store.py` (task lifecycle/event ledger) + `agent_task_grounding.py` (persisted Test Intelligence snapshot pinning + existing Scan Preflight reuse; no analyzer/LLM/Scan execution) + `agent_task_grounding_store.py` (grounding persistence + factual idempotent events) + `private_pilot_agent_task_handlers.py` (project-scoped HTTP routes) |
| Target authorization | `target_policy.py` |
| Product defect truth | `discovery_quality_projection.py` |
| Project campaign contracts (`/api/v1` only) | `campaign_api_contract.py` |
| Module-retirement strangler inventory | `architecture_inventory.py` + `architecture_roots.json` |
| Canonical product defect identity | `canonical_defect_registry.py` (role-variant aggregation: the concrete treatment actor class is evidence, not identity — `_canonical_actor_relation` keeps only the relation type; concrete classes ride in `proof.evidence_actor_classes` and surface as `evidence_actors` on each canonical defect entry, so multi-role variants of one defect surface (same normalized operation + assertion kind + violation shape) collapse into ONE canonical defect whose `canonical_defect_id` is the cross-run stable role-variant aggregation key) |
| Evaluator contract implementation | `discovery_evaluation_contract.py` |
| Artifact persistence / redaction | `artifact_redactor.py` |
| Scan-envelope account ownership (workspace reconciliation) | `db_persistence.py` (`ensure_workspace_owned_project`) + `private_pilot_scan_handlers.py` (`_handle_v12_scan`) |
| Completion & funnel SSOT | `qualibug.obligation-attempt-ledger.v1` (sealed by the discovery mainline) |
| Discovery funnel closure | `discovery_funnel.py` |
| Chinese semantic frame SSOT (P0-A) | `enterprise_understanding/chinese_semantic_schema.py` (frame schema `qualibug.chinese-semantic-frame.v1`, slot statuses, reason codes, semantic signature; constraint signatures include typed coordinates but exclude raw wording, source lineage and resolution metadata) + `chinese_semantic_receipts.py` (typed content-addressed receipts) + `chinese_semantic_ledger_adapter.py` (fact → frame projection, `qualibug.chinese-semantic-frame-ledger.v1`) + `chinese_semantic_behavior_ir_adapter.py` (frame → Behavior IR projection; fixed action-deadline clauses bind only to one exact source-backed `TIMED_WAIT` whose target operation, typed window, observer, predicate and bounded policy all resolve) |
