from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_test_asset_center.execution_approvals import issue_execution_approval, verify_execution_approval


def _expiry(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_execution_approval_binds_origin_source_scope_and_environment(tmp_path):
    approval = issue_execution_approval(
        "enterprise-project",
        root=tmp_path,
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="test-a",
        source_hash="a" * 64,
        target_base_url="https://test.example.invalid/service",
        execution_mode="safe_read_only",
        expires_at_utc=_expiry(),
        actor={"name": "release-manager", "role": "approver"},
    )
    valid = verify_execution_approval(
        "enterprise-project",
        approval["approval_id"],
        root=tmp_path,
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="test-a",
        source_hash="a" * 64,
        target_base_url="https://test.example.invalid/other-path",
        execution_mode="safe_read_only",
    )
    wrong_origin = verify_execution_approval(
        "enterprise-project",
        approval["approval_id"],
        root=tmp_path,
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="test-a",
        source_hash="a" * 64,
        target_base_url="https://test.example.invalid.evil/",
        execution_mode="safe_read_only",
    )

    assert valid["valid"] is True
    assert wrong_origin == {"valid": False, "code": "EXECUTION_APPROVAL_TARGET_ORIGIN_MISMATCH"}


def test_expired_execution_approval_is_rejected(tmp_path):
    try:
        issue_execution_approval(
            "enterprise-project",
            root=tmp_path,
            campaign_id="CMP_1",
            scope_id="scope-a",
            environment_ref="test-a",
            source_hash="a" * 64,
            target_base_url="https://test.example.invalid",
            execution_mode="safe_read_only",
            expires_at_utc=_expiry(-1),
            actor={"name": "release-manager", "role": "approver"},
        )
    except Exception as exc:
        assert str(exc) == "execution_approval_already_expired"
    else:
        raise AssertionError("expected expired approval rejection")
