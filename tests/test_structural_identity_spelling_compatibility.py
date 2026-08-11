from __future__ import annotations


def test_single_identity_param_keeps_snake_and_camel_spelling_candidates() -> None:
    from ai_test_asset_center.real_id_resolver import param_field_candidates

    candidates = param_field_candidates("userId")

    assert "userId" in candidates
    assert "user_id" in candidates
    assert "id" in candidates


def test_single_identity_binding_accepts_snake_case_response() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"user_id": "U-9"},
        "/users/{userId}",
    )

    assert bindings["userId"] == "U-9"
