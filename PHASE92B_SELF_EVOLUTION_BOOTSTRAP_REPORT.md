# Phase92B — Self-Evolution Bootstrap Runtime Patch

## Goal

Make the QualiBug bug discovery engine runnable in a controlled local environment before continuing deeper engine optimization.

## What changed

1. Added a deterministic local bootstrap reasoner:
   - File: `ai_test_asset_center/local_reasoner_bootstrap.py`
   - Generates read-only GET hypotheses from the PRD/API contract when live LLM reasoners are unavailable.
   - Does not read hidden oracle catalogs or known-bug seed files.
   - Does not perform write probes.

2. Added a fast local startup mode for the reasoner stage:
   - Env: `QUALIBUG_LOCAL_BOOTSTRAP_ONLY=1`
   - Keeps the self-evolution loop runnable without waiting for external LLM calls.

3. Added public CLI command:

```bash
python -m aitestops.cli self-evolve --project real_project_demo --local-bootstrap-only --out platform_outputs/real_project_demo/evolution_orchestrated_report.json
```

4. Fixed false failure in the self-improving loop:
   - If first-pass discovery produced findings and second-pass local bootstrap has no non-duplicate hypotheses, the loop treats that as local exhaustion/convergence, not as provider outage.

5. Added evidence-gate-gap learning signal:
   - Detects when runtime evidence is semantically confirmed but customer-visible validation remains blocked by evidence gate requirements.
   - Generates a candidate policy that adds:
     - `auth_boundary_matrix`
     - `response_sensitivity`
     - `reproduction_trace`
   - Does not relax the verifier or evidence gate.

## Runtime result in this environment

Local MES BugLab target health returned OK.

Self-evolution run completed with:

```json
{
  "terminal": "STUCK",
  "rounds": 1,
  "raw_confirmed_signals": 13,
  "needs_more_evidence": 13,
  "validated_candidates": 0,
  "total_improvements": 1
}
```

This is a good commercial-quality behavior: the engine found 13 runtime-confirmed permission/auth boundary signals, but did not promote them to customer-visible bugs until stronger business evidence is attached.

## Evolution signal generated

```json
{
  "type": "evidence_gate_gap",
  "severity": "high",
  "detail": "13 runtime-confirmed signals are blocked at needs_more_evidence"
}
```

## Candidate policy generated

The candidate policy adds stricter evidence packaging for auth-boundary findings while preserving the anti-cheat rule: no automatic promotion without paired replay/shadow evidence.

## Verification

Targeted tests passed:

```text
40 passed
```

Suites run:

- `tests/test_loop_runtime_supervisor.py`
- `tests/test_phase92a_evidence_bridge.py`
- `tests/test_discovery_engine_verifier_quality.py`
- `tests/test_production_safety_gate.py`

## Next optimization target

Implement the auth-boundary evidence bridge so runtime-confirmed permission findings can become `validated_candidate` only when they include:

- exact request/response refs for admin/viewer/no-auth
- role matrix
- sensitive-field classification
- reproduction command or script
- reviewer-ready impact explanation
