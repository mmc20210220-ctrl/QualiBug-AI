"""Decorator that adds compiled async readback to a governed write facade.

The wrapped write executor remains the sole safety and transport authority.
This adapter runs only after it returns, polls only the same approved base URL
and source-declared observation path, and never changes a rejected transport
into an accepted operation.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable

from .async_readback_executor import (
    READBACK_ASYNC_POLICY_INVALID,
    execute_async_readback,
    normalize_async_policy,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status(value: Any) -> int:
    row = _dict(value)
    try:
        return int(row.get("status") or row.get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _server_managed_field(value: Any) -> bool:
    token = "".join(char for char in _text(value).lower() if char.isalnum())
    return token in {
        "createdat",
        "updatedat",
        "createdtime",
        "updatedtime",
        "modifiedat",
        "modifiedtime",
        "timestamp",
        "versiontimestamp",
    }


def _business_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _business_projection(child)
            for key, child in sorted(value.items())
            if not _server_managed_field(key)
        }
    if isinstance(value, list):
        projected = [_business_projection(child) for child in value]
        return sorted(
            projected,
            key=lambda child: json.dumps(
                child,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
    return value


def _compiled_policy(kwargs: dict[str, Any]) -> dict[str, Any]:
    explicit = kwargs.pop("async_policy", None)
    runtime_body_plan = _dict(kwargs.get("runtime_body_plan"))
    runtime_contract = _dict(kwargs.get("runtime_contract"))
    candidates = [
        explicit,
        runtime_body_plan.get("async_policy"),
        _dict(runtime_body_plan.get("readback_contract")).get("async_policy"),
        _dict(runtime_body_plan.get("observer_contract")).get("async_policy"),
        runtime_contract.get("async_policy"),
        _dict(runtime_contract.get("readback_contract")).get("async_policy"),
        _dict(runtime_contract.get("observer_contract")).get("async_policy"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return dict(candidate)
    return {"enabled": False}


def _acceptor(terminal_condition: str, before_body: Any):
    condition = _text(terminal_condition).lower()
    if condition in {
        "immediate",
        "http_success",
        "response_available",
        "readable_response",
    }:
        return lambda response: 200 <= _status(response) < 300
    if condition in {
        "body_changed",
        "state_changed",
        "business_state_changed",
        "effect_observed",
    }:
        before_projection = _business_projection(before_body)
        return lambda response: (
            200 <= _status(response) < 300
            and isinstance(response.get("body"), (dict, list))
            and _business_projection(response.get("body"))
            != before_projection
        )
    return None


def wrap_governed_write(
    execute_write: Callable[..., dict[str, Any]],
    http_request: Callable[..., dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    """Return a governed-write callable enhanced by compiled async readback."""

    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_kwargs = dict(kwargs)
        policy_value = _compiled_policy(call_kwargs)
        receipt = deepcopy(_dict(execute_write(*args, **call_kwargs)))
        if receipt.get("accepted") is not True:
            return receipt

        observation_path = _text(call_kwargs.get("observation_path"))
        base_url = _text(call_kwargs.get("base_url"))
        actor_token = _text(call_kwargs.get("actor_token"))
        if not observation_path.startswith("/") or not base_url:
            return receipt

        try:
            policy = normalize_async_policy(policy_value)
        except ValueError as exc:
            receipt["async_readback_receipt"] = {
                "schema_version": "qualibug.async-readback-execution.v1",
                "policy": policy_value,
                "attempts": [],
                "attempt_count": 0,
                "converged": False,
                "timed_out": False,
                "reason_code": READBACK_ASYNC_POLICY_INVALID,
                "detail": str(exc),
                "final_response": {},
            }
            receipt["readback_converged"] = False
            return receipt
        if not policy["enabled"]:
            return receipt

        accept = _acceptor(
            policy["terminal_condition"],
            _dict(receipt.get("before")).get("body"),
        )
        if accept is None:
            receipt["async_readback_receipt"] = {
                "schema_version": "qualibug.async-readback-execution.v1",
                "policy": policy,
                "attempts": [],
                "attempt_count": 0,
                "converged": False,
                "timed_out": False,
                "reason_code": "READBACK_TERMINAL_CONDITION_UNSUPPORTED",
                "detail": policy["terminal_condition"],
                "final_response": {},
            }
            receipt["readback_converged"] = False
            return receipt

        url = base_url.rstrip("/") + observation_path
        async_receipt = execute_async_readback(
            read_once=lambda: http_request(
                "GET",
                url,
                token=actor_token,
            ),
            accept=accept,
            async_policy=policy,
        )
        receipt["async_readback_receipt"] = async_receipt
        receipt["readback_converged"] = async_receipt.get("converged") is True
        final_response = _dict(async_receipt.get("final_response"))
        if final_response:
            receipt["after_immediate"] = deepcopy(_dict(receipt.get("after")))
            receipt["after"] = deepcopy(final_response)
            receipt["after"]["async_attempts"] = int(
                async_receipt.get("attempt_count") or 0
            )
        return receipt

    wrapped.__name__ = getattr(execute_write, "__name__", "execute_governed_write")
    wrapped.__doc__ = getattr(execute_write, "__doc__", None)
    return wrapped
