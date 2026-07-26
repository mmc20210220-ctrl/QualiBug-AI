"""Tests for oracle_input_contract.py — SPEC v1.2 §11 / §14.5."""
import pytest
from ai_test_asset_center.oracle_input_contract import (
    build_oracle_input_contract,
    _ASSERTION_REQUIREMENTS,
)


def _make_complete_experiment():
    """Experiment with full observer coverage for state_transition."""
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "safety_contract": {"governed_write": True},
        "assertions": [
            {"assertion_id": "a1", "kind": "state_transition"},
        ],
        "observers": [
            {"observer_id": "obs_before", "kind": "before_state"},
            {"observer_id": "obs_after", "kind": "after_state"},
        ],
        "control_plan": [],
    }


def _make_authorization_experiment(with_control=True):
    """Experiment with authorization assertion."""
    exp = {
        "experiment_id": "exp_auth",
        "obligation_id": "obl_auth",
        "safety_contract": {"governed_write": False},
        "assertions": [
            {"assertion_id": "a_auth", "kind": "authorization"},
        ],
        "observers": [
            {"observer_id": "obs_1", "kind": "state_read"},
        ],
        "control_plan": [{"path": "/orders", "method": "GET"}] if with_control else [],
    }
    return exp


class TestOracleInputComplete:
    def test_full_coverage_complete(self):
        """required fields 全覆盖 → COMPLETE"""
        result = build_oracle_input_contract(
            experiment=_make_complete_experiment(),
            behavior_ir={},
        )
        assert result["overall_status"] == "COMPLETE"
        assert result["total_missing"] == 0
        assert result["reason_code"] == ""

    def test_schema_version(self):
        result = build_oracle_input_contract(
            experiment=_make_complete_experiment(),
            behavior_ir={},
        )
        assert result["schema_version"] == "qualibug.oracle-input-contract-batch.v1"
        assert result["contracts"][0]["schema_version"] == "qualibug.oracle-input-contract.v1"


class TestOracleInputIncomplete:
    def test_missing_before_observer(self):
        """缺 before → INCOMPLETE"""
        exp = _make_complete_experiment()
        exp["observers"] = [{"observer_id": "obs_after", "kind": "after_state"}]
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert "phase:before" in result["missing_inputs"]

    def test_missing_after_write_observer(self):
        """缺 after-write → INCOMPLETE"""
        exp = _make_complete_experiment()
        exp["observers"] = [{"observer_id": "obs_before", "kind": "before_state"}]
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert "phase:after_write" in result["missing_inputs"]

    def test_missing_control_for_authorization(self):
        """Authorization requires control plan."""
        exp = _make_authorization_experiment(with_control=False)
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert "control_plan_missing" in result["missing_inputs"]

    def test_conservation_missing_after_cleanup(self):
        """Conservation requires after_cleanup phase."""
        exp = {
            "experiment_id": "exp_cons",
            "obligation_id": "obl_cons",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a_cons", "kind": "conservation"}],
            "observers": [
                {"observer_id": "obs_b", "kind": "before_state"},
                {"observer_id": "obs_a", "kind": "after_state"},
            ],
            "control_plan": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert "phase:after_cleanup" in result["missing_inputs"]


class TestOracleInputEdgeCases:
    def test_empty_experiment(self):
        result = build_oracle_input_contract(experiment={}, behavior_ir={})
        assert result["overall_status"] == "COMPLETE"
        assert result["contracts"] == []

    def test_unknown_assertion_kind_uses_defaults(self):
        exp = {
            "experiment_id": "exp_x",
            "obligation_id": "obl_x",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a_x", "kind": "unknown_kind"}],
            "observers": [{"observer_id": "obs", "kind": "after_state"}],
            "control_plan": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        # Default requires after_write, observer has after_state → COMPLETE
        assert result["contracts"][0]["assertion_kind"] == "unknown_kind"

    def test_fingerprint_deterministic(self):
        exp = _make_complete_experiment()
        r1 = build_oracle_input_contract(experiment=exp, behavior_ir={})
        r2 = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert r1["contracts"][0]["fingerprint"] == r2["contracts"][0]["fingerprint"]

    def test_non_governed_write_skips_phase_checks(self):
        """Read-only experiments don't need before/after observers."""
        exp = {
            "experiment_id": "exp_read",
            "obligation_id": "obl_read",
            "safety_contract": {"governed_write": False},
            "assertions": [{"assertion_id": "a_st", "kind": "state_transition"}],
            "observers": [],
            "control_plan": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        # state_transition doesn't require control, and not governed_write → no phase checks
        assert result["overall_status"] == "COMPLETE"

    def test_assertion_requirements_registry(self):
        """All known assertion kinds have requirements defined."""
        expected_kinds = {
            "authorization", "isolation", "visibility", "state_transition",
            "conservation", "idempotency", "validation_rejection",
            "concurrency", "cross_surface_consistency",
        }
        assert set(_ASSERTION_REQUIREMENTS.keys()) == expected_kinds
