from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.object_graph import (
    build_object_graph,
)


def test_statusless_relation_without_source_authority_is_not_formalized() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "订单",
                    "to_entity": "组织",
                    "relation_type": "belongs_to",
                }
            ]
        },
        [],
        ["订单", "组织"],
    )

    assert relations == []
    assert len(unknowns) == 1
    assert unknowns[0]["reason_code"] == "ENTITY_RELATION_AUTHORITY_UNRESOLVED"
    assert unknowns[0]["details"]["legacy_status_migration_allowed"] is False


def test_statusless_declared_foreign_key_with_source_id_uses_narrow_legacy_migration() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "orders",
                    "to_entity": "customers",
                    "relation_type": "foreign_key",
                    "source_id": "schema.sql",
                    "derivation": "declared_foreign_key",
                }
            ]
        },
        [],
        ["orders", "customers"],
    )

    assert unknowns == []
    assert len(relations) == 1
    assert relations[0]["relation_authority"] == "LEGACY_SOURCE_DECLARED_FOREIGN_KEY"
    assert relations[0]["legacy_status_migrated"] is True


def test_statusless_declared_foreign_key_without_source_id_remains_unresolved() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "orders",
                    "to_entity": "customers",
                    "relation_type": "foreign_key",
                    "derivation": "declared_foreign_key",
                }
            ]
        },
        [],
        ["orders", "customers"],
    )

    assert relations == []
    assert len(unknowns) == 1
    assert unknowns[0]["reason_code"] == "ENTITY_RELATION_AUTHORITY_UNRESOLVED"


def test_unrecognized_explicit_relation_status_is_fail_closed() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "orders",
                    "to_entity": "customers",
                    "relation_type": "foreign_key",
                    "source_id": "schema.sql",
                    "derivation": "declared_foreign_key",
                    "status": "ready",
                }
            ]
        },
        [],
        ["orders", "customers"],
    )

    assert relations == []
    assert len(unknowns) == 1
    assert unknowns[0]["reason_code"] == "ENTITY_RELATION_STATUS_UNRECOGNIZED"


def test_explicit_accepted_relation_without_source_authority_is_not_formalized() -> None:
    relations, unknowns = build_object_graph(
        {
            "entity_relations": [
                {
                    "from_entity": "订单",
                    "to_entity": "组织",
                    "relation_type": "belongs_to",
                    "status": "accepted",
                }
            ]
        },
        [],
        ["订单", "组织"],
    )

    assert relations == []
    assert len(unknowns) == 1
    assert unknowns[0]["reason_code"] == "ENTITY_RELATION_SOURCE_AUTHORITY_MISSING"
