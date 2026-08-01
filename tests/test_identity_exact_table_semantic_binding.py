from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)


def _span(source: str, quote: str) -> list[dict]:
    return [
        {
            "source_id": source,
            "locator": f"{source}#identity",
            "quote": quote,
            "quote_hash": f"hash-{source}-{quote}",
        }
    ]


def _rule(fact_id: str, entity: str) -> dict:
    statement = f"admin may view {entity}"
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {"entity_refs": [entity], "actor_refs": ["admin"]},
        "object": {"entity_refs": [entity]},
        "action": {"canonical": "view", "raw": "view"},
        "conditions": [],
        "condition_combinator": "",
        "state_effects": [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "scope": {},
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": _span(fact_id, statement),
    }


def _asset(table: dict) -> dict:
    return {
        "asset_id": "identity-exact-table-semantic-test",
        "business_fact_ledger": {"items": [_rule("fact-order", "订单")]},
        "business_objects": [{"object_id": "object:order", "object": "订单"}],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [table],
        "field_dictionary": [],
        "interfaces": [],
        "ui_design_specs": [],
        "events": [],
        "relationships": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
    }


def _unresolved_refs(model: dict) -> set[str]:
    return {
        str(item.get("artifact_ref"))
        for row in model.get("identity_unknowns") or []
        for item in (row.get("details") or {}).get("unresolved_artifacts") or []
        if isinstance(item, dict) and item.get("artifact_ref")
    }


def test_exact_source_declared_table_label_creates_typed_binding_only() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            {
                "table_id": "table:orders",
                "name": "orders",
                "description": "订单",
                "source_id": "db",
                "source_locator": "DB_SCHEMA.md#orders",
            }
        )
    )

    assert [row["name"] for row in model["business_objects"]] == ["订单"]
    binding = next(
        row
        for row in model["identity_bindings"]
        if row.get("artifact_ref") == "table:orders"
    )
    assert binding["artifact_type"] == "DATABASE_TABLE"
    assert binding["relation"] == "IMPLEMENTS_ENTITY"
    assert binding["identity_authorities"] == [
        "SOURCE_DECLARED_TECHNICAL_SEMANTIC_LABEL"
    ]
    assert binding["source_semantic_labels"] == ["订单"]
    assert binding["source_semantic_fields"] == ["description"]
    assert binding["entity_id"] == model["business_objects"][0]["entity_id"]
    assert "table:orders" not in _unresolved_refs(model)
    edge = next(
        row
        for row in model["identity_edges"]
        if row.get("authority") == "SOURCE_DECLARED_TECHNICAL_SEMANTIC_LABEL"
    )
    assert edge["automatic_union_allowed"] is False
    assert edge["evidence_class"] == "EXACT_SOURCE_DECLARED_BUSINESS_LABEL"


def test_table_description_never_uses_prefix_or_suffix_inference() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            {
                "table_id": "table:orders",
                "name": "orders",
                "description": "订单主表",
                "source_id": "db",
            }
        )
    )

    assert not any(
        row.get("artifact_ref") == "table:orders"
        for row in model["identity_bindings"]
    )
    assert "table:orders" in _unresolved_refs(model)
    aggregate = next(
        row
        for row in model["identity_unknowns"]
        if "table:orders"
        in {
            item.get("artifact_ref")
            for item in (row.get("details") or {}).get("unresolved_artifacts") or []
            if isinstance(item, dict)
        }
    )
    assert aggregate["reason_code"] == "CROSS_SOURCE_IDENTITY_UNRESOLVED"
    assert aggregate["details"]["automatic_inference_allowed"] is False


def test_exact_table_label_without_source_identity_remains_unresolved() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            {
                "table_id": "table:orders",
                "name": "orders",
                "description": "订单",
            }
        )
    )

    assert not any(
        row.get("artifact_ref") == "table:orders"
        for row in model["identity_bindings"]
    )
    assert "table:orders" in _unresolved_refs(model)
