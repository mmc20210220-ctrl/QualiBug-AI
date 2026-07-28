from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center import _parse_source
from ai_test_asset_center.formal_ui_surface import install_formal_ui_surface
from ai_test_asset_center.source_ui_obligation_compat import (
    install_source_ui_family_vector_compat,
)
from ai_test_asset_center import source_ui_obligation_binding as ui_binding
from ai_test_asset_center.test_obligation import canonical_risk_families


def test_generic_ui_component_json_array_is_not_an_attempted_formal_contract() -> None:
    payload = [
        {
            "component": "OrderStatusBadge",
            "state": "approved",
            "label": "Approved",
        },
        {
            "component": "OrderTotal",
            "state": "visible",
            "label": "$120.00",
        },
    ]

    parsed = _parse_source(
        json.dumps(payload).encode("utf-8"),
        "order_components.json",
        "uiux_spec",
        "ui_component_source",
    )

    assert len(parsed["ui_specs"]) == 1
    spec = parsed["ui_specs"][0]
    assert spec["formal_ui_contracts"] == []
    assert spec["formal_ui_contract_gaps"] == []
    assert spec["formal_ui_contract_count"] == 0


def test_explicitly_typed_direct_array_contract_is_still_accepted() -> None:
    payload = [{
        "schema_version": "qualibug.ui-formal-contract.v1",
        "contract_id": "ui_order_state_visible",
        "operation_ref": "get_order",
        "actor_ref": "actor_admin",
        "ui_request": {
            "request_id": "ui_order_state_visible",
            "provider": "playwright_browser_plan",
            "start_url": "/orders/123",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "steps": [
                    {"action": "goto", "url": "/orders/123"},
                    {
                        "action": "expect_text",
                        "selector": "[data-testid='order-status']",
                        "text": "Approved",
                    },
                ]
            },
            "success_criteria": {"action": "expect_text"},
        },
    }]

    parsed = _parse_source(
        json.dumps(payload).encode("utf-8"),
        "formal_ui_contracts.json",
        "uiux_spec",
        "ui_contract_source",
    )

    spec = parsed["ui_specs"][0]
    assert [row["contract_id"] for row in spec["formal_ui_contracts"]] == [
        "ui_order_state_visible"
    ]
    assert spec["formal_ui_contract_gaps"] == []


def test_source_ui_compiler_preserves_complete_zero_valued_family_vector() -> None:
    install_formal_ui_surface()
    install_source_ui_family_vector_compat()

    result = ui_binding.compile_obligations_with_source_ui(
        {"invariants": [], "operations": [], "actors": [], "relations": []},
        base_compile=lambda _ir: {
            "schema_version": "qualibug.test-obligation-pack.v1",
            "obligations": [],
            "obligation_count": 0,
            "coverage_gaps": [],
            "by_family": {
                "authorization": 0,
                "validation": 0,
            },
        },
    )

    expected = set(canonical_risk_families())
    assert expected <= set(result["by_family"])
    assert result["by_family"]["authorization"] == 0
    assert result["by_family"]["validation"] == 0
    assert result["by_family"]["ui_state_consistency"] == 0
    assert all(value == 0 for value in result["by_family"].values())
    assert result["source_ui_obligation_receipt"]["complete_family_vector"] is True
