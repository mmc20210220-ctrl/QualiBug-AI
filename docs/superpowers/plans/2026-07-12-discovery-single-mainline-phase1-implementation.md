# Discovery Single-Mainline Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Do not use
> subagents unless the user later grants explicit permission. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Replace mixed legacy/Experiment runtime authority with one pre-run
selected, obligation-traceable discovery mainline while preserving external
quality and making every selected obligation terminal and observable.

**Architecture:** Extend existing Behavior IR, Test Obligation, Experiment,
governed executor, Delivery Gate, quality projection, and evaluator contracts.
Select exactly one authority before each run, isolate shadow execution in a
separate run envelope, and derive completion, health, trace, and formal scopes
from obligation-attempt receipts. Keep `run_v12_pipeline` as a compatibility
wrapper around a focused coordinator until legacy execution is retired.

**Tech Stack:** Python 3, pytest, dataclasses, existing QualiBug policy registry,
Behavior IR, governed sandbox executor, customer-delivery gate, trace ledger,
external evaluator CLI, PowerShell on Windows.

## Global Constraints

- QualiBug frontend port is `5174`; backend port is `8088`.
- `discovery_engine.py` timeout must remain `>= 300` seconds.
- `discovery_engine.py` and execution policy `max_tokens` must remain `>= 32768`.
- `stage_reason_all_v2.py::MAX_HYPOTHESES` remains `15`.
- default reasoner `max_workers` remains `4`.
- Production writes are forbidden. Unknown or undeclared environment types
  fail closed for writes.
- Every actual HTTP write uses the governed sandbox executor and emits one audit
  receipt. Partial accepted setup is compensated in reverse order without
  retrying the whole scenario.
- Hidden GT, evaluator match data, miss labels, benchmark answers, source code,
  and scorer rules never enter runtime prompts, policies, traces, fixtures, or
  product output.
- No benchmark endpoint, Bug ID, title, keyword, reproduction answer, customer
  name, or industry-specific business rule may be hard-coded into reusable
  discovery behavior.
- Only externally evaluated, actually executed findings may count as TP/Recall.
  Constructed unit-test data never counts as a discovered Bug.
- Current dirty worktree changes belong to the user. Do not overwrite, reset,
  delete, or silently include unrelated files in commits.
- After every Python edit, immediately run:

```powershell
python -c "import ast; ast.parse(open('path/to/file.py', encoding='utf-8').read()); print('OK')"
```

- Run focused tests after each change and commit only the files owned by the
  current task.
- Commercial Gate D, controlled-pilot, and GA thresholds remain solely in
  `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`.

---

### Task 1: Freeze Mainline Authority in Policy and Run Identity

**Files:**
- Create: `ai_test_asset_center/discovery_mainline_contract.py`
- Modify: `ai_test_asset_center/policy_registry.py:156-253`
- Modify: `ai_test_asset_center/discovery_policy_evaluation_runner.py:255-540`
- Test: `tests/test_discovery_mainline_authority.py`
- Test: `tests/test_phase109_behavior_slice_policy_registry.py`
- Test: `tests/test_discovery_policy_evaluation_runner.py`

**Interfaces:**
- Produces: `MainlineRunContract`, `build_mainline_run_contract`,
  `validate_mainline_run_contract`, and the policy field
  `ExecutionPolicy.mainline_authority`.
- Consumes: immutable run, campaign, target, environment, policy, and evaluation
  identities already supplied by scan/evaluation callers.

- [ ] **Step 1: Write failing authority-contract tests**

```python
def test_mainline_contract_requires_explicit_valid_authority() -> None:
    with pytest.raises(MainlineContractError, match="mainline_authority_missing"):
        build_mainline_run_contract(
            mainline_authority="",
            run_id="RUN-1",
            campaign_id="CMP-1",
            target_id="TARGET-1",
            environment_id="ENV-1",
            policy_version="v1",
            evaluation_mode="replay",
        )


def test_shadow_contract_suppresses_customer_and_evaluator_scopes() -> None:
    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-1",
        campaign_id="CMP-1",
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v2",
        evaluation_mode="shadow",
    )
    assert contract["customer_outputs_published"] is False
    assert contract["product_evaluation_submission_published"] is False
    assert contract["private_evaluator_observation_allowed"] is True
```

- [ ] **Step 2: Run tests and verify the contract is absent**

Run:

```powershell
pytest tests/test_discovery_mainline_authority.py -q
```

Expected: collection/import failure because
`ai_test_asset_center.discovery_mainline_contract` does not exist.

- [ ] **Step 3: Implement the immutable contract and policy field**

```python
import hashlib
import json
from typing import Any, TypedDict


MAINLINE_RUN_SCHEMA = "qualibug.discovery-mainline-run.v1"
MAINLINE_AUTHORITIES = frozenset({"legacy_champion", "experiment_candidate"})
EVALUATION_MODES = frozenset({"operational", "replay", "shadow"})


class MainlineContractError(ValueError):
    pass


class MainlineRunContract(TypedDict):
    schema_version: str
    mainline_authority: str
    run_id: str
    campaign_id: str
    target_id: str
    environment_id: str
    policy_version: str
    evaluation_mode: str
    customer_outputs_published: bool
    product_evaluation_submission_published: bool
    private_evaluator_observation_allowed: bool
    contract_fingerprint: str


def build_mainline_run_contract(*, mainline_authority: str, run_id: str,
                                campaign_id: str, target_id: str,
                                environment_id: str, policy_version: str,
                                evaluation_mode: str) -> MainlineRunContract:
    required = {
        "mainline_authority": mainline_authority,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "target_id": target_id,
        "environment_id": environment_id,
        "policy_version": policy_version,
        "evaluation_mode": evaluation_mode,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise MainlineContractError(f"{missing[0]}_missing")
    if mainline_authority not in MAINLINE_AUTHORITIES:
        raise MainlineContractError(f"mainline_authority_invalid:{mainline_authority}")
    if evaluation_mode not in EVALUATION_MODES:
        raise MainlineContractError(f"evaluation_mode_invalid:{evaluation_mode}")
    shadow = evaluation_mode == "shadow"
    contract: dict[str, Any] = {
        "schema_version": MAINLINE_RUN_SCHEMA,
        **required,
        "customer_outputs_published": not shadow,
        "product_evaluation_submission_published": not shadow,
        "private_evaluator_observation_allowed": evaluation_mode in {"replay", "shadow"},
    }
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"))
    contract["contract_fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return contract


def validate_mainline_run_contract(value: dict[str, Any]) -> MainlineRunContract:
    expected = build_mainline_run_contract(
        mainline_authority=str(value.get("mainline_authority") or ""),
        run_id=str(value.get("run_id") or ""),
        campaign_id=str(value.get("campaign_id") or ""),
        target_id=str(value.get("target_id") or ""),
        environment_id=str(value.get("environment_id") or ""),
        policy_version=str(value.get("policy_version") or ""),
        evaluation_mode=str(value.get("evaluation_mode") or ""),
    )
    if value.get("contract_fingerprint") != expected["contract_fingerprint"]:
        raise MainlineContractError("mainline_contract_fingerprint_mismatch")
    return expected
```

Add to `ExecutionPolicy`:

```python
mainline_authority: str = "legacy_champion"
```

Validate without coercing invalid values:

```python
if self.mainline_authority not in {"legacy_champion", "experiment_candidate"}:
    raise ValueError(f"invalid mainline_authority: {self.mainline_authority}")
```

- [ ] **Step 4: Bind champion/challenger evaluation roles before scan**

In `DiscoveryPolicyEvaluationRunner`, copy the selected policy's
`strategy.execution.mainline_authority` into the observed scan request and
assert that the returned run contract matches it. A shadow evaluation remains
a separate run whose output declares `customer_outputs_published=False`.

- [ ] **Step 5: Syntax-check and run focused tests**

Run:

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/discovery_mainline_contract.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/policy_registry.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/discovery_policy_evaluation_runner.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_discovery_mainline_authority.py tests/test_phase109_behavior_slice_policy_registry.py tests/test_discovery_policy_evaluation_runner.py -q
```

Expected: syntax `OK`; all selected tests pass. The evaluator-owned runner may
consume the private shadow scope, while product evaluation-submission APIs may
not publish it.

- [ ] **Step 6: Commit Task 1 only**

```powershell
git add -- ai_test_asset_center/discovery_mainline_contract.py ai_test_asset_center/policy_registry.py ai_test_asset_center/discovery_policy_evaluation_runner.py tests/test_discovery_mainline_authority.py tests/test_phase109_behavior_slice_policy_registry.py tests/test_discovery_policy_evaluation_runner.py
git commit -m "feat: freeze discovery mainline authority"
```

### Task 2: Emit Behavior IR v2 and Compile Through Explicit Relations

**Files:**
- Modify: `ai_test_asset_center/behavior_ir.py`
- Modify: `ai_test_asset_center/obligation_compiler.py`
- Modify: `ai_test_asset_center/test_obligation.py`
- Test: `tests/test_behavior_ir_obligation_experiment.py`
- Test: `tests/test_v12_behavior_ir_vertical_slice.py`

**Interfaces:**
- Produces: runtime `qualibug.behavior-ir.v2`,
  `migrate_behavior_ir_v1_to_v2(value)`, and relation-driven obligations.
- Consumes: source-grounded IR nodes and relation records only.

- [ ] **Step 1: Add failing V2 and relation-join tests**

```python
def test_runtime_behavior_ir_emits_v2_relations() -> None:
    ir = empty_behavior_ir(project_id="PROJECT-1", source_snapshot_hash="source-sha")
    assert ir["schema_version"] == "qualibug.behavior-ir.v2"
    assert all(row["relation_type"] in ALLOWED_RELATION_TYPES for row in ir["relations"])


def test_obligation_compiler_does_not_bind_first_write_operation() -> None:
    ir = behavior_ir_with_two_writes_and_one_explicit_transition_relation()
    compiled = compile_obligations_from_behavior_ir(ir)
    state = next(row for row in compiled["obligations"] if row["risk_family"] == "state")
    assert state["property"]["operation_ref"] == "op-explicit-transition"
```

- [ ] **Step 2: Verify tests expose V1 and positional binding**

Run:

```powershell
pytest tests/test_behavior_ir_obligation_experiment.py -q
```

Expected: failures showing schema v1 and an operation selected without the
required explicit relation.

- [ ] **Step 3: Implement V2 relations and explicit migration**

```python
from copy import deepcopy


SCHEMA_VERSION = "qualibug.behavior-ir.v2"
V1_SCHEMA_VERSION = "qualibug.behavior-ir.v1"
ALLOWED_RELATION_TYPES = frozenset({
    "produces", "consumes", "transitions", "permits", "denies",
    "owns", "scopes", "conserves", "observes", "compensates",
})


class BehaviorIRError(ValueError):
    pass


def normalize_relation(value: dict[str, Any]) -> dict[str, Any]:
    relation_type = _text(value.get("relation_type"))
    if relation_type not in ALLOWED_RELATION_TYPES:
        raise BehaviorIRError(f"relation_type_invalid:{relation_type}")
    required = ("id", "from_ref", "to_ref")
    missing = [key for key in required if not _text(value.get(key))]
    if missing:
        raise BehaviorIRError(f"relation_field_missing:{missing[0]}")
    return {
        **value,
        "relation_type": relation_type,
        "operation_ref": _text(value.get("operation_ref")),
        "actor_ref": _text(value.get("actor_ref")),
        "preconditions": list(value.get("preconditions") or []),
        "effects": list(value.get("effects") or []),
        "source_refs": list(value.get("source_refs") or []),
    }


def migrate_behavior_ir_v1_to_v2(value: dict[str, Any]) -> dict[str, Any]:
    if _text(value.get("schema_version")) != V1_SCHEMA_VERSION:
        raise BehaviorIRError("behavior_ir_v1_required")
    migrated = deepcopy(value)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["relations"] = [normalize_relation(row) for row in value.get("relations", [])]
    errors = validate_behavior_ir(migrated, require_explicit_relations=True)
    if errors:
        raise BehaviorIRError("behavior_ir_v2_invalid:" + ",".join(errors))
    return migrated
```

Runtime builders emit V2 directly. Public compiler entry points reject V1 with
`BehaviorIRError`; persisted diagnostic migration must call the migration
function explicitly.

- [ ] **Step 4: Replace positional operation selection with relation joins**

Remove `write_ops[0]`, `operations[0]`, actor-array ordering, and implicit
state-array ordering from obligation compilation. Use helpers such as:

```python
def related_operations(ir: dict[str, Any], *, node_ref: str,
                       relation_types: set[str]) -> list[dict[str, Any]]:
    operation_ids = {
        row["operation_ref"]
        for row in ir["relations"]
        if row["relation_type"] in relation_types
        and node_ref in {row["from_ref"], row["to_ref"]}
        and row.get("operation_ref")
    }
    return [row for row in ir["operations"] if row.get("id") in operation_ids]
```

Missing and ambiguous joins become coverage gaps and compile blockers; they do
not create a semantically different obligation.

- [ ] **Step 5: Syntax-check and run focused tests**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/behavior_ir.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/obligation_compiler.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/test_obligation.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_behavior_ir_obligation_experiment.py tests/test_v12_behavior_ir_vertical_slice.py -q
```

- [ ] **Step 6: Commit Task 2 only**

```powershell
git add -- ai_test_asset_center/behavior_ir.py ai_test_asset_center/obligation_compiler.py ai_test_asset_center/test_obligation.py tests/test_behavior_ir_obligation_experiment.py tests/test_v12_behavior_ir_vertical_slice.py
git commit -m "refactor: compile obligations from behavior relations"
```

### Task 3: Convert Legacy Inputs Through a Pure Obligation Adapter

**Files:**
- Create: `ai_test_asset_center/obligation_source_adapter.py`
- Modify: `ai_test_asset_center/hypothesis_slice_bridge.py`
- Modify: `ai_test_asset_center/obligation_compiler.py`
- Test: `tests/test_mainline_unification_bridge.py`
- Test: `tests/test_behavior_ir_obligation_experiment.py`

**Interfaces:**
- Produces: `adapt_source_candidates_to_obligations(candidates, behavior_ir)`.
- Consumes: legacy slices or LLM hypotheses only as source candidates; exact
  operation and actor references must resolve through Behavior IR.

- [ ] **Step 1: Write failing pure-adapter tests**

```python
def test_adapter_preserves_intent_without_execution_authority() -> None:
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [{"id": "op-create-resource", "method": "POST", "path": "/resources"}],
        "actors": [],
        "entities": [],
        "states": [],
        "invariants": [],
        "relations": [{
            "id": "rel-1",
            "relation_type": "produces",
            "from_ref": "op-create-resource",
            "to_ref": "entity-resource",
            "operation_ref": "op-create-resource",
            "source_refs": [{"source_id": "SRC-1"}],
        }],
    }
    candidate = {
        "candidate_id": "cand-1",
        "risk_family": "idempotency",
        "method": "POST",
        "path": "/resources",
        "source_refs": [{"source_id": "SRC-1"}],
        "property": {"template": "idempotent_effect_cardinality"},
    }
    result = adapt_source_candidates_to_obligations([candidate], ir)
    obligation = result["obligations"][0]
    assert obligation["source_refs"]
    assert obligation["required_operations"] == ["op-create-resource"]
    serialized = json.dumps(result)
    assert "send_request" not in serialized
    assert "gate_passed" not in serialized


def test_adapter_blocks_candidate_without_exact_ir_join() -> None:
    ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [{"id": "op-list-resource", "method": "GET", "path": "/resources"}],
        "actors": [], "entities": [], "states": [], "invariants": [], "relations": [],
    }
    candidate = {
        "candidate_id": "cand-unbound",
        "risk_family": "state",
        "method": "POST",
        "path": "/unknown",
        "source_refs": [{"source_id": "SRC-1"}],
    }
    result = adapt_source_candidates_to_obligations([candidate], ir)
    assert result["obligations"] == []
    assert result["coverage_gaps"][0]["code"] == "BLOCKED_MISSING_IR_RELATION"
```

- [ ] **Step 2: Run tests and verify adapter is absent**

```powershell
pytest tests/test_mainline_unification_bridge.py -q
```

- [ ] **Step 3: Implement source-only adaptation**

The adapter may normalize method/path/operation hints and lineage. It calls
`make_obligation` only after an exact IR relation join. Return:

```python
{
    "schema_version": "qualibug.obligation-source-adapter.v1",
    "input_count": len(candidates),
    "obligations": dedupe_obligations(obligations),
    "coverage_gaps": coverage_gaps,
}
```

Do not import the executor, Oracle engine, customer-delivery gate, or
persistence modules into this adapter.

- [ ] **Step 4: Make the existing bridge expose candidates, not execute them**

Keep compatibility fields used by current callers, but route the candidate
output into the adapter. Add a dependency test that rejects forbidden imports
and a runtime test proving no adapter call can issue HTTP traffic.

- [ ] **Step 5: Syntax-check and test**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/obligation_source_adapter.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/hypothesis_slice_bridge.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/obligation_compiler.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_mainline_unification_bridge.py tests/test_behavior_ir_obligation_experiment.py -q
```

- [ ] **Step 6: Commit Task 3 only**

```powershell
git add -- ai_test_asset_center/obligation_source_adapter.py ai_test_asset_center/hypothesis_slice_bridge.py ai_test_asset_center/obligation_compiler.py tests/test_mainline_unification_bridge.py tests/test_behavior_ir_obligation_experiment.py
git commit -m "refactor: adapt legacy discovery inputs to obligations"
```

### Task 4: Establish Campaign Before Planning and Add the Focused Coordinator

**Files:**
- Create: `ai_test_asset_center/discovery_mainline.py`
- Modify: `ai_test_asset_center/v12_pipeline.py:2635-4055`
- Modify: `ai_test_asset_center/enterprise_campaign.py`
- Test: `tests/test_discovery_mainline_coordinator.py`
- Test: `tests/test_phase110_enterprise_campaign.py`
- Test: `tests/test_phase109_incremental_behavior_slices.py`

**Interfaces:**
- Produces: `DiscoveryMainlineInputs`, `DiscoveryPlanningBundle`, and
  `run_discovery_mainline(inputs, legacy_runner, experiment_runner)`.
- Consumes: the immutable mainline contract from Task 1.

- [ ] **Step 1: Write failing ordering and single-runner tests**

```python
def test_campaign_identity_exists_before_behavior_ir_and_execution() -> None:
    events: list[str] = []
    contract = build_mainline_run_contract(
        mainline_authority="legacy_champion",
        run_id="RUN-1",
        campaign_id="CMP-1",
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v1",
        evaluation_mode="replay",
    )
    result = run_discovery_mainline(
        DiscoveryMainlineInputs(
            project="PROJECT-1",
            root=Path("."),
            prd_text="requirement",
            api_spec_text="GET /resources",
            db_schema_text="",
            approved_base_url="http://127.0.0.1:8080",
            campaign_context={"mainline_authority": "legacy_champion"},
        ),
        build_campaign=lambda _: events.append("campaign") or SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: events.append("plan") or SimpleNamespace(mainline_run=contract),
        legacy_runner=lambda *_: events.append("legacy") or {"mainline_run": contract},
        experiment_runner=lambda *_: events.append("experiment") or {"mainline_run": contract},
    )
    assert events[:2] == ["campaign", "plan"]
    assert result["mainline_run"]["campaign_id"] == "CMP-1"


def test_one_run_never_invokes_both_runners() -> None:
    calls = {"legacy": 0, "experiment": 0}
    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-1",
        campaign_id="CMP-1",
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v2",
        evaluation_mode="replay",
    )
    run_discovery_mainline(
        DiscoveryMainlineInputs(
            project="PROJECT-1",
            root=Path("."),
            prd_text="requirement",
            api_spec_text="GET /resources",
            db_schema_text="",
            approved_base_url="http://127.0.0.1:8080",
            campaign_context={"mainline_authority": "experiment_candidate"},
        ),
        build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
        legacy_runner=lambda *_: calls.__setitem__("legacy", calls["legacy"] + 1) or {"mainline_run": contract},
        experiment_runner=lambda *_: calls.__setitem__("experiment", calls["experiment"] + 1) or {"mainline_run": contract},
    )
    assert calls == {"legacy": 0, "experiment": 1}
```

- [ ] **Step 2: Verify current V12 backfills campaign identity**

```powershell
pytest tests/test_discovery_mainline_coordinator.py -q
```

Expected: failures because the coordinator is absent and V12 currently executes
Experiments before calling `_campaign_context`.

- [ ] **Step 3: Implement coordinator contracts**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class DiscoveryMainlineInputs:
    project: str
    root: Path
    prd_text: str
    api_spec_text: str
    db_schema_text: str
    approved_base_url: str
    campaign_context: dict[str, Any]
    existing_findings: Sequence[dict[str, Any]] = ()


@dataclass(frozen=True)
class DiscoveryPlanningBundle:
    mainline_run: MainlineRunContract
    behavior_ir: dict[str, Any]
    obligations: dict[str, Any]
    experiments: dict[str, Any]


def run_discovery_mainline(inputs: DiscoveryMainlineInputs, *, build_campaign,
                           build_plan, legacy_runner, experiment_runner) -> dict[str, Any]:
    campaign = build_campaign(inputs)
    plan = build_plan(inputs, campaign)
    authority = plan.mainline_run["mainline_authority"]
    runner = legacy_runner if authority == "legacy_champion" else experiment_runner
    result = runner(inputs, campaign, plan)
    assert_result_matches_authority(result, plan.mainline_run)
    return result


def assert_result_matches_authority(result: dict[str, Any], contract: MainlineRunContract) -> None:
    observed = validate_mainline_run_contract(dict(result.get("mainline_run") or {}))
    if observed["contract_fingerprint"] != contract["contract_fingerprint"]:
        raise MainlineContractError("mainline_result_authority_mismatch")
```

- [ ] **Step 4: Move campaign construction before Behavior IR and execution**

Refactor `run_v12_pipeline` so it constructs immutable runtime/target/campaign
identity before `build_behavior_ir`, `compile_obligations_from_behavior_ir`, or
`execute_selected_experiments`. Delete post-execution `campaign_id` backfilling.
The compatibility wrapper builds `DiscoveryMainlineInputs` and delegates once.

- [ ] **Step 5: Keep campaign identity separate from completion**

Retain `EnterpriseCampaign` for identity and persistence. Stop using
`record_cycle` as the candidate mainline completion source; Task 5 supplies
terminal-attempt completion. During migration, legacy campaign fields remain a
labelled compatibility projection only.

- [ ] **Step 6: Syntax-check and run focused tests**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/discovery_mainline.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/v12_pipeline.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/enterprise_campaign.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_discovery_mainline_coordinator.py tests/test_phase110_enterprise_campaign.py tests/test_phase109_incremental_behavior_slices.py -q
```

- [ ] **Step 7: Commit Task 4 only**

```powershell
git add -- ai_test_asset_center/discovery_mainline.py ai_test_asset_center/v12_pipeline.py ai_test_asset_center/enterprise_campaign.py tests/test_discovery_mainline_coordinator.py tests/test_phase110_enterprise_campaign.py tests/test_phase109_incremental_behavior_slices.py
git commit -m "refactor: coordinate discovery through one authority"
```

### Task 5: Make Obligation Attempts the Terminal Execution Ledger

**Files:**
- Create: `ai_test_asset_center/obligation_attempt_ledger.py`
- Modify: `ai_test_asset_center/experiment_executor.py`
- Modify: `ai_test_asset_center/customer_delivery_gate.py`
- Modify: `ai_test_asset_center/enterprise_campaign.py`
- Test: `tests/test_obligation_attempt_ledger.py`
- Test: `tests/test_experiment_contract_execution.py`
- Test: `tests/test_customer_delivery_cleanup_gate.py`

**Interfaces:**
- Produces: `qualibug.obligation-attempt-ledger.v1`,
  `build_obligation_attempt_ledger`, and
  `derive_campaign_terminal_status`.
- Consumes: selected obligation IDs, compile receipts, execution receipts,
  typed observation/Oracle receipts, gate results, and cleanup receipts.

- [ ] **Step 1: Write failing ledger invariants**

```python
def test_every_selected_obligation_has_one_terminal_attempt() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-1", "campaign_id": "CMP-1"},
        selected=[{"obligation_id": "obl-1"}, {"obligation_id": "obl-2"}],
        compile_results={
            "obl-1": {"status": "COMPILED", "experiment_id": "exp-1"},
            "obl-2": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"},
        },
        execution_results={
            "obl-1": {"status": "EXECUTED", "execution_id": "exec-1"},
        },
        gate_results={
            "obl-1": {"status": "REJECTED", "reason_code": "ORACLE_NOT_VIOLATED"},
        },
    )
    assert ledger["selected_count"] == 2
    assert ledger["terminal_count"] == 2
    assert ledger["complete"] is True


def test_duplicate_or_missing_terminal_receipt_fails_fast() -> None:
    with pytest.raises(ObligationAttemptLedgerError, match="duplicate_terminal_receipt"):
        build_obligation_attempt_ledger(
            mainline_run={"run_id": "RUN-1", "campaign_id": "CMP-1"},
            selected=[{"obligation_id": "obl-1"}],
            compile_results={"obl-1": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"}},
            execution_results={},
            gate_results={"obl-1": {"status": "REJECTED", "reason_code": "ORACLE_NOT_VIOLATED"}},
        )


def test_executed_without_violation_is_rejected_not_disappeared() -> None:
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "RUN-1", "campaign_id": "CMP-1"},
        selected=[{"obligation_id": "obl-1"}],
        compile_results={"obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}},
        execution_results={"obl-1": {"status": "EXECUTED", "execution_id": "exec-1"}},
        gate_results={"obl-1": {"status": "REJECTED", "reason_code": "ORACLE_NOT_VIOLATED"}},
    )
    assert ledger["attempts"][0]["terminal_status"] == "REJECTED"
    assert ledger["attempts"][0]["reason_code"] == "ORACLE_NOT_VIOLATED"
```

- [ ] **Step 2: Run tests and verify selected work currently disappears**

```powershell
pytest tests/test_obligation_attempt_ledger.py tests/test_experiment_contract_execution.py -q
```

- [ ] **Step 3: Implement immutable attempt joining**

```python
TERMINAL_STATUSES = frozenset({
    "DELIVERABLE", "REJECTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED",
})


def build_obligation_attempt_ledger(*, mainline_run: dict[str, Any],
                                    selected: list[dict[str, Any]],
                                    compile_results: dict[str, Any],
                                    execution_results: dict[str, Any],
                                    gate_results: dict[str, Any]) -> dict[str, Any]:
    selected_ids = [str(row.get("obligation_id") or "") for row in selected]
    if not all(selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise ObligationAttemptLedgerError("selected_obligation_identity_invalid")
    attempts = []
    for obligation_id in selected_ids:
        compile_receipt = dict(compile_results.get(obligation_id) or {})
        execution_receipt = dict(execution_results.get(obligation_id) or {})
        gate_receipt = dict(gate_results.get(obligation_id) or {})
        terminals = [
            row for row in (compile_receipt, execution_receipt, gate_receipt)
            if str(row.get("status") or "") in TERMINAL_STATUSES
        ]
        if len(terminals) != 1:
            code = "terminal_receipt_missing" if not terminals else "duplicate_terminal_receipt"
            raise ObligationAttemptLedgerError(f"{code}:{obligation_id}")
        terminal = terminals[0]
        attempts.append({
            "obligation_id": obligation_id,
            "experiment_id": str(compile_receipt.get("experiment_id") or ""),
            "execution_id": str(execution_receipt.get("execution_id") or ""),
            "terminal_status": terminal["status"],
            "reason_code": str(terminal.get("reason_code") or ""),
        })
    return {
        "schema_version": "qualibug.obligation-attempt-ledger.v1",
        "run_id": str(mainline_run["run_id"]),
        "campaign_id": str(mainline_run["campaign_id"]),
        "selected_count": len(selected_ids),
        "terminal_count": len(attempts),
        "complete": len(attempts) == len(selected_ids),
        "attempts": attempts,
    }
```

Every attempt contains continuity IDs, stage records, reason code, input/output
fingerprints, receipt references, elapsed time, and cost coverage status. It
references existing receipts instead of copying raw request/response payloads.

- [ ] **Step 4: Derive campaign completion only from attempts**

```python
def derive_campaign_terminal_status(ledger: dict[str, Any]) -> str:
    if ledger["selected_count"] != ledger["terminal_count"]:
        return "active"
    if any(row["terminal_status"] == "HARNESS_FAILED" for row in ledger["attempts"]):
        return "degraded"
    return "completed"
```

Remove candidate completion based on positive traffic, attempted slice IDs, or
`real_trace_count > 0`.

- [ ] **Step 5: Syntax-check and test**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/obligation_attempt_ledger.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/experiment_executor.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/customer_delivery_gate.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/enterprise_campaign.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_obligation_attempt_ledger.py tests/test_experiment_contract_execution.py tests/test_customer_delivery_cleanup_gate.py -q
```

- [ ] **Step 6: Commit Task 5 only**

```powershell
git add -- ai_test_asset_center/obligation_attempt_ledger.py ai_test_asset_center/experiment_executor.py ai_test_asset_center/customer_delivery_gate.py ai_test_asset_center/enterprise_campaign.py tests/test_obligation_attempt_ledger.py tests/test_experiment_contract_execution.py tests/test_customer_delivery_cleanup_gate.py
git commit -m "feat: account for every obligation terminal outcome"
```

### Task 6: Enforce Authority-Scoped Delivery and Formal Projection

**Files:**
- Modify: `ai_test_asset_center/discovery_quality_projection.py`
- Modify: `ai_test_asset_center/campaign_api_contract.py`
- Modify: `ai_test_asset_center/__main__.py:4625-4676`
- Test: `tests/test_discovery_mainline_authority.py`
- Test: `tests/test_scan_counter_unification.py`
- Test: `tests/test_gate_d_campaign_contracts.py`

**Interfaces:**
- Produces: exact authority-scoped
  `deliverable|candidate|rejected|shadow` classification and one formal ID set.
- Consumes: `mainline_run` and obligation-attempt ledger from prior tasks.

- [ ] **Step 1: Write failing shadow/formal-scope tests**

```python
def test_shadow_findings_cannot_enter_formal_projection() -> None:
    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="RUN-1",
        campaign_id="CMP-1",
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v2",
        evaluation_mode="shadow",
    )
    projected = attach_quality_projection_to_scan_result({
        "mainline_run": contract,
        "findings": [{
            "id": "FINDING-1",
            "finding_id": "FINDING-1",
            "gate_passed": True,
            "execution_status": "executed",
            "confirmation_status": "confirmed",
            "customer_delivery_status": "defect",
            "bug_status": "reproduced",
            "mainline_run": {"contract_fingerprint": contract["contract_fingerprint"]},
        }],
    })
    assert projected["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert projected["finding_classification"]["shadow"]
    assert projected["external_evaluation"]["commercial_promotion_evidence"] is False


def test_formal_ids_match_gate_submission_trace_and_api() -> None:
    receipt = build_formal_id_consistency(
        delivery_gate_ids=["FINDING-1"],
        formal_projection_ids=["FINDING-1"],
        evaluator_submission_ids=["FINDING-1"],
        trace_ledger_ids=["FINDING-1"],
        product_projection_ids=["FINDING-1"],
    )
    assert receipt["consistent"] is True
```

- [ ] **Step 2: Run tests and expose current mixed scope**

```powershell
pytest tests/test_discovery_mainline_authority.py tests/test_scan_counter_unification.py tests/test_gate_d_campaign_contracts.py -q
```

- [ ] **Step 3: Add authority filtering before quality projection**

Create one helper:

```python
def authority_scoped_findings(scan_result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    contract = validate_mainline_run_contract(_dict(scan_result.get("mainline_run")))
    if not contract["customer_outputs_published"]:
        return {"authoritative": [], "shadow": _list(scan_result.get("findings"))}
    authoritative = [
        row for row in _list(scan_result.get("findings"))
        if _dict(row.get("mainline_run")).get("contract_fingerprint")
        == contract["contract_fingerprint"]
    ]
    return {"authoritative": authoritative, "shadow": []}
```

Fail on missing/mismatched authority fingerprints; never treat them as legacy
success.

- [ ] **Step 4: Derive one formal ID consistency receipt**

Compare exact finding IDs from Delivery Gate output, formal projection,
evaluation submission, Trace Ledger, and campaign API projection. Any mismatch
emits `PIPELINE_DEGRADED_COUNT_MISMATCH` with the disagreeing sets.

```python
def build_formal_id_consistency(**scopes: list[str]) -> dict[str, Any]:
    normalized = {name: sorted(set(values)) for name, values in scopes.items()}
    unique_sets = {tuple(values) for values in normalized.values()}
    consistent = len(unique_sets) <= 1
    return {
        "schema_version": "qualibug.formal-id-consistency.v1",
        "consistent": consistent,
        "status": "OK" if consistent else "PIPELINE_DEGRADED_COUNT_MISMATCH",
        "scopes": normalized,
    }
```

- [ ] **Step 5: Syntax-check and test**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/discovery_quality_projection.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/campaign_api_contract.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/__main__.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_discovery_mainline_authority.py tests/test_scan_counter_unification.py tests/test_gate_d_campaign_contracts.py -q
```

- [ ] **Step 6: Commit Task 6 only**

```powershell
git add -- ai_test_asset_center/discovery_quality_projection.py ai_test_asset_center/campaign_api_contract.py ai_test_asset_center/__main__.py tests/test_discovery_mainline_authority.py tests/test_scan_counter_unification.py tests/test_gate_d_campaign_contracts.py
git commit -m "fix: isolate discovery formal scopes by authority"
```

### Task 7: Upgrade Trace Ledger to Obligation-Attempt V2

**Files:**
- Modify: `ai_test_asset_center/discovery_trace_ledger.py`
- Modify: `ai_test_asset_center/discovery_weakness_miner.py`
- Create: `tools/migrate_discovery_trace_ledger.py`
- Test: `tests/test_discovery_trace_weakness_mining.py`
- Test: `tests/test_pipeline_health_visibility.py`

**Interfaces:**
- Produces: runtime `qualibug.discovery-trace-ledger.v2` and explicit offline
  `migrate_trace_ledger_v1_to_v2`.
- Consumes: the obligation-attempt ledger and authority-scoped formal IDs.

- [ ] **Step 1: Write failing V2 trace tests**

```python
def test_trace_v2_has_one_row_per_obligation_attempt() -> None:
    ledger = build_discovery_trace_ledger_v2(
        {
            "obligation_attempt_ledger": {
                "schema_version": "qualibug.obligation-attempt-ledger.v1",
                "attempts": [
                    {"obligation_id": "obl-1", "terminal_status": "REJECTED"},
                    {"obligation_id": "obl-2", "terminal_status": "BLOCKED"},
                ],
            },
            "formal_count_projection": {"formal_finding_ids": []},
        },
        run_id="RUN-1",
        policy_id="POLICY-1",
        target_id="TARGET-1",
        project_id="PROJECT-1",
        industry="industry-a",
        evaluation_mode="replay",
    )
    assert ledger["schema_version"] == "qualibug.discovery-trace-ledger.v2"
    assert {row["obligation_id"] for row in ledger["attempts"]} == {"obl-1", "obl-2"}


def test_runtime_rejects_v1_without_explicit_migration() -> None:
    with pytest.raises(DiscoveryTraceError, match="trace_ledger_v2_required"):
        validate_trace_ledger({"schema_version": "qualibug.discovery-trace-ledger.v1", "traces": []})
```

- [ ] **Step 2: Verify current Trace Ledger is slice keyed**

```powershell
pytest tests/test_discovery_trace_weakness_mining.py -q
```

- [ ] **Step 3: Implement V2 as a join over attempt receipts**

The V2 ledger contains `attempts`, `formal_finding_ids`, stage-loss aggregates,
pipeline health, and redaction contract. `behavior_slice_id` is optional lineage
only. Raw bodies, credentials, target-private paths, and GT remain excluded.

- [ ] **Step 4: Implement explicit offline V1 migration**

```python
def migrate_trace_ledger_v1_to_v2(v1: dict[str, Any], *, obligation_map: dict[str, str]) -> dict[str, Any]:
    if v1.get("schema_version") != "qualibug.discovery-trace-ledger.v1":
        raise DiscoveryTraceError("trace_ledger_v1_required")
    missing = sorted({row["behavior_slice_id"] for row in v1["traces"]} - set(obligation_map))
    if missing:
        raise DiscoveryTraceError("v1_migration_obligation_map_incomplete:" + ",".join(missing))
    attempts = [
        {
            **row,
            "obligation_id": obligation_map[row["behavior_slice_id"]],
            "behavior_slice_id": row["behavior_slice_id"],
        }
        for row in v1["traces"]
    ]
    return {
        **{key: value for key, value in v1.items() if key not in {"schema_version", "traces"}},
        "schema_version": "qualibug.discovery-trace-ledger.v2",
        "attempts": attempts,
        "migration": {
            "source_schema": "qualibug.discovery-trace-ledger.v1",
            "explicit": True,
        },
    }
```

The CLI reads an operator-supplied mapping and writes a new immutable artifact;
runtime never invokes it automatically.

- [ ] **Step 5: Update weakness mining to consume V2 stage reasons**

Cluster on source, risk family, operation, adapter, compile reason, execution
reason, Oracle, gate reason, and terminal outcome. Do not cluster on titles or
GT labels.

- [ ] **Step 6: Syntax-check and test**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/discovery_trace_ledger.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/discovery_weakness_miner.py', encoding='utf-8').read()); ast.parse(open('tools/migrate_discovery_trace_ledger.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_discovery_trace_weakness_mining.py tests/test_pipeline_health_visibility.py -q
```

- [ ] **Step 7: Commit Task 7 only**

```powershell
git add -- ai_test_asset_center/discovery_trace_ledger.py ai_test_asset_center/discovery_weakness_miner.py tools/migrate_discovery_trace_ledger.py tests/test_discovery_trace_weakness_mining.py tests/test_pipeline_health_visibility.py
git commit -m "feat: trace discovery by obligation attempts"
```

### Task 8: Derive Funnel and Health Only From Attempts

**Files:**
- Modify: `ai_test_asset_center/discovery_funnel.py`
- Modify: `ai_test_asset_center/discovery_quality_projection.py`
- Modify: `ai_test_asset_center/__main__.py:4194-4255`
- Test: `tests/test_pipeline_health_visibility.py`
- Test: `tests/test_discovery_funnel_observability.py`
- Test: `tests/test_scan_counter_unification.py`

**Interfaces:**
- Produces: `effective_execution_status` from the authoritative attempt ledger
  only, followed by deletion of legacy reconciliation after callers migrate.
- Consumes: obligation-attempt ledger and Trace Ledger V2.

- [ ] **Step 1: Replace the reconciliation test with authority tests**

```python
def test_execution_status_requires_attempt_ledger() -> None:
    with pytest.raises(DiscoveryFunnelError, match="obligation_attempt_ledger_missing"):
        effective_execution_status({"phases": {"execution": {"status": "completed"}}})


def test_attempt_ledger_is_the_only_execution_status_source() -> None:
    result = {
        "obligation_attempt_ledger": {
            "schema_version": "qualibug.obligation-attempt-ledger.v1",
            "selected_count": 1,
            "terminal_count": 1,
            "complete": True,
            "attempts": [{"obligation_id": "obl-1", "terminal_status": "REJECTED"}],
        }
    }
    assert effective_execution_status(result) == "completed"
```

- [ ] **Step 2: Run tests and verify legacy fallback still wins**

```powershell
pytest tests/test_pipeline_health_visibility.py tests/test_discovery_funnel_observability.py -q
```

- [ ] **Step 3: Derive funnel stages from receipt records**

Use required stage names from the design. For every stage expose input, success,
blocked, failed, elapsed p50/p95, and reason distributions. The funnel cannot
mutate `validated_bug_count`; it reads formal projection instead.

- [ ] **Step 4: Remove scan-level behavior-slice multi-round authority**

`__main__.py` must call the coordinator once per run. Any next round is selected
inside the obligation planner and represented by attempt receipts. Delete
scan-level re-drive based on `effective_execution_status(v12)` and legacy Slice
ledger state.

- [ ] **Step 5: Syntax-check and test**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/discovery_funnel.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/discovery_quality_projection.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/__main__.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_pipeline_health_visibility.py tests/test_discovery_funnel_observability.py tests/test_scan_counter_unification.py -q
```

- [ ] **Step 6: Commit Task 8 only**

```powershell
git add -- ai_test_asset_center/discovery_funnel.py ai_test_asset_center/discovery_quality_projection.py ai_test_asset_center/__main__.py tests/test_pipeline_health_visibility.py tests/test_discovery_funnel_observability.py tests/test_scan_counter_unification.py
git commit -m "refactor: derive discovery health from attempt receipts"
```

### Task 9: Complete the Atomic Cutover and Thin V12 Wrapper

**Files:**
- Modify: `ai_test_asset_center/discovery_mainline.py`
- Modify: `ai_test_asset_center/v12_pipeline.py`
- Modify: `ai_test_asset_center/__main__.py`
- Modify: `ai_test_asset_center/continuous_evaluation.py`
- Modify: `ai_test_asset_center/private_pilot_service.py`
- Test: `tests/test_discovery_mainline_coordinator.py`
- Test: `tests/test_e2e_system_promise_closed_loop.py`
- Test: `tests/test_phase109_incremental_behavior_slices.py`
- Test: `tests/test_private_pilot_server_campaign_context_patch.py`

**Interfaces:**
- Produces: one production entry point backed by the coordinator and a
  compatibility `run_v12_pipeline` wrapper with no scheduling or formal-count
  authority.
- Consumes: Tasks 1-8 contracts.

- [ ] **Step 1: Add a failing no-dual-authority source test**

```python
def test_v12_wrapper_delegates_once_and_has_no_runtime_fallback() -> None:
    source = Path("ai_test_asset_center/v12_pipeline.py").read_text(encoding="utf-8")
    assert "effective_execution_status" not in source
    assert "fallback_to_legacy" not in source
    assert source.count("run_discovery_mainline(") == 1
```

Also assert active runtime modules do not call legacy scenario execution when
`mainline_authority=experiment_candidate`.

- [ ] **Step 2: Run focused integration tests and capture failures**

```powershell
pytest tests/test_discovery_mainline_coordinator.py tests/test_e2e_system_promise_closed_loop.py tests/test_private_pilot_server_campaign_context_patch.py -q
```

- [ ] **Step 3: Extract remaining domain work from `run_v12_pipeline`**

Keep the wrapper signature for callers:

```python
def run_v12_pipeline(project: str, root: Path, prd_text: str = "",
                     api_spec_text: str = "", db_schema_text: str = "",
                     base_url: str = "", existing_findings: list[dict] | None = None,
                     campaign_context: dict[str, Any] | None = None) -> dict[str, Any]:
    inputs = DiscoveryMainlineInputs(
        project=project,
        root=root,
        prd_text=prd_text,
        api_spec_text=api_spec_text,
        db_schema_text=db_schema_text,
        approved_base_url=base_url,
        campaign_context=dict(campaign_context or {}),
        existing_findings=tuple(existing_findings or ()),
    )
    return run_discovery_mainline(
        inputs,
        build_campaign=build_enterprise_campaign,
        build_plan=build_discovery_plan,
        legacy_runner=run_legacy_champion,
        experiment_runner=run_experiment_candidate,
    )
```

The coordinator calls only the selected runner. After candidate promotion,
`run_legacy_champion` remains available only to separate frozen comparison
runs; operational policies select `experiment_candidate`.

- [ ] **Step 4: Update every caller to pass immutable run identity**

`__main__.py`, continuous evaluation, private pilot, campaign APIs, and policy
evaluation must provide run, target, environment, campaign, policy, evaluation
mode, and authority before calling V12. Missing identity fails before planning
or requests.

- [ ] **Step 5: Syntax-check and run integration tests**

```powershell
python -c "import ast; ast.parse(open('ai_test_asset_center/discovery_mainline.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/v12_pipeline.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/__main__.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/continuous_evaluation.py', encoding='utf-8').read()); ast.parse(open('ai_test_asset_center/private_pilot_service.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_discovery_mainline_coordinator.py tests/test_e2e_system_promise_closed_loop.py tests/test_phase109_incremental_behavior_slices.py tests/test_private_pilot_server_campaign_context_patch.py -q
```

- [ ] **Step 6: Commit Task 9 only**

```powershell
git add -- ai_test_asset_center/discovery_mainline.py ai_test_asset_center/v12_pipeline.py ai_test_asset_center/__main__.py ai_test_asset_center/continuous_evaluation.py ai_test_asset_center/private_pilot_service.py tests/test_discovery_mainline_coordinator.py tests/test_e2e_system_promise_closed_loop.py tests/test_phase109_incremental_behavior_slices.py tests/test_private_pilot_server_campaign_context_patch.py
git commit -m "refactor: cut over to the discovery mainline coordinator"
```

### Task 10: Add Cycle-Time Receipts and Update Living Documentation

**Files:**
- Create: `tools/discovery_phase1_timing.py`
- Create: `tests/test_discovery_phase1_timing.py`
- Modify: `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`
- Modify: `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: immutable `qualibug.discovery-phase1-timing.v1` baseline/candidate
  receipts and updated runtime-authority documentation.
- Consumes: exact focused command list and code/input/environment fingerprints.

- [ ] **Step 1: Write failing timing-receipt tests**

```python
def test_timing_receipt_requires_five_matching_warm_runs() -> None:
    with pytest.raises(TimingReceiptError, match="five_warm_runs_required"):
        build_timing_receipt(
            command=["pytest", "tests/test_discovery_mainline_authority.py", "-q"],
            samples_ms=[10, 11],
            code_fingerprint="commit-1",
            input_fingerprint="input-1",
            environment_fingerprint="env-1",
        )


def test_compare_requires_same_command_and_environment() -> None:
    baseline_receipt = {
        "schema_version": "qualibug.discovery-phase1-timing.v1",
        "command_fingerprint": "command-1",
        "environment_fingerprint": "env-1",
        "input_fingerprint": "input-1",
        "samples_ms": [100, 101, 102, 103, 104],
        "p50_ms": 102,
    }
    candidate_receipt = {
        **baseline_receipt,
        "command_fingerprint": "command-2",
        "samples_ms": [30, 31, 32, 33, 34],
        "p50_ms": 32,
    }
    with pytest.raises(TimingReceiptError, match="timing_identity_mismatch"):
        compare_timing_receipts(baseline_receipt, candidate_receipt)
```

- [ ] **Step 2: Implement measurement and comparison**

Receipt fields include schema version, five samples, p50/p95, command
fingerprint, Python/runtime fingerprint, CPU/OS fingerprint, code commit,
input fingerprint, and creation time. Comparison passes only when p50 improves
by at least 60% and identities match.

```python
def build_timing_receipt(*, command: list[str], samples_ms: list[int],
                         code_fingerprint: str, input_fingerprint: str,
                         environment_fingerprint: str) -> dict[str, Any]:
    if len(samples_ms) != 5:
        raise TimingReceiptError("five_warm_runs_required")
    ordered = sorted(int(value) for value in samples_ms)
    command_blob = json.dumps(command, separators=(",", ":"))
    return {
        "schema_version": "qualibug.discovery-phase1-timing.v1",
        "command": command,
        "command_fingerprint": hashlib.sha256(command_blob.encode("utf-8")).hexdigest(),
        "code_fingerprint": code_fingerprint,
        "input_fingerprint": input_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "samples_ms": ordered,
        "p50_ms": ordered[2],
        "p95_ms": ordered[4],
    }


def compare_timing_receipts(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    identity_keys = ("command_fingerprint", "input_fingerprint", "environment_fingerprint")
    if any(baseline.get(key) != candidate.get(key) for key in identity_keys):
        raise TimingReceiptError("timing_identity_mismatch")
    baseline_p50 = int(baseline["p50_ms"])
    candidate_p50 = int(candidate["p50_ms"])
    improvement = (baseline_p50 - candidate_p50) / baseline_p50
    return {"improvement_ratio": improvement, "passed": improvement >= 0.60}
```

- [ ] **Step 3: Run the focused timing tests**

```powershell
python -c "import ast; ast.parse(open('tools/discovery_phase1_timing.py', encoding='utf-8').read()); print('OK')"
pytest tests/test_discovery_phase1_timing.py -q
```

- [ ] **Step 4: Update documentation after runtime cutover**

Document the exact authoritative modules, run contract, Trace Ledger V2,
attempt-ledger completion, compatibility wrapper, and rollback semantics.
Retain Goal thresholds in the Goal SSOT and architecture details in the
architecture spec. Update `AGENTS.md` in the same change because runtime
authority changed.

- [ ] **Step 5: Commit Task 10 only**

```powershell
git add -- tools/discovery_phase1_timing.py tests/test_discovery_phase1_timing.py docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md AGENTS.md
git commit -m "docs: record discovery mainline authority and timing"
```

### Task 11: Verify Phase 1 and Run Clean Champion/Candidate Evaluation

**Files:**
- Modify only if verification exposes a root-cause defect; return to the owning
  task and add a failing test before editing.
- Evidence: `_audit_packs/` and evaluator-private output paths, never product
  runtime or committed hidden GT.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: syntax, unit/integration, timing, clean-worktree, safety, cleanup,
  and external non-regression evidence.

- [ ] **Step 1: Verify frozen configuration guardrails**

```powershell
python -c "from ai_test_asset_center.discovery_engine import AutonomousDiscoveryEngine; e=AutonomousDiscoveryEngine(); assert e.client.config.timeout_seconds >= 300; assert e.client.config.max_tokens >= 32768; print('guardrails OK')"
python -c "from ai_test_asset_center.stage_reason_all_v2 import MAX_HYPOTHESES; assert MAX_HYPOTHESES == 15; print('hypothesis cap OK')"
```

Verify the effective policy reasoner `max_workers <= 4` and both product ports
remain 5174/8088.

- [ ] **Step 2: Run focused Phase 1 suite**

```powershell
pytest tests/test_discovery_mainline_authority.py tests/test_behavior_ir_obligation_experiment.py tests/test_mainline_unification_bridge.py tests/test_discovery_mainline_coordinator.py tests/test_obligation_attempt_ledger.py tests/test_experiment_contract_execution.py tests/test_discovery_trace_weakness_mining.py tests/test_pipeline_health_visibility.py tests/test_discovery_funnel_observability.py tests/test_scan_counter_unification.py tests/test_gate_d_campaign_contracts.py tests/test_discovery_phase1_timing.py -q
```

Expected: all pass.

- [ ] **Step 3: Run the full Python test suite**

```powershell
pytest -q
```

Expected: all tests pass. Any failure is investigated at its causal stage; do
not add compatibility fallbacks to hide it.

- [ ] **Step 4: Produce the stage-local timing comparison**

Run the frozen focused command five warm times for baseline and candidate using
`tools/discovery_phase1_timing.py`. Expected: matching identities and p50
improvement `>= 60%`.

- [ ] **Step 5: Create a clean eligible commit and worktree receipt**

After all required current-worktree source changes have been reviewed and
committed, use `superpowers:using-git-worktrees` to create a clean benchmark
worktree from the exact candidate commit. Leave unrelated dirty and untracked
artifacts in the original worktree untouched. Verify that every file needed by
the candidate is present in the clean worktree before evaluation.

```powershell
git status --short
git rev-parse HEAD
```

Expected: empty status before an eligible benchmark. Do not delete or hide user
artifacts to manufacture cleanliness; commit or explicitly relocate only with
the user's authority.

- [ ] **Step 6: Run real champion and candidate replay/shadow evaluations**

Use the evaluator-private 131-Bug manifest and frozen target controller. Run
four separate envelopes: champion replay, candidate replay, champion shadow,
candidate shadow. Each must bind identical input, fixture, context, target,
environment, and evaluator fingerprints.

Expected:

- no production requests or safety incidents;
- write audit coverage 100%;
- cleanup success 100%;
- pipeline health not degraded;
- every selected obligation terminal;
- formal ID/count consistency true;
- candidate TP, Recall, Precision, F1, reproduction, and duplicate rate do not
  regress from the eligible champion;
- shadow runs publish no customer output or product-created evaluation
  submission, while the evaluator-owned runner emits private shadow receipts;
- operational cost is measured or `UNKNOWN`, never synthesized as zero.

- [ ] **Step 7: Build the Phase 1 audit pack**

Run the existing audit-pack builder and include Goal assessment, run contracts,
attempt ledger, Trace Ledger V2, formal consistency receipt, timing comparison,
safety/cleanup receipts, and external evaluator receipts. Phase 1 remains
incomplete if any required artifact is missing.

- [ ] **Step 8: Commit only verified remediation, if any**

If verification required code changes, return to the owning task, add the
failing test, rerun its focused suite, and commit a root-cause fix. Do not make
an untested final cleanup commit.

## Plan Self-Review Result

- Every Phase 1 design requirement maps to a task.
- Mainline authority is frozen before execution and one run never invokes both
  runners.
- Campaign identity precedes Behavior IR, planning, fixtures, and execution.
- Completion, health, trace, and formal scopes derive from obligation attempts.
- Behavior IR V2 and Trace Ledger V2 migrations are explicit, never silent.
- Legacy inputs have a pure adapter and no execution/formal authority after
  cutover.
- Quality gates remain external and non-regressive; constructed test data never
  counts as TP.
- No configuration, port, safety, cleanup, redaction, or hidden-GT constraint is
  weakened.
