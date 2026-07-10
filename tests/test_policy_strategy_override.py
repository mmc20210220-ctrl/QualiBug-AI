from __future__ import annotations

import pytest

from ai_test_asset_center.policy_registry import StrategyBundle
from ai_test_asset_center.policy_wiring import (
    get_policy_dict,
    get_policy_value,
    policy_strategy_override,
)


def test_policy_strategy_override_is_scoped_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = StrategyBundle()
    baseline.execution.cleanup_retry_count = 1
    challenger = StrategyBundle()
    challenger.execution.cleanup_retry_count = 3
    monkeypatch.setattr(
        "ai_test_asset_center.policy_registry.get_active_policy",
        lambda: baseline,
    )

    assert get_policy_value("execution", "cleanup_retry_count", 0) == 1
    with policy_strategy_override(challenger):
        assert get_policy_value("execution", "cleanup_retry_count", 0) == 3
        assert get_policy_dict("execution")["cleanup_retry_count"] == 3
    assert get_policy_value("execution", "cleanup_retry_count", 0) == 1


def test_policy_strategy_override_restores_after_scan_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = StrategyBundle()
    challenger = StrategyBundle()
    challenger.execution.cleanup_retry_count = 3
    monkeypatch.setattr(
        "ai_test_asset_center.policy_registry.get_active_policy",
        lambda: baseline,
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        with policy_strategy_override(challenger):
            raise RuntimeError("scan failed")

    assert get_policy_value("execution", "cleanup_retry_count", -1) == 1


def test_policy_strategy_override_rejects_non_strategy() -> None:
    with pytest.raises(TypeError, match="requires StrategyBundle"):
        with policy_strategy_override({"execution": {"cleanup_retry_count": 3}}):
            pass
