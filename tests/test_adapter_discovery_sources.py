from __future__ import annotations

from ai_test_asset_center.api_contract_discovery_adapter import generate_api_contract_probes
from ai_test_asset_center.bug_family_coverage_report import build_bug_family_coverage_report
from ai_test_asset_center.compatibility_discovery_adapter import collect_compatibility_issues, generate_compatibility_probes
from ai_test_asset_center.frontend_runtime_discovery_adapter import generate_frontend_runtime_probes
from ai_test_asset_center.frontend_ux_discovery_adapter import generate_frontend_ux_probes
from ai_test_asset_center.performance_stability_discovery_adapter import (
    collect_performance_stability_issues,
    generate_performance_stability_probes,
)


def test_discovery_adapters_generate_full_spectrum_probe_sources() -> None:
    openapi = {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/api/orders/{id}": {"get": {"responses": {"200": {"description": "ok"}}}},
        },
    }
    cfg = {"request_timeout_seconds": 5, "deployment_mode": "private_deployment", "environment_class": "sandbox"}

    probes = []
    probes.extend(generate_api_contract_probes(openapi, cfg, "demo"))
    probes.extend(generate_frontend_runtime_probes(openapi, cfg, "demo"))
    probes.extend(generate_frontend_ux_probes(openapi, cfg, "demo"))
    probes.extend(generate_compatibility_probes(openapi, cfg, "demo"))
    probes.extend(generate_performance_stability_probes(openapi, cfg, "demo"))

    families = {probe["defect_family"] for probe in probes}
    assert {"api_contract", "ui", "uiux", "compatibility", "performance"}.issubset(families)


def test_performance_and_compatibility_adapters_surface_actionable_issues() -> None:
    executions = [
        {"response_status": 500, "error": "timeout waiting upstream"},
        {"response_status": 503, "error": "timeout waiting upstream"},
        {"response_status": 200, "error": None},
    ]
    perf_issues = collect_performance_stability_issues(executions, request_timeout_seconds=5)
    compat_issues = collect_compatibility_issues(
        {"environment_class": "prod_like", "deployment_mode": "public_saas"},
        openapi={"openapi": "3.1.0"},
    )

    assert any(issue["defect_family"] == "stability" for issue in perf_issues)
    assert any(issue["defect_family"] == "compatibility" for issue in compat_issues)

    coverage = build_bug_family_coverage_report([], perf_issues + compat_issues)
    assert coverage["covered_family_count"] >= 2


def test_bug_family_coverage_report_marks_browser_blocked_families() -> None:
    probes = [
        {"probe_id": "P1", "defect_family": "ui", "source": "browser_ui_replay", "capability_gate": "browser_ui_unavailable"},
        {"probe_id": "P2", "defect_family": "uiux", "source": "frontend_ux_adapter", "capability_gate": "browser_ui_unavailable"},
    ]
    coverage = build_bug_family_coverage_report(
        probes,
        [],
        health_context={
            "browser_ui_health": {
                "reason_code": "E_BROWSER_CACHE_MISSING",
                "reason": "browser cache not found",
                "action": "configure playwright_browsers_path",
            }
        },
    )
    rows = {row["family_id"]: row for row in coverage["rows"]}
    assert rows["ui"]["blocked_probe_count"] >= 1
    assert rows["ui"]["coverage_gap_reason_code"] == "E_BROWSER_CACHE_MISSING"
    assert rows["uiux"]["coverage_gap_reason_code"] == "E_BROWSER_CACHE_MISSING"
    assert coverage["missing_family_reasons"]["ui"]["reason_code"] == "E_BROWSER_CACHE_MISSING"
