"""Unit tests for the knowledge-asset -> Reasoner world-model projection.

The 11-engine Reasoner grounds hypotheses in a structured business world
(entities / documented_rules / state_machines / roles / relationships).  The
projection adapts the source-derived knowledge asset to that contract; these
tests pin the contract: source-grounded values, bounded sizes, deterministic
output, and never any content the asset does not carry.
"""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center import (
    project_knowledge_world_model,
)


def _asset() -> dict[str, Any]:
    return {
        "business_objects": [
            {"object": "orders", "source": "database_schema", "confidence": 0.9},
            {"object": "", "source": "database_schema", "confidence": 0.5},
            {"object": "users", "source": "api_spec", "confidence": 0.3},
            {"object": "balance", "source": "database_schema", "confidence": 0.7},
        ],
        "rule_library": [
            {
                "rule_id": "rule:src:1",
                "source_id": "src:prd",
                "source_locator": "PRD.md#订单",
                "statement": "订单支付前不得发货",
                "severity": "P0",
                "tokens": ["订单", "支付", "发货"],
            },
            {
                "rule_id": "rule:src:2",
                "source_id": "src:prd",
                "source_locator": "PRD.md#退款",
                "statement": "退款金额不得超过实付金额",
                "severity": "P1",
                "tokens": ["退款", "金额"],
            },
            {
                "rule_id": "rule:src:3",
                "source_id": "src:api",
                "statement": "订单支付前不得发货",
                "severity": "P2",
            },
            {"rule_id": "rule:src:4", "source_id": "src:api", "statement": "", "severity": "P0"},
        ],
        "state_machines": [
            {
                "state_machine_id": "state:src:1",
                "source_id": "src:prd",
                "object": "order",
                "states": ["CREATED", "PAID", "SHIPPED"],
                "transitions": [
                    {"from": "CREATED", "to": "PAID", "trigger": "pay"},
                    {"from": "PAID", "to": "SHIPPED", "trigger": "ship"},
                ],
            }
        ],
        "roles": [
            {"role_id": "role:src:1", "source_id": "src:prd", "role": "buyer"},
            {"role_id": "role:src:2", "source_id": "src:prd", "role": "buyer"},
            {"role_id": "role:src:3", "source_id": "src:prd", "role": "seller"},
        ],
        "entity_relations": [
            {
                "from_entity": "orders",
                "to_entity": "users",
                "relation_type": "belongs_to",
                "source_id": "src:db",
            },
            {"from_entity": "", "to_entity": "users", "relation_type": "x", "source_id": "s"},
        ],
    }


def test_projection_carries_source_grounded_content() -> None:
    world = project_knowledge_world_model(_asset())

    assert world["insufficient_evidence"] is False
    # Duplicate statements are collapsed; empty statements dropped.
    assert len(world["documented_rules"]) == 2
    assert world["documented_rules"][0]["rule"] == "订单支付前不得发货"
    assert world["documented_rules"][0]["severity"] == "P0"
    assert world["documented_rules"][0]["source"] == "src:prd@PRD.md#订单"
    assert world["documented_rules"][0]["entities_involved"] == ["订单", "支付", "发货"]

    # Empty names are dropped; is_core follows confidence.
    assert len(world["entities"]) == 3
    by_name = {e["name"]: e for e in world["entities"]}
    assert by_name["orders"]["is_core"] is True
    assert by_name["users"]["is_core"] is False

    assert len(world["state_machines"]) == 1
    sm = world["state_machines"][0]
    assert sm["entity"] == "order"
    assert sm["transitions"][0]["trigger"] == "pay"

    # Roles are deduplicated by name.
    assert {r["name"] for r in world["roles"]} == {"buyer", "seller"}

    # Relationships drop rows without a complete triple.
    assert len(world["relationships"]) == 1
    assert world["relationships"][0]["relationship_type"] == "belongs_to"


def test_projection_is_bounded_and_empty_safe() -> None:
    world = project_knowledge_world_model(
        _asset(), max_rules=1, max_relationships=0, max_roles=1
    )
    assert len(world["documented_rules"]) == 1
    assert len(world["relationships"]) == 0
    assert len(world["roles"]) == 1

    empty = project_knowledge_world_model(None)
    assert empty["insufficient_evidence"] is True
    assert empty["documented_rules"] == []
    assert empty["entities"] == []
    assert empty["state_machines"] == []
    assert empty["gaps"] == {}
