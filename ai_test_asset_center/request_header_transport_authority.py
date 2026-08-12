"""Align RequestBuildContract header claims with the real HTTP transport.

The transport in ``sandbox_write_executor_base._http_request`` has exactly
three built-in header channels:

* ``Accept: application/json`` is always sent;
* ``Authorization`` is sent only when the exact step actor resolves through a
  declared credential channel; and
* ``Content-Type: application/json`` is sent only for a non-GET/HEAD request
  whose body is not ``None``.

The historical RequestBuildContract classified those headers by name alone,
which could seal a bodyless GET as Content-Type-capable or an anonymous step as
Authorization-capable.  This module wraps the established contract builder and
recomputes only its header component from the actual step/actor/body contract.
No transport behavior is added and arbitrary business headers remain blocked.
"""
from __future__ import annotations

from copy import deepcopy
import sys
from typing import Any

_INSTALLED = False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("actor_id")): row
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("actor_id"))
    }


def _body_transport_channel(
    step: dict[str, Any],
    operation: dict[str, Any],
    experiment: dict[str, Any],
) -> tuple[bool, str]:
    """Whether the step can pass a non-None body to the existing transport."""

    from . import request_build_contract as _request

    method = _text(operation.get("method")).upper()
    if method in {"GET", "HEAD"}:
        return False, ""
    body = _request._step_body(step, operation)
    if body is not None:
        return True, "step_or_source_body"
    if _request._observed_body_projection_fields(experiment):
        return True, "observed_body_projection"
    return False, ""


def _authorization_transport_channel(
    step: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[bool, str]:
    actor_ref = _text(step.get("actor_ref"))
    actor = _dict(_actor_index(behavior_ir).get(actor_ref))
    if not actor:
        return False, ""
    role = _text(actor.get("role")).lower()
    if role in {"anonymous", "public"}:
        return False, ""
    secret_ref = _text(
        actor.get("credential_secret_ref") or actor.get("secret_ref")
    )
    if not secret_ref:
        return False, ""
    return True, "actor_declared_credential"


def build_step_header_contract(
    *,
    step: dict[str, Any],
    operation: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build header readiness from the exact transport channels above."""

    from . import request_build_contract as _request

    required = _request._required_parameters(operation, "header")
    rows: list[dict[str, Any]] = []
    blocked = False
    for parameter in required:
        name = _text(parameter.get("name") or parameter.get("field"))
        normalized = name.lower().replace("_", "-")
        status = _request.STATUS_BLOCKED
        authority = ""
        reason = "REQUEST_REQUIRED_HEADER_TRANSPORT_UNSUPPORTED"

        if normalized == "accept":
            status = _request.STATUS_DEFERRED
            authority = "http_transport_default_accept_json"
            reason = ""
        elif normalized in {"authorization", "authorisation"}:
            ready, actor_authority = _authorization_transport_channel(
                step,
                behavior_ir,
            )
            if ready:
                status = _request.STATUS_DEFERRED
                authority = actor_authority
                reason = ""
            else:
                reason = "REQUEST_AUTHORIZATION_HEADER_CHANNEL_UNPROVEN"
        elif normalized in {"content-type", "contenttype"}:
            ready, body_authority = _body_transport_channel(
                step,
                operation,
                experiment,
            )
            if ready:
                status = _request.STATUS_DEFERRED
                authority = "json_body_transport:" + body_authority
                reason = ""
            else:
                reason = "REQUEST_CONTENT_TYPE_HEADER_CHANNEL_UNPROVEN"

        if status == _request.STATUS_BLOCKED:
            blocked = True
        rows.append(
            {
                "name": name,
                "status": status,
                "authority": authority,
                "reason_code": reason,
            }
        )

    return {
        "component": "header",
        "status": (
            _request.STATUS_BLOCKED
            if blocked
            else _request.STATUS_DEFERRED
            if rows
            else _request.STATUS_READY
        ),
        "required": rows,
        "reason_code": "REQUEST_REQUIRED_HEADER_UNBUILDABLE" if blocked else "",
    }


def _find_plan_step(
    experiment: dict[str, Any],
    *,
    phase: str,
    step_id: str,
    operation_ref: str,
) -> dict[str, Any]:
    candidates = [
        row
        for row in _list(_dict(experiment).get(f"{phase}_plan"))
        if isinstance(row, dict)
    ]
    exact = [
        row
        for row in candidates
        if _text(row.get("step_id") or row.get("id")) == step_id
    ]
    if len(exact) == 1:
        return dict(exact[0])
    by_operation = [
        row
        for row in candidates
        if _text(row.get("operation_ref")) == operation_ref
    ]
    return dict(by_operation[0]) if len(by_operation) == 1 else {}


def governed_build_request_build_contract(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild only header components, then reseal the canonical fingerprint."""

    from . import request_build_contract as _request

    original = getattr(_request, "_qualibug_original_request_build_contract", None)
    if not callable(original):
        raise RuntimeError("request_build_original_builder_missing")
    contract = deepcopy(
        original(
            experiment,
            behavior_ir=behavior_ir,
            flow_execution_contract=flow_execution_contract,
        )
    )
    operations = _request._operation_index(behavior_ir)
    step_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    deferred_count = 0

    for raw_row in _list(contract.get("steps")):
        if not isinstance(raw_row, dict):
            continue
        row = deepcopy(raw_row)
        if _text(row.get("status")) == _request.STATUS_BLOCKED and not row.get("components"):
            step_rows.append(row)
            issues.append(row)
            continue
        phase = _text(row.get("phase"))
        step_id = _text(row.get("step_id"))
        operation_ref = _text(row.get("operation_ref"))
        step = _find_plan_step(
            experiment,
            phase=phase,
            step_id=step_id,
            operation_ref=operation_ref,
        )
        operation = _dict(operations.get(operation_ref))
        components = [
            deepcopy(component)
            for component in _list(row.get("components"))
            if isinstance(component, dict)
            and _text(component.get("component")) != "header"
        ]
        components.append(
            build_step_header_contract(
                step=step,
                operation=operation,
                experiment=experiment,
                behavior_ir=behavior_ir,
            )
        )
        blocked_components = [
            component
            for component in components
            if _text(component.get("status")) == _request.STATUS_BLOCKED
        ]
        deferred_components = [
            component
            for component in components
            if _text(component.get("status")) == _request.STATUS_DEFERRED
        ]
        deferred_count += len(deferred_components)
        row.update(
            {
                "status": (
                    _request.STATUS_BLOCKED
                    if blocked_components
                    else _request.STATUS_DEFERRED
                    if deferred_components
                    else _request.STATUS_READY
                ),
                "components": components,
                "blocked_components": [
                    _text(item.get("component")) for item in blocked_components
                ],
                "deferred_components": [
                    _text(item.get("component")) for item in deferred_components
                ],
            }
        )
        step_rows.append(row)
        if blocked_components:
            issues.append(row)

    semantic = {
        "schema_version": _text(contract.get("schema_version")) or _request.SCHEMA_VERSION,
        "experiment_id": _text(contract.get("experiment_id")),
        "obligation_id": _text(contract.get("obligation_id")),
        "status": (
            _request.STATUS_BLOCKED
            if issues
            else _request.STATUS_DEFERRED
            if deferred_count
            else _request.STATUS_READY
        ),
        "steps": step_rows,
        "issue_count": len(issues),
        "deferred_component_count": deferred_count,
        "source_order_selection_allowed": False,
        "synthetic_request_values_allowed": False,
    }
    semantic["contract_fingerprint"] = _request._fingerprint(semantic)
    return semantic


def install_request_header_transport_authority() -> None:
    """Install the wrapper and refresh already-imported public aliases."""

    global _INSTALLED
    from . import request_build_contract as _request

    if not hasattr(_request, "_qualibug_original_request_build_contract"):
        _request._qualibug_original_request_build_contract = (
            _request.build_request_build_contract
        )
    _request.build_request_build_contract = governed_build_request_build_contract

    # Validation requests may deliberately violate a source-declared body
    # constraint. Install the narrow body authority anywhere the formal
    # request contract is installed so compile-time and runtime rebuilds share
    # identical semantics.
    from .validation_body_contract_authority import (
        install_validation_body_contract_authority,
    )
    install_validation_body_contract_authority()

    # ``validate_request_build_contract`` resolves the module global at call time,
    # but facade modules may have imported the builder by value. Refresh only the
    # known formal compile/runtime owners if already loaded.
    for module_name in (
        f"{__package__}.experiment_compile_freezer",
        f"{__package__}.experiment_executor_governance",
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            setattr(
                module,
                "build_request_build_contract",
                governed_build_request_build_contract,
            )
    _INSTALLED = True


__all__ = [
    "build_step_header_contract",
    "governed_build_request_build_contract",
    "install_request_header_transport_authority",
]
