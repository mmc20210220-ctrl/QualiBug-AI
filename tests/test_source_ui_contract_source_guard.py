from __future__ import annotations

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.source_ui_contract_binding import bind_source_ui_contracts
from ai_test_asset_center.source_ui_contract_source_guard import (
    install_source_ui_contract_source_guard,
)


def _ir() -> dict:
    return build_behavior_ir_from_knowledge_asset(
        {
            "source_inventory": [{
                "source_id": "api-orders",
                "filename": "orders-api.json",
                "source_type": "openapi",
                "text_hash": "a" * 64,
            }],
        },
        project_id="ui-source-guard",
        source_snapshot_hash="b" * 64,
        api_operations=[{
            "operation_id": "get_order",
            "method": "GET",
            "path": "/api/orders/{id}",
            "summary": "Get order",
            "source_id": "api-orders",
            "read_write": "read",
        }],
        runtime_actors=[{
            "role": "public",
            "status": "active",
        }],
        available_surfaces={
            "http_api": True,
            "ui_browser": True,
            "db_snapshot": False,
            "process_timeline": True,
        },
    )


def _public_actor_ref(ir: dict) -> str:
    return next(
        row["id"]
        for row in ir["actors"]
        if row.get("role_key") == "public"
    )


def _contract(actor_ref: str) -> dict:
    return {
        "contract_id": "ui-order-title",
        "title": "Order title",
        "operation_ref": "get_order",
        "actor_ref": actor_ref,
        "ui_request": {
            "request_id": "ui-order-title",
            "provider": "playwright_browser_plan",
            "start_url": "https://example.test/orders/1",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "https://example.test/orders/1"},
                    {"action": "expect_text", "selector": "h1", "text": "Order"},
                ],
            },
        },
    }


def test_contract_without_source_identity_becomes_gap_not_invariant() -> None:
    install_source_ui_contract_source_guard()
    ir = _ir()
    behavior_ir, receipt = bind_source_ui_contracts(
        ir,
        {"ui_formal_contracts": [_contract(_public_actor_ref(ir))]},
    )

    assert receipt["bound_invariant_count"] == 0
    assert receipt["coverage_gap_count"] == 1
    assert receipt["reason_counts"] == {"FORMAL_UI_SOURCE_REF_MISSING": 1}
    assert not any(
        row.get("ui_contract_id") == "ui-order-title"
        for row in behavior_ir["invariants"]
    )


def test_explicit_source_id_is_converted_to_a_real_source_ref() -> None:
    install_source_ui_contract_source_guard()
    ir = _ir()
    contract = _contract(_public_actor_ref(ir))
    contract.update({
        "source_id": "ui-spec-orders",
        "source_locator": "screen:order-detail:title",
    })
    behavior_ir, receipt = bind_source_ui_contracts(
        ir,
        {"ui_formal_contracts": [contract]},
    )

    assert receipt["bound_invariant_count"] == 1
    invariant = next(
        row
        for row in behavior_ir["invariants"]
        if row.get("ui_contract_id") == "ui-order-title"
    )
    assert invariant["source_refs"][0]["source_id"] == "ui-spec-orders"
    assert invariant["source_refs"][0]["locator"] == "screen:order-detail:title"
