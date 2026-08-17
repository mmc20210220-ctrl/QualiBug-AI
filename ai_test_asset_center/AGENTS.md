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
| Runtime artifact lifecycle (content-addressed store, Run Manifests, evidence artifactization, reference GC, run-manifest post hook, unified Run retention) | `artifact_store.py` (ArtifactRef/LocalArtifactStore, canonical JSON, zstd, atomic writes, streaming put_file, sidecar metadata) + `run_manifest.py` (RunManifestStore, SPEC §15 commit ordering, failed-run policy, pinning, count retention) + `evidence_artifactization.py` (fine-grained evidence split + Dual-Read hydration) + `trace_artifactization.py` (Phase 4: trace ledger → TRACE_EVENT payload refs + TRACE_LEDGER metadata ref; hydration + Dual-Read round loader) + `intelligence_report_artifactization.py` (Phase 6: report = finding summary + artifact_refs; heavy payloads stored once; redaction parity) + `artifact_gc.py` (Phase 7: mark-and-sweep with reference-container expansion — EVIDENCE_BUNDLE_MANIFEST parts / TRACE_LEDGER attempt_refs / INTELLIGENCE_REPORT artifact_refs; dry-run default, real delete behind `QUALIBUG_ARTIFACT_GC_ENABLE=true`; re-verified liveness at delete) + `run_retention_manager.py` (Phase 8: RunRetentionManager — unified owner of run retention `QUALIBUG_RUN_RETAIN`/`QUALIBUG_FAILED_RUN_RETAIN`, GC orchestration, quota `QUALIBUG_ARTIFACT_MAX_GB`, scratch TTL `QUALIBUG_SCRATCH_TTL_HOURS`; never deletes artifacts by mtime, never touches knowledge.db) + `run_manifest_hook.py` (registered via `scan_post_hooks`; commits manifest then runs RunRetentionManager). `scan_result_retention.py` is the DEPRECATED legacy fallback used only in the store-disabled mode (SPEC §32) |
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
| Chinese semantic frame SSOT (P0-A) | `enterprise_understanding/chinese_semantic_schema.py` (frame schema `qualibug.chinese-semantic-frame.v1`, slot statuses, reason codes, semantic signature) + `chinese_semantic_receipts.py` (typed content-addressed receipts) + `chinese_semantic_ledger_adapter.py` (fact → frame projection, `qualibug.chinese-semantic-frame-ledger.v1`) + `chinese_semantic_behavior_ir_adapter.py` (frame → Behavior IR projection) |
| Chinese clause structure (P0-B) | `enterprise_understanding/chinese_context_envelope.py` (block coordinates over `document_structure_assets`: section paths, list-stack ancestor chains, table row/column headers, unique quote lookup — structure only, zero business inference) + `chinese_clause_parser.py` (atomic clause trees `qualibug.chinese-clause-tree.v1`: enumeration action candidates, negation scope, condition leaves, exception nodes; language-function words only, no industry vocabulary) + `chinese_semantic_frame_compiler.py` (frame enrichment: list-parent condition inheritance, table header mention injection, enumeration mentions, exception merge; signature recomputed; idempotent) |
| Chinese context resolution (P0-C) | `enterprise_understanding/chinese_context_resolver.py` (frame-level omitted-actor recovery from unique evidence — only-if subject, unique prior frame in the same section, unique section heading; mention-level coreference: 该X/本X/此X explicit same-sentence nouns, bare pronouns need exactly one candidate; `document_context` section/list/neighbor population; UNKNOWN never force-bound; raw text never rewritten) |
| Concept & grounding (P0-D) | `enterprise_understanding/business_concept_registry.py` (explicit-evidence concept layer: label→canonical with priority understanding_model > identity_registry > permission_matrix > data_tables; similarity never merges) + `chinese_semantic_grounding.py` (evidence chains per SPEC §12.2: actor = permission matrix > roles > UI contract > concept registry; operation = rule ref > summary verbatim > description > rule_to_interface > formal UI contract > structural entity+CRUD; entity = declared object labels/aliases; state = field-description enum / state machines; scope = ownership phrases → structured OWN; every binding has a typed GROUNDED/AMBIGUOUS/UNKNOWN receipt) |
| Legacy Chinese parse demotion (P0-E) | `behavior_ir_core.py` `build_behavior_ir_from_knowledge_asset` (frame-confirmation gate over the six legacy Chinese-text parse products + `frame_family_evidence` on invariants + `model["legacy_semantic_fallback_receipt"]`) + `_chinese_business_comprehension_extractor_v1.py` (candidate marking) + `_chinese_business_comprehension/__init__.py` `apply_v1_extractor_frame_confirmation` (phase-2 rule confirmation gate + `asset["v1_extractor_demotion_receipt"]`) + `obligation_compiler_base.py` (phase-3 `_FRAME_TYPE_FAMILY` family SSOT + CJK family/ownership counting) + `obligation_compiler_privacy_pair_base.py` (phase-3 CJK privacy-policy marker counting) + `behavior_semantic_mapper.py` (phase-4 finding-enrichment neutralization: no built-in path/role/SQL/industry dictionaries) |
| Scan execution phase & per-batch budget | `small_scale_validation_gate.HARD_BUDGET_CAP` is the single hard-cap authority (`600`). Serial `_experiment_batch_executor_single_finding_mechanics.execute_selected_experiments` and concurrent `experiment_batch_concurrent_scheduler._apply_global_budget` both import it; a local literal must never re-cap the operator's declared budget. Defaults remain small_scale ≤20 / formal ≤100, while an explicit runtime-contract budget may rise to the shared hard cap. `product_scan_mainline.py` `_apply_scan_execution_defaults` declares `validation_phase=formal` for a full scan when absent, and the phase/budget propagate through `pipeline_runtime.py` / `scan_source_runtime.py` into the receipted runtime contract. `_operation_coverage_budget` and `_family_coverage_budget` use the same cap authority; `safe_experiment_prioritizer.prioritize_experiments` places the declared family quota above the operation-fair tier, and the family set comes from the open obligation registry rather than a closed list. Anonymous-write priority requires either the governed `credential_gated_write` template or an explicit no-auth declaration; missing credential evidence remains UNKNOWN and receives no boost. |
| Identity-addressed path ownership | `obligation_compiler.py` `_with_source_declared_ownership_relations` (+`_path_identity_params`: identity-shaped path params) — path-target reads/writes whose operation text declares caller-scoped ownership (本人/自己的/归属/应校验 + identity path param, or an owned collection anchor) derive source-grounded `owns` relations with `path_target` preconditions; `experiment_protocols_base.py` `_identity_addressed_read_isolation_protocol` compiles the two-arm owned-resource read (owner reads own identity-addressed resource, viewer reads the owner's resource — both paths resolved from runtime-observed `account_id`s, no create fixture) with the `_identity_addressed_read` marker consumed by `experiment_compiler_obligation_core.py` (drops the `owned_resource` fixture / `resource_ownership` observer requirements for that shape) |
| Read-side row-state allowed-set fallback | `experiment_protocols_base.py` `_read_side_allowed_states` — primary source is the operation's own declaration (仅返回 ON_SALE); when absent, an entity-state exposure rule (用户端不展示下架商品…: exposure verb + generic non-public state word) on a PUBLIC surface (no `required_roles`) resolves the allowed set from the rule's subject entity's declared STATE enum (`semantic_type=STATE` + `enum_values` in the IR entity model), keeping only literals whose own meaning is public (`_READ_SIDE_PUBLIC_STATE_LITERALS` — ON_SALE/ACTIVE/ENABLED/PUBLISHED/…; literal semantics, never a translation table). Restricted surfaces (declared roles) stay excluded — their rows legitimately include non-public states the owner may see. Rules without a declaration or enum keep the visible `read_side_rule_lacks_decidable_assertion` BLOCKED (no vacuous observation) |
| Validation mutation array-item descent | `experiment_protocols_base.py` `_validation_protocol_material` Strategy 1 — when a top-level schema property is an array with declared `items.properties` (batch-create/detail bodies: `products: [{...}]`, `items: [{...}]`), the semantic invalid-value heuristics descend into the first element: explicit rule targets first, then element property order; the mutation addresses the first element (`$.products[0].stock`, constraint `semantic:negative_value`). Arrays without a mutatable element field fall through to the existing required-removal strategy. Generic for any batch/detail body; the runtime executor already writes `$.key[0].elem` paths |
| Validation schema-expansion authority | `_validation_obligation_expander_core.py` `_schema_constraint_expansion_eligible` — request-schema field fan-out belongs only to the dedicated `single_dimension_mutation` coverage template (plus the legacy empty-template operation-contract entrypoint). A semantic/source invariant keeps its own property and must never be crossed with unrelated request fields. An explicit typed field constraint remains authoritative and `experiment_protocols_privacy_base.py` compiles its own canonical control/treatment arms even when the surrounding semantic projection is one-sided; non-renderable path/header mutations fail as `BLOCKED_MISSING_BINDING`, never as an adapter capability gap. |
| Deployment-rule invariant authority | `behavior_ir_core.py` `build_behavior_ir_from_knowledge_asset` — a `source_type=deploy` instruction is not a business invariant unless it carries an explicit operation ref or an accepted exact-source/agent-semantic `rule_to_interface` edge. Unbound deployment guidance remains observable as `deployment_rule_lacks_executable_surface_contract` / `DEPLOYMENT_RULE_NOT_BUSINESS_INVARIANT`; explicitly bound health/availability contracts remain reachable. `_parsing.py` risk terms use ASCII lexical boundaries so a product/name containing a substring such as `bug` is not fabricated into historical-defect evidence. |
| Query-safety injection probe | `experiment_protocols_base.py` `_semantic_invalid_value` string branch — a rule declaring query-safety vocabulary (参数化/拼接/注入/SQL/parameterized/injection/concatenat*) makes the treated string field carry a generic OWASP-style probe (`' OR '1'='1`, constraint `semantic:sql_injection_probe`). Vocabulary-gated so ordinary string fields are never mutated into probes; numeric fields keep type-gated heuristics (negative value first). Industry-neutral, never benchmark data. The rule→operation binding for query-parameter rules stays the binding channel's item (rules without a subject entity remain `operation_refs: []` until then) |
| Anonymous account-enumeration guard | `account_enumeration_guard.py` `build_account_enumeration_guard_obligations` (wired in `discovery_runtime_planning.py` after the state-audit block, receipt `account_enumeration_report`) — identity-locator GET/HEAD operations (generic identity vocabulary: email/phone/mobile/username/login/account/user_id…) with NO declared permits/denies relation are anonymous-reachable by definition and get a single-arm privacy guard obligation: the anonymous response must not carry account attributes (generic account-field concepts 邮箱/手机号/状态/角色 → email/phone/mobile/status/role). The obligation names the injected `anonymous` actor (empty credential binding → executor sends the request without an Authorization header), and flows through the existing response-side privacy channel (`obligation_compiler_privacy_pair_base.py` keeps single-arm field-policy obligations; `_assertion_dsl_privacy_mechanics.py` `privacy_field_policy` absent-policy + `match_field_names` scans nested field names). Structure-derived only; never benchmark or industry-specific |
| Credential-gated write guard | `credential_gated_write_guard.py` `build_credential_gated_write_guard_obligations` (wired in `discovery_runtime_planning.py` after the account-enumeration block, receipt `credential_gated_write_report`) — WRITE operations (POST/PUT/PATCH) with NO declared permits/denies relation whose own contract demands verification-based authentication (签名/验签/校验/验证码/身份校验/凭据/signature/verification/credential/otp) get a single-arm authorization guard obligation: the anonymous write must be rejected (`http_status_class` expected 4xx). The treatment body aims the identity-locator field (email/username/…) at a real runtime account (the "any account" shape of a password-reset surface); a callback/webhook surface (callback/回调/notify/通知) needs no account locator and its status/state field carries the success literal `SUCCESS` (the forged-success shape). Protocol: `experiment_protocols_base.py::compile_family_protocol` template `credential_gated_write`. Same injected `anonymous` actor pattern as the enumeration guard; structure + the operation's own contract only, never benchmark or industry-specific |
| Write-side own-scope owns derivation | `obligation_compiler.py::_with_source_declared_ownership_relations` — a WRITE operation whose own contract declares a caller-scoped invariant (本人/仅限本人数据/只能…自己/禁止跨用户/禁止转移/cross-user) and carries an ownership parameter (fromUserId/ownerId/…, generic ownership-key vocabulary) is NOT an intentional delegation contract: the source itself forbids touching other accounts' resources. Derives source-grounded `owns` relations (precondition `ownership_input=write_declared_own_scope`) so the existing isolation channel compiles the two-arm cross-account treatment (owner operates with own identity, viewer attempts the owner's identity on the ownership binder). Reads that already declare own-scope keep their existing path; reads/writes without own-scope language stay unchanged |

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
semantic signature. Legacy Chinese parsing is demoted to observable candidate hints behind the P0-E frame-confirmation gate (see P0-E contract below).

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

Legacy Chinese parse demotion contract (P0-E): `behavior_ir_core` no longer
treats fixed-vocabulary Chinese-text parsing as final semantics when the
asset carries a Chinese Semantic Frame ledger. The frame-confirmation gate
makes the frame channel the Chinese-semantics SSOT per parse product:

- Frame identity: the rule's frame is located exactly like
  `chinese_semantic_grounding._find_rule` — origin fact id (rule id
  `zh_business:<fact tail>`, last-20-chars identity) then statement text;
  a frame is "grounded" when the P0-D grounding engine resolved at least one
  technical ref the frame channel can emit relations from
  (GROUNDED/PARTIAL; PENDING is not grounded).
- Migration points (all inside `build_behavior_ir_from_knowledge_asset`):
  1. action→summary binding — a grounded rule frame binds only operations it
     grounded (`METHOD:path`); unconfirmed candidates are demoted and
     skipped. Absent frame / ungrounded frame keep the legacy binding as an
     observable fallback (NO_FRAME_FOR_RULE / FALLBACK_WHEN_UNGROUNDED);
     no ledger keeps the legacy binding byte-for-byte (NO_FRAME_LEDGER).
  2. field-level ownership — each legacy candidate survives only when some
     grounded frame declares structured ownership
     (`scope.ownership_relation` non-raw key on an ownership frame type) for
     the same actor role on the same `METHOD:path` operation; everything
     else is demoted (FIELD_OWNERSHIP_UNCONFIRMED_SKIPPED).
  3. fixed-vocabulary CJK field-token binding is a counted candidate hint
     (CJK_FIELD_TOKEN_EXTRACTION) — the frame field grounding is its
     replacement, never both.
  4. causal-delta postcondition derivation from statement tokens is a
     counted candidate hint (CAUSAL_DELTA_TOKEN_EXTRACTION).
  5. umbrella exclusion — a grounded frame is structured evidence: the rule
     is never umbrella-excluded (UMBRELLA_PATTERN_OVERRIDDEN_BY_GROUNDED_FRAME);
     absent/ungrounded frames keep the legacy exclusion, receipted
     (UMBRELLA_PATTERN_FALLBACK).
  6. token-promoted idempotency (重复/幂等/… without risk-domain
     classification) is a counted candidate hint
     (IDEMPOTENCY_TOKEN_CANDIDATE); the risk-domain classification remains
     structured evidence.
- Every fallback/demotion is counted in
  `model["legacy_semantic_fallback_receipt"]`
  (`qualibug.legacy-semantic-fallback-receipt.v1`: frame_ledger_present,
  used, kind_counts, reason_codes=[LEGACY_FALLBACK_USED], contract). The
  receipt is attached AFTER the content address so it never rotates
  model_id. Assets without a frame ledger are byte-identical to pre-P0-E
  builds; their receipt records only NO_FRAME_LEDGER-style counts (nothing
  else is a "fallback" when no SSOT exists). `validate_behavior_ir` does not
  see the receipt (it is not part of the behavior model).
V1 extractor regex demotion (P0-E phase 2): the legacy fixed-vocabulary
regex extractor (`_chinese_business_comprehension_extractor_v1.py`) is a
candidate discovery layer, never a self-asserted fact authority:
- Every fact it produces (`analyze_chinese_business_source` document path
  and `project_openapi_interface_chinese_spans` OpenAPI prose path) carries
  `semantic_candidate=True` + `candidate_reason=legacy_regex_vocabulary_hit`
  (TERM_ALIAS glossary rows are dictionaries, not business rules);
  `_rule_from_fact` rules inherit the marker. The marker survives the
  structure-first compiler's same-signature merge (`_atomize_existing_fact`
  preserves unknown fields) — it states the rule text's legacy regex origin.
- `apply_v1_extractor_frame_confirmation` (in
  `_chinese_business_comprehension/__init__.py`) runs in `composition.py`
  right after `ground_semantic_frames` on BOTH the full and incremental
  paths. It decides each candidate rule against the frame SSOT with the same
  identity and groundedness semantics as the P0-E behavior-IR gate (origin
  fact id ↔ `zh_business:<fact tail>`, then statement text; grounded =
  technical_grounding op/actor/entity refs resolved): frame grounded →
  `frame_confirmation=CONFIRMED` (FRAME_GROUNDED); frame ungrounded →
  FALLBACK_UNGROUNDED (FRAME_UNGROUNDED); no frame → UNCONFIRMED_NO_FRAME
  (NO_FRAME_FOR_RULE). The decision rides on the rule and is receipted in
  `asset["v1_extractor_demotion_receipt"]`
  (`qualibug.v1-extractor-demotion-receipt.v1`: frame_ledger_present,
  candidate_rule_count, confirmed_count, kind_counts,
  reason_codes=[V1_EXTRACTOR_CANDIDATE_DEMOTION]).
- `behavior_ir_core` carries `frame_confirmation` + reason onto the invariant
  (transparent pass — rules without the status gain nothing), so the
  demotion is observable end-to-end. Assets without a frame ledger are
  untouched: no `frame_confirmation` field, receipt records only
  V1_EXTRACTOR_NO_FRAME_LEDGER, and the Behavior IR stays byte-identical.
Obligation compiler family CJK token demotion (P0-E phase-3): the
obligation compiler no longer lets fixed CJK vocabulary decide risk families
when the frame SSOT has spoken:
- `behavior_ir_core` writes `frame_family_evidence` ({frame_id, frame_type,
  grounded}) onto the invariant when the rule's frame is grounded (P0-E gate
  identity/groundedness semantics); no ledger / no frame → no evidence field.
- `obligation_compiler_base` family detection: a grounded frame_type mapped
  by `_FRAME_TYPE_FAMILY` (TIME_WINDOW_CONSTRAINT→temporal,
  QUANTITY/FORMULA_CONSTRAINT→conservation, VALIDATION/UNIQUENESS/
  CARDINALITY_CONSTRAINT→validation, PERMISSION/OWNERSHIP/SCOPE/
  DATA_VISIBILITY_RULE→visibility, STATE_TRANSITION/COMPENSATION_RULE/
  PROCESS_ORDERING→state) decides the family — the legacy kind-token
  detection (incl. CJK tokens 库存/金额/隐私/过期/可见/状态/因果/后置) does not
  run. Without grounded frame family evidence the legacy detection runs and
  CJK token hits are counted `CJK_FAMILY_TOKEN_FALLBACK` on the P0-E
  `legacy_semantic_fallback_receipt`, gated on `frame_ledger_present`
  (ledger-less assets keep plain legacy semantics, no counts).
- Operation-level CJK ownership language markers (自己的/本人/归属/只能查询)
  are counted `OWNERSHIP_LANGUAGE_CJK_CANDIDATE` (the frame channel's
  structured ownership — IR owns relations — is the ownership SSOT) and CJK
  privacy policy markers (Chinese items of `_ABSENT_MARKERS`/`_MASK_MARKERS`,
  derived from the single word list, never duplicated) are counted
  `PRIVACY_POLICY_CJK_CANDIDATE` (frame privacy-policy granularity is a
  registered extension point) — both under the same ledger gate.
Finding-enrichment neutralization (P0-E phase-4,
`behavior_semantic_mapper.py`): the enrichment knowledge bases no longer
embed customer/benchmark business data — the built-in API-path→page map
(PAGE_MAP), role table (ROLE_ACTIONS) and SQL template table (SQL_HINTS,
which leaked target table names like inventory/inventory_ledger/bom_line)
are removed. SQL verification hints are generated from the finding's own
declared `source_entity` only (`SELECT * FROM {entity}`, fail-safe empty
without a declared entity); page/module labels fall back to neutral labels
for industry paths while generic system concepts (auth/approvals/config/
scan/knowledge/tasks/connectors/sync/cache/import/health) stay mapped;
RISK_IMPACT wording is industry-neutral (no 库存/资金/订单). Reproduction
steps use generic business templates (never 买家身份/物料清单/BOM). The
enrichment contract stays additive and format-only: it never invents
entities, tables, SQL semantics, actors or impact claims.

Registered prerequisites (not implemented, honest): frame privacy-policy
granularity — `PRIVACY_POLICY_CJK_CANDIDATE` counting has no SSOT counterpart
because the structured fact layer has no privacy-policy fact type
(absent/masked). Completing it requires a PRIVACY_POLICY fact type in
`structured_fact_compiler.py` sourced from declared privacy statements (an
open-semantic extraction concern, SPEC §19), then frame schema + grounding +
compiler consumption. The 131-bug benchmark re-run remains the activation
verification gate and is executed separately from the unit/CI gates.


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
- Role-variant aggregation (distribution balance): the concrete treatment
  actor class is EVIDENCE, not identity. "buyer can access the owner's
  resource" and "auditor can access the owner's resource" on the same
  interface, assertion kind and violation shape are ONE defect whose breadth
  is proven by the role set — the canonical identity keeps only the relation
  type (`control_to_treatment` / `actor_insensitive_property`), concrete
  classes ride in `proof.evidence_actor_classes` and surface as
  `evidence_actors` on the canonical defect entry (union across all merged
  occurrences), `occurrence_finding_ids` merge every variant's occurrences,
  and the representative is the highest-confidence occurrence (stable
  tie-break). The `canonical_defect_id` is therefore the cross-run stable
  role-variant aggregation key: the same defect surface aggregates
  identically no matter which roles a run explored. The verified discovery
  archive (`verified_discovery_archive.py`) keys by the same role-invariant
  aggregation identity derived from the finding's own structure (assertion
  kind + normalized verb/path + violation shape; risk_family deliberately
  excluded because the same surface can be compiled under different families
  across variants), and `load_verified_discovery_archive` re-keys legacy
  per-role entries once, idempotently (`migrate_archive_for_aggregation`),
  so aggregated deliveries align with archived history instead of
  duplicating it.
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
- Sequential precondition identity flow has one source-declared authority:
  `multi_level_dependency_chain.py` projects an
  `qualibug.identity-output-binding.v1` from the established entity's
  Behavior IR `identity_fields`; `flow_data_requirement.py` receipts the
  exact producer, response field/path, aliases, and consumer targets;
  `flow_data_execution_contract.py` proves the ordered producer-to-consumer
  lineage; and `experiment_precondition_executor.py` captures only that
  declared response field. Missing or multiple identity fields, incomplete
  output contracts, and conflicting response values fail closed with
  registered binding-gap reasons. A conventional `{id}` or field-name guess
  is never an identity authority.
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

- Fact first-loss lineage has one product-side authority path:
  `fact_first_loss_ledger.attach_fact_refs_to_planning_artifacts` joins an
  accepted fact through canonical Business World Model evidence, its exact
  business behavior, governed implementation binding, and an explicit
  Behavior IR invariant/relation identity before stamping the compiled
  obligation and its experiments. Text, path, operation-name, and source
  locator similarity are never lineage authority; duplicate or conflicting
  identities fail closed in `qualibug.fact-ref-planning-attach.v1`. This
  projection is diagnostic lineage only and must not alter compile, selection,
  execution, or quality decisions.
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
- Message-chain verification (cross-service event consumption) is the
  multi-hop extension of the single-hop event observer:
  `message_chain_surface.py` registers observer
  `message_chain_delivery_observer`, assertion kind
  `message_chain_consistency` and protocol
  `event_delivery_consistency:message_chain_verification` on the same
  family; `message_chain_contract_overlay.py` admits source-bound
  `message_chain_contracts` (schema `qualibug.formal-message-chain-
  contract.v1`: event name / trigger source / consumers / expected effects)
  and operator-declared `runtime_event_surfaces` (schema
  `qualibug.runtime-event-surface.v1`, the degradation channel — no source
  refs, no invented business rules) from scan-body fields
  `message_chain_contracts` / `runtime_event_surfaces` or typed
  `external_signal_requests`; `message_chain_binding.py` binds them into
  Behavior IR invariants (expression kind `message_chain_consistency`) plus
  a `produces` relation and compiles one
  `event_delivery_consistency` obligation (wrapper installed after the
  source_event wrapper). The chain observer polls the declared relative
  event GET path WITHOUT collapsing duplicates (the single-hop observer
  dedupes by event id, hiding duplicate deliveries): it counts distinct
  expected-type deliveries, detects duplicate delivery (same event id twice
  in one poll batch under `duplicate_mode=log`, or reappearance across
  polls under `duplicate_mode=queue`), checks ordering (declared sequence
  field strictly increasing / timestamp field non-decreasing / declared
  expected type sequence) and reads every consumer's declared target state
  back over a relative GET (`{correlation}` path placeholder or query
  parameter) asserting the declared expected state. Pre-cleanup observation
  (`install_message_chain_pre_cleanup_observer`) runs the chain observer
  before cleanup compensation removes the correlated entity, and the
  finalizer reuses that receipt. Chain evidence stays privacy-safe: event
  ids fingerprinted, only declared identity/type/state fields kept, raw
  payloads never included; transport failure or incomplete coverage is
  INDETERMINATE, never PASS.
- The `registered_observer_evidence_bridge` installer is a no-op: the
  evidence merge it once performed by replacing the finalizer's dispatcher
  is now first-class inside
  `observer_contracts_base.observe_experiment_requirements`.
- Enterprise-knowledge build execution is single-parse per source version and
  invocation: `_api.build_enterprise_business_knowledge_asset` hands its exact
  parsed rows directly to `composition.build_enterprise_business_knowledge_asset`;
  `_parsed_sources_for_context` must reuse the hash-verified Document Structure
  IR from that row and fail visibly on a missing handoff or source-hash drift.
  `qualibug.enterprise-source-parse-execution.v1` records parse, reparse,
  structure-reuse and structure-rebuild counts; this is invocation-local flow,
  never a cross-build cache. Understanding projections may share only finalized
  read-only heavy evidence (`document_structure_assets` and the prior-pass
  `enterprise_understanding_model`) through
  `schema.clone_asset_for_understanding_projection`; every mutable cognition
  branch remains deep-isolated.
- The knowledge-asset → Reasoner bridge has one lossless semantic projection
  authority: `enterprise_knowledge_center.project_knowledge_world_model`.
  Explicit entity aliases/identifiers/business fields/provenance, rule binding
  readiness and operation refs, role permissions, cross-source contradictions,
  parse/coverage gaps, and validated inferred-semantic candidates must survive
  the projection. Inferred candidates travel only in `semantic_hypotheses` with
  `authority=UNVERIFIED_SEMANTIC_HYPOTHESIS`, never in `documented_rules`. A
  rule is verifiable
  only when the asset carries an explicit operation binding/readiness signal;
  presence in `rule_library` alone is not verification. Structured fact
  retrieval in `reasoning_fact_retrieval.retrieve_grounded_facts` schedules
  rule, unverified-semantic-hypothesis, state, relation, entity, permission,
  conflict, and gap sections fairly. Within the explicit-rule and
  unverified-semantic-hypothesis channels, both the world-model projection and
  fact retrieval round-robin by declared source identity before applying their
  receipted budgets; source-total/projected/emitted counts expose any lost
  document coverage. Thus neither a large rule list nor one verbose document
  may starve other business evidence or make missing coverage look complete.
  `collect_reasoner_hypotheses` receives the scan's project/root explicitly;
  chunk and learned-memory retrieval use that authority before process-global
  environment state, preventing cross-project drift. `ReasonerPolicy`
  normalizes legacy persisted defaults to the package runtime guardrail of 40
  hypotheses per engine; the only supported lower budget is the explicit
  `QUALIBUG_REASONER_MAX_HYPOTHESES_PER_ENGINE` per-run override, which remains
  visible in the reasoner receipt.
- Reasoner multi-step/cascade intent becomes executable authority only through
  a unique source-declared Behavior IR `process_graph`. The hypothesis bridge
  may retain an ordered operation hint only after each path joins uniquely to
  a source-declared API operation; `obligation_source_adapter` must then resolve
  every operation uniquely in Behavior IR and match the ordered sequence to
  exactly one graph carrying source refs. Only that graph may populate an
  obligation's `required_operations` and `property.process_graph`. LLM step
  text is never a process definition. Missing or ambiguous operation/graph
  joins remain `BLOCKED_DEEP_COMPREHENSION_UNCOMPILED`, not a guessed sequence.
- Format-equivalent field dictionaries share one table-identity rule in
  `_parsing_mechanics`: an explicit row-level `Table`/`数据表` declaration wins;
  section, sheet and container locators are fallback provenance only and must
  never become business entities when the row declares its table. The same
  content in `.md`, `.csv`, `.xlsx` and `.docx` must project the same table and
  field sets (`tests/test_enterprise_material_format_equivalence.py`).
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
  span to the source (`source[start:end] == text`), assigns a stable
  `candidate_id`, requires semantic-span
  containment, numeric fidelity (a normalized threshold must appear verbatim
  in the evidence), derivation coverage for every normalized field, and a
  constraint signal before any candidate may claim `explicit` — hallucinated
  actors/actions/conditions/thresholds and inferred-as-explicit claims are
  rejected with named reason codes, never by confidence. A source-anchored
  candidate already labelled `inferred` may lack an explicit modality because
  its role is to preserve Chinese implicit meaning (process order, role/action
  allocation, causal or cross-entity linkage) as a falsifiable hypothesis; it
  never becomes a fact by validation alone.
  `rule_origin=explicit|inferred` are kept separate; only explicit may ever
  enter formal rule governance. Inferred candidates project through
  `project_knowledge_world_model.semantic_hypotheses` into a separately labelled
  `UNVERIFIED SEMANTIC HYPOTHESES` reasoner block. The prompt must copy any used
  candidate ids into `semantic_hypothesis_refs`; when it combines meanings from
  multiple sources it must preserve every used candidate id and source and must
  surface conflicts rather than harmonize them. The hypothesis bridge preserves
  the complete ref list as depth lineage. This channel may design governed runtime
  experiments but cannot satisfy formal rule authority or customer-delivery
  evidence, and graph-context activation must not erase it. Runtime modes:
  `off` (regex-only), `shadow`
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
  Source breadth has one shared execution authority:
  `_semantic_extraction.run_semantic_extraction_batch`, consumed by both full
  and incremental knowledge builds. It attempts every selected source in
  deterministic registry order; there is no product-side source-count,
  source-length, chunk-count, or candidate-count ceiling by default. A caller
  may declare the positive operator budget
  `semantic_max_chunks_per_source`; skipped ranges then remain explicit in the
  per-source receipt and asset `coverage_gaps`, and the budget is part of the
  cache identity so a bounded result can never poison a later unbounded build.
  Source and chunk executors share one process-wide four-call provider
  semaphore: nested pools must never multiply the declared concurrency limit.
  `qualibug.semantic-extraction-batch.v1` receipts bind target, attempted,
  completed, skipped and gap source counts plus scheduling/concurrency policy.
  Incremental refresh must preserve the same downstream closure as a full
  build: regex rules for the changed source come from its freshly parsed row;
  eligible LLM rules attach to that row before source replacement (never to a
  soon-to-be-replaced asset list); and the shared candidate-validation gate is
  recomputed after semantic candidates merge so multi-source validated entity
  candidates re-enter `business_objects`. Mode, batch and per-source receipts
  carry stable `receipt_id` values so repeated refreshes replace prior state
  instead of accumulating identity-less receipts.

## Self-Learning Closed Loop — Implementation Anchors

The learning loop is a first-class mainline stage, not an add-on. Anchors
(update together with the code):

- **WRITE side (per scan close)** — `closed_loop_feedback.build_closed_loop_context`
  extracts open-taxonomy patterns (`type:method:entity` signatures) from
  customer-deliverable findings into the SQLite knowledge base via
  `learning_pattern_bridge.LearningPatternBridge` (category `risk_pattern`);
  re-confirmed signatures reinforce to 0.95, consumed-but-not-reconfirmed
  signatures decay (×0.95, floor 0.05, never deleted). Patterns also carry
  comprehension-layer semantics from the finding's own observed fields —
  `assertion_kind` (category), reproducing `actor` (`reproduction.actor`),
  `semantic_summary` (`description`), and `behavior_delta` (differing
  expected/actual fields only, never full bodies) — which the reasoner's
  learned-memory block renders as violated-behavior guidance, not just
  endpoint names. The same close also records per-engine confirmation
  attention (`engine_feedback.record_confirmed_engine_attribution`), records
  the round in `cross_round_knowledge_transfer.record_round_completion`
  (observational provenance only), persists verified binding-resolver
  mappings (`binding_experience_learning.build_binding_experience_context`,
  category `binding_resolver`), and writes the executed-set diff report
  (`learning_effect_observation.write_learning_effect_report`).
- **READ side (per scan start)** — `__main__` loads the SQLite knowledge
  base into `campaign_context["learned_knowledge"]` (risk patterns +
  `binding_resolvers` + `cross_round_insights`). Consumption surfaces:
  (1) planning-time bounded ranking boost (`learning_knowledge_consumption`
  `build_learned_boost_index`/`apply_learned_boost`, cap 1.5×, path-entity /
  risk-family matching only — never budget/compile changes);
  (2) reasoner comprehension memory block
  (`build_learned_memory_prompt_block`, ≤8 patterns / ≤1200 chars appended
  to every engine prompt, attention guidance only);
  (3) engine attention weights (`engine_feedback.resolve_engine_attention_weights`,
  cap 2.0, staleness decay 90 days);
  (4) resolver-priority reorder (`binding_experience_learning.apply_binding_experience_reorder`,
  stable verified-first sort of an experiment's existing source-declared
  resolver list; never adds sources, never changes binding status).
  Consumption state is visible in the scan receipt: `learning_consumption_receipt`,
  `binding_experience_receipt` (planning bundle) and `learned_memory_receipt` /
  `engine_attention_receipt` / `fact_retrieval_receipt` / `semantic_dedup_receipt`
  / `graph_context` (reasoner `provider_meta` — surfaced at the
  `collect_reasoner_hypotheses` boundary, never dropped).
- **Trigger** — `auto_learning_trigger.AutoLearningTrigger` fires after a
  scan when the authoritative v12 fields carry a signal:
  `formal_count_projection.formal_customer_deliverable_count` (or
  `canonical_defect_count`) ≥ threshold, or
  `pipeline_health.blocked_obligation_count` > 0 (blockage is itself a
  learning opportunity). Its execution extracts detection signals
  (`bug_pattern_memory.extract_detection_signals`) and persists them back
  into the KB as `learned:*` `risk_pattern` entries — the legacy
  `learned_probes.json` output had no mainline consumer and is no longer
  produced.
- **Effect observability** — `learning_effect_observation` diffs adjacent
  rounds of the same campaign from the immutable per-round trace ledgers
  (executed obligation id sets, blocked reason-code distribution,
  delivery occurrences, canonical defects) into
  `platform_outputs/<project>/learning_effect/`. These are diagnostic
  observables only — never recall/precision/commercial capability; pairing a
  learning change with a controlled re-run (champion/challenger) remains the
  promotion evidence path. `tools/learning_effectiveness_dashboard.py` and
  `tools/enhanced_learning_signals.py` read the authoritative v12 fields
  (`formal_count_projection`, `canonical_defect_registry`,
  `obligation_attempt_ledger`, `pipeline_health`).
- **Boundaries (must hold)** — the loop never infers request bodies,
  credentials, business rules, entity/table names, SQL, or impact; resolved
  business values never enter the KB (binding receipts stay
  fingerprint-only; `binding_resolver` entries carry source-declared
  resolver identities only); failures stay visible in every receipt
  (`load_failure`, `FAILED` statuses); internal counts (patterns stored,
  obligations boosted, resolvers reordered) are mechanism observables, never
  recall/precision claims.

## Rule Contract-Field Validation Binding — Implementation Anchors

- **WRITE side (planning, additive IR stage)** —
  `rule_contract_validation_binding.bind_rule_contract_validation_invariants`
  derives `validation`-kind invariants for source constraint rules whose
  derived invariants are still unbound (`operation_refs == []`): a rule's
  grounded semantic-frame subject resolves to the governed schema entity via
  the entity field nodes' own source descriptions (优惠券 ↔
  `coupons.expires_at` — 优惠券过期时间, visible enterprise material, never a
  translation table); the rule's constraint vocabulary (状态/有效期/次数/
  类目/封顶/最低/金额 + English tokens — industry-neutral business language)
  scores the entity's declared fields entity-scoped; the entity's consuming
  operations bind through path/module identity and request/response
  contract-field overlap with the rule's OWN top-scored constraint fields
  (never bare trigger-text token matching); the constraint operator is
  extracted from the behavior phrase (`must_equal`/`under_limit`/
  `scope_restricted`/`capped`/`within_time_window`/`non_negative`/`minimum`
  + literal expected value when the statement names one, e.g. ACTIVE);
  operands keep only top-scored fields (a min-order rule carries
  `min_order_amount`, not every amount field); explicit declared field names
  in the statement (discount_amount 不能小于 0) score above vocabulary
  groups. Staged in
  `discovery_runtime_semantic_binding.build_behavior_ir_with_semantic_operation_bindings`
  after `bind_business_behavior_invariants`; receipt
  `qualibug.rule-contract-validation-binding.v1` on the IR (per-rule
  dispositions BOUND / NO_ENTITY_FIELD_MATCH / MULTI_ENTITY_MONEY_AMBIGUOUS /
  NO_CONSUMING_OPERATION — every skip is named, nothing fabricated).
- **Non-duplication contract** — the stage derives only for rules where
  every already-derived invariant is unbound; the shared subject-frame
  channel in `behavior_ir_core.build_behavior_ir_from_knowledge_asset`
  (frame subject → business-object aliases → tables → operations, with the
  decision-operation rekind and entity-scoped operands) is the primary
  channel; this stage is the fallback semantic layer that does not depend on
  `business_objects` being populated (it reads schema tables + field
  dictionary directly). Rules already bound by either channel are never
  duplicated.
- **Historical defect material (H3)** —
  `historical_defect_rule_binding.enrich_asset_with_historical_defect_rules`
  re-admits historical-defect documents (generic names HISTORICAL_BUGS.md /
  historical_bugs.md / 历史缺陷* …) into the planning asset as
  defect-class rule candidates with origin `historical_defect` (amount/
  calculation classes → money-consistency rules that flow through the same
  binding channel; every other class is recorded as a coverage note, never a
  rule; entry titles only participate in classification — body words such as
  敏感金额字段 in a role-filtering class never misclassify). Receipt
  `qualibug.historical-defect-rule-binding.v1`. The shared requirement-doc
  scoring change (admitting `historical_bug` source types in
  `scan_source_runtime`) is deliberately NOT applied here — recorded for the
  coordinator to merge.
- **Boundaries (must hold)** — operands carry field identities from the
  entity's own declared schema, never inferred values; the derived invariant
  never references benchmark/GT material; a rule that cannot resolve to a
  unique entity + fields + consuming operation stays unbound with a named
  reason; the downstream compiler/evaluator chain (validation →
  `validation_rejection`, http-response observer, delivery gate) is reused
  unchanged — this stage only supplies the missing binding link.

## Semantic Contract Binding — Implementation Anchors

Interface-documented business contracts (per-endpoint 关键契约/业务约束 lines
inside API documents and OpenAPI operation descriptions) bind to their owning
operations through `enterprise_knowledge_center/semantic_contract_binding.py`
(`apply_semantic_contract_binding`, staged in
`discovery_runtime_planning.build_discovery_plan` after the runtime overlay
merge and before the Behavior IR build). Pure-Chinese (CJK) business
statements — state machine, money conservation, idempotency,
sensitive-content contracts — previously failed every authoritative
rule-to-interface channel (verbatim-excerpt / exclusive ASCII contract fields /
same-source neighbors) and stayed unbound invariants with zero obligations.
Binding channels (all evidence-carrying, `status=accepted`):

- endpoint section line-range (a rule whose `source_locator=line:N` falls
  inside an interface's section is that interface's own contract line) /
  verbatim containment in the interface excerpt / CJK action-term bigram
  overlap with the interface's own summary → `rule_to_interface` edge
  (`derivation=interface_contract_attachment`);
- OpenAPI operation-description contracts are materialized into attached
  rules (`source_locator` with `#interface=<id>` identity, explicit
  `operation_refs`, channel `interface_contract_declaration`);
- conservation equations are structured into binary forms
  (field_equality / upper_bound / non_negative) with operands resolved to
  unique canonical fields — never the previous whole-statement multi-field
  `unchanged_sum` garbage equation that blocked every database-numeric
  projection;
- state-machine forbidden/allowed transitions bind via TO-state names in the
  bound contract text (`transition["operation_ref"]`), feeding
  `_derive_state_transition_relations` / forbidden-transition invariants;
- sensitive-content contracts (不得返回/禁止泄露 + key/password/secret
  vocabulary) route to the privacy family.

Pure enrichment: the adapter never invents rules, operations, actors or
fields; every edge carries a named evidence channel; rules that resolve to
nothing stay unbound with a named reason. Receipt
`qualibug.semantic-contract-binding.v1` on the IR.

## Rule-Surface Binding & Decision-Endpoint Validation — Implementation Anchors

Object-eligibility rules (优惠券必须在有效期内 / 状态必须为 ACTIVE / 使用次数
不能超过限制 / 类目券只能用于指定类目 / 折扣券必须遵守封顶金额) are concrete
contracts, not vague overlays, and the decision operations that enforce them
(validate/check/use/claim/simulate) carry the verdict in the RESPONSE body.
Anchors (update together with the code):

- **Subject-frame binding channel** — `behavior_ir_core`
  (`_subject_channel_resolution`): a rule's grounded semantic-frame subject
  (plus its constraint vocabulary, for subject-less rules like 必须满足最低
  订单金额) resolves through the asset's own business-object aliases →
  schema tables → operations whose PATH segments name the object. Resolved
  rules are never umbrella-excluded (`SUBJECT_FRAME_CHANNEL_CONCRETE`), bind
  to the object's DECISION surface only (`SUBJECT_FRAME_BINDING`), rekind
  state/permission/conservation rules to the validation family on that
  surface (`SUBJECT_DECISION_OP_REKIND`), and carry entity-scoped contract
  operands (`SUBJECT_ENTITY_SCOPED_OPERANDS` — 有效期→expires_at, 状态→
  status, 次数→user_limit/global_limit, 类目→category_scope, 封顶→
  max_discount, 最低金额→min_order_amount; never the global money fields a
  bare 金额 term would collect). Vague overlays (数据一致性) stay
  umbrella-excluded; money conservation on non-decision surfaces keeps its
  family.
- **Consumption-state / amount-boundary / object-scope treatment arms** —
  `experiment_protocols_base` (`_non_public_entity_treatment` consumption
  trigger + `_amount_boundary_treatment` + `_scope_violation_treatment`): the
  treatment input for an eligibility rule is an entity row the environment
  ACTUALLY has in the forbidden state (status non-public, validity date
  passed, a declared min/cap boundary, or a declared scope) — resolved at
  runtime, never guessed. Mutations: `runtime_entity_state_violation` (with
  `violation_mode` status/expiry/usage/any — usage selects a row whose
  declared usage reached its limit, reading the limit from the rule's OWN
  constrained limit fields carried as `usage_limit_fields` and the used count
  from the remaining numeric fields, so a user_limit value is never misread
  as a used count; rows without usage data never match, fail closed),
  `runtime_amount_boundary_violation`
  (min_amount → boundary − 1; max_cap → cap × 100 / rate + 1 on a percent-
  type row) and `runtime_scope_violation` (a scope-declaring row + a
  distinct scope value observed in the same collection for the line-item
  category). All three are resolved in `experiment_plan_step_executor_core`
  from the entity's own list read and FAIL CLOSED when no violating row
  exists (`BLOCKED_RUNTIME_VIOLATION_ROW_MISSING` /
  `BLOCKED_RUNTIME_AMOUNT_BOUNDARY_ROW_MISSING` /
  `BLOCKED_RUNTIME_SCOPE_ROW_MISSING`) — a treatment must never silently
  equal the control body.
- **Decision input surface gate (obligation compiler)** —
  `obligation_compiler_base` (`_decision_input_surface` + the
  `explicit_body_validation` exemption): the historical read-drop that keeps
  body-schema validation rules (format/required/type) off read operations
  with no body to validate must NOT swallow an ENTITY-ELIGIBILITY rule
  (expression operands carry entity fields) on its entity's own decision
  input surface (POST/PUT/PATCH whose path/summary carries the generic
  decision vocabulary 校验/验证/使用/领取/可用/模拟/试算/计算/预估/报价/
  validate/check/use/claim/simulate/estimate) — the decision response IS the
  effect, so the rule is decidable there even when the operation is
  read-like. Without this gate the decision-surface obligations for
  状态必须为 ACTIVE / 必须满足最低订单金额 / 类目券只能用于指定类目 /
  折扣券必须遵守封顶金额 silently vanish as coverage gaps.
- **Usage-limit decision-surface binding** — `behavior_ir_core`: a quota
  rule (使用次数/限用/只能使用 + 不能超过/限制/上限) keeps its idempotency
  replay invariant on the CONSUMPTION operations AND gains a second
  `validation`-kind invariant (`operator=under_limit`,
  `constraint_kind=USAGE_LIMIT`, `derived_invariant_kind=
  usage_decision_surface`) on the remaining DECISION operations — a decision
  surface that certifies an exhausted entity as usable violates the quota
  even though no consumption happens in that call. The runtime usage
  resolver fails closed when the environment exposes no usage data; never a
  fabricated finding.
- **Decision endpoints** — an operation whose path/summary carries
  validation vocabulary (校验/验证/使用/领取/模拟/validate/check/use/claim/
  simulate/estimate) is a decision surface: it does not mutate the entity,
  its response body IS the effect. The validation protocol marks such
  assertions `response_decision`, the observer parses decision-flavoured
  flags (valid/eligible/approved/usable/enabled — false = business
  rejection; the entity-code key is never misread as a reject token), and
  the validation oracle converts an accepted 2xx with an acceptance decision
  on a marked assertion into `VALIDATION_REJECTION_NOT_ENFORCED` (VIOLATION)
  instead of the historical zero-effect INDETERMINATE. Unmarked experiments
  keep the fail-closed INDETERMINATE. Cap rules assert the value bound
  directly (`json_path_compare` with `expected_path` — discountAmount ≤
  coupon.max_discount from the target's own response, numeric-safe).
- **Boundaries (must hold)** — the channel is fully source-driven
  (business-object aliases, schema fields and the rule's own text); no
  benchmark/GT material, no industry-term tables, no fuzzy similarity
  (containment/head-noun only, unique head required); rules that resolve to
  nothing stay unbound with visible fallback receipts; failures are visible
  in the attempt ledger (`BLOCKED_RUNTIME_*_ROW_MISSING`,
  `JSON_COMPARE_EXPECTED_PATH_MISSING`).
