# Phase71 Deep Analysis — Project-Scoped Data Isolation

## Scope and evidence discipline

This analysis treats QualiBug's private pilot service as the system under test.
It separates three different things that are often conflated:

1. an OpenAPI or PRD contract gap;
2. a candidate requiring a sandbox or deterministic replay; and
3. a reproducible runtime defect.

Only the third category was used to justify the Phase71 detection change.
No destructive request, production target, credential capture, or model-only
finding was used.

## Baseline metrics

The baseline autonomous pipeline report contained 13 candidates: P1=5, P2=1
and P3=7. All were emitted by `deep_bug_mining`; four had static proof and
nine were contract- or traceability-inferred. The pattern library reported 21
patterns (14 pre-seeded and 7 learned). The separate private self-dogfood audit
had no P0/P1 runtime finding before project-scope probing was added.

The isolated health check was healthy, but its LLM status was `offline` because
no provider was configured in the build runtime. LLM availability was not used
as evidence for any conclusion in this phase.

## System architecture and state machines

```text
PRD / OpenAPI / runtime evidence
  -> enterprise knowledge assets
  -> autonomous pipeline and deterministic reasoning engines
  -> evidence, validation queue and risk plan
  -> private pilot reports, release dashboard and export
```

Primary entities and legal transitions:

| Entity | Legal state / transition | Key rule |
|---|---|---|
| Knowledge source | ingest -> active/versioned -> reanalyze or delete | Source is project-scoped and auditable. |
| Scan | inputs prepared -> scan run -> discovery/validation/calibration -> report/history | Summary, detail, severity and impact aggregates must agree. |
| Pilot task | waiting_approval -> approved -> queued -> running -> succeeded / failed / requeued; rejected/cancelled terminal | Submitter and approver roles are separated. |
| Environment | declared test/staging safe target -> read-only execution; production/undeclared -> blocked | Safety boundary is evaluated before network activity. |
| LLM advisory | prompt -> `unverified_hypothesis` -> deterministic replay required | It cannot affect formal findings or release decisions directly. |

Private-service boundaries are equally important to the state model: `/health`
is public; every other route needs a trusted actor and role. For public or
private-cloud binding, the reverse proxy additionally owns the project
allow-list and injects `X-QualiBug-Project-Scopes`.

## Baseline finding-to-producer traceback

| # | Baseline finding | Producer / trigger | Evidence | Quality assessment |
|---:|---|---|---|---|
| 1 | Global security scheme absent | `deep_bug_mining._permission_findings` detected no `securitySchemes` in the reduced dogfood fixture | contract-inferred | Real fixture contract gap; not proof of a live authorization bypass. |
| 2 | Knowledge ingest idempotency risk | `_idempotency_findings`, operation-local `Import` signal on `POST /api/knowledge/ingest` | contract-inferred | Likely real API-contract gap; backend semantics still need sandbox replay. |
| 3 | Ingest lacks 401/403 response contract | `_error_contract_findings` | contract-inferred | Real specification gap; runtime boundary may still reject correctly. |
| 4 | Scan run lacks 401/403 response contract | `_error_contract_findings` | contract-inferred | Real specification gap; not itself a runtime data leak. |
| 5 | Settings save lacks 401/403 response contract | `_error_contract_findings` | contract-inferred | Real specification gap; not itself a runtime data leak. |
| 6 | Export requirement lacks mapped API | `_requirement_traceability_findings` matched PRD export language | traceability-inferred | Likely documentation/contract gap; service has an undocumented report route, so capability absence was not proven. |
| 7 | Ingest auth boundary undocumented | `_permission_findings` | contract-inferred | Static gap; runtime validator classified it as guarded. |
| 8 | Scan auth boundary undocumented | `_permission_findings` | contract-inferred | Static gap; runtime validator classified it as guarded. |
| 9 | Settings auth boundary undocumented | `_permission_findings` | contract-inferred | Static gap; runtime validator classified it as guarded. |
| 10 | Settings save lacks operationId | `_spec_structure_findings` | static-proof | True metadata defect, P3 materiality. |
| 11 | Scan run lacks operationId | `_spec_structure_findings` | static-proof | True metadata defect, P3 materiality. |
| 12 | Knowledge ingest lacks operationId | `_spec_structure_findings` | static-proof | True metadata defect, P3 materiality. |
| 13 | Health lacks operationId | `_spec_structure_findings` | static-proof | True metadata defect, P3 materiality. |

The baseline did **not** test whether an authenticated caller could replace a
`project` query value with another customer's project. Tenant and role checks
cannot establish that relation when a service uses project/workspace IDs rather
than a tenant field in response rows.

## Endpoint detection-surface coverage matrix

Legend: **✓** deterministic coverage exists; **△** static/configured-only or
partial coverage; **S** requires an isolated mutation/concurrency sandbox; **—**
not applicable to the route's data shape. `P` marks the Phase71 project-scope
check, which is enabled only through an explicit GET contract and distinct
identity context.

| Endpoint | Auth | Contract | Data | Logic | Race | Leak | Coverage reason |
|---|---|---|---|---|---|---|---|
| GET `/health` | — | △ | — | △ | — | △ | Public liveness only; crash/error-shape check is safe, no customer entity. |
| GET `/dashboard` | ✓ P | △ | △ | △ | — | △ | Project view; aggregate drift and page secret audit are partial. |
| GET `/api/pilot/overview` | ✓ P | △ | △ | △ | — | △ | Project overview is read-only; semantic data rules need an explicit contract. |
| GET `/knowledge` | ✓ P | △ | △ | △ | — | △ | Project HTML; source ownership needs explicit source/tenant fixture. |
| GET `/api/knowledge/asset` | ✓ P | △ | △ | △ | — | △ | Asset read path; cross-project selector is now testable. |
| POST `/api/knowledge/ingest` | △ | ✓ | S | S | S | △ | Mutation; idempotency, size and race checks require sandbox fixtures. |
| POST `/api/knowledge/delete` | △ | △ | S | S | S | △ | Destructive transition cannot run against a live target. |
| POST `/api/knowledge/reanalyze` | △ | △ | S | S | S | △ | Async/version effects need sandbox task evidence. |
| GET `/api/knowledge/preview` | ✓ P | △ | △ | △ | — | ✓ | Source-ID enumeration, project selector and secret rendering are relevant. |
| GET `/findings` | ✓ P | △ | ✓ | ✓ | — | △ | Summary/detail conservation and project boundary are applicable. |
| GET `/api/findings` | ✓ P | △ | ✓ | ✓ | — | △ | Phase71 proof path; report aggregate consistency is covered. |
| POST `/api/scan/run` | △ | ✓ | S | S | S | △ | State transition and idempotency need a dedicated sandbox. |
| POST `/api/environment/config` | △ | ✓ | S | S | S | △ | Configuration writes need role and transaction sandbox evidence. |
| GET `/settings` | ✓ P | △ | △ | △ | — | ✓ | Project/credential-reference visibility is relevant; values must not render. |
| POST `/api/settings/save` | △ | ✓ | S | S | S | △ | Settings writes require sandbox and role-transition validation. |
| POST `/api/connectors/register` | △ | △ | S | S | S | ✓ | Credential reference and validation-error leak checks are sandbox-only. |
| GET `/api/pilot/tasks` | ✓ P | △ | △ | ✓ | — | △ | Project task visibility and legal state read consistency apply. |
| POST `/api/pilot/tasks` | △ | △ | S | S | S | △ | Task creation can be idempotent/racy; requires sandbox. |
| POST `/api/pilot/tasks/approve` | △ | △ | S | ✓ | S | △ | Approver separation and state transition require sandbox. |
| POST `/api/pilot/tasks/run-next` | △ | △ | S | ✓ | S | △ | Queue ordering and duplicate dispatch require sandbox. |
| GET `/control-plane` | ✓ P | △ | △ | ✓ | — | △ | Project operations view; read-model rules require explicit fields. |
| GET `/api/control-plane/overview` | ✓ P | △ | △ | ✓ | — | △ | Project aggregate and state visibility are read-only. |
| GET `/release` | ✓ P | △ | ✓ | ✓ | — | △ | Release risks, counts and project boundary are applicable. |
| GET `/benchmark` | ✓ P | △ | △ | △ | — | △ | Project benchmark read; business semantics need explicit relation config. |

### Why cells are not active checks

1. **Mutation routes (`S`)**: applying malformed, repeated or concurrent writes
   to enterprise systems can change state. The safe live policy intentionally
   excludes them; sandbox execution is the required next layer, not a blind
   coverage omission.
2. **Data/logic on GET pages (`△`)**: existing engines can evaluate them only
   when a customer maps entity keys, expected formulas or source/target views.
   Guessing fields from labels would create noise.
3. **Race (`—`/`S`)**: every meaningful race requires repeated state-changing
   execution or a deterministic replay fixture; it is not safe to infer from
   one GET response.
4. **Leak (`△`)**: page/report secret audit and response-sensitive-field logic
   exist, but exhaustive per-field expectations still require enterprise
   configuration.

## Adversarial endpoint analysis

| Endpoint group | Adversarial questions and expected boundary |
|---|---|
| `/health` | Is it available without exposing filesystem paths, stack traces or configuration values? It is intentionally public but should expose only liveness. |
| Project GET views (`/dashboard`, `/knowledge`, `/findings`, `/settings`, `/control-plane`, `/release`, `/benchmark`) | Can a caller omit actor headers, substitute another `project`, observe stale aggregates, or receive tokens/connector details? Actor/role and Phase71 project scope apply; data formulas need explicit contracts. |
| Project GET APIs (`/api/pilot/overview`, `/api/knowledge/asset`, `/api/findings`, `/api/pilot/tasks`, `/api/control-plane/overview`) | Can authenticated principals swap project ID, enumerate source/task IDs, read stale or cross-view inconsistent data, or observe sensitive fields? Phase71 targets the project-selector condition. |
| `/api/knowledge/preview` | Can another project's `source_id` be replayed with a substituted project, can content leak, and do errors reveal paths? The same project-scope boundary applies; source ownership is a future explicit relation. |
| Knowledge mutation APIs | Can oversized/invalid imports, repeated ingest, delete/reanalyze ordering or another project ID create orphaned versions? These need a seeded sandbox, not live writes. |
| Scan/environment/settings/connector mutation APIs | Can caller role/project scope be bypassed, can idempotency fail, can malformed input expose a secret or absolute path, and can configuration transition to production? Contract/static checks run now; mutation evidence must be sandboxed. |
| Pilot task mutation APIs | Can a submitter approve their own task, invoke invalid transitions, run a task twice or race the queue? These are state-machine/race questions requiring a sandbox actor matrix. |

## Gap ranking and decision

| Gap | Exploitability | Impact | Current detectability | Decision |
|---|---|---|---|---|
| Authenticated caller substitutes project selector | High: one GET with another project ID | High: cross-customer report/knowledge/release disclosure | Low: existing tenant/role checks do not model project selector relation | **Selected** |
| Runtime-only routes absent from OpenAPI | Medium | Medium to high depending on route | Partial: static contract drift only | Not selected; no evidence that remaining routes bypass the new global scope gate. |
| Mutation error reveals paths or stack details | Medium | Medium | Partial | Not selected; safe evidence requires a non-destructive seeded sandbox. |
| Task transition/race defect | Medium | High | Low in live mode | Not selected; meaningful proof requires controlled mutations/concurrency. |

## Phase71 outcome and learning

Before remediation, a local safe fixture gave an authenticated `alpha_user`
only role headers and requested `GET /api/findings?project=bravo_project`.
The service returned HTTP 200 and the foreign report marker. This was a
reproducible P0, not a pattern match.

The selected solution extends the existing consistency/isolation engine with
`project_scope_contracts`. It requires an OpenAPI-declared GET query parameter,
an explicit own/foreign project pair, and an independent authenticated context.
It then makes exactly two GETs. A success response for the foreign project is
P0 evidence; no success response is synthesized from a missing mapping.

The service remediation requires a trusted reverse-proxy project allow-list on
public/private-cloud binding. After the fix, the own project returned HTTP 200,
the foreign project returned HTTP 403, and the same Oracle emitted no finding.
The self-dogfood audit now includes this negative authorization probe.