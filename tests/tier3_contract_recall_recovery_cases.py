"""Tier-3 contract rejection classification, tested against the real linker.

The upstream commit that introduced the ``CANDIDATE_RECALL_MISS`` signal only
shipped a self-contained toy test that exercised no product code. These cases
drive ``enrich_knowledge_asset_with_agent_relationships`` with a fake provider
client and assert the real receipt classification:

* an interface the model invented            -> UNKNOWN_INTERFACE_ID (reject)
* a real interface outside the candidate set -> CANDIDATE_RECALL_MISS (reject,
  diagnostic/recovery signal, never an acceptance path)
* an interface inside the candidate set      -> accepted relationship

The cases target the linker's Tier-3 contract validator directly (same path as
``test_agent_semantic_linker_incremental.py``), because the authority facade
additionally fills zero-score interfaces into candidate windows, which
legitimately widens the candidate set and would mask the miss classification.
"""

from __future__ import annotations

from ai_test_asset_center.agent_semantic_linker import (
    enrich_knowledge_asset_with_agent_relationships,
)


class Tier3FakeClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        return self.response

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset() -> dict:
    interfaces = [
        {
            "interface_id": f"api:GET:/orders/sub{i:02d}",
            "method": "GET",
            "path": f"/orders/sub{i:02d}",
            "summary": f"Order subresource {i}",
            "field_dictionary": ["total"],
            "source_id": "api-src",
        }
        for i in range(15)
    ]
    interfaces.append({
        "interface_id": "api:GET:/coupons",
        "method": "GET",
        "path": "/coupons",
        "summary": "List coupons",
        "field_dictionary": ["code"],
        "source_id": "api-src",
    })
    return {
        "asset_id": "tier3-contract",
        "rule_library": [{
            "rule_id": "rule-order",
            "statement": "The order total must be computed.",
            "kind": "conservation",
            "source_id": "prd-source",
            "semantic_frame": {
                "modality": "REQUIRED",
                "polarity": "positive",
                "condition": "",
                "subject": "order total",
                "behavior": "The order total must be computed.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": interfaces,
        "data_tables": [
            {"table_id": "table:orders", "name": "orders", "columns": ["total"]}
        ],
        "relationships": [],
    }


def _response(interface_id: str) -> dict:
    return {
        "assessments": [{
            "rule_id": "rule-order",
            "disposition": "LINKED",
            "reason": "x",
            "relationships": [{
                "interface_id": interface_id,
                "confidence": 0.9,
                "reason": "link",
                "evidence_refs": ["rule-order", interface_id, "table:orders"],
            }],
        }]
    }


def test_invented_interface_is_rejected_as_unknown_interface_id() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=Tier3FakeClient(_response("api:GET:/nonexistent")),
    )

    assert enriched["relationships"] == []
    assert receipt["rejections"][0]["reason_code"] == "UNKNOWN_INTERFACE_ID"


def test_real_interface_outside_candidate_set_is_candidate_recall_miss() -> None:
    # /coupons exists in the interface catalog but is not in the order rule's
    # candidate shortlist: the contract must reject it while classifying the
    # rejection as a recall miss, not an invented identity.
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=Tier3FakeClient(_response("api:GET:/coupons")),
    )

    assert enriched["relationships"] == []
    assert receipt["rejected_non_candidate_count"] == 1
    assert receipt["rejections"][0]["reason_code"] == "CANDIDATE_RECALL_MISS"


def test_candidate_interface_remains_acceptable() -> None:
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(),
        client=Tier3FakeClient(_response("api:GET:/orders/sub00")),
    )

    assert len(enriched["relationships"]) == 1
    assert enriched["relationships"][0]["to"] == "api:GET:/orders/sub00"
    assert not [
        row for row in receipt.get("rejections", [])
        if row.get("reason_code") in {"CANDIDATE_RECALL_MISS", "UNKNOWN_INTERFACE_ID"}
    ]
