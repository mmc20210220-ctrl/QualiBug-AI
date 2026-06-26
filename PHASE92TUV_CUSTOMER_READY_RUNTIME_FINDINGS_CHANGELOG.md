# Phase92T/U/V/W — Customer-ready runtime findings

## Goal

Continue the Phase92P/Q/R/S runtime loop from evidence detection into customer-facing delivery.
The previous phases could validate business-invariant violations from before/after snapshots, semantic observer joins and cross-observer conservation checks.  Phase92T/U/V/W makes those validated candidates easier to trust, triage, reproduce and act on in a commercial report.

## Phase92T — Runtime finding evidence packager

Added `ai_test_asset_center/runtime_finding_evidence_packager.py`.

Each runtime validated finding now receives a structured `evidence_package` containing:

- evidence strength score and evidence grade;
- customer-ready summary explaining why this is not a static rule hit;
- HTTP response evidence chain;
- before/after snapshot counts and status codes;
- observer kinds and evidence goals;
- violated invariant list;
- delta summary, including negative values, replay IDs, semantic graph summary and cross-observer failures;
- generated reproduction asset inventory.

## Phase92U — Customer impact triage

Added `ai_test_asset_center/runtime_finding_customer_triage.py`.

Each validated finding now receives:

- severity: `critical | high | medium | low`;
- priority: `P0 | P1 | P2 | P3`;
- risk family: security/data isolation, business resource integrity, workflow/state integrity or runtime contract violation;
- customer impact summary;
- blast-radius signals;
- recommended owner;
- recommended next step.

## Phase92V — Customer delivery index

Added `ai_test_asset_center/runtime_customer_report_builder.py`.

The execution report now includes `customer_delivery_index`, which aggregates validated findings by:

- priority;
- severity;
- risk family;
- violated invariant kind;
- evidence coverage;
- top customer actions sorted by priority, severity and evidence strength.

## Phase92W — Reproduction artifact backlinks

Added `ai_test_asset_center/runtime_reproduction_asset_linker.py`.

After executor outputs are created, each finding now links directly back to generated artifacts:

- machine-readable execution report;
- customer-readable markdown report;
- PowerShell reproduction asset;
- pytest regression asset.

The report now includes `reproduction_artifact_index`, and each finding embeds `reproduction_artifact_links` plus a primary repro asset pointer.

## Executor integration

Updated `ai_test_asset_center/grounded_probe_executor.py`:

- engine advanced to `grounded_probe_executor_v12_phase92w`;
- governance flags now expose Phase92T/U/V/W;
- `_finding_from_observation` embeds `evidence_package`, `customer_triage`, `severity`, `priority`, `customer_impact_summary`, `violated_invariants` and `delta_summary`;
- report summary includes customer-ready package count, strong evidence count, critical/high counts and priority distribution;
- markdown report prints evidence strength, triage and delta summary;
- report top-level includes `customer_delivery_index` and `reproduction_artifact_index`;
- every finding receives concrete generated artifact backlinks after output files are written.

## Tests added

- `tests/test_phase92t_runtime_finding_evidence_packager.py`
- `tests/test_phase92u_runtime_finding_customer_triage.py`
- `tests/test_phase92v_runtime_customer_report_builder.py`
- `tests/test_phase92w_runtime_reproduction_asset_linker.py`

## Verification

Passed:

```bash
python -m pytest tests/test_phase92t_runtime_finding_evidence_packager.py -q
# 2 passed
```

```bash
python -m pytest tests/test_phase92u_runtime_finding_customer_triage.py -q
# 2 passed
```

```bash
python -m pytest tests/test_phase92v_runtime_customer_report_builder.py -q
# 1 passed
```

```bash
python -m pytest tests/test_phase92w_runtime_reproduction_asset_linker.py -q
# 1 passed
```

```bash
python -m pytest tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py -q
# 30 passed
```

```bash
python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py -q
# 40 passed
```

```bash
python -m compileall -q ai_test_asset_center tests/test_phase92t_runtime_finding_evidence_packager.py tests/test_phase92u_runtime_finding_customer_triage.py tests/test_phase92v_runtime_customer_report_builder.py tests/test_phase92w_runtime_reproduction_asset_linker.py
# passed
```

Full `python -m pytest -q` was started and progressed with passing tests, but timed out within the current execution window before completion; no failure output was observed before timeout.
