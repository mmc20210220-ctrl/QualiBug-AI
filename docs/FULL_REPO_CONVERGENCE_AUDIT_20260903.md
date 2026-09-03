# QualiBug-AI Full-Repository Convergence Audit

Date: 2026-09-03
Branch: `chore/full-repo-convergence-audit-20260903`
Draft PR: #187

## Objective

Converge the repository around one authoritative product/runtime path while preserving measured Bug Yield, evidence integrity, safety gates, and externally useful compatibility only where it is actually required.

The audit does **not** treat module count reduction as a product-quality claim. A deletion is acceptable only when runtime reachability, tests, release packaging, and compatibility evidence show it is safe.

## Classification

Each item is classified as one of:

- **KEEP/FREEZE** — authoritative and value-bearing; avoid feature expansion unless a measured benchmark requires it.
- **CONSOLIDATE** — required behavior exists but is split across facades, installers, patches, wrappers, or duplicate authorities.
- **RETIRE** — no current product/runtime value; remove from the current tree after dependency proof.
- **P0 CORRECTNESS** — can change Bug Yield, finding truth, safety, search allocation, or authority.
- **P1 RELIABILITY** — can silently degrade capability, execution, or observability.
- **P2 HYGIENE** — cognitive/CI/repository noise with low runtime risk.

## Current authoritative product chain

Current declared scan authority:

`ai_test_asset_center.__main__.scan`
→ `product_scan_mainline`
→ `discovery_mainline`
→ `discovery_runtime`
→ planning / experiment compilation / governed execution / Oracle / canonical defect registry / delivery gate.

Current deployed private-pilot service:

`qualibug-server`
→ `ai_test_asset_center.private_pilot_entrypoint.run_server`
→ `private_pilot_service`.

## Confirmed convergence findings

| Area | Finding | Classification | Action |
|---|---|---:|---|
| Runtime composition | `private_pilot_entrypoint` installs a long chain of `*_patch` components at startup. These patches are no longer temporary in practice; they are architecture. | CONSOLIDATE / P1 | Classify every patch by truth/search authority versus presentation/adapter responsibility and fold permanent behavior into explicit composition modules. |
| Scan result authority | `__main__.scan()` persists the canonical `scan_result.json` inside `_scan_impl`, then executes post-hooks. Truth-changing post-hooks can therefore create a returned result that differs from the already persisted scan result. | P0 CORRECTNESS | Make post-hooks projection-only, or move any authoritative mutation before canonical persistence and re-run canonical validation. |
| Finding repair | `private_pilot_scan_result_repair_patch` can retry evidence persistence, change `confirmation_status` from empty/`inconclusive` to `confirmed`, move rows from candidates to findings, and change headline counts. | P0 CORRECTNESS | Root-fix the original persistence failure and retire this repair layer. Until then, require canonical defect/delivery authority before any restoration; never promote truth from persistence success alone. |
| Private Pilot DB authority | Private-pilot persistence re-collects findings from report/legacy projections rather than directly consuming the current canonical formal scope. The cumulative SQLite table deduplicates with `title|method|path` instead of `canonical_defect_id`. | P0 CORRECTNESS | Persist current-formal canonical identities end-to-end; store `canonical_defect_id` as first-class DB identity and use cumulative history only as a projection. |
| Search authority | `private_pilot_coverage_steering_patch` reads coverage gaps, project/platform learning weights and historical confirmed findings, then reorders slices and raises priority. It is a real search-policy authority, not presentation. | P0 CORRECTNESS | Move into the single canonical search-policy layer. Do not remove or retune until frozen multi-target Bug Yield measurement exists. |
| Discovery composition | `discovery_runtime_semantic_binding` performs many import-time installer side effects and replaces planning symbols with compatibility wrappers. | CONSOLIDATE / P0 | Replace implicit import-time mutation with one explicit runtime registry/composition step after reachability tests exist. |
| Post-hook breadth | Built-in scan post-hook installation previously swallowed every import/installer exception with `continue`, making missing projections indistinguishable from successful installation. | P1 RELIABILITY | Fixed on this branch: failures are now logged with module, installer and exception details while remaining non-blocking. |
| Compatibility label | `v12_pipeline` is marked compatibility but still contains substantial Campaign/runtime/scheduling logic and imports `pipeline_*` / `v12_legacy_*`. | CONSOLIDATE / P0 | Do not delete by label. First extract remaining live responsibilities and prove callers. |
| CLI authority | Package script `qualibug` still points to `aitestops.cli:main`, while `aitestops discover` is explicitly deprecated and canonical discovery lives under `ai_test_asset_center`. | CONSOLIDATE / P0 | Separate supported tooling from product operations before switching the primary CLI. Do not break existing tool commands blindly. |
| Legacy API | `backend/main.py` is explicitly deprecated but still exposes synthetic MockEngine endpoints alongside compatibility real-scan endpoints. | RETIRE / P1 | Separate/remove synthetic endpoints; retain only a thin compatibility adapter if external consumers exist. |
| Dev mock service | `enterprise_bug_factory/app/main.py` explicitly identifies itself as dev-only and uses `core.engine.MockEngine`. | RETIRE / P2 | Move to test/example fixture or delete after references are checked. |
| Release boundary | Docker copies `ai_test_asset_center/` and `aitestops/`, but not `backend/`, `core/` or `enterprise_bug_factory/`. Those latter trees are not production-image dependencies. | RETIRE CANDIDATE | Treat them as dev/compatibility surfaces, not product core; verify external consumers before removal. |
| Historical package | `aitestops/` remains a sizable older product/tooling package with its own generators, engines, failure analysis, UI tester and CLI, and is still included in the production image/package. | CONSOLIDATE / RETIRE | Split retained tooling from retired product implementation; stop presenting it as the primary CLI, then remove unnecessary production-image/package dependency. |
| Silent degradation | `product_scan_mainline._scan_campaign_context_defaults` still catches connector/config loading failures with bare `except Exception: pass`. | P1 RELIABILITY | Replace with visible structured degradation receipt/log while preserving safe fallback. |
| Silent degradation | `ai_test_asset_center.__main__._phase_time` and some retention/tail projections still contain non-observable fail-soft paths. | P2/P1 | Preserve non-blocking behavior but make every capability/breadth loss visible. |
| CI authority | Permanent `quality-gates.yml` contained branch-specific one-shot jobs capable of dispatch/apply/push behavior and previously requested write permissions. | RETIRE / P1 | Fixed on this branch: permanent PR gate is test-only and read-only. |
| CI event pollution | Runner-probe and one-time workflows listened to all PR/`pull_request_target` events even when meaningful only for historical PRs. Draft PR #187 immediately created unrelated Recall and one-time delivery checks. | RETIRE / P2 | Retire completed probes and one-time delivery workflows/triggers; reusable product gates only should observe normal PR events. |
| Legacy tests in formal gate | The backend contract manifest still includes tests that protect deprecated `backend/main.py` and `core.MockEngine`, so CI currently encodes legacy compatibility as permanent product contract. | CONSOLIDATE / P1 | Split canonical product gate and time-bounded legacy compatibility gate; give legacy an explicit retirement condition. |
| Repo hygiene | Root mixes product source with development-assistant state, generated/audit areas and historical working assets. | RETIRE / P2 | Keep current source tree navigable; Git history is the archive. `.canvases/` and `.codebuddy/` are removed on this branch; evaluate remaining tool-state directories individually. |
| Architecture governance | Architecture inventory exists and is useful, but the budget still permits a very large surface (`430` modules / `245000` Python lines). | KEEP + RATCHET | Never increase these ceilings; reduce only after dependency-proven deletions. |

## Completed convergence changes on this branch

- Created this persistent convergence audit ledger.
- Retired `.github/workflows/one-time-export-current-main.yml` and its trigger.
- Removed `.github/noop-tree-probe` and `.github/noop-tree-probe-2`.
- Simplified permanent `.github/workflows/quality-gates.yml` to the test-only backend contract gate and reduced workflow permissions to `contents: read`.
- Retired the global PR Recall runner probes (`recall-root-fix-verify.yml`, `recall-root-fix-target-probe.yml`).
- Retired the completed single-object complex delivery workflow and trigger.
- Removed `.canvases/` and `.codebuddy/` from the current source tree; their history remains in Git.
- Made built-in scan post-hook installation/timing failures observable without changing scan verdict behavior.

## Audit tracks

1. **Entrypoints and authority** — packaging scripts, HTTP entrypoints, scan entrypoints, compatibility adapters.
2. **Discovery cognition** — ingestion, knowledge asset, semantic linker, Behavior IR, hypothesis/reasoner, obligation generation.
3. **Search policy** — hypothesis budgets, family caps, coverage steering, historical-learning boosts, fairness and dedup.
4. **Compilation and execution** — bindings, fixture/precondition chains, experiment compiler, scheduler, transport, cleanup.
5. **Observation and adjudication** — observers, assertions, Oracle, reproduction, canonical identity, delivery gate.
6. **Product/API/UI** — private-pilot service mixins, frontend, command center, report projections, connectors.
7. **Compatibility/dead paths** — `v12_*`, `pipeline_*`, `real_project_*`, `aitestops`, `backend`, mocks, zombie modules.
8. **Tests/CI/evaluation** — test credibility, wrong-target monkeypatches, stale tests, one-time workflows, hidden-GT isolation.
9. **Repository/release hygiene** — scratch, generated artifacts, package data, Docker/release contents, docs drift.

## Non-negotiable convergence rules

- No deletion based only on file age/name or a `compatibility` label.
- One product capability must have one authoritative implementation and one explicit composition point.
- Import-time monkey-patching/installer chains are migration mechanisms, not a permanent architecture target.
- Post-hooks may not silently create a second finding truth after canonical persistence.
- A fallback that reduces discovery breadth must be observable in the final scan artifact, not logs only.
- Canonical defect identity must survive into persistence, UI and cumulative history; never fall back to heuristic title/path identity where a canonical ID exists.
- No raw finding count is used as a success criterion; preserve canonical unique-defect and external-evaluator truth.
- Historical benchmark/evaluator data remains isolated from runtime cognition.
- Search-policy changes require frozen multi-target measurement; code plausibility is not a Bug Yield claim.
- Completed one-time CI machinery is removed from the current tree; Git history is the archive.
- Architecture module/LOC budgets only ratchet downward.

## Next convergence gates

Before changing live discovery semantics, establish:

- exact import/reachability map for every declared product/evaluation/tooling root;
- focused contract test for canonical entrypoint → current-formal findings → canonical registry → persistence → delivery/UI projection;
- proof or retirement path for `private_pilot_scan_result_repair_patch`;
- explicit list of live responsibilities still implemented under `v12_pipeline` / `pipeline_*`;
- explicit list of `aitestops` commands that remain intentionally supported;
- explicit classification of every private-pilot startup installer as canonical truth/search authority, operational adapter, or retirement candidate;
- a frozen external multi-target Bug Yield run before any search-policy deletion/retuning.
