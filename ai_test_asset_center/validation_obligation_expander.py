"""Public validation-obligation expansion with semantic boundary preservation.

The frozen schema expander remains the authority for source-declared JSON Schema
constraints. This facade only preserves generic semantic mutations that the
protocol compiler already supports, so schema-derived type variants cannot hide
an executable field-meaning boundary such as email format or password strength.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import _validation_obligation_expander_core as _core
from .behavior_ir_core import _infer_operation_effect
from .experiment_protocols_base import _semantic_invalid_value
from .validation_parameter_authority import install_validation_parameter_authority
from .request_header_transport_authority import (
    install_request_header_transport_authority,
)


# The formal expander, protocol materializer and RequestBuildContract must share
# the same parameter-control and transport-header authorities before the first
# variant is produced. This also ensures the compile-time request fingerprint
# uses the same header truth as a fresh-process runtime rebuild.
install_validation_parameter_authority()
install_request_header_transport_authority()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _semantic_body_targets(
    nodes: list[tuple[tuple[str | int, ...], dict[str, Any], Any, bool]],
) -> list[tuple[tuple[str | int, ...], str, Any, str]]:
    """Return executable semantic mutations for source-documented body fields."""

    exact_constraint_keys = {
        "enum",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "exclusiveMinimum",
        "maximum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
    }
    targets: list[tuple[tuple[str | int, ...], str, Any, str]] = []
    for tokens, field_schema, example_value, _required in nodes:
        # The existing protocol mutates top-level JSON body fields faithfully.
        # Nested/query transports stay with the formal source-schema expander.
        if len(tokens) != 1 or not isinstance(tokens[0], str):
            continue
        # Exact source constraints outrank field-identity inference.
        if exact_constraint_keys.intersection(field_schema):
            continue
        declared_type = _text(field_schema.get("type")).lower()
        if not declared_type:
            if isinstance(example_value, bool):
                declared_type = "boolean"
            elif isinstance(example_value, int):
                declared_type = "integer"
            elif isinstance(example_value, float):
                declared_type = "number"
            elif isinstance(example_value, str):
                declared_type = "string"
            elif isinstance(example_value, list):
                declared_type = "array"
            elif isinstance(example_value, dict):
                declared_type = "object"
        semantic = _semantic_invalid_value(
            tokens[0],
            declared_type,
            field_schema,
        )
        if semantic is None:
            continue
        invalid_value, constraint = semantic
        if invalid_value == example_value:
            continue
        targets.append((tokens, constraint, deepcopy(invalid_value), "body"))
    return targets


def _semantic_variants(
    obligation: dict[str, Any],
    *,
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    obl = _core._dict(obligation)
    if _text(obl.get("risk_family")) != "validation":
        return []
    guarded = _core._with_validation_effect_observer(
        obl,
        include_business_effect=(
            _infer_operation_effect(
                operation,
                _text(operation.get("method")).upper(),
            )
            == "write"
        ),
    )
    prop = _core._dict(guarded.get("property"))
    typed_constraint = _core._typed_expression_constraint(prop)
    if typed_constraint:
        return []
    explicit_tokens = _core._explicit_tokens(prop)
    if explicit_tokens and _text(prop.get("validation_constraint")):
        return []
    if not _core._schema_constraint_expansion_eligible(prop):
        return []

    schema = _core._body_schema(_core._dict(operation))
    example = _core._request_example(_core._dict(operation))
    nodes = _core._schema_nodes(schema, example) if schema and example else []
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
            elif nodes:
                return []
        elif nodes:
            return []

    original_id = (
        _text(guarded.get("obligation_id"))
        or "validation_obligation"
    )
    variants: list[dict[str, Any]] = []
    for tokens, constraint, invalid_value, location in _semantic_body_targets(nodes):
        variant = deepcopy(guarded)
        variant["obligation_id"] = _core._variant_id(
            original_id,
            tokens,
            constraint,
        )
        leaf = tokens[-1] if tokens else ""
        variant_property = dict(prop)
        variant_property.update({
            "field": str(leaf),
            "field_path": _core._display_path(tokens),
            "field_tokens": list(tokens),
            "json_path": _core._json_path(tokens),
            "parameter_location": location,
            "expanded_from_obligation_id": original_id,
            "expected_rejection_status_class": 4,
            "expected_treatment_effect_count": 0,
            # Keep semantic identity distinct from exact JSON Schema authority.
            # The existing protocol compiler therefore uses its established
            # semantic mutation path rather than the formal type branch.
            "semantic_validation_constraint": constraint,
            "semantic_invalid_value": invalid_value,
            "semantic_validation_source": "documented_field_identity",
        })
        variant["property"] = variant_property
        variant["compile_status"] = "PENDING"
        variants.append(variant)
    return variants


def _is_unexpanded_fallback(
    row: dict[str, Any],
    obligation: dict[str, Any],
) -> bool:
    prop = _core._dict(row.get("property"))
    return (
        _text(row.get("obligation_id"))
        == _text(_core._dict(obligation).get("obligation_id"))
        and not _text(prop.get("expanded_from_obligation_id"))
        and not _text(prop.get("validation_constraint"))
    )


def expand_validation_obligation(
    obligation: dict[str, Any],
    *,
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expand formal constraints while keeping existing semantic paths reachable."""

    formal = _core.expand_validation_obligation(
        obligation,
        operation=operation,
    )
    semantic = _semantic_variants(
        obligation,
        operation=operation,
    )
    if not semantic:
        return formal
    # A lone unexpanded row is the core's "no formal target" sentinel, not a
    # separate executable variant. Semantic variants replace only that sentinel.
    formal = [
        row
        for row in formal
        if not _is_unexpanded_fallback(row, obligation)
    ]
    # Meaningful same-type boundaries run before generic wrong-type checks, while
    # every source-declared formal variant remains present and deterministic.
    return [*semantic, *formal]
