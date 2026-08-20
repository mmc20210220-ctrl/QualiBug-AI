"""Generic regressions for exact Behavior-IR coverage authority."""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_hypothesis_coverage import (
    build_behavior_ir_coverage_map,
    compute_obligation_coverage_gaps,
)
from ai_test_asset_center.recall_coverage_authority import (
    attach_coverage_origin,
    obligation_match_dimensions,
)


def _source() -> list[dict[str, str]]:
    return [{"source_id": "prd", "locator": "permission:1", "kind": "rule"}]


def _ir() -> dict:
    return {
        "operations": [
            {"id": "op_a", "method": "POST", "path": "/api/a"},
            {"id": "op_b", "method": "POST", "path": "/api/b"},
            {"id": "op_c", "method": "PATCH", "path": "/api/c/{id}"},
        ],
        "actors": [
            {"id": "actor_admin", "role": "admin", "runtime_bound": True},
            {"id": "actor_user", "role": "user", "runtime_bound": True},
        ],
        "entities": [],
        "states": [],
        "invariants": [],
        "relations": [{
            "id": "rel_admin_a",
            "relation_type": "permits",
            "from_ref": "actor_admin",
            "to_ref": "op_a",
            "actor_ref": "actor_admin",
            "operation_ref": "op_a",
            "status": "accepted",
            "derivation": "explicit",
            "source_refs": _source(),
        }],
    }


def _auth_obligation(
    operation: str,
    *,
    actor: str = "actor_admin",
    relation_refs: list[str] | None = None,
) -> dict:
    return {
        "obligation_id": f"obl_{operation}_{actor}",
        "risk_family": "authorization",
        "subject_refs": [operation, actor],
        "required_operations": [operation],
        "required_actors": [actor],
        "relation_refs": list(relation_refs or []),
        "property": {"operation_ref": operation, "actor_ref": actor},
    }


def test_authorization_coverage_is_relation_backed_not_cartesian() -> None:
    coverage = build_behavior_ir_coverage_map(_ir())
    auth = [
        node for node in coverage["nodes"]
        if node.get("risk_family") == "authorization"
    ]
    assert len(auth) == 1
    assert auth[0]["node_type"] == "relation"
    assert auth[0]["ir_node_id"] == "rel_admin_a"
    assert auth[0]["actor_id"] == "actor_admin"
    assert auth[0]["operation_ref"] == "op_a"
    assert not any(node.get("node_type") == "actor_operation" for node in auth)


def test_same_family_different_operation_does_not_false_cover() -> None:
    gaps = compute_obligation_coverage_gaps(_ir(), [_auth_obligation("op_b")])
    assert gaps["covered_count"] == 0
    assert gaps["uncovered_count"] == 1
    assert gaps["coverage_lineage"][0]["status"] == "UNCOVERED"


def test_exact_authorization_relation_records_lineage() -> None:
    obligation = _auth_obligation("op_a", relation_refs=["rel_admin_a"])
    gaps = compute_obligation_coverage_gaps(_ir(), [obligation])
    assert gaps["covered_count"] == 1
    lineage = gaps["coverage_lineage"][0]
    assert lineage["status"] == "COVERED"
    assert lineage["covered_by_obligation_id"] == obligation["obligation_id"]
    assert set(lineage["match_dimensions"]) >= {"relation", "operation", "actor"}


def test_source_less_authorization_relation_is_not_coverage_authority() -> None:
    model = _ir()
    model["relations"][0]["source_refs"] = []
    model["relations"][0]["derivation"] = "schema-derived"
    coverage = build_behavior_ir_coverage_map(model)
    assert not [
        node for node in coverage["nodes"]
        if node.get("risk_family") == "authorization"
    ]
    assert any(
        gap.get("code") == "AUTHORIZATION_RELATION_SOURCE_UNBOUND"
        for gap in coverage.get("coverage_gaps", [])
    )


def test_legacy_auth_pair_requires_exact_operation_and_actor() -> None:
    node = build_behavior_ir_coverage_map(_ir())["nodes"][0]
    assert obligation_match_dimensions(_auth_obligation("op_b"), node) == []
    assert obligation_match_dimensions(
        _auth_obligation("op_a", actor="actor_user"), node
    ) == []
    assert set(obligation_match_dimensions(_auth_obligation("op_a"), node)) == {
        "risk_family", "operation", "actor"
    }


def test_gap_fill_carries_relation_origin() -> None:
    output = attach_coverage_origin(
        [{
            "obligation_id": "generated",
            "subject_refs": ["op_a"],
            "property": {"_coverage_node_id": "cov_rel_rel_admin_a"},
        }],
        {"uncovered_nodes": [{
            "coverage_id": "cov_rel_rel_admin_a",
            "node_type": "relation",
            "ir_node_id": "rel_admin_a",
        }]},
    )
    assert "rel_admin_a" in output[0]["subject_refs"]
    assert output[0]["relation_refs"] == ["rel_admin_a"]
    assert output[0]["property"]["_coverage_origin_status"] == "SOURCE_BACKED_GAP_FILL"
