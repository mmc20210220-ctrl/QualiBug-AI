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

def test_exact_api_resource_bridge_requires_authoritative_api_binding_and_semantic_corroboration() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:refunds",
            "name": "refunds",
            "description": "退款售后",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#refunds",
        }
    ]
    asset["interfaces"] = [
        {
            "interface_id": "api:refund",
            "path": "/api/refunds/:id",
            "summary": "查询退款单详情",
            "source_id": "api",
            "source_locator": "API_SPEC.md#refunds",
        }
    ]
    bindings.append(
        {
            "artifact_type": "API_OPERATION",
            "artifact_ref": "api:refund",
            "entity_id": "entity:refund",
            "status": "RESOLVED",
            "identity_authorities": ["SOURCE_BACKED_RULE_IMPLEMENTATION"],
            "source_rule_refs": ["rule:refund"],
            "source_fact_refs": ["fact:refund"],
            "evidence": [
                _evidence("api", "API_SPEC.md#refunds", "查询退款单详情")
            ],
        }
    )
    mentions.append(_table_mention("table:refunds"))
    unknowns = _aggregate_unknown("table:refunds")

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={},
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts={"api:refund"},
        unknowns=unknowns,
    )

    table_binding = next(row for row in bindings if row.get("artifact_ref") == "table:refunds")
    assert table_binding["entity_id"] == "entity:refund"
    assert table_binding["identity_authorities"] == [API_AUTHORITY]
    assert table_binding["source_interface_refs"] == ["api:refund"]
    assert table_binding["source_semantic_labels"] == ["退款"]
    assert projected == []

def test_api_resource_path_overlap_without_exact_segment_does_not_bind() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:refund_history",
            "name": "refund_history",
            "description": "退款历史",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#refund_history",
        }
    ]
    asset["interfaces"] = [
        {
            "interface_id": "api:refund",
            "path": "/api/refunds/:id",
            "summary": "查询退款单详情",
            "source_id": "api",
            "source_locator": "API_SPEC.md#refunds",
        }
    ]
    bindings.append(
        {
            "artifact_type": "API_OPERATION",
            "artifact_ref": "api:refund",
            "entity_id": "entity:refund",
            "status": "RESOLVED",
            "identity_authorities": ["SOURCE_BACKED_RULE_IMPLEMENTATION"],
            "evidence": [_evidence("api", "API_SPEC.md#refunds", "查询退款单详情")],
        }
    )
    mentions.append(_table_mention("table:refund_history"))
    unknowns = _aggregate_unknown("table:refund_history")

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={},
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts={"api:refund"},
        unknowns=unknowns,
    )

    assert not any(row.get("artifact_ref") == "table:refund_history" for row in bindings)
    assert projected

def test_nested_resource_cannot_bind_parent_table_to_child_entity() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:users",
            "name": "users",
            "description": "用户与角色",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#users",
        }
    ]
    asset["interfaces"] = [
        {
            "interface_id": "api:addresses",
            "path": "/api/users/addresses",
            "summary": "查询用户地址",
            "source_id": "api",
            "source_locator": "API_SPEC.md#addresses",
        }
    ]
    bindings.append(
        {
            "artifact_type": "API_OPERATION",
            "artifact_ref": "api:addresses",
            "entity_id": "entity:refund",
            "status": "RESOLVED",
            "identity_authorities": ["SOURCE_BACKED_RULE_IMPLEMENTATION"],
            "evidence": [_evidence("api", "API_SPEC.md#addresses", "查询用户地址")],
        }
    )
    mentions.append(_table_mention("table:users"))
    unknowns = _aggregate_unknown("table:users")

    projected = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={},
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts={"api:addresses"},
        unknowns=unknowns,
    )

    assert not any(row.get("artifact_ref") == "table:users" for row in bindings)
    assert projected

def test_projection_is_deterministic_and_never_mutates_business_clusters() -> None:
    asset, result, mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:refunds",
            "name": "refunds",
            "description": "退款售后",
            "source_id": "db",
            "source_locator": "DB_SCHEMA.md#refunds",
        }
    ]
    asset["interfaces"] = [
        {
            "interface_id": "api:refund",
            "path": "/api/refunds",
            "summary": "申请退款",
            "source_id": "api",
            "source_locator": "API_SPEC.md#refunds",
        }
    ]
    api_binding = {
        "artifact_type": "API_OPERATION",
        "artifact_ref": "api:refund",
        "entity_id": "entity:refund",
        "status": "RESOLVED",
        "identity_authorities": ["SOURCE_BACKED_RULE_IMPLEMENTATION"],
        "evidence": [_evidence("api", "API_SPEC.md#refunds", "申请退款")],
    }
    original_clusters = deepcopy(result["clusters"])

    receipts = []
    for _ in range(2):
        local_result = {"clusters": deepcopy(original_clusters)}
        local_bindings = [deepcopy(api_binding)]
        project_source_governed_table_bindings(
            deepcopy(asset),
            local_result,
            rule_authority={},
            mentions=[_table_mention("table:refunds")],
            edges=[],
            bindings=local_bindings,
            bound_artifacts={"api:refund"},
            unknowns=_aggregate_unknown("table:refunds"),
        )
        receipts.append(local_result["source_governed_table_binding"])
        assert local_result["clusters"] == original_clusters
        assert local_bindings[-1]["identity_authorities"] == [API_AUTHORITY]

    assert receipts[0] == receipts[1]

def test_token_overlap_api_binding_is_never_promoted_to_table_authority() -> None:
    asset, result, _mentions, edges, bindings = _base()
    asset["data_tables"] = [
        {
            "table_id": "table:refunds",
            "name": "refunds",
            "description": "退款售后",
            "source_id": "db",
        }
    ]
    asset["interfaces"] = [
        {
            "interface_id": "api:refund",
            "path": "/api/refunds/:id",
            "source_id": "api",
        }
    ]
    bindings.append(
        {
            "artifact_type": "API_OPERATION",
            "artifact_ref": "api:refund",
            "entity_id": "entity:refund",
            "status": "RESOLVED",
            "identity_authorities": ["TOKEN_OVERLAP_RELATION_GATE"],
            "evidence": [_evidence("api", "api#refund", "refund")],
        }
    )

    project_source_governed_table_bindings(
        asset,
        result,
        rule_authority={},
        mentions=[_table_mention("table:refunds")],
        edges=edges,
        bindings=bindings,
        bound_artifacts={"api:refund"},
        unknowns=_aggregate_unknown("table:refunds"),
    )

    assert not any(
        row.get("artifact_ref") == "table:refunds" for row in bindings
    )
    receipt = result["source_governed_table_binding"]
    assert receipt["token_overlap_authority_used"] is False
    assert receipt["name_similarity_authority_used"] is False
