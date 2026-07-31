from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_evidence_policy import (
    apply_identity_evidence_policy,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_resolution import (
    resolve_enterprise_identities,
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


def _asset(facts: list[dict], **extra) -> dict:
    asset = {
        "asset_id": "identity-governance-test",
        "business_fact_ledger": {"items": facts},
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [],
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
    asset.update(extra)
    return asset


def test_registry_split_never_reuses_one_entity_id_twice() -> None:
    prior_id = "enterprise_entity:stable-order"
    asset = _asset(
        [_rule("fact-order", "Order"), _rule("fact-line", "OrderLine")],
        enterprise_identity_registry={
            "schema": "qualibug.enterprise-identity-registry.v1",
            "entities": [
                {
                    "entity_id": prior_id,
                    "canonical_label": "Order",
                    "labels": ["Order", "OrderLine"],
                    "aliases": ["OrderLine"],
                    "status": "RESOLVED",
                }
            ],
        },
    )

    model = build_enterprise_understanding_model(asset)

    ids = [row["entity_id"] for row in model["business_objects"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert prior_id not in ids
    assert any(
        row["kind"] == "IDENTITY_REGISTRY_SPLIT_CONFLICT"
        for row in model["identity_conflicts"]
    )
    assert model["gate"]["entry_allowed"] is False
    receipt = asset["enterprise_identity_registry_recompute_receipt"]
    assert receipt["split_conflict_count"] == 1
    assert receipt["silent_split_identity_reuse_allowed"] is False


def test_api_identity_is_typed_binding_not_business_union() -> None:
    asset = _asset(
        [_rule("fact-order", "Order")],
        business_objects=[{"object_id": "object:order", "object": "Order"}],
        interfaces=[
            {
                "interface_id": "interface:get-order",
                "method": "GET",
                "path": "/orders/{id}",
                "operation_id": "getOrder",
                "summary": "Read order",
                "business_object": "Order",
                "source_id": "openapi",
                "source_locator": "GET /orders/{id}",
            }
        ],
    )

    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["Order"]
    binding = next(
        row
        for row in model["identity_bindings"]
        if row.get("artifact_ref") == "interface:get-order"
    )
    assert binding["artifact_type"] == "API_OPERATION"
    assert binding["relation"] == "EXPOSES_ENTITY"
    assert binding["entity_id"] == model["business_objects"][0]["entity_id"]
    assert all(row["name"] != "getOrder" for row in model["business_objects"])


def test_unbound_ui_and_event_stay_partial() -> None:
    asset = _asset(
        [_rule("fact-order", "Order")],
        business_objects=[{"object": "Order"}],
        ui_design_specs=[
            {
                "ui_spec_id": "ui:order-detail",
                "name": "Order detail",
                "source_id": "prototype",
            }
        ],
        events=[
            {
                "event_id": "event:order-created",
                "event_name": "OrderCreated",
                "source_id": "event-catalog",
            }
        ],
    )

    model = build_enterprise_understanding_model(asset)

    unresolved = {
        row.get("details", {}).get("artifact_ref") for row in model["identity_unknowns"]
    }
    assert {"ui:order-detail", "event:order-created"}.issubset(unresolved)
    assert model["identity_gate"]["status"] == "PARTIAL_ENTERPRISE_IDENTITY_BINDING"
    assert model["identity_gate"]["entry_allowed"] is True


def test_long_parenthetical_definition_is_candidate_only() -> None:
    alias_fact = {
        "fact_id": "definition-parenthetical",
        "kind": "TERM_ALIAS",
        "status": "ACCEPTED",
        "canonical_term": "Order",
        "alias": "Order line collection",
        "raw_statement": "Order (Order line collection)",
        "source_spans": _span("definition-parenthetical", "Order (Order line collection)"),
    }
    asset = _asset([alias_fact])

    apply_identity_evidence_policy(asset)
    result = resolve_enterprise_identities(asset)

    assert alias_fact["identity_evidence_class"] == "POSSIBLE_EQUIVALENCE"
    assert alias_fact["formal_identity_union_allowed"] is False
    assert result["edges"][0]["status"] == "CANDIDATE_ONLY"
    assert len(result["clusters"]) == 2


def test_existing_authority_decision_ledger_is_reused() -> None:
    asset = _asset(
        [_rule("fact-order", "Order")],
        cross_document_conflicts=[
            {
                "conflict_id": "conflict:alias",
                "kind": "TERM_ALIAS_IDENTITY_CONFLICT",
                "status": "RESOLVED",
                "facts": [{"fact_id": "alias-a"}, {"fact_id": "alias-b"}],
                "authority_decision": {
                    "decision_id": "decision:1",
                    "action": "SELECT_FACT",
                    "selected_fact_id": "alias-a",
                    "selected_source_ref": {"source_id": "glossary-a"},
                },
            }
        ],
    )

    build_enterprise_understanding_model(asset)

    receipt = asset["enterprise_identity_authority_projection_receipt"]
    assert receipt["authority_ledger_schema"] == "qualibug.operator-authority-decision-ledger.v1"
    assert receipt["new_identity_decision_ledger_created"] is False
    assert receipt["resolved_identity_conflict_count"] == 1
    assert receipt["applied_decisions"][0]["decision_id"] == "decision:1"
