from __future__ import annotations


def test_binding_target_may_use_created_resource_generic_id() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _strict_validate_fixture_preconditions,
    )

    failures = _strict_validate_fixture_preconditions(
        {
            "assertions": [],
            "treatment_plan": [
                {"body": {"order_id": "{order_id}"}}
            ],
        },
        {"id": "order-17"},
        "order_id",
    )

    assert failures == []


def test_different_identity_field_cannot_borrow_fixture_generic_id() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _strict_validate_fixture_preconditions,
    )

    failures = _strict_validate_fixture_preconditions(
        {
            "assertions": [],
            "treatment_plan": [
                {"body": {"addressId": "{order_id}"}}
            ],
        },
        {"id": "order-17"},
        "order_id",
    )

    assert {
        "field": "addressId",
        "reason": "fixture_precondition_identity_authority_mismatch",
        "target": "order_id",
    } in failures


def test_explicit_different_identity_field_remains_authoritative() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _strict_validate_fixture_preconditions,
    )

    failures = _strict_validate_fixture_preconditions(
        {
            "assertions": [],
            "treatment_plan": [
                {"body": {"addressId": "{order_id}"}}
            ],
        },
        {"id": "order-17", "addressId": "address-4"},
        "order_id",
    )

    assert failures == []
