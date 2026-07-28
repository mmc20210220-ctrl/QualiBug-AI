from __future__ import annotations

import pytest

from ai_test_asset_center.private_pilot_scan_context_contract import (
    build_campaign_context_from_scan_body,
)


def test_scan_context_threads_discovery_controls_to_planning() -> None:
    context = build_campaign_context_from_scan_body({
        "base_url": "http://127.0.0.1:8080",
        "environment_type": "test",
        "agent_semantic_linking_enabled": True,
        "runtime_interface_discovery_enabled": True,
        "runtime_interface_discovery_budget": 321,
    })

    assert context["execution_mode"] == "approved_sandbox_write"
    assert context["agent_semantic_linking_enabled"] is True
    assert context["runtime_interface_discovery_enabled"] is True
    assert context["runtime_interface_discovery_budget"] == 321


def test_scan_context_preserves_explicit_disabled_controls() -> None:
    context = build_campaign_context_from_scan_body({
        "agent_semantic_linking_enabled": False,
        "runtime_interface_discovery_enabled": False,
    })

    assert context["agent_semantic_linking_enabled"] is False
    assert context["runtime_interface_discovery_enabled"] is False
    assert "runtime_interface_discovery_budget" not in context


def test_scan_context_threads_explicit_observer_adapters_without_inference() -> None:
    context = build_campaign_context_from_scan_body({
        "base_url": "https://example.test",
        "ui_base_url": "https://example.test",
        "declared_adapters": ["ui_browser", "db_sql", "ui_browser"],
    })

    assert context["declared_adapters"] == ["ui_browser", "db_sql"]


def test_scan_context_preserves_explicit_empty_adapter_declaration() -> None:
    context = build_campaign_context_from_scan_body({"declared_adapters": []})
    assert context["declared_adapters"] == []


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("ui_browser", "declared_adapters_not_list"),
        (None, "declared_adapters_not_list"),
        ([1], "declared_adapter_name_not_string"),
        ([""], "declared_adapter_name_empty"),
        (["x" * 81], "declared_adapter_name_too_long"),
        (["telepathy"], "declared_adapter_unknown:telepathy"),
    ],
)
def test_scan_context_rejects_invalid_adapter_declarations(
    value: object,
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=rf"^{reason}$"):
        build_campaign_context_from_scan_body({"declared_adapters": value})


@pytest.mark.parametrize(
    "key",
    [
        "agent_semantic_linking_enabled",
        "runtime_interface_discovery_enabled",
    ],
)
@pytest.mark.parametrize("value", ["true", 1, 0, None, [], {}])
def test_scan_context_rejects_non_boolean_discovery_controls(
    key: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=rf"^{key}_not_boolean$"):
        build_campaign_context_from_scan_body({key: value})


@pytest.mark.parametrize("value", [True, 0, -1, 5001, "100", None])
def test_scan_context_rejects_invalid_runtime_discovery_budget(
    value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"^runtime_interface_discovery_budget_invalid$",
    ):
        build_campaign_context_from_scan_body({
            "runtime_interface_discovery_budget": value,
        })
