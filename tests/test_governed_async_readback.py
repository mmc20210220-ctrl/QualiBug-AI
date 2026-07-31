from __future__ import annotations

from ai_test_asset_center.governed_async_readback import wrap_governed_write


def _accepted_write(**kwargs):
    assert "async_policy" not in kwargs
    return {
        "accepted": True,
        "before": {"status": 200, "body": {"state": "pending"}},
        "after": {"status": 200, "body": {"state": "pending"}},
    }


def test_explicit_compiled_policy_polls_approved_observation_path() -> None:
    calls: list[tuple[str, str]] = []
    bodies = iter(
        [
            {"state": "pending"},
            {"state": "ready"},
        ]
    )

    def http_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        return {"status": 200, "body": next(bodies)}

    execute = wrap_governed_write(_accepted_write, http_request)
    receipt = execute(
        base_url="https://approved.example.test",
        observation_path="/orders/1",
        actor_token="test-token",
        runtime_contract={},
        runtime_body_plan={},
        async_policy={
            "enabled": True,
            "poll_interval_ms": 1,
            "expected_max_delay_ms": 10,
            "max_attempts": 3,
            "terminal_condition": "business_state_changed",
        },
    )

    assert calls == [
        ("GET", "https://approved.example.test/orders/1"),
        ("GET", "https://approved.example.test/orders/1"),
    ]
    assert receipt["readback_converged"] is True
    assert receipt["after_immediate"]["body"]["state"] == "pending"
    assert receipt["after"]["body"]["state"] == "ready"
    assert receipt["after"]["async_attempts"] == 2


def test_nested_readback_contract_policy_is_consumed() -> None:
    calls = {"count": 0}

    def http_request(method: str, url: str, **kwargs):
        calls["count"] += 1
        return {"status": 200, "body": {"state": "ready"}}

    execute = wrap_governed_write(_accepted_write, http_request)
    receipt = execute(
        base_url="https://approved.example.test",
        observation_path="/orders/1",
        actor_token="test-token",
        runtime_contract={},
        runtime_body_plan={
            "readback_contract": {
                "async_policy": {
                    "enabled": True,
                    "poll_interval_ms": 1,
                    "expected_max_delay_ms": 10,
                    "max_attempts": 2,
                    "terminal_condition": "http_success",
                }
            }
        },
    )

    assert calls["count"] == 1
    assert receipt["readback_converged"] is True


def test_disabled_policy_does_not_add_an_extra_read() -> None:
    calls = {"count": 0}

    def http_request(method: str, url: str, **kwargs):
        calls["count"] += 1
        return {"status": 200, "body": {}}

    execute = wrap_governed_write(_accepted_write, http_request)
    receipt = execute(
        base_url="https://approved.example.test",
        observation_path="/orders/1",
        actor_token="test-token",
        runtime_contract={},
        runtime_body_plan={},
    )

    assert calls["count"] == 0
    assert "async_readback_receipt" not in receipt


def test_rejected_write_never_triggers_async_readback() -> None:
    calls = {"count": 0}

    def rejected_write(**kwargs):
        return {"accepted": False, "status": "rejected"}

    def http_request(method: str, url: str, **kwargs):
        calls["count"] += 1
        return {"status": 200, "body": {}}

    execute = wrap_governed_write(rejected_write, http_request)
    receipt = execute(
        base_url="https://approved.example.test",
        observation_path="/orders/1",
        actor_token="test-token",
        runtime_contract={},
        runtime_body_plan={},
        async_policy={
            "enabled": True,
            "poll_interval_ms": 1,
            "expected_max_delay_ms": 10,
            "max_attempts": 2,
        },
    )

    assert receipt["accepted"] is False
    assert calls["count"] == 0


def test_unsupported_terminal_condition_fails_closed_without_polling() -> None:
    calls = {"count": 0}

    def http_request(method: str, url: str, **kwargs):
        calls["count"] += 1
        return {"status": 200, "body": {}}

    execute = wrap_governed_write(_accepted_write, http_request)
    receipt = execute(
        base_url="https://approved.example.test",
        observation_path="/orders/1",
        actor_token="test-token",
        runtime_contract={},
        runtime_body_plan={},
        async_policy={
            "enabled": True,
            "poll_interval_ms": 1,
            "expected_max_delay_ms": 10,
            "max_attempts": 2,
            "terminal_condition": "arbitrary_python_expression",
        },
    )

    assert calls["count"] == 0
    assert receipt["readback_converged"] is False
    assert receipt["async_readback_receipt"]["reason_code"] == (
        "READBACK_TERMINAL_CONDITION_UNSUPPORTED"
    )
