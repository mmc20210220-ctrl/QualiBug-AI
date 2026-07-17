"""Evaluator match ontology must alias product family short ids."""

from __future__ import annotations

from benchmark_evaluator.benchmark_compute import (
    _canonical_match_family,
    _match_finding_to_gt,
)


def test_product_concurrency_aliases_to_match_ontology_race_family() -> None:
    finding = {
        "title": "[ContractOracle] concurrency_final_invariant: admin POST /api/inventory/reserve",
        "category": "concurrency_final_invariant",
        "description": "control succeeded; treatment violated the typed assertion",
        "actual": {
            "invariant_held": False,
            "before_values": {"available_qty": 5},
            "after_values": {"available_qty": -1},
        },
        "path": "/api/inventory/reserve",
        "reproduction": {"method": "POST", "path": "/api/inventory/reserve"},
    }
    assert _canonical_match_family(finding) == "concurrency_race_condition"

    gt = {
        "bug_id": "SYNTH-CONC-001",
        "title": "concurrent reserve drives available_qty negative",
        "type": "并发/库存",
        "match_keywords": ["reserve", "并发", "超卖", "available_qty", "库存"],
        "trigger": "POST /api/inventory/reserve under concurrent load",
    }
    assert _canonical_match_family(gt) == "concurrency_race_condition"

    matched = _match_finding_to_gt(finding, [gt], set())
    assert matched is not None
    assert matched["bug_id"] == "SYNTH-CONC-001"
    assert float(matched["__match_score"]) >= 0.58


def test_isolation_owner_tenant_visibility_aliases_to_tenant_isolation() -> None:
    """Same-role isolation obligations must not be scored as generic authz."""
    finding = {
        "title": "[ContractOracle] owner_tenant_visibility: buyer GET /api/cart/items?userId=peer-id",
        "category": "owner_tenant_visibility",
        "risk_family": "isolation",
        "description": "control=buyer succeeded; treatment=buyer violated the typed assertion",
        "failed_assertions": [{"assertion_id": "assert_isolation", "kind": "owner_tenant_visibility"}],
        "actual": {
            "owner_can_access": True,
            "viewer_can_access": True,
            "leak_detected": True,
        },
        "path": "/api/cart/items",
        "reproduction": {"method": "GET", "path": "/api/cart/items?userId=peer-id"},
    }
    assert _canonical_match_family(finding) == "tenant_isolation"

    gt = {
        "bug_id": "SYNTH-CART-001",
        "title": "购物车查询传入 userId 查看别人购物车",
        "type": "数据隔离",
        "match_keywords": ["cart/items", "userId", "别人购物车", "越权"],
        "trigger": "buyer01 GET /api/cart/items?userId=buyer02",
    }
    assert _canonical_match_family(gt) == "tenant_isolation"
    matched = _match_finding_to_gt(finding, [gt], set())
    assert matched is not None
    assert matched["bug_id"] == "SYNTH-CART-001"
    assert float(matched["__match_score"]) >= 0.58

    """Role-denied owner_tenant_visibility is authz, not field visibility leak."""
    search_finding = {
        "title": "[ContractOracle] owner_tenant_visibility: seller GET /api/users/admin/search",
        "category": "owner_tenant_visibility",
        "description": "control=admin succeeded; treatment=seller violated the typed assertion",
        "actual": {
            "owner_can_access": True,
            "viewer_can_access": True,
            "leak_detected": True,
        },
        "path": "/api/users/admin/search",
        "reproduction": {"method": "GET", "path": "/api/users/admin/search"},
    }
    assert _canonical_match_family(search_finding) == "authorization_access_control"

    search_gt = {
        "bug_id": "SYNTH-USER-003",
        "title": "普通用户越权访问用户搜索",
        "type": "权限",
        "match_keywords": ["admin/search", "用户搜索", "普通用户", "越权"],
        "trigger": "GET /api/users/admin/search as non-admin",
    }
    assert _canonical_match_family(search_gt) == "authorization_access_control"
    matched_search = _match_finding_to_gt(search_finding, [search_gt], set())
    assert matched_search is not None
    assert matched_search["bug_id"] == "SYNTH-USER-003"

    adjust_finding = {
        "title": "[ContractOracle] owner_tenant_visibility: buyer POST /api/inventory/admin/adjust",
        "category": "owner_tenant_visibility",
        "description": "control=admin succeeded; treatment=buyer violated the typed assertion",
        "actual": {
            "owner_can_access": True,
            "viewer_can_access": True,
            "leak_detected": True,
        },
        "path": "/api/inventory/admin/adjust",
        "reproduction": {"method": "POST", "path": "/api/inventory/admin/adjust"},
    }
    assert _canonical_match_family(adjust_finding) == "authorization_access_control"

    adjust_gt = {
        "bug_id": "SYNTH-INV-005",
        "title": "buyer 越权库存调整",
        "type": "权限",
        "match_keywords": ["admin/adjust", "库存调整", "buyer", "越权"],
        "trigger": "POST /api/inventory/admin/adjust as buyer",
    }
    matched_adjust = _match_finding_to_gt(adjust_finding, [adjust_gt], set())
    assert matched_adjust is not None
    assert matched_adjust["bug_id"] == "SYNTH-INV-005"
