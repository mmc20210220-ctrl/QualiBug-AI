from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.api_contract_discovery_adapter import generate_api_contract_probes
from ai_test_asset_center.bug_family_coverage_report import build_bug_family_coverage_report
from ai_test_asset_center.compatibility_discovery_adapter import collect_compatibility_issues, generate_compatibility_probes
from ai_test_asset_center.frontend_runtime_discovery_adapter import generate_frontend_runtime_probes
from ai_test_asset_center.frontend_ux_discovery_adapter import generate_frontend_ux_probes
from ai_test_asset_center.full_spectrum_capability_matrix import build_full_spectrum_capability_matrix
from ai_test_asset_center.performance_stability_discovery_adapter import (
    collect_performance_stability_issues,
    generate_performance_stability_probes,
)
from ai_test_asset_center.privacy_compliance_discovery_adapter import (
    collect_privacy_compliance_issues,
    generate_privacy_compliance_probes,
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
    probes.extend(generate_privacy_compliance_probes(openapi, cfg, "demo"))

    families = {probe["defect_family"] for probe in probes}
    assert {"api_contract", "ui", "uiux", "compatibility", "performance", "privacy_compliance"}.issubset(families)


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


def test_full_spectrum_coverage_tracks_declared_vs_materialized_sources() -> None:
    openapi = {
        "openapi": "3.0.0",
        "paths": {
            "/api/orders": {"get": {"responses": {"200": {"description": "ok"}}}},
        },
    }
    cfg = {"request_timeout_seconds": 5, "deployment_mode": "private_deployment", "environment_class": "sandbox"}
    probes = []
    probes.extend(generate_api_contract_probes(openapi, cfg, "demo"))
    probes.extend(generate_frontend_runtime_probes(openapi, cfg, "demo"))

    matrix = build_full_spectrum_capability_matrix(
        probes,
        onboarding={
            "checks": [
                {"name": "base_url_reachable", "ok": True},
                {"name": "safety_boundary", "ok": True},
                {"name": "openapi_parse", "ok": True},
                {"name": "openapi_paths", "count": 1},
            ]
        },
        browser_ui_health={"enabled": False, "reason_code": "E_BROWSER_DISABLED", "reason": "browser disabled", "action": "enable browser"},
    )
    coverage = build_bug_family_coverage_report([], [], capability_rows=list(matrix["rows"]))
    rows = {row["family_id"]: row for row in coverage["rows"]}

    api_row = rows["api_contract"]
    assert api_row["declared_source_count"] >= 2
    assert api_row["materialized_source_count"] >= 1
    assert "phase104_api_contract" in api_row["missing_declared_sources"]

    ui_row = rows["ui"]
    assert ui_row["declared_source_count"] >= 3
    assert ui_row["materialized_source_count"] >= 1
    assert "frontend_runtime" in ui_row["materialized_sources"]
    assert ui_row["missing_source_count"] >= 1
    assert coverage["declared_source_count"] >= coverage["materialized_source_count"]


def test_privacy_compliance_adapter_surfaces_materialized_sources(tmp_path: Path) -> None:
    openapi = {
        "openapi": "3.0.0",
        "paths": {
            "/api/admin/users/export": {
                "get": {
                    "summary": "export users",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "email": {"type": "string"},
                                            "phone": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    control = {
        "security_audit_report": {
            "audit_events": [],
            "risk_operations": [{"kind": "write_setup", "count": 1}],
            "audit_chain_integrity": {"passed": False, "event_index": 0},
            "credential_policy": {"plaintext_credentials_persisted": False},
        }
    }
    probes = generate_privacy_compliance_probes(openapi, {}, "demo", tmp_path, enterprise_testops_control_plane=control)
    issues = collect_privacy_compliance_issues(openapi, project_id="demo", root=tmp_path, enterprise_testops_control_plane=control)
    matrix = build_full_spectrum_capability_matrix(
        probes,
        onboarding={
            "checks": [
                {"name": "base_url_reachable", "ok": True},
                {"name": "safety_boundary", "ok": True},
                {"name": "openapi_parse", "ok": True},
                {"name": "openapi_paths", "count": 1},
            ]
        },
        enterprise_testops_preflight=control,
    )
    coverage = build_bug_family_coverage_report(probes, issues, capability_rows=list(matrix["rows"]))
    privacy_row = next(row for row in coverage["rows"] if row["family_id"] == "privacy_compliance")

    assert any(probe["source"] == "audit_privacy_probe" for probe in probes)
    assert any(issue["defect_family"] == "privacy_compliance" for issue in issues)
    assert privacy_row["materialized_source_count"] >= 2
    assert "audit_privacy_probe" in privacy_row["materialized_sources"]
    assert coverage["covered_family_count"] >= 1
