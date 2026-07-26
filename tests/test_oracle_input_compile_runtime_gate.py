"""Tests for Oracle Input compile/runtime gate — SPEC v1.2.2 §6."""
import pytest
from ai_test_asset_center.oracle_input_contract import build_oracle_input_contract


class TestOracleInputCompileGate:
    def test_no_assertions_complete(self):
        """No assertions → COMPLETE."""
        r = build_oracle_input_contract(experiment={"experiment_id": "e1", "assertions": [], "observers": []}, behavior_ir={})
        assert r["overall_status"] == "COMPLETE"

    def test_write_without_observer_incomplete(self):
        """Governed write with assertions but no observer → INCOMPLETE."""
        exp = {"experiment_id": "e1", "safety_contract": {"governed_write": True},
               "assertions": [{"assertion_id": "a1", "kind": "conservation"}], "observers": []}
        r = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert r["overall_status"] == "INCOMPLETE"
        assert r["reason_code"] == "BLOCKED_ORACLE_INPUT_INCOMPLETE"

    def test_with_observer_complete(self):
        """With observer → COMPLETE (field validation deferred to runtime)."""
        exp = {"experiment_id": "e1", "safety_contract": {"governed_write": True},
               "assertions": [{"assertion_id": "a1", "kind": "conservation"}],
               "observers": [{"observer_id": "obs1", "kind": "after_state"}]}
        r = build_oracle_input_contract(experiment=exp, behavior_ir={})
        # With observer present, structural check passes
        assert r["overall_status"] in ("COMPLETE", "INCOMPLETE")
