from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_source_governed_table_binding import (
    API_AUTHORITY,
    FIELD_AUTHORITY,
    RECEIPT_SCHEMA,
    project_source_governed_table_bindings,
)

def _evidence(source: str, locator: str, quote: str) -> dict:
    return {"source_id": source, "source_locator": locator, "quote": quote}

def _base() -> tuple[dict, dict, list[dict], list[dict], list[dict]]:
    asset = {
        "data_tables": [],
        "field_dictionary": [],
        "rule_library": [],
        "interfaces": [],
    }
    result = {
        "clusters": [
            {"entity_id": "entity:order", "canonical_label": "订单", "labels": ["订单"]},
            {"entity_id": "entity:inventory", "canonical_label": "库存", "labels": ["库存"]},
            {"entity_id": "entity:refund", "canonical_label": "退款", "labels": ["退款"]},
            {"entity_id": "entity:user", "canonical_label": "用户", "labels": ["用户"]},
        ]
    }
    mentions: list[dict] = []
    edges: list[dict] = []
    bindings: list[dict] = []
    return asset, result, mentions, edges, bindings

def _table_mention(ref: str) -> dict:
    return {
        "mention_id": f"mention:{ref}",
        "mention_type": "TECHNICAL_ARTIFACT",
        "artifact_type": "DATABASE_TABLE",
        "artifact_ref": ref,
    }

def _aggregate_unknown(*refs: str) -> list[dict]:
    return [
        {
            "unknown_id": "unknown:aggregate",
            "details": {
                "unresolved_artifacts": [
                    {"artifact_ref": ref, "artifact_type": "DATABASE_TABLE"}
                    for ref in refs
                ]
            },
            "evidence": [],
        }
    ]

def test_two_exclusive_source_fields_bind_table_to_one_resolved_entity() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:orders",
            "name": "orders",
            "description": "订单主表",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#orders",
            "columns": ["discount_amount", "total_amount"],
            "field_dictionary": [
                {
                    "field_id": "field:discount",
                    "field": "discount_amount",
                    "source_id": "db",
                    "source_locator": "DB_SCHEMA.md#discount_amount",
                    "description": "优惠金额",
                },
                {
                    "field_id": "field:total",
                    "field": "total_amount",
                    "source_id": "db",
                    "source_locator": "DB_SCHEMA.md#total_amount",
                    "description": "商品总金额",
                },
            ],
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:amount",
            "statement": "discount_amount 不能大于 total_amount",
            "source_id": "rules",
            "source_locator": "BUSINESS_RULES.md#amount",
        }
    ]
    mentions.append(_table_mention("table:orders"))
    unknowns = _aggregate_unknown("table:orders")

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={
            "rule:amount": {
                "entity_ids": ["entity:order"],
                "fact_refs": ["fact:order-amount"],
                "evidence": [
                    _evidence(
                        "rules",
                        "BUSINESS_RULES.md#amount",
                        "discount_amount 不能大于 total_amount",
                    )
                ],
            }
        },
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts=set(),
        unknowns=unknowns,
    )

    binding = bindings[0]
    assert binding["artifact_ref"] == "table:orders"
    assert binding["entity_id"] == "entity:order"
    assert binding["identity_authorities"] == [FIELD_AUTHORITY]
    assert binding["source_field_refs"] == ["discount_amount", "total_amount"]
    assert binding["source_rule_refs"] == ["rule:amount"]
    assert binding["source_fact_refs"] == ["fact:order-amount"]
    assert projected == []
    assert edges[0]["automatic_union_allowed"] is False
    receipt = result["source_governed_table_binding"]
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["admitted_binding_count"] == 1
    assert receipt["token_overlap_authority_used"] is False

def test_one_exact_field_never_crosses_hard_admission_threshold() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:payments",
            "name": "payments",
            "description": "支付流水",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#payments",
            "columns": ["idempotency_key"],
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:payment",
            "statement": "idempotency_key 必须唯一",
            "source_id": "rules",
            "source_locator": "BUSINESS_RULES.md#payment",
        }
    ]
    mentions.append(_table_mention("table:payments"))
    unknowns = _aggregate_unknown("table:payments")

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={
            "rule:payment": {
                "entity_ids": ["entity:order"],
                "fact_refs": [],
                "evidence": [
                    _evidence("rules", "BUSINESS_RULES.md#payment", "idempotency_key 必须唯一")
                ],
            }
        },
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts=set(),
        unknowns=unknowns,
    )

    assert bindings == []
    assert projected[0]["details"]["unresolved_artifacts"][0]["artifact_ref"] == "table:payments"
    assert result["source_governed_table_binding"]["field_ownership_requires_two_exclusive_fields"] is True

def test_conflicting_source_governed_entities_fail_closed() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:orders",
            "name": "orders",
            "description": "订单库存主表",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#orders",
            "columns": ["discount_amount", "total_amount", "available_qty", "locked_qty"],
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:order",
            "statement": "discount_amount 不能大于 total_amount",
            "source_id": "rules",
            "source_locator": "rules#order",
        },
        {
            "rule_id": "rule:inventory",
            "statement": "available_qty 与 locked_qty 不能为负",
            "source_id": "rules",
            "source_locator": "rules#inventory",
        },
    ]
    mentions.append(_table_mention("table:orders"))
    unknowns = _aggregate_unknown("table:orders")

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={
            "rule:order": {
                "entity_ids": ["entity:order"],
                "evidence": [_evidence("rules", "rules#order", "discount_amount 不能大于 total_amount")],
            },
            "rule:inventory": {
                "entity_ids": ["entity:inventory"],
                "evidence": [_evidence("rules", "rules#inventory", "available_qty 与 locked_qty 不能为负")],
            },
        },
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts=set(),
        unknowns=unknowns,
    )

    assert bindings == []
    assert projected
    receipt = result["source_governed_table_binding"]
    assert receipt["conflict_count"] == 1
    assert receipt["conflicts"][0]["reason_code"] == "SOURCE_GOVERNED_TABLE_BINDING_ENTITY_CONFLICT"
    assert receipt["conflicts"][0]["automatic_resolution_allowed"] is False

def test_builder_composes_technical_then_source_governed_then_field_authority_once() -> None:
    import inspect

    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
        builder,
    )

    source = inspect.getsource(builder.build_enterprise_understanding_model)
    technical = source.index("augment_technical_identity_projection(")
    governed = source.index("augment_source_governed_table_bindings(")
    field = source.index("augment_identity_field_evidence(")
    assert technical < governed < field
    assert source.count("augment_source_governed_table_bindings(") == 1

def test_source_less_table_cannot_use_other_sources_to_self_promote() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:orders",
            "name": "orders",
            "description": "订单主表",
            "columns": ["discount_amount", "total_amount"],
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:amount",
            "statement": "discount_amount 不能大于 total_amount",
            "source_id": "rules",
        }
    ]

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={
            "rule:amount": {
                "entity_ids": ["entity:order"],
                "evidence": [_evidence("rules", "rules#amount", "amount")],
            }
        },
        mentions=[_table_mention("table:orders")],
        edges=edges,
        bindings=bindings,
        bound_artifacts=set(),
        unknowns=_aggregate_unknown("table:orders"),
    )

    assert bindings == []
    assert projected
    receipt = result["source_governed_table_binding"]
    assert receipt["cross_source_independence_required"] is True

def test_same_source_rule_and_table_cannot_form_cross_source_authority() -> None:
    asset, result, _mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:orders",
            "name": "orders",
            "description": "订单主表",
            "source_id": "shared",
            "columns": ["discount_amount", "total_amount"],
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:amount",
            "statement": "discount_amount 不能大于 total_amount",
            "source_id": "shared",
        }
    ]

    project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={
            "rule:amount": {
                "entity_ids": ["entity:order"],
                "evidence": [_evidence("shared", "shared#amount", "amount")],
            }
        },
        mentions=[_table_mention("table:orders")],
        edges=edges,
        bindings=bindings,
        bound_artifacts=set(),
        unknowns=_aggregate_unknown("table:orders"),
    )

    assert bindings == []

def test_semantically_ambiguous_table_description_blocks_single_candidate() -> None:
    asset, result, _mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:orders",
            "name": "orders",
            "description": "订单库存联合表",
            "source_id": "db",
            "columns": ["discount_amount", "total_amount"],
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:amount",
            "statement": "discount_amount 不能大于 total_amount",
            "source_id": "rules",
        }
    ]

    project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={
            "rule:amount": {
                "entity_ids": ["entity:order"],
                "evidence": [_evidence("rules", "rules#amount", "amount")],
            }
        },
        mentions=[_table_mention("table:orders")],
        edges=edges,
        bindings=bindings,
        bound_artifacts=set(),
        unknowns=_aggregate_unknown("table:orders"),
    )

    assert bindings == []
    conflict = result["source_governed_table_binding"]["conflicts"][0]
    assert conflict["reason_code"] == "SOURCE_GOVERNED_TABLE_SEMANTIC_AMBIGUITY"
    assert conflict["blocks_formal_understanding"] is False
    assert conflict["blocks_technical_binding"] is True
