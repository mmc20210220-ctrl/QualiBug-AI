"""Regression: fixture cleanup identity binds the created resource, not a
child line item's natural key.

run24 failure chain (evidence: sandbox_write_audit.jsonl, 2026-08-10 window):
- fixture setup ``POST /api/orders`` succeeded with 201, response body
  ``{...order, items: [{sku: SKU-PHONE-001, ...}]}`` (OrderWithItems — the
  order row carries ``id`` (uuid), the line items carry only the product
  natural key ``sku``).
- cleanup then sent ``POST /api/orders/SKU-PHONE-001/cancel`` → 500
  (``无效的类型 uuid 输入语法: "SKU-PHONE-001"``) → cleanup receipt failed →
  the whole experiment HARNESS_FAILED (33 such cancel attempts in run24).

Root causes (both structural, industry-neutral):
- the entity-candidate extractor treated the create response's ``items``
  child relation as a list envelope and returned the line-item rows,
  discarding the order object that carries the resource identity;
- the bare ``{id}`` placeholder candidate list included cross-entity natural
  keys (``sku``/``code``/``business_no``), so a line item's product sku could
  masquerade as the order resource identity.

This test drives the real main chain (materialize_experiment_fixtures →
cleanup-identity projection) with the run24 response shape.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_fixture_materializer_core import (
    materialize_experiment_fixtures,
)

_ORDER_UUID = "0e70000f-443a-407f-b7f3-050c4d6cbffb"
_ORDER_SKU = "SKU-PHONE-001"


def _orders_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/api/orders",
                "request_example": {
                    "items": [{"sku": _ORDER_SKU, "qty": 1}],
                    "couponCode": "NEW100",
                    "addressId": "<address_id>",
                },
            },
            {
                "id": "op_list_orders",
                "method": "GET",
                "path": "/api/orders",
            },
            {
                "id": "op_cancel_order",
                "method": "POST",
                "path": "/api/orders/:id/cancel",
            },
        ],
        "actors": [],
        "relations": [
            {
                "relation_type": "compensates",
                "from_ref": "op_cancel_order",
                "to_ref": "op_create_order",
                "operation_ref": "op_cancel_order",
                "status": "accepted",
                "source_refs": [{"source_id": "orders-api"}],
            }
        ],
    }


def _experiment(**overrides: object) -> dict:
    experiment: dict = {
        "experiment_id": "exp_order_cleanup_identity",
        "obligation_id": "obl_order_cancel",
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_order"],
            "nodes": [
                {
                    "node_id": "fix_order",
                    "kind": "runtime_read_binding",
                    "target": "order_id",
                    "constructible": True,
                }
            ],
        },
        "control_plan": [],
        "treatment_plan": [
            {
                "actor_ref": "actor_buyer",
                "operation_ref": "op_cancel_order",
                "path": "/api/orders/{id}/cancel",
            }
        ],
        "observers": [{"observer_id": "http_response", "surface": "http_api"}],
        "assertions": [{"kind": "state_transition_authorization"}],
        "safety_contract": {"governed_write": True, "cleanup_not_required": False},
        "compiled_adapters": ["http_api"],
        "environment_type": "test",
    }
    experiment.update(overrides)
    return experiment


def _materializer_inputs(**overrides: object) -> dict:
    ir = _orders_ir()
    inputs: dict = {
        "exp": _experiment(),
        "eid": "exp_order_cleanup_identity",
        "oid": "obl_order_cancel",
        "resolved_campaign_id": "CMP_test",
        "resolved_execution_id": "EXEC_test",
        "started": time.time(),
        "actors": {
            "actor_buyer": {
                "id": "actor_buyer",
                "role": "buyer",
                "credential_secret_ref": "secret:buyer",
            }
        },
        "ops": {row["id"]: row for row in ir["operations"]},
        "tokens": {"secret:buyer": "token-buyer", "buyer": "token-buyer"},
        "binding_plan": {
            "order_id": {
                "target": "order_id",
                "target_path": "/{order_id}",
                "status": "runtime_resolvable",
                "resolver_operations": [
                    {
                        "operation_ref": "op_list_orders",
                        "method": "GET",
                        "path": "/api/orders",
                    }
                ],
            }
        },
        "resolver_actor_ref": "actor_buyer",
        "resolver_token": "token-buyer",
        "activation_requirements": {"actor": [], "fixture": [], "cleanup": []},
        "root": Path("."),
        "project": "test-project",
        "base_url": "http://target.test",
        "runtime_contract": {
            "status": "approved",
            "approved_base_url": "http://target.test",
        },
        "campaign_id": "CMP_test",
        "behavior_ir": ir,
    }
    inputs.update(overrides)
    return inputs


def _run24_create_response() -> dict:
    """POST /api/orders 201 body exactly as the order-service builds it:
    the order row (``RETURNING *`` → id/order_no/user_id/status/amounts) plus
    ``items`` normalized from the products table (sku/title/price/status/
    category + qty/lineAmount — no ``id`` on the line items)."""
    return {
        "id": _ORDER_UUID,
        "order_no": "BM17859397981137749",
        "user_id": "17fc13c7-98b1-4fa1-9959-83f2f66081e0",
        "status": "PENDING_PAYMENT",
        "total_amount": "6999.00",
        "discount_amount": "0.00",
        "payable_amount": "6999.00",
        "items": [
            {
                "sku": _ORDER_SKU,
                "title": "iPhone 15",
                "price": "6999.00",
                "status": "ON_SALE",
                "category": "PHONE",
                "qty": 1,
                "lineAmount": "6999.00",
            }
        ],
    }


def test_cleanup_identity_binds_order_uuid_not_line_item_sku(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run24 shape: the pending fixture cleanup for /api/orders/{id}/cancel
    must carry the created order uuid — a line item's product sku must never
    enter the resource-id placeholder."""
    resolver_calls: list[str] = []

    def fake_run_http_step(**kwargs: object) -> dict:
        path = str(kwargs.get("path") or "")
        resolver_calls.append(path)
        # Empty collection → auto-fixture create kicks in.
        return {
            "method": "GET",
            "path": path,
            "status_code": 200,
            "body": [],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    def fake_governed_write(**kwargs: object) -> dict:
        return {
            "accepted": True,
            "method": str(kwargs.get("method") or "POST").upper(),
            "path": str(kwargs.get("path") or ""),
            "before": {"status": 200, "body": []},
            "write": {
                "method": "POST",
                "path": "/api/orders",
                "status": 201,
                "body": _run24_create_response(),
            },
            "after": {"status": 200, "body": [_run24_create_response()]},
            "after_ref": "sandbox_after_setup:/api/orders:201",
            "receipt_id": "audit-create",
            "operation_phase": "experiment_fixture_setup",
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core.execute_governed_control_write",
        fake_governed_write,
    )
    result = materialize_experiment_fixtures(**_materializer_inputs())
    assert result["status"] == "ready"

    # The created resource identity must be the order uuid, not the sku.
    assert result["runtime_bindings"].get("order_id") == _ORDER_UUID

    pending = result["pending_fixture_cleanups"]
    assert len(pending) == 1
    entry = pending[0]
    assert (entry.get("cleanup") or {}).get("path", "").endswith("/{id}/cancel")
    cleanup_identity = entry.get("cleanup_identity") or {}
    assert cleanup_identity.get("id") == _ORDER_UUID
    assert cleanup_identity.get("id") != _ORDER_SKU
    # The sku must not be bound under the resource-id key at all.
    assert all(
        value != _ORDER_SKU for value in cleanup_identity.values()
    ), f"sku leaked into cleanup identity: {cleanup_identity}"


def test_cleanup_identity_fail_closed_when_create_has_no_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create response with only a sku (no id) must NOT produce a sku-bound
    cleanup identity — the resource is not identifiable, so the cleanup
    projection stays empty (honest block) instead of a wrong 500 target."""
    def fake_run_http_step(**kwargs: object) -> dict:
        return {
            "method": "GET",
            "path": str(kwargs.get("path") or ""),
            "status_code": 200,
            "body": [],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    def fake_governed_write(**kwargs: object) -> dict:
        body = {
            "order_no": "BM-ONLY-SKU",
            "status": "PENDING_PAYMENT",
            "items": [{"sku": _ORDER_SKU, "title": "Phone", "price": "1.00"}],
        }
        return {
            "accepted": True,
            "method": "POST",
            "path": "/api/orders",
            "before": {"status": 200, "body": []},
            "write": {"method": "POST", "path": "/api/orders", "status": 201, "body": body},
            "after": {"status": 200, "body": [body]},
            "after_ref": "sandbox_after_setup:/api/orders:201",
            "receipt_id": "audit-create",
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core.execute_governed_control_write",
        fake_governed_write,
    )
    result = materialize_experiment_fixtures(**_materializer_inputs())
    pending = result.get("pending_fixture_cleanups") or []
    for entry in pending:
        cleanup_identity = entry.get("cleanup_identity") or {}
        assert all(
            value != _ORDER_SKU for value in cleanup_identity.values()
        ), f"sku leaked into cleanup identity: {cleanup_identity}"
