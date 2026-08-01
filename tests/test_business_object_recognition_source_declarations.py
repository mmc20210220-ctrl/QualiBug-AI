from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)
from tests.test_business_object_recognition_types import _asset, _rule, _span


def _bind_primary_object(fact: dict, label: str) -> None:
    fact["claims"] = [{
        "claim_type": "PRIMARY_OPERATION",
        "predicate": (fact.get("action") or {}).get("canonical") or "查看",
        "object_refs": [label],
    }]


def _entity_heading_tree(title: str, *, parent: str = "核心实体") -> dict:
    return {
        "items": [
            {
                "nodes": [
                    {
                        "node_id": f"node:{title}",
                        "semantic_heading": True,
                        "raw_heading": title,
                        "title": title,
                        "path_titles": ["企业数据字典", parent, title],
                        "evidence": {
                            "source_id": "data-dictionary",
                            "source_locator": f"DATA_DICTIONARY.md#{title}",
                            "quote": title,
                            "quote_hash": f"hash-{title}",
                        },
                    }
                ]
            }
        ]
    }


def _entity_relation_tree(
    relation: str, *, parent: str = "实体关系"
) -> dict:
    return {
        "items": [
            {
                "nodes": [
                    {
                        "node_id": f"node:{relation}",
                        "semantic_heading": False,
                        "title": relation,
                        "path_titles": ["企业数据字典", parent],
                        "evidence": {
                            "source_id": "data-dictionary",
                            "source_locator": f"DATA_DICTIONARY.md#{relation}",
                            "quote": relation,
                            "quote_hash": f"hash-{relation}",
                        },
                    }
                ]
            }
        ]
    }


def test_entity_relation_section_declares_endpoints_and_fk_display_alias() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            document_semantic_trees=_entity_relation_tree(
                "Order ──N:1──> Customer"
            ),
            field_dictionary=[
                {
                    "field_id": "field:customer",
                    "field": "customer_id",
                    "type": "FK→Customer",
                    "description": "客户",
                    "source_id": "data-dictionary",
                    "source_locator": "DATA_DICTIONARY.md#customer-id",
                }
            ],
        )
    )

    recognition = model["business_object_recognition"]
    assert set(recognition["accepted_labels"]) == {"Order", "Customer", "客户"}
    customer = next(
        row
        for row in model["business_objects"]
        if {row["name"], *row["aliases"]} == {"Customer", "客户"}
    )
    assert customer["name"] in {"Customer", "客户"}
    assert any(
        {edge["left_label"], edge["right_label"]} == {"Customer", "客户"}
        for edge in recognition["accepted_alias_edges"]
    )


def test_relation_shaped_prose_outside_entity_relation_section_is_not_authority() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            document_semantic_trees=_entity_relation_tree(
                "Order ──N:1──> Customer", parent="系统概述"
            ),
        )
    )

    assert model["business_objects"] == []
    assert model["business_object_recognition"]["candidates"] == []


def test_foreign_key_label_cannot_create_object_without_relation_declaration() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            field_dictionary=[
                {
                    "field_id": "field:customer",
                    "field": "customer_id",
                    "type": "FK→Customer",
                    "description": "客户",
                    "source_id": "data-dictionary",
                    "source_locator": "DATA_DICTIONARY.md#customer-id",
                }
            ],
        )
    )

    assert model["business_objects"] == []
    assert model["business_object_recognition"]["candidates"] == []


def test_foreign_key_relationship_qualifier_is_not_promoted_as_display_alias() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            document_semantic_trees=_entity_relation_tree(
                "Order ──N:1──> Warehouse"
            ),
            field_dictionary=[
                {
                    "field_id": "field:warehouse",
                    "field": "warehouse_id",
                    "type": "FK→Warehouse",
                    "description": "所在仓库",
                    "source_id": "data-dictionary",
                    "source_locator": "DATA_DICTIONARY.md#warehouse-id",
                }
            ],
        )
    )

    recognition = model["business_object_recognition"]
    assert set(recognition["accepted_labels"]) == {"Order", "Warehouse"}
    assert "所在仓库" not in {
        label for row in recognition["candidates"] for label in row["labels"]
    }
    assert not any(
        edge["authority"] == "SOURCE_ENTITY_RELATION_FIELD_LABEL"
        for edge in recognition["accepted_alias_edges"]
    )


def test_core_entity_heading_declares_object_and_bilingual_alias() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            document_semantic_trees=_entity_heading_tree("Product (商品)"),
        )
    )

    assert len(model["business_objects"]) == 1
    obj = model["business_objects"][0]
    assert {obj["name"], *obj["aliases"]} == {"Product", "商品"}
    recognition = model["business_object_recognition"]
    assert set(recognition["accepted_labels"]) == {"Product", "商品"}
    assert recognition["gate"]["metrics"]["accepted_alias_edge_count"] == 1


def test_heading_outside_entity_declaration_section_is_not_object_authority() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            document_semantic_trees=_entity_heading_tree(
                "Product (商品)", parent="关键业务约束"
            ),
        )
    )

    assert model["business_objects"] == []
    assert model["business_object_recognition"]["candidates"] == []


def test_declared_entity_authority_blocks_unbound_rule_fragments_and_keeps_surfaces() -> None:
    facts = [
        _rule("r-inventory", ["库存"]),
        _rule("r-return-record", ["退货记录"]),
        _rule("r-application", ["申请"]),
        _rule("r-generic-data", ["数据"]),
    ]
    facts[0]["raw_statement"] = "库存管理"
    facts[0]["normalized_statement"] = "库存管理"
    facts[0]["source_spans"] = _span("r-inventory", "库存管理")
    facts[1]["raw_statement"] = "查看自己的退货记录"
    facts[1]["normalized_statement"] = "查看自己的退货记录"
    facts[1]["source_spans"] = _span("r-return-record", "查看自己的退货记录")
    facts[2]["raw_statement"] = "申请退货"
    facts[2]["normalized_statement"] = "申请退货"
    facts[2]["source_spans"] = _span("r-application", "申请退货")
    facts[3]["raw_statement"] = "不可修改任何业务数据"
    facts[3]["normalized_statement"] = "不可修改任何业务数据"
    facts[3]["source_spans"] = _span("r-generic-data", "不可修改任何业务数据")
    _bind_primary_object(facts[0], "库存")
    _bind_primary_object(facts[1], "退货记录")
    _bind_primary_object(facts[2], "申请")
    _bind_primary_object(facts[3], "数据")

    trees = {
        "items": [
            {
                "nodes": [
                    _entity_heading_tree("InventoryBatch (库存批次)")["items"][0]["nodes"][0],
                    _entity_heading_tree("Reservation (库存预留)")["items"][0]["nodes"][0],
                    _entity_heading_tree("Return (退货)")["items"][0]["nodes"][0],
                ]
            }
        ]
    }
    model = build_enterprise_understanding_model(
        _asset(facts, document_semantic_trees=trees)
    )
    recognition = model["business_object_recognition"]

    assert {"InventoryBatch", "库存批次", "Return", "退货", "库存", "退货记录"}.issubset(
        set(recognition["accepted_labels"])
    )
    assert "申请" not in recognition["accepted_labels"]
    assert "数据" not in recognition["accepted_labels"]
    rejected = {
        row["label"]: row["reason_code"]
        for row in recognition["rejected_fact_mentions"]
    }
    assert rejected["申请"] == (
        "UNDECLARED_OBJECT_SLOT_WHEN_SOURCE_DECLARATIONS_EXIST"
    )
    assert rejected["数据"] == (
        "UNDECLARED_OBJECT_SLOT_WHEN_SOURCE_DECLARATIONS_EXIST"
    )
    for surface_label in ("库存", "退货记录"):
        surface = next(
            row
            for row in recognition["candidates"]
            if surface_label in row["labels"]
        )
        assert surface["status"] == "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING"
        assert surface["identity_resolution_eligible"] is False
    formal_names = {row["name"] for row in model["business_objects"]}
    assert "库存" not in formal_names
    assert "退货记录" not in formal_names


def test_ambiguous_surface_in_object_slot_is_typed_but_identity_stays_pending() -> None:
    fact = _rule("r-inventory", ["库存"])
    fact["raw_statement"] = "库存不足时禁止分配"
    fact["normalized_statement"] = fact["raw_statement"]
    fact["source_spans"] = _span("r-inventory", fact["raw_statement"])
    _bind_primary_object(fact, "库存")
    trees = {
        "items": [{
            "nodes": [
                _entity_heading_tree("InventoryBatch (库存批次)")["items"][0]["nodes"][0],
                _entity_heading_tree("Reservation (库存预留)")["items"][0]["nodes"][0],
            ]
        }]
    }

    recognition = build_enterprise_understanding_model(
        _asset([fact], document_semantic_trees=trees)
    )["business_object_recognition"]

    surface = next(row for row in recognition["candidates"] if row["labels"] == ["库存"])
    assert surface["status"] == "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING"
    assert set(surface["surface_parent_labels"]) == {"库存批次", "库存预留"}
    assert surface["identity_resolution_eligible"] is False


def test_source_prose_prefix_does_not_drop_object_classifier_into_action() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            [],
            document_semantic_trees=_entity_heading_tree("PickList (拣货单)"),
            interfaces=[
                {
                    "interface_id": "api:start-pick",
                    "source_id": "openapi",
                    "source_locator": "openapi.yaml#/start-pick",
                    "openapi_summary": "开始拣货",
                }
            ],
        )
    )

    recognition = model["business_object_recognition"]
    assert "拣货单" in recognition["accepted_labels"]
    assert "拣货" not in {
        label for row in recognition["candidates"] for label in row["labels"]
    }
