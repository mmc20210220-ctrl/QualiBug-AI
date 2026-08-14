from __future__ import annotations

import copy
import json

from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center import obligation_compiler
from ai_test_asset_center.enterprise_knowledge_center import _parse_source
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    install_formal_ui_contract_parser,
)
from ai_test_asset_center.formal_ui_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
    install_formal_ui_surface,
)
from ai_test_asset_center.formal_ui_surface_guard import install_formal_ui_read_only_guard
from ai_test_asset_center.source_ui_contract_binding import bind_source_ui_contracts
from ai_test_asset_center.source_ui_obligation_binding import (
    compile_obligations_with_source_ui,
)


# The parser patch is registered by the composition root
# (``configure_source_parser_extensions``); a direct ``_parse_source`` call in
# tests mirrors that registration exactly like the product does.
install_formal_ui_contract_parser()


def _contract(*, operation_ref: str = "get_order", actor_ref: str = "actor_admin") -> dict:
    return {
        "contract_id": "ui_order_approved_visible",
        "title": "Approved order state is visible",
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
        "ui_request": {
            "request_id": "ui_order_approved_visible",
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
                        "timeout_ms": 5000,
                    },
                ]
            },
            "success_criteria": {"source": "prd:approved-order-view"},
        },
        "source_refs": [{
            "source_id": "ui_source_1",
            "version": "",
            "locator": "json:ui_formal_contracts[1]",
            "kind": "formal_ui_contract",
            "quote_hash": "",
        }],
        "source_id": "ui_source_1",
        "source_locator": "json:ui_formal_contracts[1]",
        "status": "accepted",
    }


def _ir() -> dict:
    model = bir.empty_behavior_ir(project_id="ui-project", source_snapshot_hash="source-hash")
    operation = bir._fact_node(
        node_id="bir_op_get_order",
        typed_fields={
            "operation_id": "get_order",
            "service": "orders",
            "method": "GET",
            "path": "/api/orders/{id}",
            "request_schema": {},
            "request_example": {},
            "response_schema": {},
            "parameters": ["id"],
            "field_dictionary": [],
            "security": [],
            "summary": "Get one order",
            "description": "",
            "tags": ["orders"],
            "side_effect_class": "read",
            "read_write": "read",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
            "source_operation_refs": ["get_order", "openapi:get_order"],
        },
        source_refs=[{
            "source_id": "api_source_1",
            "version": "",
            "locator": "GET /api/orders/{id}",
            "kind": "api_operation",
            "quote_hash": "",
        }],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    actor = bir._fact_node(
        node_id="actor_admin",
        typed_fields={
            "role": "admin",
            "role_key": "admin",
            "account_ref": "admin@example.test",
            "tenant_scope": "all",
            "credential_secret_ref": "secret_ref:test_accounts:admin@example.test",
            "account_status": "active",
            "allowed_resources": ["orders"],
            "allowed_actions": ["read"],
            "denied_actions": [],
            "runtime_bound": True,
        },
        source_refs=[{
            "source_id": "runtime_actors",
            "version": "",
            "locator": "admin:admin@example.test",
            "kind": "runtime_actor",
            "quote_hash": "",
        }],
        confidence=1.0,
        derivation="runtime-observed",
        status="accepted",
    )
    model["operations"] = [operation]
    model["actors"] = [actor]
    model["model_id"] = bir._content_addressed_id(model)
    assert bir.validate_behavior_ir(model, require_explicit_relations=True) == []
    return model


def _asset(contract: dict | None = None) -> dict:
    return {
        "ui_design_specs": [{
            "ui_spec_id": "ui:source:orders",
            "source_id": "ui_source_1",
            "name": "Order detail",
            "formal_ui_contracts": [contract or _contract()],
            "formal_ui_contract_gaps": [],
        }]
    }


def test_plain_ui_prototype_does_not_become_a_formal_contract() -> None:
    svg = b"""<svg><title>Order Detail</title><text>Approved</text><text>Loading</text></svg>"""
    parsed = _parse_source(svg, "order_detail.svg", "uiux_svg", "ui_source_plain")

    assert len(parsed["ui_specs"]) == 1
    spec = parsed["ui_specs"][0]
    assert spec["text_labels"]
    assert spec["formal_ui_contracts"] == []
    assert spec["formal_ui_contract_count"] == 0


def test_explicit_json_ui_contract_is_preserved_with_source_identity() -> None:
    payload = {
        "ui_formal_contracts": [_contract()],
        "screen": "Order Detail",
    }
    parsed = _parse_source(
        json.dumps(payload).encode("utf-8"),
        "order_detail.json",
        "uiux_spec",
        "ui_source_1",
    )

    contract = parsed["ui_specs"][0]["formal_ui_contracts"][0]
    assert contract["contract_id"] == "ui_order_approved_visible"
    assert contract["operation_ref"] == "get_order"
    assert contract["actor_ref"] == "actor_admin"
    assert contract["ui_request"]["provider"] == "playwright_browser_plan"
    assert contract["source_refs"][0]["source_id"] == "ui_source_1"
    assert parsed["ui_specs"][0]["formal_ui_contract_gaps"] == []


def test_incomplete_ui_contract_stays_a_visible_parser_gap() -> None:
    payload = {
        "ui_formal_contracts": [{
            "contract_id": "missing-identities",
            "ui_request": {
                "provider": "playwright_browser_plan",
                "start_url": "/orders/123",
                "browser_plan": {"steps": [{"action": "screenshot"}]},
            },
        }]
    }
    parsed = _parse_source(
        json.dumps(payload).encode("utf-8"),
        "incomplete_ui.json",
        "uiux_spec",
        "ui_source_gap",
    )

    spec = parsed["ui_specs"][0]
    assert spec["formal_ui_contracts"] == []
    gap = spec["formal_ui_contract_gaps"][0]
    assert gap["reason_code"] == "FORMAL_UI_CONTRACT_INCOMPLETE"
    assert "operation_ref_or_method_path" in gap["missing_requirements"]
    assert "actor_ref_or_actor_role" in gap["missing_requirements"]
    assert "expect_text_or_expect_url" in gap["missing_requirements"]


def test_exact_source_contract_binds_to_one_ir_operation_and_actor() -> None:
    bound, receipt = bind_source_ui_contracts(_ir(), _asset())

    assert receipt["status"] == "BOUND"
    assert receipt["bound_invariant_count"] == 1
    invariant = next(
        row for row in bound["invariants"]
        if row.get("ui_contract_id") == "ui_order_approved_visible"
    )
    assert invariant["operation_refs"] == ["bir_op_get_order"]
    assert invariant["ui_actor_ref"] == "actor_admin"
    assert invariant["expression"]["kind"] == "ui_source_expectation"
    relation = next(
        row for row in bound["relations"]
        if row.get("from_ref") == invariant["id"]
    )
    assert relation["relation_type"] == "observes"
    assert relation["operation_ref"] == "bir_op_get_order"
    assert relation["actor_ref"] == "actor_admin"


def test_ambiguous_or_write_prerequisite_never_becomes_an_invariant() -> None:
    model = _ir()
    duplicate = copy.deepcopy(model["operations"][0])
    duplicate["id"] = "bir_op_get_order_duplicate"
    model["operations"].append(duplicate)
    model["model_id"] = bir._content_addressed_id(model)

    ambiguous, receipt = bind_source_ui_contracts(model, _asset())
    assert receipt["bound_invariant_count"] == 0
    assert receipt["reason_counts"] == {"FORMAL_UI_OPERATION_AMBIGUOUS": 1}
    assert any(
        row.get("reason_code") == "FORMAL_UI_OPERATION_AMBIGUOUS"
        for row in ambiguous["coverage_gaps"]
    )

    write_model = _ir()
    write_model["operations"][0]["method"] = "POST"
    write_model["operations"][0]["read_write"] = "write"
    write_model["model_id"] = bir._content_addressed_id(write_model)
    write_bound, write_receipt = bind_source_ui_contracts(write_model, _asset())
    assert write_receipt["bound_invariant_count"] == 0
    assert write_receipt["reason_counts"] == {
        "FORMAL_UI_PREREQUISITE_WRITE_NOT_ALLOWED": 1
    }
    assert not any(row.get("ui_contract_id") for row in write_bound["invariants"])


def test_one_ui_invariant_produces_one_ui_obligation_not_validation() -> None:
    install_formal_ui_surface()
    install_formal_ui_read_only_guard()
    bound, _receipt = bind_source_ui_contracts(_ir(), _asset())

    compiled = compile_obligations_with_source_ui(
        bound,
        base_compile=obligation_compiler.compile_obligations_from_behavior_ir,
    )
    ui_rows = [
        row for row in compiled["obligations"]
        if row["property"].get("invariant_ref")
        == next(inv["id"] for inv in bound["invariants"] if inv.get("ui_contract_id"))
    ]

    assert len(ui_rows) == 1
    obligation = ui_rows[0]
    assert obligation["risk_family"] == RISK_FAMILY
    assert obligation["property"]["template"] == PROTOCOL_TEMPLATE
    assert obligation["required_operations"] == ["bir_op_get_order"]
    assert obligation["required_actors"] == ["actor_admin"]
    assert obligation["required_observers"] == [OBSERVER_ID]
    assert obligation["cleanup_requirement"]["required"] is False
    assert compiled["source_ui_obligation_receipt"]["obligation_count"] == 1
    assert compiled["source_ui_obligation_receipt"][
        "misclassified_obligation_count_removed"
    ] >= 1
    assert not any(
        row["risk_family"] == "validation"
        and row["property"].get("invariant_ref") == obligation["property"]["invariant_ref"]
        for row in compiled["obligations"]
    )
