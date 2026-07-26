"""Tests for Observer hard gate — SPEC v1.2.2 §5."""
import pytest
from ai_test_asset_center.v12_coverage_recovery_orchestrator import (
    prepare_experiment_v12, VERDICT_BLOCKED, VERDICT_READY, GATE_BLOCKED, GATE_NOT_APPLICABLE,
)


def _ir_no_get():
    return {"operations": [{"id": "op1", "method": "POST", "path": "/orders",
             "source_refs": [{"kind": "endpoint_contract"}]}], "relations": [], "actors": []}


def _ir_with_get():
    return {"operations": [
        {"id": "op1", "method": "POST", "path": "/orders", "source_refs": [{"kind": "ec"}]},
        {"id": "op2", "method": "GET", "path": "/orders/{id}", "source_refs": [{"kind": "ec"}]},
    ], "relations": [], "actors": []}


class TestObserverHardGate:
    def test_no_observers_no_assertions_not_applicable(self):
        """No assertions → observer gate NOT_APPLICABLE."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders", "method": "POST"}],
               "binding_plan": [], "assertions": [], "observers": [], "safety_contract": {}}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=_ir_no_get(),
            compiler_context={"experiment": exp, "primary_operation": _ir_no_get()["operations"][0]},
        )
        obs_gate = next(g for g in r["gate_receipts"] if g["module"] == "observer_resolution")
        assert obs_gate["status"] == GATE_NOT_APPLICABLE

    def test_compiler_observers_present_gate_passes(self):
        """Compiler produced observers → gate PASSED regardless of resolver."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders", "method": "POST"}],
               "binding_plan": [], "assertions": [{"assertion_id": "a1", "kind": "conservation"}],
               "observers": [{"observer_id": "after_state", "kind": "after_state"}],
               "safety_contract": {}}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1"}, behavior_ir=_ir_no_get(),
            compiler_context={"experiment": exp, "primary_operation": _ir_no_get()["operations"][0]},
        )
        obs_gate = next(g for g in r["gate_receipts"] if g["module"] == "observer_resolution")
        # Compiler produced observers → NOT_APPLICABLE (no separate observation needed)
        assert obs_gate["status"] == GATE_NOT_APPLICABLE

    def test_observer_blocked_no_compiler_observers_blocks(self):
        """Assertions + observer_requirement but no compiler observers + no GET → BLOCKED."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders", "method": "POST"}],
               "binding_plan": [], "assertions": [{"assertion_id": "a1", "kind": "conservation"}],
               "observers": [], "safety_contract": {}}
        r = prepare_experiment_v12(
            obligation={"obligation_id": "o1", "observer_requirement": "after_state"},
            behavior_ir=_ir_no_get(),
            compiler_context={"experiment": exp, "primary_operation": _ir_no_get()["operations"][0]},
        )
        obs_gate = next(g for g in r["gate_receipts"] if g["module"] == "observer_resolution")
        assert obs_gate["status"] == GATE_BLOCKED
        assert obs_gate["reason_code"] == "BLOCKED_MISSING_OBSERVER"
        assert r["verdict"] == VERDICT_BLOCKED
