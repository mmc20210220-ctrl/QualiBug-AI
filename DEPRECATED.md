# Deprecated & Dormant Modules

> Architecture health audit — generated 2025-06-28.
> See AGENTS.md for runtime guardrails and syntax-check rules.

## Overview

QualiBug's codebase has grown through rapid iteration (11 engine hot path, 13-module side path, 150+ Python source files). This document tracks modules that are **defined but not wired** into any active execution path. Each entry includes a roadmap for activation.

**Policy**: Deprecated modules are preserved (not deleted) because they represent validated design work. Each must either be **activated** (wired into a path) or **pruned** (removed with a clear reason). No module stays "zombie" indefinitely.

---

## 🔴 Zombie Modules (0 external cross-references)

These files exist in the codebase but are never imported or referenced by any other module.

| # | File | Lines | Original Purpose | Activation Roadmap |
|---|------|-------|-----------------|-------------------|
| 1 | `cross_endpoint_verifier.py` | 265 | Compare entity state across multiple API endpoints (list vs detail, admin vs viewer, cache vs source-of-truth) | Wire into `real_project_defect_discovery.py` as a Side Path analysis module, or merge into Hot Path verifier pipeline |
| 2 | `entity_catalog_builder.py` | 751 | Extract typed entity catalog from PRD + OpenAPI (identity/state/amount/quantity fields) | **High priority** — Core building block for behavior space ontology. Wire into `discovery_engine.py` as pre-processing step before entity-level probe generation |
| 3 | `historical_bug_importer.py` | 485 | Import historical bug reports from CSV/JSON/Markdown; classify by risk keyword; map to API surface | **Critical for data flywheel** — Wire into onboarding pipeline to bootstrap cross-enterprise behavior pattern library |
| 4 | `rag_ab_evaluator.py` | 272 | A/B evaluate RAG retrieval strategies (recall, precision, latency) | Wire into Enterprise Knowledge Center evaluation harness |

## 🟡 Near-Zombie Modules (≤1 external reference)

These modules have minimal wiring — often only referenced by a single import that may itself be dead code.

| # | File | Refs | Original Purpose | Activation Roadmap |
|---|------|------|-----------------|-------------------|
| 5 | `issue_lifecycle_center.py` | 0 | Central issue lifecycle tracking (status transitions, SLA) | Wire into `runtime_finding_lifecycle_registry.py` |
| 6 | `model_evaluation_harness.py` | 0 | Evaluate ML model quality on benchmark suites | Future: model evaluation pipeline |
| 7 | `model_deployment_gate.py` | 0 | Gate model deployment based on evaluation metrics | Future: CI/CD model deployment gate |
| 8 | `rag_quality_gate.py` | 0 | Quality gate for RAG pipeline outputs | Wire into `rag_probe_generator.py` pipeline |
| 9 | `rag_probe_generator.py` | 1 | Generate probes targeting RAG/KB endpoints | Verify single reference is active; activate or prune |
| 10 | `human_feedback_loop.py` | 1 | Collect human feedback on findings for RLHF | Wire into finding lifecycle for human-in-the-loop review |

## 🟠 Side-Path-Only Engines (not in Hot Path)

These modules are wired into `real_project_defect_discovery.py` (Side Path, triggered via `blind_project_runner.py`) but NOT in `stage_reason_all_v2.py` (Hot Path, triggered via cron loop).

| # | Module | Engine | Side Path? | Hot Path? |
|---|--------|--------|-----------|-----------|
| 11 | `business_lifecycle_reasoning.py` | lifecycle | ✅ | ❌ |
| 12 | `business_assurance_coverage.py` | assurance | ✅ | ❌ |
| 13 | `business_adaptation_layer.py` | adaptation | ✅ | ❌ |
| 14 | `defect_discovery.py` | defect_classification | ✅ | ❌ |
| 15 | `multisource_reasoning.py` | multi_source | ✅ | ❌ |
| 16 | `multi_industry_business_reasoning.py` | multi_industry | ✅ | ❌ |

**Note**: These 6 engines are fully implemented with LLM prompt templates and probe generation logic. They only need to be added to the `engines` list in `stage_reason_all_v2.py` (lines 342-354) to activate them in the Hot Path.

---

## Architecture Decision: Hot Path vs Side Path

```
                    ┌─────────────────────┐
                    │   run_loop_worker.py │  ◄── Unified entry point
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ autonomous_evolution│
                    │ _orchestrator.py    │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                                 │
    ┌─────────▼──────────┐          ┌───────────▼──────────┐
    │  HOT PATH           │          │  SIDE PATH            │
    │  stage_reason_all   │          │  real_project_defect  │
    │  _v2.py             │          │  _discovery.py        │
    │                     │          │                       │
    │  11 engines:        │          │  13 modules:          │
    │  causality          │          │  + 6 engines above    │
    │  invariant          │          │  + business_causality │
    │  reconciliation     │          │  + consistency_iso    │
    │  counterexample     │          │  + metamorphic_diff   │
    │  consistency        │          │  + temporal_regression│
    │  population         │          │  + outcome_validation │
    │  outcome            │          │  + reconciliation     │
    │  temporal           │          │  + invariant_mining   │
    │  saga               │          │                       │
    │  event_chain        │          │  Triggered ONLY by:   │
    │  metamorphic        │          │  blind_project_runner │
    │                     │          │                       │
    │  Triggered by:      │          │  ──────────────────   │
    │  cron loop (always) │          │  DISCONNECTED from:   │
    │                     │          │  - cron loop          │
    └─────────────────────┘          │  - Web UI _run_job   │
                                     └──────────────────────┘
```

**Primary action item**: Bridge Hot Path and Side Path by adding the 6 Side-Path-only engines to `stage_reason_all_v2.py`'s engine list, OR merge the Side Path modules into the Hot Path verifier pipeline.

---

## Cleanup Progress

| Date | Action | Details |
|------|--------|---------|
| 2025-06-28 | Deprecation markers added | All 4 zombie + 6 near-zombie modules tagged with `[DEPRECATED]` headers |
| 2025-06-28 | DEPRECATED.md created | Centralized tracking of architecture debt |
