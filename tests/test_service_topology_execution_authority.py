from __future__ import annotations

from ai_test_asset_center.service_topology_execution_authority import (
    BLOCKED_CROSS_SERVICE_ROUTE_UNAVAILABLE,
    BLOCKED_SERVICE_ROUTE_UNAVAILABLE,
    build_service_topology,
    resolve_experiment_execution_route,
)


def _ir() -> dict:
    return {
        "operations": [
            {"id": "op-a", "service": "alpha"},
            {"id": "op-b", "_service_name": "beta"},
            {"id": "op-c", "service_name": "gamma"},
        ]
    }


def _runtime_contract() -> dict:
    return {
        "approved_base_url": "http://127.0.0.1:9107",
        "requested_base_url": "http://127.0.0.1:9107",
        "environment_type": "test",
        "execution_mode": "formal",
        "status": "ready",
    }


def test_topology_uses_project_declared_urls_not_fixed_ports() -> None:
    topology = build_service_topology(
        {
            "multi_service": {
                "services": {
                    "alpha": "http://127.0.0.1:39117/",
                    "beta": {
                        "base_url": "http://127.0.0.1:48763/api/",
                        "environment_type": "test",
                    },
                }
            }
        }
    )
    assert topology["alpha"]["approved_base_url"] == "http://127.0.0.1:39117"
    assert topology["beta"]["approved_base_url"] == "http://127.0.0.1:48763/api"
    assert set(topology) == {"alpha", "beta"}


def test_single_service_experiment_routes_to_own_declared_target() -> None:
    topology = build_service_topology(
        {
            "multi_service": {
                "services": {
                    "alpha": "http://127.0.0.1:39117",
                    "beta": "http://127.0.0.1:48763",
                }
            }
        }
    )
    experiment = {
        "obligation_id": "obl-beta",
        "experiment_id": "exp-beta",
        "required_operations": ["op-b"],
        "treatment_plan": [{"step_id": "s1", "operation_ref": "op-b"}],
    }
    route = resolve_experiment_execution_route(
        experiment=experiment,
        behavior_ir=_ir(),
        base_url="http://127.0.0.1:39117",
        runtime_contract=_runtime_contract(),
        topology=topology,
    )
    assert route["status"] == "READY"
    assert route["mode"] == "single_service_routed"
    assert route["routed_service_ref"] == "beta"
    assert route["base_url"] == "http://127.0.0.1:48763"


def test_graph_multi_service_reuses_approved_target_runtime() -> None:
    topology = build_service_topology(
        {
            "multi_service": {
                "services": {
                    "alpha": "http://127.0.0.1:39117",
                    "beta": {
                        "approved_base_url": "http://127.0.0.1:48763",
                        "actor_token_keys": {"actor-1": "secret:beta:actor-1"},
                    },
                }
            }
        }
    )
    graph = {
        "execution_graph_id": "graph-1",
        "nodes": [
            {"node_id": "s1", "operation_ref": "op-a", "system_ref": "alpha"},
            {"node_id": "s2", "operation_ref": "op-b", "system_ref": "beta"},
        ],
        "topological_order": ["s1", "s2"],
        "edges": [{"source_node_id": "s1", "target_node_id": "s2"}],
    }
    experiment = {
        "obligation_id": "obl-graph",
        "experiment_id": "exp-graph",
        "required_operations": ["op-a", "op-b"],
        "treatment_plan": [
            {"step_id": "s1", "operation_ref": "op-a", "_execution_graph": graph},
            {"step_id": "s2", "operation_ref": "op-b", "_execution_graph": graph},
        ],
    }
    route = resolve_experiment_execution_route(
        experiment=experiment,
        behavior_ir=_ir(),
        base_url="http://127.0.0.1:39117",
        runtime_contract=_runtime_contract(),
        topology=topology,
    )
    assert route["status"] == "READY"
    assert route["mode"] == "process_graph_multi_service"
    approved = route["runtime_contract"]["approved_targets"]
    assert approved["alpha"]["approved_base_url"] == "http://127.0.0.1:39117"
    assert approved["beta"]["approved_base_url"] == "http://127.0.0.1:48763"
    assert approved["beta"]["actor_token_keys"]["actor-1"] == "secret:beta:actor-1"


def test_non_graph_cross_service_experiment_blocks_instead_of_wrong_target() -> None:
    topology = build_service_topology(
        {
            "multi_service": {
                "services": {
                    "alpha": "http://127.0.0.1:39117",
                    "beta": "http://127.0.0.1:48763",
                }
            }
        }
    )
    experiment = {
        "obligation_id": "obl-cross",
        "experiment_id": "exp-cross",
        "required_operations": ["op-a", "op-b"],
        "treatment_plan": [
            {"step_id": "s1", "operation_ref": "op-a"},
            {"step_id": "s2", "operation_ref": "op-b"},
        ],
    }
    route = resolve_experiment_execution_route(
        experiment=experiment,
        behavior_ir=_ir(),
        base_url="http://127.0.0.1:39117",
        runtime_contract=_runtime_contract(),
        topology=topology,
    )
    assert route["status"] == "BLOCKED"
    assert route["reason_code"] == BLOCKED_CROSS_SERVICE_ROUTE_UNAVAILABLE
    assert route["service_refs"] == ["alpha", "beta"]


def test_missing_declared_service_route_blocks_fail_closed() -> None:
    topology = build_service_topology(
        {"multi_service": {"services": {"alpha": "http://127.0.0.1:39117"}}}
    )
    experiment = {
        "required_operations": ["op-c"],
        "treatment_plan": [{"step_id": "s1", "operation_ref": "op-c"}],
    }
    route = resolve_experiment_execution_route(
        experiment=experiment,
        behavior_ir=_ir(),
        base_url="http://127.0.0.1:39117",
        runtime_contract=_runtime_contract(),
        topology=topology,
    )
    assert route["status"] == "BLOCKED"
    assert route["reason_code"] == BLOCKED_SERVICE_ROUTE_UNAVAILABLE
    assert route["missing_service_refs"] == ["gamma"]


def test_no_multi_service_topology_preserves_single_target_behavior() -> None:
    experiment = {
        "required_operations": ["op-b"],
        "treatment_plan": [{"step_id": "s1", "operation_ref": "op-b"}],
    }
    route = resolve_experiment_execution_route(
        experiment=experiment,
        behavior_ir=_ir(),
        base_url="http://127.0.0.1:9107",
        runtime_contract=_runtime_contract(),
        topology={},
    )
    assert route["status"] == "READY"
    assert route["mode"] == "single_target_legacy"
    assert route["base_url"] == "http://127.0.0.1:9107"
