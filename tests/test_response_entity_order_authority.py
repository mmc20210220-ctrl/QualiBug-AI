from __future__ import annotations


def test_entity_candidates_keep_target_response_order() -> None:
    from ai_test_asset_center.real_id_resolver import _extract_entity_candidates

    body = {
        "items": [
            {"id": "FIRST", "balance": 0, "qty": 0},
            {"id": "SECOND", "balance": 9999, "qty": 88},
        ]
    }

    rows = _extract_entity_candidates(body)

    assert [row["id"] for row in rows] == ["FIRST", "SECOND"]


def test_business_richness_does_not_change_single_identity_binding_choice() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    body = {
        "items": [
            {"id": "FIRST", "amount": 0},
            {"id": "SECOND", "amount": 100000},
        ]
    }

    bindings = bind_entity_fields(body, "/resources/{id}")

    assert bindings["id"] == "FIRST"
