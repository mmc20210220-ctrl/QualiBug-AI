from __future__ import annotations

import threading

import pytest

from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.fixture_dag import build_fixture_dag_for_experiment
from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.observer_contracts import observe_experiment_requirements
from ai_test_asset_center.runtime_binding_materializer import runtime_cleanup_paths


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
            "before": {"status": 200, "body": {"id": kwargs["path"].rsplit("/", 1)[-1]}},
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
        "ai_test_asset_center.experiment_executor.execute_governed_control_write",
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

    assert result["status"] == "EXECUTED"
    assert result["cleanup_failures"] == 0
    assert cleanup_paths == ["/resources/r-2", "/resources/r-1"]
    effect = next(
        receipt
        for receipt in result["observer_receipts"]
        if receipt["observer_id"] == "business_effect"
    )
    assert effect["status"] == "OBSERVED"
    assert effect["evidence"]["effect_count"] == 2
    assert result["finding"] is not None


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

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        http_request,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_governed_control_write",
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
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor.execute_governed_control_write",
        lambda **_kwargs: {
            "accepted": True,
            "status": "executed",
            "before": {"status": 200, "body": []},
            "write": {"status": 201, "body": {}},
            "after": {"status": 200, "body": [{"name": "unidentified"}]},
        },
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_executor._http_request",
        lambda *_args, **_kwargs: pytest.fail("experiment must stop before probes"),
    )

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

    def governed_write(**kwargs):
        phase = kwargs["operation_phase"]
        if phase in {"experiment_control", "experiment_treatment"}:
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
        "ai_test_asset_center.experiment_executor.execute_governed_control_write",
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


def test_source_invariant_obligation_compiles_typed_assertion_observers() -> None:
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

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    assert {
        observer["observer_id"]
        for observer in experiment["observers"]
    } >= {"typed_assertion", "source_invariant"}


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


def test_validation_write_blocks_without_source_observation_path() -> None:
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

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_OBSERVER"
    assert experiment["compile_receipt"]["detail"] == "write_observer"
