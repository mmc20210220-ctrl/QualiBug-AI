from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_structural_evidence import (
    IDENTITY_STRUCTURAL_CANDIDATE_SCHEMA,
    IDENTITY_STRUCTURAL_EVIDENCE_SCHEMA,
    project_identity_structural_candidates,
)


def _evidence(ref: str) -> list[dict]:
    return [
        {
            "source_id": "source",
            "source_locator": ref,
            "quote": ref,
        }
    ]


def _object(entity_id: str, label: str) -> dict:
    return {
        "entity_id": entity_id,
        "object_id": entity_id,
        "canonical_label": label,
        "name": label,
        "evidence": _evidence(f"object:{label}"),
    }


def _operation(entity_id: str, name: str) -> dict:
    return {
        "operation_id": f"operation:{entity_id}:{name}",
        "name": name,
        "business_entity_refs": [entity_id],
        "evidence": _evidence(f"operation:{entity_id}:{name}"),
    }


def _lifecycle(
    entity_id: str,
    transitions: list[tuple[str, str]],
) -> dict:
    states = sorted({state for pair in transitions for state in pair})
    return {
        "lifecycle_id": f"lifecycle:{entity_id}",
        "business_entity_ref": entity_id,
        "states": states,
        "transitions": [
            {
                "transition_id": f"transition:{entity_id}:{source}:{target}",
                "from_state": source,
                "to_state": target,
                "transition_kind": "ALLOWED",
                "completeness": "COMPLETE",
                "evidence": _evidence(
                    f"transition:{entity_id}:{source}:{target}"
                ),
            }
            for source, target in transitions
        ],
        "evidence": _evidence(f"lifecycle:{entity_id}"),
    }


def _relation(source: str, relation_type: str, target: str) -> dict:
    return {
        "relation_id": f"relation:{source}:{relation_type}:{target}",
        "source_entity_ref": source,
        "relation_type": relation_type,
        "target_entity_ref": target,
        "evidence": _evidence(f"relation:{source}:{relation_type}:{target}"),
    }


def _model(
    *,
    operations: dict[str, list[str]] | None = None,
    lifecycles: dict[str, list[tuple[str, str]]] | None = None,
    relations: list[tuple[str, str, str]] | None = None,
    labels: dict[str, str] | None = None,
) -> dict:
    labels = labels or {
        "entity:a": "订单",
        "entity:b": "销售单",
        "entity:customer": "客户",
    }
    rows = [_object(entity_id, label) for entity_id, label in labels.items()]
    operation_rows = [
        _operation(entity_id, name)
        for entity_id, names in (operations or {}).items()
        for name in names
    ]
    lifecycle_rows = [
        _lifecycle(entity_id, transitions)
        for entity_id, transitions in (lifecycles or {}).items()
    ]
    relation_rows = [
        _relation(source, relation_type, target)
        for source, relation_type, target in (relations or [])
    ]
    return {
        "business_objects": rows,
        "operations": operation_rows,
        "lifecycles": lifecycle_rows,
        "object_relations": relation_rows,
        "identity_clusters": [
            {"entity_id": entity_id, "canonical_label": label}
            for entity_id, label in labels.items()
        ],
        "identity_bindings": [
            {
                "binding_id": f"binding:{entity_id}",
                "entity_id": entity_id,
                "artifact_ref": f"artifact:{entity_id}",
            }
            for entity_id in labels
        ],
        "gate": {"status": "PASS", "entry_allowed": True},
        "metrics": {},
    }


def _resolution(model: dict) -> dict:
    return {
        "clusters": deepcopy(model["identity_clusters"]),
        "bindings": deepcopy(model["identity_bindings"]),
        "gate": deepcopy(model["gate"]),
    }


def test_exact_lifecycle_operations_and_neighborhood_create_strong_candidate() -> None:
    transitions = [("草稿", "待审批"), ("待审批", "已完成")]
    model = _model(
        operations={
            "entity:a": ["创建", "审批"],
            "entity:b": ["创建", "审批"],
        },
        lifecycles={
            "entity:a": transitions,
            "entity:b": transitions,
        },
        relations=[
            ("entity:a", "REFERENCES", "entity:customer"),
            ("entity:b", "REFERENCES", "entity:customer"),
        ],
    )
    resolution = _resolution(model)
    original_clusters = deepcopy(resolution["clusters"])
    original_bindings = deepcopy(resolution["bindings"])
    original_gate = deepcopy(resolution["gate"])
    asset: dict = {}

    projected = project_identity_structural_candidates(asset, model, resolution)

    receipt = projected["identity_structural_evidence"]
    assert receipt["schema"] == IDENTITY_STRUCTURAL_EVIDENCE_SCHEMA
    assert receipt["candidate_count"] == 1
    assert receipt["strong_candidate_count"] == 1
    candidate = receipt["candidate_pairs"][0]
    assert candidate["schema"] == IDENTITY_STRUCTURAL_CANDIDATE_SCHEMA
    assert candidate["candidate_entity_ids"] == ["entity:a", "entity:b"]
    assert candidate["strength"] == "STRONG_STRUCTURAL_CANDIDATE"
    assert candidate["matched_dimensions"] == [
        "EXACT_OPERATION_SET",
        "EXACT_LIFECYCLE_TOPOLOGY",
        "SHARED_RELATION_NEIGHBORHOOD",
    ]
    assert candidate["status"] == "CANDIDATE_ONLY"
    assert candidate["automatic_resolution_allowed"] is False
    assert candidate["automatic_entity_union_allowed"] is False
    assert candidate["requires_operator_review"] is True
    assert candidate["evidence"]
    assert asset["enterprise_identity_structural_evidence"] == receipt
    assert resolution["clusters"] == original_clusters
    assert resolution["bindings"] == original_bindings
    assert resolution["gate"] == original_gate
    assert projected["gate"] == original_gate
    for obj in projected["business_objects"][:2]:
        assert obj["structural_identity_candidate_refs"] == [
            candidate["candidate_id"]
        ]


def test_one_matching_dimension_never_creates_candidate() -> None:
    model = _model(
        operations={
            "entity:a": ["创建", "审批", "关闭"],
            "entity:b": ["创建", "审批", "关闭"],
        }
    )
    resolution = _resolution(model)

    projected = project_identity_structural_candidates({}, model, resolution)

    assert projected["identity_structural_candidates"] == []
    assert projected["identity_structural_evidence"]["candidate_count"] == 0
    assert projected["gate"]["entry_allowed"] is True


def test_three_operations_plus_shared_relation_is_review_candidate() -> None:
    model = _model(
        operations={
            "entity:a": ["创建", "审批", "关闭"],
            "entity:b": ["创建", "审批", "关闭"],
        },
        relations=[
            ("entity:a", "BELONGS_TO", "entity:customer"),
            ("entity:b", "BELONGS_TO", "entity:customer"),
        ],
    )
    resolution = _resolution(model)

    projected = project_identity_structural_candidates({}, model, resolution)

    candidate = projected["identity_structural_candidates"][0]
    assert candidate["strength"] == "REVIEW_STRUCTURAL_CANDIDATE"
    assert candidate["matched_dimensions"] == [
        "EXACT_OPERATION_SET",
        "SHARED_RELATION_NEIGHBORHOOD",
    ]
    assert candidate["matched_lifecycle_transitions"] == []


def test_two_operations_plus_shared_relation_is_below_admission_threshold() -> None:
    model = _model(
        operations={
            "entity:a": ["创建", "审批"],
            "entity:b": ["创建", "审批"],
        },
        relations=[
            ("entity:a", "BELONGS_TO", "entity:customer"),
            ("entity:b", "BELONGS_TO", "entity:customer"),
        ],
    )
    resolution = _resolution(model)

    projected = project_identity_structural_candidates({}, model, resolution)

    assert projected["identity_structural_candidates"] == []


def test_different_lifecycle_topology_does_not_double_count_operations() -> None:
    model = _model(
        operations={
            "entity:a": ["创建", "审批"],
            "entity:b": ["创建", "审批"],
        },
        lifecycles={
            "entity:a": [("草稿", "待审批"), ("待审批", "已完成")],
            "entity:b": [("草稿", "待审批"), ("待审批", "已取消")],
        },
    )
    resolution = _resolution(model)

    projected = project_identity_structural_candidates({}, model, resolution)

    assert projected["identity_structural_candidates"] == []


def test_projection_is_deterministic_and_does_not_merge_same_name_conflicts() -> None:
    transitions = [("新建", "处理中"), ("处理中", "完成")]
    initial = _model(
        labels={
            "entity:a": "Order",
            "entity:b": "order",
        },
        operations={
            "entity:a": ["create", "finish"],
            "entity:b": ["create", "finish"],
        },
        lifecycles={
            "entity:a": transitions,
            "entity:b": transitions,
        },
    )

    left_model = deepcopy(initial)
    right_model = deepcopy(initial)
    left = project_identity_structural_candidates(
        {}, left_model, _resolution(left_model)
    )
    right = project_identity_structural_candidates(
        {}, right_model, _resolution(right_model)
    )

    assert left["identity_structural_evidence"] == right[
        "identity_structural_evidence"
    ]
    assert left["identity_structural_candidates"] == []
    assert left["metrics"]["enterprise_identity_structural_profile_count"] == 2
    assert (
        left["identity_structural_evidence"][
            "automatic_similarity_merge_allowed"
        ]
        is False
    )
    assert (
        left["identity_structural_evidence"]["changes_identity_resolution"]
        is False
    )
