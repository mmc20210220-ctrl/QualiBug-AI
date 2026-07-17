from __future__ import annotations

import json

import pytest

from ai_test_asset_center.adaptive_discovery_planner import plan_obligation_round
from ai_test_asset_center.adaptive_planning_history import (
    AdaptivePlanningHistoryError,
    build_planning_budget_receipt,
    build_planning_history_receipt,
    historical_yield_from_receipt,
    load_prior_planning_history_receipt,
    select_matching_historical_yield,
)


IDENTITY = {
    "policy_id": "policy-a",
    "policy_version": "7",
    "strategy_fingerprint": "a" * 64,
}


def _obligation(oid: str, family: str) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": family,
        "subject_refs": [oid],
        "confidence": 0.8,
        "compile_status": "COMPILED",
    }


def test_configured_budget_is_never_silently_increased() -> None:
    receipt = build_planning_budget_receipt(3)

    assert receipt["configured_budget"] == 3
    assert receipt["effective_budget"] == 3
    assert receipt["consumed_budget"] == 0


def test_cold_start_does_not_fabricate_yield_or_cost() -> None:
    plan = plan_obligation_round(
        [_obligation("obl-a", "authorization")],
        budget=1,
    )

    assert plan["history_status"] == "COLD_START"
    assert plan["formal_yield_status"] == "NOT_MEASURED"
    assert plan["historical_receipt_ids"] == []
    assert plan["stop_condition"] == "in_scope_obligations_scheduled"


def test_matching_receipt_supplies_only_observed_execution_history() -> None:
    receipt = build_planning_history_receipt(
        policy_identity=IDENTITY,
        attempts=[
            {
                "risk_family": "state",
                "stages": [
                    {"stage": "compile", "status": "COMPILED"},
                    {"stage": "execution", "status": "EXECUTED"},
                ],
            },
            {
                "risk_family": "state",
                "stages": [
                    {"stage": "compile", "status": "BLOCKED"},
                ],
            },
        ],
    )

    history = historical_yield_from_receipt(
        receipt,
        expected_policy_identity=IDENTITY,
    )

    assert history["compile:state"] == 0.5
    assert history["exec:state"] == 0.5
    assert not any(key.startswith("formal_yield:") for key in history)
    assert not any(key.startswith("cost:") for key in history)


def test_history_identity_or_fingerprint_mismatch_fails_closed() -> None:
    receipt = build_planning_history_receipt(
        policy_identity=IDENTITY,
        attempts=[],
    )
    tampered = {**receipt, "attempt_count": 99}

    with pytest.raises(
        AdaptivePlanningHistoryError,
        match="planning_history_fingerprint_mismatch",
    ):
        historical_yield_from_receipt(
            tampered,
            expected_policy_identity=IDENTITY,
        )

    with pytest.raises(
        AdaptivePlanningHistoryError,
        match="planning_history_policy_identity_mismatch:policy_id",
    ):
        historical_yield_from_receipt(
            receipt,
            expected_policy_identity={**IDENTITY, "policy_id": "policy-b"},
        )


def test_prior_scan_receipt_is_loaded_for_the_next_run(tmp_path) -> None:
    receipt = build_planning_history_receipt(
        policy_identity=IDENTITY,
        attempts=[],
    )
    path = tmp_path / "platform_outputs" / "project-a" / "scan_result.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"v12": {"adaptive_planning_history_receipt": receipt}}),
        encoding="utf-8",
    )

    assert load_prior_planning_history_receipt(
        tmp_path,
        "project-a",
    ) == receipt


def test_valid_history_for_another_policy_becomes_explicit_cold_start() -> None:
    receipt = build_planning_history_receipt(
        policy_identity=IDENTITY,
        attempts=[],
    )

    history, reason = select_matching_historical_yield(
        receipt,
        expected_policy_identity={**IDENTITY, "policy_version": "8"},
    )

    assert history == {}
    assert reason == "POLICY_IDENTITY_MISMATCH"
