from __future__ import annotations

import json

from ai_test_asset_center import agent_semantic_linker as impl
from ai_test_asset_center import agent_semantic_linker_authority as authority
from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class ZeroOverlapFactFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_fact_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        row = packet["business_semantic_model"]["rules_to_assess"][0]
        fact_ids = [fact["fact_id"] for fact in row["supporting_facts"]]
        self.seen_fact_ids.append(fact_ids)
        if "table:target" not in fact_ids:
            return {
                "assessments": [{
                    "rule_id": row["rule"]["rule_id"],
                    "disposition": "NO_EXECUTABLE_INTERFACE",
                    "reason": "The decisive structured fact is not in this evidence window.",
                    "relationships": [],
                }]
            }
        return {
            "assessments": [{
                "rule_id": row["rule"]["rule_id"],
                "disposition": "LINKED",
                "reason": "The opaque structured fact is the decisive evidence for the executable payment operation.",
                "relationships": [{
                    "interface_id": "api:POST:/payments/submit",
                    "confidence": 0.98,
                    "reason": "The decisive fact is present in the supplied evidence window.",
                    "evidence_refs": [row["rule"]["rule_id"], "api:POST:/payments/submit", "table:target"],
                }],
            }]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset(fact_count: int) -> dict:
    facts = []
    for index in range(fact_count):
        if index == fact_count - 1:
            facts.append({
                "table_id": "table:target",
                "name": "settlement_ledger",
                "columns": ["available_balance"],
                "source_id": "schema-source",
            })
        else:
            facts.append({
                "table_id": f"table:decoy:{index:02d}",
                "name": f"payment_request_{index:02d}",
                "columns": ["payment_id"],
                "source_id": "schema-source",
            })
    return {
        "asset_id": "supporting-fact-zero-overlap-regression",
        "rule_library": [{
            "rule_id": "rule-payment-idempotency",
            "statement": "The payment submission must be idempotent.",
            "kind": "business_rule",
            "source_id": "prd-source",
            "semantic_frame": {
                "condition": "",
                "subject": "payment submission",
                "behavior": "The payment submission must be idempotent.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/payments/submit",
            "operation_id": "submit_payment",
            "method": "POST",
            "path": "/payments/submit",
            "summary": "Submit payment",
            "description": "Submit a payment",
            "source_id": "api-source",
            "field_dictionary": ["payment_id"],
        }],
        "data_tables": facts,
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def _original_supporting_fact_ids(asset: dict) -> list[str]:
    facts = impl._all_fact_rows(asset)
    rule = asset["rule_library"][0]
    ctx = impl._rule_context(
        rule,
        impl._build_asset_signals(asset, impl._semantic_lexicon()),
        impl._semantic_lexicon(),
    )
    return [row["fact_id"] for row in authority._original_recall_supporting_facts(ctx, facts)]


def test_zero_overlap_decisive_fact_was_unrecoverably_dropped_by_original_recall() -> None:
    asset = _asset(20)

    original_ids = _original_supporting_fact_ids(asset)

    assert "table:target" not in original_ids
    assert len(original_ids) == 19
    assert impl._fact_recall_score(
        next(row for row in impl._all_fact_rows(asset) if row["fact_id"] == "table:target"),
        impl._rule_context(
            asset["rule_library"][0],
            impl._build_asset_signals(asset, impl._semantic_lexicon()),
            impl._semantic_lexicon(),
        ),
    ) == 0


def test_zero_overlap_decisive_fact_is_kept_inside_the_20_fact_window() -> None:
    client = ZeroOverlapFactFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(20),
        client=client,
    )

    assert client.calls == 1
    assert len(client.seen_fact_ids[0]) == 20
    assert client.seen_fact_ids[0][-1] == "table:target"
    assert receipt["accepted_relationship_count"] == 1
    assert receipt["status"] == "VERIFIED"
    assert any(
        row.get("relation") == "rule_to_interface"
        and row.get("to") == "api:POST:/payments/submit"
        for row in enriched["relationships"]
    )


def test_zero_overlap_decisive_fact_is_recovered_in_a_later_paged_window() -> None:
    client = ZeroOverlapFactFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(21),
        client=client,
    )

    assert client.calls == 2
    assert len(client.seen_fact_ids[0]) == 20
    assert "table:target" not in client.seen_fact_ids[0]
    assert client.seen_fact_ids[1] == ["table:target"]
    assert receipt["supporting_fact_paging"]["window_fact_counts"] == [20, 1]
    assert receipt["supporting_fact_paging"]["zero_score_fact_fill_enabled"] is True
    assert receipt["accepted_relationship_count"] == 1
    assert receipt["status"] == "VERIFIED"
    assert any(
        row.get("relation") == "rule_to_interface"
        and row.get("to") == "api:POST:/payments/submit"
        for row in enriched["relationships"]
    )
