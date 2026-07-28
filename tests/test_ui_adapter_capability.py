from __future__ import annotations

from ai_test_asset_center.adapter_capability import (
    missing_declaration_reason,
    resolve_available_adapters,
)


def test_browser_adapter_is_not_inferred_from_runtime_or_installed_tools(tmp_path) -> None:
    adapters = resolve_available_adapters(
        tmp_path,
        "project",
        {
            "status": "approved",
            "approved_base_url": "https://example.test",
            "playwright_available": True,
            "ui_base_url": "https://example.test",
        },
    )
    assert "ui_browser" not in adapters


def test_browser_adapter_requires_explicit_runtime_declaration(tmp_path) -> None:
    adapters = resolve_available_adapters(
        tmp_path,
        "project",
        {
            "status": "approved",
            "approved_base_url": "https://example.test",
            "declared_adapters": ["ui_browser"],
        },
    )
    assert "ui_browser" in adapters
    reason = missing_declaration_reason("ui_browser")
    assert "ui_browser" in reason
    assert "runtime_contract.declared_adapters" in reason
