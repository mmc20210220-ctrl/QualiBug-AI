"""Regressions for automatic continuation round-limit Recall authority."""
from __future__ import annotations


def test_automatic_mode_uses_absolute_safety_ceiling(monkeypatch) -> None:
    from ai_test_asset_center.pipeline_slices import (
        _ABS_MAX_ROUND_LIMIT,
        _behavior_slice_settings,
    )

    monkeypatch.delenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", raising=False)

    settings = _behavior_slice_settings()

    assert settings["round_limit"] == _ABS_MAX_ROUND_LIMIT == 48


def test_explicit_round_limit_remains_hard_operator_override(monkeypatch) -> None:
    from ai_test_asset_center.pipeline_slices import _behavior_slice_settings

    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "3")

    settings = _behavior_slice_settings()

    assert settings["round_limit"] == 3


def test_explicit_round_limit_is_still_absolute_safety_clamped(monkeypatch) -> None:
    from ai_test_asset_center.pipeline_slices import (
        _ABS_MAX_ROUND_LIMIT,
        _behavior_slice_settings,
    )

    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "999")

    settings = _behavior_slice_settings()

    assert settings["round_limit"] == _ABS_MAX_ROUND_LIMIT
