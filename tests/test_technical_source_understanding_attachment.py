from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._linking import (
    _contract_fields_for_interface,
    _links_by_exclusive_contract_fields,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.object_graph import (
    build_object_graph,
)


def test_object_graph_reads_from_entity_to_entity_foreign_key() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "orders",
                    "to_entity": "customers",
                    "relation_type": "foreign_key",
                    "source_id": "schema.sql",
                    "derivation": "declared_foreign_key",
                    "status": "accepted",
                }
            ]
        },
        [],
        ["orders", "customers"],
    )

    assert len(relations) == 1
    assert relations[0]["source_object_ref"] == "orders"
    assert relations[0]["target_object_ref"] == "customers"
    assert relations[0]["relation_type"] == "REFERENCES"
    assert unknowns == []


def test_object_graph_rejects_path_segment_operates_on() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "openapi:POST:/orders",
                    "to_entity": "orders",
                    "relation_type": "operates_on",
                    "source_id": "openapi.yaml",
                    "derivation": "path_segment_heuristic",
                    "status": "candidate",
                },
                {
                    "from_entity": "openapi:POST:/orders",
                    "to_entity": "orders",
                    "relation_type": "operates_on",
                    "source_id": "openapi.yaml",
                    "derivation": "path_segment_heuristic",
                    "status": "accepted",
                },
            ]
        },
        [],
        ["orders"],
    )

    assert relations == []
    assert unknowns == []


def test_object_graph_emits_unknown_for_unresolved_foreign_key() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "orders",
                    "to_entity": "warehouses",
                    "relation_type": "foreign_key",
                    "source_id": "schema.sql",
                    "derivation": "declared_foreign_key",
                }
            ]
        },
        [],
        ["orders"],
    )

    assert relations == []
    assert len(unknowns) == 1
    assert unknowns[0]["kind"] == "TECHNICAL_RELATION_ENDPOINT_UNRESOLVED"
    assert unknowns[0]["automatic_inference_allowed"] is False


def test_contract_fields_include_openapi_runtime_metadata() -> None:
    fields = _contract_fields_for_interface(
        {
            "interface_id": "openapi:POST:/orders/{order_id}/ship",
            "request_body_fields": [
                {"name": "warehouse_id", "field": "warehouse_id", "location": "BODY"},
                {"name": "priority", "field": "options.priority", "location": "BODY"},
            ],
            "parameter_contracts": [
                {"name": "order_id", "location": "PATH"},
                {"name": "dry_run", "location": "QUERY"},
            ],
        }
    )

    assert "warehouse_id" in fields
    assert "priority" in fields
    assert "options.priority" in fields or "priority" in fields
    assert "order_id" in fields
    assert "dry_run" in fields


def test_exclusive_contract_fields_bind_via_request_body_fields() -> None:
    edges = _links_by_exclusive_contract_fields(
        [
            {
                "rule_id": "rule-warehouse-required",
                "statement": "发货时 warehouse_id 必须填写。",
                "risk_type": "validation",
            }
        ],
        [
            {
                "interface_id": "openapi:POST:/orders/ship",
                "method": "POST",
                "path": "/orders/ship",
                "request_body_fields": [
                    {"name": "warehouse_id", "field": "warehouse_id", "location": "BODY"},
                ],
            },
            {
                "interface_id": "openapi:GET:/orders/{id}",
                "method": "GET",
                "path": "/orders/{id}",
                "request_body_fields": [],
            },
        ],
    )

    assert len(edges) == 1
    assert edges[0]["to"] == "openapi:POST:/orders/ship"
    assert edges[0]["status"] == "accepted"


def test_technical_interfaces_without_business_operations_emit_unknown() -> None:
    model = build_enterprise_understanding_model(
        {
            "asset_id": "asset-tech-only",
            "interfaces": [
                {
                    "interface_id": "openapi:POST:/orders",
                    "method": "POST",
                    "path": "/orders",
                    "operation_id": "createOrder",
                }
            ],
            "data_tables": [],
            "business_fact_ledger": {"items": []},
            "entity_relations": [],
        }
    )

    kinds = {row.get("kind") for row in model.get("unknowns") or []}
    assert "TECHNICAL_OPERATIONS_WITHOUT_BUSINESS_OPERATIONS" in kinds
    assert model.get("operations") == []


def test_cross_source_identity_unresolved_without_term_alias() -> None:
    model = build_enterprise_understanding_model(
        {
            "asset_id": "asset-cross-source",
            "interfaces": [],
            "data_tables": [
                {
                    "name": "orders",
                    "table_id": "tbl-orders",
                    "columns": ["order_id"],
                    "source_id": "schema.sql",
                }
            ],
            "business_fact_ledger": {
                "items": [
                    {
                        "fact_id": "fact-order-create",
                        "kind": "RULE",
                        "status": "ACCEPTED",
                        "raw_statement": "客服可以创建订单。",
                        "modality": "MAY",
                        "subject": {"actor_refs": ["客服"], "entity_refs": ["订单"]},
                        "object": {"entity_refs": ["订单"]},
                        "action": {"canonical": "创建", "raw": "创建"},
                        "source_id": "prd.md",
                        "source_locator": "L1",
                        "quote": "客服可以创建订单。",
                    }
                ]
            },
            "entity_relations": [],
        }
    )

    kinds = {row.get("kind") for row in model.get("unknowns") or []}
    assert "CROSS_SOURCE_IDENTITY_UNRESOLVED" in kinds
    object_names = {row.get("name") for row in model.get("business_objects") or []}
    assert "订单" in object_names
    assert "orders" in object_names


def test_fk_relation_attaches_when_both_tables_are_understood_objects() -> None:
    model = build_enterprise_understanding_model(
        {
            "asset_id": "asset-fk",
            "interfaces": [],
            "data_tables": [
                {"name": "orders", "columns": ["order_id", "customer_id"], "source_id": "schema.sql"},
                {"name": "customers", "columns": ["customer_id"], "source_id": "schema.sql"},
            ],
            "business_fact_ledger": {"items": []},
            "entity_relations": [
                {
                    "from_entity": "orders",
                    "to_entity": "customers",
                    "relation_type": "foreign_key",
                    "source_id": "schema.sql",
                    "derivation": "declared_foreign_key",
                }
            ],
        }
    )

    relations = model.get("object_relations") or []
    assert any(
        row.get("source_object_ref") == "orders"
        and row.get("target_object_ref") == "customers"
        and row.get("relation_type") == "REFERENCES"
        for row in relations
    )


def test_openapi_chinese_summary_projects_into_fact_with_interface_span() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
        build_chinese_first_comprehension,
        project_openapi_interface_chinese_spans,
    )

    asset = {
        "project_id": "openapi-zh-span",
        "interfaces": [
            {
                "interface_id": "api:POST:/orders/{id}/cancel",
                "source_id": "api.openapi.yaml",
                "source_kind": "openapi",
                "method": "POST",
                "path": "/orders/{id}/cancel",
                "operation_id": "cancelOrder",
                "openapi_summary": "已提交订单必须由客服取消。",
                "openapi_description": "",
                "summary": "已提交订单必须由客服取消。",
                "description": "",
            }
        ],
        "business_objects": [{"name": "订单"}],
        "roles": [{"role": "客服"}],
        "summary": {},
        "rule_library": [],
        "coverage_gaps": [],
    }

    coverage, facts = project_openapi_interface_chinese_spans(asset)
    assert coverage
    assert any(row.get("span_kind") == "OPENAPI_OPERATION_SUMMARY" for row in coverage)
    assert facts
    assert all(row.get("interface_id") == "api:POST:/orders/{id}/cancel" for row in facts)
    assert all(
        any(
            span.get("interface_id") == "api:POST:/orders/{id}/cancel"
            and span.get("attachment") == "openapi_interface_prose"
            for span in (row.get("source_spans") or [])
        )
        for row in facts
    )

    enriched = build_chinese_first_comprehension(asset, [])
    receipt = enriched.get("openapi_interface_span_attachment_receipt") or {}
    assert receipt.get("automatic_inference_from_path_vocabulary_allowed") is False
    assert receipt.get("attached_interface_prose_fact_count", 0) >= 1
    ledger_facts = (enriched.get("business_fact_ledger") or {}).get("items") or []
    assert any(
        row.get("interface_id") == "api:POST:/orders/{id}/cancel"
        for row in ledger_facts
    )


def test_openapi_path_vocabulary_alone_does_not_invent_business_facts() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
        project_openapi_interface_chinese_spans,
    )
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
        build_enterprise_understanding_model,
    )

    asset = {
        "asset_id": "path-only",
        "interfaces": [
            {
                "interface_id": "api:POST:/orders",
                "source_id": "api.openapi.yaml",
                "source_kind": "openapi",
                "method": "POST",
                "path": "/orders",
                "operation_id": "createOrder",
                "summary": "Create order",
                "openapi_summary": "Create order",
                "description": "",
                "openapi_description": "",
            }
        ],
        "data_tables": [],
        "business_fact_ledger": {"items": []},
        "entity_relations": [],
    }

    coverage, facts = project_openapi_interface_chinese_spans(asset)
    assert facts == []
    assert all(row.get("status") == "TERMINAL_NON_CHINESE" for row in coverage)

    model = build_enterprise_understanding_model(asset)
    kinds = {row.get("kind") for row in model.get("unknowns") or []}
    assert "TECHNICAL_OPERATIONS_WITHOUT_BUSINESS_OPERATIONS" in kinds


def test_openapi_preserves_separate_summary_and_description() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import _openapi_operations

    rows = _openapi_operations(
        {
            "paths": {
                "/items": {
                    "post": {
                        "summary": "创建条目",
                        "description": "操作员可以创建条目，但已关闭条目不得修改。",
                        "operationId": "createItem",
                        "responses": {"201": {"description": "created"}},
                    }
                }
            }
        },
        source_id="spec.yaml",
    )
    assert len(rows) == 1
    assert rows[0]["openapi_summary"] == "创建条目"
    assert "不得修改" in rows[0]["openapi_description"]
    assert rows[0]["description"] == rows[0]["openapi_description"]


def test_ambiguous_openapi_chinese_summary_stays_fail_closed() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
        project_openapi_interface_chinese_spans,
    )

    coverage, facts = project_openapi_interface_chinese_spans(
        {
            "interfaces": [
                {
                    "interface_id": "api:POST:/x",
                    "source_id": "api.yaml",
                    "source_kind": "openapi",
                    "summary": "该对象必须处理。",
                    "openapi_summary": "该对象必须处理。",
                    "description": "",
                }
            ]
        }
    )
    # Deictic subject without unique context → PENDING / AMBIGUOUS, never invented entity.
    assert coverage
    if facts:
        assert all(row.get("status") in {"PENDING", "ACCEPTED"} for row in facts)
        pending_or_ambiguous = any(row.get("status") == "PENDING" for row in facts) or any(
            row.get("status") == "AMBIGUOUS" for row in coverage
        )
        assert pending_or_ambiguous or any(
            "COREFERENCE" in str(row.get("ambiguities"))
            or "BUSINESS_SUBJECT" in str(row.get("ambiguities"))
            for row in facts
        )
