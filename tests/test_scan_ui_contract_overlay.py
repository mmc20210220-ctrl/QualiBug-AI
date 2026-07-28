from __future__ import annotations

from ai_test_asset_center import discovery_runtime_planning as planning
from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs
from ai_test_asset_center.discovery_runtime_semantic_binding import (
    _planning_inputs_with_declared_adapters,
    build_behavior_ir_with_semantic_operation_bindings,
)
from ai_test_asset_center.scan_ui_contract_overlay import (
    bind_scan_ui_contract_context,
    overlay_scan_ui_contracts,
    reset_scan_ui_contract_context,
)


def _source_ref() -> dict:
    return {
        "source_id": "ui-spec-orders",
        "version": "v1",
        "locator": "screen:order-detail:title",
        "kind": "formal_ui_contract",
        "quote_hash": "a" * 64,
    }


def _request(**overrides) -> dict:
    request = {
        "request_id": "ui-order-title",
        "title": "Order title must be rendered",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/orders/1",
        "execution_mode": "safe_read_only",
        "operation_id": "get_order",
        "actor_role": "anonymous",
        "source_refs": [_source_ref()],
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "https://example.test/orders/1"},
                {"action": "expect_text", "selector": "h1", "text": "Order details"},
            ],
        },
    }
    request.update(overrides)
    return request


def test_auto_generated_screenshot_request_stays_smoke_only() -> None:
    request = _request(
        request_id="auto-entry",
        source_refs=[],
        success_criteria={},
        metadata={"auto_generated": True},
        browser_plan={
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "https://example.test"},
                {"action": "screenshot"},
            ],
        },
    )
    asset, receipt = overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert asset["ui_formal_contracts"] == []
    assert receipt["formal_candidate_count"] == 0
    assert receipt["auto_generated_request_count"] == 1
    assert receipt["status"] == "NOT_REQUESTED"


def test_source_less_explicit_contract_becomes_visible_coverage_gap() -> None:
    request = _request(source_refs=[])
    asset, receipt = overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert asset["ui_formal_contracts"] == []
    assert receipt["status"] == "BLOCKED"
    assert receipt["coverage_gap_count"] == 1
    assert asset["coverage_gaps"][0]["reason_code"] == "FORMAL_UI_SOURCE_REF_MISSING"


def test_explicit_scan_contract_enters_existing_source_ui_ir_and_obligation_chain() -> None:
    context = {
        "declared_adapters": ["ui_browser"],
        "ui_execution_requests": [_request()],
    }
    token = bind_scan_ui_contract_context(context)
    try:
        behavior_ir = build_behavior_ir_with_semantic_operation_bindings(
            {
                "source_inventory": [{
                    "source_id": "ui-spec-orders",
                    "filename": "orders-ui.json",
                    "source_type": "uiux",
                    "text_hash": "b" * 64,
                }],
            },
            project_id="project-ui",
            source_snapshot_hash="c" * 64,
            api_operations=[{
                "operation_id": "get_order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "summary": "Get order",
                "source_id": "api-orders",
                "read_write": "read",
            }],
            runtime_actors=[{
                "role": "anonymous",
                "status": "active",
            }],
            available_surfaces={
                "http_api": True,
                "ui_browser": True,
                "db_snapshot": False,
                "process_timeline": True,
            },
        )
    finally:
        reset_scan_ui_contract_context(token)

    overlay_receipt = behavior_ir["scan_ui_contract_overlay_receipt"]
    assert overlay_receipt["contract_added_count"] == 1
    binding_receipt = behavior_ir["source_ui_contract_binding_receipt"]
    assert binding_receipt["bound_invariant_count"] == 1
    ui_invariants = [
        row
        for row in behavior_ir["invariants"]
        if row.get("ui_contract_id") == "ui-order-title"
    ]
    assert len(ui_invariants) == 1
    assert ui_invariants[0]["expression"]["kind"] == "ui_source_expectation"

    obligation_pack = planning.compile_obligations_from_behavior_ir(behavior_ir)
    ui_obligations = [
        row
        for row in obligation_pack["obligations"]
        if row.get("risk_family") == "ui_state_consistency"
    ]
    assert len(ui_obligations) == 1
    obligation = ui_obligations[0]
    assert obligation["required_observers"] == ["ui_source_expectation_reader"]
    assert obligation["property"]["template"] == "source_declared_ui_expectation"
    assert obligation["cleanup_requirement"]["required"] is False
    assert obligation_pack["source_ui_obligation_receipt"]["obligation_count"] == 1


def test_direct_planning_call_merges_top_level_adapter_declaration_into_runtime_contract(
    tmp_path,
) -> None:
    inputs = DiscoveryMainlineInputs(
        project="project-ui",
        root=tmp_path,
        prd_text="",
        api_spec_text="",
        db_schema_text="",
        approved_base_url="https://example.test",
        campaign_context={
            "declared_adapters": ["ui_browser"],
            "_runtime_contract": {
                "status": "approved",
                "approved_base_url": "https://example.test",
            },
        },
    )

    effective = _planning_inputs_with_declared_adapters(inputs)

    assert effective is not inputs
    assert effective.campaign_context["declared_adapters"] == ["ui_browser"]
    assert effective.campaign_context["_runtime_contract"]["declared_adapters"] == [
        "ui_browser"
    ]
    assert "declared_adapters" not in inputs.campaign_context["_runtime_contract"]
