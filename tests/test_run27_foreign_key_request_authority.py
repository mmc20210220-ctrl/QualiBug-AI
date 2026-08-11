from __future__ import annotations


def _operation() -> dict:
    return {
        "id": "create-line",
        "method": "POST",
        "path": "/api/lines",
        "request_schema": {
            "type": "object",
            "properties": {
                "orderId": {"type": "integer", "x-foreign-key": True},
                "couponCode": {"type": "string", "x-foreign-key": True},
            },
        },
    }


def test_numeric_one_is_not_guessed_to_be_fake_foreign_key() -> None:
    from ai_test_asset_center.foreign_key_request_authority import (
        foreign_key_materialization_violations,
    )

    assert foreign_key_materialization_violations(
        {"orderId": 1},
        _operation(),
    ) == []


def test_business_words_are_not_guessed_to_be_fake_foreign_keys() -> None:
    from ai_test_asset_center.foreign_key_request_authority import (
        foreign_key_materialization_violations,
    )

    for value in ("test", "unknown", "dummy", "sample", "default"):
        assert foreign_key_materialization_violations(
            {"couponCode": value},
            _operation(),
        ) == []


def test_surviving_placeholder_is_a_proven_materialization_failure() -> None:
    from ai_test_asset_center.foreign_key_request_authority import (
        foreign_key_materialization_violations,
    )

    assert foreign_key_materialization_violations(
        {"couponCode": "{couponCode}"},
        _operation(),
    ) == ["couponCode"]
    assert foreign_key_materialization_violations(
        {"couponCode": "<couponCode>"},
        _operation(),
    ) == ["couponCode"]


def test_qualibug_unresolved_sentinel_is_rejected() -> None:
    from ai_test_asset_center.foreign_key_request_authority import (
        foreign_key_materialization_violations,
    )

    assert foreign_key_materialization_violations(
        {"couponCode": "QUALIBUG_ACTOR_IDENTITY_UNRESOLVED"},
        _operation(),
    ) == ["couponCode"]


def test_non_foreign_key_placeholder_is_not_reclassified_by_fk_guard() -> None:
    from ai_test_asset_center.foreign_key_request_authority import (
        foreign_key_materialization_violations,
    )

    operation = _operation()
    operation["request_schema"]["properties"]["note"] = {"type": "string"}
    assert foreign_key_materialization_violations(
        {"note": "{note}"},
        operation,
    ) == []
