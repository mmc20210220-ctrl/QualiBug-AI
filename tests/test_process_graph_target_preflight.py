from __future__ import annotations

from ai_test_asset_center import experiment_executor
from ai_test_asset_center.experiment_runtime_support import (
    preflight_experiment_executable as original_preflight,
)


def _behavior_ir() -> dict:
    return {
        "actors": [
            {
                "id": "actor-writer",
                "role": "writer",
                "credential_secret_ref": "primary:actor-writer",
            }
        ],
        "operations": [
            {
                "id": "op-read-orders",
                "method": "GET",
                "path": "/orders",
                "read_write": "read",
            }
        ],
    }


def _graph_experiment() -> dict:
    graph = {
        "execution_graph_id": "graph-target-preflight",
        "process_id": "process-target-preflight",
        "nodes": [
            {
                "node_id": "read-orders",
                "step_id": "read-orders",
                "operation_ref": "op-read-orders",
                "actor_ref": "actor-writer",
                "system_ref": "orders",
                "method": "GET",
                "path": "/orders",
            }
        ],
        "edges": [],
        "topological_order": ["read-orders"],
        "wait_contracts": [],
    }
    return {
        "experiment_id": "exp-target-preflight",
        "obligation_id": "obl-target-preflight",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "precondition_plan": [],
        "treatment_plan": [
            {
                "step_id": "read-orders",
                "operation_ref": "op-read-orders",
                "actor_ref": "actor-writer",
                "system_ref": "orders",
                "method": "GET",
                "path": "/orders",
                "_execution_graph": graph,
            }
        ],
        "process_graph_write_contract": {
            "status": "RESOLVED",
            "execution_graph_id": "graph-target-preflight",
            "write_step_ids": [],
            "write_contracts_by_node": {},
            "cleanup_steps": [],
        },
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "binding_plan": [],
        "observers": [
            {"observer_id": "http_response", "surface": "http_api"}
        ],
        "assertions": [],
        "compiled_adapters": ["http_api"],
        "safety_contract": {"governed_write": False},
    }


def test_public_preflight_identity_remains_runtime_support_authority() -> None:
    assert experiment_executor.preflight_experiment_executable is original_preflight


def test_graph_exclusive_actor_defers_global_token_lookup() -> None:
    exp = _graph_experiment()
    ir = _behavior_ir()
    tokens = {"orders:actor-writer": "orders-token"}

    original = original_preflight(
        exp,
        behavior_ir=ir,
        actor_tokens=tokens,
    )
    assert original == (
        False,
        "BLOCKED_MISSING_ACTOR",
        "token_unresolved:actor-writer",
    )

    adapted = experiment_executor._graph_aware_preflight(
        exp,
        behavior_ir=ir,
        actor_tokens=tokens,
    )
    assert adapted == (True, "", "")
    # The caller-owned token map is never mutated with a compatibility marker.
    assert tokens == {"orders:actor-writer": "orders-token"}


def test_pregraph_actor_never_receives_credential_deferral() -> None:
    exp = _graph_experiment()
    exp["control_plan"] = [
        {
            "step_id": "control-read-orders",
            "operation_ref": "op-read-orders",
            "actor_ref": "actor-writer",
            "method": "GET",
            "path": "/orders",
        }
    ]

    adapted = experiment_executor._graph_aware_preflight(
        exp,
        behavior_ir=_behavior_ir(),
        actor_tokens={"orders:actor-writer": "orders-token"},
    )
    assert adapted == (
        False,
        "BLOCKED_MISSING_ACTOR",
        "exact_credential_unresolved:actor-writer",
    )


def test_fixture_actor_never_receives_credential_deferral() -> None:
    exp = _graph_experiment()
    exp["binding_plan"] = [
        {
            "target": "order_id",
            "fixture_setup": {
                "operation_ref": "op-create-order",
                "actor_refs": ["actor-writer"],
            },
        }
    ]

    adapted = experiment_executor._graph_aware_preflight(
        exp,
        behavior_ir=_behavior_ir(),
        actor_tokens={"orders:actor-writer": "orders-token"},
    )
    assert adapted == (
        False,
        "BLOCKED_MISSING_ACTOR",
        "exact_credential_unresolved:actor-writer",
    )
