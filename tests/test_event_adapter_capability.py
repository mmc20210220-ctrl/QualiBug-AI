from __future__ import annotations

from ai_test_asset_center.adapter_capability import (
    missing_declaration_reason,
    resolve_available_adapters,
)


def test_event_observer_is_not_inferred_from_event_shaped_urls(tmp_path) -> None:
    adapters = resolve_available_adapters(
        tmp_path,
        "project",
        {
            "status": "approved",
            "approved_base_url": "https://example.test/events",
            "event_formal_contracts": [{
                "observer_path": "/test-observers/events",
            }],
        },
    )

    assert "event_observer_http" not in adapters


def test_event_observer_requires_explicit_runtime_declaration(tmp_path) -> None:
    adapters = resolve_available_adapters(
        tmp_path,
        "project",
        {
            "status": "approved",
            "approved_base_url": "https://example.test",
            "declared_adapters": ["event_observer_http"],
        },
    )

    assert "event_observer_http" in adapters
    reason = missing_declaration_reason("event_observer_http")
    assert "event_observer_http" in reason
    assert "runtime_contract.declared_adapters" in reason
