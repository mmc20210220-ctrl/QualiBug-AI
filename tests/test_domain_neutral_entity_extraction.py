from __future__ import annotations


def test_unknown_domain_single_collection_is_supported() -> None:
    from ai_test_asset_center.real_id_resolver import _extract_raw_entity_candidates

    assert _extract_raw_entity_candidates(
        {"widgets": [{"id": "W1"}, {"id": "W2"}]}
    ) == [{"id": "W1"}, {"id": "W2"}]


def test_multiple_unknown_business_collections_are_ambiguous() -> None:
    from ai_test_asset_center.real_id_resolver import _extract_raw_entity_candidates

    assert _extract_raw_entity_candidates(
        {
            "orders": [{"id": "O1"}],
            "users": [{"id": "U1"}],
        }
    ) == []


def test_generic_data_envelope_has_structural_priority() -> None:
    from ai_test_asset_center.real_id_resolver import _extract_raw_entity_candidates

    assert _extract_raw_entity_candidates(
        {
            "data": [{"id": "W1"}],
            "unrelated": [{"id": "X1"}],
        }
    ) == [{"id": "W1"}]


def test_resource_object_identity_beats_child_collection() -> None:
    from ai_test_asset_center.real_id_resolver import _extract_raw_entity_candidates

    body = {
        "id": "ORDER-1",
        "items": [{"id": "LINE-1"}],
    }
    assert _extract_raw_entity_candidates(body) == [body]
