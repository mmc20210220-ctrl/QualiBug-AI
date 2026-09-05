from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.behavior_ir_mainline_base import (
    attach_stable_behavior_identity,
    build_behavior_ir_from_knowledge_asset,
    build_minimum_ir_delta,
    match_behavior_ir_revisions,
)


def _model(*, source_hash: str, operation_id: str = "op_v1", summary: str = "List orders") -> dict:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "project_id": "project-a",
        "source_snapshot_hash": source_hash,
        "entities": [
            {"id": "entity_v1", "name": "Order", "service": "orders"},
        ],
        "operations": [
            {
                "id": operation_id,
                "service": "orders",
                "method": "GET",
                "path": "/orders/{order_id}",
                "summary": summary,
                "source_refs": [{"source_id": "api", "locator": "GET /orders/{order_id}"}],
                "confidence": 1.0,
                "derivation": "explicit",
            },
        ],
        "actors": [],
        "states": [],
        "invariants": [],
        "relations": [
            {
                "id": "relation_v1",
                "relation_type": "observes",
                "from_ref": "entity_v1",
                "to_ref": operation_id,
                "operation_ref": operation_id,
                "actor_ref": "",
                "source_refs": [],
            },
        ],
    }


def test_same_behavior_keeps_logical_key_across_source_revisions() -> None:
    before = attach_stable_behavior_identity(_model(source_hash="a" * 64))
    after = attach_stable_behavior_identity(
        _model(source_hash="b" * 64, operation_id="op_v2")
    )

    assert before["logical_key"] == after["logical_key"]
    assert before["revision_id"] != after["revision_id"]
    assert before["operations"][0]["logical_key"] == after["operations"][0]["logical_key"]
    assert before["relations"][0]["logical_key"] == after["relations"][0]["logical_key"]

    matches = match_behavior_ir_revisions(before, after)
    assert matches["operations"] == [
        {
            "logical_key": before["operations"][0]["logical_key"],
            "previous_id": "op_v1",
            "current_id": "op_v2",
        }
    ]


def test_mutated_behavior_is_changed_not_removed_and_added() -> None:
    before = _model(source_hash="a" * 64, summary="List orders")
    after = _model(source_hash="b" * 64, operation_id="op_v2", summary="List orders with status")

    delta = build_minimum_ir_delta(before, after)
    operation_key = attach_stable_behavior_identity(deepcopy(before))["operations"][0]["logical_key"]

    assert delta["source_snapshot_changed"] is True
    assert delta["collections"]["operations"]["added"] == []
    assert delta["collections"]["operations"]["removed"] == []
    assert delta["collections"]["operations"]["changed"] == [operation_key]


def test_minimum_delta_reports_added_and_removed_logical_behavior() -> None:
    before = _model(source_hash="a" * 64)
    after = _model(source_hash="b" * 64)
    after["operations"] = [
        {
            "id": "create_order",
            "service": "orders",
            "method": "POST",
            "path": "/orders",
            "summary": "Create order",
            "source_refs": [],
        }
    ]
    after["relations"] = []

    before_with_identity = attach_stable_behavior_identity(deepcopy(before))
    after_with_identity = attach_stable_behavior_identity(deepcopy(after))
    delta = build_minimum_ir_delta(before, after)

    assert delta["collections"]["operations"]["removed"] == [
        before_with_identity["operations"][0]["logical_key"]
    ]
    assert delta["collections"]["operations"]["added"] == [
        after_with_identity["operations"][0]["logical_key"]
    ]
    assert delta["summary"]["added"] >= 1
    assert delta["summary"]["removed"] >= 1


def test_production_behavior_ir_builder_emits_revision_identity() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {},
        project_id="project-a",
        source_snapshot_hash="c" * 64,
    )

    assert model["logical_key"].startswith("birlk_model_")
    assert model["revision_id"].startswith("birrev_")
    assert model["revision_identity_schema"] == "qualibug.behavior-ir-revision.v1"
    assert model["model_id"].startswith("bir_model_")
