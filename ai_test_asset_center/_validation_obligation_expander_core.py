"""Expand source-grounded validation obligations across nested request schemas."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from .behavior_ir_core import _infer_operation_effect

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = operation.get("request_example")
    if isinstance(direct, dict) and direct:
        return deepcopy(direct)
    request_schema = _dict(operation.get("request_schema"))
    for media in _dict(request_schema.get("content")).values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return deepcopy(example)
        for row in _dict(media.get("examples")).values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return deepcopy(value)
    return {}


def _body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_schema = _dict(operation.get("request_schema"))
    if _text(request_schema.get("type")) or _dict(
        request_schema.get("properties")
    ):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema:
            return schema
    return {}


def _parameter_entries(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize OpenAPI parameters[] and path placeholders into constraint rows."""

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _append(
        *,
        location: str,
        name: str,
        schema: dict[str, Any] | None = None,
        example: Any = None,
        required: bool = False,
    ) -> None:
        loc = _text(location).lower() or "query"
        field = _text(name)
        if not field or (loc, field) in seen:
            return
        seen.add((loc, field))
        field_schema = dict(schema or {})
        if not _text(field_schema.get("type")) and example is not None:
            if isinstance(example, bool):
                field_schema.setdefault("type", "boolean")
            elif isinstance(example, int) and not isinstance(example, bool):
                field_schema.setdefault("type", "integer")
            elif isinstance(example, float):
                field_schema.setdefault("type", "number")
            elif isinstance(example, list):
                field_schema.setdefault("type", "array")
            elif isinstance(example, dict):
                field_schema.setdefault("type", "object")
            else:
                field_schema.setdefault("type", "string")
        entries.append({
            "location": loc,
            "name": field,
            "schema": field_schema,
            "example": example,
            "required": bool(required),
        })

    for raw in _list(operation.get("parameters")):
        if isinstance(raw, str):
            # Markdown-derived parameter names without location: treat as body
            # field candidates only when absent from the JSON body example so
            # query/path docs are still reachable.
            continue
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"))
        location = _text(raw.get("in") or raw.get("location")).lower() or "query"
        schema = _dict(raw.get("schema"))
        example = raw.get("example")
        if example is None:
            example = schema.get("example")
        if example is None and _text(schema.get("type")).lower() == "string":
            example = "example"
        if example is None and _text(schema.get("type")).lower() == "integer":
            example = 1
        _append(
            location=location,
            name=name,
            schema=schema,
            example=example,
            required=bool(raw.get("required")),
        )

    path = _text(operation.get("path") or operation.get("raw_path"))
    for match in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path):
        _append(location="path", name=match, schema={"type": "string"}, example="1", required=True)
    for match in re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", path):
        _append(location="path", name=match, schema={"type": "string"}, example="1", required=True)

    return entries


def _matches_declared_type(value: Any, declared_type: str) -> bool:
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "array":
        return isinstance(value, list)
    if declared_type == "object":
        return isinstance(value, dict)
    if declared_type == "null":
        return value is None
    return False


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _exclusive_boundary(
    field_schema: dict[str, Any],
    key: str,
    fallback_key: str,
) -> int | float | None:
    raw = field_schema.get(key)
    if _numeric(raw):
        return raw
    if raw is True and _numeric(field_schema.get(fallback_key)):
        return field_schema[fallback_key]
    return None


def _constraint_targets(
    *,
    field_schema: dict[str, Any],
    example_value: Any,
    required: bool,
) -> list[tuple[str, Any]]:
    targets: list[tuple[str, Any]] = []
    if required:
        targets.append(("required", True))

    declared_type = _text(field_schema.get("type")).lower()
    if declared_type and _matches_declared_type(example_value, declared_type):
        targets.append((f"type:{declared_type}", declared_type))

    enum_values = _list(field_schema.get("enum"))
    if enum_values and example_value in enum_values:
        targets.append(("enum", deepcopy(enum_values)))

    if isinstance(example_value, str):
        min_length = field_schema.get("minLength")
        if (
            isinstance(min_length, int)
            and not isinstance(min_length, bool)
            and min_length > 0
            and len(example_value) >= min_length
        ):
            targets.append(("minLength", min_length))
        max_length = field_schema.get("maxLength")
        if (
            isinstance(max_length, int)
            and not isinstance(max_length, bool)
            and max_length >= 0
            and len(example_value) <= max_length
        ):
            targets.append(("maxLength", max_length))
        pattern = _text(field_schema.get("pattern"))
        if pattern:
            try:
                matches = re.search(pattern, example_value) is not None
            except re.error:
                matches = False
            if matches:
                targets.append(("pattern", pattern))

    if _numeric(example_value):
        minimum = field_schema.get("minimum")
        if _numeric(minimum) and example_value >= minimum:
            targets.append(("minimum", minimum))
        exclusive_minimum = _exclusive_boundary(
            field_schema,
            "exclusiveMinimum",
            "minimum",
        )
        if (
            exclusive_minimum is not None
            and example_value > exclusive_minimum
        ):
            targets.append(("exclusiveMinimum", exclusive_minimum))
        maximum = field_schema.get("maximum")
        if _numeric(maximum) and example_value <= maximum:
            targets.append(("maximum", maximum))
        exclusive_maximum = _exclusive_boundary(
            field_schema,
            "exclusiveMaximum",
            "maximum",
        )
        if (
            exclusive_maximum is not None
            and example_value < exclusive_maximum
        ):
            targets.append(("exclusiveMaximum", exclusive_maximum))

    if isinstance(example_value, list):
        min_items = field_schema.get("minItems")
        if (
            isinstance(min_items, int)
            and not isinstance(min_items, bool)
            and min_items > 0
            and len(example_value) >= min_items
        ):
            targets.append(("minItems", min_items))
        max_items = field_schema.get("maxItems")
        if (
            isinstance(max_items, int)
            and not isinstance(max_items, bool)
            and max_items >= 0
            and len(example_value) <= max_items
        ):
            targets.append(("maxItems", max_items))
    return targets


def _json_path(tokens: tuple[str | int, ...]) -> str:
    path = "$"
    for token in tokens:
        if isinstance(token, int):
            path += f"[{token}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            path += f".{token}"
        else:
            escaped = token.replace("\\", "\\\\").replace("'", "\\'")
            path += f"['{escaped}']"
    return path


def _display_path(tokens: tuple[str | int, ...]) -> str:
    return _json_path(tokens).removeprefix("$.")


def _parse_generated_json_path(value: str) -> tuple[str | int, ...]:
    text = _text(value)
    if not text:
        return ()
    if not text.startswith("$"):
        return tuple(part for part in text.split(".") if part)
    tokens: list[str | int] = []
    index = 1
    while index < len(text):
        if text[index] == ".":
            index += 1
            match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
            if not match:
                return ()
            token = match.group(0)
            tokens.append(token)
            index += len(token)
            continue
        if text.startswith("['", index):
            end = index + 2
            escaped = False
            chars: list[str] = []
            while end < len(text):
                char = text[end]
                if escaped:
                    chars.append(char)
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif text.startswith("']", end):
                    break
                else:
                    chars.append(char)
                end += 1
            if end >= len(text) or not text.startswith("']", end):
                return ()
            tokens.append("".join(chars))
            index = end + 2
            continue
        if text[index] == "[":
            end = text.find("]", index)
            if end < 0:
                return ()
            raw = text[index + 1 : end]
            if not raw.isdigit():
                return ()
            tokens.append(int(raw))
            index = end + 1
            continue
        return ()
    return tuple(tokens)


def _schema_nodes(
    schema: dict[str, Any],
    example: Any,
    *,
    tokens: tuple[str | int, ...] = (),
    required: bool = False,
    depth: int = 0,
) -> list[tuple[tuple[str | int, ...], dict[str, Any], Any, bool]]:
    if depth > 12:
        return []
    nodes: list[
        tuple[tuple[str | int, ...], dict[str, Any], Any, bool]
    ] = []
    if tokens:
        nodes.append((tokens, schema, example, required))

    properties = _dict(schema.get("properties"))
    if isinstance(example, dict) and properties:
        required_fields = {
            _text(value)
            for value in _list(schema.get("required"))
            if _text(value)
        }
        for field, field_schema_raw in properties.items():
            field_name = str(field)
            field_schema = _dict(field_schema_raw)
            if field_name not in example or not field_schema:
                continue
            nodes.extend(_schema_nodes(
                field_schema,
                example[field_name],
                tokens=(*tokens, field_name),
                required=field_name in required_fields,
                depth=depth + 1,
            ))

    items_schema = _dict(schema.get("items"))
    if (
        isinstance(example, list)
        and example
        and items_schema
    ):
        nodes.extend(_schema_nodes(
            items_schema,
            example[0],
            tokens=(*tokens, 0),
            required=False,
            depth=depth + 1,
        ))
    return nodes


def _explicit_tokens(property_spec: dict[str, Any]) -> tuple[str | int, ...]:
    raw_tokens = property_spec.get("field_tokens")
    if isinstance(raw_tokens, list) and raw_tokens and all(
        isinstance(value, (str, int)) and not isinstance(value, bool)
        for value in raw_tokens
    ):
        return tuple(raw_tokens)
    json_path = _text(property_spec.get("json_path"))
    if json_path:
        return _parse_generated_json_path(json_path)
    field_path = _text(
        property_spec.get("field_path")
        or property_spec.get("field")
        or property_spec.get("field_name")
        or property_spec.get("field_ref")
    )
    if not field_path:
        return ()
    return tuple(part for part in field_path.split(".") if part)


def _typed_expression_constraint(
    property_spec: dict[str, Any],
) -> dict[str, Any]:
    expression = _dict(property_spec.get("expression"))
    if _text(expression.get("operator")) != "field_constraint":
        return {}
    operands = _list(expression.get("operands"))
    if len(operands) != 1 or not isinstance(operands[0], dict):
        raise ValueError("field_constraint_requires_exactly_one_typed_operand")
    operand = _dict(operands[0])
    tokens = operand.get("field_tokens")
    constraint = _text(operand.get("validation_constraint"))
    if (
        not isinstance(tokens, list)
        or not tokens
        or not all(
            isinstance(token, (str, int)) and not isinstance(token, bool)
            for token in tokens
        )
        or not constraint
    ):
        raise ValueError("field_constraint_operand_invalid")
    return {
        "field_tokens": list(tokens),
        "validation_constraint": constraint,
        "validation_constraint_value": deepcopy(
            operand.get("validation_constraint_value")
        ),
        "validation_constraint_source": "source_invariant",
    }


def _variant_id(
    obligation_id: str,
    tokens: tuple[str | int, ...],
    constraint: str,
) -> str:
    material = json.dumps(
        [obligation_id, list(tokens), constraint],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{obligation_id}__v_{digest}"


def _with_validation_effect_observer(
    obligation: dict[str, Any],
    *,
    include_business_effect: bool = True,
) -> dict[str, Any]:
    row = deepcopy(_dict(obligation))
    observers = [
        _text(value)
        for value in _list(row.get("required_observers"))
        if _text(value)
    ]
    observer_ids = ["http_response"]
    if include_business_effect:
        observer_ids.append("business_effect")
    for observer_id in observer_ids:
        if observer_id not in observers:
            observers.append(observer_id)
    row["required_observers"] = observers
    return row


def _schema_constraint_expansion_eligible(
    property_spec: dict[str, Any],
) -> bool:
    """Return whether this property requests schema-coverage fan-out.

    A source invariant already names the property to verify. Crossing it with
    every request-schema field changes its meaning and creates unrelated
    variants. Schema fan-out is authoritative only for the dedicated
    single-dimension template (or the legacy empty template used by callers
    that provide only an operation contract). Explicit field constraints are
    handled before this predicate.
    """

    prop = _dict(property_spec)
    template = _text(prop.get("template"))
    if template not in {"", "single_dimension_mutation"}:
        return False
    if any(
        _text(prop.get(field))
        for field in (
            "invariant_ref",
            "source_intent",
            "source_rule_ref",
            "source_rule_statement",
        )
    ):
        return False
    expression = _dict(prop.get("expression"))
    return not any(
        expression.get(field) not in (None, "", [], {})
        for field in ("kind", "operator", "operands", "raw")
    )


def expand_validation_obligation(
    obligation: dict[str, Any],
    *,
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return deterministic validation variants for nested documented fields."""

    obl = _dict(obligation)
    if _text(obl.get("risk_family")) != "validation":
        return [obl]
    guarded = _with_validation_effect_observer(
        obl,
        include_business_effect=(
            _infer_operation_effect(
                operation,
                _text(operation.get("method")).upper(),
            )
            == "write"
        ),
    )
    prop = _dict(guarded.get("property"))
    typed_constraint = _typed_expression_constraint(prop)
    if typed_constraint:
        prop = {**prop, **typed_constraint}
        guarded["property"] = prop
    explicit_tokens = _explicit_tokens(prop)
    if explicit_tokens and _text(prop.get("validation_constraint")):
        normalized = deepcopy(guarded)
        normalized_property = dict(prop)
        normalized_property.setdefault("field_tokens", list(explicit_tokens))
        normalized_property.setdefault("json_path", _json_path(explicit_tokens))
        normalized_property.setdefault(
            "field_path",
            _display_path(explicit_tokens),
        )
        normalized["property"] = normalized_property
        return [normalized]

    if not _schema_constraint_expansion_eligible(prop):
        return [guarded]

    schema = _body_schema(_dict(operation))
    example = _request_example(_dict(operation))
    nodes = _schema_nodes(schema, example) if schema and example else []
    if explicit_tokens:
        exact = [node for node in nodes if node[0] == explicit_tokens]
        if exact:
            nodes = exact
        elif len(explicit_tokens) == 1:
            leaf_matches = [
                node
                for node in nodes
                if node[0] and node[0][-1] == explicit_tokens[0]
            ]
            if len(leaf_matches) == 1:
                nodes = leaf_matches
            elif not nodes:
                nodes = []
            else:
                return [guarded]
        elif nodes:
            return [guarded]

    targets: list[
        tuple[tuple[str | int, ...], str, Any, str]
    ] = []
    for tokens, field_schema, example_value, required in nodes:
        for constraint, constraint_value in _constraint_targets(
            field_schema=field_schema,
            example_value=example_value,
            required=required,
        ):
            targets.append((tokens, constraint, constraint_value, "body"))

    for parameter in _parameter_entries(_dict(operation)):
        location = _text(parameter.get("location")) or "query"
        name = _text(parameter.get("name"))
        tokens = (f"@{location}", name)
        if explicit_tokens and tokens != explicit_tokens:
            if not (
                len(explicit_tokens) == 1
                and explicit_tokens[0] == name
            ):
                continue
        example_value = parameter.get("example")
        if example_value is None:
            continue
        for constraint, constraint_value in _constraint_targets(
            field_schema=_dict(parameter.get("schema")),
            example_value=example_value,
            required=bool(parameter.get("required")),
        ):
            targets.append((tokens, constraint, constraint_value, location))

    if not targets:
        return [guarded]

    original_id = (
        _text(guarded.get("obligation_id"))
        or "validation_obligation"
    )
    variants: list[dict[str, Any]] = []
    for tokens, constraint, constraint_value, location in targets:
        variant = deepcopy(guarded)
        variant["obligation_id"] = _variant_id(
            original_id,
            tokens,
            constraint,
        )
        leaf = tokens[-1] if tokens else ""
        variant_property = dict(prop)
        variant_property.update({
            "field": str(leaf),
            "field_path": _display_path(tokens),
            "field_tokens": list(tokens),
            "json_path": _json_path(tokens),
            "parameter_location": location,
            "validation_constraint": constraint,
            "validation_constraint_value": deepcopy(constraint_value),
            "validation_constraint_source": "request_schema",
            "expanded_from_obligation_id": original_id,
            "expected_rejection_status_class": 4,
            "expected_treatment_effect_count": 0,
        })
        variant["property"] = variant_property
        variant["compile_status"] = "PENDING"
        variants.append(variant)
    return variants
