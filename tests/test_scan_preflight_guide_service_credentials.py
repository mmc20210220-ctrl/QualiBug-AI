import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "test-private-pilot-secret")

from ai_test_asset_center.__main__ import _scan_preflight_guide


def _approved_runtime_contract() -> dict:
    return {"status": "approved"}


def _manifest() -> dict:
    return {"source_id": "openapi-main", "source_hash": "sha256:source"}


def test_scan_preflight_guide_blocks_healthy_claim_when_service_credentials_unverified() -> None:
    guide = _scan_preflight_guide(
        context={
            "scope_id": "orders-scope",
            "environment_ref": "staging",
            "test_data_contract": {"strategy": "reuse_verified_existing"},
            "services": [
                {
                    "name": "orders",
                    "auth": {"bearer_token_configured": True},
                    "db": {"host": "127.0.0.1", "name": "orders", "password_configured": True},
                }
            ],
            "execution_approval_id": "eap_123",
        },
        base_url="http://127.0.0.1:8011",
        manifest=_manifest(),
        runtime_contract=_approved_runtime_contract(),
        test_data_plan={"status": "ready", "missing_requirements": []},
        runtime_observed=True,
    )

    check = next(item for item in guide["checks"] if item["key"] == "service_credentials")

    assert check["status"] == "configured_unverified"
    assert "orders:auth" in check["detail"]
    assert "orders:db" in check["detail"]
    assert "service_credentials" in guide["missing"]
    assert guide["healthy_claim_allowed"] is False


def test_scan_preflight_guide_allows_healthy_claim_when_service_credentials_verified() -> None:
    guide = _scan_preflight_guide(
        context={
            "scope_id": "orders-scope",
            "environment_ref": "staging",
            "test_data_contract": {"strategy": "reuse_verified_existing"},
            "services": [
                {
                    "name": "orders",
                    "auth": {"bearer_token_configured": True},
                    "auth_check": {"all_ok": True},
                    "db": {"host": "127.0.0.1", "name": "orders", "password_configured": True},
                    "db_check": {"ok": True},
                }
            ],
            "execution_approval_id": "eap_123",
        },
        base_url="http://127.0.0.1:8011",
        manifest=_manifest(),
        runtime_contract=_approved_runtime_contract(),
        test_data_plan={"status": "ready", "missing_requirements": []},
        runtime_observed=True,
    )

    check = next(item for item in guide["checks"] if item["key"] == "service_credentials")

    assert check["status"] == "ready"
    assert "service_credentials" not in guide["missing"]
    assert guide["healthy_claim_allowed"] is True
