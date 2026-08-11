from __future__ import annotations


def test_multiple_materializable_identity_observers_are_ambiguous(monkeypatch) -> None:
    import ai_test_asset_center.experiment_runtime_support as runtime

    monkeypatch.setattr(
        runtime,
        "_strict_declared_effect_observers",
        lambda *args, **kwargs: [
            {
                "operation_ref": "read-one",
                "method": "GET",
                "path": "/api/items/{id}",
            },
            {
                "operation_ref": "read-detail",
                "method": "GET",
                "path": "/api/items/{id}/detail",
            },
        ],
    )
    monkeypatch.setattr(
        runtime._core,
        "_runtime_setup_value_from_response",
        lambda body, name: body.get(name) if isinstance(body, dict) else None,
    )

    result = runtime._response_bound_observation_path(
        {"id": "create", "method": "POST", "path": "/api/items"},
        {},
        {"id": "I-1"},
    )

    assert result == {}


def test_unique_response_bound_observer_uses_declared_method(monkeypatch) -> None:
    import ai_test_asset_center.experiment_runtime_support as runtime

    monkeypatch.setattr(
        runtime,
        "_strict_declared_effect_observers",
        lambda *args, **kwargs: [
            {
                "operation_ref": "head-one",
                "method": "HEAD",
                "path": "/api/items/{id}",
            }
        ],
    )
    monkeypatch.setattr(
        runtime._core,
        "_runtime_setup_value_from_response",
        lambda body, name: body.get(name) if isinstance(body, dict) else None,
    )

    result = runtime._response_bound_observation_path(
        {"id": "create", "method": "POST", "path": "/api/items"},
        {},
        {"id": "I-1"},
    )

    assert result == {
        "operation_ref": "head-one",
        "method": "HEAD",
        "path": "/api/items/I-1",
        "path_template": "/api/items/{id}",
    }
