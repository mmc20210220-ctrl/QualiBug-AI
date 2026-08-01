from __future__ import annotations

from ai_test_asset_center.cleanup_plan_validator import validate_cleanup_plan
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.validation_obligation_expander import expand_validation_obligation


def test_source_declared_read_only_post_does_not_require_cleanup() -> None:
    """A source-declared validation POST is readable evidence, not a mutation."""
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-read-only-post",
            "risk_family": "validation",
            "property": {
                "template": "validation_rejection",
                "operation_ref": "op-validate-coupon",
                "actor_ref": "actor-buyer",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-validate-coupon"],
            "required_observers": ["http_response"],
            "source_refs": [{"source_id": "api", "locator": "POST /api/coupons/validate"}],
        },
        behavior_ir={
            "operations": [{
                "id": "op-validate-coupon",
                "method": "POST",
                "path": "/api/coupons/validate",
                "read_write": "read",
                "side_effect_class": "read",
                "request_example": {"couponCode": "SOURCE_DECLARED_VALUE"},
                "source_refs": [{"source_id": "api", "locator": "POST /api/coupons/validate"}],
            }],
            "actors": [{
                "id": "actor-buyer",
                "role": "buyer",
                "runtime_bound": True,
                "credential_secret_ref": "secret_ref:test_accounts:buyer",
            }],
            "relations": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment
    assert experiment["cleanup_plan"] == []
    assert experiment["safety_contract"]["governed_write"] is False
    assert experiment["assertions"][0]["kind"] == "http_status_class"
    assert experiment["assertions"][1]["kind"] == "http_status_class"
    assert validate_cleanup_plan(
        experiment,
        {
            "operations": [{
                "id": "op-validate-coupon",
                "method": "POST",
                "path": "/api/coupons/validate",
                "read_write": "read",
                "side_effect_class": "read",
            }],
        },
    )["valid"] is True


def test_read_only_post_validation_does_not_require_business_effect_observer() -> None:
    variants = expand_validation_obligation(
        {
            "obligation_id": "obl-read-only-post-validation",
            "risk_family": "validation",
            "required_observers": ["http_response"],
            "property": {"operation_ref": "op-validate-coupon"},
        },
        operation={
            "id": "op-validate-coupon",
            "method": "POST",
            "path": "/api/coupons/validate",
            "read_write": "read",
            "side_effect_class": "read",
        },
    )

    assert variants
    assert "business_effect" not in variants[0]["required_observers"]
