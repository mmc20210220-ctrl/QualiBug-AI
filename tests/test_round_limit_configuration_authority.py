"""Recall fixes must not replace configured round limits with safety ceilings."""
from __future__ import annotations


def test_policy_round_limit_remains_default_when_env_is_absent(monkeypatch) -> None:
    import ai_test_asset_center.pipeline_slices as slices
    import ai_test_asset_center.policy_wiring as policy

    monkeypatch.delenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", raising=False)

    def fake_policy(section, key, default=None):
        if key == "max_behavior_slices_per_round":
            return 15
        if key == "incremental_discovery_round":
            return 1
        if key == "incremental_discovery_round_limit":
            return 16
        return default

    monkeypatch.setattr(policy, "get_policy_value", fake_policy)
    settings = slices._behavior_slice_settings()

    assert settings["round_limit"] == 16
    assert settings["round_limit"] != slices._ABS_MAX_ROUND_LIMIT


def test_explicit_operator_round_limit_still_wins(monkeypatch) -> None:
    import ai_test_asset_center.pipeline_slices as slices
    import ai_test_asset_center.policy_wiring as policy

    monkeypatch.setattr(
        policy,
        "get_policy_value",
        lambda section, key, default=None: 16
        if key == "incremental_discovery_round_limit"
        else default,
    )
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "5")

    assert slices._behavior_slice_settings()["round_limit"] == 5


def test_safety_ceiling_only_clamps_explicit_or_scaled_value(monkeypatch) -> None:
    import ai_test_asset_center.pipeline_slices as slices
    import ai_test_asset_center.policy_wiring as policy

    monkeypatch.setattr(
        policy,
        "get_policy_value",
        lambda section, key, default=None: 16
        if key == "incremental_discovery_round_limit"
        else default,
    )
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "999")

    assert slices._behavior_slice_settings()["round_limit"] == slices._ABS_MAX_ROUND_LIMIT
