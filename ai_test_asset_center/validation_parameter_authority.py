"""Shared authority for source-declared validation parameter mutations.

The validation expander, protocol materializer and RequestBuildContract must
agree on one truth boundary:

* a parameter control value is executable only when the source declares a
  concrete value (example/default/const or a single unambiguous enum value);
* schema type alone never authorizes fabricating ``\"example\"`` / ``1``;
* a treatment that intentionally removes one source-required query parameter is
  a valid negative probe, not a request-build failure; and
* path/header mutations remain subject to the existing wire-capability gate.

The installer patches the existing module globals so the established compiler,
protocol and request-contract implementations remain the only engines.  No
parallel compiler or transport path is introduced.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_INSTALLED = False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_flag(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return _text(value).lower() in {"true", "yes", "1", "required"}


def declared_parameter_control_value(
    parameter: dict[str, Any],
) -> tuple[bool, Any, str]:
    """Return one source-declared concrete control value, never a guess.

    ``0`` and ``False`` are valid declared examples/defaults. ``None`` is not
    used as a transport control value because its wire representation depends on
    parameter serialization rules that this authority does not invent.
    """

    row = _dict(parameter)
    schema = _dict(row.get("schema"))
    candidates = (
        ("parameter_example", row, "example"),
        ("schema_example", schema, "example"),
        ("parameter_default", row, "default"),
        ("schema_default", schema, "default"),
        ("schema_const", schema, "const"),
    )
    for authority, source, key in candidates:
        if key not in source:
            continue
        value = source.get(key)
        if value is not None:
            return True, deepcopy(value), authority

    enum_values = [
        deepcopy(value)
        for value in _list(schema.get("enum"))
        if value is not None
    ]
    if len(enum_values) == 1:
        return True, enum_values[0], "schema_singleton_enum"
    return False, None, ""


def strict_parameter_entries(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize only source-materializable request parameters.

    The historical expander fabricated ``example`` / ``1`` from a declared
    schema type and also invented ``1`` for undeclared path placeholders. That
    made a control request look valid without any source or runtime authority.
    Such rows are omitted here; without a concrete control value no exact
    parameter validation variant is executable.
    """

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for container in ("parameters", "request_parameters", "params"):
        for raw in _list(_dict(operation).get(container)):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            name = _text(row.get("name") or row.get("field"))
            location = _text(
                row.get("in") or row.get("location") or row.get("parameter_in")
            ).lower() or "query"
            if not name or (location, name) in seen:
                continue
            found, value, authority = declared_parameter_control_value(row)
            if not found:
                continue
            seen.add((location, name))
            schema = dict(_dict(row.get("schema")))
            if not _text(schema.get("type")):
                if isinstance(value, bool):
                    schema["type"] = "boolean"
                elif isinstance(value, int) and not isinstance(value, bool):
                    schema["type"] = "integer"
                elif isinstance(value, float):
                    schema["type"] = "number"
                elif isinstance(value, list):
                    schema["type"] = "array"
                elif isinstance(value, dict):
                    schema["type"] = "object"
                elif value is not None:
                    schema["type"] = "string"
            entries.append(
                {
                    "location": location,
                    "name": name,
                    "schema": schema,
                    "example": deepcopy(value),
                    "required": _required_flag(row.get("required")),
                    "control_value_authority": authority,
                    "source_declared_control_value": True,
                }
            )
    return entries


def strict_parameter_constraint_material(
    operation: dict[str, Any],
    property_spec: dict[str, Any],
    *,
    location: str,
    tokens: tuple[str | int, ...],
    constraint: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Build one parameter mutation from the same source-value authority."""

    if len(tokens) != 1 or not isinstance(tokens[0], str) or not constraint:
        return {}, {}, {}, "parameter_constraint_tokens_invalid"
    name = str(tokens[0])
    parameter_row: dict[str, Any] = {}
    for container in ("parameters", "request_parameters", "params"):
        for raw in _list(_dict(operation).get(container)):
            if not isinstance(raw, dict):
                continue
            if _text(raw.get("name") or raw.get("field")) != name:
                continue
            param_in = _text(
                raw.get("in") or raw.get("location") or raw.get("parameter_in")
            ).lower() or location
            if param_in != location:
                continue
            parameter_row = dict(raw)
            break
        if parameter_row:
            break
    if not parameter_row:
        return {}, {}, {}, "parameter_constraint_source_parameter_missing"

    found, current, value_authority = declared_parameter_control_value(parameter_row)
    if not found:
        return {}, {}, {}, "parameter_control_value_authority_missing"

    schema = dict(_dict(parameter_row.get("schema")))
    if not _text(schema.get("type")):
        if isinstance(current, bool):
            schema["type"] = "boolean"
        elif isinstance(current, int) and not isinstance(current, bool):
            schema["type"] = "integer"
        elif isinstance(current, float):
            schema["type"] = "number"
        elif isinstance(current, list):
            schema["type"] = "array"
        elif isinstance(current, dict):
            schema["type"] = "object"
        else:
            schema["type"] = "string"

    constraint_value = property_spec.get("validation_constraint_value")
    constraint_source = _text(
        property_spec.get("validation_constraint_source") or "request_schema"
    )
    if constraint_source not in {"request_schema", "source_invariant"}:
        return {}, {}, {}, "source_constraint_lineage_invalid"

    control = {name: deepcopy(current)}
    treatment = deepcopy(control)
    if constraint == "required":
        treatment.pop(name, None)
        operator = "remove_required_parameter"
    else:
        # Reuse the established JSON-Schema mutation implementation. This
        # authority changes only how the valid control is sourced.
        from . import experiment_protocols_privacy_base as _privacy

        ok, invalid_value, operator = _privacy._invalid_constraint_value(
            constraint=constraint,
            field_schema=schema,
            current=current,
            constraint_value=constraint_value,
        )
        if not ok:
            return {}, {}, {}, f"source_constraint_mutation_unavailable:{constraint}"
        treatment[name] = deepcopy(invalid_value)

    from . import experiment_protocols_privacy_base as _privacy

    mutation = {
        "json_path": _text(property_spec.get("json_path"))
        or _privacy._json_path((f"@{location}", name)),
        "field_tokens": [f"@{location}", name],
        "parameter_location": location,
        "constraint": constraint,
        "constraint_value": deepcopy(constraint_value),
        "source": constraint_source,
        "operator": operator,
        "control_value_authority": value_authority,
        "source_declared_control_value": True,
    }
    return control, treatment, mutation, ""


def _declared_required_parameter_removals(
    step: dict[str, Any],
    *,
    location: str,
) -> set[str]:
    """Return exact intentionally-absent required parameters for one step."""

    mutation = _dict(_dict(step).get("mutation"))
    if not (
        _text(mutation.get("parameter_location")).lower() == location
        and _text(mutation.get("constraint")) == "required"
        and _text(mutation.get("operator")) == "remove_required_parameter"
        and mutation.get("source_declared_control_value") is True
    ):
        return set()
    tokens = mutation.get("field_tokens")
    if not (
        isinstance(tokens, list)
        and len(tokens) == 2
        and _text(tokens[0]).lower() == f"@{location}"
        and _text(tokens[1])
    ):
        return set()
    return {_text(tokens[1])}


def query_contract_with_declared_required_removal(
    step: dict[str, Any],
    operation: dict[str, Any],
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any],
) -> dict[str, Any]:
    """Allow only the exact source-declared required-query negative arm."""

    from . import request_build_contract as _request

    original = getattr(_request, "_qualibug_original_query_contract", None)
    if not callable(original):
        raise RuntimeError("request_build_original_query_contract_missing")
    result = dict(
        original(
            step,
            operation,
            experiment=experiment,
            behavior_ir=behavior_ir,
            flow_execution_contract=flow_execution_contract,
        )
    )
    removals = _declared_required_parameter_removals(step, location="query")
    if not removals:
        return result

    rows = [dict(row) for row in _list(result.get("required")) if isinstance(row, dict)]
    for row in rows:
        if (
            _text(row.get("name")) in removals
            and _text(row.get("status")) == _request.STATUS_BLOCKED
            and _text(row.get("reason_code")) == "REQUEST_REQUIRED_QUERY_MISSING"
        ):
            row.update(
                {
                    "status": _request.STATUS_READY,
                    "authority": "declared_required_query_removal_mutation",
                    "reason_code": "",
                    "intentional_absence": True,
                }
            )

    if any(_text(row.get("status")) == _request.STATUS_BLOCKED for row in rows):
        status = _request.STATUS_BLOCKED
    elif any(_text(row.get("status")) == _request.STATUS_DEFERRED for row in rows):
        status = _request.STATUS_DEFERRED
    else:
        status = _request.STATUS_READY
    result.update(
        {
            "status": status,
            "required": rows,
            "reason_code": (
                "REQUEST_REQUIRED_QUERY_UNBUILDABLE"
                if status == _request.STATUS_BLOCKED
                else ""
            ),
        }
    )
    return result


def install_validation_parameter_authority() -> None:
    """Install one authority into the three established validation stages."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import _validation_obligation_expander_core as _expander
    from . import experiment_protocols_privacy_base as _privacy
    from . import request_build_contract as _request

    _expander._parameter_entries = strict_parameter_entries
    _privacy._parameter_constraint_material = strict_parameter_constraint_material

    if not hasattr(_request, "_qualibug_original_query_contract"):
        _request._qualibug_original_query_contract = _request._query_contract
    _request._query_contract = query_contract_with_declared_required_removal
    _INSTALLED = True


__all__ = [
    "declared_parameter_control_value",
    "strict_parameter_entries",
    "strict_parameter_constraint_material",
    "query_contract_with_declared_required_removal",
    "install_validation_parameter_authority",
]
