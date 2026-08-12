"""Source-backed authority for validation requests that intentionally violate body schema.

Validation probes must be able to send exactly the malformed request declared by
JSON Schema without teaching the general RequestBuildContract to invent request
values.  This authority makes two narrow corrections:

* JSON Schema ``required`` means object-key presence; an empty string/list/object
  is still a present field and is governed by its own type/length constraints.
* a source-schema ``required`` mutation carries an explicit
  ``required_field_removal`` stamp so the request builder can distinguish an
  intentional negative arm from an accidentally unbuildable request.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_REQUEST_INSTALLED = False
_PROTOCOL_INSTALLED = False


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _t(value: Any) -> str:
    return str(value or "").strip()


def _recompute_body_status(result: dict[str, Any], request_module: Any) -> dict[str, Any]:
    rows = [dict(row) for row in _l(result.get("required")) if isinstance(row, dict)]
    placeholders = [
        dict(row) for row in _l(result.get("placeholders")) if isinstance(row, dict)
    ]
    all_rows = [*rows, *placeholders]
    if any(_t(row.get("status")) == request_module.STATUS_BLOCKED for row in all_rows):
        status = request_module.STATUS_BLOCKED
    elif any(_t(row.get("status")) == request_module.STATUS_DEFERRED for row in all_rows):
        status = request_module.STATUS_DEFERRED
    else:
        status = request_module.STATUS_READY
    result.update(
        {
            "status": status,
            "required": rows,
            "placeholders": placeholders,
            "reason_code": "REQUEST_BODY_UNBUILDABLE" if status == request_module.STATUS_BLOCKED else "",
        }
    )
    return result


def body_contract_with_schema_presence(
    step: dict[str, Any],
    operation: dict[str, Any],
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    """Treat a required body field as present when the JSON object contains its key."""

    from . import request_build_contract as request

    original = getattr(request, "_qualibug_original_body_contract", None)
    if not callable(original):
        raise RuntimeError("request_build_original_body_contract_missing")
    result = deepcopy(
        original(
            step,
            operation,
            experiment=experiment,
            behavior_ir=behavior_ir,
            flow_execution_contract=flow_execution_contract,
        )
    )
    body = request._step_body(step, operation)
    body_dict = body if isinstance(body, dict) else {}
    rows = [dict(row) for row in _l(result.get("required")) if isinstance(row, dict)]
    for row in rows:
        field = _t(row.get("field"))
        if (
            field
            and field in body_dict
            and _t(row.get("status")) == request.STATUS_BLOCKED
            and _t(row.get("reason_code")) == "REQUEST_REQUIRED_BODY_FIELD_MISSING"
        ):
            row.update(
                {
                    "status": request.STATUS_READY,
                    "authority": "json_schema_required_key_presence",
                    "reason_code": "",
                }
            )
    result["required"] = rows
    return _recompute_body_status(result, request)


def _stamp_required_body_removal(result: dict[str, Any]) -> dict[str, Any]:
    if _t(result.get("status")) != "COMPILED":
        return result
    treatment = [dict(row) for row in _l(result.get("treatment_plan")) if isinstance(row, dict)]
    if len(treatment) != 1:
        return result
    step = treatment[0]
    mutation = _d(step.get("mutation"))
    if not (
        _t(mutation.get("parameter_location")).lower() in {"", "body"}
        and _t(mutation.get("constraint")).lower() == "required"
        and _t(mutation.get("operator")) == "remove_required_field"
        and _t(mutation.get("source")) == "request_schema"
    ):
        return result
    path = _t(mutation.get("json_path"))
    leaf = path.rsplit(".", 1)[-1].strip("[]") if path else ""
    if not leaf:
        return result
    step["required_field_removal"] = [leaf]
    updated = dict(result)
    updated["treatment_plan"] = treatment
    return updated


def install_validation_body_contract_authority() -> None:
    global _REQUEST_INSTALLED, _PROTOCOL_INSTALLED

    if not _REQUEST_INSTALLED:
        from . import request_build_contract as request

        if not hasattr(request, "_qualibug_original_body_contract"):
            request._qualibug_original_body_contract = request._body_contract
        request._body_contract = body_contract_with_schema_presence
        _REQUEST_INSTALLED = True

    if not _PROTOCOL_INSTALLED:
        from . import experiment_protocols_privacy_base as privacy

        if not hasattr(privacy, "_qualibug_original_validation_body_protocol"):
            privacy._qualibug_original_validation_body_protocol = privacy.compile_family_protocol
        original = privacy._qualibug_original_validation_body_protocol

        def compile_family_protocol(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return _stamp_required_body_removal(dict(original(*args, **kwargs)))

        privacy.compile_family_protocol = compile_family_protocol
        _PROTOCOL_INSTALLED = True


__all__ = [
    "body_contract_with_schema_presence",
    "install_validation_body_contract_authority",
]
