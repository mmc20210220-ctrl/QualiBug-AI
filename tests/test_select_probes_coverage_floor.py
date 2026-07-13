from __future__ import annotations

from ai_test_asset_center import risk_based_probe_planner as planner


def _probe(
    probe_id: str,
    *,
    path: str = "/api/orders",
    priority_score: float = 0.7,
    risk_type: str = "business_rule",
    method: str = "GET",
    source: str = "real_project_pattern",
    execution_policy: str = "direct",
) -> dict[str, object]:
    return {
        "probe_id": probe_id,
        "risk_type": risk_type,
        "severity": "P1",
        "method": method,
        "path": path,
        "source": source,
        "execution_policy": execution_policy,
        "priority_score": priority_score,
        "validated_yield_priority_score": 0.0,
    }


def _module_set(selected: list[dict[str, object]]) -> set[str]:
    return {planner._module_of(p) for p in selected}


def test_module_coverage_floor_reaches_low_priority_modules() -> None:
    # 3 high-priority probes in one module, 1 low-priority probe each in two others.
    combined = [
        _probe("A1", path="/api/orders/a1", priority_score=0.95),
        _probe("A2", path="/api/orders/a2", priority_score=0.94),
        _probe("A3", path="/api/orders/a3", priority_score=0.93),
        _probe("B1", path="/api/products/b1", priority_score=0.10),
        _probe("C1", path="/api/inventory/c1", priority_score=0.10),
    ]
    # Old pure-priority behavior with max_count=3 would pick A1,A2,A3 (1 module).
    selected, _ = planner._select_probes_by_budget(
        combined,
        mode="aggressive",
        allow_destructive=True,
        budget={"risk_budget": {"business_rule": 100}},
        max_count=3,
    )
    ids = [p["probe_id"] for p in selected]
    assert "B1" in ids, "coverage floor must pull in an uncovered low-priority module"
    assert "C1" in ids, "coverage floor must pull in an uncovered low-priority module"
    assert _module_set(selected) == {"orders", "products", "inventory"}
    assert len(selected) == 3


def test_no_coverage_floor_change_when_single_module() -> None:
    combined = [
        _probe("A1", path="/api/orders/a1", priority_score=0.95),
        _probe("A2", path="/api/orders/a2", priority_score=0.94),
        _probe("A3", path="/api/orders/a3", priority_score=0.10),
    ]
    selected, _ = planner._select_probes_by_budget(
        combined,
        mode="aggressive",
        allow_destructive=True,
        budget={"risk_budget": {"business_rule": 100}},
        max_count=2,
    )
    assert [p["probe_id"] for p in selected] == ["A1", "A2"]


def test_risk_budget_still_enforced() -> None:
    combined = [
        _probe("A1", path="/api/orders/a1", priority_score=0.9, risk_type="business_rule"),
        _probe("B1", path="/api/products/b1", priority_score=0.9, risk_type="business_rule"),
        _probe("C1", path="/api/inventory/c1", priority_score=0.9, risk_type="business_rule"),
    ]
    selected, skipped = planner._select_probes_by_budget(
        combined,
        mode="aggressive",
        allow_destructive=True,
        budget={"risk_budget": {"business_rule": 1}},
        max_count=10,
    )
    assert len(selected) == 1
    assert sum(1 for s in skipped if s["reason"] == "risk_budget_exceeded") == 2


def test_destructive_blocked_in_safe_mode() -> None:
    combined = [
        _probe("D1", path="/api/orders/d1", priority_score=0.9, risk_type="idempotency", method="POST"),
    ]
    selected, skipped = planner._select_probes_by_budget(
        combined,
        mode="safe",
        allow_destructive=False,
        budget={"risk_budget": {"idempotency": 10}},
        max_count=10,
    )
    assert selected == []
    assert any(s["reason"] == "destructive_blocked_by_mode" for s in skipped)


def test_module_of_extracts_business_resource() -> None:
    assert planner._module_of({"path": "/api/orders/x"}) == "orders"
    assert planner._module_of({"path": "/api/products"}) == "products"
    assert planner._module_of({"path": "/orders"}) == "orders"
    assert planner._module_of({"path": "/"}) == "root"
    assert planner._module_of({}) == "root"
