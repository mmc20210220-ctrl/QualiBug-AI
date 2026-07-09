# Discovery Pipeline — Core Intelligence

The discovery pipeline is the heart of QualiBug AI. It follows a **Reader → Reasoner → Executor → Verifier** loop pattern.

## Layer Architecture

### 1. V12 Pipeline (`v12_pipeline.py`)
Top-level orchestration with **Campaign governance**:
- Bind project scope + environment reference + source snapshot hash
- Time-bounded execution approval before live target access
- HAR recording and source snapshotting
- Planning without target; execution requires source+approval

### 2. Discovery Engine (`discovery_engine.py`)
Autonomous discovery loop:
- **Reader**: Extract entities, API endpoints, and business rules from PRD+OpenAPI
- **Reasoner**: Generate defect hypotheses using LLM reasoning
- **Executor**: Execute probes against live target via `grounded_probe_executor.py`
- **Verifier**: Confirm or falsify hypotheses based on evidence
- Config guard: `timeout_seconds ≥ 300`, `max_tokens ≥ 32768`

### 3. Multi-Stage Reasoning (`stage_reason_all_v2.py`)
Chains analysis stages with `MAX_HYPOTHESES=15` per engine and `max_workers=4` parallel.

### 4. Self-Improving Loop (`self_improving_loop.py` / `autonomous_evolution_orchestrator.py`)
Champion/challenger pattern with formal state machine:
PLANNED → COLLECTING_SIGNALS → DIAGNOSING → CANDIDATE_GENERATED → VALIDATING → REPLAY_EVALUATING → SHADOW_EVALUATING → COMPARING → PROMOTED / ROLLED_BACK

### 5. Grounded Probe Executor (`grounded_probe_executor.py`)
6,582 lines — the largest execution module. Executes HTTP probes against live targets with evidence collection, browser automation (Playwright), and database snapshot comparison.
