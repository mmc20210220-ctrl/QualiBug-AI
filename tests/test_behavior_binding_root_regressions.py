from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_contract_implementation_authority import (
    apply_event_contract_validation_failures,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.event_observer_implementation_projection import (
    project_formal_event_observers,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding import (
    build_behavior_implementation_bindings,
)


def _behavior() -> dict:
    return {
        "behavior_id": "behavior:create-address",
        "status": "CONFIRMED",
        "operation_ref": "创建",
        "object_refs": ["用户地址"],
        "actor_refs": [],
        "preconditions": [],
        "state_effects": [],
        "data_effects": [],
        "expected_effects": [],
        "permission_decision": "UNSPECIFIED",
    }


def _asset(interfaces: list[dict]) -> dict:
    return {
        "interfaces": interfaces,
        "relationships": [],
        "tables": [],
        "field_dictionary": [],
        "ui_actions": [],
        "event_formal_contracts": [],
    }


def _interface(interface_id: str, path: str, summary: str, **extra: object) -> dict:
    return {
        "interface_id": interface_id,
        "source_id": "api-doc",
        "method": "POST",
        "path": path,
        "operation_id": interface_id.rsplit(":", 1)[-1],
        "summary": summary,
        **extra,
    }


def test_unique_action_object_source_phrase_is_authoritative() -> None:
    interfaces = [
        _interface(
            "api:POST:/users/addresses",
            "/users/addresses",
            "创建用户地址",
        ),
        _interface("api:POST:/products", "/products", "创建商品"),
    ]

    bindings, unknowns, conflicts, _gate = build_behavior_implementation_bindings(
        _asset(interfaces), [_behavior()]
    )

    authoritative = [
        row
        for row in bindings[0]["api_operation_bindings"]
        if row.get("authoritative") is True
    ]
    assert conflicts == []
    assert [row["interface_id"] for row in authoritative] == [
        "api:POST:/users/addresses"
    ]
    assert authoritative[0]["derivation"] == (
        "exact_operation_object_source_identity"
    )
    assert not any(
        row.get("kind") == "BEHAVIOR_API_BINDING_UNRESOLVED"
        for row in unknowns
    )


def test_action_and_object_in_different_labels_never_form_exact_identity() -> None:
    interfaces = [
        _interface(
            "api:POST:/generic-records",
            "/generic-records",
            "创建记录",
            tags=["用户地址"],
        )
    ]

    bindings, unknowns, _conflicts, _gate = build_behavior_implementation_bindings(
        _asset(interfaces), [_behavior()]
    )

    assert not any(
        row.get("authoritative") is True
        for row in bindings[0]["api_operation_bindings"]
    )
    assert any(
        row.get("kind") == "BEHAVIOR_API_BINDING_UNRESOLVED"
        for row in unknowns
    )


def test_multiple_exact_action_object_phrases_remain_ambiguous() -> None:
    interfaces = [
        _interface(
            "api:POST:/users/addresses",
            "/users/addresses",
            "创建用户地址",
        ),
        _interface(
            "api:POST:/admin/users/addresses",
            "/admin/users/addresses",
            "创建用户地址",
        ),
    ]

    bindings, unknowns, conflicts, _gate = build_behavior_implementation_bindings(
        _asset(interfaces), [_behavior()]
    )

    assert conflicts == []
    assert not any(
        row.get("authoritative") is True
        for row in bindings[0]["api_operation_bindings"]
    )
    ambiguity = next(
        row
        for row in unknowns
        if row.get("kind") == "BEHAVIOR_API_BINDING_AMBIGUOUS"
    )
    assert ambiguity["candidate_interface_refs"] == [
        "api:POST:/users/addresses",
        "api:POST:/admin/users/addresses",
    ]


def test_no_event_contract_preserves_existing_binding_gate() -> None:
    binding = {
        "binding_id": "binding:create-address",
        "behavior_ref": "behavior:create-address",
        "status": "BOUND",
        "scenario_planning_ready": True,
    }
    gate = {
        "status": "PASS",
        "entry_allowed": True,
        "scenario_planning_allowed": True,
        "metrics": {"scenario_ready_binding_count": 1},
    }

    bindings, unknowns, conflicts, projected_gate = project_formal_event_observers(
        _asset([]),
        [_behavior()],
        [binding],
        [],
        [],
        gate,
    )

    assert bindings == [binding]
    assert unknowns == []
    assert conflicts == []
    assert projected_gate["status"] == "PASS"
    assert projected_gate["entry_allowed"] is True
    assert projected_gate["metrics"]["scenario_ready_binding_count"] == 1
    assert projected_gate["metrics"]["formal_event_contract_count"] == 0


def test_no_event_validation_failure_preserves_existing_binding_gate() -> None:
    binding = {
        "binding_id": "binding:create-address",
        "status": "BOUND",
        "scenario_planning_ready": True,
    }
    gate = {
        "status": "PASS",
        "entry_allowed": True,
        "scenario_planning_allowed": True,
        "metrics": {"scenario_ready_binding_count": 1},
    }

    bindings, unknowns, conflicts, projected_gate = (
        apply_event_contract_validation_failures(
            [binding],
            [],
            [],
            gate,
            [],
        )
    )

    assert bindings == [binding]
    assert unknowns == []
    assert conflicts == []
    assert projected_gate == gate
