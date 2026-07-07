from __future__ import annotations

import json

from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe
from ai_test_asset_center.real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from ai_test_asset_center.snapshot_observer_planner import plan_snapshot_observers_for_probe


def _probe(path: str) -> dict:
    return {
        "candidate_id": "QBPATH-1",
        "risk_type": "conservation_probe",
        "execution_policy": "disposable_sandbox_required",
        "endpoint": {"method": "POST", "path": path},
        "probe_plan": {"mutation": {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1}},
    }


def test_shared_placeholder_normalization_supports_common_path_styles() -> None:
    assert normalize_path_placeholders("/api/orders/:orderId/items/${lineId}") == "/api/orders/{orderId}/items/{lineId}"
    assert normalize_path_placeholders("/api/orders/<orderId>") == "/api/orders/{orderId}"
    assert normalize_path_placeholders("/api/orders/{orderId:int}") == "/api/orders/{orderId}"
    assert infer_path_params("/api/orders/:orderId/items/<lineId>") == ["orderId", "lineId"]
    assert path_has_placeholders("/api/orders/${orderId}")


def test_auto_fixture_uses_shared_placeholder_normalization_for_colon_paths() -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "amount": {"type": "number"}},
                                }
                            }
                        }
                    }
                }
            },
            "/api/orders/:order_id": {"get": {}, "delete": {}},
        },
    }

    bundle = build_auto_fixture_for_probe(
        _probe("/api/orders/:order_id"),
        config={"qualibug_auto_create_test_data": True, "api_doc_text": json.dumps(spec)},
    )

    assert bundle["setup_requests"][0]["path"] == "/api/orders"
    assert bundle["cleanup_requests"][0]["path"] == "/api/orders/{order_id}"
    assert bundle["snapshots"]["before"][0]["path"] == "/api/orders/{order_id}"
    assert bundle["path_params"]["order_id"].startswith("qb_auto_qbpath_1_")


def test_snapshot_observer_planner_normalizes_candidate_paths_before_binding() -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders/:order_id": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "tenant_id"},
                    ]
                }
            },
            "/api/orders": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "tenant_id"},
                    ]
                }
            },
        },
    }

    plan = plan_snapshot_observers_for_probe(
        _probe("/api/orders/:order_id"),
        spec=spec,
        primary_fixture_id="ord_123",
        seed="qbtest",
        max_observers=3,
    )

    assert plan["observers"]
    detail = next(item for item in plan["observers"] if item["observer_kind"] == "primary_resource_detail")
    assert detail["path"] == "/api/orders/{order_id}"
    assert detail["path_params"]["order_id"] == "ord_123"
