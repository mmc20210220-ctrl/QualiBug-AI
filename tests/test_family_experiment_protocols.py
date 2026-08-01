from __future__ import annotations

import threading

import pytest

import ai_test_asset_center.experiment_outcome_finalizer as outcome_finalizer
import ai_test_asset_center.experiment_barrier_executor as barrier_executor
import ai_test_asset_center.experiment_plan_executor as plan_executor
import ai_test_asset_center.experiment_runtime_support as runtime_support
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import (
    _select_fixture_actor,
    _select_runtime_binding,
    execute_one_experiment,
)
from ai_test_asset_center.experiment_fixture_materializer import (
    _auto_fixture_create_for_binding_target,
    materialize_experiment_fixtures,
)
from ai_test_asset_center.fixture_dag import build_fixture_dag_for_experiment
from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.observer_contracts import observe_experiment_requirements
from ai_test_asset_center.runtime_binding_materializer import (
    runtime_cleanup_paths,
    validated_fixture_setup,
)
from ai_test_asset_center.runtime_binding_graph import _declared_fixture_actor_refs
from ai_test_asset_center.write_reversibility_contract import build_reversibility_proof


def _patch_http_request(monkeypatch: pytest.MonkeyPatch, http_request) -> None:
    """Patch HTTP transport on every module that binds ``_http_request``."""
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        http_request,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._http_request",
        http_request,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor._http_request",
        http_request,
    )


def test_finalizer_rejects_unserializable_receipt_objects() -> None:
    with pytest.raises(TypeError, match="unsupported_receipt_object"):
        outcome_finalizer._safe_serialize(object())


def _patch_governed_write(monkeypatch: pytest.MonkeyPatch, governed_write) -> None:
    """Patch governed writes across experiment execution modules."""
    for mod in (
        "ai_test_asset_center.experiment_executor",
        "ai_test_asset_center.experiment_fixture_materializer",
        "ai_test_asset_center.experiment_fixture_materializer_core",
        "ai_test_asset_center.experiment_barrier_executor",
        "ai_test_asset_center.experiment_plan_executor",
        "ai_test_asset_center.experiment_cleanup_executor",
    ):
        monkeypatch.setattr(f"{mod}.execute_governed_control_write", governed_write)


def test_partial_execution_preserves_later_pretransport_block_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outcome_finalizer,
        "observe_experiment_requirements",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        outcome_finalizer,
        "evaluate_contract_oracle",
        lambda **kwargs: {
            "status": "PROPERTY_HELD",
            "verdict": "property_held",
        },
    )

    result = outcome_finalizer.finalize_experiment_execution(
        exp={
            "experiment_id": "experiment-1",
            "obligation_id": "obligation-1",
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
            "source_refs": [],
            "assertions": [],
        },
        steps_out=[{
            "phase": "control",
            "method": "GET",
            "path": "/resources/one",
            "status_code": 200,
        }],
        observations={},
        contract_evidence_receipts=[],
        fixture_receipts=[],
        binding_materialization_receipts=[],
        pre_transport_block_reasons=[
            "BLOCKED_MISSING_BINDING:treatment_1:resourceId"
        ],
        cleanup_failures=0,
        runtime_bindings={},
        ops={},
        actors={},
        eid="experiment-1",
        oid="obligation-1",
        campaign_id="campaign-1",
        resolved_campaign_id="campaign-1",
        resolved_execution_id="execution-1",
        started=0.0,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"


@pytest.mark.parametrize(
    ("actors", "ops", "expected_reason"),
    [
        (
            {},
            {"op-read": {"method": "GET", "path": "/resources"}},
            "BLOCKED_MISSING_ACTOR",
        ),
        (
            {"actor-reader": {"role": "reader"}},
            {"op-other": {"method": "GET", "path": "/resources"}},
            "BLOCKED_MISSING_OPERATION",
        ),
    ],
)
def test_runtime_never_falls_back_across_missing_actor_or_operation_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    actors: dict,
    ops: dict,
    expected_reason: str,
) -> None:
    transport_calls: list[tuple] = []
    monkeypatch.setattr(
        plan_executor,
        "_run_http_step",
        lambda **kwargs: transport_calls.append(tuple(kwargs.items())),
    )

    result = plan_executor.execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[{
            "step_id": "treatment-1",
            "actor_ref": "actor-reader",
            "operation_ref": "op-read",
            "method": "GET",
            "path": "/resources",
        }],
        consumed_barrier_steps=set(),
        actors=actors,
        ops=ops,
        tokens={},
        runtime_bindings={},
        activation_requirements={"treatment": ["treatment-1"]},
        observations={},
        eid="experiment-1",
        oid="obligation-1",
        resolved_campaign_id="campaign-1",
        resolved_execution_id="execution-1",
        campaign_id="campaign-1",
        root=tmp_path,
        project="project-1",
        base_url="http://127.0.0.1:8088",
        runtime_contract={},
    )

    assert transport_calls == []
    assert result["steps"][0]["reason"] == expected_reason
    assert result["contract_evidence_receipts"][0]["status"] == "BLOCKED"


def test_runtime_preflight_allows_response_only_write_without_effect_observer() -> None:
    # V1.7: response-only write experiments (authorization/validation/isolation/
    # visibility) assert the write is REJECTED. No state change is expected, so
    # effect observation is unnecessary: http_response supplies the status-code
    # evidence. The preflight must allow this shape instead of blocking it.
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [{
            "actor_ref": "actor-public",
            "operation_ref": "op-create",
            "intent": "permitted_operation_invocation",
        }],
        "observers": [{"observer_id": "http_response"}],
        "assertions": [{
            "kind": "http_status_class",
            "template": "permitted_operation_invocation",
        }],
        "cleanup_plan": [{"operation_ref": "op-delete"}],
        "safety_contract": {"governed_write": True},
    }
    behavior_ir = {
        "actors": [{"id": "actor-public", "role": "public"}],
        "operations": [
            {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{resourceId}",
                "read_write": "write",
            },
        ],
    }

    ok, reason, detail = runtime_support.preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
    )

    assert ok is True
    assert reason == ""


def test_runtime_preflight_blocks_declared_effect_observer_without_readback() -> None:
    # A write experiment that DECLARES an effect observer (entity_state) but has
    # no source-declared observation path, no IR effect observer and no compiled
    # readback resolver must not degrade to http_response: the response would
    # become its own proof. This is the fail-closed half of the V1.7 observer
    # contract.
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [{
            "actor_ref": "actor-public",
            "operation_ref": "op-create",
            "intent": "permitted_operation_invocation",
        }],
        "observers": [{"observer_id": "http_response"}, {"observer_id": "entity_state"}],
        "assertions": [],
        "cleanup_plan": [{"operation_ref": "op-delete"}],
        "safety_contract": {"governed_write": True},
    }
    behavior_ir = {
        "actors": [{"id": "actor-public", "role": "public"}],
        "operations": [
            {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{resourceId}",
                "read_write": "write",
            },
        ],
    }

    ok, reason, detail = runtime_support.preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
    )

    assert ok is False
    assert reason == "BLOCKED_MISSING_OBSERVER"
    assert detail == "write_observer:op-create"


def test_runtime_preflight_blocks_unresolved_cleanup_body_before_write_transport() -> None:
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor-buyer",
            "operation_ref": "op-cancel",
            "intent": "permitted_operation_invocation",
        }],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "resolver_operations": [{
                "operation_ref": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
            }],
        }],
        "fixture_dag": {
            "status": "READY",
            "nodes": [{
                "node_id": "bind-id",
                "kind": "runtime_read_binding",
                "target": "id",
            }],
            "setup_order": ["bind-id"],
        },
        "observers": [{"observer_id": "http_response"}],
        "assertions": [{
            "kind": "http_status_class",
            "template": "permitted_operation_invocation",
        }],
        "cleanup_plan": [{
            "action": "reverse_order_compensation",
            "mode": "recreate_compensated_resource",
            "operation_ref": "op-recreate-order",
            "path": "/api/orders",
            "method": "POST",
            "body": {
                "items": [{"sku": "SKU-1", "qty": 1}],
                "addressId": "<address_id>",
            },
            "runtime_response_binding_required": False,
        }],
        "safety_contract": {"governed_write": True},
    }
    behavior_ir = {
        "actors": [{"id": "actor-buyer", "role": "public"}],
        "operations": [{
            "id": "op-cancel",
            "method": "POST",
            "path": "/api/orders/{id}/cancel",
            "read_write": "write",
        }, {
            "id": "op-list-orders",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
        }, {
            "id": "op-recreate-order",
            "method": "POST",
            "path": "/api/orders",
            "read_write": "write",
        }],
    }

    ok, reason, detail = runtime_support.preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
    )

    assert ok is False
    assert reason == "BLOCKED_NON_REVERSIBLE_WRITE"
    assert detail == "cleanup_preflight_body_placeholder_unresolved:address_id"

    experiment["cleanup_plan"][0]["body"] = None

    ok, reason, detail = runtime_support.preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
    )

    assert ok is False
    assert reason == "BLOCKED_NON_REVERSIBLE_WRITE"
    assert detail == "cleanup_preflight_recreate_body_missing:op-recreate-order"


@pytest.mark.parametrize(
    ("actors", "operation_ref", "expected_reason"),
    [
        ({}, "op-create", "BLOCKED_MISSING_ACTOR"),
        (
            {"actor-writer": {"role": "public"}},
            "op-missing",
            "BLOCKED_MISSING_OPERATION",
        ),
    ],
)
def test_barrier_runtime_blocks_missing_identity_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    actors: dict,
    operation_ref: str,
    expected_reason: str,
) -> None:
    write_calls: list[dict] = []
    monkeypatch.setattr(
        barrier_executor,
        "sandbox_write_allowed",
        lambda **kwargs: (True, ""),
    )
    monkeypatch.setattr(
        barrier_executor,
        "execute_governed_control_write",
        lambda **kwargs: write_calls.append(kwargs) or {},
    )
    steps = [
        {
            "step_id": "control-1",
            "actor_ref": "actor-writer",
            "operation_ref": operation_ref,
            "barrier_group": "group-1",
            "barrier_participant": "control",
        },
        {
            "step_id": "treatment-1",
            "actor_ref": "actor-writer",
            "operation_ref": operation_ref,
            "barrier_group": "group-1",
            "barrier_participant": "treatment",
        },
    ]
    result = barrier_executor.execute_barrier_plans(
        control_plan=[steps[0]],
        treatment_plan=[steps[1]],
        actors=actors,
        ops={
            "op-create": {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
                "request_example": {"value": 1},
            },
            "op-list": {
                "id": "op-list",
                "method": "GET",
                "path": "/resources",
                "read_write": "read",
            },
        },
        tokens={},
        runtime_bindings={},
        activation_requirements={
            "control": ["control-1"],
            "treatment": ["treatment-1"],
        },
        eid="experiment-1",
        oid="obligation-1",
        resolved_campaign_id="campaign-1",
        resolved_execution_id="execution-1",
        campaign_id="campaign-1",
        root=tmp_path,
        project="project-1",
        base_url="http://127.0.0.1:8088",
        runtime_contract={},
        observations={},
    )

    assert write_calls == []
    assert {step["reason"] for step in result["steps"]} == {expected_reason}



def _idempotency_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op-create",
                "operation_id": "create_resource",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
                "request_schema": {
                    "content": {
                        "application/json": {
                            "example": {"externalRef": "source-ref", "value": 1},
                        },
                    },
                },
            },
            {
                "id": "op-list",
                "operation_id": "list_resources",
                "method": "GET",
                "path": "/resources",
                "read_write": "read",
            },
            {
                "id": "op-read",
                "operation_id": "read_resource",
                "method": "GET",
                "path": "/resources/{id}",
                "read_write": "read",
            },
            {
                "id": "op-delete",
                "operation_id": "delete_resource",
                "method": "DELETE",
                "path": "/resources/{id}",
                "read_write": "write",
            },
        ],
        "actors": [{"id": "actor-writer", "role": "public", "account_status": "active"}],
    }


def _idempotency_obligation() -> dict:
    return {
        "obligation_id": "obl-idempotency",
        "source_refs": [{
            "kind": "api_contract",
            "source_id": "resource-api",
            "locator": "POST /resources",
        }],
        "risk_family": "idempotency",
        "property": {
            "operation_ref": "op-create",
            "template": "idempotent_effect_cardinality",
            "expected_effect_count": 1,
            "actor_ref": "actor-writer",
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-writer"],
        "required_observers": ["business_effect", "http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
    }


def test_idempotency_compiles_repeated_identical_write_protocol() -> None:
    experiment = compile_experiment_for_obligation(
        _idempotency_obligation(),
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert [step["protocol_step"] for step in experiment["control_plan"]] == [
        "initial_write",
    ]
    assert [step["protocol_step"] for step in experiment["treatment_plan"]] == [
        "repeat_write",
    ]
    assert experiment["control_plan"][0]["body"] == experiment["treatment_plan"][0]["body"]
    assert experiment["control_plan"][0]["body"] is not experiment["treatment_plan"][0]["body"]
    assert {row["observer_id"] for row in experiment["observers"]} == {
        "business_effect",
        "http_response",
    }


def test_idempotency_obligation_keeps_explicit_permitted_actor() -> None:
    ir = empty_behavior_ir(project_id="project")
    ir.update({
        "operations": [{
            "id": "op-create",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
            "confidence": 0.9,
            "source_refs": [{"source_id": "api"}],
        }],
        "actors": [{
            "id": "actor-writer",
            "role": "writer",
            "account_status": "active",
            "credential_secret_ref": "secret_ref:test_accounts:writer",
            "confidence": 0.9,
            "source_refs": [{"source_id": "permission"}],
        }],
        "invariants": [{
            "id": "inv-idempotency",
            "expression": {"kind": "idempotency"},
            "confidence": 0.9,
            "source_refs": [{"source_id": "rule"}],
        }],
        "relations": [
            {
                "id": "rel-permit",
                "relation_type": "permits",
                "from_ref": "actor-writer",
                "to_ref": "op-create",
                "operation_ref": "op-create",
                "actor_ref": "actor-writer",
                "preconditions": [],
                "effects": [],
                "source_refs": [{"source_id": "permission"}],
            },
            {
                "id": "rel-invariant",
                "relation_type": "observes",
                "from_ref": "op-create",
                "to_ref": "inv-idempotency",
                "operation_ref": "op-create",
                "actor_ref": "",
                "preconditions": [],
                "effects": [],
                "source_refs": [{"source_id": "rule"}],
            },
        ],
    })

    obligations = compile_obligations_from_behavior_ir(ir)["obligations"]
    idempotency = next(row for row in obligations if row["risk_family"] == "idempotency")

    assert idempotency["required_actors"] == ["actor-writer"]
    assert idempotency["property"]["actor_ref"] == "actor-writer"

    ir["invariants"][0]["expression"] = {"kind": "validation"}
    validation = next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "validation"
    )
    assert validation["required_observers"] == ["http_response"]


def test_compiler_does_not_assign_an_unpermitted_actor_to_an_actorless_rule() -> None:
    obligation = _idempotency_obligation()
    obligation["required_actors"] = []
    obligation["property"] = {
        **obligation["property"],
        "actor_ref": "",
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"] == {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_ACTOR",
        "detail": "source_permitted_actor_missing:op-create",
    }


def test_compiler_does_not_drop_a_missing_required_operation() -> None:
    obligation = _idempotency_obligation()
    obligation["required_operations"] = ["op-create", "op-unknown"]

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"] == {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_OPERATION",
        "detail": "op-unknown",
    }


def test_compiler_contains_no_generated_business_identity_fallback() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "experiment_compiler_obligation.py"
    ).read_text(encoding="utf-8")

    assert '"source_priority": "generated_identity"' not in source
    assert "_fallback_id = str(_uuid.uuid4())" not in source


def _governed_step(
    *,
    step_id: str,
    write_id: str,
    before: list[dict],
    after: list[dict],
) -> dict:
    return {
        "step_id": step_id,
        "phase": "treatment",
        "method": "POST",
        "path": "/resources",
        "status_code": 201,
        "body": {"id": write_id},
        "governance_receipt": {
            "accepted": True,
            "before": {"status": 200, "body": before},
            "write": {"status": 201, "body": {"id": write_id}},
            "after": {"status": 200, "body": after},
        },
    }


@pytest.mark.parametrize(
    ("second_after", "expected_effect_count"),
    [
        ([{"id": "r-1"}], 1),
        ([{"id": "r-1"}, {"id": "r-2"}], 2),
    ],
)
def test_business_effect_observer_counts_unique_persisted_effects(
    second_after: list[dict],
    expected_effect_count: int,
) -> None:
    steps = [
        _governed_step(
            step_id="treatment_1",
            write_id="r-1",
            before=[],
            after=[{"id": "r-1"}],
        ),
        _governed_step(
            step_id="treatment_2",
            write_id=second_after[-1]["id"],
            before=[{"id": "r-1"}],
            after=second_after,
        ),
    ]
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "idempotency"}],
            "observers": [{"observer_id": "business_effect"}],
        },
        observations={"execution_steps": steps},
    )

    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["effect_count"] == expected_effect_count
    assert receipts[0]["evidence"]["http_statuses"] == [201, 201]


def test_business_effect_counts_records_not_foreign_key_fields() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "idempotency"}],
            "observers": [{"observer_id": "business_effect"}],
        },
        observations={
            "execution_steps": [{
                "step_id": "treatment_1",
                "phase": "treatment",
                "method": "POST",
                "path": "/resources",
                "status_code": 201,
                "body": {"id": "r-1", "owner_id": "owner-1"},
                "governance_receipt": {
                    "accepted": True,
                    "before": {"status": 200, "body": []},
                    "write": {
                        "status": 201,
                        "body": {"id": "r-1", "owner_id": "owner-1"},
                    },
                    "after": {
                        "status": 200,
                        "body": [{"id": "r-1", "owner_id": "owner-1"}],
                    },
                },
            }],
        },
    )

    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["effect_count"] == 1


def test_business_effect_observer_counts_existing_entity_business_field_update() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "authorization"}],
            "observers": [{"observer_id": "business_effect"}],
        },
        observations={
            "execution_steps": [{
                "step_id": "treatment_1",
                "phase": "treatment",
                "method": "PATCH",
                "path": "/users/u-1/balance",
                "status_code": 200,
                "body": {"id": "u-1", "balance": "200.00"},
                "governance_receipt": {
                    "accepted": True,
                    "before": {
                        "status": 200,
                        "body": [{"id": "u-1", "balance": "100.00"}],
                    },
                    "write": {
                        "status": 200,
                        "body": {"id": "u-1", "balance": "200.00"},
                    },
                    "after": {
                        "status": 200,
                        "body": [{"id": "u-1", "balance": "200.00"}],
                    },
                },
            }],
        },
    )

    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["effect_count"] == 1
    assert receipts[0]["evidence"]["business_field_change_count"] == 1
    assert receipts[0]["evidence"]["identity_effect_count"] == 0


def test_business_effect_observer_ignores_server_managed_only_update() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "authorization"}],
            "observers": [{"observer_id": "business_effect"}],
        },
        observations={
            "execution_steps": [{
                "step_id": "treatment_1",
                "phase": "treatment",
                "method": "PATCH",
                "path": "/inventory/SKU-1",
                "status_code": 200,
                "body": {"id": "SKU-1", "available_qty": 10},
                "governance_receipt": {
                    "accepted": True,
                    "before": {
                        "status": 200,
                        "body": {
                            "id": "SKU-1",
                            "available_qty": 10,
                            "updated_at": "2026-01-01T00:00:00Z",
                        },
                    },
                    "write": {
                        "status": 200,
                        "body": {
                            "id": "SKU-1",
                            "available_qty": 10,
                            "updated_at": "2026-01-01T00:00:01Z",
                        },
                    },
                    "after": {
                        "status": 200,
                        "body": {
                            "id": "SKU-1",
                            "available_qty": 10,
                            "updated_at": "2026-01-01T00:00:01Z",
                        },
                    },
                },
            }],
        },
    )

    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["effect_count"] == 0
    assert receipts[0]["evidence"]["business_field_change_count"] == 0


def test_entity_state_observer_emits_governed_before_after_receipt() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "http_status_class"}],
            "observers": [{"observer_id": "entity_state"}],
        },
        observations={
            "execution_steps": [
                _governed_step(
                    step_id="treatment_1",
                    write_id="r-1",
                    before=[],
                    after=[{"id": "r-1"}],
                )
            ],
        },
    )

    assert receipts[0]["observer_id"] == "entity_state"
    assert receipts[0]["status"] == "OBSERVED"
    assert receipts[0]["evidence"]["entity_state_observed"] is True
    assert receipts[0]["evidence"]["state_change_count"] == 1


def test_state_snapshot_observers_emit_before_after_final_receipts() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "state_transition"}],
            "observers": [
                {"observer_id": "before_state"},
                {"observer_id": "after_state"},
                {"observer_id": "final_state"},
            ],
        },
        observations={
            "execution_steps": [
                _governed_step(
                    step_id="treatment_1",
                    write_id="order-1",
                    before=[{"id": "order-1", "status": "PENDING"}],
                    after=[{"id": "order-1", "status": "PAID"}],
                )
            ],
        },
    )

    by_observer = {receipt["observer_id"]: receipt for receipt in receipts}
    assert by_observer["before_state"]["status"] == "OBSERVED"
    assert by_observer["before_state"]["evidence"]["before_state"] == "PENDING"
    assert by_observer["after_state"]["status"] == "OBSERVED"
    assert by_observer["after_state"]["evidence"]["after_state"] == "PAID"
    assert by_observer["final_state"]["status"] == "OBSERVED"
    assert by_observer["final_state"]["evidence"]["final_state"] == "PAID"
    assert by_observer["final_state"]["evidence"]["cleanup_phase_excluded"] is True


def test_barrier_timeline_observer_requires_explicit_barrier_events() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "concurrency"}],
            "observers": [{"observer_id": "barrier_timeline"}],
        },
        observations={
            "execution_steps": [
                _governed_step(
                    step_id="treatment_1",
                    write_id="r-1",
                    before=[],
                    after=[{"id": "r-1"}],
                ),
                _governed_step(
                    step_id="treatment_2",
                    write_id="r-2",
                    before=[{"id": "r-1"}],
                    after=[{"id": "r-1"}, {"id": "r-2"}],
                ),
            ],
        },
    )

    assert receipts[0]["observer_id"] == "barrier_timeline"
    assert receipts[0]["status"] == "INDETERMINATE"
    assert receipts[0]["reason_code"] == "BARRIER_TIMELINE_MISSING"


def test_barrier_timeline_observer_emits_explicit_barrier_receipt() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": "concurrency"}],
            "observers": [{"observer_id": "barrier_timeline"}],
        },
        observations={
            "barrier_timeline": [
                {"event": "ready", "participant": "request-a", "at_ms": 1},
                {"event": "ready", "participant": "request-b", "at_ms": 2},
                {"event": "release", "participant": "all", "at_ms": 3},
                {"event": "completed", "participant": "request-a", "at_ms": 31},
                {"event": "completed", "participant": "request-b", "at_ms": 33},
            ],
        },
    )

    receipt = receipts[0]
    assert receipt["observer_id"] == "barrier_timeline"
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["event_count"] == 5
    assert receipt["evidence"]["participant_count"] == 2
    assert receipt["evidence"]["barrier_released"] is True
    assert receipt["evidence"]["timeline_fingerprint"]


def test_typed_assertion_and_source_invariant_observers_emit_contract_receipts() -> None:
    receipts = observe_experiment_requirements(
        {
            "source_refs": [{"source_id": "requirements", "locator": "rule:balance"}],
            "assertions": [{
                "assertion_id": "assert-conservation",
                "kind": "conservation",
                "property": {
                    "invariant_ref": "inv-balance",
                    "expression": {"kind": "conservation"},
                },
            }],
            "observers": [
                {"observer_id": "typed_assertion"},
                {"observer_id": "source_invariant"},
            ],
        },
        observations={},
    )

    by_observer = {receipt["observer_id"]: receipt for receipt in receipts}
    assert by_observer["typed_assertion"]["status"] == "OBSERVED"
    assert by_observer["typed_assertion"]["evidence"]["assertion_kind"] == "conservation"
    assert by_observer["typed_assertion"]["evidence"]["typed_assertion"] is True
    assert by_observer["source_invariant"]["status"] == "OBSERVED"
    assert by_observer["source_invariant"]["evidence"]["invariant_ref"] == "inv-balance"
    assert by_observer["source_invariant"]["evidence"]["source_ref_count"] == 1


def test_source_invariant_observer_requires_source_reference() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{
                "assertion_id": "assert-conservation",
                "kind": "conservation",
                "property": {"invariant_ref": "inv-balance"},
            }],
            "observers": [{"observer_id": "source_invariant"}],
        },
        observations={},
    )

    assert receipts[0]["observer_id"] == "source_invariant"
    assert receipts[0]["status"] == "INDETERMINATE"
    assert receipts[0]["reason_code"] == "SOURCE_INVARIANT_SOURCE_REF_MISSING"


def test_cleanup_paths_are_derived_per_accepted_write() -> None:
    steps = [
        _governed_step(
            step_id="treatment_1",
            write_id="r-1",
            before=[],
            after=[{"id": "r-1"}],
        ),
        _governed_step(
            step_id="treatment_2",
            write_id="r-2",
            before=[{"id": "r-1"}],
            after=[{"id": "r-1"}, {"id": "r-2"}],
        ),
    ]

    paths, missing = runtime_cleanup_paths("/resources/{id}", steps)

    assert paths == [
        ("/resources/r-1", {"id": "r-1"}),
        ("/resources/r-2", {"id": "r-2"}),
    ]
    assert missing == []


def test_cleanup_path_uses_single_observed_new_identity_when_write_body_is_empty() -> None:
    step = _governed_step(
        step_id="treatment_1",
        write_id="r-1",
        before=[],
        after=[{"id": "r-1"}],
    )
    step["body"] = {}
    step["governance_receipt"]["write"]["body"] = {}

    paths, missing = runtime_cleanup_paths("/resources/{id}", [step])

    assert paths == [("/resources/r-1", {"id": "r-1"})]
    assert missing == []


def test_idempotency_executor_observes_two_effects_and_cleans_each_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    experiment = compile_experiment_for_obligation(
        _idempotency_obligation(),
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )
    experiment["fixture_dag"] = {"status": "READY", "nodes": [], "setup_order": []}
    write_index = 0
    cleanup_paths: list[str] = []

    def governed_write(**kwargs):
        nonlocal write_index
        if kwargs["operation_phase"] in {
            "experiment_control",
            "experiment_treatment",
        }:
            write_index += 1
            write_id = f"r-{write_index}"
            before = [{"id": f"r-{index}"} for index in range(1, write_index)]
            after = [*before, {"id": write_id}]
            return {
                "accepted": True,
                "status": "executed",
                "method": kwargs["method"],
                "path": kwargs["path"],
                "before": {"status": 200, "body": before},
                "write": {"status": 201, "body": {"id": write_id}},
                "after": {"status": 200, "body": after},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {
                    "phase": kwargs["operation_phase"],
                    "id": write_id,
                },
            }
        assert kwargs["operation_phase"] == "experiment_cleanup"
        cleanup_paths.append(kwargs["path"])
        return {
            "accepted": True,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            # Equivalence hard gate: cleanup must return the environment to the
            # pre-write state, which the after-observation proves via a 2xx with
            # an empty collection (the created resource is gone).
            "before": {"status": 200, "body": [{"id": "r-1"}]},
            "write": {"status": 204, "body": {}},
            "after": {"status": 200, "body": []},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=_idempotency_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-idempotency",
        actor_tokens={},
    )

    # V1.7 per-step cleanup: each accepted write is compensated exactly once, in
    # reverse write order, and every cleanup write reaches transport with 2xx.
    cleanup_steps = [
        step for step in result["steps"] if step.get("phase") == "cleanup"
    ]
    assert [step.get("path") for step in cleanup_steps] == [
        "/resources/r-2",
        "/resources/r-1",
    ]
    assert all(
        200 <= int(step.get("status_code") or 0) < 300
        for step in cleanup_steps
    )
    # V1.3.0 equivalence hard gate: this mocked harness has no sealed
    # after-cleanup readback, so environment restoration stays unproven and the
    # lifecycle is EXECUTED_BUT_NOT_RESTORED with no finding produced.
    assert result["status"] == "EXECUTED_BUT_NOT_RESTORED"
    effect = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "business_effect"
    )
    assert effect["status"] == "OBSERVED"
    assert effect["evidence"]["effect_count"] == 2
    assert result["finding"] is None


def _isolation_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op-list",
                "operation_id": "list_resources",
                "method": "GET",
                "path": "/resources",
                "read_write": "read",
            },
            {
                "id": "op-read",
                "operation_id": "read_resource",
                "method": "GET",
                "path": "/resources/{id}",
                "read_write": "read",
            },
            {
                "id": "op-create",
                "operation_id": "create_resource",
                "method": "POST",
                "path": "/resources",
                "read_write": "write",
                "request_schema": {
                    "content": {
                        "application/json": {
                            "example": {"name": "source-declared"},
                        },
                    },
                },
            },
            {
                "id": "op-delete",
                "operation_id": "delete_resource",
                "method": "DELETE",
                "path": "/resources/{id}",
                "read_write": "write",
            },
        ],
        "actors": [
            {"id": "actor-owner", "role": "public", "account_ref": "owner"},
            {"id": "actor-viewer", "role": "public", "account_ref": "viewer"},
        ],
        "relations": [{
            "id": "permit-owner-create",
            "relation_type": "permits",
            "from_ref": "actor-owner",
            "to_ref": "op-create",
            "operation_ref": "op-create",
            "actor_ref": "actor-owner",
        }],
    }


def _isolation_obligation() -> dict:
    return {
        "obligation_id": "obl-isolation",
        "source_refs": [{
            "kind": "permission_matrix",
            "source_id": "resource-roles",
            "locator": "owner->viewer:resource.read:DENY",
        }],
        "risk_family": "isolation",
        "property": {
            "operation_ref": "op-read",
            "owner_actor_ref": "actor-owner",
            "viewer_actor_ref": "actor-viewer",
            "require_ownership_evidence": True,
            "require_same_resource": True,
        },
        "required_operations": ["op-read"],
        "required_actors": ["actor-owner", "actor-viewer"],
        "required_fixtures": ["owned_resource"],
        "required_observers": ["http_response", "resource_ownership"],
        "cleanup_requirement": {"required": False},
    }


def test_fixture_actor_selection_prefers_control_actor_from_declared_refs() -> None:
    actor_ref, actor, token = _select_fixture_actor(
        {"actor_refs": ["actor-other", "actor-treatment", "actor-control"]},
        control_plan=[{"actor_ref": "actor-control"}],
        treatment_plan=[{"actor_ref": "actor-treatment"}],
        actors={
            "actor-other": {
                "id": "actor-other",
                "role": "writer",
                "credential_secret_ref": "secret:other",
            },
            "actor-treatment": {
                "id": "actor-treatment",
                "role": "writer",
                "credential_secret_ref": "secret:treatment",
            },
            "actor-control": {
                "id": "actor-control",
                "role": "writer",
                "credential_secret_ref": "secret:control",
            },
        },
        tokens={
            "secret:other": "token-other",
            "secret:treatment": "token-treatment",
            "secret:control": "token-control",
        },
    )

    assert actor_ref == "actor-control"
    assert actor["id"] == "actor-control"
    assert token == "token-control"


def _cart_collection_operations() -> dict[str, dict]:
    return {
        "cart_list": {
            "id": "cart_list",
            "method": "GET",
            "path": "/api/cart/items",
        },
        "cart_create": {
            "id": "cart_create",
            "method": "POST",
            "path": "/api/cart/items",
            "request_example": {"sku": "SKU-PHONE-001", "qty": 1},
        },
        "cart_delete": {
            "id": "cart_delete",
            "method": "DELETE",
            "path": "/api/cart/items/{id}",
        },
        "cart_patch": {
            "id": "cart_patch",
            "method": "PATCH",
            "path": "/api/cart/items/{id}",
            "request_example": {"qty": 2},
        },
    }


def test_auto_fixture_create_uses_campaign_actors_when_owner_absent() -> None:
    """Regression: a resolver-only binding (no fixture_owner_actor_ref) on an
    empty collection used to block auto-fixture entirely, so a cart {id} binding
    with an available POST create + DELETE cleanup stayed BLOCKED_MISSING_BINDING.
    The runtime actor picker prefers control/treatment actors from the declared
    candidate pool, so the pool may contain every executable campaign actor.
    """
    binding = {
        "status": "runtime_resolvable",
        "target": "id",
        "target_path": "/api/cart/items/{id}",
        "resolver_operations": [
            {"operation_ref": "cart_list", "method": "GET", "path": "/api/cart/items"},
        ],
    }
    actors = {
        "buyer": {"id": "buyer", "role": "customer", "credential_secret_ref": "secret:buyer"},
        "admin": {"id": "admin", "role": "admin", "credential_secret_ref": "secret:admin"},
    }

    auto_create = _auto_fixture_create_for_binding_target(
        "id",
        binding,
        _cart_collection_operations(),
        {},
        actors=actors,
    )

    assert auto_create is not None
    assert auto_create["create_operation_ref"] == "cart_create"
    assert auto_create["force_fixture_setup"] is True
    fixture_setup = validated_fixture_setup(
        {"fixture_setup": auto_create["fixture_setup"]},
        _cart_collection_operations(),
        actors,
    )
    assert set(fixture_setup["actor_refs"]) == {"buyer", "admin"}
    assert fixture_setup["cleanup_operations"]
    # The disposable create must remain plan-aligned at runtime: control actor
    # is preferred from the candidate pool.
    selected_ref, selected, selected_token = _select_fixture_actor(
        fixture_setup,
        control_plan=[{"actor_ref": "buyer"}],
        treatment_plan=[{"actor_ref": "admin"}],
        actors=actors,
        tokens={"secret:buyer": "token-buyer", "secret:admin": "token-admin"},
    )
    assert selected_ref == "buyer"
    assert selected_token == "token-buyer"


def test_auto_fixture_create_stays_fail_closed_without_executable_actor() -> None:
    binding = {
        "status": "runtime_resolvable",
        "target": "id",
        "target_path": "/api/cart/items/{id}",
        "resolver_operations": [
            {"operation_ref": "cart_list", "method": "GET", "path": "/api/cart/items"},
        ],
    }
    # Only anonymous actors exist: no one can own the disposable resource.
    assert (
        _auto_fixture_create_for_binding_target(
            "id",
            binding,
            _cart_collection_operations(),
            {},
            actors={"anon": {"id": "anon", "role": "anonymous"}},
        )
        is None
    )
    # No declared actors at all: still fail closed, never invent an identity.
    assert (
        _auto_fixture_create_for_binding_target(
            "id",
            binding,
            _cart_collection_operations(),
            {},
            actors={},
        )
        is None
    )


def test_auto_fixture_create_prefers_declared_owner_actor() -> None:
    binding = {
        "status": "runtime_resolvable",
        "target": "id",
        "target_path": "/api/cart/items/{id}",
        "fixture_owner_actor_ref": "buyer",
        "resolver_operations": [
            {"operation_ref": "cart_list", "method": "GET", "path": "/api/cart/items"},
        ],
    }
    actors = {
        "buyer": {"id": "buyer", "role": "customer", "credential_secret_ref": "secret:buyer"},
        "admin": {"id": "admin", "role": "admin", "credential_secret_ref": "secret:admin"},
    }
    auto_create = _auto_fixture_create_for_binding_target(
        "id",
        binding,
        _cart_collection_operations(),
        {},
        actors=actors,
    )
    assert auto_create is not None
    fixture_setup = validated_fixture_setup(
        {"fixture_setup": auto_create["fixture_setup"]},
        _cart_collection_operations(),
        actors,
    )
    assert fixture_setup["actor_refs"] == ["buyer"]


def test_empty_collection_binding_uses_auto_fixture_and_binds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Full materialization path regression: a resolver-only binding on an EMPTY
    collection (e.g. a cart with no items after a target reset) used to block
    with BLOCKED_MISSING_BINDING because auto-fixture required a declared owner
    actor. The campaign actors are the candidate pool; the runtime picker
    prefers the control actor, the disposable create runs, and the created id
    becomes the binding value.
    """
    import ai_test_asset_center.experiment_runtime_support as runtime_support
    import ai_test_asset_center.sandbox_write_executor as sandbox_executor

    new_item_id = "c8f0aaaa-1111-2222-3333-444455556666"
    cart_state: list[dict] = []  # reset target: cart is empty

    def mock_http_request(method, url, headers=None, body=None, timeout=10.0, token=""):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1]
        if method == "GET" and path == "/api/cart/items":
            return {"status": 200, "body": list(cart_state), "headers": {}, "error": ""}
        if method == "POST" and path == "/api/cart/items":
            item = {
                "id": new_item_id,
                "user_id": "buyer-user-id",
                "sku": (body or {}).get("sku", "SKU-PHONE-001"),
                "qty": (body or {}).get("qty", 1),
                "price_snapshot": "6999.00",
                "selected": True,
                "created_at": "2026-08-01T00:00:00Z",
            }
            cart_state.append(item)
            return {"status": 201, "body": item, "headers": {}, "error": ""}
        if method == "DELETE" and path.startswith("/api/cart/items/"):
            cart_state[:] = [
                row for row in cart_state if row["id"] != path.rsplit("/", 1)[-1]
            ]
            return {"status": 200, "body": {"ok": True}, "headers": {}, "error": ""}
        return {"status": 404, "body": {"error": "no route"}, "headers": {}, "error": ""}

    def mock_sandbox_allowed(*args, **kwargs):
        return True, "test"

    monkeypatch.setattr(runtime_support, "_http_request", mock_http_request)
    monkeypatch.setattr(sandbox_executor, "_http_request", mock_http_request)
    monkeypatch.setattr(sandbox_executor, "sandbox_write_allowed", mock_sandbox_allowed)

    exp = {
        "experiment_id": "exp-cart",
        "obligation_id": "obl-cart",
        "source_refs": [],
        "assertions": [],
        "fixture_dag": {
            "nodes": [
                {"node_id": "bind-id", "kind": "runtime_read_binding", "target": "id"}
            ],
            "setup_order": ["bind-id"],
        },
        "binding_plan": [
            {
                "target": "id",
                "target_path": "/api/cart/items/{id}",
                "status": "runtime_resolvable",
                "source_priority": "same_actor_list_read",
                "resolver_operations": [
                    {
                        "operation_ref": "cart_list",
                        "method": "GET",
                        "path": "/api/cart/items",
                    }
                ],
            }
        ],
        "control_plan": [
            {"actor_ref": "buyer", "operation_ref": "cart_patch", "body": {"qty": 2}}
        ],
        "treatment_plan": [],
    }
    binding_plan = {
        item["target"]: item
        for item in exp["binding_plan"]
        if isinstance(item, dict) and item.get("target")
    }
    actors = {
        "buyer": {
            "id": "buyer",
            "role": "customer",
            "credential_secret_ref": "secret:buyer",
        },
        "admin": {"id": "admin", "role": "admin", "credential_secret_ref": "secret:admin"},
    }

    result = materialize_experiment_fixtures(
        exp=exp,
        eid="exp-cart",
        oid="obl-cart",
        resolved_campaign_id="campaign-1",
        resolved_execution_id="execution-1",
        started=0.0,
        actors=actors,
        ops=_cart_collection_operations(),
        tokens={"secret:buyer": "token-buyer", "secret:admin": "token-admin"},
        binding_plan=binding_plan,
        resolver_actor_ref="buyer",
        resolver_token="token-buyer",
        activation_requirements={"actor": ["buyer"], "fixture": []},
        root=tmp_path,
        project="benchmark_mall",
        base_url="http://localhost:8080",
        runtime_contract={"environment_kind": "test"},
        campaign_id="campaign-1",
    )

    assert result["status"] == "ready", result
    assert result["runtime_bindings"].get("id") == new_item_id
    assert any(
        row.get("kind") == "runtime_read_binding" and row.get("status") == "resolved"
        for row in result["fixture_receipts"]
    )
    # The disposable fixture ran a real create and left the cart observable.
    assert len(cart_state) == 1


def test_runtime_binding_prefers_entity_that_differs_from_declared_mutation() -> None:
    binding = _select_runtime_binding(
        [
            {"id": "already-disabled", "status": "DISABLED"},
            {"id": "active-user", "status": "ACTIVE"},
        ],
        "/api/users/{id}/status",
        preferred_body={"status": "DISABLED"},
    )

    assert binding["id"] == "active-user"


def test_isolation_compiles_source_grounded_owned_fixture_proof() -> None:
    experiment = compile_experiment_for_obligation(
        _isolation_obligation(),
        behavior_ir=_isolation_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    identity_binding = next(
        row for row in experiment["binding_plan"] if row.get("target") == "id"
    )
    assert identity_binding["force_fixture_setup"] is True
    assert identity_binding["fixture_setup"]["actor_refs"] == ["actor-owner"]
    fixture_proof = next(
        row
        for row in experiment["binding_plan"]
        if row.get("fixture_id") == "owned_resource"
    )
    assert fixture_proof["status"] == "fixture_proof"
    assert fixture_proof["owner_actor_ref"] == "actor-owner"
    assert fixture_proof["binding_target"] == "id"
    dag = build_fixture_dag_for_experiment(experiment, behavior_ir=_isolation_ir())
    assert dag["status"] == "READY"
    ownership_node = next(
        node for node in dag["nodes"] if node.get("kind") == "ownership_fixture_proof"
    )
    assert ownership_node["owner_actor_ref"] == "actor-owner"
    assert ownership_node["binding_target"] == "id"
    assert ownership_node["requires_read_proof"] is True


def test_isolation_executor_forces_owned_fixture_and_emits_ownership_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    experiment = compile_experiment_for_obligation(
        _isolation_obligation(),
        behavior_ir=_isolation_ir(),
        environment_type="test",
    )
    experiment["fixture_dag"] = build_fixture_dag_for_experiment(
        experiment,
        behavior_ir=_isolation_ir(),
    )
    governed_phases: list[str] = []

    def http_request(method, url, *, token="", body=None):
        path = url.removeprefix("http://target.invalid")
        if path == "/resources":
            return {
                "status": 200,
                "body": [],
                "headers": {"content-type": "application/json"},
            }
        assert path == "/resources/r-owned"
        return {
            "status": 200,
            "body": {"id": "r-owned", "name": "source-declared"},
            "headers": {"content-type": "application/json"},
        }

    def governed_write(**kwargs):
        governed_phases.append(kwargs["operation_phase"])
        if kwargs["operation_phase"] == "experiment_fixture_setup":
            assert kwargs["actor_identity"] == "public"
            assert kwargs["path"] == "/resources"
            return {
                "accepted": True,
                "status": "executed",
                "method": kwargs["method"],
                "path": kwargs["path"],
                "before": {"status": 200, "body": [{"id": "existing"}]},
                "write": {"status": 201, "body": {"id": "r-owned"}},
                "after": {
                    "status": 200,
                    "body": [{"id": "existing"}, {"id": "r-owned"}],
                },
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "fixture_setup", "path": kwargs["path"]},
            }
        assert kwargs["operation_phase"] == "experiment_fixture_cleanup"
        assert kwargs["path"] == "/resources/r-owned"
        return {
            "accepted": True,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "r-owned"}},
            "write": {"status": 204, "body": {}},
            "after": {"status": 404, "body": {}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "fixture_cleanup", "path": kwargs["path"]},
        }

    _patch_http_request(
        monkeypatch,
        http_request,
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=_isolation_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-isolation",
        actor_tokens={},
    )

    assert result["status"] == "EXECUTED"
    assert governed_phases == [
        "experiment_fixture_setup",
        "experiment_fixture_cleanup",
    ]
    ownership = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "resource_ownership"
    )
    assert ownership["status"] == "OBSERVED"
    assert ownership["evidence"]["owner_actor_ref_fingerprint"]
    assert ownership["evidence"]["resource_identity_fingerprint"]
    assert result["cleanup_failures"] == 0
    assert result["finding"] is not None


def test_fixture_cleanup_runs_after_experiment_write_compensations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    ir = _isolation_ir()
    ir["operations"].extend([
        {
            "id": "op-reserve",
            "operation_id": "reserve_capacity",
            "method": "POST",
            "path": "/capacity/reserve",
            "read_write": "write",
        },
        {
            "id": "op-release",
            "operation_id": "release_capacity",
            "method": "POST",
            "path": "/capacity/release",
            "read_write": "write",
        },
        {
            "id": "op-capacity-state",
            "operation_id": "get_capacity",
            "method": "GET",
            "path": "/capacity/reserve",
            "read_write": "read",
        },
    ])
    ir["relations"].append({
        "kind": "compensates",
        "source": "op-release",
        "target": "op-reserve",
    })
    experiment = compile_experiment_for_obligation(
        _isolation_obligation(),
        behavior_ir=ir,
        environment_type="test",
    )
    experiment["fixture_dag"] = build_fixture_dag_for_experiment(
        experiment,
        behavior_ir=ir,
    )
    experiment["control_plan"] = [{
        "step_id": "control_1",
        "operation_ref": "op-reserve",
        "actor_ref": "actor-owner",
        "body": {"amount": 1},
    }]
    experiment["treatment_plan"] = [{
        "step_id": "treatment_1",
        "operation_ref": "op-reserve",
        "actor_ref": "actor-viewer",
        "body": {"amount": 1},
    }]
    experiment["cleanup_plan"] = [{
        "action": "source_declared_compensation",
        "operation_ref": "op-release",
        "compensates_operation_ref": "op-reserve",
        "path": "/capacity/release",
        "method": "POST",
        "body_from_original_request": True,
    }]
    experiment["safety_contract"] = {
        **experiment.get("safety_contract", {}),
        "governed_write": True,
    }
    experiment["write_reversibility_proof"] = build_reversibility_proof(
        primary_operation_ref="op-reserve",
        primary_method="POST",
        primary_path="/capacity/reserve",
        cleanup_plan=experiment["cleanup_plan"],
        source_refs=experiment.get("source_refs") or [],
        behavior_ir=ir,
        experiment=experiment,
    )
    phases: list[str] = []
    capacity = 2

    def governed_write(**kwargs):
        nonlocal capacity
        phase = kwargs["operation_phase"]
        phases.append(phase)
        if phase == "experiment_fixture_setup":
            return {
                "accepted": True,
                "status": "executed",
                "method": kwargs["method"],
                "path": kwargs["path"],
                "before": {"status": 200, "body": []},
                "write": {"status": 201, "body": {"id": "r-owned"}},
                "after": {"status": 200, "body": [{"id": "r-owned"}]},
                "audit_path": "audit.jsonl",
                "audit_record": {"phase": phase},
            }
        if phase in {"experiment_control", "experiment_treatment"}:
            before = capacity
            capacity -= 1
            return {
                "accepted": True,
                "before": {"status": 200, "body": {"value": before}},
                "write": {"status": 200, "body": {"value": capacity}},
                "after": {"status": 200, "body": {"value": capacity}},
                "audit_path": "audit.jsonl",
                "audit_record": {"phase": phase},
            }
        if phase == "experiment_cleanup":
            before = capacity
            capacity += 1
            return {
                "accepted": True,
                "before": {"status": 200, "body": {"value": before}},
                "write": {"status": 200, "body": {"value": capacity}},
                "after": {"status": 200, "body": {"value": capacity}},
                "audit_path": "audit.jsonl",
                "audit_record": {"phase": phase},
            }
        assert phase == "experiment_fixture_cleanup"
        return {
            "accepted": True,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "r-owned"}},
            "write": {"status": 204, "body": {}},
            "after": {"status": 404, "body": {}},
            "audit_path": "audit.jsonl",
            "audit_record": {"phase": phase},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-global-cleanup-order",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_NON_REVERSIBLE_WRITE"
    assert "missing_cleanup_for_steps" in result["detail"]
    assert result.get("cleanup_failures", 0) == 0
    assert phases == []


def test_empty_patch_without_source_declared_body_is_blocked() -> None:
    ir = {
        "operations": [
            {
                "id": "op-update-settings",
                "method": "PATCH",
                "path": "/settings",
                "read_write": "write",
                "request_schema": {},
                "request_example": {},
            },
            {
                "id": "op-read-settings",
                "method": "GET",
                "path": "/settings",
                "read_write": "read",
            },
        ],
        "actors": [
            {"id": "actor-owner", "role": "public", "account_ref": "owner"},
            {"id": "actor-viewer", "role": "public", "account_ref": "viewer"},
        ],
        "entities": [{
            "id": "entity-settings",
            "name": "settings",
            "fields": ["id", "selected", "quota", "updated_at"],
        }],
        "relations": [
            {
                "id": "rel-update-settings",
                "relation_type": "transitions",
                "operation_ref": "op-update-settings",
                "from_ref": "op-update-settings",
                "to_ref": "entity-settings",
            },
            {
                "id": "rel-read-settings",
                "relation_type": "observes",
                "operation_ref": "op-read-settings",
                "from_ref": "op-read-settings",
                "to_ref": "entity-settings",
            },
        ],
        "conflicts": [],
    }
    obligation = {
        "obligation_id": "obl-runtime-patch",
        "risk_family": "authorization",
        "property": {
            "template": "authorization_control_treatment",
            "control_actor_ref": "actor-owner",
            "treatment_actor_ref": "actor-viewer",
            "operation_ref": "op-update-settings",
            "require_same_resource": True,
        },
        "required_operations": ["op-update-settings"],
        "required_actors": ["actor-owner", "actor-viewer"],
        "required_fixtures": [],
        "required_observers": ["http_response", "actor_identity"],
        "cleanup_requirement": {"required": True, "mode": "snapshot_restore"},
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"] == {
        "status": "BLOCKED",
        # A PATCH with no source-declared request body is a source-bound failure
        # before reversibility is even evaluated: the mutation fields must come
        # from source material, never be invented. This replaced the older
        # "field_snapshot_restore_no_writable_fields" classification.
        "reason_code": "BLOCKED_MISSING_BINDING",
        "detail": "source_declared_request_body_missing:op-update-settings",
    }


def test_authorization_collection_read_forces_source_declared_control_fixture() -> None:
    behavior_ir = {
        "operations": [
            {
                "id": "op-list-cart",
                "operation_id": "list_cart_items",
                "method": "GET",
                "path": "/cart/items",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "kind": "api_operation"}],
            },
            {
                "id": "op-add-cart",
                "operation_id": "add_cart_item",
                "method": "POST",
                "path": "/cart/items",
                "read_write": "write",
                "request_example": {"sku": "SKU-1", "qty": 1},
                "source_refs": [{"source_id": "api", "kind": "api_operation"}],
            },
            {
                "id": "op-delete-cart",
                "operation_id": "delete_cart_item",
                "method": "DELETE",
                "path": "/cart/items/{id}",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "kind": "api_operation"}],
            },
        ],
        "actors": [
            {
                "id": "actor-buyer",
                "role": "buyer",
                "account_ref": "buyer@example.test",
                "credential_secret_ref": "secret_ref:test_accounts:buyer",
                "account_status": "active",
                "runtime_bound": True,
            },
            {
                "id": "actor-warehouse",
                "role": "warehouse",
                "account_ref": "warehouse@example.test",
                "credential_secret_ref": "secret_ref:test_accounts:warehouse",
                "account_status": "active",
                "runtime_bound": True,
            },
        ],
        "relations": [
            {
                "id": "rel-buyer-read-cart",
                "relation_type": "permits",
                "from_ref": "actor-buyer",
                "to_ref": "op-list-cart",
                "operation_ref": "op-list-cart",
                "actor_ref": "actor-buyer",
            },
            {
                "id": "rel-buyer-add-cart",
                "relation_type": "permits",
                "from_ref": "actor-buyer",
                "to_ref": "op-add-cart",
                "operation_ref": "op-add-cart",
                "actor_ref": "actor-buyer",
            },
            {
                "id": "rel-warehouse-deny-cart",
                "relation_type": "denies",
                "from_ref": "actor-warehouse",
                "to_ref": "op-list-cart",
                "operation_ref": "op-list-cart",
                "actor_ref": "actor-warehouse",
            },
            {
                "id": "rel-add-produces-cart",
                "relation_type": "produces",
                "from_ref": "op-add-cart",
                "to_ref": "entity-cart-item",
                "operation_ref": "op-add-cart",
            },
            {
                "id": "rel-list-observes-cart",
                "relation_type": "observes",
                "from_ref": "op-list-cart",
                "to_ref": "entity-cart-item",
                "operation_ref": "op-list-cart",
            },
            {
                "id": "rel-delete-consumes-cart",
                "relation_type": "consumes",
                "from_ref": "op-delete-cart",
                "to_ref": "entity-cart-item",
                "operation_ref": "op-delete-cart",
            },
            {
                "id": "rel-delete-compensates-add",
                "relation_type": "compensates",
                "from_ref": "op-delete-cart",
                "to_ref": "op-add-cart",
                "operation_ref": "op-delete-cart",
            },
        ],
    }
    obligation = {
        "obligation_id": "obl-cart-read-auth",
        "risk_family": "authorization",
        "property": {
            "template": "authorization_control_treatment",
            "control_actor_ref": "actor-buyer",
            "treatment_actor_ref": "actor-warehouse",
            "operation_ref": "op-list-cart",
            "require_same_resource": True,
        },
        "required_operations": ["op-list-cart"],
        "required_actors": ["actor-buyer", "actor-warehouse"],
        "required_observers": ["http_response", "actor_identity"],
        "cleanup_requirement": {"required": False},
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    fixture_binding = next(
        row
        for row in experiment["binding_plan"]
        if row.get("required_fixture_id") == "control_resource"
    )
    assert fixture_binding["force_fixture_setup"] is True
    assert fixture_binding["fixture_owner_actor_ref"] == "actor-buyer"
    assert fixture_binding["fixture_setup"]["operation_ref"] == "op-add-cart"
    assert fixture_binding["fixture_setup"]["cleanup_operations"] == [{
        "operation_ref": "op-delete-cart",
        "method": "DELETE",
        "path": "/cart/items/{id}",
    }]

    dag = build_fixture_dag_for_experiment(experiment, behavior_ir=behavior_ir)

    assert dag["status"] == "READY", dag
    assert any(
        node["kind"] == "runtime_read_binding"
        and node["target"] == fixture_binding["target"]
        and node["constructible"] is True
        for node in dag["nodes"]
    )


def test_runtime_mutation_block_is_blocked_before_transport_without_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [
            {
                "id": "op-patch",
                "method": "PATCH",
                "path": "/settings",
                "read_write": "write",
                "request_example": {"selected": False},
            },
            {
                "id": "op-read",
                "method": "GET",
                "path": "/settings",
                "read_write": "read",
            },
        ],
        "actors": [{"id": "actor-control", "role": "public"}],
    }
    experiment = {
        "experiment_id": "exp-runtime-block",
        "obligation_id": "obl-runtime-block",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [{
            "step_id": "control",
            "operation_ref": "op-patch",
            "actor_ref": "actor-control",
            "runtime_body_plan": {
                "schema_version": "qualibug.source-observed-mutation-plan.v1",
                "candidate_fields": ["selected"],
            },
        }],
        "treatment_plan": [{
            "step_id": "treatment",
            "operation_ref": "op-patch",
            "actor_ref": "actor-control",
            "runtime_body_plan": {
                "schema_version": "qualibug.source-observed-mutation-plan.v1",
                "candidate_fields": ["selected"],
            },
        }],
        "cleanup_plan": [{
            "action": "restore_before_snapshot",
            "mode": "snapshot_restore",
            "operation_ref": "op-patch",
            "method": "PATCH",
            "path": "/settings",
        }],
        "safety_contract": {"governed_write": True},
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "observers": [{"observer_id": "http_response"}],
        "assertions": [],
    }
    experiment["write_reversibility_proof"] = build_reversibility_proof(
        primary_operation_ref="op-patch",
        primary_method="PATCH",
        primary_path="/settings",
        cleanup_plan=experiment["cleanup_plan"],
        behavior_ir=behavior_ir,
        experiment=experiment,
    )
    governed_calls: list[str] = []

    def governed_write(**kwargs):
        governed_calls.append(kwargs["operation_phase"])
        return {
            "accepted": False,
            "status": "blocked",
            "reason": "runtime_mutation_target_ambiguous",
            "before": {"status": 200, "body": []},
            "write": {
                "status": 0,
                "body": "",
                "error": "runtime_mutation_target_ambiguous",
            },
            "after": {},
            "runtime_body_receipt": {
                "status": "BLOCKED",
                "reason_code": "runtime_mutation_target_ambiguous",
            },
            "audit_path": "audit.jsonl",
            "audit_record": {"phase": kwargs["operation_phase"]},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-runtime-block",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    # An experiment-level snapshot restore covers both writes to the same
    # operation. The next independent gate is the source-observed mutation
    # materialization, which blocks before transport when its target is
    # ambiguous.
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert result["detail"] == "runtime_mutation_target_ambiguous"
    assert result.get("cleanup_failures", 0) == 0
    # The governed executor is invoked to validate the source-observed mutation
    # plan and returns BLOCKED before target transport; the hook call itself is
    # not evidence that an HTTP write was sent.
    assert governed_calls == ["experiment_control", "experiment_treatment"]


def test_unresolved_body_placeholder_blocks_before_any_write_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [{
            "id": "op-create",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
        }, {
            "id": "op-list",
            "method": "GET",
            "path": "/resources",
            "read_write": "read",
        }, {
            "id": "op-delete",
            "method": "DELETE",
            "path": "/resources/{id}",
            "read_write": "write",
        }],
        "actors": [{"id": "actor-control", "role": "public"}],
    }
    experiment = {
        "experiment_id": "exp-body-binding-block",
        "obligation_id": "obl-body-binding-block",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [{
            "step_id": "control",
            "operation_ref": "op-create",
            "actor_ref": "actor-control",
            "body": {"resource_id": "<missing_id>"},
        }],
        "treatment_plan": [{
            "step_id": "treatment",
            "operation_ref": "op-create",
            "actor_ref": "actor-control",
            "body": {"resource_id": "<missing_id>"},
        }],
        "cleanup_plan": [{
            "operation_ref": "op-delete",
            "mode": "reverse_order",
            "method": "DELETE",
            "path": "/resources/{id}",
        }],
        "safety_contract": {"governed_write": True},
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
            {"observer_id": "entity_state"},
        ],
        "assertions": [],
    }
    experiment["write_reversibility_proof"] = build_reversibility_proof(
        primary_operation_ref="op-create",
        primary_method="POST",
        primary_path="/resources",
        cleanup_plan=experiment["cleanup_plan"],
        behavior_ir=behavior_ir,
        experiment=experiment,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        lambda **_kwargs: pytest.fail("unresolved body binding reached transport"),
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-body-binding-block",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    # V1.7 multi-write coverage gate fires before body-binding resolution: the
    # single unscoped cleanup cannot cover control+treatment writes, so the
    # experiment blocks on the cleanup coverage violation before transport.
    assert result["reason_code"] == "BLOCKED_NON_REVERSIBLE_WRITE"
    assert "missing_cleanup_for_steps" in result["detail"]


def test_unresolved_read_path_placeholder_blocks_before_target_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [{
            "id": "op-read",
            "method": "GET",
            "path": "/resources/{id}",
            "read_write": "read",
        }, {
            "id": "op-list",
            "method": "GET",
            "path": "/resources",
            "read_write": "read",
        }],
        "actors": [{"id": "actor-reader", "role": "public"}],
    }
    experiment = {
        "experiment_id": "exp-path-binding-block",
        "obligation_id": "obl-path-binding-block",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment",
            "operation_ref": "op-read",
            "actor_ref": "actor-reader",
        }],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "target_path": "/resources/{id}",
            "resolver_operations": [{
                "operation_ref": "op-list",
                "method": "GET",
                "path": "/resources",
            }],
        }],
        "fixture_dag": {
            "status": "READY",
            "nodes": [{"node_id": "bind-id", "kind": "runtime_read_binding", "target": "id"}],
            "setup_order": ["bind-id"],
        },
        "observers": [{"observer_id": "http_response"}],
        "assertions": [],
    }
    requested_urls: list[str] = []

    def http_request(method: str, url: str, **_kwargs):
        requested_urls.append(url)
        if url.endswith("/resources/{id}"):
            pytest.fail("unresolved path binding reached target transport")
        return {"status": 200, "body": [], "headers": {}, "duration_ms": 1}

    _patch_http_request(monkeypatch, http_request)

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-path-binding-block",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "runtime_read_binding_unresolved:id" in result["detail"]
    assert requested_urls == ["http://target.invalid/resources"]


def test_owner_scoped_read_binding_precedes_disposable_fixture_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [{
            "id": "op-read",
            "method": "GET",
            "path": "/resources/{id}",
            "read_write": "read",
        }, {
            "id": "op-list",
            "method": "GET",
            "path": "/resources",
            "read_write": "read",
        }, {
            "id": "op-create",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
            "request_example": {"name": "source-declared"},
        }, {
            "id": "op-delete",
            "method": "DELETE",
            "path": "/resources/{id}",
            "read_write": "write",
        }],
        "actors": [
            {"id": "actor-owner", "role": "public"},
            {"id": "actor-viewer", "role": "public"},
        ],
    }
    experiment = {
        "experiment_id": "exp-owner-read-first",
        "obligation_id": "obl-owner-read-first",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [{
            "step_id": "control_1",
            "operation_ref": "op-read",
            "actor_ref": "actor-owner",
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "operation_ref": "op-read",
            "actor_ref": "actor-viewer",
        }],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "target_path": "/resources/{id}",
            "source_priority": "same_actor_list_read",
            "resolver_operations": [{
                "operation_ref": "op-list",
                "method": "GET",
                "path": "/resources",
            }],
            "fixture_setup": {
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/resources",
                "actor_refs": ["actor-owner"],
                "body_template": {"name": "source-declared"},
                "cleanup_operations": [{
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                }],
            },
            "force_fixture_setup": True,
            "fixture_owner_actor_ref": "actor-owner",
        }],
        "fixture_dag": {
            "status": "READY",
            "nodes": [{
                "node_id": "bind-id",
                "kind": "runtime_read_binding",
                "target": "id",
            }, {
                "node_id": "prove-owner",
                "kind": "ownership_fixture_proof",
                "binding_target": "id",
                "owner_actor_ref": "actor-owner",
            }],
            "setup_order": ["bind-id", "prove-owner"],
        },
        "observers": [{"observer_id": "http_response"}],
        "assertions": [],
    }
    requested_urls: list[str] = []

    def http_request(method: str, url: str, **_kwargs):
        requested_urls.append(url)
        if url.endswith("/resources"):
            return {
                "status": 200,
                "body": [{"id": "resource-1"}],
                "headers": {},
                "duration_ms": 1,
            }
        return {"status": 200, "body": {"id": "resource-1"}, "headers": {}, "duration_ms": 1}

    _patch_http_request(monkeypatch, http_request)
    _patch_governed_write(
        monkeypatch,
        lambda **_kwargs: pytest.fail("read-resolvable binding performed fixture write"),
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-owner-read-first",
        actor_tokens={},
    )

    binding = result["binding_materialization_receipts"][0]
    assert binding["status"] == "BOUND"
    assert binding["resolver_actor_ref"] == "actor-owner"
    assert binding["ownership_proof_status"] == "OBSERVED"
    assert requested_urls[0] == "http://target.invalid/resources"
    assert requested_urls.count("http://target.invalid/resources/resource-1") == 2
    assert result["cleanup_failures"] == 0
    assert not any(
        receipt.get("subject_id") == "fixture_cleanup:id"
        for receipt in result["contract_evidence_receipts"]
    )
    assert "fixture_cleanup:id" not in (
        result["oracle_verdict"]["activation_receipt"]["required"]["cleanup"]
    )


def test_sandbox_denial_stops_write_before_governed_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [{
            "id": "op-write",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
            "request_example": {"name": "source-declared"},
        }, {
            "id": "op-observe-resources",
            "method": "GET",
            "path": "/resources",
            "read_write": "read",
        }, {
            "id": "op-delete",
            "method": "DELETE",
            "path": "/resources/{id}",
            "read_write": "write",
        }],
        "actors": [{"id": "actor-writer", "role": "public"}],
    }
    experiment = {
        "experiment_id": "exp-sandbox-denied",
        "obligation_id": "obl-sandbox-denied",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "operation_ref": "op-write",
            "actor_ref": "actor-writer",
            "body": {"name": "source-declared"},
        }],
        "cleanup_plan": [{
            "operation_ref": "op-delete",
            "mode": "reverse_order",
            "method": "DELETE",
            "path": "/resources/{id}",
        }],
        "safety_contract": {"governed_write": True},
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "observers": [{"observer_id": "http_response"}],
        "assertions": [],
    }
    experiment["write_reversibility_proof"] = build_reversibility_proof(
        primary_operation_ref="op-write",
        primary_method="POST",
        primary_path="/resources",
        cleanup_plan=experiment["cleanup_plan"],
        behavior_ir=behavior_ir,
        experiment=experiment,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (False, "READ_ONLY_MODE"),
    )
    _patch_governed_write(
        monkeypatch,
        lambda **_kwargs: pytest.fail("sandbox-denied write reached governed transport"),
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-sandbox-denied",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_TARGET_POLICY"
    assert result["steps"][0]["status"] == "blocked_write"
    assert result["steps"][0]["reason"] == "READ_ONLY_MODE"


def test_unresolved_required_runtime_fixture_blocks_before_control_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [{
            "id": "op-read-collection",
            "method": "GET",
            "path": "/cart/items",
            "read_write": "read",
        }, {
            "id": "op-list-fixtures",
            "method": "GET",
            "path": "/fixtures",
            "read_write": "read",
        }],
        "actors": [
            {"id": "actor-owner", "role": "public"},
            {"id": "actor-viewer", "role": "public"},
        ],
    }
    experiment = {
        "experiment_id": "exp-required-fixture-binding-block",
        "obligation_id": "obl-required-fixture-binding-block",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [{
            "step_id": "control_1",
            "operation_ref": "op-read-collection",
            "actor_ref": "actor-owner",
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "operation_ref": "op-read-collection",
            "actor_ref": "actor-viewer",
        }],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "resolver_operations": [{
                "operation_ref": "op-list-fixtures",
                "method": "GET",
                "path": "/fixtures",
            }],
        }],
        "fixture_dag": {
            "status": "READY",
            "nodes": [{"node_id": "fix-required-id", "kind": "runtime_read_binding", "target": "id"}],
            "setup_order": ["fix-required-id"],
        },
        "activation_requirements": {
            "fixture": ["fix-required-id"],
            "control": ["control_1"],
            "treatment": ["treatment_1"],
            "actor": ["actor-owner", "actor-viewer"],
            "observer": ["http_response"],
        },
        "observers": [{"observer_id": "http_response"}],
        "assertions": [],
    }
    requested_urls: list[str] = []

    def http_request(method: str, url: str, **_kwargs):
        requested_urls.append(url)
        if url.endswith("/cart/items"):
            pytest.fail("control/treatment reached transport after required fixture binding failed")
        return {"status": 200, "body": [], "headers": {}, "duration_ms": 1}

    _patch_http_request(monkeypatch, http_request)

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-required-fixture-binding-block",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "runtime_read_binding_unresolved:id" in result["detail"]
    assert requested_urls == ["http://target.invalid/fixtures"]


def test_barrier_unresolved_body_placeholder_blocks_before_write_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    behavior_ir = {
        "operations": [{
            "id": "op-create",
            "method": "POST",
            "path": "/resources",
            "read_write": "write",
        }, {
            "id": "op-list",
            "method": "GET",
            "path": "/resources",
            "read_write": "read",
        }, {
            "id": "op-delete",
            "method": "DELETE",
            "path": "/resources/{id}",
            "read_write": "write",
        }],
        "actors": [{"id": "actor-control", "role": "public"}],
    }
    experiment = {
        "experiment_id": "exp-barrier-body-binding-block",
        "obligation_id": "obl-barrier-body-binding-block",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [{
            "step_id": "control",
            "operation_ref": "op-create",
            "actor_ref": "actor-control",
            "body": {"resource_id": "<missing_id>"},
            "barrier_group": "group-1",
            "barrier_participant": "control",
        }],
        "treatment_plan": [{
            "step_id": "treatment",
            "operation_ref": "op-create",
            "actor_ref": "actor-control",
            "body": {"resource_id": "<missing_id>"},
            "barrier_group": "group-1",
            "barrier_participant": "treatment",
        }],
        "cleanup_plan": [{
            "operation_ref": "op-delete",
            "mode": "reverse_order",
            "method": "DELETE",
            "path": "/resources/{id}",
        }],
        "safety_contract": {"governed_write": True},
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
            {"observer_id": "barrier_timeline"},
        ],
        "assertions": [],
    }
    experiment["write_reversibility_proof"] = build_reversibility_proof(
        primary_operation_ref="op-create",
        primary_method="POST",
        primary_path="/resources",
        cleanup_plan=experiment["cleanup_plan"],
        behavior_ir=behavior_ir,
        experiment=experiment,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        lambda **_kwargs: pytest.fail("unresolved barrier body reached write transport"),
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-barrier-body-binding-block",
        actor_tokens={},
    )

    assert result["status"] == "BLOCKED"
    # V1.7 multi-write coverage gate fires before body-binding resolution (see
    # test_unresolved_body_placeholder_blocks_before_any_write_transport).
    assert result["reason_code"] == "BLOCKED_NON_REVERSIBLE_WRITE"
    assert "missing_cleanup_for_steps" in result["detail"]
    assert result.get("cleanup_failures", 0) == 0


def test_single_participant_barrier_is_explicit_pretransport_block(tmp_path) -> None:
    observations: dict[str, object] = {}
    result = barrier_executor.execute_barrier_plans(
        control_plan=[{
            "step_id": "control-only",
            "operation_ref": "op-read",
            "actor_ref": "actor-control",
            "barrier_group": "group-incomplete",
            "barrier_participant": "control",
        }],
        treatment_plan=[],
        actors={"actor-control": {"id": "actor-control"}},
        ops={"op-read": {"id": "op-read", "method": "GET", "path": "/resources"}},
        tokens={},
        runtime_bindings={},
        activation_requirements={},
        eid="exp-incomplete-barrier",
        oid="obl-incomplete-barrier",
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        observations=observations,
    )

    assert observations["harness_error"] is True
    assert result["pre_transport_block_reasons"] == [
        "barrier_group_participant_count_invalid:group-incomplete:1"
    ]
    assert result["steps"][0]["status"] == "blocked_request"
    assert result["contract_evidence_receipts"][0]["status"] == "BLOCKED"


def test_accepted_fixture_without_identity_is_visible_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    experiment = compile_experiment_for_obligation(
        _isolation_obligation(),
        behavior_ir=_isolation_ir(),
        environment_type="test",
    )
    experiment["fixture_dag"] = build_fixture_dag_for_experiment(
        experiment,
        behavior_ir=_isolation_ir(),
    )
    _patch_governed_write(
        monkeypatch,
        lambda **_kwargs: {
            "accepted": True,
            "status": "executed",
            "before": {"status": 200, "body": []},
            "write": {"status": 201, "body": {}},
            "after": {"status": 200, "body": [{"name": "unidentified"}]},
        },
    )
    def binding_read_only(method, url, **_kwargs):
        if url == "http://target.invalid/resources":
            return {"status": 200, "body": [], "headers": {}, "duration_ms": 1}
        pytest.fail("experiment must stop before probes")

    _patch_http_request(monkeypatch, binding_read_only)

    result = execute_one_experiment(
        experiment,
        behavior_ir=_isolation_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-fixture-failure",
        actor_tokens={},
    )

    assert result["status"] == "HARNESS_FAILURE"
    assert result["reason_code"] == "FIXTURE_SETUP_IDENTITY_UNRESOLVED"
    assert result["cleanup_failures"] == 1
    assert result["finding"] is None


def test_validation_compiles_source_schema_control_and_single_mutation() -> None:
    behavior_ir = _idempotency_ir()
    behavior_ir["operations"][0]["request_schema"] = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["externalRef"],
                    "properties": {
                        "externalRef": {"type": "string", "minLength": 1},
                        "value": {"type": "integer", "minimum": 0},
                    },
                },
                "example": {"externalRef": "source-ref", "value": 1},
            },
        },
    }
    obligation = {
        "obligation_id": "obl-validation",
        "risk_family": "validation",
        "property": {
            "operation_ref": "op-create",
            "actor_ref": "actor-writer",
            "template": "invariant_validation",
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-writer"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["control_plan"][0]["body"] == {
        "externalRef": "source-ref",
        "value": 1,
    }
    assert experiment["treatment_plan"][0]["body"] == {"value": 1}
    assert experiment["treatment_plan"][0]["mutation"] == {
        "json_path": "$.externalRef",
        "constraint": "required",
        "source": "request_schema",
    }
    assert experiment["assertions"][0]["kind"] == "http_status_class"
    assert experiment["assertions"][0]["expected_class"] == 4


def test_validation_write_compiles_entity_state_observer_when_read_observer_exists() -> None:
    behavior_ir = _idempotency_ir()
    behavior_ir["operations"][0]["request_schema"] = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["externalRef"],
                    "properties": {
                        "externalRef": {"type": "string", "minLength": 1},
                        "value": {"type": "integer", "minimum": 0},
                    },
                },
                "example": {"externalRef": "source-ref", "value": 1},
            },
        },
    }
    obligation = {
        "obligation_id": "obl-validation-entity-state",
        "risk_family": "validation",
        "property": {
            "operation_ref": "op-create",
            "actor_ref": "actor-writer",
            "template": "invariant_validation",
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-writer"],
        "required_observers": ["http_response", "entity_state"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    entity_state = next(
        observer
        for observer in experiment["observers"]
        if observer["observer_id"] == "entity_state"
    )
    assert entity_state["resolver_operations"][0]["path"] == "/resources"


def test_state_snapshot_observers_compile_with_source_declared_read_observer() -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-state-snapshots"
    obligation["risk_family"] = "state"
    obligation["required_observers"] = [
        "http_response",
        "before_state",
        "after_state",
        "final_state",
    ]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "from_state": "PENDING",
        "to_state": "PAID",
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    snapshot_observers = {
        observer["observer_id"]: observer
        for observer in experiment["observers"]
        if observer["observer_id"] in {"before_state", "after_state", "final_state"}
    }
    assert set(snapshot_observers) == {"before_state", "after_state", "final_state"}
    assert all(
        observer["resolver_operations"][0]["path"] == "/resources"
        for observer in snapshot_observers.values()
    )


def test_concurrency_obligation_compiles_final_state_and_barrier_timeline() -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-concurrency-barrier"
    obligation["risk_family"] = "concurrency"
    obligation["required_observers"] = ["final_state", "barrier_timeline"]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "template": "concurrent_final_invariant",
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    observer_ids = {observer["observer_id"] for observer in experiment["observers"]}
    assert {"final_state", "barrier_timeline"} <= observer_ids
    assert len(experiment["control_plan"]) == 1
    assert len(experiment["treatment_plan"]) == 1
    control = experiment["control_plan"][0]
    treatment = experiment["treatment_plan"][0]
    assert control["protocol_step"] == "concurrent_write"
    assert treatment["protocol_step"] == "concurrent_write"
    assert control["barrier_group"] == treatment["barrier_group"]
    assert control["barrier_participant"] != treatment["barrier_participant"]
    assert control["body"] == treatment["body"]
    assert control["body"] is not treatment["body"]
    final_state = next(
        observer
        for observer in experiment["observers"]
        if observer["observer_id"] == "final_state"
    )
    assert final_state["resolver_operations"][0]["path"] == "/resources"


def test_concurrency_executor_releases_control_and_treatment_with_barrier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-concurrency-runtime"
    obligation["risk_family"] = "concurrency"
    obligation["required_observers"] = ["final_state", "barrier_timeline"]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "template": "concurrent_final_invariant",
    }
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )
    experiment["fixture_dag"] = {"status": "READY", "nodes": [], "setup_order": []}
    transport_barrier = threading.Barrier(2)
    write_ids: dict[str, str] = {
        "experiment_control": "r-control",
        "experiment_treatment": "r-treatment",
    }
    submitted_bodies: list[dict] = []

    def governed_write(**kwargs):
        phase = kwargs["operation_phase"]
        if phase in {"experiment_control", "experiment_treatment"}:
            submitted_bodies.append(dict(kwargs["body"]))
            transport_barrier.wait(timeout=2)
            write_id = write_ids[phase]
            return {
                "accepted": True,
                "status": "executed",
                "method": kwargs["method"],
                "path": kwargs["path"],
                "before": {"status": 200, "body": [{"id": "seed", "status": "PENDING"}]},
                "write": {"status": 201, "body": {"id": write_id, "status": "PAID"}},
                "after": {"status": 200, "body": [{"id": write_id, "status": "PAID"}]},
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": phase, "id": write_id},
            }
        assert phase == "experiment_cleanup"
        return {
            "accepted": True,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": kwargs["path"].rsplit("/", 1)[-1]}},
            "write": {"status": 204, "body": {}},
            "after": {"status": 404, "body": {}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": phase, "path": kwargs["path"]},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._run_http_step",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("concurrency request semantics must not be inferred")
        ),
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=_idempotency_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-concurrency",
        actor_tokens={},
    )

    barrier = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "barrier_timeline"
    )
    assert barrier["status"] == "OBSERVED"
    assert barrier["evidence"]["participant_count"] == 2
    assert barrier["evidence"]["barrier_released"] is True
    assert {
        step["protocol_step"]
        for step in result["steps"]
        if step.get("phase") in {"control", "treatment"}
    } == {"concurrent_write"}
    assert submitted_bodies == [
        experiment["control_plan"][0]["body"],
        experiment["treatment_plan"][0]["body"],
    ] or submitted_bodies == [
        experiment["treatment_plan"][0]["body"],
        experiment["control_plan"][0]["body"],
    ]


def test_source_invariant_without_conservation_terms_is_blocked() -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-source-invariant"
    obligation["risk_family"] = "conservation"
    obligation["required_observers"] = ["typed_assertion", "source_invariant"]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "invariant_ref": "inv-balance",
        "expression": {"kind": "conservation"},
    }
    obligation["source_refs"] = [
        {"source_id": "requirements", "locator": "rule:balance"},
    ]

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"] == {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
        "detail": "conservation_requires_non_empty_equation_terms",
    }


def test_conservation_obligation_compiles_entity_state_and_conservation_write_protocol() -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-conservation-protocol"
    obligation["risk_family"] = "conservation"
    obligation["required_observers"] = ["typed_assertion", "source_invariant", "entity_state"]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "template": "invariant_conservation",
        "invariant_ref": "inv-balance",
        "expression": {
            "kind": "conservation",
            "equation": {
                "operator": "unchanged_sum",
                "terms": ["value", "reserved"],
            },
        },
    }
    obligation["source_refs"] = [
        {"source_id": "requirements", "locator": "rule:balance"},
    ]

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["control_plan"] == []
    assert experiment["treatment_plan"][0]["protocol_step"] == "conservation_write"
    assert experiment["treatment_plan"][0]["body"] == {"externalRef": "source-ref", "value": 1}
    assert {
        observer["observer_id"]
        for observer in experiment["observers"]
    } >= {"typed_assertion", "source_invariant", "entity_state"}
    assert experiment["assertions"][0]["kind"] == "conservation"
    assert experiment["assertions"][0]["require_control"] is False


def test_entity_state_observer_emits_conservation_values_from_governed_snapshots() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{
                "assertion_id": "assert-conservation",
                "kind": "conservation",
                "equation": {
                    "operator": "unchanged_sum",
                    "terms": ["value", "reserved"],
                },
            }],
            "observers": [{"observer_id": "entity_state"}],
        },
        observations={
            "execution_steps": [{
                "phase": "treatment",
                "step_id": "treatment_1",
                "method": "POST",
                "operation_ref": "op-create",
                "governance_receipt": {
                    "before": {
                        "status": 200,
                        "body": {"id": "resource-1", "value": 100, "reserved": 0},
                    },
                    "after": {
                        "status": 200,
                        "body": {"id": "resource-1", "value": 80, "reserved": 20},
                    },
                },
            }],
        },
    )

    receipt = receipts[0]
    assert receipt["observer_id"] == "entity_state"
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["before_values"] == {"reserved": 0, "value": 100}
    assert receipt["evidence"]["after_values"] == {"reserved": 20, "value": 80}


def test_conservation_executor_evaluates_snapshot_values_through_contract_oracle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-conservation-runtime"
    obligation["risk_family"] = "conservation"
    obligation["required_observers"] = ["typed_assertion", "source_invariant", "entity_state"]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "template": "invariant_conservation",
        "invariant_ref": "inv-balance",
        "expression": {
            "kind": "conservation",
            "equation": {
                "operator": "unchanged_sum",
                "terms": ["value", "reserved"],
            },
        },
    }
    obligation["source_refs"] = [
        {"source_id": "requirements", "locator": "rule:balance"},
    ]
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )
    experiment["fixture_dag"] = {"status": "READY", "nodes": [], "setup_order": []}

    def governed_write(**kwargs):
        if kwargs["operation_phase"] == "experiment_treatment":
            return {
                "accepted": True,
                "status": "executed",
                "method": kwargs["method"],
                "path": kwargs["path"],
                "before": {
                    "status": 200,
                    "body": {"id": "resource-1", "value": 100, "reserved": 0},
                },
                "write": {"status": 201, "body": {"id": "resource-1"}},
                "after": {
                    "status": 200,
                    "body": {"id": "resource-1", "value": 80, "reserved": 20},
                },
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "treatment", "id": "resource-1"},
            }
        assert kwargs["operation_phase"] == "experiment_cleanup"
        return {
            "accepted": True,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "resource-1"}},
            "write": {"status": 204, "body": {}},
            "after": {"status": 404, "body": {}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=_idempotency_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-conservation",
        actor_tokens={},
    )

    assert result["status"] == "EXECUTED"
    assert result["cleanup_failures"] == 0
    assert result["oracle_verdict"]["status"] == "PROPERTY_HELD"
    assert result["oracle_verdict"]["assertions"][0]["status"] == "PASS"
    assert result["finding"] is None


def test_temporal_window_observer_emits_eventual_consistency_evidence() -> None:
    receipts = observe_experiment_requirements(
        {
            "assertions": [{
                "assertion_id": "assert-temporal",
                "kind": "eventual_consistency",
                "window_ms": 1000,
            }],
            "observers": [{"observer_id": "temporal_window"}],
        },
        observations={
            "temporal_timeline": [
                {"event": "trigger", "at_ms": 0, "status_code": 202},
                {"event": "final_observed", "at_ms": 250, "status_code": 200},
            ],
        },
    )

    receipt = receipts[0]
    assert receipt["observer_id"] == "temporal_window"
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["converged"] is True
    assert receipt["evidence"]["within_window"] is True
    assert receipt["evidence"]["elapsed_ms"] == 250


def test_temporal_obligation_compiles_temporal_write_protocol() -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-temporal-protocol"
    obligation["risk_family"] = "temporal"
    obligation["required_observers"] = [
        "typed_assertion",
        "source_invariant",
        "temporal_window",
    ]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "template": "invariant_temporal",
        "invariant_ref": "inv-eventual",
        "expression": {
            "kind": "temporal",
            "window_ms": 1000,
        },
    }
    obligation["source_refs"] = [
        {"source_id": "requirements", "locator": "rule:eventual"},
    ]

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert experiment["control_plan"] == []
    assert experiment["treatment_plan"][0]["protocol_step"] == "temporal_write"
    assert experiment["treatment_plan"][0]["body"] == {"externalRef": "source-ref", "value": 1}
    assert {
        observer["observer_id"]
        for observer in experiment["observers"]
    } >= {"typed_assertion", "source_invariant", "temporal_window"}
    assert experiment["assertions"][0]["kind"] == "eventual_consistency"
    assert experiment["assertions"][0]["window_ms"] == 1000


def test_temporal_executor_feeds_window_evidence_to_contract_oracle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    obligation = _idempotency_obligation()
    obligation["obligation_id"] = "obl-temporal-runtime"
    obligation["risk_family"] = "temporal"
    obligation["required_observers"] = [
        "typed_assertion",
        "source_invariant",
        "temporal_window",
    ]
    obligation["property"] = {
        "operation_ref": "op-create",
        "actor_ref": "actor-writer",
        "template": "invariant_temporal",
        "invariant_ref": "inv-eventual",
        "expression": {
            "kind": "temporal",
            "window_ms": 1000,
        },
    }
    obligation["source_refs"] = [
        {"source_id": "requirements", "locator": "rule:eventual"},
    ]
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=_idempotency_ir(),
        environment_type="test",
    )
    experiment["fixture_dag"] = {"status": "READY", "nodes": [], "setup_order": []}

    def governed_write(**kwargs):
        if kwargs["operation_phase"] == "experiment_treatment":
            return {
                "accepted": True,
                "status": "executed",
                "method": kwargs["method"],
                "path": kwargs["path"],
                "before": {"status": 200, "body": []},
                "write": {
                    "status": 202,
                    "body": {"id": "resource-1"},
                    "duration_ms": 250,
                },
                "after": {
                    "status": 200,
                    "body": [{"id": "resource-1", "status": "ready"}],
                },
                "audit_path": "sandbox_write_audit.jsonl",
                "audit_record": {"phase": "treatment", "id": "resource-1"},
            }
        assert kwargs["operation_phase"] == "experiment_cleanup"
        return {
            "accepted": True,
            "status": "executed",
            "method": kwargs["method"],
            "path": kwargs["path"],
            "before": {"status": 200, "body": {"id": "resource-1"}},
            "write": {"status": 204, "body": {}},
            "after": {"status": 404, "body": {}},
            "audit_path": "sandbox_write_audit.jsonl",
            "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_barrier_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_cleanup_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_plan_executor.sandbox_write_allowed",
        lambda **_kwargs: (True, ""),
    )
    _patch_governed_write(
        monkeypatch,
        governed_write,
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=_idempotency_ir(),
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-temporal",
        actor_tokens={},
    )

    assert result["status"] == "EXECUTED"
    temporal = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "temporal_window"
    )
    assert temporal["status"] == "OBSERVED"
    assert result["oracle_verdict"]["status"] == "PROPERTY_HELD"
    assert result["oracle_verdict"]["assertions"][0]["status"] == "PASS"


def test_write_effect_observer_uses_source_declared_body_bound_lookup() -> None:
    behavior_ir = {
        "operations": [
            {
                "id": "op-pay",
                "operation_id": "pay_order",
                "method": "POST",
                "path": "/api/payments/pay",
                "read_write": "write",
                "request_schema": {
                    "content": {
                        "application/json": {
                            "example": {
                                "orderId": "<order_id>",
                                "amount": 100,
                                "channel": "BALANCE",
                            },
                        },
                    },
                },
            },
            {
                "id": "op-read-payment-by-order",
                "operation_id": "read_payment_by_order",
                "method": "GET",
                "path": "/api/payments/order/{orderId}",
                "read_write": "read",
            },
            {
                "id": "op-read-order",
                "operation_id": "read_order",
                "method": "GET",
                "path": "/api/orders/{orderId}",
                "read_write": "read",
            },
            {
                "id": "op-list-orders",
                "operation_id": "list_orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
            },
            {
                "id": "op-void-payment",
                "operation_id": "void_payment_by_order",
                "method": "POST",
                "path": "/api/payments/order/{orderId}/void",
                "read_write": "write",
            },
        ],
        "actors": [
            {
                "id": "actor-writer",
                "role": "public",
                "account_status": "active",
            },
        ],
        "relations": [
            {
                "kind": "compensates",
                "source": "op-void-payment",
                "target": "op-pay",
            },
        ],
    }
    obligation = {
        "obligation_id": "obl-payment-idempotency",
        "risk_family": "idempotency",
        "property": {
            "operation_ref": "op-pay",
            "template": "idempotent_effect_cardinality",
            "actor_ref": "actor-writer",
        },
        "required_operations": ["op-pay"],
        "required_actors": ["actor-writer"],
        "required_observers": ["business_effect", "http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-void-payment",
            "mode": "reverse_order",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    business_effect = next(
        observer
        for observer in experiment["observers"]
        if observer["observer_id"] == "business_effect"
    )
    assert business_effect["resolver_operations"] == [
        {
            "operation_ref": "op-read-payment-by-order",
            "method": "GET",
            "path": "/api/payments/order/{orderId}",
        },
    ]


def test_validation_compiles_source_declared_type_mutation_without_required_fields() -> None:
    behavior_ir = _idempotency_ir()
    behavior_ir["operations"][0]["request_schema"] = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "externalRef": {"type": "string"},
                        "value": {"type": "integer"},
                    },
                },
                "example": {"externalRef": "source-ref", "value": 1},
            },
        },
    }
    obligation = {
        "obligation_id": "obl-validation-type",
        "risk_family": "validation",
        "property": {
            "operation_ref": "op-create",
            "actor_ref": "actor-writer",
            "template": "invariant_validation",
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-writer"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["control_plan"][0]["body"] == {
        "externalRef": "source-ref",
        "value": 1,
    }
    assert experiment["treatment_plan"][0]["body"] == {
        "externalRef": {},
        "value": 1,
    }
    assert experiment["treatment_plan"][0]["mutation"] == {
        "json_path": "$.externalRef",
        "constraint": "type:string",
        "source": "request_schema",
    }


def test_validation_targets_field_named_by_source_invariant() -> None:
    behavior_ir = _idempotency_ir()
    behavior_ir["operations"][0]["request_schema"] = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "externalRef": {"type": "string"},
                        "value": {"type": "integer"},
                    },
                },
                "example": {"externalRef": "source-ref", "value": 1},
            },
        },
    }
    obligation = {
        "obligation_id": "obl-validation-source-field",
        "risk_family": "validation",
        "property": {
            "operation_ref": "op-create",
            "actor_ref": "actor-writer",
            "template": "invariant_validation",
            "expression": {
                "kind": "business_rule",
                "operator": "must_hold",
                "raw": "`value` must be a positive integer",
            },
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-writer"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["treatment_plan"][0]["body"] == {
        "externalRef": "source-ref",
        "value": {},
    }
    assert experiment["treatment_plan"][0]["mutation"] == {
        "json_path": "$.value",
        "constraint": "type:integer",
        "source": "request_schema",
    }


def test_validation_write_compiles_with_response_only_observer() -> None:
    # V1.7: a response-only family (validation) asserts the write is REJECTED.
    # http_response supplies the status-code evidence; no effect observer is
    # required, so compilation succeeds instead of blocking on write_observer.
    behavior_ir = _idempotency_ir()
    behavior_ir["operations"] = [
        operation
        for operation in behavior_ir["operations"]
        if operation["id"] not in {"op-list", "op-read"}
    ]
    behavior_ir["operations"][0]["request_schema"] = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["externalRef"],
                    "properties": {"externalRef": {"type": "string"}},
                },
                "example": {"externalRef": "source-ref"},
            },
        },
    }
    obligation = {
        "obligation_id": "obl-validation-no-observer",
        "risk_family": "validation",
        "property": {"operation_ref": "op-create", "actor_ref": "actor-writer"},
        "required_operations": ["op-create"],
        "required_actors": ["actor-writer"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
        },
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    # http_response must be attached as the status-code evidence surface.
    assert any(
        isinstance(observer, dict)
        and observer.get("observer_id") == "http_response"
        for observer in experiment["observers"]
    )
    # The compiled response-only experiment must pass the runtime preflight
    # (no declared effect observer, so no effect evidence is required).
    ok, reason, detail = runtime_support.preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
    )
    assert ok is True, (reason, detail)


def test_auto_fixture_requires_identity_bound_cleanup_operation() -> None:
    operations = {
        "op-create": {
            "id": "op-create",
            "method": "POST",
            "path": "/resources",
            "request_example": {"name": "source-name"},
        },
        "op-archive-all": {
            "id": "op-archive-all",
            "method": "DELETE",
            "path": "/resources/archive",
        },
    }
    binding = {
        "target": "id",
        "target_path": "/resources/{id}",
        "fixture_owner_actor_ref": "actor-buyer",
        "resolver_operations": [{
            "operation_ref": "op-list",
            "method": "GET",
            "path": "/resources",
        }],
    }
    actors = {
        "actor-buyer": {
            "id": "actor-buyer",
            "role": "buyer",
            "credential_secret_ref": "secret_ref:test_accounts:buyer",
        },
    }

    assert _auto_fixture_create_for_binding_target(
        "id",
        binding,
        operations,
        {"id": binding},
        actors=actors,
    ) is None

    operations["op-delete"] = {
        "id": "op-delete",
        "method": "DELETE",
        "path": "/resources/{id}",
    }
    fixture = _auto_fixture_create_for_binding_target(
        "id",
        binding,
        operations,
        {"id": binding},
        actors=actors,
    )

    assert fixture is not None
    assert fixture["fixture_setup"]["cleanup_operations"] == [{
        "operation_ref": "op-delete",
        "method": "DELETE",
        "path": "/resources/{id}",
    }]
    assert fixture["fixture_setup"]["actor_refs"] == ["actor-buyer"]


def test_runtime_binding_does_not_cross_actor_scope_on_empty_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """An empty owner collection must not borrow another actor's resource ID."""
    behavior_ir = {
        "operations": [{
            "id": "op-list",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
        }, {
            "id": "op-read",
            "method": "GET",
            "path": "/api/orders/{id}",
            "read_write": "read",
        }],
        "actors": [
            {
                "id": "actor-admin",
                "role": "admin",
                "credential_secret_ref": "secret_ref:test_accounts:admin",
            },
            {
                "id": "actor-buyer",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:test_accounts:buyer",
            },
        ],
    }
    experiment = {
        "experiment_id": "exp-empty-collection-fallback",
        "obligation_id": "obl-empty-collection-fallback",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [{
            "step_id": "control_1",
            "operation_ref": "op-read",
            "actor_ref": "actor-admin",
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "operation_ref": "op-read",
            "actor_ref": "actor-admin",
        }],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "target_path": "/api/orders/{id}",
            "source_priority": "same_actor_list_read",
            "fixture_owner_actor_ref": "actor-admin",
            "resolver_operations": [{
                "operation_ref": "op-list",
                "method": "GET",
                "path": "/api/orders",
            }],
        }],
        "fixture_dag": {
            "status": "READY",
            "nodes": [{
                "node_id": "bind-id",
                "kind": "runtime_read_binding",
                "target": "id",
            }],
            "setup_order": ["bind-id"],
        },
        "observers": [{"observer_id": "http_response"}],
        "assertions": [],
    }
    seen_tokens: list[str] = []

    def http_request(method: str, url: str, **kwargs):
        token = str(kwargs.get("token") or "")
        seen_tokens.append(token)
        if token == "admin-token":
            return {"status": 200, "body": [], "headers": {}, "duration_ms": 1}
        if token == "buyer-token":
            return {
                "status": 200,
                "body": [{"id": "order-buyer-1"}],
                "headers": {},
                "duration_ms": 1,
            }
        return {"status": 401, "body": {}, "headers": {}, "duration_ms": 1}

    _patch_http_request(monkeypatch, http_request)
    _patch_governed_write(
        monkeypatch,
        lambda **_kwargs: pytest.fail("empty-collection fallback must not create fixture"),
    )

    result = execute_one_experiment(
        experiment,
        behavior_ir=behavior_ir,
        root=tmp_path,
        project="project",
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign",
        execution_id="execution-empty-collection-fallback",
        actor_tokens={
            "secret_ref:test_accounts:admin": "admin-token",
            "secret_ref:test_accounts:buyer": "buyer-token",
        },
    )

    binding = result["binding_materialization_receipts"][0]
    assert binding["status"] == "BLOCKED"
    assert binding["resolver_actor_ref"] == "actor-admin"
    assert "admin-token" in seen_tokens
    assert "buyer-token" not in seen_tokens


def test_auto_fixture_rejects_action_name_as_cleanup_authority() -> None:
    operations = {
        "op-create": {
            "id": "op-create",
            "method": "POST",
            "path": "/api/orders",
            "request_example": {"addressId": "<addressId>"},
        },
        "op-cancel": {
            "id": "op-cancel",
            "method": "POST",
            "path": "/api/orders/{id}/cancel",
        },
    }
    binding = {
        "target": "order_id",
        "target_path": "/api/orders/{id}",
        "fixture_owner_actor_ref": "actor-buyer",
        "resolver_operations": [{
            "operation_ref": "op-list",
            "method": "GET",
            "path": "/api/orders",
        }],
    }
    actors = {"actor-buyer": {"id": "actor-buyer", "role": "buyer"}}

    fixture = _auto_fixture_create_for_binding_target(
        "order_id",
        binding,
        operations,
        {"order_id": binding},
        actors=actors,
    )

    assert fixture is None


def test_validated_fixture_setup_derives_body_dependency_resolvers() -> None:
    setup = validated_fixture_setup(
        {
            "fixture_setup": {
                "operation_ref": "op-create-order",
                "method": "POST",
                "path": "/api/orders",
                "actor_refs": ["actor-buyer"],
                "cleanup_operations": [{
                    "operation_ref": "op-cancel",
                    "method": "POST",
                    "path": "/api/orders/{id}/cancel",
                    "compensates_operation_ref": "op-create-order",
                }],
            },
        },
        {
            "op-create-order": {
                "id": "op-create-order",
                "method": "POST",
                "path": "/api/orders",
                "request_example": {"addressId": "<addressId>", "note": "x"},
            },
            "op-list-addresses": {
                "id": "op-list-addresses",
                "method": "GET",
                "path": "/api/users/addresses",
            },
            "op-cancel": {
                "id": "op-cancel",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
            },
        },
        {
            "actor-buyer": {
                "id": "actor-buyer",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:test_accounts:buyer",
            },
        },
    )

    assert setup
    assert len(setup["body_bindings"]) == 1
    body_binding = setup["body_bindings"][0]
    assert body_binding["target"] == "addressId"
    assert body_binding["template_token"] == "addressId"
    assert body_binding["fallback"] == ""
    assert body_binding["resolver_operations"][0]["operation_ref"] == "op-list-addresses"
    assert body_binding["resolver_operations"][0]["path"] == "/api/users/addresses"


def test_fixture_actor_is_not_inferred_from_admin_role() -> None:
    actor_refs = _declared_fixture_actor_refs(
        {"id": "op-create", "method": "POST", "path": "/resources"},
        behavior_ir={
            "actors": [{
                "id": "actor-admin",
                "role": "admin",
                "credential_secret_ref": "secret_ref:test_accounts:admin",
            }],
        },
    )

    assert actor_refs == []


def test_runtime_fixture_setup_does_not_fallback_to_any_available_actor() -> None:
    setup = validated_fixture_setup(
        {
            "fixture_setup": {
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/resources",
                "actor_refs": [],
                "cleanup_operations": [{
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                }],
            },
        },
        {
            "op-create": {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "request_example": {"name": "source-name"},
            },
            "op-delete": {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
            },
        },
        {
            "actor-admin": {
                "id": "actor-admin",
                "role": "admin",
                "credential_secret_ref": "secret_ref:test_accounts:admin",
            },
        },
    )

    assert setup == {}


def test_runtime_fixture_setup_rejects_unresolved_body_fallback() -> None:
    setup = validated_fixture_setup(
        {
            "fixture_setup": {
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/resources",
                "actor_refs": ["actor-writer"],
                "body_template": {"ownerId": "<owner_id>"},
                "body_bindings": [{
                    "target": "ownerId",
                    "template_token": "owner_id",
                    "resolver_operations": [],
                    "fallback": "invented-owner",
                }],
                "cleanup_operations": [{
                    "operation_ref": "op-delete",
                    "method": "DELETE",
                    "path": "/resources/{id}",
                }],
            },
        },
        {
            "op-create": {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "request_example": {"ownerId": "<owner_id>"},
            },
            "op-delete": {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
            },
        },
        {
            "actor-writer": {
                "id": "actor-writer",
                "credential_secret_ref": "secret_ref:test_accounts:writer",
            },
        },
    )

    assert setup == {}


def test_runtime_fixture_setup_requires_identity_bound_cleanup() -> None:
    setup = validated_fixture_setup(
        {
            "fixture_setup": {
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/resources",
                "actor_refs": ["actor-writer"],
                "body_template": {"name": "source-name"},
                "cleanup_operations": [],
            },
        },
        {
            "op-create": {
                "id": "op-create",
                "method": "POST",
                "path": "/resources",
                "request_example": {"name": "source-name"},
            },
        },
        {
            "actor-writer": {
                "id": "actor-writer",
                "credential_secret_ref": "secret_ref:test_accounts:writer",
            },
        },
    )

    assert setup == {}
