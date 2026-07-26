"""An untested implication must not report as verified.

``_evaluate_state_implication`` returned an empty reason_code -- which means PASS --
whenever the trigger state was not observed, labelling the result "vacuously_true". So
"if the root entity reached state X then the related entity must satisfy C" passed
green every time X was never seen.

Vacuous truth is sound for an invariant evaluated over arbitrary given data. It is not
sound here, because establishing the trigger state is the experiment's OWN job. A
failure to reach X -- an unestablished precondition, a state field read from the wrong
key, a write that silently did not apply -- is indistinguishable from "the trigger
legitimately did not occur", and the first three are precisely the conditions the
experiment exists to detect.

The behaviour change landed in the same file as the kind-to-evidence contract and so
was committed under 8e78d58; these tests document and pin it.
"""

from __future__ import annotations

from ai_test_asset_center.assertion_dsl_base import _evaluate_state_implication


TRIGGER_NOT_OBSERVED = "STATE_IMPLICATION_TRIGGER_NOT_OBSERVED"


def _spec() -> dict[str, object]:
    """The real spec shape: condition/constraint nested under structured_expression.

    root_field and root_value are read from ``condition`` only — a flat spec silently
    yields an empty root_value, which makes condition_met False for the wrong reason.
    """
    return {
        "type": "state_implication",
        "structured_expression": {
            "condition": {
                "entity": "primary",
                "field": "lifecycle_state",
                "value": "TERMINAL",
            },
            "constraint": {
                "entity": "dependent",
                "field": "held_quantity",
                "value": "0",
                "operator": "EQ",
            },
        },
    }


def test_unobserved_trigger_is_indeterminate_not_pass() -> None:
    observations = {
        "multi_entity_state": {
            "primary": {"after": [{"lifecycle_state": "ACTIVE"}]},
        }
    }
    reason_code, expected, actual = _evaluate_state_implication(_spec(), observations)

    # An empty reason_code is PASS in this contract; it must not be empty here.
    assert reason_code == TRIGGER_NOT_OBSERVED
    assert reason_code != ""
    assert expected["implication"] == "not_tested"
    assert actual["condition_met"] is False


def test_unobserved_trigger_is_not_reported_as_a_violation() -> None:
    """The caller keys off the "VIOLATED" substring to decide violation vs missing.

    See the cross_entity_consistency branch in assertion_dsl_base: a reason code
    containing "VIOLATED" is a test failure, anything else is missing evidence. An
    untested implication is missing evidence, not a defect in the target.
    """
    observations = {"multi_entity_state": {"primary": {"after": [{"lifecycle_state": "ACTIVE"}]}}}
    reason_code, _expected, _actual = _evaluate_state_implication(_spec(), observations)

    assert "VIOLATED" not in reason_code


def test_reason_names_the_state_that_was_required() -> None:
    """The coverage statement has to say what was not reached, or it is not actionable."""
    observations = {"multi_entity_state": {"primary": {"after": [{"lifecycle_state": "ACTIVE"}]}}}
    _reason_code, expected, actual = _evaluate_state_implication(_spec(), observations)

    assert expected["required_root_state"] == "TERMINAL"
    assert actual["observed_root_field"] == "lifecycle_state"


def test_observed_trigger_still_evaluates_the_constraint() -> None:
    """Guard against over-blocking: a real trigger must still be judged.

    With the trigger observed and the dependent entity satisfying the constraint, the
    implication holds and the result must be PASS (empty reason code).
    """
    observations = {
        "multi_entity_state": {
            "primary": {"after": [{"lifecycle_state": "TERMINAL"}]},
            "dependent": {"after": [{"held_quantity": "0"}]},
        }
    }
    reason_code, _expected, actual = _evaluate_state_implication(_spec(), observations)

    assert actual["condition_met"] is True
    assert reason_code == "", f"a satisfied implication must PASS, got {reason_code!r}"


def test_observed_trigger_with_broken_constraint_is_a_violation() -> None:
    observations = {
        "multi_entity_state": {
            "primary": {"after": [{"lifecycle_state": "TERMINAL"}]},
            "dependent": {"after": [{"held_quantity": "7"}]},
        }
    }
    reason_code, _expected, actual = _evaluate_state_implication(_spec(), observations)

    assert actual["condition_met"] is True
    assert "VIOLATED" in reason_code, (
        f"a broken implication must be a violation, got {reason_code!r}"
    )
