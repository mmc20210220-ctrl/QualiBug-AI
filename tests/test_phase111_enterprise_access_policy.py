from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_access_policy import (
    AccessPolicyError,
    authenticate_token,
    is_authorized,
    parse_token_policy,
    require_authorized,
)


POLICY = {
    "token-a": {
        "principal_id": "principal-a",
        "tenant_id": "tenant-a",
        "permissions": ["source.register", "campaign.plan"],
        "project_ids": ["project-a"],
    },
    "token-b": {
        "principal_id": "principal-b",
        "tenant_id": "tenant-b",
        "permissions": ["*"],
        "project_ids": ["*"],
    },
}


def test_policy_authenticates_opaque_token_and_enforces_project_scope():
    policy = parse_token_policy(POLICY)
    principal = authenticate_token("token-a", policy)

    assert principal is not None
    assert principal.tenant_id == "tenant-a"
    assert is_authorized(principal, permission="campaign.plan", project_id="project-a") is True
    assert is_authorized(principal, permission="campaign.plan", project_id="project-b") is False
    assert is_authorized(principal, permission="campaign.execute", project_id="project-a") is False


def test_wildcard_permissions_and_projects_are_policy_controlled():
    principal = authenticate_token("token-b", parse_token_policy(POLICY))

    assert principal is not None
    assert is_authorized(principal, permission="campaign.execute", project_id="any-project") is True


def test_invalid_policy_or_missing_principal_fails_closed():
    with pytest.raises(AccessPolicyError, match="access_policy_principal_invalid"):
        parse_token_policy({"token": {"tenant_id": "tenant", "permissions": []}})

    with pytest.raises(AccessPolicyError, match="access_principal_missing"):
        require_authorized(None, permission="campaign.plan", project_id="project-a")
