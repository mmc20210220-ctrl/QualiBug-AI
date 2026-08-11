from __future__ import annotations

from ai_test_asset_center import experiment_protocols as protocols


def test_account_state_precondition_source_marker_is_authoritative() -> None:
    result = {
        "status": "COMPILED",
        "control_plan": [{"step_id": "control_1"}],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "mutation": {
                "json_path": "$.email",
                "constraint": "account_state_not_active:test@example.com",
                "source": "account_state_precondition",
            },
        }],
        "assertion": {"kind": "validation_rejection"},
    }
    assert protocols._validation_authority_problem(
        result=result,
        property_spec={
            "invariant_ref": "bir:active-login",
            "source_rule_statement": "仅 ACTIVE 用户可登录，禁用用户不得登录",
        },
    ) == ""


def test_account_state_marker_without_source_rule_fails_closed() -> None:
    result = {
        "status": "COMPILED",
        "treatment_plan": [{
            "step_id": "treatment_1",
            "mutation": {
                "source": "account_state_precondition",
                "constraint": "account_state_not_active:test@example.com",
            },
        }],
    }
    assert protocols._validation_authority_problem(
        result=result,
        property_spec={},
    ) == "source_validation_mutation_lacks_rule:account_state_precondition"
