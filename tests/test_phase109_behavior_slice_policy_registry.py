from __future__ import annotations

import pytest

from ai_test_asset_center.policy_registry import ExecutionPolicy, PolicyRegistry


def test_execution_policy_clamps_incremental_slice_controls():
    policy = ExecutionPolicy(
        max_behavior_slices_per_round=999,
        incremental_discovery_round=0,
        incremental_discovery_round_limit=999,
        require_runtime_receipt_for_slice_confirmation=True,
    )

    assert policy.max_behavior_slices_per_round == 15
    assert policy.incremental_discovery_round == 1
    assert policy.incremental_discovery_round_limit == 12
    assert policy.require_runtime_receipt_for_slice_confirmation is True


def test_registry_persists_incremental_slice_policy_defaults(tmp_path):
    path = tmp_path / "policy_registry.json"
    first = PolicyRegistry(path)
    active = first.get_active_strategy().execution

    assert active.max_behavior_slices_per_round == 15
    assert active.incremental_discovery_round == 1
    assert active.incremental_discovery_round_limit == 8
    assert active.require_runtime_receipt_for_slice_confirmation is True

    restored = PolicyRegistry(path)
    restored_execution = restored.get_active_strategy().execution
    assert restored_execution.max_behavior_slices_per_round == 15
    assert restored_execution.incremental_discovery_round == 1
    assert restored_execution.incremental_discovery_round_limit == 8


def test_execution_policy_defaults_to_installed_experiment_candidate_authority() -> None:
    policy = ExecutionPolicy()

    assert policy.mainline_authority == "experiment_candidate"


def test_execution_policy_rejects_unknown_mainline_authority() -> None:
    with pytest.raises(ValueError, match="invalid mainline_authority"):
        ExecutionPolicy(mainline_authority="automatic_fallback")


def test_registry_persists_mainline_authority(tmp_path) -> None:
    path = tmp_path / "policy_registry.json"
    registry = PolicyRegistry(path)
    active = registry.get_active()
    assert active is not None
    active.strategy.execution.mainline_authority = "experiment_candidate"
    registry._save()

    restored = PolicyRegistry(path).get_active_strategy().execution

    assert restored.mainline_authority == "experiment_candidate"
