"""P0-D: business concept registry — explicit-evidence concept layer (SPEC §11).

Covers declared-label indexing, alias-aware canonical lookup, ambiguity on
several distinct canonicals, and the never-merge-by-similarity contract.
"""

from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.business_concept_registry import (
    CHINESE_BUSINESS_CONCEPT_REGISTRY_SCHEMA,
    build_business_concept_registry,
    concept_lookup,
)


def _asset() -> dict:
    return {
        "enterprise_understanding_model": {
            "actors": [
                {"actor_id": "business_actor:buyer", "name": "买家", "aliases": ["顾客"]}
            ],
            "business_objects": [
                {"object_id": "business_object:order", "name": "订单", "aliases": ["orders"]}
            ],
        },
        "enterprise_identity_registry": {
            "entities": [
                {
                    "entity_id": "identity_cluster:wh",
                    "entity_type": "actor",
                    "canonical_label": "仓库管理员",
                    "aliases": ["仓管"],
                }
            ]
        },
        "permission_matrix": [
            {"permission_id": "p1", "role": "买家", "resource": "/api/orders"}
        ],
        "data_tables": [
            {"table_id": "table:orders", "name": "orders", "business_label": "订单表"}
        ],
        "field_dictionary": [
            {"field_id": "field:amount", "field": "amount", "description": "订单金额"}
        ],
    }


def test_registry_schema_and_merge_contract() -> None:
    registry = build_business_concept_registry(_asset())
    assert registry["schema"] == CHINESE_BUSINESS_CONCEPT_REGISTRY_SCHEMA
    assert registry["merge_contract"]["similarity_merge_allowed"] is False
    assert registry["merge_contract"]["merge_requires_declared_equivalence"] is True
    assert registry["receipts"][0]["payload"]["similarity_merge_allowed"] is False


def test_lookup_resolves_declared_labels_and_aliases() -> None:
    registry = build_business_concept_registry(_asset())
    # Permission-matrix role resolves the actor concept.
    hit = concept_lookup(registry, "actor", "买家")
    assert hit["status"] == "RESOLVED"
    assert "business_actor:buyer" in hit["canonical"]
    # Understanding-model alias resolves the same concept.
    alias_hit = concept_lookup(registry, "actor", "顾客")
    assert alias_hit["status"] == "RESOLVED"
    assert alias_hit["canonical"] == hit["canonical"]
    # Identity registry alias (declared) resolves.
    wh = concept_lookup(registry, "actor", "仓管")
    assert wh["status"] == "RESOLVED"
    assert "identity_cluster:wh" in wh["canonical"]
    # Object concept with data-table label.
    obj = concept_lookup(registry, "object", "订单")
    assert obj["status"] == "RESOLVED"
    assert "business_object:order" in obj["canonical"]


def test_ambiguous_lookup_when_several_canonicals() -> None:
    registry = build_business_concept_registry(
        {
            "permission_matrix": [
                {"permission_id": "p1", "role": "管理员"},
                {"permission_id": "p2", "role": "管理员"},
            ],
            "enterprise_understanding_model": {
                "actors": [
                    {"actor_id": "business_actor:a1", "name": "管理员"},
                    {"actor_id": "business_actor:a2", "name": "管理员"},
                ],
                "business_objects": [],
            },
        }
    )
    hit = concept_lookup(registry, "actor", "管理员")
    assert hit["status"] == "AMBIGUOUS"
    assert len(hit["candidates"]) == 2


def test_unknown_lookup_never_merges_by_similarity() -> None:
    registry = build_business_concept_registry(_asset())
    hit = concept_lookup(registry, "actor", "仓库")
    assert hit["status"] == "UNKNOWN"
    assert hit["canonical"] == ""
    # Containment near-match is candidate-only and never upgrades status.
    assert "identity_cluster:wh" in hit.get("near_match_candidates", [])
    assert hit["candidates"] == []
