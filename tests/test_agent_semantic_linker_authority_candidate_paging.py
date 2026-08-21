from __future__ import annotations

import json

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)


class CandidatePagingFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.rule_calls = 0
        self.candidate_batch_sizes: list[int] = []
        self.seen_interface_ids: list[list[str]] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls += 1
        prompt = str(kwargs["user_prompt"])
        packet = json.loads(
            prompt.split("INPUT:\n", 1)[1].split("\n\nFINAL CONTRACT CHECK", 1)[0]
        )
        assert packet["assessment_mode"] == "rule_to_interface"
        self.rule_calls += 1
        row = packet["business_semantic_model"]["rules_to_assess"][0]
        candidates = row["candidate_interfaces"]
        ids = [candidate["interface_id"] for candidate in candidates]
        self.candidate_batch_sizes.append(len(ids))
        self.seen_interface_ids.append(ids)
        target = "api:GET:/target"
        if target not in ids:
            return {
                "assessments": [{
                    "rule_id": row["rule"]["rule_id"],
                    "disposition": "NO_EXECUTABLE_INTERFACE",
                    "reason": "No candidate in this source window is the target operation.",
                    "relationships": [],
                }]
            }
        return {
            "assessments": [{
                "rule_id": row["rule"]["rule_id"],
                "disposition": "LINKED",
                "reason": "The target operation is the source-backed interface for this rule.",
                "relationships": [{
                    "interface_id": target,
                    "confidence": 0.98,
                    "reason": "The target operation matches the documented rule.",
                    "evidence_refs": [row["rule"]["rule_id"], target],
                }],
            }]
        }

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": float(self.calls), "total_tokens": 0.0}


def _asset(interface_count: int) -> dict:
    interfaces = []
    for index in range(interface_count):
        if index == interface_count - 1:
            path = "/target"
            interface_id = "api:GET:/target"
        else:
            path = f"/decoy/{index:02d}"
            interface_id = f"api:GET:{path}"
        interfaces.append({
            "interface_id": interface_id,
            "operation_id": f"op_{index:02d}",
            "method": "GET",
            "path": path,
            "summary": "Read resource",
            "description": "Documented read operation",
            "source_id": "source-1",
            "field_dictionary": ["id", "status"],
        })
    return {
        "asset_id": "candidate-paging-regression",
        "rule_library": [{
            "rule_id": "rule-001",
            "statement": "The target resource must be readable through the documented operation.",
            "kind": "business_rule",
            "source_id": "source-1",
            "semantic_frame": {
                "condition": "",
                "subject": "target resource",
                "behavior": "The target resource must be readable through the documented operation.",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": interfaces,
        "data_tables": [],
        "field_dictionary": [],
        "roles": [],
        "permission_matrix": [],
        "entity_relations": [],
        "state_machines": [],
        "relationships": [],
    }


def test_interface_recall_pages_past_the_12_candidate_window() -> None:
    client = CandidatePagingFakeClient()
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(13),
        client=client,
    )

    assert receipt["candidate_paging"] == {
        "enabled": True,
        "window_size": 12,
        "window_count": 2,
        "source_interface_count": 13,
        "window_interface_counts": [12, 1],
        "candidate_budget_skipped_count": 0,
        "candidate_window_fill_enabled": True,
        "reason_code": "SOURCE_INTERFACES_PAGED_INSTEAD_OF_TOP_CANDIDATE_TRUNCATION",
    }
    assert client.rule_calls == 2
    assert client.candidate_batch_sizes == [12, 1]
    assert "api:GET:/target" not in client.seen_interface_ids[0]
    assert "api:GET:/target" in client.seen_interface_ids[1]
    assert receipt["accepted_relationship_count"] == 1
    assert any(
        row.get("relation") == "rule_to_interface"
        and row.get("to") == "api:GET:/target"
        for row in enriched["relationships"]
    )
    assert receipt["status"] == "VERIFIED"


def test_interface_recall_below_the_window_keeps_original_single_request_path() -> None:
    client = CandidatePagingFakeClient()
    _, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset(12),
        client=client,
    )

    assert "candidate_paging" not in receipt
    assert client.rule_calls == 1
    assert client.candidate_batch_sizes == [12]
