from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_evidence_policy import (
    apply_identity_evidence_policy,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_resolution import (
    resolve_enterprise_identities,
)


def _rule(fact_id: str, source_id: str, entity: str, *, system: str = "") -> dict:
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
        "state_effects": [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "scope": {"system": system},
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": [
            {
                "source_id": source_id,
                "locator": f"{source_id}#business",
                "quote": statement,
                "quote_hash": f"hash-{fact_id}",
            }
        ],
    }


def _asset(facts: list[dict]) -> dict:
    return {
        "asset_id": "identity-scope-test",
        "business_fact_ledger": {"items": facts},
        "business_objects": [],
        "data_tables": [],
    }


def test_same_label_in_different_sources_without_scope_is_candidate_only() -> None:
    asset = _asset(
        [
            _rule("fact-erp", "erp-prd", "Task"),
            _rule("fact-mes", "mes-prd", "Task"),
        ]
    )
    apply_identity_evidence_policy(asset)

    result = resolve_enterprise_identities(asset)

    assert len(result["clusters"]) == 2
    cross_source_edges = [
        row
        for row in result["edges"]
        if row.get("evidence_class") == "EXACT_LABEL_SAME_SCOPE"
        and row.get("reason_code") == "EXACT_LABEL_SCOPE_MISSING"
    ]
    assert cross_source_edges
    assert all(row["automatic_union_allowed"] is False for row in cross_source_edges)


def test_same_label_with_explicit_same_system_scope_can_union() -> None:
    asset = _asset(
        [
            _rule("fact-a", "prd-a", "Task", system="MES"),
            _rule("fact-b", "prd-b", "Task", system="MES"),
        ]
    )
    apply_identity_evidence_policy(asset)

    result = resolve_enterprise_identities(asset)

    assert len(result["clusters"]) == 1
    accepted = [
        row
        for row in result["edges"]
        if row.get("evidence_class") == "EXACT_LABEL_SAME_SCOPE"
        and row.get("status") == "ACCEPTED"
    ]
    assert accepted
    assert all(row["automatic_union_allowed"] is True for row in accepted)
