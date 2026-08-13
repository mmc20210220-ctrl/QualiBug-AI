"""Depth preservation in the hypothesis → source-candidate bridge.

The reasoner emits cross-entity cascade chains, lifecycle source states and
multi-step verification intent. The single-operation obligation model cannot
express all of it, but the bridge must preserve it observably instead of
silently dropping it (the measured comprehension-loss root cause).
"""
from __future__ import annotations

from ai_test_asset_center.hypothesis_slice_bridge import (
    hypotheses_to_source_candidates,
)


def _endpoints() -> list[dict]:
    return [
        {
            "operation_id": "createResource",
            "entity": "resource",
            "action": "create",
            "path": "/resources",
            "method": "POST",
        },
        {
            "operation_id": "getResource",
            "entity": "resource",
            "action": "read",
            "path": "/resources",
            "method": "GET",
        },
    ]


def test_bridge_preserves_depth_fields_on_candidate() -> None:
    hypotheses = [{
        "hypothesis_id": "hypothesis-deep",
        "title": "Overdue order cascade blocks downstream settlement",
        "category": "causality",
        "method": "POST",
        "related_endpoints": ["/resources"],
        "source_refs": [{"source_id": "SRC-1", "quote": "order lifecycle"}],
        "cascade_chain": [
            {"from": "order", "to": "settlement", "effect": "blocks"},
            {"from": "settlement", "to": "ledger", "effect": "delays"},
        ],
        "source_state": "OVERDUE",
        "target_entity": "settlement",
        "cascade_check": "settlement must not proceed for an overdue order",
        "verification_method": {
            "method": "POST",
            "path": "/resources",
            "step1": "POST /resources",
            "step2": "GET /resources/{id} and assert state",
        },
    }]

    candidates, funnel = hypotheses_to_source_candidates(
        hypotheses,
        api_endpoints=_endpoints(),
        origin="mainline_reasoner",
    )

    assert funnel["bound"] == 1
    assert funnel["depth_preserved"] == 1
    depth = candidates[0]["depth"]
    assert depth["cascade_chain"] == hypotheses[0]["cascade_chain"]
    assert depth["source_state"] == "OVERDUE"
    assert depth["target_entity"] == "settlement"
    assert depth["cascade_check"] == "settlement must not proceed for an overdue order"
    assert "step1" in depth["verification_steps"]
    assert "step2" in depth["verification_steps"]


def test_bridge_omits_depth_key_when_no_depth_fields() -> None:
    hypotheses = [{
        "hypothesis_id": "hypothesis-flat",
        "title": "Repeated create may duplicate",
        "category": "idempotency",
        "method": "POST",
        "related_endpoints": ["/resources"],
        "source_refs": [{"source_id": "SRC-1"}],
    }]

    candidates, funnel = hypotheses_to_source_candidates(
        hypotheses,
        api_endpoints=_endpoints(),
        origin="mainline_reasoner",
    )

    assert funnel["bound"] == 1
    assert funnel["depth_preserved"] == 0
    assert "depth" not in candidates[0]
