"""Tests for fixture DAG all-issue gate — SPEC v1.2.2 §11."""
import pytest
from ai_test_asset_center.v12_coverage_recovery_orchestrator import (
    prepare_experiment_v12, VERDICT_BLOCKED, GATE_BLOCKED, GATE_NOT_APPLICABLE,
)


class TestFixtureDagAllIssueGate:
    def test_no_fixtures_not_applicable(self):
        """No fixtures → fixture gate NOT_APPLICABLE."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders", "method": "POST"}],
               "binding_plan": [], "assertions": [], "observers": [],
               "safety_contract": {}, "fixtures": []}
        ir = {"operations": [{"id": "op1", "method": "POST", "path": "/orders"}], "relations": [], "actors": []}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        fix_gate = next(g for g in r["gate_receipts"] if g["module"] == "fixture_dependency_dag")
        assert fix_gate["status"] == GATE_NOT_APPLICABLE

    def test_fixture_dag_uses_execution_order(self):
        """Materializer uses fixture_dependency_dag.execution_order."""
        import inspect
        from ai_test_asset_center.experiment_fixture_materializer import materialize_experiment_fixtures
        src = inspect.getsource(materialize_experiment_fixtures)
        assert "execution_order" in src
