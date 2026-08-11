from __future__ import annotations


def test_runtime_candidates_do_not_reapply_business_richness_ranking() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _runtime_entity_candidates,
    )

    rows = _runtime_entity_candidates(
        {
            "items": [
                {"id": "FIRST", "balance": 0, "qty": 0},
                {"id": "SECOND", "balance": 9999, "qty": 88},
            ]
        }
    )

    assert [row["id"] for row in rows] == ["FIRST", "SECOND"]


def test_preferred_write_body_is_not_identity_selection_authority() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _select_runtime_binding,
    )

    binding = _select_runtime_binding(
        {
            "items": [
                {"id": "FIRST", "status": "ACTIVE"},
                {"id": "SECOND", "status": "BLOCKED"},
            ]
        },
        "/resources/{id}",
        preferred_body={"status": "ACTIVE"},
    )

    assert binding["id"] == "FIRST"


def test_explicit_state_scope_remains_selection_authority() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _select_runtime_binding,
    )

    binding = _select_runtime_binding(
        [
            {"id": "FIRST", "status": "ACTIVE"},
            {"id": "SECOND", "status": "BLOCKED"},
        ],
        "@state=blocked@/resources/{id}",
        preferred_body={"status": "ACTIVE"},
    )

    assert binding["id"] == "SECOND"
