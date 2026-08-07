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
`CommandCenterBuilderMixin`, and `AuthScopeMixin`. Support modules remain
importable via the service facade: `private_pilot_regression_projection.py`,
`private_pilot_defect_summaries.py`,
`private_pilot_command_center_helpers.py`, `private_pilot_scan_prep.py`
(includes ingest auto-scan), `private_pilot_continuous.py`,
`private_pilot_command_center_envelope.py`, `private_pilot_json_io.py`,
`private_pilot_debug_client.py`, `private_pilot_project_assets.py`,
`private_pilot_tenant_auth.py` (owns `PROJECT_SCOPE_HEADER`),
`private_pilot_campaign_projection.py`, and
`private_pilot_scan_aggregates.py`.

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
| Target authorization | `target_policy.py` |
| Product defect truth | `discovery_quality_projection.py` |
| Project campaign contracts (`/api/v1` only) | `campaign_api_contract.py` |
| Module-retirement strangler inventory | `architecture_inventory.py` + `architecture_roots.json` |
| Canonical product defect identity | `canonical_defect_registry.py` |
| Evaluator contract implementation | `discovery_evaluation_contract.py` |
| Artifact persistence / redaction | `artifact_redactor.py` |
| Scan-envelope account ownership (workspace reconciliation) | `db_persistence.py` (`ensure_workspace_owned_project`) + `private_pilot_scan_handlers.py` (`_handle_v12_scan`) |
| Completion & funnel SSOT | `qualibug.obligation-attempt-ledger.v1` (sealed by the discovery mainline) |
| Discovery funnel closure | `discovery_funnel.py` |
| Chinese semantic frame SSOT (P0-A) | `enterprise_understanding/chinese_semantic_schema.py` (frame schema `qualibug.chinese-semantic-frame.v1`, slot statuses, reason codes, semantic signature) + `chinese_semantic_receipts.py` (typed content-addressed receipts) + `chinese_semantic_ledger_adapter.py` (fact → frame projection, `qualibug.chinese-semantic-frame-ledger.v1`) + `chinese_semantic_behavior_ir_adapter.py` (frame → Behavior IR projection) |
| Chinese clause structure (P0-B) | `enterprise_understanding/chinese_context_envelope.py` (block coordinates over `document_structure_assets`: section paths, list-stack ancestor chains, table row/column headers, unique quote lookup — structure only, zero business inference) + `chinese_clause_parser.py` (atomic clause trees `qualibug.chinese-clause-tree.v1`: enumeration action candidates, negation scope, condition leaves, exception nodes; language-function words only, no industry vocabulary) + `chinese_semantic_frame_compiler.py` (frame enrichment: list-parent condition inheritance, table header mention injection, enumeration mentions, exception merge; signature recomputed; idempotent) |
| Chinese context resolution (P0-C) | `enterprise_understanding/chinese_context_resolver.py` (frame-level omitted-actor recovery from unique evidence — only-if subject, unique prior frame in the same section, unique section heading; mention-level coreference: 该X/本X/此X explicit same-sentence nouns, bare pronouns need exactly one candidate; `document_context` section/list/neighbor population; UNKNOWN never force-bound; raw text never rewritten) |
| Concept & grounding (P0-D) | `enterprise_understanding/business_concept_registry.py` (explicit-evidence concept layer: label→canonical with priority understanding_model > identity_registry > permission_matrix > data_tables; similarity never merges) + `chinese_semantic_grounding.py` (evidence chains per SPEC §12.2: actor = permission matrix > roles > UI contract > concept registry; operation = rule ref > summary verbatim > description > rule_to_interface > formal UI contract > structural entity+CRUD; entity = declared object labels/aliases; state = field-description enum / state machines; scope = ownership phrases → structured OWN; every binding has a typed GROUNDED/AMBIGUOUS/UNKNOWN receipt) |

Chinese semantic frame contract (P0-A): the frame ledger is projected in
`composition.py` after the second cognition pass (both full and incremental
paths), so actor/entity registries are final before exact-match grounding.
`behavior_ir_core.build_behavior_ir_from_knowledge_asset` consumes the frame
ledger as an advisory channel: only GROUNDED frame slots may contribute
relations, contributions merge by deterministic node id (legacy relations are
never overwritten), provenance rides in `source_refs`
(kind=`chinese_semantic_frame`), and the projection receipt is stored as
`model["semantic_frame_projection_receipt"]`. In P0-A production frames are
ungrounded (`TECHNICAL_GROUNDING_PENDING`), so the channel adds nothing; the
grounding engine (P0-D) activates it. P0-A adds no Chinese vocabulary: no
word lists, role tables or action patterns; slot mapping is typed-only, and
the raw ownership phrase is preserved as evidence but excluded from the
semantic signature. Legacy Chinese parsing remains authoritative until P0-E.

Chinese clause structure contract (P0-B): the three stages run in
`composition.py` right after the frame projection (full and incremental
paths): envelope → clause trees → frame enrichment. They are CANDIDATE
layers — they add structure the facts missed (inherited conditions, table
header mentions, enumeration action candidates, exception nodes) but never
override fact-derived slots and never bind semantics to technical objects
(that is P0-D grounding). Only language function words (modality, negation,
enumeration, condition/exception markers per SPEC §9.1/§9.4) are used; no
industry terms, role names or benchmark vocabulary. Ambiguity is explicit:
`CLAUSE_SEGMENTATION_AMBIGUOUS` / `NEGATION_SCOPE_AMBIGUOUS` /
`CONDITION_SCOPE_AMBIGUOUS` / `EXCEPTION_SCOPE_UNRESOLVED`, never a forced
guess; "未发货"-style state negations are conditions, never prohibitions.
The frame semantic signature is recomputed after enrichment (conditions and
actor mentions are typed slots), keeping frames fail-closed valid; the
Behavior IR channel is untouched, so P0-B introduces zero production
behavior change until grounding activates the frame channel.

Chinese context resolution contract (P0-C): runs in `composition.py` right
after the frame enrichment (full and incremental paths). It complements the
fact-level resolvers (`_chinese_document_context` / `_document_ir_context`),
never duplicating them: frame candidates come from the clause tree
(只有…才 subject), prior frames in the same section (envelope order +
section_block_ids), and section heading titles (known actor names from the
understanding model / identity registry / same-source frame mentions,
alias-aware, longest-surface-name display). A candidate is injected only
when unique; ambiguity adds `MULTIPLE_ACTOR_CANDIDATES` and the slot stays
OMITTED with `OMITTED_ACTOR_UNRESOLVED`. Coreference stays mention-level:
该X/本X/此X name their referent explicitly in the raw text (never rewritten);
bare pronouns (其/上述/…) resolve only with exactly one frame mention, else
`COREFERENCE_UNRESOLVED`. Actor mentions are NOT part of the semantic
signature, so P0-C never changes signatures or the Behavior IR channel.

Concept & grounding contract (P0-D): `ground_semantic_frames` runs in
`composition.py` right after context resolution (full and incremental paths).
It ACTIVATES the P0-A Behavior IR channel: grounded frames now contribute
owns/permits/denies relations. Grounded refs are emitted in IR-resolvable
forms — actor role names, `METHOD:path` operation forms (interface ids
converted), declared entity labels/aliases (the IR builder keeps ASCII entity
names only; Chinese mentions resolve only through the operator-declared
lexicon alias groups). Frame relations canonicalize endpoints through the
node reference index and carry the permission-row scope, so they dedup
against legacy relations instead of duplicating them; genuinely new grounded
relations (e.g., ownership the legacy field phrases missed) are added with
full grounding receipts in source_refs. Multiple candidates are AMBIGUOUS
with MULTIPLE_*_CANDIDATES (never first-item picks); word-list guessing
(CJK field tokens, semantic classification tables, containment scoring)
never grounds anything. The semantic signature is recomputed after scope
structuring; frames stay fail-closed valid. The 131-bug benchmark re-run is
the required verification gate for this activation and is executed
separately from the unit/CI gates.


Side-effect-free import rule: importing `ai_test_asset_center` must be
side-effect free. Runtime scenario contracts are validated and explicitly
compiled into the V12 path; product `scan()` rejects evaluator-private
seed/observation fields. Evaluator scoring must never be installed through
product package import or product runtime patches.

- Target authorization: environment identity and environment type are
  separate required facts; localhost or an environment name must never imply
  write safety. Project preflight, V12 runtime, API/DB/UI adapters, and
  sandbox writes must consume the same `TargetPolicyDecision`.
- Product defect truth: current-run `deliverable|candidate|rejected`,
  current campaign, and historical shelf are separate scopes; legacy
  readiness counters are diagnostic only and cannot replace
  `formal_customer_deliverable_count`.
- Project campaign contracts are exposed only under `/api/v1`. Evaluation
  submissions must be Ground-Truth-free, pass `artifact_redactor.py`, and
  stay `NOT_MEASURED` until an external evaluator receipt is verified.
- Workspace reconciliation: workspace-provisioned projects (directory
  provisioning, no account-registry row) are registered idempotently by the
  governed scan path only when the request principal is
  `local_development` (loopback-bound). Credential-authenticated principals
  are never auto-provisioned — they must register through the account API.
  Created tenant rows carry discarded random credentials (identity rows
  only); a foreign username conflict fails closed instead of binding the
  workspace to the wrong account.
- Python-module retirement uses the non-destructive strangler inventory in
  `architecture_inventory.py`, with roots and responsibility overrides in
  `architecture_roots.json`. Architecture counts are diagnostic only and
  must never become discovery-quality claims. Per-root runtime evidence is
  collected by `tools/collect_architecture_import_trace.py`, which marks a
  root complete only after a real successful Python process observes the
  declared module and, when declared, executes the exact callable. Runtime
  trace roots cover declared product/evaluation/tooling authorities, project
  scripts, and active discovery entrypoints; test modules remain static
  reachability roots and are verified by the separate passing-test gate
  rather than hundreds of synthetic import-only sessions. A runtime import
  trace is trusted only when its content is authenticated with an
  evaluator-owned HMAC key stored outside the product workspace; unsigned or
  invalid traces fail closed. Even a complete authenticated trace can
  advance a candidate only to manual deletion review. No module may be
  deleted automatically: static unreachability, a complete
  supported-entrypoint runtime import trace, resolved dynamic-import
  uncertainty, passing tests, and manual deletion review are all required.
  The operating procedure lives in `docs/DISCOVERY_MODULE_STRANGLER.md`.
- Canonical defect identity: `canonical_defect_count` is customer-visible
  unique truth; `delivery_occurrence_count` is audit evidence only. Titles,
  severity, confidence, historical rows, and delivery occurrences must never
  create a parallel customer-visible defect identity or readiness count.
- Importing `ai_test_asset_center` must be side-effect free. Runtime
  scenario contracts are validated and explicitly compiled into the V12
  path; product `scan()` rejects evaluator-private seed/observation fields.
  Evaluator scoring must never be installed through product package import
  or product runtime patches.

## Discovery Runtime Module Notes

Implementation-level pipeline facts (the governance, evaluation, and
commercial rules stay in the root Discovery Harness Evolution Contract):

- Discovery mainline authority is selected before campaign creation and
  frozen in `qualibug.discovery-mainline-run.v1`. The public
  `v12_pipeline.run_v12_pipeline` function is a compatibility wrapper that
  invokes `discovery_mainline.run_discovery_mainline` exactly once. It must
  never retry with, fall back to, or switch to the other authority after an
  exception. Legacy schedule, scenario HTTP execution, oracle-finding, and
  other compatibility helpers live in `v12_legacy_schedule.py`,
  `v12_legacy_scenario_exec.py`, `v12_legacy_oracle_findings.py`, and
  `v12_compat_helpers.py` and are re-exported from `v12_pipeline` — they are
  not on the `experiment_candidate` delivery path. Runtime/fuzzer/slice/DB
  helpers SSOTs are `pipeline_runtime.py`, `pipeline_fuzzer.py`,
  `pipeline_slices.py`, and `pipeline_db.py` (explicit imports; `import *`
  must not be used for `_`-prefixed symbols). Industry coupon DB sampling
  was removed from the product path.
- Binding closure and space exploration are first-class planning-stage
  enrichments in `discovery_runtime_planning.py`. The planning function
  constructs a `BindingLedger` from Behavior IR via `binding_builder`,
  resolves conflicts via `binding_conflict_resolver`, and runs
  `binding_completeness_gate` before experiment compilation. Non-COMPILED
  experiments that fail the gate receive `BLOCKED_MISSING_BINDING` with
  sub-codes (`FIELD_NOT_BOUND`, `ENTITY_NOT_BOUND`, `FIXTURE_NOT_BOUND`,
  `RELATION_NOT_BOUND`, `ACTOR_NOT_BOUND`, `STATE_NOT_REACHABLE`);
  already-COMPILED experiments are never downgraded. The plan bundle emits
  `binding_closure_receipt` (schema `qualibug.binding-closure-receipt.v1`)
  and `space_exploration_receipt` (schema
  `qualibug.space-exploration-receipt.v1`). Space coordinate annotation
  (`space_coordinate`), invariant graph, operator registry, combination
  generator, and coverage-guided reorder all run within the existing budget
  envelope — no new budget expansion. Runtime binding probes are
  contract-gated and remain `PROBES_SKIPPED_CONTRACT_NOT_APPROVED` until
  the runtime contract is explicitly approved.
- Isolation-family identity binding uses caller-scoped `/me` semantics in
  `experiment_compiler_obligation_core.py`: a `GET`/`HEAD` `*/me` operation
  in Behavior IR is bound to every arm actor explicitly declared by the
  obligation (`owner_actor_ref`/`viewer_actor_ref`/`control_actor_ref`/
  `treatment_actor_ref`), because a `/me` endpoint returns the callers own
  identity and arm isolation is enforced at runtime by executing each arm
  with its own credentials. An explicit `actor_ref` on the operation still
  wins. When the IR declares no `/me` operation, the compiler falls back to
  a caller-scoped owned-entity read (`_owned_entity_identity_resolver` in
  `experiment_compiler_support.py`): a source-declared `owns` relation must
  tie the control actor to a collection `GET`/`HEAD` whose observed entity
  declares the ownership field, and the resulting binding
  (`source_priority=owner_identity_owned_entity_read`,
  `identity_extraction=owner_field_consensus`) pins
  `fixture_owner_actor_ref` to the control actor so the runtime resolves
  with the owner's own credentials. At runtime
  (`consensus_identity_value` in `experiment_runtime_support.py`) the
  identity binds only when every observed row agrees on the owner field;
  disagreement fails closed with `owner_identity_conflict` and never falls
  through to fixture creation. When neither `/me` nor a qualifying owned
  read exists — or no declared arm actor is present in the actor registry —
  the experiment remains visibly `BLOCKED_MISSING_BINDING`
  (`owner_identity_resolver_missing`); no identity is ever inferred.
- Write operations that declare a request schema but no request example may
  source the body from observed data instead of blocking with
  `source_declared_request_body_missing`. `_observed_write_body_resolver`
  (`experiment_compiler_support.py`) requires a source-declared collection
  `GET`/`HEAD` on the write's own collection whose observed entity declares
  every required body field; the compiler emits a `__observed_body` binding
  (`source_priority=observed_entity_write_body`,
  `body_projection_fields=<schema properties>`) pinned to the writer's
  credentials and suppresses synthetic schema-default bodies in family
  protocols via `defer_write_body_to_runtime`. At runtime
  (`project_observed_body` in `experiment_runtime_support.py`) the body is
  projected from ONE observed row (best field coverage wins) using only
  schema-declared field names — the environment's own data, never
  synthesized values; the plan-step executor merges it under step-compiled
  fields (which win on conflict), and the pre-transport required-field
  gate still blocks visibly with exactly the fields the observed data
  could not supply. When no qualifying collection read exists, the
  historical visible block is preserved unchanged.
- `deep_experiment_planner` and `deep_experiment_protocol_adapter` are
  diagnostic-only research surfaces. They are not imported or invoked by
  `discovery_runtime_planning`; a heuristic deep plan must never replace a
  compiler-blocked experiment. Product execution may advance only
  experiments compiled from exact source actors, operations, request bodies,
  bindings, observers, assertions, and cleanup authority.
- Product-mainline business-semantic joins require exact source identities
  or accepted `agent_semantic_linker` rule/interface identities.
  State-name/path similarity, invariant field/entity overlap, and
  permission-matrix vocabulary must never create operation references, API
  paths, request schemas, request examples, or executable experiments.
  Unresolved joins remain visible coverage gaps; `invariant_operation_binder`
  and `field_level_golden_rules` are diagnostic-only. The product default
  invariant graph contains only source-backed Behavior IR invariants and
  must not inject universal authorization, idempotency, conservation, or
  lifecycle claims. Relation correlation keys are source declarations; they
  must not be derived from entity names or relation vocabulary.
- Multi-layer observation and cross-surface evidence are execution-stage
  enrichments in `experiment_executor.py`. After typed observers complete,
  `multi_layer_observation.check_observation_completeness` emits
  `qualibug.multi-layer-observation.v1` and
  `cross_surface_oracle.detect_emergent_violation` emits evidence-only
  cross-surface receipts. Cross-surface oracle is NOT a discovery
  authority: it cannot produce formal findings, activate contract oracles,
  or influence the customer-delivery gate. It only enriches the observation
  record for downstream diagnostic use.
- `experiment_candidate` planning and execution live in
  `discovery_runtime.py`; selected experiments execute only through
  `experiment_executor.execute_selected_experiments`. Contract oracle
  evaluation on that path is the customer-delivery authority; the legacy
  `OracleEngine` registry is diagnostic only and must not auto-attach
  industry oracles from path, entity, or domain heuristics. The legacy
  domain may run only when `legacy_champion` was explicitly selected before
  the run and a gate-verifiable legacy runner is installed. The product
  `run_v12_pipeline` wrapper currently installs only
  `experiment_candidate`; selecting unavailable `legacy_champion` fails
  closed with `mainline_runner_unavailable` before campaign creation. The
  execution-policy default is `experiment_candidate` because that is the
  only installed product runner. Operators may still select `legacy_champion`
  only when a gate-verifiable legacy runner is installed before the next
  immutable run contract; never switch authority inside an active campaign.
- `qualibug.obligation-attempt-ledger.v1` is the completion and funnel SSOT.
  Every selected, blocked, or deferred obligation must have exactly one
  terminal attempt with a reason code. Zero selected obligations and
  all-blocked runs remain visibly `BLOCKED`; empty findings from them must
  never be interpreted as a defect-free target. Funnel closure projections
  must stay on that ledger: `discovery_funnel.py` owns the receipt-only
  conservation check, explicit reason-code registry profile, and redacted
  JSON/Markdown loss report. Missing identity or stage receipts remain
  visible as `INCOMPLETE`/`FAILED_SAFE`; internal funnel counts never become
  recall, precision, or defect-free claims.
- Mainline-owned stage receipts bind the immutable run identity through
  `bind_stage_receipt_identity` before ledger sealing. An unhandled runner
  exception must remain the original propagated exception while emitting a
  terminal `HARNESS_FAILED` attempt with `MAINLINE_RUNTIME_EXCEPTION` when
  the campaign persistence authority is available; request transport and
  cleanup remain `NOT_PROVEN`/`UNKNOWN` rather than being inferred.
- A path placeholder may compile only when its binding plan names an exact,
  source-declared concrete `GET`/`HEAD` operation from Behavior IR. Runtime
  materializes that value with the control actor before control/treatment,
  emits a fingerprint-only binding receipt, and reuses the same resource
  value for both paths. Missing, invalid, or unsuccessful resolvers remain
  visibly `BLOCKED_MISSING_BINDING`; invented identifiers and hidden seed
  reads are prohibited.
- Idempotency and concurrency obligations must come from an explicit source
  invariant joined to an exact Behavior IR operation. A write method alone
  is not evidence that an idempotency or concurrency contract exists;
  blanket write-effect obligations are forbidden.
- Behavior IR operation identity is canonicalized by service + method +
  normalized path template. Duplicate source operation aliases are retained
  as source references and emitted as explicit conflict receipts; they must
  not silently overwrite one another. Markdown JSON/YAML/curl request
  examples must remain structured request examples, and an unbound state
  transition must emit a visible coverage gap rather than infer an
  operation. Behavior IR validation must report duplicate node identities as
  `duplicate_node_id:<collection>:<id>`; downstream compilers must never
  receive an IR graph where duplicate node ids were silently dropped or
  overwritten.
- Missing environment_type must not default to `test`. Unresolved
  actor/fixture/observer/cleanup compensation must be `BLOCKED`, never
  `COMPILED`. Global reset may set `environment_restored` but must preserve
  original `cleanup_failures`.

## Runtime Execution Contract Rules

Implementation-level runtime contract rules owned by this package (the
evaluator/commercial governance rules stay in the root Discovery Harness
Evolution Contract):

- Agent semantic linking may propose intent only between exact source-backed
  rule and interface identities. It receives the existing knowledge asset's
  source-grounded semantic frames, schemas, entities, fields, roles,
  permissions, and state machines in bounded batches; every rule must receive
  a terminal `LINKED`, `NO_EXECUTABLE_INTERFACE`, or `AMBIGUOUS` assessment.
  An accepted link must cite the exact rule, interface, and any supporting
  fact identities it used. The rationale remains synthetic intent and is
  never business fact or finding evidence. Unknown, duplicate, low-confidence,
  invalid-evidence, omitted, or over-budget proposals must be rejected or
  exposed visibly in `qualibug.agent-semantic-link-receipt.v1`; they must
  never enter Behavior IR silently. Malformed provider responses and provider
  failures remain fail-fast. Prompt payloads pass the shared artifact
  redaction boundary and must not include credential values or
  request-example values.
- Explicit UI execution requests are part of the `run_v12_pipeline`
  compatibility path and execute only through the governed UI adapter.
  Playwright locator intent must resolve to one visible DOM/accessibility
  candidate and emit `qualibug.multimodal-locator.v1` with both DOM and
  element-image fingerprints; ambiguous or missing candidates remain blocked.
- Evaluator fixture campaign identity and product mainline campaign identity
  are separate authorities. The evaluator-owned loopback gateway binds the
  product identity from complete correlation headers, strips those headers
  before forwarding, and seals exact per-attempt request/write counts outside
  the product workspace. A one-target authenticated diagnostic is evidence
  for that target only and can never bypass commercial-shape promotion gates.
- Product identity consistency may contain only product-owned scopes. The
  external evaluator must first validate every submitted scope against the
  independently rebuilt canonical and occurrence identities, then bind
  `formal_authority_occurrence_ids`, `evaluator_submission_occurrence_ids`,
  and `evaluator_submission_ids` itself. Missing evaluator-owned scopes in
  product output are not a product failure; conflicting submitted scopes
  fail closed.
- Behavior IR operation identity is canonicalized by service + method +
  normalized path template. Duplicate source operation aliases are retained
  as source references and emitted as explicit conflict receipts; they must
  not silently overwrite one another. Markdown JSON/YAML/curl request
  examples must remain structured request examples, and an unbound state
  transition must emit a visible coverage gap rather than infer an operation.
- Runtime interface discovery is a governed read-only planning round
  (`planning_round=0`). Candidate paths may be derived only from documented
  route vocabulary plus the deployment-owned semantic action policy. Anonymous
  `401/403` is not proof of route existence: discovery requires a correlated
  active test-actor confirmation, stops after the first conclusive response,
  excludes disabled/locked actors, and emits no finding by itself.
- A proven runtime interface may expand Behavior IR only through
  `qualibug.behavior-ir-expansion-round.v1`. The receipt binds input/output
  Behavior IR identities and exact observation fingerprints; only obligation
  identities absent from the immutable first round may enter
  `planning_round=2`, and those experiments execute only through
  `execute_selected_experiments`. Indeterminate or absent observations must
  terminate the round as `STAGNATED`, never invent an operation or rerun
  prior obligations.
- Runtime state snapshot observers (`before_state`, `after_state`,
  `final_state`) are implemented typed observers. They compile only when a
  source-declared effect read exists, emit fingerprinted state receipts from
  governed write before/after snapshots, exclude cleanup phases from
  experiment final state, and feed assertion DSL evidence through
  `experiment_executor.py`.
- Runtime `barrier_timeline` is an implemented typed observer, but it must
  remain fail-closed: only explicit barrier/timeline events with a release
  marker and at least two participants may produce an OBSERVED receipt.
  Sequential HTTP steps alone must remain INDETERMINATE and must never be
  treated as concurrency evidence.
- Runtime `typed_assertion` and `source_invariant` are implemented typed
  observers for contract lineage only. They prove that a bounded assertion
  kind and source-grounded invariant entered the runtime chain; they must
  not evaluate the business result or create a customer-visible finding by
  themselves.
- Concurrency executable experiments must compile as one control participant
  and one treatment participant sharing a `barrier_group`. The executor must
  release that group concurrently and emit explicit ready/release/completed
  timeline events; a sequential treatment-only plan is a protocol bug, not
  concurrency evidence.
- Contract oracle activation must require a control plan only when the typed
  assertion semantically requires control or the experiment actually contains
  control steps. State/validation single-write experiments may activate from
  source refs, treatment evidence, typed observers, and actor/fixture/cleanup
  receipts; authorization/isolation/visibility assertions still require
  control and remain fail-closed.
- Contract oracle activation is derived only from validated evidence
  receipts. Runtime code must never synthesize verified receipt identities,
  force `ACTIVE`, evaluate assertions after blocked activation, or convert
  `INDETERMINATE` into `PROPERTY_HELD`; customer delivery must reject
  activation/reference mismatches.
- Runtime bindings must try exact source-declared actor-bound `GET`/`HEAD`
  resolvers before governed disposable-fixture setup. A forced ownership
  fixture means setup is the fallback when the owner-scoped read yields no
  resource, not permission to skip that read. Resolver failure, unresolved
  placeholders, sandbox denial, and read-only mode must stop target write
  transport.
- Fixture create-body dependency resolution has three source-grounded legs,
  in priority order: declared HTTP `GET`/`HEAD` list-reads, then a declared
  database read (`adapter: db_sql`, `method: DB_READ`), then governed
  disposable dependency create. The DB leg is only derivable when a
  source-declared entity matches the placeholder stem by naming convention
  AND declares both a physical storage table and an identity column — an
  entity name alone never becomes a table name. Execution of the DB leg is
  read-only, gated by `persistence_read_allowed` (declared non-production
  environment), DSN-sourced from customer-declared `multi_service_config.json`,
  and identifiers are validated against the introspected schema; every
  refusal carries a named reason code and an empty observation never counts
  as a resolved value. Implemented in
  `runtime_binding_materializer_base.declared_persistence_resolver` /
  `validated_runtime_resolvers_with_receipts` and
  `experiment_fixture_materializer_core._run_declared_db_identity_read`.
  When the DB leg fails for environmental reasons
  (`persistence_config_invalid` / `persistence_read_not_permitted` /
  `persistence_source_not_declared` / `persistence_read_failed`) and the
  dependency still cannot be resolved, the blocked detail must name
  `dependency_db_read_unavailable:<leaf>:<reason>` (never
  `dependency_fixture_setup_not_generated`) and the fixture receipt carries
  `dependency_db_read_failures` — an environment fault must never masquerade
  as a fixture-generation capability gap.
- Runtime execution must preserve source-declared request bodies exactly.
  Concurrency, capacity, quantity, stock, balance, quota, and similar
  semantics may be exercised only when an explicit source invariant and
  bound request schema provide them; observed values must never be used to
  heuristically rewrite a compiled request.
- Customer-delivery and reproduction receipts must bind exact obligation,
  activation, observer, cleanup, and observation receipt identities. Prefix
  matching, variant matching, generated fingerprints, and synthesized request
  semantics are prohibited.
- Compatibility delivery gates must not waive cleanup because of path
  vocabulary, action-style routes, write operations labeled read-only, or
  observed database change. Every accepted write needs an exact governed
  cleanup receipt or explicit source-observed unchanged-state proof.
- Behavior IR validation must report duplicate node identities as
  `duplicate_node_id:<collection>:<id>`. Source-to-IR conversion may merge
  canonical operations with explicit source references, but downstream
  compilers must never receive an IR graph where duplicate node ids were
  silently dropped or overwritten.
- Conservation executable experiments must use a dedicated
  `conservation_write` protocol, not the generic treatment fallback.
  Conservation obligations require source lineage, typed/source-invariant
  receipts, entity-state snapshot evidence, and assertion DSL
  `before_values`/`after_values` derived from real governed before/after
  observations.
- Temporal executable experiments must use a dedicated `temporal_write`
  protocol plus `temporal_window` typed observer, not a generic HTTP
  treatment. Temporal windows must come from source-grounded
  assertion/property evidence; runtime convergence evidence must come from
  actual trigger/final-observed timeline events and feed assertion DSL
  `converged`/`within_window`.
- Cleanup requirement detection must compare all non-server-managed business
  fields on the same observed entity, not only fields echoed by the write
  response. A write response that returns only an identity still requires
  cleanup when governed before/after snapshots prove business state changed.
- Empty formal delivery projections may synthesize an explicit empty
  defect-identity consistency receipt only when there are zero delivery
  occurrences, zero canonical registry rows, and zero formal customer
  deliverables. Any non-empty formal delivery scope must validate real
  canonical identity consistency and fail closed on missing or mismatched
  scopes.
- Adaptive obligation planning binds the configured slice limit in
  `qualibug.adaptive-planning-budget.v1`; runtime must never silently
  increase it. Prior-run `qualibug.adaptive-planning-history.v1` may
  influence compile/execution conversion only when policy id, policy version,
  strategy fingerprint, and receipt fingerprint match. Product-owned history
  must keep formal yield, model cost, and unit deliverable cost
  `NOT_MEASURED` until authoritative external or provider receipts exist;
  missing or non-matching history is an explicit cold start.

## Ledger & Run Lifecycle Rules

- Trace and weakness diagnostics consume
  `qualibug.discovery-trace-ledger.v3`, keyed by obligation attempt
  identity. V1 input requires the explicit offline migration; silent schema
  fallback is prohibited. Replay and shadow runs set
  `customer_outputs_published=false`.
- Runtime rollback is a next-run policy decision only. Select
  `legacy_champion` before creating a new immutable run contract; never roll
  back inside an active campaign or after either runner has started.
- A campaign may reopen a terminal attempt only when its prior immutable
  ledger proves that every terminal was `BLOCKED`/`DEFERRED`, zero executed
  target-request receipts exist, and no behavior slice was attempted. The
  retry must emit an audit event; any observed target request permanently
  forbids whole-run retry.
- Phase-1 cycle-time claims require immutable
  `qualibug.discovery-phase1-timing.v1` receipts from
  `tools/discovery_phase1_timing.py`: five warm runs for baseline and
  candidate, matching command/input/environment/runtime/system identities, a
  clean code commit, and at least 60% p50 improvement. Timing evidence never
  substitutes for external quality evidence.

## Package Risk Points

- Side-effect-free import: importing `ai_test_asset_center` must never
  install evaluator scoring, runtime patches, or scenario contracts (see
  Implementation SSOT Registry).
- Guardrail floors: the root Critical Configuration Guardrails table is the
  authority for `discovery_engine/_engine.py` and `stage_reason_all_v2.py`
  floors; module code must keep the asserts and never lower the floors.
- Evaluator-private isolation: benchmark source, hidden GT, scoring rules,
  match keywords, reproduction answers, and evaluator miss labels must never
  enter prompts, runtime context, traces, detectors, fixtures, Oracles, or
  product-facing outputs inside this package.
- Evidence discipline: the root evidence-enrichment rule (no inferred
  request bodies, credentials, actors, business rules, entity/table names,
  SQL, or impact claims; synthetic guidance cannot satisfy the
  customer-delivery gate) binds all modules in this package.
- Cleanup observability: cleanup/compensation decisions are traceable only
  through the `[cleanup-trace]` structured events in
  `experiment_cleanup_executor_core.py`; ad-hoc stderr prints are forbidden
  on that path.

## Enterprise Comprehension — Implementation Anchors

Capability philosophy, the four-link reachability chain, and the "no
structural limit" rules stay in the root Enterprise Business Comprehension
Contract. This section pins only the package-local anchors:

- `test_obligation.CANONICAL_RISK_FAMILIES` is a 10-family built-in set, but
  the registry is OPEN: `register_risk_family` accepts a full descriptor
  (relation types + protocol template + observers + assertion kind) that
  writes the downstream by-family maps, so a genuinely new bug class needs
  no core-code edit. `resolve_risk_family` never rewrites an unfamiliar
  family — it stays declared, is marked unregistered, and `make_obligation`
  BLOCKs visibly (`RISK_FAMILY_NOT_REGISTERED`). Promotion candidates and
  capability-gap families resolve with a recorded reason code, never
  silently.
- `assertion_dsl_base.SUPPORTED_KINDS` is a built-in 19-kind set with an
  open `register_assertion_kind` entry point that validates evidence
  producibility at registration time. Two built-in kinds (`cardinality`,
  `cross_surface_consistency`) remain structurally INDETERMINATE — their
  evidence keys (`collection`, `surfaces_agree`) are written by no observer
  — and are blocked at COMPILE time so the gap stays countable.
  `concurrency_final_invariant` is now falsifiable: when no producer wrote
  `invariant_held`, the evaluator computes it from the source-declared
  expression over the final_state observer's after-values
  (`COMPUTED_FROM_SOURCE_INVARIANT`), so a declared bound such as
  `available_qty >= 0` turns a concurrent lost-update that drives the value
  negative into a VIOLATION. Only a structured comparison the source
  declared is computed; unstructured invariants or missing numerics stay
  `FINAL_INVARIANT_MISSING` (INDETERMINATE), never a guessed verdict.
- `observer_contracts_base.OBSERVER_REGISTRY` holds 13 built-in observers,
  all adapter `http_api`, with an open `register_observer` entry point
  (single-envelope handlers are dispatched by
  `observe_experiment_requirements`, and OBSERVED evidence from
  runtime-registered observers is merged into observations first-class
  inside that dispatch — no wrapper around the dispatcher).
  `db_state_audit` still appends raw findings outside the observer →
  assertion → contract-oracle → delivery-gate chain and cannot produce a
  deliverable; `ui_execution_adapter` findings remain `candidate`-status
  compatibility-path output.
- `db_sql` (persistence): `discovery_runtime_planning.build_discovery_plan`
  installs `persistence_assertions.install_persistence_surface()`
  immediately after adapter resolution when `services[].db` is declared,
  and records `qualibug.adapter-surface-install.v1` in the plan bundle
  (observer `persistence_state_reader`, kinds
  `persisted_state_enumeration` / `persisted_field_bound`, family
  `persistence_integrity`). Behavior IR canonical fields retain
  source-declared `enum_values`, `min_value` and `max_value` (bounds come
  from OpenAPI schema `minimum`/`maximum` or an explicit field-dictionary
  declaration — never from storage precision/scale).
  `compile_obligations_from_behavior_ir(behavior_ir, *, root, project)`
  generates a `persistence_integrity` obligation only for an entity with a
  source-declared storage `table`, a canonical field with declared
  `enum_values` (enumeration obligation) or declared bounds (bound
  obligation), and a `produces`/`observes` relation to a real operation;
  bound obligations carry `persistence_bounded_field` +
  `persistence_min`/`persistence_max`, enumeration obligations carry
  `persistence_state_field` + `persistence_allowed_states`. Without a
  declared database, workspace identity, or installed surface, no
  persistence obligation is fabricated — the branch records a visible
  coverage gap instead. The handler is read-only and source-declared
  (`PERSISTENCE_TARGET_NOT_SOURCE_DECLARED`, `PERSISTENCE_READ_NOT_PERMITTED`,
  `PERSISTENCE_SOURCE_NOT_DECLARED`).
- `event_observer_http` / `ui_browser` (event / UI) and the http_api
  latency/stability surfaces (performance / stability): installed
  unconditionally by `discovery_runtime_semantic_binding` at import
  (observer + assertion kind + risk family + protocol, idempotent).
  Contract invariants enter Behavior IR through
  `bind_source_{ui,event,performance,stability}_contracts`, and the
  source_event/performance/stability/ui/job obligation-binding wrappers
  (installed in that order, stability included) compile them into
  obligations; the `experiment_protocols` facade consults the registered
  protocol compilers before the built-in chain. Evidence is produced by the
  handlers themselves: the event handler polls a declared relative GET path,
  the UI handler runs the declared Playwright plan through the governed UI
  adapter, and performance/stability summarize governed `execution_steps`
  (`duration_ms`/`_attempts` recorded by `sandbox_write_executor_base`).
  Workspace identity reaches these handlers first-class:
  `experiment_executor.execute_one_experiment` injects
  `_observer_runtime_context` (root/project/runtime_contract) into the
  executed experiment — no method-replacement wrappers. Every wrapper in
  the compile chain forwards `root`/`project`;
  `install_source_stability_obligation_binding` preserves outer install
  markers via `functools.wraps` so the chain cannot nest itself.
- The `registered_observer_evidence_bridge` installer is a no-op: the
  evidence merge it once performed by replacing the finalizer's dispatcher
  is now first-class inside
  `observer_contracts_base.observe_experiment_requirements`.
- Source-backed business-rule semantic extraction (shadow + augment phases):
  the regex extractor
  (`enterprise_knowledge_center/_chinese_business_comprehension_extractor_v1.py`)
  is the HIGH-PRECISION CANDIDATE layer — generic language-form parsing,
  cross-industry signal words, deterministic evidence validation. It must
  not grow industry keyword tables (legacy and frozen). Open-semantic recall
  lives in `enterprise_knowledge_center/_semantic_extraction.py`
  `kind=rule`: the LLM emits rule candidates
  (evidence_spans[]/semantic_spans/normalized_suggestion/derivations/rule_origin),
  and `validate_rule_candidates` deterministically anchors every evidence
  span to the source (`source[start:end] == text`), requires semantic-span
  containment, numeric fidelity (a normalized threshold must appear verbatim
  in the evidence), derivation coverage for every normalized field, and a
  constraint signal in the evidence — hallucinated
  actors/actions/conditions/thresholds and inferred-as-explicit claims are
  rejected with named reason codes, never by confidence.
  `rule_origin=explicit|inferred` are kept separate; only explicit may ever
  enter formal governance. Runtime modes: `off` (regex-only), `shadow`
  (default: candidates validated and recorded, formal Canonical Rule output
  untouched), `augment` (validated explicit LLM-only candidates are promoted
  into `rule_library` and flow through the existing structurize → implicit
  governance → Behavior IR chain — activated ONLY when the operator confirms
  the SPEC §19 promotion gates via `rule_promotion_gates_met`, otherwise
  resolves to shadow with `promotion_gates_not_met`), `required` (fails
  visibly without a provider). `resolve_semantic_rule_extraction_mode` emits
  `qualibug.semantic-rule-extraction-mode.v1` with
  requested/effective/provider/fallback — degradation is never silent. A
  unified rule candidate ledger (`build_rule_candidate_ledger`,
  `qualibug.rule-candidate-ledger.v1`) merges regex facts and validated LLM
  candidates per source: evidence de-dup by overlapping spans,
  semantic-signature merge (source-anchored terms, key attributes
  operator_family/action/threshold) into one `MERGED` entry with
  `extractor_support: [regex, llm]`, and threshold/operator divergence kept
  as mutual `CONFLICTED` entries with `conflict_refs` — confidence never
  resolves a conflict. `promote_rule_candidates_to_rules` promotes only
  llm+explicit+non-conflicted+non-MERGED (regex already present) candidates
  that carry anchored evidence; every promoted row keeps its `candidate_id`
  for fact_ref tracing, and `rule_promotion_gates_met` checks the §19 gates
  as data (no evidence-less promotion, no silent conflict resolution, full
  traceability) before augment may activate.
