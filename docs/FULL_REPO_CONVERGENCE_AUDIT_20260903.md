# QualiBug-AI Full-Repository Convergence Audit

Date: 2026-09-03
Branch: `chore/full-repo-convergence-audit-20260903`

## Objective

Converge the repository around one authoritative product/runtime path while preserving measured Bug Yield, evidence integrity, safety gates, and externally useful compatibility only where it is actually required.

The audit does **not** treat module count reduction as a product-quality claim. A deletion is acceptable only when runtime reachability, tests, release packaging, and compatibility evidence show it is safe.

## Classification

Each item is classified as one of:

- **KEEP/FREEZE** — authoritative and value-bearing; avoid feature expansion unless a measured benchmark requires it.
- **CONSOLIDATE** — required behavior exists but is split across facades, installers, patches, wrappers, or duplicate authorities.
- **RETIRE** — no current product/runtime value; remove from the current tree after dependency proof.
- **P0 CORRECTNESS** — can change Bug Yield, findings truth, safety, or authority.
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
| Runtime composition | `private_pilot_entrypoint` installs a long chain of `*_patch` components at startup. These patches are no longer temporary in practice; they are architecture. | CONSOLIDATE / P1 | Inventory patch ownership and fold permanent patches into explicit composition modules. |
| Discovery composition | `discovery_runtime_semantic_binding` performs many import-time installer side effects and replaces planning symbols with compatibility wrappers. | CONSOLIDATE / P0 | Replace implicit import-time mutation with one explicit runtime registry/composition step after reachability tests exist. |
| Compatibility label | `v12_pipeline` is marked compatibility but still contains substantial Campaign/runtime/scheduling logic and imports `pipeline_*` / `v12_legacy_*`. | CONSOLIDATE / P0 | Do not delete by label. First extract remaining live responsibilities and prove callers. |
| CLI authority | Package script `qualibug` still points to `aitestops.cli:main`, while `aitestops discover` is explicitly deprecated and canonical discovery lives under `ai_test_asset_center`. | CONSOLIDATE / P0 | Make one user-facing CLI authority; retain tooling subcommands only as explicit tools. |
| Legacy API | `backend/main.py` is explicitly deprecated but still exposes synthetic MockEngine endpoints alongside compatibility real-scan endpoints. | RETIRE / P1 | Separate/remove synthetic endpoints; retain only a thin compatibility adapter if external consumers exist. |
| Dev mock service | `enterprise_bug_factory/app/main.py` explicitly identifies itself as dev-only and uses `core.engine.MockEngine`. | RETIRE / P2 | Move to test/example fixture or delete after references are checked. |
| Historical package | `aitestops/` remains a sizable older product/tooling package with its own generators, engines, failure analysis, UI tester and CLI. | CONSOLIDATE / RETIRE | Split retained tooling from retired product implementation; stop presenting it as the primary CLI. |
| Silent degradation | `product_scan_mainline._scan_campaign_context_defaults` still catches connector/config loading failures with bare `except Exception: pass`. | P1 RELIABILITY | Replace with visible structured degradation receipt/log while preserving safe fallback. |
| Silent degradation | `ai_test_asset_center.__main__._phase_time` still swallows all instrumentation failures. | P2 HYGIENE | Make debug/warning-visible without affecting scan result. |
| CI hygiene | `.github/workflows` contains many `one-time-*`, `apply-*-root-fix` and trigger-driven migration workflows; `.github/` also contains trigger/probe files. | RETIRE / P2 | Retire completed one-time workflows and triggers; preserve reusable quality gates only. |
| Repo hygiene | Root contains development-assistant state and historical working areas (`.canvases`, `.codebuddy`, `.codex`, `.omm`, `.scratch`, `.trae`) plus generated/audit directories. | RETIRE / P2 | Separate source-of-truth product repository from local/agent scratch and generated evidence. |
| Architecture governance | Architecture inventory exists and is useful, but the budget still permits a very large surface (`430` modules / `245000` Python lines). | KEEP + RATCHeT | Never increase these ceilings; reduce only after dependency-proven deletions. |

## First completed convergence changes

- Retired `.github/workflows/one-time-export-current-main.yml`.
- Removed `.github/source-export-trigger` that existed only to activate that completed one-time workflow.

## Audit tracks

1. **Entrypoints and authority** — packaging scripts, HTTP entrypoints, scan entrypoints, compatibility adapters.
2. **Discovery cognition** — ingestion, knowledge asset, semantic linker, Behavior IR, hypothesis/reasoner, obligation generation.
3. **Compilation and execution** — bindings, fixture/precondition chains, experiment compiler, scheduler, transport, cleanup.
4. **Observation and adjudication** — observers, assertions, Oracle, reproduction, canonical identity, delivery gate.
5. **Product/API/UI** — private-pilot service mixins, frontend, command center, report projections, connectors.
6. **Compatibility/dead paths** — `v12_*`, `pipeline_*`, `real_project_*`, `aitestops`, `backend`, mocks, zombie modules.
7. **Tests/CI/evaluation** — test credibility, wrong-target monkeypatches, stale tests, one-time workflows, hidden-GT isolation.
8. **Repository/release hygiene** — scratch, generated artifacts, package data, Docker/release contents, docs drift.

## Non-negotiable convergence rules

- No deletion based only on file age/name or a `compatibility` label.
- One product capability must have one authoritative implementation and one explicit composition point.
- Import-time monkey-patching/installer chains are migration mechanisms, not a permanent architecture target.
- A fallback that reduces discovery breadth must be observable in the final scan artifact, not logs only.
- No raw finding count is used as a success criterion; preserve canonical unique-defect and external-evaluator truth.
- Historical benchmark/evaluator data remains isolated from runtime cognition.
- Completed one-time CI machinery is removed from the current tree; Git history is the archive.
- Architecture module/LOC budgets only ratchet downward.

## Next convergence gates

Before changing live discovery semantics, establish:

- exact import/reachability map for every declared product/evaluation/tooling root;
- test coverage for canonical entrypoint → plan → execution → Oracle → canonical registry → delivery;
- explicit list of live responsibilities still implemented under `v12_pipeline` / `pipeline_*`;
- explicit list of `aitestops` commands that remain intentionally supported;
- explicit list of private-pilot `*_patch` installers that are permanent behavior versus removable migration layers.
