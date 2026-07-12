"""Tests for scenario-level fixture DAG."""
from __future__ import annotations

from ai_test_asset_center.fixture_dag import attach_fixture_dag_to_experiments, build_fixture_dag_for_experiment


def test_fixture_dag_ready_with_bound_actors_and_cleanup_reverse() -> None:
    experiment = {
        "experiment_id": "exp_1",
        "control_plan": [{"actor_ref": "actor_a"}],
        "treatment_plan": [{"actor_ref": "actor_b"}],
        "binding_plan": [
            {"target": "id", "status": "bound", "source_priority": "experiment_setup_response", "value_fingerprint": "abc"},
        ],
        "setup_plan": [{"action": "resolve_bindings"}],
    }
    ir = {
        "actors": [
            {"id": "actor_a", "role": "owner", "credential_secret_ref": "secret_ref:a"},
            {"id": "actor_b", "role": "viewer", "credential_secret_ref": "secret_ref:b"},
        ]
    }
    dag = build_fixture_dag_for_experiment(experiment, behavior_ir=ir)
    assert dag["status"] == "READY"
    assert dag["cleanup_order"] == list(reversed([
        n["node_id"] for n in dag["nodes"]
        if n["kind"] in {"dependency_create", "disposable_fixture", "setup_step"}
    ]))
    assert dag["rules"]["no_fake_ids"] is True


def test_fixture_dag_blocks_unresolved_binding_without_fixture() -> None:
    experiment = {
        "experiment_id": "exp_2",
        "control_plan": [],
        "treatment_plan": [],
        "binding_plan": [{"target": "id", "status": "unresolved"}],
        "setup_plan": [],
    }
    dag = build_fixture_dag_for_experiment(experiment, behavior_ir={"actors": []})
    assert dag["status"] == "BLOCKED"
    assert any(r["reason_code"] == "BLOCKED_MISSING_FIXTURE" for r in dag["blocked_reasons"])


def test_fixture_dag_accepts_source_declared_runtime_read_binding() -> None:
    experiment = {
        "experiment_id": "exp_runtime_binding",
        "control_plan": [],
        "treatment_plan": [],
        "binding_plan": [{
            "target": "id",
            "status": "runtime_resolvable",
            "source_priority": "same_actor_list_read",
            "resolver_operations": [{
                "operation_ref": "list_resources",
                "method": "GET",
                "path": "/resources",
            }],
        }],
        "setup_plan": [],
    }

    dag = build_fixture_dag_for_experiment(experiment, behavior_ir={"actors": []})

    assert dag["status"] == "READY"
    binding_node = next(node for node in dag["nodes"] if node["kind"] == "runtime_read_binding")
    assert binding_node["target"] == "id"
    assert binding_node["resolver_operations"][0]["path"] == "/resources"
    assert binding_node["requires_read_proof"] is True


def test_attach_fixture_dag_moves_blocked_experiments() -> None:
    pack = {
        "experiments": [{
            "experiment_id": "exp_ok",
            "obligation_id": "obl_1",
            "control_plan": [{"actor_ref": "a1"}],
            "treatment_plan": [{"actor_ref": "a1"}],
            "binding_plan": [{"target": "x", "status": "bound", "source_priority": "schema_generated", "value_fingerprint": "1"}],
            "setup_plan": [],
            "compile_receipt": {"status": "COMPILED"},
        }, {
            "experiment_id": "exp_bad",
            "obligation_id": "obl_2",
            "control_plan": [],
            "treatment_plan": [],
            "binding_plan": [{"target": "id", "status": "unresolved"}],
            "setup_plan": [],
            "compile_receipt": {"status": "COMPILED"},
        }],
        "blocked_experiments": [],
    }
    ir = {"actors": [{"id": "a1", "credential_secret_ref": "secret_ref:a1", "role": "r"}]}
    out = attach_fixture_dag_to_experiments(pack, behavior_ir=ir)
    assert out["compiled_count"] == 1
    assert out["blocked_count"] == 1
    assert out["experiments"][0]["fixture_dag"]["status"] == "READY"
    assert out["blocked_experiments"][0]["compile_receipt"]["status"] == "BLOCKED"
