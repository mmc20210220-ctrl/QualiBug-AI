from __future__ import annotations

from ai_test_asset_center.defect_discovery import (
    concrete_path,
    generate_high_value_pattern_probes,
    is_enterprise_bug_factory_demo,
)


def _ops(*paths_methods: tuple[str, str], resource: str | None = None) -> list[dict]:
    rows = []
    for path, method in paths_methods:
        rows.append({
            "path": path,
            "method": method,
            "resource": resource or path.strip("/").split("/")[0],
            "summary": path,
            "risk_hints": [],
        })
    return rows


def test_pattern_probes_use_openapi_paths_not_synthetic_mall_ids():
    model = {
        "operations": _ops(
            ("/orders/{order_id}", "GET"),
            ("/orders/{order_id}/cancel", "POST"),
            ("/orders", "POST"),
            ("/cart/apply-coupon", "POST"),
            ("/admin/orders", "GET"),
            ("/admin/products/{product_id}", "POST"),
            ("/login", "POST"),
        )
    }
    probes = generate_high_value_pattern_probes(model)
    paths = {p["path"] for p in probes}
    assert "/orders/{order_id}" in paths
    assert "/orders/{order_id}/cancel" in paths
    assert "/cart/apply-coupon" in paths
    assert not any("o900" in str(p.get("path") or "") for p in probes)
    assert not any("p100" in str(p.get("path") or "") for p in probes)
    assert any(p["probe_id"] == "PATTERN_ORDER_IDOR_READ" for p in probes)
    assert any(p["probe_id"] == "PATTERN_COUPON_REUSE" for p in probes)


def test_pattern_probes_activate_for_healthcare_without_mall_paths():
    model = {
        "industry": "healthcare",
        "operations": _ops(
            ("/api/patients/{patient_id}", "GET",),
            ("/api/appointments", "POST"),
            ("/api/appointments/{id}/cancel", "POST"),
            ("/api/vouchers/apply", "POST"),
            ("/admin/patients", "GET"),
        )
    }
    # fix resource hints
    model["operations"][0]["resource"] = "patients"
    model["operations"][0]["summary"] = "patient record"
    model["operations"][1]["resource"] = "appointments"
    model["operations"][1]["summary"] = "book appointment capacity"
    model["operations"][2]["summary"] = "cancel appointment"
    model["operations"][3]["summary"] = "apply voucher benefit discount"
    model["operations"][4]["summary"] = "admin patients"

    probes = generate_high_value_pattern_probes(model)
    assert probes
    assert any(p["risk_type"] == "idor" and "/api/patients/{patient_id}" in p["path"] for p in probes)
    assert any(p["risk_type"] == "stock_consistency" and p["path"] == "/api/appointments" for p in probes)
    assert any(p["risk_type"] == "coupon_abuse" and p["path"] == "/api/vouchers/apply" for p in probes)
    assert any("BENEFIT_" in p["probe_id"] for p in probes)
    assert not any("/orders" in p["path"] or "/cart/" in p["path"] for p in probes)
    assert not any(p["probe_id"].startswith("PATTERN_COUPON_") for p in probes)


def test_pattern_probes_empty_without_matching_operations():
    assert generate_high_value_pattern_probes({"operations": _ops(("/health", "GET"))}) == []


def test_concrete_path_preserves_placeholders():
    assert concrete_path("/orders/{order_id}") == "/orders/{order_id}"
    assert concrete_path("/products/{product_id}") == "/products/{product_id}"
    assert "o900" not in concrete_path("/orders/{id}")
    assert "p100" not in concrete_path("/products/{id}")


def test_bug_factory_demo_requires_explicit_benchmark_target():
    mall_ops = _ops(
        ("/orders", "POST"),
        ("/payments", "POST"),
        ("/refunds", "POST"),
        ("/cart/apply-coupon", "POST"),
        ("/admin/orders", "GET"),
    )
    assert is_enterprise_bug_factory_demo({"industry": "ecommerce", "operations": mall_ops}) is False
    assert is_enterprise_bug_factory_demo({
        "industry": "ecommerce",
        "project_id": "benchmark_mall",
        "operations": mall_ops,
    }) is True
    assert is_enterprise_bug_factory_demo({
        "benchmark_factory_demo": True,
        "operations": mall_ops,
    }) is True
    assert is_enterprise_bug_factory_demo({
        "project_id": "benchmark_mall",
        "operations": _ops(("/orders", "POST")),
    }) is False
