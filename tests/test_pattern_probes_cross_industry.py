from __future__ import annotations

from ai_test_asset_center.defect_discovery import (
    body_for_probe,
    business_object_for_api,
    concrete_path,
    execute_probe,
    generate_defect_probes,
    generate_high_value_pattern_probes,
    infer_business_model,
    login_accounts,
    predicted_template_for_probe,
)
from ai_test_asset_center.adaptive_probe_optimizer import build_adaptive_probe_plan


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
    assert any(p["risk_type"] == "idor" and p["path"] == "/orders/{order_id}" for p in probes)
    assert any(p["risk_type"] == "coupon_abuse" and p["path"] == "/cart/apply-coupon" for p in probes)


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


def test_runtime_discovery_contains_no_benchmark_answer_probe_factory():
    from pathlib import Path

    source_path = Path(__file__).parents[1] / "ai_test_asset_center" / "defect_discovery.py"
    source = source_path.read_text(encoding="utf-8").lower()

    for forbidden in (
        "generate_benchmark_compatibility_probes",
        "is_enterprise_bug_factory_demo",
        "benchmark_compat",
        "o900",
        "p100",
    ):
        assert forbidden not in source, f"benchmark answer remains in runtime discovery: {forbidden}"

    adaptive_source = (
        Path(__file__).parents[1] / "ai_test_asset_center" / "adaptive_probe_optimizer.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in ("adaptive_template_library", "/orders", "/products", "/payments", "/refunds"):
        assert forbidden not in adaptive_source, f"fixed runtime strategy remains: {forbidden}"


def test_runtime_probe_body_uses_only_documented_openapi_example() -> None:
    openapi = {
        "paths": {
            "/api/work-items": {
                "post": {
                    "summary": "Create work item",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "example": {"subject": "source-declared", "priority": 2},
                            }
                        }
                    },
                }
            }
        }
    }

    model = infer_business_model("", openapi, {"accounts": []})
    operation = model["operations"][0]
    item = {
        "probe_id": "generic",
        "risk_type": "validation",
        "path": operation["path"],
        "request_example": operation["request_example"],
    }

    assert body_for_probe(item) == {"subject": "source-declared", "priority": 2}
    assert body_for_probe({**item, "request_example": {}}) == {}


def test_generated_write_probe_carries_its_source_request_contract() -> None:
    openapi = {
        "paths": {
            "/api/reservations": {
                "post": {
                    "summary": "Create reservation with capacity limit",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"},
                                "example": {"slot_ref": "documented-slot", "count": 1},
                            }
                        }
                    },
                }
            }
        }
    }
    model = infer_business_model("", openapi, {"accounts": [{"role": "operator"}]})

    probes = generate_defect_probes([], model)
    bound = [item for item in probes if item["path"] == "/api/reservations"]

    assert bound
    assert all(item["request_example"] == {"slot_ref": "documented-slot", "count": 1} for item in bound)
    assert all(item["request_contract_source"] == "openapi_documented_example" for item in bound)


def test_legacy_discovery_write_is_blocked_before_http_execution() -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("legacy write must not reach HTTP transport")

    client = RecordingClient()
    result = execute_probe(
        client,
        {},
        {
            "probe_id": "source_bound_write",
            "source": "generic_auto",
            "actor": "anonymous",
            "method": "POST",
            "path": "/api/work-items",
            "risk_type": "validation",
            "expected_status": 400,
            "expected": "reject invalid input",
            "bug_signal": "unexpected acceptance",
            "request_example": {"subject": "documented"},
        },
    )

    assert client.calls == []
    assert result["execution_status"] == "blocked"
    assert result["reason_code"] == "BLOCKED_UNGOVERNED_LEGACY_WRITE"
    assert result["assertion_result"] == "blocked"


def test_predicted_template_identity_is_derived_from_source_risk_and_route() -> None:
    template = predicted_template_for_probe({
        "probe_id": "probe_1",
        "risk_type": "tenant_isolation",
        "method": "GET",
        "path": "/api/lab-samples/{sample_ref}",
    })

    assert template.startswith("SOURCE_TENANT_ISOLATION_GET_")
    assert "LAB_SAMPLES" in template
    assert "ORDER" not in template


def test_business_object_is_derived_from_arbitrary_route_shape() -> None:
    assert business_object_for_api("GET /api/v3/lab-samples/{sample_ref}") == "lab-samples"
    assert business_object_for_api("POST /service/work-items") == "work-items"


def test_legacy_account_loader_never_posts_login_requests() -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("account loading must not issue an ungoverned POST")

    client = RecordingClient()
    tokens = login_accounts(client, {
        "accounts": [
            {"role": "operator", "username": "declared", "access_token": "token-from-source"},
            {"role": "reviewer", "username": "needs-login", "password": "not-used"},
        ]
    })

    assert client.calls == []
    assert tokens == {"operator": "token-from-source", "declared": "token-from-source"}


def test_adaptive_probe_plan_preserves_finding_identity_without_template_routes() -> None:
    plan = build_adaptive_probe_plan([
        {
            "finding_id": "finding-1",
            "risk_type": "tenant_isolation",
            "severity": "P1",
            "confidence_score": 0.8,
            "method": "GET",
            "path": "/api/lab-samples/{sample_ref}",
        }
    ])

    assert len(plan) == 1
    assert plan[0]["path"] == "/api/lab-samples/{sample_ref}"
    assert plan[0]["risk_type"] == "tenant_isolation"
    assert plan[0]["finding_id"] == "finding-1"
