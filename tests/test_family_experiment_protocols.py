from __future__ import annotations

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
