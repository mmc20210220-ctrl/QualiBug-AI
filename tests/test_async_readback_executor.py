from __future__ import annotations

import pytest

from ai_test_asset_center.async_readback_executor import (
    READBACK_ASYNC_POLICY_INVALID,
    READBACK_ASYNC_TIMEOUT,
    execute_async_readback,
    normalize_async_policy,
)


def _clock():
    current = {"value": 0.0}

    def monotonic() -> float:
        return current["value"]

    def sleep(seconds: float) -> None:
        current["value"] += seconds

    return monotonic, sleep


def test_disabled_policy_preserves_one_immediate_attempt() -> None:
    calls = {"count": 0}

    def read_once() -> dict:
        calls["count"] += 1
        return {"status": 200, "body": {"state": "pending"}}

    monotonic, sleep = _clock()
    receipt = execute_async_readback(
        read_once=read_once,
        accept=lambda response: response["body"]["state"] == "ready",
        async_policy={"enabled": False},
        sleep=sleep,
        monotonic=monotonic,
    )

    assert calls["count"] == 1
    assert receipt["attempt_count"] == 1
    assert receipt["converged"] is False
    assert receipt["timed_out"] is False
    assert receipt["reason_code"] == ""


def test_enabled_policy_polls_until_source_predicate_converges() -> None:
    states = iter(["pending", "pending", "ready"])
    monotonic, sleep = _clock()

    receipt = execute_async_readback(
        read_once=lambda: {
            "status": 200,
            "body": {"state": next(states)},
        },
        accept=lambda response: response["body"]["state"] == "ready",
        async_policy={
            "enabled": True,
            "poll_interval_ms": 100,
            "expected_max_delay_ms": 500,
            "max_attempts": 4,
            "terminal_condition": "state_equals_ready",
        },
        sleep=sleep,
        monotonic=monotonic,
    )

    assert receipt["converged"] is True
    assert receipt["attempt_count"] == 3
    assert receipt["elapsed_ms"] == 200
    assert receipt["reason_code"] == ""


def test_stability_requirement_prevents_single_transient_success() -> None:
    states = iter(["ready", "pending", "ready", "ready"])
    monotonic, sleep = _clock()

    receipt = execute_async_readback(
        read_once=lambda: {
            "status": 200,
            "body": {"state": next(states)},
        },
        accept=lambda response: response["body"]["state"] == "ready",
        async_policy={
            "enabled": True,
            "poll_interval_ms": 50,
            "expected_max_delay_ms": 500,
            "max_attempts": 4,
            "required_stable_observations": 2,
        },
        sleep=sleep,
        monotonic=monotonic,
    )

    assert receipt["converged"] is True
    assert receipt["attempt_count"] == 4
    assert receipt["attempts"][-1]["stable_observation_count"] == 2


def test_timeout_is_named_and_retains_all_attempts() -> None:
    monotonic, sleep = _clock()

    receipt = execute_async_readback(
        read_once=lambda: {"status": 200, "body": {"state": "pending"}},
        accept=lambda response: False,
        async_policy={
            "enabled": True,
            "poll_interval_ms": 100,
            "expected_max_delay_ms": 200,
            "max_attempts": 3,
        },
        sleep=sleep,
        monotonic=monotonic,
    )

    assert receipt["converged"] is False
    assert receipt["timed_out"] is True
    assert receipt["reason_code"] == READBACK_ASYNC_TIMEOUT
    assert receipt["attempt_count"] == 3


@pytest.mark.parametrize(
    "policy,field",
    [
        (
            {
                "enabled": True,
                "poll_interval_ms": 100,
                "expected_max_delay_ms": 1000,
                "max_attempts": 0,
            },
            "max_attempts",
        ),
        (
            {
                "enabled": True,
                "poll_interval_ms": 0,
                "expected_max_delay_ms": 1000,
                "max_attempts": 2,
            },
            "poll_interval_required",
        ),
        (
            {
                "enabled": True,
                "poll_interval_ms": 1000,
                "expected_max_delay_ms": 100,
                "max_attempts": 2,
            },
            "attempt_window_exceeds_delay",
        ),
    ],
)
def test_invalid_enabled_policy_fails_closed(policy: dict, field: str) -> None:
    with pytest.raises(ValueError) as exc:
        normalize_async_policy(policy)

    assert READBACK_ASYNC_POLICY_INVALID in str(exc.value)
    assert field in str(exc.value)
