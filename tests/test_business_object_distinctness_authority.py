from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_distinctness_source_authority import (
    _bind_state_machines,
    _normalized_trees,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_source_preparation import (
    finalize_source_declared_recognition,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_structural_distinctness_review import (
    project_distinctness_structural_candidates,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.object_behavior_surface_binding import (
    prepare_identity_safe_behavior_surfaces,
    project_behavior_surface_bindings,
)


def _source_heading_asset() -> dict:
    return {
        "document_semantic_trees": {
            "items": [
                {
                    "source_id": "prd:maintenance",
                    "nodes": [
                        {
                            "semantic_heading": True,
                            "raw_heading": "2. 核心业务实体",
                            "title": "2. 核心业务实体",
                            "path_titles": ["2. 核心业务实体"],
                        },
                        {
                            "semantic_heading": True,
                            "raw_heading": "2.1 维护工单 (MaintenanceTicket)",
                            "title": "2.1 维护工单 (MaintenanceTicket)",
                            "path_titles": [
                                "2. 核心业务实体",
                                "2.1 维护工单 (MaintenanceTicket)",
                            ],
                        },
                        {
                            "semantic_heading": True,
                            "raw_heading": "3. 维护工单状态机",
                            "title": "3. 维护工单状态机",
                            "path_titles": ["3. 维护工单状态机"],
                        },
                    ],
                }
            ]
        },
        "state_machines": [
            {
                "state_machine_id": "machine:maintenance",
                "source_id": "prd:maintenance",
                "object": "ticket",
                "states": ["NEW", "ACTIVE", "CLOSED"],
            }
        ],
    }


def test_numbered_headings_and_state_machine_use_source_local_object_authority() -> None:
    normalized, declared = _normalized_trees(_source_heading_asset())
    nodes = normalized["document_semantic_trees"]["items"][0]["nodes"]

    assert nodes[0]["raw_heading"] == "业务实体"
    assert nodes[1]["raw_heading"] == "维护工单 (MaintenanceTicket)"
    assert declared == {
        "prd:maintenance": [("维护工单", "MaintenanceTicket")]
    }

    bindings = _bind_state_machines(normalized, declared)
    machine = normalized["state_machines"][0]
    assert machine["object"] == "维护工单"
    assert machine["raw_object"] == "ticket"
    assert machine["object_binding_scope"] == "LIFECYCLE_BINDING_ONLY"
    assert machine["automatic_identity_union_allowed"] is False
    assert bindings == [
        {
            "source_id": "prd:maintenance",
            "surface_label": "维护工单",
            "parent_label": "维护工单",
            "authority": "SOURCE_LOCAL_UNIQUE_ENTITY_HEADING",
            "scope": "LIFECYCLE_BINDING_ONLY",
            "automatic_identity_union_allowed": False,
        }
    ]


def _surface_recognition(evidence: list[dict]) -> dict:
    return {
        "candidates": [
            {
                "candidate_id": "candidate:ticket-surface",
                "comparison_key": "工单",
                "labels": ["工单"],
                "status": "ACCEPTED",
                "source_surface_origin": True,
                "surface_parent_keys": ["维护工单"],
                "identity_resolution_eligible": True,
                "evidence": evidence,
            }
        ],
        "accepted_comparison_keys": ["工单"],
        "accepted_labels": ["工单"],
        "identity_resolution_eligible_comparison_keys": ["工单"],
        "gate": {"metrics": {}},
    }


def _surface_authority() -> dict:
    return {
        "declared_labels": {"维护工单": "维护工单"},
        "declaration_surface_modes": {"维护工单": {"suffix": True}},
        "surface_parents": {},
        "rejected_fact_mentions": [],
    }


def test_direct_surface_evidence_is_not_tainted_by_another_slash_quote() -> None:
    recognition = _surface_recognition(
        [
            {"quote": "工单关闭后禁止再次分配"},
            {"quote": "创建/查看自己的工单"},
        ]
    )

    result = finalize_source_declared_recognition(recognition, _surface_authority())

    assert result["candidates"][0]["status"] == "ACCEPTED"
    assert result["accepted_labels"] == ["工单"]


def test_slash_only_surface_evidence_stays_unauthorized() -> None:
    recognition = _surface_recognition([{"quote": "创建/查看自己的工单"}])

    result = finalize_source_declared_recognition(recognition, _surface_authority())

    candidate = result["candidates"][0]
    assert candidate["status"] == "PENDING_SOURCE_SURFACE_NOT_AUTHORIZED"
    assert candidate["identity_resolution_eligible"] is False
    assert result["accepted_labels"] == []


def _behavior_recognition() -> dict:
    return {
        "identity_resolution_eligible_comparison_keys": ["维护工单"],
        "candidates": [
            {
                "candidate_id": "candidate:maintenance-ticket",
                "status": "ACCEPTED",
                "labels": ["维护工单"],
                "explicit_object_authority": True,
                "identity_resolution_eligible": True,
            },
            {
                "candidate_id": "candidate:ticket-surface",
                "status": "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING",
                "labels": ["工单"],
                "surface_parent_labels": ["维护工单"],
                "identity_resolution_eligible": False,
            },
        ],
    }


def _behavior_asset() -> dict:
    return {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:close-ticket",
                    "kind": "RULE",
                    "raw_statement": "工单关闭后禁止再次提交",
                    "subject": {"entity_refs": [], "entity_mentions": []},
                    "object": {
                        "entity_refs": ["工单"],
                        "entity_mentions": ["工单"],
                        "resolution_evidence": [
                            {"mention": "工单", "resolved_ref": "工单"}
                        ],
                    },
                }
            ]
        }
    }


def test_pending_surface_binds_behavior_only_after_identity_without_union() -> None:
    recognition = _behavior_recognition()
    prepared = prepare_identity_safe_behavior_surfaces(
        _behavior_asset(), recognition
    )
    pre_identity = prepared["business_fact_ledger"]["items"][0]["object"]
    assert pre_identity["entity_refs"] == []
    assert pre_identity["raw_entity_mentions"] == ["工单"]

    prepared["enterprise_identity_resolution"] = {
        "label_to_entity": {"维护工单": "entity:maintenance-ticket"}
    }
    projected = project_behavior_surface_bindings(prepared, recognition)
    object_slot = projected["business_fact_ledger"]["items"][0]["object"]
    receipt = projected["business_object_behavior_binding_receipt"]

    assert object_slot["entity_refs"] == ["维护工单"]
    assert object_slot["resolved_entity_refs"] == ["entity:maintenance-ticket"]
    assert object_slot["identity_pending_mentions"] == ["工单"]
    assert receipt["scope"] == "BEHAVIOR_BINDING_ONLY"
    assert receipt["runs_after_identity_resolution"] is True
    assert receipt["identity_union_performed"] is False
    assert receipt["automatic_alias_edge_created"] is False


def _evidence(source_id: str, locator: str) -> list[dict]:
    return [
        {
            "source_id": source_id,
            "source_locator": locator,
            "quote": locator,
        }
    ]


def _structural_model() -> dict:
    labels = {
        "entity:account": ("客户账户", "prd:account"),
        "entity:profile": ("客户档案", "prd:profile"),
        "entity:supplier": ("供应商账户", "prd:supplier"),
    }
    operations = ["查看", "提交", "关闭"]
    same_lifecycle = [("NEW", "ACTIVE"), ("ACTIVE", "SUSPENDED")]
    supplier_lifecycle = [("DRAFT", "APPROVED"), ("APPROVED", "ARCHIVED")]
    return {
        "business_objects": [
            {
                "entity_id": entity_id,
                "object_id": entity_id,
                "canonical_label": label,
                "name": label,
                "evidence": _evidence(source_id, f"object:{label}"),
            }
            for entity_id, (label, source_id) in labels.items()
        ],
        "operations": [
            {
                "operation_id": f"operation:{entity_id}:{name}",
                "name": name,
                "business_entity_refs": [entity_id],
                "evidence": _evidence(source_id, f"operation:{name}"),
            }
            for entity_id, (_label, source_id) in labels.items()
            for name in operations
        ],
        "lifecycles": [
            {
                "lifecycle_id": f"lifecycle:{entity_id}",
                "business_entity_ref": entity_id,
                "states": sorted({state for edge in transitions for state in edge}),
                "transitions": [
                    {
                        "from_state": source,
                        "to_state": target,
                        "transition_kind": "ALLOWED",
                        "completeness": "COMPLETE",
                        "evidence": _evidence(source_id, f"{source}>{target}"),
                    }
                    for source, target in transitions
                ],
                "evidence": _evidence(source_id, f"lifecycle:{entity_id}"),
            }
            for entity_id, (_label, source_id) in labels.items()
            for transitions in [
                supplier_lifecycle
                if entity_id == "entity:supplier"
                else same_lifecycle
            ]
        ],
        "object_relations": [],
        "gate": {"status": "PASS", "entry_allowed": True},
        "metrics": {},
    }


def test_independent_exact_structure_is_review_only_and_lifecycle_conflict_vetoes() -> None:
    model = _structural_model()
    original = deepcopy(model)
    asset: dict = {}
    resolution = {"clusters": [], "bindings": [], "gate": deepcopy(model["gate"])}

    projected = project_distinctness_structural_candidates(
        asset, model, resolution
    )
    receipt = projected["identity_structural_evidence"]

    assert receipt["candidate_count"] == 1
    candidate = receipt["candidate_pairs"][0]
    assert set(candidate["canonical_labels"].values()) == {
        "客户账户",
        "客户档案",
    }
    assert candidate["source_independence_verified"] is True
    assert candidate["automatic_entity_union_allowed"] is False
    assert candidate["requires_operator_review"] is True

    assert receipt["suppressed_pair_count"] == 2
    assert receipt["suppressed_lifecycle_contradiction_count"] == 2
    assert {
        row["reason_code"] for row in receipt["suppressed_pairs"]
    } == {
        "STRUCTURAL_MATCH_REJECTED_BY_COMPLETE_LIFECYCLE_CONTRADICTION"
    }
    assert receipt["automatic_entity_union_allowed"] is False
    assert resolution["clusters"] == []
    assert resolution["bindings"] == []
    assert projected["gate"] == original["gate"]


def test_understanding_projection_clone_shares_only_finalized_heavy_evidence() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
        clone_asset_for_understanding_projection,
    )

    asset = {
        "document_structure_assets": {"items": [{"blocks": [{"text": "rule"}]}]},
        "enterprise_understanding_model": {"business_objects": [{"name": "Order"}]},
        "business_fact_ledger": {"items": [{"fact_id": "fact:1"}]},
    }

    cloned = clone_asset_for_understanding_projection(asset)

    assert cloned["document_structure_assets"] is asset["document_structure_assets"]
    assert cloned["enterprise_understanding_model"] is asset["enterprise_understanding_model"]
    assert cloned["business_fact_ledger"] is not asset["business_fact_ledger"]
    cloned["business_fact_ledger"]["items"][0]["fact_id"] = "changed"
    assert asset["business_fact_ledger"]["items"][0]["fact_id"] == "fact:1"
