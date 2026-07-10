from __future__ import annotations

from ai_test_asset_center.defect_discovery import (
    generate_adaptive_policy_probes,
    generate_feedback_adjusted_policy_probes,
    generate_feedback_learning_probes,
    normalize_synthetic_probe_path,
)


def _ops(*rows: tuple[str, str, str]) -> list[dict]:
    out = []
    for path, method, summary in rows:
        out.append({
            "path": path,
            "method": method,
            "summary": summary,
            "resource": path.strip("/").split("/")[0],
            "risk_hints": [],
        })
    return out


def test_normalize_synthetic_probe_path_strips_mall_ids():
    assert normalize_synthetic_probe_path("/orders/o900") == "/orders/{order_id}"
    assert normalize_synthetic_probe_path("/products/p100") == "/products/{product_id}"
    assert normalize_synthetic_probe_path("/orders/{order_id}") == "/orders/{order_id}"


def test_feedback_learning_binds_to_healthcare_openapi_without_mall_paths():
    model = {
        "operations": _ops(
            ("/api/appointments", "POST", "book appointment capacity"),
            ("/api/appointments/{id}/cancel", "POST", "cancel appointment"),
            ("/api/patients/{patient_id}", "GET", "patient record"),
            ("/api/vouchers/apply", "POST", "apply voucher benefit discount"),
            ("/api/settlements", "POST", "create settlement payment"),
            ("/api/settlements/callback", "POST", "settlement payment callback webhook"),
            ("/api/refunds", "POST", "refund settlement"),
            ("/admin/patients", "GET", "admin patients"),
            ("/api/tenants/records", "GET", "tenant scoped records"),
        )
    }
    probes = generate_feedback_learning_probes(model)
    assert probes
    paths = {p["path"].split("?", 1)[0] for p in probes}
    assert "/api/appointments" in paths
    assert "/api/patients/{patient_id}" in paths
    assert "/api/vouchers/apply" in paths
    assert not any("o900" in str(p.get("path") or "") for p in probes)
    assert not any("/orders" in str(p.get("path") or "") for p in probes)
    assert not any("/cart/" in str(p.get("path") or "") for p in probes)
    assert any(p["predicted_template_id"] == "IDOR_ORDER_ACCESS" and p["path"] == "/api/patients/{patient_id}" for p in probes)
    assert any(p["predicted_template_id"] == "COUPON_THRESHOLD_BYPASS" and p["path"] == "/api/vouchers/apply" for p in probes)


def test_feedback_learning_keeps_mall_binding_when_orders_exist():
    model = {
        "operations": _ops(
            ("/orders", "POST", "create order stock"),
            ("/orders/{order_id}", "GET", "order detail"),
            ("/orders/{order_id}/cancel", "POST", "cancel order"),
            ("/cart/apply-coupon", "POST", "apply coupon discount"),
            ("/payments", "POST", "create payment"),
            ("/payments/callback", "POST", "payment callback"),
            ("/refunds", "POST", "create refund"),
            ("/admin/orders", "GET", "admin orders"),
            ("/tenant/orders", "GET", "tenant orders"),
        )
    }
    probes = generate_feedback_learning_probes(model)
    assert any(p["path"] == "/orders" for p in probes)
    assert any(p["path"] == "/orders/{order_id}" for p in probes)
    assert any(p["path"] == "/cart/apply-coupon" for p in probes)
    assert not any("o900" in str(p.get("path") or "") for p in probes)


def test_feedback_learning_skips_when_no_role_match():
    model = {"operations": _ops(("/health", "GET", "health"))}
    assert generate_feedback_learning_probes(model) == []


def test_adaptive_and_feedback_adjusted_normalize_paths(monkeypatch, tmp_path):
    # adaptive: empty policy -> []
    monkeypatch.setattr(
        "ai_test_asset_center.defect_discovery.build_learned_probe_policy",
        lambda root: {"template_policies": [{
            "template_id": "IDOR_ORDER_ACCESS",
            "priority_score": 0.9,
            "recommended_variants": 1,
            "strategy": "idor",
            "probe_type": "learned_idor_probe",
            "risk_type": "idor",
            "severity": "P0",
            "actor": "normal_user",
            "method": "GET",
            "path": "/orders/o900",
            "expected_status": 403,
            "api_template": "GET /orders/{order_id}",
        }]},
    )
    model = {"operations": _ops(("/orders/{order_id}", "GET", "order detail"))}
    adaptive = generate_adaptive_policy_probes(model)
    assert adaptive
    assert adaptive[0]["path"] == "/orders/{order_id}"
    assert "o900" not in adaptive[0]["path"]

    policy_file = tmp_path / "feedback_adjusted_probe_policy.json"
    policy_file.write_text(
        '{"private_leak_check": {"passed": true}, "template_policies": [{'
        '"template_id": "COUPON_THRESHOLD_BYPASS", "priority_score": 0.9,'
        '"recommended_variants": 1, "title": "benefit", "probe_type": "learned_coupon_probe",'
        '"risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user",'
        '"method": "POST", "path": "/cart/apply-coupon", "expected_status": 400,'
        '"api_template": "POST /cart/apply-coupon", "human_feedback_count": 1}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDBACK_ADJUSTED_POLICY_PATH", str(policy_file))
    adjusted = generate_feedback_adjusted_policy_probes({
        "operations": _ops(("/cart/apply-coupon", "POST", "apply coupon")),
    })
    assert adjusted
    assert adjusted[0]["path"] == "/cart/apply-coupon"
