from __future__ import annotations

from ai_test_asset_center import enterprise_pilot_runtime
from ai_test_asset_center import scan_impl_prepare


def test_preflight_uses_project_credential_catalog_when_registry_profile_is_empty(
    monkeypatch, tmp_path
):
    calls: list[tuple[str, str]] = []

    def load_credentials(project: str, root):
        calls.append((project, str(root)))
        return [
            {
                "profile": "buyer",
                "email": "buyer@example.com",
                "password": "visible-only-in-process",
            }
        ]

    monkeypatch.setattr(
        enterprise_pilot_runtime,
        "load_project_test_credentials",
        load_credentials,
    )
    config: dict[str, object] = {}

    source = scan_impl_prepare._bind_preflight_test_credentials(
        "enterprise-project", tmp_path, config
    )

    assert source == "project_test_credential_catalog"
    assert calls == [("enterprise-project", str(tmp_path))]
    assert config["test_credentials"] == [
        {
            "profile": "buyer",
            "email": "buyer@example.com",
            "password": "visible-only-in-process",
        }
    ]


def test_preflight_keeps_explicit_registry_credentials_as_authority(
    monkeypatch, tmp_path
):
    def unexpected_catalog_read(*_args, **_kwargs):
        raise AssertionError("project catalog must not override explicit profile")

    monkeypatch.setattr(
        enterprise_pilot_runtime,
        "load_project_test_credentials",
        unexpected_catalog_read,
    )
    config: dict[str, object] = {
        "test_credentials": {
            "buyer": {
                "email": "buyer@example.com",
                "password": "configured-in-registry",
            }
        }
    }

    source = scan_impl_prepare._bind_preflight_test_credentials(
        "enterprise-project", tmp_path, config
    )

    assert source == "connector_registry.test_profile"
    assert config["test_credentials"]["buyer"]["email"] == "buyer@example.com"
