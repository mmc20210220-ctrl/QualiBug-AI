"""Concurrency quantity stress: scale shared qty from observed free-pool capacity."""
from __future__ import annotations

from ai_test_asset_center.experiment_runtime_support import (
    _select_observed_capacity,
    stress_concurrency_quantity_bodies,
)


def test_select_observed_capacity_prefers_free_pool_over_locked() -> None:
    selected = _select_observed_capacity(
        {"available_qty": 5, "locked_qty": 2, "safety_stock": 1},
        "qty",
    )
    assert selected == ("available_qty", 5)


def test_select_observed_capacity_fails_closed_when_ambiguous() -> None:
    assert _select_observed_capacity(
        {"available_qty": 5, "free_qty": 4},
        "qty",
    ) is None


def test_stress_concurrency_scales_unique_qty_from_observed_capacity(
    monkeypatch,
) -> None:
    operations = {
        "reserve": {
            "id": "reserve",
            "method": "POST",
            "path": "/api/inventory/reserve",
            "read_write": "write",
            "request_example": {"sku": "SKU-1", "qty": 1, "orderId": "o-1"},
        },
        "read": {
            "id": "read",
            "method": "GET",
            "path": "/api/inventory/{sku}",
            "read_write": "read",
        },
    }
    steps = [
        {
            "operation_ref": "reserve",
            "body": {"sku": "SKU-1", "qty": 1, "orderId": "o-1"},
        },
        {
            "operation_ref": "reserve",
            "body": {"sku": "SKU-1", "qty": 1, "orderId": "o-1"},
        },
    ]

    def fake_http(**kwargs):
        assert kwargs["path"] == "/api/inventory/SKU-1"
        return {
            "status_code": 200,
            "body": {"sku": "SKU-1", "available_qty": 5, "locked_qty": 0},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._run_http_step",
        fake_http,
    )
    receipt = stress_concurrency_quantity_bodies(
        steps=steps,
        operations=operations,
        runtime_bindings={"sku": "SKU-1"},
        base_url="http://localhost:8080",
        actor_token="token",
    )
    assert receipt["status"] == "APPLIED"
    assert receipt["stressed_qty"] == 3
    assert steps[0]["body"]["qty"] == 3
    assert steps[1]["body"]["qty"] == 3


def test_stress_concurrency_skips_adjust_delta_without_qty_alignment(
    monkeypatch,
) -> None:
    operations = {
        "adjust": {
            "id": "adjust",
            "method": "POST",
            "path": "/api/inventory/admin/adjust",
            "read_write": "write",
            "request_example": {"sku": "SKU-1", "delta": 10, "reason": "audit"},
        },
        "read": {
            "id": "read",
            "method": "GET",
            "path": "/api/inventory/{sku}",
            "read_write": "read",
        },
    }
    steps = [
        {"operation_ref": "adjust", "body": {"sku": "SKU-1", "delta": 10}},
        {"operation_ref": "adjust", "body": {"sku": "SKU-1", "delta": 10}},
    ]
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_runtime_support._run_http_step",
        lambda **kwargs: {
            "status_code": 200,
            "body": {"sku": "SKU-1", "available_qty": 5, "locked_qty": 0},
        },
    )
    receipt = stress_concurrency_quantity_bodies(
        steps=steps,
        operations=operations,
        runtime_bindings={"sku": "SKU-1"},
        base_url="http://localhost:8080",
        actor_token="token",
    )
    assert receipt["status"] == "SKIPPED"
    assert steps[0]["body"]["delta"] == 10
