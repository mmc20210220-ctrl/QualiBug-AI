from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from ai_test_asset_center import connector_auto_sync as auto
from ai_test_asset_center.connector_registry import (
    ConnectorCredentialField,
    ConnectorManifest,
    ConnectorRegistry,
)


PROJECT = "enterprise-project"


class _GenericAdapter:
    def __init__(self, connector_type: str = "alpha", *, oauth: bool = False) -> None:
        auth_modes = ("oauth2",) if oauth else ()
        credential_fields = (
            ConnectorCredentialField(
                name="oauth_access_token",
                field_type="token",
                secret=True,
                auth_modes=("oauth2",),
            ),
            ConnectorCredentialField(
                name="oauth_refresh_token",
                field_type="token",
                secret=True,
                auth_modes=("oauth2",),
            ),
        ) if oauth else ()
        oauth_schema = {
            "type": "oauth2_authorization_code",
            "authorization_endpoint": "https://provider.example.test/authorize",
            "token_endpoint": "https://provider.example.test/token",
            "client_id": "client",
            "redirect_uri": "https://app.example.test/callback",
            "auth_mode": "oauth2",
            "access_token_field": "oauth_access_token",
            "refresh_token_field": "oauth_refresh_token",
            "minimum_scopes": ["read"],
        } if oauth else {}
        self._manifest = ConnectorManifest(
            connector_type=connector_type,
            display_name="Generic connector",
            category="knowledge_base",
            version="1",
            supported_resource_types=("document",),
            sync_modes=("FULL", "INCREMENTAL"),
            auth_modes=auth_modes,
            credential_fields=credential_fields,
            oauth_schema=oauth_schema,
            capability_contract_version="test-v1",
        )
        self.sync_context: dict[str, Any] = {}

    def manifest(self) -> ConnectorManifest:
        return self._manifest

    def test_connection(self, context: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": "AVAILABLE",
            "connector_type": context["connector_type"],
        }

    def discover(self, context: Mapping[str, Any], cursor: str = "") -> dict[str, Any]:
        return {"descriptors": [], "complete": True}

    def classify_resource(self, descriptor: Mapping[str, Any]) -> Any:
        return None

    def materialize(
        self,
        context: Mapping[str, Any],
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {}

    def build_cursor(self, discovery_result: Any) -> str:
        return ""

    def managed_remote_checkpoint(self, context: Mapping[str, Any]) -> str:
        return ""

    def managed_sync(self, context: Mapping[str, Any]) -> dict[str, Any]:
        self.sync_context = dict(context)
        return {
            "status": "COMPLETE",
            "sync_epoch_id": "sync-generic",
            "next_cursor": "generic-cursor",
        }


def _patch_checkpoint_path(monkeypatch, tmp_path: Path, adapter: _GenericAdapter) -> None:
    instance = {
        "connector_instance_id": "alpha-main",
        "connector_type": "alpha",
        "status": "ACTIVE",
        "connection_profile_ref": "connection-profile://alpha-main",
        "resource_scope": "documents",
        "metadata": {},
        "last_committed_cursor_fingerprint": "",
    }
    monkeypatch.setattr(auto._core, "_instance", lambda *args, **kwargs: dict(instance))
    monkeypatch.setattr(
        auto._core,
        "build_default_connector_registry",
        lambda: ConnectorRegistry((adapter,)),
    )
    monkeypatch.setattr(
        auto,
        "recover_pending_connector_lifecycle_checkpoint",
        lambda *args, **kwargs: {"recovery_action": ""},
    )
    monkeypatch.setattr(
        auto,
        "_CORE_RECOVER_MANAGED_CHECKPOINT",
        lambda *args, **kwargs: {"action": "CONSISTENT"},
    )
    monkeypatch.setattr(auto, "load_connector_sync_checkpoint", lambda *a, **k: "")
    monkeypatch.setattr(auto, "validate_connector_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(
        auto,
        "begin_connector_checkpoint_commit",
        lambda *a, **k: {"attempt_id": "checkpoint-generic"},
    )
    monkeypatch.setattr(auto, "stage_connector_checkpoint_result", lambda *a, **k: {})
    monkeypatch.setattr(auto, "commit_connector_sync_checkpoint", lambda *a, **k: {})
    monkeypatch.setattr(auto, "clear_connector_checkpoint_journal", lambda *a, **k: {})

    @contextmanager
    def fence(*args: Any, **kwargs: Any):
        yield {"takeover": False}

    monkeypatch.setattr(auto, "managed_connector_sync_fence", fence)


def test_generic_dispatcher_selects_adapter_by_manifest_type(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = _GenericAdapter()
    _patch_checkpoint_path(monkeypatch, tmp_path, adapter)

    result = auto.run_managed_connector_sync(
        PROJECT,
        "alpha-main",
        root=tmp_path,
    )

    assert result["status"] == "COMPLETE"
    assert result["checkpoint_commit_protocol"] == "RECOVERABLE_TWO_STAGE"
    assert adapter.sync_context["connector_type"] == "alpha"
    assert adapter.sync_context["connector_instance_id"] == "alpha-main"


def test_generic_dispatcher_refreshes_declared_oauth_before_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = _GenericAdapter(oauth=True)
    _patch_checkpoint_path(monkeypatch, tmp_path, adapter)
    observed: dict[str, object] = {}

    def refresh(project, connector, **kwargs):
        observed["call"] = (project, connector, kwargs)
        return {
            "supported": True,
            "attempted": True,
            "refreshed": True,
            "refresh_status": "SUCCEEDED",
            "credential_values_returned": False,
            "source_identity_preserved": True,
            "checkpoint_preserved": True,
        }

    monkeypatch.setattr(auto, "refresh_connector_oauth", refresh)
    result = auto.run_managed_connector_sync(
        PROJECT,
        "alpha-main",
        root=tmp_path,
    )

    assert observed["call"][0:2] == (PROJECT, "alpha-main")
    assert result["oauth_refresh"]["refresh_status"] == "SUCCEEDED"


def test_generic_connection_test_uses_the_same_registry_dispatcher(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = _GenericAdapter()
    instance = {
        "connector_instance_id": "alpha-main",
        "connector_type": "alpha",
        "status": "ACTIVE",
        "connection_profile_ref": "connection-profile://alpha-main",
        "resource_scope": "documents",
        "metadata": {},
    }
    monkeypatch.setattr(auto._core, "_instance", lambda *args, **kwargs: dict(instance))
    monkeypatch.setattr(
        auto._core,
        "build_default_connector_registry",
        lambda: ConnectorRegistry((adapter,)),
    )

    result = auto.test_managed_connector_connection(
        PROJECT,
        "alpha-main",
        root=tmp_path,
    )

    assert result == {"status": "AVAILABLE", "connector_type": "alpha"}


def test_auto_sync_sweep_isolates_connector_failures_and_keeps_policy_per_instance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first = "alpha-main"
    second = "alpha-secondary"
    monkeypatch.setattr(auto, "_project_ids", lambda root: [PROJECT])
    monkeypatch.setattr(
        auto,
        "_profile_index",
        lambda project, root: {
            first: {"connector_instance_id": first},
            second: {"connector_instance_id": second},
        },
    )
    monkeypatch.setattr(
        auto,
        "list_connector_instances",
        lambda *args, **kwargs: {
            "connector_instances": [
                {
                    "connector_instance_id": first,
                    "connector_type": "alpha",
                    "status": "ACTIVE",
                    "metadata": {
                        "sync_interval_seconds": 1800,
                        "sync_retry_base_seconds": 30,
                        "sync_rate_limit_per_minute": 1,
                        "sync_max_resources": 17,
                    },
                },
                {
                    "connector_instance_id": second,
                    "connector_type": "alpha",
                    "status": "ACTIVE",
                    "metadata": {"sync_interval_seconds": 3600},
                },
            ]
        },
    )
    monkeypatch.setattr(
        auto,
        "_policy",
        lambda: {
            "refresh_seconds": 21600,
            "sweep_seconds": 10,
            "initial_delay_seconds": 0,
            "retry_base_seconds": 60,
            "retry_max_seconds": 3600,
        },
    )
    auto._ATTEMPTS.clear()
    calls: list[str] = []

    def runner(project: str, connector: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(connector)
        if connector == first:
            raise RuntimeError("first connector failed")
        return {"status": "COMPLETE", "sync_epoch_id": "sync-secondary"}

    result = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=10_000.0,
        sync_runner=runner,
    )

    assert result["attempted"] == 2
    assert result["failed"] == 1
    assert result["succeeded"] == 1
    assert calls == [first, second]
    assert len(
        auto._ATTEMPTS[(str(tmp_path.resolve()), PROJECT, first)][
            "attempt_timestamps"
        ]
    ) == 1
    assert auto._ATTEMPTS[(str(tmp_path.resolve()), PROJECT, second)][
        "state"
    ] == "healthy"
    blocked = auto.run_connector_auto_sync_sweep(
        tmp_path,
        now=10_010.0,
        sync_runner=runner,
    )
    assert blocked["attempted"] == 0
    assert blocked["skipped"] == 2
    assert calls == [first, second]
    assert auto._ATTEMPTS[(str(tmp_path.resolve()), PROJECT, first)][
        "last_error_category"
    ] == "RETRYING"
    policy = auto._instance_policy(
        {
            "metadata": {
                "sync_interval_seconds": 1800,
                "sync_max_resources": 17,
            }
        }
    )
    assert policy["refresh_seconds"] == 1800
    assert policy["max_resources"] == 17


def test_due_policy_is_connector_type_neutral() -> None:
    now = 100_000.0
    instance = {
        "status": "ACTIVE",
        "connector_type": "alpha",
        "active_sync_epoch_id": "",
        "last_successful_sync_at_utc": auto._utc(now - 3600),
        "last_failed_sync_at_utc": "",
    }

    assert auto._due(instance, {}, now=now, refresh_seconds=1800)
