"""Tests for oracle input field coverage — SPEC v1.2.1 §10."""
import pytest
from ai_test_asset_center.oracle_input_contract import build_oracle_input_contract


class TestOracleInputFieldCoverage:
    def test_observer_output_contract_present(self):
        exp = {
            "experiment_id": "exp_1",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a1", "kind": "state_transition"}],
            "observers": [{"observer_id": "before_state", "kind": "before_state"}],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert "observer_output_contract" in result
        ooc = result["observer_output_contract"]
        assert "produced_phases" in ooc
        assert "produced_fields" in ooc
        assert "produced_entities" in ooc

    def test_field_subset_validation(self):
        """For governed writes, required_fields must be subset of produced_fields."""
        exp = {
            "experiment_id": "exp_1",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a1", "kind": "state_transition"}],
            "observers": [
                {"observer_id": "before_state", "kind": "before_state"},
                {"observer_id": "after_state", "kind": "after_state"},
            ],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        # With before_state and after_state observers, state_field should be produced
        assert result["overall_status"] == "COMPLETE"

    def test_blocked_oracle_input_incomplete_reason(self):
        """Missing observers → BLOCKED_ORACLE_INPUT_INCOMPLETE reason code."""
        exp = {
            "experiment_id": "exp_1",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a1", "kind": "conservation"}],
            "observers": [],  # No observers
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert result["reason_code"] == "BLOCKED_ORACLE_INPUT_INCOMPLETE"

    def test_read_only_skips_field_checks(self):
        """Non-governed writes skip field subset validation."""
        exp = {
            "experiment_id": "exp_1",
            "safety_contract": {"governed_write": False},
            "assertions": [{"assertion_id": "a1", "kind": "state_transition"}],
            "observers": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "COMPLETE"
