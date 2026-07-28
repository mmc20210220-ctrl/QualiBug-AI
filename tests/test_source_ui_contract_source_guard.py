from __future__ import annotations

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.source_ui_contract_binding import bind_source_ui_contracts
from ai_test_asset_center.source_ui_contract_source_guard import (
    install_source_ui_contract_source_guard,
)


def _ir() -> dict:
    ir = empty_behavior_ir(project_id="ui-source-guard")
    ir["operations"] = [{
        "id": "bir_op_get_order",
        "operation_id": "get_order",
        "method": "GET",
        "path": "/api/orders/{id}",
        "read_write": "read",
        "source_refs": [{
            "source_id": "api-orders",
            "version": "v1",
            "locator": "GET /api/orders/{id}",
            "kind": "api_operation",
            "quote_hash": "",
        }],
        "confidence": 1.0,
        "derivation": "explicit",
        "status": "accepted",
    }]
    ir["actors"] = [{
        "id": "bir_actor_public",
        "role": "public",
        "role_key": "public",
        "credential_secret_ref": "",
        "runtime_bound": True,
        "account_status": "active",
        "source_refs": [{
            "source_id": "roles",
            "version": "v1",
            "locator": "public",
            "kind": "runtime_actor",
            "quote_hash": "",
        }],
        "confidence": 1.0,
        "derivation": "runtime-observed",
        "status": "accepted",
    }]
    return ir


def _contract() -> dict:
    return {
        "contract_id": "ui-order-title",
        "title": "Order title",
        "operation_ref": "get_order",
        "actor_ref": "bir_actor_public",
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
    behavior_ir, receipt = bind_source_ui_contracts(
        _ir(),
        {"ui_formal_contracts": [_contract()]},
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
    contract = _contract()
    contract.update({
        "source_id": "ui-spec-orders",
        "source_locator": "screen:order-detail:title",
    })
    behavior_ir, receipt = bind_source_ui_contracts(
        _ir(),
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
