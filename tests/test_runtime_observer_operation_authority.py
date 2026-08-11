from __future__ import annotations


def test_path_without_ir_operation_cannot_gain_observer_authority() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _operation_for_observation_path,
        _declared_observation_path,
    )

    operations = {
        "read-other": {
            "id": "read-other",
            "method": "GET",
            "path": "/api/other",
        }
    }

    assert _operation_for_observation_path("/api/missing", operations) == {}
    assert _declared_observation_path("/api/missing", operations) == ""


def test_same_path_multiple_writes_are_runtime_observer_ambiguous() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _operation_for_observation_path,
    )

    operations = {
        "post-item": {
            "id": "post-item",
            "method": "POST",
            "path": "/api/items/{id}",
        },
        "patch-item": {
            "id": "patch-item",
            "method": "PATCH",
            "path": "/api/items/{id}",
        },
    }

    assert _operation_for_observation_path("/api/items/{id}", operations) == {}


def test_declared_effect_observers_reject_path_only_synthetic_operation() -> None:
    from ai_test_asset_center.runtime_binding_graph import declared_effect_observers

    behavior_ir = {
        "operations": [
            {"id": "write-a", "method": "POST", "path": "/api/a"},
            {"id": "read-b", "method": "GET", "path": "/api/b"},
        ],
        "relations": [
            {
                "relation_type": "observes",
                "from_ref": "read-b",
                "to_ref": "entity-b",
                "entity_ref": "entity-b",
            }
        ],
        "entities": [{"id": "entity-b"}],
    }

    assert declared_effect_observers(
        {"path": "/api/a"},
        behavior_ir=behavior_ir,
        max_candidates=5,
    ) == []


def test_operation_id_cannot_be_reused_with_different_path() -> None:
    from ai_test_asset_center.runtime_binding_graph import declared_effect_observers

    behavior_ir = {
        "operations": [
            {"id": "write-a", "method": "POST", "path": "/api/a"},
            {"id": "read-a", "method": "GET", "path": "/api/a"},
        ],
        "relations": [],
    }

    assert declared_effect_observers(
        {"id": "write-a", "method": "POST", "path": "/api/forged"},
        behavior_ir=behavior_ir,
        max_candidates=5,
    ) == []
