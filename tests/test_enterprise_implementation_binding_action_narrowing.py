from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_authority import (
    _bind_action_with_congruent_exact_narrowing,
)


def _interface(interface_id: str, operation_id: str, path: str) -> dict:
    return {
        "interface_id": interface_id,
        "operation_id": operation_id,
        "method": "POST",
        "path": path,
        "summary": operation_id,
        "source_id": "api-spec",
    }


def _edge(rule_id: str, interface_id: str) -> dict:
    return {
        "from": rule_id,
        "to": interface_id,
        "relation": "rule_to_interface",
        "status": "accepted",
        "derivation": "exact_source_section",
        "evidence_gate": "exact_source_section",
        "evidence": {"source_id": "prd", "source_locator": "section:order-actions"},
    }


def _behavior(operation_ref: str) -> dict:
    return {
        "behavior_id": "behavior:order-action",
        "source_refs": ["rule:order-actions"],
        "operation_ref": operation_ref,
        "object_refs": ["order"],
        "state_effects": [],
        "data_effects": [],
        "permission_decision": "UNSPECIFIED",
    }


def test_unique_exact_behavior_operation_narrows_broad_rule_authority():
    interfaces = [
        _interface("api:pay", "payOrder", "/orders/{id}/pay"),
        _interface("api:cancel", "cancelOrder", "/orders/{id}/cancel"),
    ]
    asset = {
        "relationships": [
            _edge("rule:order-actions", "api:pay"),
            _edge("rule:order-actions", "api:cancel"),
        ]
    }

    bindings, unknowns, conflicts = _bind_action_with_congruent_exact_narrowing(
        asset, _behavior("payOrder"), interfaces
    )

    assert [row["interface_id"] for row in bindings] == ["api:pay"]
    assert unknowns == []
    assert conflicts == []


def test_exact_operation_outside_rule_authority_remains_conflicted_and_not_promoted():
    interfaces = [
        _interface("api:pay", "payOrder", "/orders/{id}/pay"),
        _interface("api:cancel", "cancelOrder", "/orders/{id}/cancel"),
        _interface("api:refund", "refundOrder", "/orders/{id}/refund"),
    ]
    asset = {
        "relationships": [
            _edge("rule:order-actions", "api:cancel"),
            _edge("rule:order-actions", "api:refund"),
        ]
    }

    bindings, _unknowns, conflicts = _bind_action_with_congruent_exact_narrowing(
        asset, _behavior("payOrder"), interfaces
    )

    assert {row["interface_id"] for row in bindings} == {"api:cancel", "api:refund"}
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "BEHAVIOR_API_BINDING_CONFLICT"
    assert conflicts[0]["automatic_resolution_allowed"] is False


def test_no_unique_exact_operation_preserves_broad_rule_authority_fail_closed():
    interfaces = [
        _interface("api:update-a", "updateOrder", "/orders/{id}/a"),
        _interface("api:update-b", "updateOrder", "/orders/{id}/b"),
    ]
    asset = {
        "relationships": [
            _edge("rule:order-actions", "api:update-a"),
            _edge("rule:order-actions", "api:update-b"),
        ]
    }

    bindings, unknowns, conflicts = _bind_action_with_congruent_exact_narrowing(
        asset, _behavior("updateOrder"), interfaces
    )

    assert {row["interface_id"] for row in bindings} == {"api:update-a", "api:update-b"}
    assert unknowns == []
    assert conflicts == []
