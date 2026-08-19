"""Shared authority for source-declared validation parameter mutations.

The validation expander, protocol materializer and RequestBuildContract must
agree on one truth boundary:

* a parameter control value is executable only when the source declares a
  concrete value (example/default/const or a single unambiguous enum value);
* schema type alone never authorizes fabricating ``\"example\"`` / ``1``;
* a treatment that intentionally removes one source-required query parameter is
  a valid negative probe, not a request-build failure; and
* path/header mutations remain subject to the existing wire-capability gate.

The request-only installer is deliberately separate from the compile installer:
a fresh runtime needs only the RequestBuildContract query rule and must not pull
validation-expander/protocol modules into execution just to rebuild a fingerprint.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_INSTALLED = False
_REQUEST_INSTALLED = False


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
    """Return one source-declared concrete control value, never a guess."""

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
    """Normalize only source-materializable request parameters."""

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
        # Compatibility with the lenient IR shape: some compilers emit
        # ``parameters: ["sku"]`` (plain source-declared names) instead of
        # dict rows. The name is still source-declared; resolve the schema
        # from request_schema.properties[name] so the strict authority
        # install is import-order independent of the lenient compiler.
        declared_names = {
            _text(raw)
            for raw in _list(_dict(operation).get("parameters"))
            if isinstance(raw, str)
        }
        _props = _dict(_dict(operation.get("request_schema")).get("properties"))
        if name in declared_names or name in _props:
            _prop_schema = (
                dict(_props.get(name))
                if isinstance(_props.get(name), dict)
                else {}
            )
            parameter_row = {
                "name": name,
                "in": location,
                "schema": dict(_prop_schema),
                "example": _prop_schema.get("example"),
            }
        elif location == "header":
            # Header mutations are never wire-rendered (no executor dispatch
            # path consumes a header plan dict), so their only possible
            # outcome is the honest downstream block naming the unapplied
            # operator. Allow an undeclared header name to reach that block
            # with an explicit sentinel authority instead of failing closed
            # on a source-authority rule that cannot apply.
            parameter_row = {
                "name": name,
                "in": location,
                "schema": {"type": "string"},
            }
        else:
            return {}, {}, {}, "parameter_constraint_source_parameter_missing"

    found, current, value_authority = declared_parameter_control_value(parameter_row)
    if not found:
        # Path/header parameters are never wire-rendered as mutated business
        # values: the executor substitutes a sentinel for path placeholders
        # and never consumes a header plan dict at all. A sentinel control
        # value for these locations keeps the compile reaching the honest
        # downstream block (identical wire arms) instead of failing closed on
        # an authority rule that cannot apply. Query parameters without a
        # source-declared value stay fail-closed.
        if location == "path":
            current = "1"
            value_authority = "path_placeholder_sentinel"
        elif location == "header":
            current = "example"
            value_authority = "header_sentinel_unrenderable"
        else:
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


def install_request_parameter_contract_authority() -> None:
    """Install only the RequestBuildContract query rule; safe for runtime."""

    global _REQUEST_INSTALLED
    if _REQUEST_INSTALLED:
        return
    from . import request_build_contract as _request

    if not hasattr(_request, "_qualibug_original_query_contract"):
        _request._qualibug_original_query_contract = _request._query_contract
    _request._query_contract = query_contract_with_declared_required_removal
    _REQUEST_INSTALLED = True


def install_validation_parameter_authority() -> None:
    """Install compile-time expander/protocol authority plus request authority."""

    global _INSTALLED
    if _INSTALLED:
        install_request_parameter_contract_authority()
        from .validation_body_control_authority import (
            install_validation_body_control_authority,
        )
        install_validation_body_control_authority()
        return

    from . import _validation_obligation_expander_core as _expander
    from . import experiment_protocols_privacy_base as _privacy

    _expander._parameter_entries = strict_parameter_entries
    _privacy._parameter_constraint_material = strict_parameter_constraint_material
    from .validation_body_control_authority import (
        install_validation_body_control_authority,
    )
    install_validation_body_control_authority()
    install_request_parameter_contract_authority()
    _INSTALLED = True


__all__ = [
    "declared_parameter_control_value",
    "strict_parameter_entries",
    "strict_parameter_constraint_material",
    "query_contract_with_declared_required_removal",
    "install_request_parameter_contract_authority",
    "install_validation_parameter_authority",
]
