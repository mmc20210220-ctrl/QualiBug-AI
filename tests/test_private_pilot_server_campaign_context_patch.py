from __future__ import annotations

from pathlib import Path


def test_scan_body_builds_campaign_context_from_frontend_contract() -> None:
    from ai_test_asset_center.private_pilot_server import _build_campaign_context_from_scan_body

    context = _build_campaign_context_from_scan_body(
        {
            "source_manifest": {
                "source_id": "openapi-main",
                "source_hash": "a" * 64,
                "source_version_id": "srcv_1",
                "source_origin": "registered_source_registry",
            },
            "base_url": "http://127.0.0.1:8000",
            "scope_id": "checkout-scope",
            "environment_ref": "staging-env",
            "execution_approval_id": "approval-001",
            "execution_mode": "safe_read_only",
            "test_data_contract": {"strategy": "blocked_with_testability_gap"},
            "release_policy": {"block_on_p0": True},
        }
    )

    assert context["source_manifest"]["source_id"] == "openapi-main"
    assert context["source_manifest"]["source_hash"] == "a" * 64
    assert context["base_url"] == "http://127.0.0.1:8000"
    assert context["scope_id"] == "checkout-scope"
    assert context["environment_ref"] == "staging-env"
    assert context["execution_approval_id"] == "approval-001"
    assert context["execution_mode"] == "safe_read_only"
    assert context["test_data_contract"] == {"strategy": "blocked_with_testability_gap"}
    assert context["release_policy"] == {"block_on_p0": True}


def test_scan_body_prefers_registered_source_content_over_connector_fallback(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.private_pilot_server import _build_campaign_context_from_scan_body, _prepare_scan_body_for_campaign

    content = "openapi: 3.0.0\npaths:\n  /api/orders:\n    get:\n      responses:\n        '200': {description: ok}\n"
    manifest = register_source_asset(
        "demo",
        "openapi-main",
        content,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "tester", "role": "qa_lead"},
    )

    prepared = _prepare_scan_body_for_campaign(
        "demo",
        tmp_path,
        {
            "source_manifest": {
                "source_id": manifest["source_id"],
                "source_hash": manifest["source_hash"],
                "source_version_id": manifest["source_version_id"],
            },
            "base_url": "http://127.0.0.1:8000",
            "scope_id": "checkout-scope",
            "environment_ref": "staging-env",
        },
    )
    context = _build_campaign_context_from_scan_body(prepared)

    assert prepared["api_doc"] == content
    assert prepared["source_manifest"]["source_id"] == "openapi-main"
    assert context["source_manifest"]["source_hash"] == manifest["source_hash"]
    assert context["base_url"] == "http://127.0.0.1:8000"
    assert context["scope_id"] == "checkout-scope"
    assert context["environment_ref"] == "staging-env"


def test_scan_body_can_auto_select_latest_registered_source_when_frontend_omits_manifest(tmp_path: Path) -> None:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset
    from ai_test_asset_center.private_pilot_server import _build_campaign_context_from_scan_body, _prepare_scan_body_for_campaign

    content = "GET /api/products\n"
    manifest = register_source_asset(
        "demo",
        "api-doc",
        content,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "tester", "role": "qa_lead"},
    )

    prepared = _prepare_scan_body_for_campaign(
        "demo",
        tmp_path,
        {
            "scope_id": "catalog-scope",
            "environment_ref": "qa-env",
            "test_data_strategy": "blocked_with_testability_gap",
        },
    )
    context = _build_campaign_context_from_scan_body(prepared)

    assert prepared["api_doc"] == content
    assert context["source_manifest"]["source_id"] == manifest["source_id"]
    assert context["source_manifest"]["source_hash"] == manifest["source_hash"]
    assert context["scope_id"] == "catalog-scope"
    assert context["environment_ref"] == "qa-env"
    assert context["test_data_contract"] == {"strategy": "blocked_with_testability_gap"}


def test_install_patch_replaces_continuous_loop_and_reports_status() -> None:
    from ai_test_asset_center import private_pilot_service as service
    from ai_test_asset_center.private_pilot_server import (
        customer_delivery_gate_patch_status,
        install_customer_delivery_gate_patch,
        restore_customer_delivery_gate_patch,
    )

    restore_customer_delivery_gate_patch()
    original_loop = service._continuous_scan_loop
    original_start = service.PrivatePilotHandler._handle_continuous_start

    install_customer_delivery_gate_patch()
    status = customer_delivery_gate_patch_status()

    assert service._continuous_scan_loop is not original_loop
    assert service.PrivatePilotHandler._handle_continuous_start is not original_start
    assert status["scan_campaign_context_patched"] is True
    assert status["continuous_scan_context_patched"] is True

    restore_customer_delivery_gate_patch()
    assert service._continuous_scan_loop is original_loop
    assert service.PrivatePilotHandler._handle_continuous_start is original_start


def test_service_credentials_are_masked_for_frontend() -> None:
    from ai_test_asset_center.private_pilot_credentials_patch import MASKED_SECRET, mask_service_credentials_for_frontend

    services = [
        {
            "name": "order-service",
            "base_url": "https://orders.internal",
            "admin_pass": "legacy-admin-secret",
            "auth": {
                "type": "password_login",
                "admin": {"username": "admin", "password": "admin-secret"},
                "qa": {"username": "qa", "password": "qa-secret"},
                "bearer_token": "bearer-secret",
                "api_key": "api-secret",
            },
            "db": {"host": "db.internal", "user": "orders", "password": "db-secret"},
        }
    ]

    masked = mask_service_credentials_for_frontend("demo", services)
    rendered = repr(masked)

    assert "legacy-admin-secret" not in rendered
    assert "admin-secret" not in rendered
    assert "qa-secret" not in rendered
    assert "bearer-secret" not in rendered
    assert "api-secret" not in rendered
    assert "db-secret" not in rendered
    assert masked[0]["admin_pass"] == MASKED_SECRET
    assert masked[0]["auth"]["admin"]["password"] == MASKED_SECRET
    assert masked[0]["auth"]["bearer_token"] == MASKED_SECRET
    assert masked[0]["db"]["password"] == MASKED_SECRET
    assert masked[0]["auth"]["admin"]["password_ref"].startswith("qualibug://credentials/demo/order-service/")


def test_local_credential_key_is_created_when_missing(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.private_pilot_credentials_patch import CREDENTIAL_KEY_ENV, ensure_local_credential_encryption_key

    monkeypatch.delenv(CREDENTIAL_KEY_ENV, raising=False)
    source = ensure_local_credential_encryption_key(tmp_path)
    key_path = tmp_path / "platform_workspace" / ".secrets" / "credential_encryption.key"

    assert source == "local_private_key_file"
    assert key_path.exists()
    assert key_path.read_text(encoding="utf-8").strip()


def test_credential_patch_module_owns_handler_installation() -> None:
    module_text = Path("ai_test_asset_center/private_pilot_credentials_patch.py").read_text(encoding="utf-8")

    assert "def install_service_credentials_patch" in module_text
    assert "def restore_service_credentials_patch" in module_text
    assert "_handle_get_service_credentials_masked" in module_text
    assert "_handle_save_service_credentials_secure" in module_text
