"""Tests for v1.2.1 main chain wiring — SPEC v1.2.1 §5."""
import pytest
from ai_test_asset_center.v12_coverage_recovery_orchestrator import (
    prepare_experiment_v12,
    VERDICT_READY,
    VERDICT_BLOCKED,
)


def _make_ir():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order"},
            {"id": "op_read", "method": "GET", "path": "/orders/{orderId}", "entity_ref": "order"},
        ],
        "relations": [],
        "actors": [{"id": "actor_1", "role": "buyer"}],
    }


def _make_experiment():
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "treatment_plan": [{"path": "/orders", "method": "POST"}],
        "control_plan": [],
        "cleanup_plan": [],
        "observers": [{"observer_id": "after_state", "kind": "after_state"}],
        "binding_plan": [],
        "assertions": [],
        "safety_contract": {"governed_write": True},
    }


class TestOrchestratorVerdict:
    def test_ready_verdict(self):
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={"experiment": _make_experiment(), "primary_operation": _make_ir()["operations"][0]},
        )
        assert result["schema_version"] == "qualibug.v12-coverage-recovery-orchestrator.v1"
        assert result["verdict"] == VERDICT_READY
        assert result["coverage_recovery_version"] == "v1.2.1"

    def test_module_results_attached(self):
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={"experiment": _make_experiment(), "primary_operation": _make_ir()["operations"][0]},
        )
        mr = result["module_results"]
        assert "observer_resolution_plan" in mr
        assert "binding_coverage_graph" in mr
        assert "oracle_input_contract" in mr
        assert "fixture_dependency_dag" in mr
        assert "compensation_relation_plan" in mr

    def test_fingerprint_present(self):
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={"experiment": _make_experiment(), "primary_operation": _make_ir()["operations"][0]},
        )
        assert len(result["fingerprint"]) == 32

    def test_forbidden_binding_blocks(self):
        exp = _make_experiment()
        exp["binding_plan"] = [{"target": "fakeId", "source_kind": "RANDOM_PLACEHOLDER"}]
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={"experiment": exp, "primary_operation": _make_ir()["operations"][0]},
        )
        assert result["verdict"] == VERDICT_BLOCKED
        assert result["primary_blocking_reason"]["module"] == "binding_coverage_graph"
