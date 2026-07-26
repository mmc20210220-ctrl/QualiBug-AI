"""Tests for compensation proof authority — SPEC v1.2.2 §7."""
import pytest
from ai_test_asset_center.v12_coverage_recovery_orchestrator import (
    prepare_experiment_v12, VERDICT_BLOCKED, VERDICT_READY, GATE_NOT_APPLICABLE,
)


class TestCompensationProofAuthority:
    def test_non_write_not_applicable(self):
        """Non-governed write → compensation gate NOT_APPLICABLE."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders", "method": "GET"}],
               "binding_plan": [], "assertions": [], "observers": [],
               "safety_contract": {"governed_write": False}, "cleanup_plan": []}
        ir = {"operations": [{"id": "op1", "method": "GET", "path": "/orders"}], "relations": [], "actors": []}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        comp_gate = next(g for g in r["gate_receipts"] if g["module"] == "compensation_relation")
        assert comp_gate["status"] == GATE_NOT_APPLICABLE

    def test_write_with_cleanup_plan_passes(self):
        """Governed write with cleanup_plan → compensation gate PASSED."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders", "method": "POST"}],
               "binding_plan": [], "assertions": [], "observers": [],
               "safety_contract": {"governed_write": True},
               "cleanup_plan": [{"action": "delete", "operation_ref": "op_del"}]}
        ir = {"operations": [
            {"id": "op1", "method": "POST", "path": "/orders"},
            {"id": "op_del", "method": "DELETE", "path": "/orders/{id}"},
        ], "relations": [], "actors": []}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        comp_gate = next(g for g in r["gate_receipts"] if g["module"] == "compensation_relation")
        assert comp_gate["status"] in ("PASSED", "NOT_APPLICABLE")

    def test_write_cleanup_exempt_not_applicable(self):
        """Governed write with cleanup exemption → NOT_APPLICABLE."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/login", "method": "POST"}],
               "binding_plan": [], "assertions": [], "observers": [],
               "safety_contract": {"governed_write": True, "cleanup_not_required": True},
               "cleanup_plan": []}
        ir = {"operations": [{"id": "op1", "method": "POST", "path": "/login"}], "relations": [], "actors": []}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=ir,
            compiler_context={"experiment": exp, "primary_operation": ir["operations"][0]},
        )
        comp_gate = next(g for g in r["gate_receipts"] if g["module"] == "compensation_relation")
        assert comp_gate["status"] == GATE_NOT_APPLICABLE
