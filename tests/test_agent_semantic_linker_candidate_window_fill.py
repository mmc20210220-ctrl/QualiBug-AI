from __future__ import annotations

import json

from ai_test_asset_center import agent_semantic_linker as impl
from ai_test_asset_center import agent_semantic_linker_authority as authority
from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class CandidateWindowFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.candidate_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        rule_row = packet["business_semantic_model"]["rules_to_assess"][0]
        candidates = rule_row["candidate_interfaces"]
        candidate_ids = [row["interface_id"] for row in candidates]
        self.candidate_ids.append(candidate_ids)
        rule_id = rule_row["rule"]["rule_id"]
        target = "api:POST:/opaque-target"
        if target not in candidate_ids:
            return {
                "assessments": [{
                    "rule_id": rule_id,
                    "disposition": "NO_EXECUTABLE_INTERFACE",
                    "reason": "The decisive interface is not visible in this candidate window.",
                    "relationships": [],
                }]
            }
        return {
            "assessments": [{
                "rule_id": rule_id,
                "disposition": "LINKED",
                "reason": "The opaque target interface is the documented executable surface.",
                "relationships": [{
                    "interface_id": target,
                    "confidence": 0.98,
                    "reason": "The target interface is present in the supplied candidate window.",
                    "evidence_refs": [rule_id, target, "table:payments"],
                }],
            }]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset() -> dict:
    interfaces = []
    for index in range(12):
        if index < 3:
            interfaces.append({
                "interface_id": f"api:POST:/payment-decoy-{index}",
                "operation_id": f"payment_decoy_{index}",
                "method": "POST",
                "path": f"/payment-decoy-{index}",
                "summary": "Payment amount operation",
                "description": "Documented payment amount operation",
                "source_id": "api-source",
                "field_dictionary": ["amount"],
            })
        else:
            interfaces.append({
                "interface_id": (
                    "api:POST:/opaque-target"
                    if index == 3
                    else f"api:POST:/unrelated-{index}"
                ),
                "operation_id": (
                    "opaque_target"
                    if index == 3
                    else f"unrelated_{index}"
                ),
                "method": "POST",
                "path": (
                    "/opaque-target"
                    if index == 3
                    else f"/unrelated-{index}"
                ),
                "summary": (
                    "Execute operation"
                    if index == 3
                    else f"Generic operation {index}"
                ),
                "description": "Generic documented operation",
                "source_id": "api-source-other",
                "field_dictionary": ["unrelated"],
            })

    return {
        "asset_id": "candidate-window-zero-score-regression",
        "rule_library": [{
            "rule_id": "rule-payment-conservation",
            "statement": "The payment amount must be conserved.",
            "kind": "business_rule",
            "source_id": "prd-source",
            "semantic_frame": {
                "condition": "",
                "subject": "payment amount",
                "behavior": "The payment amount must be conserved.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": interfaces,
        "data_tables": [{
            "table_id": "table:payments",
            "name": "payments",
            "columns": ["amount"],
            "source_id": "schema-source",
        }],
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def test_zero_score_interface_is_filled_into_a_bounded_candidate_window() -> None:
    asset = _asset()
    client = CandidateWindowFakeClient()

    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=client,
    )

    assert client.calls == 1
    assert len(client.candidate_ids[0]) == 12
    assert "api:POST:/opaque-target" in client.candidate_ids[0]
    assert receipt["accepted_relationship_count"] == 1
    assert receipt["status"] == "VERIFIED"
    assert receipt["rule_assessments"][0]["candidate_interface_count"] == 12
    assert "window_fill" in receipt["rule_assessments"][0]["recall_channels"]["api:POST:/opaque-target"]
    assert any(
        row.get("relation") == "rule_to_interface"
        and row.get("to") == "api:POST:/opaque-target"
        for row in enriched["relationships"]
    )


def test_original_candidate_recall_dropped_the_zero_score_target_in_this_fixture() -> None:
    asset = _asset()
    lexicon = impl._semantic_lexicon()
    signals = impl._build_asset_signals(asset, lexicon)
    interfaces = {
        str(row["interface_id"]): row
        for row in asset["interfaces"]
    }
    interface_signals = impl._interface_signal_map(interfaces, lexicon)
    ctx = impl._rule_context(asset["rule_library"][0], signals, lexicon)

    ranked, _stats = authority._original_recall_candidate_interfaces(
        ctx,
        interface_signals,
        asset,
    )

    assert len(ranked) == 3
    assert "api:POST:/opaque-target" not in ranked
