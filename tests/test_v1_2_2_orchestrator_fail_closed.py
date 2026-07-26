"""Tests for v1.2.2 Orchestrator Fail-Closed — SPEC v1.2.2 §4."""
import pytest
from ai_test_asset_center.v12_coverage_recovery_orchestrator import (
    prepare_experiment_v12,
    VERDICT_READY,
    VERDICT_BLOCKED,
    GATE_PASSED,
    GATE_BLOCKED,
    GATE_NOT_APPLICABLE,
)


def _make_ir():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders",
             "read_write": "write", "source_refs": [{"kind": "endpoint_contract"}]},
            {"id": "op_get", "method": "GET", "path": "/orders/{orderId}",
             "read_write": "read", "source_refs": [{"kind": "endpoint_contract"}]},
        ],
        "relations": [],
        "actors": [{"id": "actor_1", "kind": "customer"}],
    }


def _make_experiment(**overrides):
    base = {
        "experiment_id": "exp_1",
        "treatment_plan": [{"path": "/orders", "method": "POST"}],
        "binding_plan": [],
        "assertions": [],
        "observers": [],
        "safety_contract": {},
        "cleanup_plan": [],
    }
    base.update(overrides)
    return base


class TestOrchestratorFailClosed:
    def test_all_gates_pass_returns_ready(self):
        """All gates PASSED → verdict READY."""
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={
                "experiment": _make_experiment(),
                "primary_operation": _make_ir()["operations"][0],
            },
        )
        assert result["verdict"] == VERDICT_READY
        assert result["schema_version"] == "qualibug.v12-coverage-recovery-orchestrator.v2"

    def test_gate_receipts_present(self):
        """All five gate receipts are present."""
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={
                "experiment": _make_experiment(),
                "primary_operation": _make_ir()["operations"][0],
            },
        )
        gates = result["gate_receipts"]
        assert len(gates) == 5
        modules = {g["module"] for g in gates}
        assert "observer_resolution" in modules
        assert "compensation_relation" in modules
        assert "oracle_input_contract" in modules
        assert "binding_coverage_graph" in modules
        assert "fixture_dependency_dag" in modules

    def test_blocked_binding_blocks_verdict(self):
        """Binding graph BLOCKED → verdict BLOCKED."""
        exp = _make_experiment(
            binding_plan=[{"target": "orderId", "source_kind": "RANDOM_PLACEHOLDER"}],
            treatment_plan=[{"path": "/orders/{orderId}", "method": "PUT"}],
        )
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={"experiment": exp, "primary_operation": _make_ir()["operations"][0]},
        )
        assert result["verdict"] == VERDICT_BLOCKED
        assert any(g["module"] == "binding_coverage_graph" and g["status"] == GATE_BLOCKED for g in result["gate_receipts"])

    def test_no_module_can_be_informational(self):
        """No gate receipt has 'informational' or 'non-fatal' semantics."""
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={
                "experiment": _make_experiment(),
                "primary_operation": _make_ir()["operations"][0],
            },
        )
        for gate in result["gate_receipts"]:
            assert gate["status"] in (GATE_PASSED, GATE_BLOCKED, GATE_NOT_APPLICABLE,
                                      "SOURCE_DEPENDENT", "ENVIRONMENT_DEPENDENT")

    def test_blocked_gate_prevents_ready(self):
        """Any single BLOCKED gate prevents READY verdict."""
        exp = _make_experiment(
            binding_plan=[{"target": "id", "source_kind": "LLM_INVENTED_VALUE"}],
            treatment_plan=[{"path": "/orders/{id}", "method": "DELETE"}],
        )
        result = prepare_experiment_v12(
            obligation={"obligation_id": "obl_1"},
            behavior_ir=_make_ir(),
            compiler_context={"experiment": exp, "primary_operation": _make_ir()["operations"][0]},
        )
        assert result["verdict"] != VERDICT_READY
