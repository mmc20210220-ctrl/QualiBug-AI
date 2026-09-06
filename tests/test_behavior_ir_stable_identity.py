from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center.behavior_ir import (
    StableBehaviorIdentityError,
    attach_stable_behavioral_identity,
    build_behavior_ir_from_knowledge_asset,
    build_ir_delta,
    match_behavior_ir_revisions,
    validate_revision_identity,
)


def _model(
    *,
    source_hash: str,
    operation_id: str = "op_v1",
    summary: str = "List orders",
) -> dict:
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
                "source_refs": [
                    {
                        "source_id": "api",
                        "version": source_hash[:8],
                        "locator": "GET /orders/{order_id}",
                    }
                ],
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
                "source_refs": [],
            },
        ],
    }


def test_same_behavior_keeps_logical_key_across_source_revisions() -> None:
    before = attach_stable_behavioral_identity(_model(source_hash="a" * 64))
    after = attach_stable_behavioral_identity(
        _model(source_hash="b" * 64, operation_id="op_v2")
    )

    assert before["logical_key"] == after["logical_key"]
    assert before["revision_id"] != after["revision_id"]
    assert before["operations"][0]["logical_key"] == after["operations"][0]["logical_key"]
    assert before["relations"][0]["logical_key"] == after["relations"][0]["logical_key"]

    result = match_behavior_ir_revisions(before, after)
    operation_match = next(
        row for row in result["matches"] if row["collection"] == "operations"
    )
    assert operation_match == {
        "logical_key": before["operations"][0]["logical_key"],
        "collection": "operations",
        "previous_id": "op_v1",
        "current_id": "op_v2",
    }
    assert result["llm_used"] is False


def test_mutated_behavior_is_modified_not_removed_and_added() -> None:
    before = _model(source_hash="a" * 64, summary="List orders")
    after = _model(
        source_hash="b" * 64,
        operation_id="op_v2",
        summary="List orders with status",
    )

    operation_key = attach_stable_behavioral_identity(deepcopy(before))["operations"][0]["logical_key"]
    delta = build_ir_delta(before, after)

    assert operation_key in delta["modified"]
    assert operation_key not in delta["added"]
    assert operation_key not in delta["removed"]
    assert delta["llm_used"] is False


def test_ir_delta_reports_added_removed_and_impacted() -> None:
    before = _model(source_hash="a" * 64)
    after = _model(source_hash="b" * 64)
    before_identity = attach_stable_behavioral_identity(deepcopy(before))
    removed_operation_key = before_identity["operations"][0]["logical_key"]
    entity_key = before_identity["entities"][0]["logical_key"]

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
    added_operation_key = attach_stable_behavioral_identity(deepcopy(after))["operations"][0]["logical_key"]

    delta = build_ir_delta(before, after)

    assert removed_operation_key in delta["removed"]
    assert added_operation_key in delta["added"]
    assert entity_key in delta["impacted"]
    assert set(delta) >= {
        "added",
        "modified",
        "removed",
        "impacted",
        "previous_revision_id",
        "current_revision_id",
    }


def test_identity_collision_fails_closed_instead_of_cross_revision_matching() -> None:
    model = _model(source_hash="a" * 64)
    model["operations"].append(
        {
            "id": "duplicate_transport",
            "service": "orders",
            "method": "GET",
            "path": "/orders/{id}",
            "summary": "Duplicate declaration",
            "source_refs": [],
        }
    )

    identity = attach_stable_behavioral_identity(model)
    assert identity["behavioral_identity"]["collision_count"] >= 1
    assert all(row["logical_key_matchable"] is False for row in identity["operations"])

    matches = match_behavior_ir_revisions(identity, deepcopy(identity))
    operation_keys = {
        row["logical_key"]
        for row in identity["operations"]
    }
    assert not operation_keys.intersection({row["logical_key"] for row in matches["matches"]})


def test_revision_identity_detects_mutation() -> None:
    model = attach_stable_behavioral_identity(_model(source_hash="a" * 64))
    assert validate_revision_identity(model) is True

    model["operations"][0]["summary"] = "tampered after revision identity"
    with pytest.raises(StableBehaviorIdentityError, match="behavior_ir_revision_identity_mutated"):
        validate_revision_identity(model)


def test_revision_identity_is_invariant_to_collection_order() -> None:
    left = _model(source_hash="a" * 64)
    left["entities"].append({"id": "entity_customer", "name": "Customer", "service": "orders"})
    right = deepcopy(left)
    right["entities"] = list(reversed(right["entities"]))

    left_identity = attach_stable_behavioral_identity(left)
    right_identity = attach_stable_behavioral_identity(right)

    assert left_identity["revision_id"] == right_identity["revision_id"]
    assert (
        left_identity["behavioral_identity"]["revision"]["fingerprint"]
        == right_identity["behavioral_identity"]["revision"]["fingerprint"]
    )


def test_repeated_attach_recomputes_identity_from_current_final_ir() -> None:
    model = attach_stable_behavioral_identity(_model(source_hash="a" * 64))
    original_key = model["operations"][0]["logical_key"]
    original_revision = model["revision_id"]

    model["operations"][0]["method"] = "POST"
    attach_stable_behavioral_identity(model)

    assert model["operations"][0]["logical_key"] != original_key
    assert model["revision_id"] != original_revision


def test_public_production_behavior_ir_authority_emits_stable_identity() -> None:
    model = build_behavior_ir_from_knowledge_asset(
        {},
        project_id="project-a",
        source_snapshot_hash="c" * 64,
    )

    assert model["logical_key"].startswith("birlk_model_")
    assert model["revision_id"].startswith("birrev_")
    assert model["revision_identity_schema"] == "qualibug.source-revision.v1"
    assert model["behavioral_identity"]["parallel_behavior_ir_created"] is False
    assert model["behavioral_identity"]["llm_change_classification_used"] is False
    assert model["model_id"].startswith("bir_model_")
