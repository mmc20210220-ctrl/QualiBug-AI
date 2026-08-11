from __future__ import annotations


def _obligation() -> dict:
    return {
        "obligation_id": "obl-1",
        "required_operations": ["stale-operation-id"],
        "source_refs": [
            {"kind": "api_operation", "locator": "POST /api/orders"}
        ],
    }


def test_exact_locator_returns_all_duplicate_operation_ids() -> None:
    from ai_test_asset_center.experiment_compiler_base import (
        _locator_operation_candidates,
    )

    candidates, locators = _locator_operation_candidates(
        _obligation(),
        {
            "op-a": {"id": "op-a", "method": "POST", "path": "/api/orders"},
            "op-b": {"id": "op-b", "method": "POST", "path": "/api/orders"},
        },
    )

    assert candidates == ["op-a", "op-b"]
    assert locators == ["POST /api/orders"]


def test_duplicate_exact_locator_blocks_before_semantic_compiler_runs() -> None:
    from ai_test_asset_center.experiment_compiler_base import (
        _compile_one_obligation_in_batch,
    )

    obligation = _obligation()
    compiled: list[dict] = []
    blocked: list[dict] = []
    abstract: list[dict] = []

    def _must_not_run(*args, **kwargs):
        raise AssertionError("ambiguous operation must not reach semantic compiler")

    _compile_one_obligation_in_batch(
        obligation,
        operations={
            "op-a": {"id": "op-a", "method": "POST", "path": "/api/orders"},
            "op-b": {"id": "op-b", "method": "POST", "path": "/api/orders"},
        },
        behavior_ir={"operations": []},
        environment_type="test",
        policy_version="test",
        compiler=_must_not_run,
        available_adapters=None,
        compiled=compiled,
        blocked=blocked,
        abstract=abstract,
    )

    assert compiled == []
    assert abstract == []
    assert len(blocked) == 1
    assert obligation["compile_status"] == "BLOCKED"
    assert obligation["block_reason"] == "BLOCKED_MISSING_OPERATION"
    receipt = blocked[0]["operation_identity_ambiguity_receipt"]
    assert receipt["candidate_operation_ids"] == ["op-a", "op-b"]
    assert receipt["source_order_selection_allowed"] is False


def test_one_exact_locator_candidate_is_unique_recovery_authority() -> None:
    from ai_test_asset_center.experiment_compiler_base import (
        _locator_operation_candidates,
    )

    candidates, _ = _locator_operation_candidates(
        _obligation(),
        {
            "op-a": {"id": "op-a", "method": "POST", "path": "/api/orders"},
            "op-read": {"id": "op-read", "method": "GET", "path": "/api/orders"},
        },
    )

    assert candidates == ["op-a"]
