from __future__ import annotations

from ai_test_asset_center.experiment_compile_freezer import (
    BLOCKED_READBACK_CONTRACT_AMBIGUOUS,
    BLOCKED_READBACK_CONTRACT_INCOMPLETE,
    freeze_compiled_experiment,
)
from ai_test_asset_center.flow_data_requirement import (
    BLOCKED_FLOW_DATA_BINDING_INCOMPLETE,
)


IR = {
    "operations": [
        {"id": "op_write", "method": "POST", "read_write": "write", "path": "/items/{id}/activate", "entity_refs": ["entity_item"]},
        {"id": "op_second", "method": "POST", "read_write": "write", "path": "/items/{id}/confirm", "entity_refs": ["entity_item"]},
        {"id": "op_read", "method": "GET", "read_write": "read", "path": "/items/{id}", "entity_refs": ["entity_item"]},
        {"id": "op_read_alt", "method": "GET", "read_write": "read", "path": "/items/{id}/status", "entity_refs": ["entity_item"]},
    ]
}


def _policy(*, terminal: str = "business_state_changed") -> dict:
    return {
        "enabled": True,
        "expected_max_delay_ms": 1000,
        "poll_interval_ms": 100,
        "max_attempts": 5,
        "required_stable_observations": 1,
        "terminal_condition": terminal,
    }


def _observer(
    *,
    observer_id: str = "after_state",
    policy: dict | None = None,
    resolver_operations: list[dict] | None = None,
) -> dict:
    return {
        "observer_id": observer_id,
        "readback_contract_id": f"rb_{observer_id}",
        "resolver_operations": resolver_operations
        if resolver_operations is not None
        else [
            {
                "operation_ref": "op_read",
                "method": "GET",
                "path": "/items/{id}",
            }
        ],
        "async_policy": policy if policy is not None else _policy(),
        "provenance_fingerprint": f"prov_{observer_id}",
    }


def _experiment() -> dict:
    return {
        "experiment_id": "exp_freeze",
        "obligation_id": "obl_freeze",
        "compile_receipt": {"status": "COMPILED"},
        "assertions": [
            {"property": {"operation_ref": "op_write"}}
        ],
        "observers": [_observer()],
        "binding_plan": [
            {
                "target": "id",
                "status": "bound",
                "source_priority": "source_declared_path_example",
                "target_path": "/items/{id}",
                "materialized_value": "item_1",
                "resolver_operations": [
                    {
                        "operation_ref": "op_read",
                        "method": "GET",
                        "path": "/items/{id}",
                    }
                ],
            }
        ],
        "fixture_dependency_dag": {
            "execution_order": ["binding:id"],
            "nodes": [{"node_id": "binding:id", "target": "id"}],
        },
        "disposable_fixture_contract": {
            "fixture_id": "legacy_fixture_1",
            "create_operation_ref": "op_write",
            "entity_ref": "entity_item",
            "status": "RESOLVED",
        },
        "precondition_plan": [],
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "step_activate",
                "operation_ref": "op_write",
                "actor_ref": "actor_1",
            }
        ],
        "cleanup_plan": [
            {"source_step_id": "step_activate", "operation_ref": "op_second"}
        ],
    }


def _two_step_graph() -> dict:
    return {
        "process_id": "process_confirm",
        "topological_order": ["step_activate", "step_confirm"],
        "nodes": [
            {
                "node_id": "step_activate",
                "step_id": "step_activate",
                "operation_ref": "op_write",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "confirmation_id",
                        "json_path": "$.confirmationId",
                    }
                ],
            },
            {
                "node_id": "step_confirm",
                "step_id": "step_confirm",
                "operation_ref": "op_second",
                "input_binding_refs": [
                    {
                        "producer_node_id": "step_activate",
                        "producer_output_field": "confirmation_id",
                        "target": "confirmation_id",
                    }
                ],
            },
        ],
        "edges": [
            {
                "source_node_id": "step_activate",
                "target_node_id": "step_confirm",
            }
        ],
    }


def test_freeze_binds_compiled_async_policy_to_exact_primary_step() -> None:
    frozen = freeze_compiled_experiment(_experiment(), behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "COMPILED"
    assert frozen["compile_receipt"]["compile_freeze_status"] == "FROZEN"
    step = frozen["treatment_plan"][0]
    assert step["async_policy"] == _policy()
    assert step["runtime_body_plan"]["async_policy"] == _policy()
    assert step["readback_contract"]["resolver_operations"] == [
        {
            "operation_ref": "op_read",
            "method": "GET",
            "path": "/items/{id}",
            "readback_contract_id": "",
            "readback_surface_type": "",
        }
    ]
    assert frozen["flow_requirements"]["required_step_ids"] == [
        "step_activate"
    ]
    assert frozen["flow_requirements"]["write_step_ids"] == [
        "step_activate"
    ]
    requirement = frozen["flow_data_requirement"]
    assert requirement["status"] == "FROZEN"
    assert requirement["materialized_before_measurement_targets"] == ["id"]
    assert frozen["compile_receipt"]["flow_data_requirement_id"] == (
        requirement["requirement_id"]
    )
    execution_contract = frozen["flow_data_execution_contract"]
    assert execution_contract["status"] == "FROZEN"
    assert frozen["compile_receipt"][
        "flow_data_execution_contract_fingerprint"
    ] == execution_contract["contract_fingerprint"]
    assert frozen["disposable_fixture_contract"]["projection_only"] is True
    assert frozen["disposable_fixture_contract"]["is_flow_data_authority"] is False


def test_global_primary_observer_does_not_leak_to_other_flow_step() -> None:
    experiment = _experiment()
    experiment["treatment_plan"].append(
        {
            "step_id": "step_confirm",
            "operation_ref": "op_second",
            "actor_ref": "actor_1",
        }
    )

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    primary, secondary = frozen["treatment_plan"]
    assert primary["async_policy"]["enabled"] is True
    assert "async_policy" not in secondary
    bindings = frozen["flow_requirements"]["observer_bindings"]
    assert [row["bound"] for row in bindings] == [True, False]
    assert [
        row["operation_ref"]
        for row in frozen["flow_data_requirement"]["step_requirements"]
    ] == ["op_write", "op_second"]


def test_step_declared_observer_requirement_binds_secondary_step() -> None:
    experiment = _experiment()
    experiment["observers"].append(
        _observer(
            observer_id="confirm_state",
            policy={
                "enabled": False,
                "expected_max_delay_ms": 0,
                "poll_interval_ms": 0,
                "max_attempts": 1,
                "terminal_condition": "immediate",
            },
            resolver_operations=[
                {
                    "operation_ref": "op_read_alt",
                    "method": "GET",
                    "path": "/items/{id}/status",
                }
            ],
        )
    )
    experiment["treatment_plan"].append(
        {
            "step_id": "step_confirm",
            "operation_ref": "op_second",
            "actor_ref": "actor_1",
            "observer_requirements": ["confirm_state"],
        }
    )

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)
    secondary = frozen["treatment_plan"][1]

    assert secondary["readback_contract"]["observer_ids"] == [
        "confirm_state"
    ]
    assert secondary["readback_contract"]["resolver_operations"][0][
        "operation_ref"
    ] == "op_read_alt"


def test_missing_secondary_path_binding_blocks_compile() -> None:
    experiment = _experiment()
    experiment["treatment_plan"].append(
        {
            "step_id": "step_confirm",
            "operation_ref": "op_second",
            "actor_ref": "actor_1",
            "path": "/items/{confirmation_id}/confirm",
        }
    )

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_FLOW_DATA_BINDING_INCOMPLETE
    )
    assert "step_confirm:confirmation_id" in frozen["compile_receipt"]["detail"]


def test_graph_output_can_supply_later_step_with_explicit_binding_ref() -> None:
    experiment = _experiment()
    graph = _two_step_graph()
    experiment["treatment_plan"][0].update(
        {
            "output_binding_specs": graph["nodes"][0]["output_binding_specs"],
            "_execution_graph": graph,
        }
    )
    experiment["treatment_plan"].append(
        {
            "step_id": "step_confirm",
            "operation_ref": "op_second",
            "actor_ref": "actor_1",
            "path": "/items/{confirmation_id}/confirm",
            "input_binding_refs": graph["nodes"][1]["input_binding_refs"],
            "_execution_graph": graph,
        }
    )
    experiment["binding_plan"].append(
        {
            "target": "confirmation_id",
            "status": "runtime_resolvable",
            "source_priority": "sequential_output_binding",
        }
    )

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "COMPILED"
    second = frozen["flow_data_execution_contract"]["step_contracts"][1]
    assert second["required_targets"] == ["confirmation_id"]
    assert second["missing_targets"] == []
    assert second["input_bindings"][0]["status"] == "RESOLVED"


def test_sequential_output_binding_is_not_treated_as_executable() -> None:
    experiment = _experiment()
    experiment["treatment_plan"][0]["output_binding_specs"] = [
        {
            "canonical_field_id": "confirmation_id",
            "json_path": "$.confirmationId",
        }
    ]

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_FLOW_DATA_BINDING_INCOMPLETE
    )
    assert "OUTPUT_BINDING_EXECUTOR_UNAVAILABLE" in frozen[
        "compile_receipt"
    ]["detail"]


def test_query_placeholder_requires_an_executable_binding_source() -> None:
    experiment = _experiment()
    experiment["treatment_plan"][0]["query"] = {
        "tenantId": "{tenant_id}"
    }

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_FLOW_DATA_BINDING_INCOMPLETE
    )
    assert "STEP_BINDING_EXECUTION_SOURCE_MISSING" in frozen[
        "compile_receipt"
    ]["detail"]


def test_graph_output_requires_exact_response_source_path() -> None:
    experiment = _experiment()
    graph = _two_step_graph()
    graph["nodes"][0]["output_binding_specs"] = [
        {"canonical_field_id": "confirmation_id"}
    ]
    experiment["treatment_plan"][0].update(
        {
            "output_binding_specs": graph["nodes"][0]["output_binding_specs"],
            "_execution_graph": graph,
        }
    )

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_FLOW_DATA_BINDING_INCOMPLETE
    )
    assert "OUTPUT_BINDING_IDENTITY_INCOMPLETE" in frozen[
        "compile_receipt"
    ]["detail"]


def test_conflicting_step_async_policies_block_compile() -> None:
    experiment = _experiment()
    experiment["observers"].append(
        _observer(
            observer_id="business_effect",
            policy=_policy(terminal="http_success"),
        )
    )

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_READBACK_CONTRACT_AMBIGUOUS
    )


def test_enabled_policy_without_unique_read_surface_blocks_compile() -> None:
    experiment = _experiment()
    experiment["observers"] = [
        _observer(resolver_operations=[])
    ]

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_READBACK_CONTRACT_INCOMPLETE
    )


def test_enabled_policy_with_two_read_surfaces_blocks_as_ambiguous() -> None:
    experiment = _experiment()
    experiment["observers"] = [
        _observer(
            resolver_operations=[
                {"operation_ref": "op_read", "method": "GET", "path": "/items/{id}"},
                {"operation_ref": "op_read_alt", "method": "GET", "path": "/items/{id}/status"},
            ]
        )
    ]

    frozen = freeze_compiled_experiment(experiment, behavior_ir=IR)

    assert frozen["compile_receipt"]["status"] == "BLOCKED"
    assert frozen["compile_receipt"]["reason_code"] == (
        BLOCKED_READBACK_CONTRACT_AMBIGUOUS
    )


def test_freeze_is_deterministic_and_idempotent() -> None:
    once = freeze_compiled_experiment(_experiment(), behavior_ir=IR)
    twice = freeze_compiled_experiment(once, behavior_ir=IR)

    assert once["compile_freeze_receipt"]["freeze_fingerprint"] == (
        twice["compile_freeze_receipt"]["freeze_fingerprint"]
    )
    assert once["flow_requirements"] == twice["flow_requirements"]
    assert once["flow_data_requirement"] == twice["flow_data_requirement"]
    assert once["flow_data_execution_contract"] == (
        twice["flow_data_execution_contract"]
    )
    assert once["treatment_plan"] == twice["treatment_plan"]
