from __future__ import annotations

import pytest

from ai_test_asset_center.connector_connection_profiles import (
    ConnectorProfileError,
    _profile_transaction,
)
from ai_test_asset_center.private_pilot_service import (
    KNOWLEDGE_MANAGER_ROLES,
    PrivatePilotHandler,
)


ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def test_profile_transaction_translates_busy_lease_to_governed_error(
    tmp_path,
    monkeypatch,
):
    import ai_test_asset_center.connector_connection_profiles as authority
    from ai_test_asset_center.enterprise_knowledge_center.transaction_lock import (
        KnowledgeTransactionBusy,
    )

    class BusyLease:
        def __enter__(self):
            raise KnowledgeTransactionBusy({"operation": "other_knowledge_mutation"})

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        authority,
        "knowledge_transaction",
        lambda *args, **kwargs: BusyLease(),
    )

    with pytest.raises(
        ConnectorProfileError,
        match="connector_profile_transaction_busy",
    ):
        with _profile_transaction(
            tmp_path,
            "enterprise-project",
            operation="configure_feishu_connector",
            actor=ACTOR,
        ):
            raise AssertionError("busy lease must block before mutation")


def test_private_service_connector_authorization_uses_knowledge_roles():
    handler = object.__new__(PrivatePilotHandler)
    observed = {}

    def require_role(actor, allowed_roles, action):
        observed["actor"] = actor
        observed["roles"] = set(allowed_roles)
        observed["action"] = action
        return True

    handler._require_role = require_role  # type: ignore[method-assign]

    assert handler._require_connector_manager(ACTOR, "knowledge connector operation") is True
    assert observed["roles"] == KNOWLEDGE_MANAGER_ROLES
    assert "security_owner" not in observed["roles"]
    assert "testops_admin" not in observed["roles"]
