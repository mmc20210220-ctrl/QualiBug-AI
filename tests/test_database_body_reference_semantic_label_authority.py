from __future__ import annotations

from ai_test_asset_center.database_body_reference_semantic_label import project_exact_description_anchor_relations


def _operation(ref: str, description: str, source: str = "openapi") -> dict:
    return {
        "id": ref,
        "source_refs": [{"source_id": source, "locator": ref}],
        "request_schema": {
            "type": "object",
            "properties": {"orderId": {"type": "string", "format": "uuid", "description": description}},
        },
    }


def test_exact_source_description_can_propagate_only_from_resolved_anchor() -> None:
    model = {"operations": [_operation("anchor", "订单 ID"), _operation("target", "订单 ID")]}
    seed = [{
        "id": "rel-anchor",
        "status": "RESOLVED",
        "operation_ref": "anchor",
        "body_path": "orderId",
        "target_entity_ref": "ent-order",
    }]
    rows = project_exact_description_anchor_relations(model, seed)
    assert len(rows) == 1
    assert rows[0]["operation_ref"] == "target"
    assert rows[0]["target_entity_ref"] == "ent-order"
    assert rows[0]["authority"] == "source_exact_body_description+resolved_relation_anchor"


def test_description_propagation_is_fail_closed_for_different_source_or_ambiguous_anchor() -> None:
    model = {
        "operations": [
            _operation("a1", "订单 ID"),
            _operation("a2", "订单 ID"),
            _operation("target", "订单 ID"),
            _operation("other-source", "订单 ID", source="other"),
        ]
    }
    seed = [
        {"id": "r1", "status": "RESOLVED", "operation_ref": "a1", "body_path": "orderId", "target_entity_ref": "ent-order"},
        {"id": "r2", "status": "RESOLVED", "operation_ref": "a2", "body_path": "orderId", "target_entity_ref": "ent-refund"},
    ]
    assert project_exact_description_anchor_relations(model, seed) == []
