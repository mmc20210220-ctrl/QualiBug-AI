from __future__ import annotations

from types import SimpleNamespace

from ai_test_asset_center.__main__ import _build_cli_campaign_context


def _args(**overrides):
    defaults = {
        "base_url": "http://127.0.0.1:8080",
        "scope_id": "checkout-scope",
        "environment_ref": "customer-preprod",
        "environment_type": "",
        "execution_mode": "",
        "test_data_strategy": "",
        "source_id": "api-contract",
        "source_hash": "a" * 64,
        "source_version_id": "srcv_1",
        "execution_approval_id": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_cli_defaults_explicit_nonproduction_target_to_governed_write() -> None:
    context = _build_cli_campaign_context(
        _args(environment_type="preprod")
    )

    assert context["execution_mode"] == "approved_sandbox_write"
    assert context["environment_type"] == "preprod"
    assert context["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout-scope",
    }


def test_cli_keeps_unknown_environment_read_only_fail_closed() -> None:
    context = _build_cli_campaign_context(_args(environment_type=""))

    assert context["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in context


def test_cli_preserves_operator_safe_read_only_kill_switch() -> None:
    context = _build_cli_campaign_context(
        _args(environment_type="staging", execution_mode="safe_read_only")
    )

    assert context["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in context
