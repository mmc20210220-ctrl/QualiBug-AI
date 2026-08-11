from __future__ import annotations


def test_successful_control_write_requires_cleanup_independent_of_family() -> None:
    from ai_test_asset_center.experiment_outcome_finalizer_core import (
        _actual_accepted_business_write,
    )

    assert _actual_accepted_business_write(
        exp={"risk_family": "authorization", "safety_contract": {}},
        steps_out=[
            {
                "phase": "control",
                "method": "POST",
                "status_code": 201,
                "governance_receipt": {"accepted": True},
            },
            {
                "phase": "treatment",
                "method": "POST",
                "status_code": 403,
                "governance_receipt": {"accepted": False},
            },
        ],
    ) is True


def test_rejected_only_write_does_not_manufacture_cleanup_requirement() -> None:
    from ai_test_asset_center.experiment_outcome_finalizer_core import (
        _actual_accepted_business_write,
    )

    assert _actual_accepted_business_write(
        exp={"risk_family": "validation", "safety_contract": {}},
        steps_out=[
            {
                "phase": "treatment",
                "method": "PATCH",
                "status_code": 422,
                "governance_receipt": {"accepted": False},
            }
        ],
    ) is False


def test_declared_ephemeral_exchange_remains_cleanup_exempt() -> None:
    from ai_test_asset_center.experiment_outcome_finalizer_core import (
        _actual_accepted_business_write,
    )

    assert _actual_accepted_business_write(
        exp={
            "risk_family": "validation",
            "safety_contract": {
                "cleanup_not_required": True,
                "business_effect_requirement": "NOT_APPLICABLE",
            },
        },
        steps_out=[
            {"phase": "treatment", "method": "POST", "status_code": 200}
        ],
    ) is False


def test_missing_restoration_proof_with_actual_write_removes_finding() -> None:
    from ai_test_asset_center.experiment_outcome_finalizer_core import (
        _fail_closed_actual_write_cleanup,
    )

    governed = _fail_closed_actual_write_cleanup(
        {
            "status": "EXECUTED",
            "finding": {"finding_id": "must-not-survive"},
            "finding_created": True,
            "environment_restored": True,
            "lifecycle_state": "EXPERIMENT_COMPLETED",
            "execution_finalization_receipt": {
                "derived_terminal_status": "TRUE_COMPLETED"
            },
            "execution_receipt": {
                "status": "EXECUTED",
                "environment_restored": True,
                "lifecycle_state": "EXPERIMENT_COMPLETED",
            },
        },
        cleanup_receipt=None,
        cleanup_failures=0,
    )

    assert governed["finding"] is None
    assert governed["finding_created"] is False
    assert governed["environment_restored"] is False
    assert governed["status"] == "EXECUTED_BUT_NOT_RESTORED"
    assert governed["lifecycle_state"] == "EXECUTED_BUT_NOT_RESTORED"
    assert governed["execution_finalization_receipt"] == {}
    assert governed["finalizer_block_reason"] == (
        "BLOCKED_CLEANUP_EQUIVALENCE_MISSING"
    )


def test_facade_composition_hooks_are_mirrored_into_mechanics(monkeypatch) -> None:
    import ai_test_asset_center.experiment_outcome_finalizer_core as facade

    def _observer(*args, **kwargs):
        return []

    def _oracle(*args, **kwargs):
        return {"status": "PROPERTY_HELD"}

    def _cleanup(*args, **kwargs):
        return {"equivalence_status": "EQUIVALENT"}

    monkeypatch.setattr(facade, "observe_experiment_requirements", _observer)
    monkeypatch.setattr(facade, "evaluate_contract_oracle", _oracle)
    monkeypatch.setattr(facade, "evaluate_cleanup_equivalence", _cleanup)

    facade._sync_composed_finalizer_hooks()

    assert facade._core.observe_experiment_requirements is _observer
    assert facade._core.evaluate_contract_oracle is _oracle
    assert facade._core.evaluate_cleanup_equivalence is _cleanup
