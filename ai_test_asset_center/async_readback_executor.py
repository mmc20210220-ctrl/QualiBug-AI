"""Bounded executor for source-declared asynchronous readback policies.

This module owns timing only. It does not discover endpoints, identities, fields,
or business predicates. Callers must supply the already-resolved read function
and an explicit acceptance predicate. Disabled policies preserve the historical
single-attempt behavior; enabled policies are bounded by both attempt count and
expected maximum delay.
"""
from __future__ import annotations

import time
from typing import Any, Callable


ASYNC_READBACK_SCHEMA = "qualibug.async-readback-execution.v1"
READBACK_ASYNC_TIMEOUT = "READBACK_ASYNC_TIMEOUT"
READBACK_ASYNC_POLICY_INVALID = "READBACK_ASYNC_POLICY_INVALID"

_MAX_ATTEMPTS_HARD_LIMIT = 50
_MAX_DELAY_MS_HARD_LIMIT = 300_000
_MAX_INTERVAL_MS_HARD_LIMIT = 60_000


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_async_policy(value: Any) -> dict[str, Any]:
    """Validate and normalize an existing compiled async policy.

    No retry policy is invented. Missing/disabled policy means exactly one
    immediate attempt. Enabled policy must declare a positive bounded attempt
    count and non-negative interval/delay values.
    """
    raw = _dict(value)
    enabled = raw.get("enabled") is True
    if not enabled:
        return {
            "enabled": False,
            "expected_max_delay_ms": 0,
            "poll_interval_ms": 0,
            "max_attempts": 1,
            "required_stable_observations": 1,
            "terminal_condition": _text(
                raw.get("terminal_condition") or "immediate"
            ),
        }

    max_attempts = _int(raw.get("max_attempts"), 0)
    interval_ms = _int(raw.get("poll_interval_ms"), -1)
    max_delay_ms = _int(raw.get("expected_max_delay_ms"), -1)
    stable = _int(
        raw.get("required_stable_observations")
        or raw.get("stable_observations")
        or 1,
        1,
    )
    if not 1 <= max_attempts <= _MAX_ATTEMPTS_HARD_LIMIT:
        raise ValueError(f"{READBACK_ASYNC_POLICY_INVALID}:max_attempts")
    if not 0 <= interval_ms <= _MAX_INTERVAL_MS_HARD_LIMIT:
        raise ValueError(f"{READBACK_ASYNC_POLICY_INVALID}:poll_interval_ms")
    if not 0 <= max_delay_ms <= _MAX_DELAY_MS_HARD_LIMIT:
        raise ValueError(
            f"{READBACK_ASYNC_POLICY_INVALID}:expected_max_delay_ms"
        )
    if not 1 <= stable <= max_attempts:
        raise ValueError(
            f"{READBACK_ASYNC_POLICY_INVALID}:required_stable_observations"
        )
    if max_attempts > 1 and interval_ms <= 0:
        raise ValueError(
            f"{READBACK_ASYNC_POLICY_INVALID}:poll_interval_required"
        )
    minimum_window = interval_ms * max(0, max_attempts - 1)
    if max_delay_ms and minimum_window > max_delay_ms:
        raise ValueError(
            f"{READBACK_ASYNC_POLICY_INVALID}:attempt_window_exceeds_delay"
        )

    return {
        "enabled": True,
        "expected_max_delay_ms": max_delay_ms,
        "poll_interval_ms": interval_ms,
        "max_attempts": max_attempts,
        "required_stable_observations": stable,
        "terminal_condition": _text(
            raw.get("terminal_condition") or "source_declared_predicate"
        ),
    }


def execute_async_readback(
    *,
    read_once: Callable[[], dict[str, Any]],
    accept: Callable[[dict[str, Any]], bool],
    async_policy: dict[str, Any] | None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Execute one source-resolved readback with bounded convergence polling."""
    policy = normalize_async_policy(async_policy)
    started = monotonic()
    attempts: list[dict[str, Any]] = []
    stable_count = 0
    converged = False
    final_response: dict[str, Any] = {}

    for attempt_number in range(1, policy["max_attempts"] + 1):
        response = _dict(read_once())
        accepted = bool(accept(response))
        stable_count = stable_count + 1 if accepted else 0
        elapsed_ms = max(0, int((monotonic() - started) * 1000))
        attempts.append(
            {
                "attempt": attempt_number,
                "elapsed_ms": elapsed_ms,
                "status_code": _int(
                    response.get("status_code") or response.get("status"),
                    0,
                ),
                "predicate_accepted": accepted,
                "stable_observation_count": stable_count,
            }
        )
        final_response = response
        if stable_count >= policy["required_stable_observations"]:
            converged = True
            break
        if attempt_number >= policy["max_attempts"]:
            break

        projected_elapsed = elapsed_ms + policy["poll_interval_ms"]
        max_delay_ms = policy["expected_max_delay_ms"]
        if max_delay_ms and projected_elapsed > max_delay_ms:
            break
        sleep(policy["poll_interval_ms"] / 1000.0)

    elapsed_ms = max(0, int((monotonic() - started) * 1000))
    return {
        "schema_version": ASYNC_READBACK_SCHEMA,
        "policy": policy,
        "attempts": attempts,
        "attempt_count": len(attempts),
        "elapsed_ms": elapsed_ms,
        "converged": converged,
        "timed_out": bool(policy["enabled"] and not converged),
        "reason_code": "" if converged else (
            READBACK_ASYNC_TIMEOUT if policy["enabled"] else ""
        ),
        "final_response": final_response,
    }
