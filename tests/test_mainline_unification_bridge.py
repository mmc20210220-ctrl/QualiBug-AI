"""Tests for hypothesis → behavior-slice bridge (mainline unification)."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.hypothesis_slice_bridge import hypotheses_to_slices


def test_hypotheses_to_slices_binds_real_endpoint_and_drops_unbound():
    api_endpoints = [
        {"entity": "order", "action": "create", "path": "/api/v1/resources", "method": "POST"},
        {"entity": "order", "action": "list", "path": "/api/v1/resources", "method": "GET"},
        {"entity": "payment", "action": "pay", "path": "/api/v1/payments", "method": "POST"},
    ]
    hypotheses = [
        {
            "hypothesis_id": "ana_1",
            "title": "资源创建缺少幂等保护",
            "category": "idempotency",
            "severity": "P1",
            "entity": "order",
            "related_endpoints": ["/api/v1/resources"],
            "expected_behavior": "重复提交不得产生多条资源",
            "_reasoner_engine": "business_rules",
        },
        {
            "hypothesis_id": "ana_unbound",
            "title": "无法绑定的假设",
            "category": "invariant",
            "severity": "P2",
            "entity": "nonexistent_entity_xyz",
            "description": "没有任何可匹配的路径或实体",
        },
    ]

    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=api_endpoints, origin="analyzer")

    assert funnel["input"] == 2
    assert funnel["bound"] == 1
    assert funnel["dropped_no_endpoint"] == 1
    assert funnel["by_origin"]["analyzer"]["bound"] == 1
    assert len(slices) == 1

    slice_row = slices[0]
    assert slice_row["endpoints"] == ["/api/v1/resources"]
    assert slice_row["endpoints"][0].startswith("/")
    assert slice_row["source_refs"], "source_refs must be non-empty for source grounding"
    assert slice_row["slice_id"].startswith("BHV_")
    assert slice_row["kind"] in {"invariant", "permission", "isolation", "concurrency", "money"}
    # Oracle field must map to a real oracle class name
    oracle_fields = [k for k in slice_row if k.endswith("_oracle")]
    assert oracle_fields, "slice must carry an oracle binding"
    assert any(
        slice_row[k] in {
            "IdempotencyOracle", "ConsistencyOracle", "PermissionOracle",
            "TenantIsolationOracle", "ConcurrencyOracle", "MoneyOracle",
            "InventoryOracle", "StateOracle", "WorkflowOracle",
            "CacheConsistencyOracle", "TransactionOracle", "AuditOracle",
            "PrivacyOracle",
        }
        for k in oracle_fields
    )


def test_hypotheses_to_slices_empty_catalog_drops_all():
    hypotheses = [
        {
            "hypothesis_id": "x1",
            "title": "有路径但目录为空",
            "related_endpoints": ["/api/v1/resources"],
            "category": "permission",
        }
    ]
    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=[], origin="llm_reasoner")
    assert slices == []
    assert funnel["bound"] == 0
    assert funnel["dropped_no_endpoint"] == 1
    assert funnel["origin"] == "llm_reasoner"
