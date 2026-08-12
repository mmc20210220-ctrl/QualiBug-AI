"""Public-entry mainline contract: compile → execute → observe → oracle → delivery.

This is the gate for the next E2E round. A run through the PUBLIC entries must
prove, per executed experiment:

* required business-step ledger non-empty — ``executed_step_ids ∩
  required_step_ids ≠ ∅``; fixture/binding materialization requests are
  timeline events, never ledger rows, so they can never count as executed;
* zero ``PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH`` rejections (receipts declare
  step scopes that match recorded business rows);
* no spurious finalizer ledger-hash block (the semantic view seals through the
  same ledger state the finalizer compares);
* the oracle is evaluated and the finding passes the delivery gate.

The transport is mocked at ``_http_request`` across every by-value binding the
executor chain captures; everything else (compiler, fixture materializer,
plan executor, process-step ledger, observers, oracle, finalizer, delivery
gate) is the real mainline.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)
from ai_test_asset_center.experiment_runtime_support import _stable_id
from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
    validate_customer_delivery_gate_receipt_v2,
)
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)

_HTTP_MOCK_TARGETS = (
    "ai_test_asset_center.experiment_executor._http_request",
    "ai_test_asset_center.experiment_plan_executor._http_request",
    "ai_test_asset_center.experiment_runtime_support._http_request",
    "ai_test_asset_center.experiment_runtime_credentials._http_request",
    "ai_test_asset_center.sandbox_write_executor._http_request",
    "ai_test_asset_center.sandbox_write_executor_base._http_request",
    "ai_test_asset_center._experiment_runtime_support_mechanics._http_request",
    "ai_test_asset_center._experiment_executor_governance_authority_mechanics._http_request",
    "ai_test_asset_center.experiment_plan_step_executor_core._http_request",
)


def _base_ir(*, with_clerk: bool = False) -> dict:
    """Industry-neutral Behavior IR: one read operation, manager permitted."""
    actors = [
        {
            "id": "manager",
            "role": "manager",
            "account_ref": "manager@corp",
            "runtime_bound": True,
            "secret_ref": "secret://manager",
            "credential_secret_ref": "secret://manager",
            "source_refs": [{"kind": "role", "ref": "role-manager"}],
        }
    ]
    relations = [
        {
            "id": "rel-permit",
            "relation_type": "permits",
            "from_ref": "manager",
            "to_ref": "op-read",
            "operation_ref": "op-read",
            "actor_ref": "manager",
            "preconditions": [],
            "effects": [],
            "source_refs": [{"kind": "permission", "ref": "perm-manager"}],
        }
    ]
    if with_clerk:
        actors.append(
            {
                "id": "clerk",
                "role": "clerk",
                "account_ref": "clerk@corp",
                "runtime_bound": True,
                "secret_ref": "secret://clerk",
                "credential_secret_ref": "secret://clerk",
                "source_refs": [{"kind": "role", "ref": "role-clerk"}],
            }
        )
        relations.append(
            {
                "id": "rel-deny",
                "relation_type": "denies",
                "from_ref": "clerk",
                "to_ref": "op-read",
                "operation_ref": "op-read",
                "actor_ref": "clerk",
                "preconditions": [],
                "effects": [],
                "source_refs": [{"kind": "permission", "ref": "deny-clerk"}],
            }
        )
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "sources": [{"id": "src-1", "kind": "document", "source_ref": "doc"}],
        "entities": [],
        "operations": [
            {
                "id": "op-read",
                "method": "GET",
                "path": "/api/things",
                "read_write": "read",
                "source_refs": [{"kind": "interface", "ref": "iface"}],
            }
        ],
        "actors": actors,
        "states": [],
        "relations": relations,
        "invariants": [],
        "observation_surfaces": [],
        "ui_specs": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }


def _compile_experiment(*, with_clerk: bool = False) -> tuple[dict, dict]:
    """Public compile path: Behavior IR → obligations → compiled experiment."""
    ir = _base_ir(with_clerk=with_clerk)
    obligation_pack = compile_obligations_from_behavior_ir(ir, root="", project="")
    family = "authorization"
    obligation = next(
        (
            row
            for row in obligation_pack["obligations"]
            if isinstance(row, dict) and row.get("risk_family") == family
        ),
        None,
    )
    assert obligation is not None, "authorization obligation must compile"
    experiment_pack = compile_experiments(
        [obligation], behavior_ir=ir, environment_type="test"
    )
    experiments = [
        row
        for row in experiment_pack.get("experiments", [])
        if isinstance(row, dict)
    ]
    assert experiments, "at least one COMPILED experiment required"
    experiment = dict(experiments[0])
    assert (
        (experiment.get("compile_receipt") or {}).get("status") == "COMPILED"
    ), experiment.get("compile_receipt")
    experiment["fixture_dag"] = {"status": "READY", "nodes": [], "setup_order": []}
    experiment["safety_contract"] = {"environment_type": "test"}
    return experiment, ir


def _mock_transport(monkeypatch, status_code: int, body: dict) -> None:
    def fake_http(method: str, url: str, **_kwargs: object) -> dict:
        return {"status": status_code, "body": body, "headers": {}}

    for target in _HTTP_MOCK_TARGETS:
        monkeypatch.setattr(target, fake_http)


def _runtime_contract() -> dict:
    return {
        "approved_base_url": "http://target.invalid",
        "environment_type": "test",
        "environment_ref": "test-env",
        "status": "approved",
    }


def test_compile_execute_observe_oracle_ledger_contract(monkeypatch, tmp_path) -> None:
    """The public mainline proves business steps executed with clean identity.

    Two-arm authorization experiment (manager permitted, clerk denied): the
    mocked target returns 200 for BOTH arms — the denied clerk succeeds, which
    is the violation the http_status_class assertion must surface.
    """
    experiment, ir = _compile_experiment(with_clerk=True)
    _mock_transport(monkeypatch, 200, {"items": []})

    result = execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract=_runtime_contract(),
        campaign_id="campaign",
        execution_id="execution-mainline",
        actor_tokens={
            "secret://manager": "tok-m",
            "secret://clerk": "tok-c",
        },
    )

    # Execution reached the target for both required business steps.
    assert result.get("status") == "EXECUTED", result.get("detail")
    required = set(result.get("required_step_ids") or [])
    assert required, "compiled experiment must declare required business steps"
    executed = set(result.get("executed_step_ids") or [])
    # The gate: required business-step ledger non-empty; fixture/binding
    # requests never count (they are timeline events, not ledger rows).
    assert required <= executed, (
        f"required business steps missing from executed ledger: "
        f"required={sorted(required)} executed={sorted(executed)}"
    )
    recorded = set(result.get("recorded_step_ids") or [])
    assert required <= recorded, "required steps must be recorded in the ledger"

    # The execution steps really reached transport (mocked target responses).
    business_steps = [
        step
        for step in (result.get("steps") or [])
        if isinstance(step, dict)
        and step.get("phase") in {"control", "treatment"}
    ]
    assert business_steps, "no control/treatment steps reached transport"
    assert all(
        int(step.get("status_code") or 0) == 200 for step in business_steps
    )

    # No spurious finalizer ledger-hash block; the semantic view sealed
    # through the same ledger state the finalizer compares.
    assert not result.get("finalizer_block_reason"), result.get(
        "finalizer_block_reason"
    )

    # The oracle was evaluated (an authorization comparison verdict).
    oracle = result.get("oracle_verdict") or {}
    assert oracle.get("verdict"), "oracle must be evaluated"
    assert oracle.get("receipt_id"), "oracle receipt must be content-addressed"

    # Receipt identity: no scoped receipt declared a step that was not
    # recorded (the run27 PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH symptom).
    rejections = result.get("process_step_receipt_scope_rejections") or []
    assert not rejections, f"receipt identity mismatches: {rejections[:5]}"


def test_single_arm_violation_reaches_delivery_gate(monkeypatch, tmp_path) -> None:
    """A permitted read that 404s violates its http_status_class contract.

    The executed violation finding then flows through the same public delivery
    gate builders the batch executor uses and must validate as DELIVERABLE.
    """
    experiment, ir = _compile_experiment(with_clerk=False)
    _mock_transport(monkeypatch, 404, {"error": "not_found"})

    result = execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract=_runtime_contract(),
        campaign_id="campaign",
        execution_id="execution-delivery",
        actor_tokens={"secret://manager": "tok-m"},
    )

    assert result.get("status") == "EXECUTED", result.get("detail")
    executed = set(result.get("executed_step_ids") or [])
    required = set(result.get("required_step_ids") or [])
    assert required <= executed, (
        f"required business steps missing from executed ledger: "
        f"required={sorted(required)} executed={sorted(executed)}"
    )

    oracle = result.get("oracle_verdict") or {}
    assert oracle.get("verdict"), oracle.get("verdict")
    assert oracle.get("status") == "VIOLATION", oracle.get("status")
    finding = dict(result.get("finding") or {})
    assert finding, "violation must produce a finding"
    # ── Delivery link: the public gate builders exactly as the batch uses ──
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-contract",
        campaign_id="campaign",
        target_id="target-1",
        environment_id="env-1",
        policy_version="v1.0.0-test",
        evaluation_mode="operational",
    )
    # The batch seals finding identity before the gate (identity is evidence,
    # never a mutable field on the caller's dict).
    finding_id = _stable_id("finding", "evidence-1")
    finding.update(
        {
            "id": finding_id,
            "finding_id": finding_id,
            "candidate_id": "candidate-1",
            "behavior_slice_id": "slice-1",
            "slice_id": "slice-1",
            "evidence_id": "evidence-1",
            "campaign_id": "campaign",
            "obligation_id": result.get("obligation_id"),
            "experiment_id": result.get("experiment_id"),
            "execution_id": "execution-delivery",
            "mainline_run": {
                "contract_fingerprint": _text(mainline.get("contract_fingerprint"))
            },
        }
    )
    operational = build_execution_operational_receipt(
        receipt_id="operational-execution-delivery",
        execution_status=result.get("status") or "EXECUTED",
        steps=[row for row in (result.get("steps") or []) if isinstance(row, dict)],
        cleanup_failures=int(result.get("cleanup_failures") or 0),
    )
    delivery_execution_receipt = build_delivery_execution_receipt(
        mainline_run=mainline,
        candidate_id="candidate-1",
        slice_id="slice-1",
        obligation_id=result.get("obligation_id"),
        experiment_id=result.get("experiment_id"),
        execution_id="execution-delivery",
        evidence_id="evidence-1",
        operational_receipt=operational,
        observation_receipt_ids=_all_observation_receipt_ids(result),
        oracle_receipt_id=_text(oracle.get("receipt_id")),
        elapsed_ms=result.get("elapsed_ms") or 1,
        cost_coverage_status="UNKNOWN",
    )
    reproduction_receipt = build_reproduction_receipt(
        execution_receipt=delivery_execution_receipt,
        # The reproduction contract requires each replayed HTTP step to carry
        # its observation receipt id; the mainline attaches the typed observer
        # receipts per step, so project them onto the step rows exactly as the
        # executor does for governed steps (never invented).
        steps=_reproduction_steps(result),
        oracle_receipt=oracle,
        source_refs=[
            dict(row)
            for row in (experiment.get("source_refs") or [])
            if isinstance(row, dict)
        ],
    )
    gate_receipt = build_customer_delivery_gate_receipt_v2(
        finding=finding,
        execution_receipt=delivery_execution_receipt,
        contract_evidence_receipts=[
            dict(row)
            for row in (result.get("contract_evidence_receipts") or [])
            if isinstance(row, dict)
        ],
        observer_receipts=[
            dict(row)
            for row in (result.get("observer_receipts") or [])
            if isinstance(row, dict)
        ],
        oracle_receipt=oracle,
        reproduction_receipt=reproduction_receipt,
    )
    # The gate must validate as a structural receipt (the batch re-validates
    # every gate it produces; DELIVERABLE additionally requires the cleanup
    # and evidence adjudication to hold for this read-only violation).
    validated = validate_customer_delivery_gate_receipt_v2(gate_receipt)
    assert validated.get("status") == "DELIVERABLE", validated.get("status")
    assert gate_receipt.get("gate_receipt_id"), (
        "delivery gate receipt must be sealed"
    )


def test_divergent_fixture_dag_orders_still_execute_business_steps(
    monkeypatch, tmp_path
) -> None:
    """A fixture in fixture_dag.setup_order but absent from the v12 execution
    order must be genuinely processed, never reconciled into a synthetic
    receipt that the precondition wrapper blocks as BLOCKED_FIXTURE_DAG_DRIFT.

    run28: 221 obligations were blocked pre-transport by this divergence
    (materializer iterated only fixture_dependency_dag.execution_order while
    oracle activation reads fixture_dag.setup_order).
    """
    experiment, ir = _compile_experiment(with_clerk=False)
    experiment["safety_contract"] = {"environment_type": "test"}
    # Bypass the compiler merge: setup_order schedules a runtime-read binding
    # node that the v12 execution_order omits.
    experiment["fixture_dag"] = {
        "status": "READY",
        "fixture_dag_id": "dag-divergent",
        "setup_order": ["node-actor", "node-thing"],
        "nodes": [
            {
                "node_id": "node-actor",
                "kind": "actor_context",
                "actor_ref": "manager",
            },
            {
                "node_id": "node-thing",
                "kind": "runtime_read_binding",
                "target": "thingId",
                "actor_ref": "manager",
            },
        ],
    }
    experiment["fixture_dependency_dag"] = {
        "execution_order": ["node-actor"],
        "nodes": [
            {
                "node_id": "node-actor",
                "kind": "actor_context",
                "actor_ref": "manager",
            }
        ],
    }
    experiment["binding_plan"] = [
        {
            "target": "thingId",
            "status": "bound",
            "source_priority": "source_declared_path_example",
            "materialized_value": "thing-1",
        }
    ]
    _mock_transport(monkeypatch, 200, {"items": []})

    result = execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract=_runtime_contract(),
        campaign_id="campaign",
        execution_id="execution-divergent",
        actor_tokens={"secret://manager": "tok-m"},
    )

    assert result.get("status") == "EXECUTED", result.get("detail")
    required = set(result.get("required_step_ids") or [])
    executed = set(result.get("executed_step_ids") or [])
    assert required <= executed, (
        f"required business steps missing: required={sorted(required)} "
        f"executed={sorted(executed)}"
    )
    assert not result.get("finalizer_block_reason"), result.get(
        "finalizer_block_reason"
    )


def _all_observation_receipt_ids(result: dict) -> list[str]:
    """Observer + contract-evidence receipt ids, exactly as the batch projects.

    The delivery execution receipt's observation_receipt_ids must cover every
    contract evidence receipt the oracle activation verifies.
    """
    ids: list[str] = []
    for key in ("observer_receipts", "contract_evidence_receipts"):
        for row in (result.get(key) or []):
            if isinstance(row, dict) and _text(row.get("receipt_id")):
                ids.append(_text(row.get("receipt_id")))
    return ids


def _reproduction_steps(result: dict) -> list[dict]:
    """Project the typed observer receipts onto the executed step rows.

    The delivery reproduction contract requires each replayed HTTP step to
    carry its observation receipt id. The mainline attaches typed observer
    receipts per step (evidence.step_id); this projection binds exactly those
    ids — nothing is invented.
    """
    observation_by_step: dict[str, str] = {}
    for row in (result.get("observer_receipts") or []):
        if not isinstance(row, dict):
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        step_id = _text(evidence.get("step_id"))
        if step_id and _text(row.get("receipt_id")):
            observation_by_step.setdefault(step_id, _text(row.get("receipt_id")))
    steps: list[dict] = []
    for row in (result.get("steps") or []):
        if not isinstance(row, dict):
            continue
        step = dict(row)
        step_id = _text(step.get("step_id"))
        if step_id in observation_by_step:
            step["observation_receipt_id"] = observation_by_step[step_id]
        steps.append(step)
    return steps


def _text(value: object) -> str:
    return str(value or "").strip()
