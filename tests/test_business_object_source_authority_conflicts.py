from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_source_conflicts import (
    OBJECT_DECLARATION_ALIAS_CONFLICT,
    detect_business_object_source_conflicts,
    project_business_object_source_conflicts,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding._object_source_declarations import (
    source_object_declarations,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_object_recognition import (
    recognize_business_objects,
)


def _node(source_id: str, locator: str, heading: str) -> dict:
    return {
        "node_id": f"node:{source_id}:{heading}",
        "semantic_heading": True,
        "raw_heading": heading,
        "title": heading,
        "path_titles": ["客户管理", "业务对象", heading],
        "evidence": {
            "source_id": source_id,
            "source_locator": locator,
            "quote": heading,
            "quote_hash": f"hash:{source_id}:{heading}",
        },
    }


def _asset() -> dict:
    return {
        "asset_id": "asset:object-source-conflict",
        "source_inventory": [
            {"source_id": "src:legacy", "source_type": "prd"},
            {"source_id": "src:current", "source_type": "prd"},
        ],
        "document_semantic_trees": {
            "items": [
                {
                    "source_id": "src:legacy",
                    "nodes": [
                        _node(
                            "src:legacy",
                            "PRD_LEGACY.md#line=5",
                            "CustomerProfile（客户）",
                        ),
                        _node(
                            "src:legacy",
                            "PRD_LEGACY.md#line=8",
                            "Contract（合同）",
                        ),
                    ],
                },
                {
                    "source_id": "src:current",
                    "nodes": [
                        _node(
                            "src:current",
                            "PRD_CURRENT.md#line=5",
                            "CustomerAccount（客户）",
                        ),
                        _node(
                            "src:current",
                            "PRD_CURRENT.md#line=8",
                            "Contract（合同）",
                        ),
                    ],
                },
            ]
        },
        "business_fact_ledger": {"items": []},
    }


def test_conflicting_cross_source_object_aliases_fail_closed() -> None:
    asset = _asset()
    raw = source_object_declarations(asset)
    conflicts = detect_business_object_source_conflicts(raw)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["kind"] == OBJECT_DECLARATION_ALIAS_CONFLICT
    assert conflict["entity"] == "客户"
    assert conflict["status"] == "UNRESOLVED"
    assert conflict["automatic_winner_selected"] is False
    assert len(conflict["object_declaration_participants"]) == 2
    assert {
        row["canonical_label"]
        for row in conflict["object_declaration_participants"]
    } == {"CustomerProfile", "CustomerAccount"}

    project_business_object_source_conflicts(asset)
    recognition = recognize_business_objects(asset)

    assert recognition["accepted_labels"] == ["Contract", "合同"]
    assert recognition["gate"]["status"] == (
        "BLOCKED_BUSINESS_OBJECT_SOURCE_AUTHORITY_CONFLICT"
    )
    assert recognition["gate"]["entry_allowed"] is False
    assert recognition["gate"]["identity_resolution_allowed"] is False
    assert recognition["gate"]["metrics"][
        "unresolved_source_authority_conflict_count"
    ] == 1
    assert any(
        row["reason_code"] == "BUSINESS_OBJECT_DECLARATION_ALIAS_CONFLICT"
        and row["blocks_formal_understanding"] is True
        for row in recognition["gate"]["critical_conflicts"]
    )


def test_operator_selected_object_declaration_supersedes_other_source() -> None:
    asset = _asset()
    project_business_object_source_conflicts(asset)
    conflict = asset["cross_document_conflicts"][0]
    selected = next(
        row
        for row in conflict["object_declaration_participants"]
        if row["canonical_label"] == "CustomerAccount"
    )
    conflict["status"] = "RESOLVED"
    conflict["authority_decision"] = {
        "status": "RESOLVED",
        "action": "SELECT_FACT",
        "selected_fact_id": selected["fact_id"],
        "authority_source_id": selected["source_id"],
        "document_version": "CURRENT_APPROVED",
        "automatic_resolution_allowed": False,
    }

    declarations = source_object_declarations(asset)
    raw_labels = {
        label
        for row in declarations
        for label in row.get("labels") or []
    }
    recognition = recognize_business_objects(asset)

    # Source extraction remains immutable/auditable; governance is applied only
    # in the recognition projection consumed by the formal model.
    assert "CustomerProfile" in raw_labels
    assert recognition["accepted_labels"] == [
        "Contract",
        "CustomerAccount",
        "合同",
        "客户",
    ]
    assert recognition["gate"]["status"] == "PASS"
    assert recognition["gate"]["metrics"][
        "resolved_source_authority_conflict_count"
    ] == 1
    assert recognition["gate"]["metrics"][
        "unresolved_source_authority_conflict_count"
    ] == 0


def test_cross_source_agreement_is_not_an_authority_conflict() -> None:
    asset = _asset()
    current_nodes = asset["document_semantic_trees"]["items"][1]["nodes"]
    current_nodes[0] = _node(
        "src:current",
        "PRD_CURRENT.md#line=5",
        "CustomerProfile（客户）",
    )

    conflicts = detect_business_object_source_conflicts(
        source_object_declarations(asset)
    )

    assert conflicts == []


def test_same_source_parser_duplicates_do_not_create_conflict() -> None:
    asset = _asset()
    legacy_tree = asset["document_semantic_trees"]["items"][0]
    legacy_tree["nodes"].append(
        deepcopy(
            _node(
                "src:legacy",
                "PRD_LEGACY.md#line=12",
                "CustomerAccount（客户）",
            )
        )
    )
    asset["document_semantic_trees"]["items"] = [legacy_tree]
    asset["source_inventory"] = [
        {"source_id": "src:legacy", "source_type": "prd"}
    ]

    conflicts = detect_business_object_source_conflicts(
        source_object_declarations(asset)
    )

    assert conflicts == []
