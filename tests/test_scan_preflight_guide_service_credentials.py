import os

os.environ.setdefault("QUALIBUG_JWT_SECRET", "test-private-pilot-secret")

from ai_test_asset_center.__main__ import _scan_preflight_guide
from ai_test_asset_center.target_policy import build_target_policy_decision


def _approved_runtime_contract() -> dict:
    decision = build_target_policy_decision(
        requested_base_url="http://127.0.0.1:8011",
        approved_base_url="http://127.0.0.1:8011",
        environment_type="staging",
        environment_ref="staging",
        execution_mode="approved_sandbox_write",
        runtime_status="approved",
    )
    return {
        "status": "approved",
        "approved_base_url": "http://127.0.0.1:8011",
        "requested_base_url": "http://127.0.0.1:8011",
        "environment_type": "staging",
        "environment_ref": "staging",
        "execution_mode": "approved_sandbox_write",
        "target_policy_decision": decision,
    }


def _manifest() -> dict:
    return {"source_id": "openapi-main", "source_hash": "sha256:source"}


def test_target_policy_requires_separate_environment_identity() -> None:
    decision = build_target_policy_decision(
        requested_base_url="http://127.0.0.1:8011",
        approved_base_url="http://127.0.0.1:8011",
        environment_type="staging",
        environment_ref="",
        execution_mode="approved_sandbox_write",
        runtime_status="approved",
    )

    assert decision["status"] == "blocked"
    assert decision["write_allowed"] is False
    assert "ENVIRONMENT_REFERENCE_MISSING" in decision["blocking_codes"]


def test_scan_preflight_guide_blocks_healthy_claim_when_service_credentials_unverified() -> None:
    guide = _scan_preflight_guide(
        context={
            "scope_id": "orders-scope",
            "environment_ref": "staging",
            "environment_type": "staging",
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
            "environment_type": "staging",
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
