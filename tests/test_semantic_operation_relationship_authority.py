from __future__ import annotations

from ai_test_asset_center.semantic_operation_binding import (
    _accepted_rule_interface_edges,
)


def _edge(**overrides):
    row = {
        "edge_id": "edge:semantic",
        "from": "rule:submit",
        "to": "interface:submit",
        "relation": "rule_to_interface",
        "status": "accepted",
        "derivation": "agent_semantic_mapping",
        "evidence_gate": "behavior_ir_ids_and_runtime_oracle_required",
        "evidence": {
            "rule_source_id": "prd.md",
            "interface_source_id": "openapi.yaml",
            "supporting_fact_refs": ["fact:submit"],
        },
    }
    row.update(overrides)
    return row


def test_source_backed_semantic_edge_is_admitted() -> None:
    rows = _accepted_rule_interface_edges({"relationships": [_edge()]})
    assert len(rows) == 1
    assert rows[0]["rule_ref"] == "rule:submit"
    assert rows[0]["interface_ref"] == "interface:submit"


def test_statusless_semantic_edge_is_not_admitted() -> None:
    edge = _edge()
    edge.pop("status")
    assert _accepted_rule_interface_edges({"relationships": [edge]}) == []


def test_semantic_edge_without_structured_evidence_is_not_admitted() -> None:
    assert _accepted_rule_interface_edges(
        {"relationships": [_edge(evidence={})]}
    ) == []
