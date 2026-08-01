from __future__ import annotations

from ai_test_asset_center.experiment_lifecycle_runtime import (
    new_experiment_lifecycle_ledger,
)


def _ledger(experiment: dict):
    return new_experiment_lifecycle_ledger(
        experiment,
        experiment_id="experiment-1",
        obligation_id="obligation-1",
        campaign_id="campaign-1",
        run_id="run-1",
    )


def test_zero_target_frozen_flow_data_is_fixtureless_by_authority() -> None:
    ledger = _ledger(
        {
            "flow_data_requirement": {
                "status": "FROZEN",
                "binding_targets": [],
                "materialized_before_measurement_targets": [],
                "step_requirements": [],
            },
            "treatment_plan": [{"step_id": "treatment-1"}],
        }
    )

    assert ledger.fixture_required is False
    assert ledger.fixture_id == "NOT_APPLICABLE"
    assert ledger.protocol_id == "NOT_APPLICABLE"
    assert ledger.required_step_ids == ["treatment-1"]


def test_binding_only_setup_does_not_invent_a_fixture() -> None:
    ledger = _ledger(
        {
            "setup_plan": [
                {"action": "resolve_bindings"},
                {"action": "query_entity_binding"},
            ],
            "treatment_plan": [{"step_id": "treatment-1"}],
        }
    )

    assert ledger.fixture_required is False
    assert ledger.fixture_id == "NOT_APPLICABLE"
