from __future__ import annotations


def _ir() -> dict:
    return {
        "operations": [
            {"id": "read-orders", "method": "GET", "path": "/api/orders"},
            {
                "id": "write-order",
                "method": "POST",
                "path": "/api/orders/{orderId}",
            },
        ]
    }


def _flow(target: str) -> dict:
    return {
        "schema_version": "qualibug.flow-data-execution-contract.v1",
        "status": "FROZEN",
        "step_contracts": [
            {
                "step_id": "treatment_1",
                "initial_binding_targets": [target],
                "input_bindings": [],
                "sequential_identity_targets": [],
            }
        ],
    }


def test_blocked_binding_target_is_not_initial_materialization_authority() -> None:
    from ai_test_asset_center.binding_target_materialization_authority import (
        resolve_binding_target_materialization,
    )

    receipt = resolve_binding_target_materialization(
        "orderId",
        experiment={
            "binding_plan": [
                {
                    "target": "orderId",
                    "status": "blocked",
                    "source_priority": "body_reference_target_unresolved",
                    "blocked_reason": "BODY_REFERENCE_TARGET_UNRESOLVED",
                }
            ]
        },
        behavior_ir=_ir(),
        flow_execution_contract=_flow("orderId"),
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == "BODY_REFERENCE_TARGET_UNRESOLVED"


def test_fingerprint_only_bound_row_is_not_reconstructable_value() -> None:
    from ai_test_asset_center.binding_target_materialization_authority import (
        resolve_binding_target_materialization,
    )

    receipt = resolve_binding_target_materialization(
        "orderId",
        experiment={
            "binding_plan": [
                {
                    "target": "orderId",
                    "status": "bound",
                    "source_priority": "same_actor_list_read",
                    "value_fingerprint": "deadbeef",
                }
            ]
        },
        behavior_ir=_ir(),
        flow_execution_contract=_flow("orderId"),
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == (
        "BINDING_TARGET_HAS_NO_EXECUTABLE_MATERIALIZATION_CHANNEL"
    )


def test_validated_runtime_resolver_requires_runtime_binding_dag_node() -> None:
    from ai_test_asset_center.binding_target_materialization_authority import (
        resolve_binding_target_materialization,
    )

    binding = {
        "target": "orderId",
        "target_path": "/api/orders/{orderId}",
        "status": "runtime_resolvable",
        "source_priority": "same_actor_list_read",
        "resolver_operations": [
            {
                "operation_ref": "read-orders",
                "method": "GET",
                "path": "/api/orders",
            }
        ],
    }
    without_node = resolve_binding_target_materialization(
        "orderId",
        experiment={"binding_plan": [binding]},
        behavior_ir=_ir(),
        flow_execution_contract=_flow("orderId"),
    )
    assert without_node["status"] == "UNRESOLVED"
    assert without_node["reason_code"] == "BINDING_RUNTIME_DAG_NODE_MISSING"

    with_node = resolve_binding_target_materialization(
        "orderId",
        experiment={
            "binding_plan": [binding],
            "fixture_dag": {
                "nodes": [
                    {
                        "node_id": "bind-order",
                        "kind": "runtime_read_binding",
                        "target": "orderId",
                        "constructible": True,
                    }
                ]
            },
        },
        behavior_ir=_ir(),
        flow_execution_contract=_flow("orderId"),
    )
    assert with_node["status"] == "RESOLVED"
    assert with_node["authority"] == "validated_runtime_resolver"


def test_unprojected_credential_target_is_old_artifact_gap() -> None:
    from ai_test_asset_center.binding_target_materialization_authority import (
        resolve_binding_target_materialization,
    )

    receipt = resolve_binding_target_materialization(
        "password",
        experiment={
            "binding_plan": [
                {
                    "target": "password",
                    "status": "runtime_resolvable",
                    "source_priority": "actor_credential_secret",
                    "actor_ref": "actor-user",
                    "credential_secret_ref": "secret_ref:test_accounts:user@example.test",
                    "credential_actor_authority": "unique_required_actor",
                }
            ]
        },
        behavior_ir=_ir(),
        flow_execution_contract=_flow("password"),
    )

    assert receipt["status"] == "UNRESOLVED"
    assert receipt["reason_code"] == (
        "BINDING_CREDENTIAL_REQUIRES_SECRET_REF_PROJECTION"
    )
    assert receipt["secret_value_persisted"] is False


def test_runtime_preflight_rejects_stale_frozen_unexecutable_binding() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _runtime_initial_binding_authority,
    )

    experiment = {
        "binding_plan": [
            {
                "target": "orderId",
                "status": "blocked",
                "source_priority": "path_placeholder_unresolvable",
                "blocked_reason": "PLACEHOLDER_PATH_PARAMETER_NOT_RESOLVED",
            }
        ],
        "flow_data_execution_contract": _flow("orderId"),
    }
    ok, reason, detail = _runtime_initial_binding_authority(
        experiment,
        _ir(),
    )

    assert ok is False
    assert reason == "BLOCKED_MISSING_BINDING"
    assert "orderId" in detail
    assert "PLACEHOLDER_PATH_PARAMETER_NOT_RESOLVED" in detail
