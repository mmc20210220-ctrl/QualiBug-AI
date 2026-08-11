from __future__ import annotations


def _experiment(step: dict) -> dict:
    return {
        "control_plan": [step],
        "treatment_plan": [],
    }


def test_missing_ir_method_never_defaults_to_get() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _operation_method_authority,
    )

    ok, reason, detail = _operation_method_authority(
        _experiment({"operation_ref": "op-1"}),
        {"operations": [{"id": "op-1", "path": "/api/items"}]},
    )

    assert ok is False
    assert reason == "BLOCKED_MISSING_OPERATION"
    assert detail == "source_declared_method_missing:op-1"


def test_step_method_cannot_override_ir_method() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _operation_method_authority,
    )

    ok, reason, detail = _operation_method_authority(
        _experiment({"operation_ref": "op-1", "method": "POST"}),
        {
            "operations": [
                {"id": "op-1", "method": "GET", "path": "/api/items"}
            ]
        },
    )

    assert ok is False
    assert reason == "BLOCKED_OPERATION_CONTRACT_DRIFT"
    assert detail == "method_mismatch:op-1:step=POST:ir=GET"


def test_matching_source_method_passes_authority_gate() -> None:
    from ai_test_asset_center.experiment_runtime_support import (
        _operation_method_authority,
    )

    ok, reason, detail = _operation_method_authority(
        _experiment({"operation_ref": "op-1", "method": "PATCH"}),
        {
            "operations": [
                {"id": "op-1", "method": "PATCH", "path": "/api/items/{id}"}
            ]
        },
    )

    assert ok is True
    assert reason == ""
    assert detail == ""
