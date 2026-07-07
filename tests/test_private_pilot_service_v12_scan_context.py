from __future__ import annotations

import json
import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "test-private-pilot-secret")

from ai_test_asset_center import enterprise_pilot_runtime as runtime
from ai_test_asset_center import private_pilot_service as service
from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.execution_approvals import verify_execution_approval
from ai_test_asset_center.ssrf_guard import SsrfBlockedError


API_SPEC = '{"openapi":"3.0.0","paths":{"/api/orders":{"get":{"responses":{"200":{"description":"ok"}}}}}}'


def test_prepare_v12_scan_body_loads_registered_source_and_runtime_defaults(tmp_path, monkeypatch):
    manifest = register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    monkeypatch.setenv("QUALIBUG_SCOPE_ID", "checkout-scope")
    monkeypatch.setenv("QUALIBUG_ENVIRONMENT_REF", "staging-env")

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {"base_url": "http://127.0.0.1:8000"},
        local_dev_mode=False,
    )

    assert prepared["api_doc"] == API_SPEC
    assert prepared["source_manifest"]["source_id"] == manifest["source_id"]
    assert prepared["source_manifest"]["source_hash"] == manifest["source_hash"]
    assert prepared["scope_id"] == "checkout-scope"
    assert prepared["environment_ref"] == "staging-env"
    assert prepared["execution_mode"] == "approved_sandbox_write"
    assert prepared["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }
    assert "execution_approval_id" not in prepared


def test_prepare_v12_scan_body_auto_issues_local_runtime_approval_for_non_production(tmp_path):
    manifest = register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "api_doc": API_SPEC,
            "base_url": "http://127.0.0.1:8000",
            "scope_id": "checkout-scope",
            "environment_ref": "staging-env",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    approval_id = prepared.get("execution_approval_id", "")
    binding = service._predicted_campaign_binding("demo", tmp_path, prepared)

    assert approval_id.startswith("eap_")
    verdict = verify_execution_approval(
        "demo",
        approval_id,
        root=tmp_path,
        campaign_id=binding["campaign_id"],
        scope_id=binding["scope_id"],
        environment_ref=binding["environment_ref"],
        source_hash=binding["source_hash"],
        target_base_url=prepared["base_url"],
        execution_mode=prepared["execution_mode"],
    )
    assert verdict["valid"] is True
    assert prepared["execution_mode"] == "approved_sandbox_write"
    assert prepared["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }


def test_prepare_v12_scan_body_promotes_runtime_defaults_after_base_url_is_backfilled(tmp_path):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "test_profile": {
                    "scope_id": "checkout-scope",
                    "environment_ref": "local-benchmark",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    first_pass = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {},
        local_dev_mode=False,
    )
    assert "execution_mode" not in first_pass
    assert "test_data_contract" not in first_pass

    first_pass["base_url"] = "http://127.0.0.1:8080"
    second_pass = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        first_pass,
        local_dev_mode=False,
    )

    assert second_pass["execution_mode"] == "approved_sandbox_write"
    assert second_pass["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }


def test_predicted_campaign_binding_matches_scan_campaign_for_markdown_api(tmp_path):
    api_markdown = """# API

### GET /api/orders

### GET /api/orders/:id
"""
    manifest = register_source_asset("demo", "api-contract", api_markdown, source_type="openapi", root=tmp_path)
    input_dir = tmp_path / "platform_workspace" / "demo" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "PRD.md").write_text("订单必须处于 `PENDING_PAYMENT` 状态。", encoding="utf-8")
    (input_dir / "schema.sql").write_text(
        "CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT CHECK (status IN ('PENDING_PAYMENT','PAID')));",
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "api_doc": api_markdown,
            "base_url": "http://127.0.0.1:8000",
            "scope_id": "checkout-scope",
            "environment_ref": "staging-env",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    binding = service._predicted_campaign_binding("demo", tmp_path, prepared)
    result = scan(
        "demo",
        root=tmp_path,
        prd_text=str(prepared.get("prd") or ""),
        api_doc_text=str(prepared.get("api_doc") or ""),
        base_url=str(prepared.get("base_url") or ""),
        save_report=False,
        campaign_context={
            "scope_id": prepared["scope_id"],
            "environment_ref": prepared["environment_ref"],
            "execution_approval_id": prepared["execution_approval_id"],
            "execution_mode": prepared["execution_mode"],
            "source_manifest": prepared["source_manifest"],
        },
    )

    assert binding["campaign_id"] == result["campaign"]["campaign_id"]


def test_prepare_v12_scan_body_backfills_prd_from_project_input(tmp_path):
    input_dir = tmp_path / "platform_workspace" / "demo" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "PRD.md").write_text("Orders must not be payable after cancellation.", encoding="utf-8")
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {"base_url": "http://127.0.0.1:8000"},
        local_dev_mode=False,
    )

    assert prepared["prd"] == "Orders must not be payable after cancellation."


def test_load_connector_registry_preserves_test_profile(tmp_path):
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "connectors": [{"connector_id": "gateway", "enabled": True, "endpoint_ref": "http://localhost:8080"}],
                "test_profile": {
                    "api_base_url": "http://localhost:8080",
                    "test_credentials": {"buyer": {"email": "buyer01@example.com", "password": "Test@123456"}},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    loaded = runtime.load_connector_registry("demo", tmp_path)

    assert loaded["connectors"][0]["connector_id"] == "gateway"
    assert loaded["test_profile"]["api_base_url"] == "http://localhost:8080"
    assert loaded["test_profile"]["test_credentials"]["buyer"]["email"] == "buyer01@example.com"


def test_prepare_v12_scan_body_uses_connector_registry_runtime_defaults(tmp_path):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "connectors": [{"connector_id": "gateway", "enabled": True, "endpoint_ref": "http://127.0.0.1:8080"}],
                "test_profile": {
                    "api_base_url": "http://127.0.0.1:8080",
                    "scope_id": "benchmark-mall-checkout",
                    "environment_ref": "local-benchmark",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {"base_url": "http://127.0.0.1:8080"},
        local_dev_mode=False,
    )

    assert prepared["scope_id"] == "benchmark-mall-checkout"
    assert prepared["environment_ref"] == "local-benchmark"
    assert prepared["execution_mode"] == "approved_sandbox_write"
    assert prepared["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "benchmark-mall-checkout",
    }


def test_prepare_v12_scan_body_keeps_production_like_targets_read_only(tmp_path):
    register_source_asset("demo", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    prepared = service._prepare_v12_scan_body(
        "demo",
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "checkout-scope",
            "environment_ref": "prod",
        },
        local_dev_mode=False,
    )

    assert prepared["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in prepared


def test_validate_scan_base_url_allows_loopback_for_local_private_service():
    service._validate_scan_base_url("http://127.0.0.1:8080", local_dev_mode=True)


def test_validate_scan_base_url_blocks_loopback_outside_local_private_service():
    try:
        service._validate_scan_base_url("http://127.0.0.1:8080", local_dev_mode=False)
    except SsrfBlockedError:
        return
    raise AssertionError("expected loopback base_url to be blocked outside local private service")
