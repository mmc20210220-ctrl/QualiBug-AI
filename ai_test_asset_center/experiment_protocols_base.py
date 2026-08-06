"""Family-specific executable experiment protocol compiler.

This module owns step semantics. The generic experiment compiler owns contract
assembly and blockers; it must never replace a missing family protocol with a
single status-code probe.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .oracle_expression_resolver import resolve_expression_from_invariant


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _minimal_body_from_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal default request body from the operation's schema properties."""
    schema = _dict(operation.get("request_schema") or operation.get("requestBody") or {})
    props = _dict(schema.get("properties"))
    if not props:
        props = _dict(_dict(schema.get("content", {})).get("application/json", {}).get("schema", {}).get("properties"))
    if not props:
        return {}
    body: dict[str, Any] = {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        prop_type = _text(prop.get("type") or "string")
        example = prop.get("example")
        if example is not None:
            body[name] = example
        elif prop_type == "string":
            body[name] = "test_value"
        elif prop_type in ("integer", "number"):
            body[name] = 1
        elif prop_type == "boolean":
            body[name] = True
        elif prop_type == "array":
            body[name] = []
        else:
            body[name] = {}
    return body


def source_request_example(
    operation: dict[str, Any],
    *,
    sibling_operations: list[Any] | None = None,
) -> dict[str, Any]:
    """Return an explicitly documented request example, never synthesized data."""

    from .experiment_compiler_support import _source_request_example

    example = _source_request_example(
        operation,
        sibling_operations=sibling_operations,
    )
    return deepcopy(example) if example else {}


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_schema = _dict(_dict(operation).get("request_schema"))
    # Prefer the schema that actually declares fields. A top-level
    # ``{"type": "object", "properties": {}}`` shell with the real field
    # declarations under ``content.<media>.schema`` must resolve to the
    # content schema, or every field-based protocol sees an empty field set
    # and blocks on a source schema that does declare fields.
    if _dict(request_schema.get("properties")):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema and _dict(schema.get("properties")):
            return schema
    if _text(request_schema.get("type")):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema:
            return schema
    return {}


def _generate_minimal_body_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal valid request body from a JSON Schema definition.

    Used as a fallback when no documented request example exists. The generated
    body uses type-appropriate default values for required fields.
    """
    if not isinstance(schema, dict):
        return {}
    properties = _dict(schema.get("properties"))
    if not properties:
        return {}
    required = [_text(v) for v in (schema.get("required") or []) if _text(v)]
    body: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        field_type = _text(field_schema.get("type")).lower()
        # Only populate required fields or fields needed for meaningful requests
        if field_name not in required and field_name not in properties:
            continue
        if field_type == "string":
            example = field_schema.get("example") or field_schema.get("default")
            body[field_name] = str(example) if example else "test_value"
        elif field_type == "integer":
            body[field_name] = int(field_schema.get("example") or field_schema.get("default") or 1)
        elif field_type == "number":
            body[field_name] = float(field_schema.get("example") or field_schema.get("default") or 1.0)
        elif field_type == "boolean":
            body[field_name] = True
        elif field_type == "array":
            body[field_name] = []
        elif field_type == "object":
            body[field_name] = {}
        else:
            body[field_name] = "test_value"
    return body


# ── Semantic invalid value heuristics (industry-neutral, field-name driven) ──
_NUMERIC_NEGATIVE_FIELDS = re.compile(
    r"(price|amount|total|balance|stock|quantity|qty|count|num|limit|quota|"
    r"weight|volume|rate|fee|cost|salary|wage|budget|credit|debit|payment|"
    r"refund|discount|tax|margin|profit|revenue|income|expense|"
    r"价格|金额|余额|库存|数量|限额|配额|费用|单价|总价|退款|优惠)",
    re.IGNORECASE,
)
_PASSWORD_FIELDS = re.compile(
    r"(pass(word|wd|phrase)?|pwd|secret|credential|密码|口令)", re.IGNORECASE
)
_EMAIL_FIELDS = re.compile(r"(e-?mail|邮箱|邮件地址)", re.IGNORECASE)
_PHONE_FIELDS = re.compile(r"(phone|mobile|tel|cell|手机|电话|联系方式)", re.IGNORECASE)
_DATE_FIELDS = re.compile(r"(date|time|_at$|_on$|日期|时间)", re.IGNORECASE)


def _semantic_invalid_value(
    field_name: str,
    declared_type: str,
    property_schema: dict[str, Any],
    semantic_text: str = "",
) -> tuple[Any, str] | None:
    """Generate a semantically invalid value based on field name/type heuristics.

    Returns (invalid_value, constraint_description) or None if no heuristic applies.
    Industry-neutral: uses common field-name patterns, never benchmark-specific values.
    """
    combined = f"{field_name} {semantic_text}".lower()

    # Numeric fields that should reject negative values
    if declared_type in ("integer", "number"):
        if _NUMERIC_NEGATIVE_FIELDS.search(combined):
            return -1, "semantic:negative_value"
        # Check for maximum constraint in schema
        maximum = property_schema.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            return maximum + 1, "semantic:exceeds_maximum"
        # Generic numeric boundary: zero for quantities
        if re.search(r"(quantity|qty|count|num|stock|数量|库存)", combined, re.IGNORECASE):
            return 0, "semantic:zero_quantity"

    # String fields with semantic constraints
    if declared_type == "string":
        if _PASSWORD_FIELDS.search(combined):
            return "1", "semantic:weak_password"
        if _EMAIL_FIELDS.search(combined):
            return "not-an-email", "semantic:invalid_email_format"
        if _PHONE_FIELDS.search(combined):
            return "0", "semantic:invalid_phone_format"
        if _DATE_FIELDS.search(combined):
            return "1900-13-99", "semantic:invalid_date"
        # Check for minLength constraint
        min_length = property_schema.get("minLength")
        if isinstance(min_length, int) and min_length > 1:
            return "x", "semantic:below_min_length"
        # Check for pattern constraint
        if property_schema.get("pattern"):
            return "!!!invalid!!!", "semantic:pattern_violation"

    return None


# Response-side constraint signals: the rule constrains what a response may
# carry (导出结果禁止包含 password) rather than a request body mutation.
# Generic Chinese business syntax — not industry-specific vocabulary.
_RESPONSE_SIDE_SIGNALS = ("导出", "结果", "响应", "返回", "输出")
_RESPONSE_FORBID_FIELD_RE = re.compile(
    r"(?:禁止|不得|不能|不允许|不可|不应)[^，,。；;\n]{0,20}?"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)


def _extract_forbidden_response_fields(property_spec: dict[str, Any]) -> list[str]:
    """Extract ASCII fields a response-side rule forbids in its output.

    A rule like 导出结果禁止包含 password 或其他认证凭据 names the forbidden
    field after a prohibition word; the identifier is source material, never
    a hardcoded name. Returns [] when the rule is not response-side (no
    export/result/response/return/output signal), so write-side validation
    keeps its existing body-mutation protocol.
    """
    expression = _dict(property_spec.get("expression"))
    raw = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    if not raw or not any(signal in raw for signal in _RESPONSE_SIDE_SIGNALS):
        return []
    fields = [
        match.group(1)
        for match in _RESPONSE_FORBID_FIELD_RE.finditer(raw)
    ]
    return list(dict.fromkeys(fields))


def _validation_protocol_material(
    operation: dict[str, Any],
    property_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    control = source_request_example(operation)
    schema = _request_body_schema(operation)
    if not schema:
        # No request body schema — but if we have a control body from
        # request_example, apply semantic invalid values using inferred types.
        if control and isinstance(control, dict):
            semantic_text_no_schema = "\n".join(
                _text(v)
                for v in (
                    _dict(property_spec.get("expression")).get("raw"),
                    property_spec.get("source_intent"),
                    property_spec.get("description"),
                )
                if _text(v)
            )
            # Try explicit target fields first, then all fields
            explicit_no_schema = [
                _text(v).removeprefix("$.")
                for v in (
                    property_spec.get("field"),
                    property_spec.get("field_name"),
                    property_spec.get("field_ref"),
                    property_spec.get("json_path"),
                    _dict(property_spec.get("expression")).get("field"),
                    _dict(property_spec.get("expression")).get("field_name"),
                )
                if _text(v)
            ]
            field_order_no_schema = [
                *[f for f in explicit_no_schema if f in control],
                *[f for f in control if f not in explicit_no_schema],
            ]
            for field in field_order_no_schema:
                val = control[field]
                if isinstance(val, bool):
                    inferred_type = "boolean"
                elif isinstance(val, int):
                    inferred_type = "integer"
                elif isinstance(val, float):
                    inferred_type = "number"
                elif isinstance(val, str):
                    inferred_type = "string"
                else:
                    continue
                result = _semantic_invalid_value(field, inferred_type, {}, semantic_text_no_schema)
                if result is not None:
                    invalid_value, constraint = result
                    treatment = deepcopy(control)
                    treatment[field] = invalid_value
                    return control, treatment, {
                        "json_path": f"$.{field}",
                        "constraint": constraint,
                        "source": "inferred_from_example",
                    }
            # No semantic match — fall back to removing first field
            if field_order_no_schema:
                field = field_order_no_schema[0]
                treatment = deepcopy(control)
                treatment.pop(field, None)
                return control, treatment, {
                    "json_path": f"$.{field}",
                    "constraint": "required_inferred",
                    "source": "inferred_from_example",
                }
        # No control body or no fields — use synthetic fallback
        method = _text(operation.get("method", "")).upper()
        if method in ("PATCH", "PUT"):
            control = {"status": "active"}
            return control, {}, {"json_path": "$.status", "constraint": "synthetic", "source": "synthetic_fallback"}
        if method == "POST":
            control = {}
            return control, {}, {"json_path": "$", "constraint": "synthetic", "source": "synthetic_fallback"}
        if method == "DELETE":
            # DELETE has no body — the validation test checks the HTTP response
            control = {}
            return control, {}, {"json_path": "$", "constraint": "synthetic", "source": "synthetic_fallback_delete"}
        return {}, {}, {}
    if not control:
        # No documented example — generate a minimal valid body from the schema.
        # This is a best-effort fallback: the generated body may not exercise all
        # business rules, but it allows the obligation to compile and execute,
        # which is better than blocking the entire discovery pipeline.
        control = _generate_minimal_body_from_schema(schema)
        if not control:
            return {}, {}, {}
    properties = _dict(schema.get("properties"))

    explicit_targets: list[str] = []
    expression = _dict(property_spec.get("expression"))
    direct_values = [
        property_spec.get("field"),
        property_spec.get("field_name"),
        property_spec.get("field_ref"),
        property_spec.get("json_path"),
        expression.get("field"),
        expression.get("field_name"),
        expression.get("field_ref"),
        expression.get("json_path"),
    ]
    semantic_text = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    for field in properties:
        normalized_direct = {
            _text(value).removeprefix("$.")
            for value in direct_values
            if _text(value)
        }
        if field in normalized_direct or re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(str(field))}(?![A-Za-z0-9_])",
            semantic_text,
        ):
            explicit_targets.append(str(field))

    # ── Strategy 1: semantic invalid value (catches negative price, weak password, etc.) ──
    semantic_field_order = [
        *explicit_targets,
        *[str(f) for f in properties if str(f) not in explicit_targets],
    ]
    for field in semantic_field_order:
        if field not in control:
            continue
        raw_property = properties.get(field)
        property_schema = _dict(raw_property)
        declared_type = _text(property_schema.get("type")).lower()
        if not declared_type:
            # Infer type from control value
            val = control[field]
            if isinstance(val, bool):
                declared_type = "boolean"
            elif isinstance(val, int):
                declared_type = "integer"
            elif isinstance(val, float):
                declared_type = "number"
            elif isinstance(val, str):
                declared_type = "string"
            else:
                continue
        result = _semantic_invalid_value(field, declared_type, property_schema, semantic_text)
        if result is not None:
            invalid_value, constraint = result
            treatment = deepcopy(control)
            treatment[field] = invalid_value
            return control, treatment, {
                "json_path": f"$.{field}",
                "constraint": constraint,
                "source": "request_schema",
            }

    # ── Strategy 2: remove required field ──
    required = [
        _text(value)
        for value in (schema.get("required") or [])
        if _text(value)
    ]
    required_order = [
        *[field for field in explicit_targets if field in required],
        *[field for field in required if field not in explicit_targets],
    ]
    for field in required_order:
        if field not in control:
            continue
        treatment = deepcopy(control)
        treatment.pop(field, None)
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": "required",
            "source": "request_schema",
        }

    # ── Strategy 3: type mismatch ──
    def matches_declared_type(value: Any, declared_type: str) -> bool:
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

    field_order = [
        *explicit_targets,
        *[str(field) for field in properties if str(field) not in explicit_targets],
    ]
    for field in field_order:
        raw_property = properties.get(field)
        property_schema = _dict(raw_property)
        declared_type = _text(property_schema.get("type")).lower()
        if field not in control or not matches_declared_type(control[field], declared_type):
            continue
        invalid_value: Any = [] if declared_type == "object" else {} if declared_type != "null" else True
        treatment = deepcopy(control)
        treatment[field] = invalid_value
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": f"type:{declared_type}",
            "source": "request_schema",
        }
    return {}, {}, {}


def compile_family_protocol(
    *,
    risk_family: str,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile exact family steps or return one typed blocker."""

    family = _text(risk_family)
    method = _text(operation.get("method")).upper()
    template = _text(property_spec.get("template"))
    sibling_operations = (
        _list(_dict(behavior_ir).get("operations")) if behavior_ir else []
    )
    # Source-grounded permitted invocation: one actor, observe the documented
    # operation. Used when IR has permits but no executable deny pair — must
    # not invent a second actor or silently drop the module from scheduling.
    if template == "permitted_operation_invocation":
        actor = control_actor_ref or treatment_actor_ref or _text(
            property_spec.get("actor_ref")
        )
        if not actor:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": "permitted_actor",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": actor,
                "operation_ref": operation_ref,
                "intent": "permitted_operation_invocation",
                "protocol_step": "permitted_invocation",
                "property_template": template,
            }],
            "assertion": {
                "kind": "http_status_class",
                "expected_class": 2,
                "compare_field": "status_code",
            },
        }
    needs_control = family in {
        "authorization",
        "isolation",
        "validation",
        "privacy",
        "visibility",
    }
    if needs_control and not control_actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "control_actor",
        }
    if not treatment_actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "treatment_actor",
        }

    if family == "idempotency":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "idempotency_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "idempotency_initial_write",
                "protocol_step": "initial_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "idempotency_repeat_write",
                "protocol_step": "repeat_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
        }

    if family == "concurrency":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "concurrency_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        barrier_group = f"barrier:{operation_ref}"
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "concurrency_participant_control",
                "protocol_step": "concurrent_write",
                "barrier_group": barrier_group,
                "barrier_participant": "control",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "concurrency_participant_treatment",
                "protocol_step": "concurrent_write",
                "barrier_group": barrier_group,
                "barrier_participant": "treatment",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
        }

    if family == "conservation":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "conservation_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        expression = _dict(property_spec.get("expression"))
        equation = _dict(property_spec.get("equation") or expression.get("equation"))
        # Prefer structured operands over NL guessing when present.
        if not equation:
            _op_terms: list[str] = []
            for _op in _list(expression.get("operands")):
                if not isinstance(_op, dict):
                    continue
                _f = _text(_op.get("field_id") or _op.get("field"))
                if _f:
                    _op_terms.append(_f)
            if _op_terms:
                equation = {
                    "operator": _text(expression.get("operator")) or "unchanged_sum",
                    "terms": list(dict.fromkeys(_op_terms)),
                }
        # V1.6.0: never invent conservation terms via NL guessing when structure
        # is empty. Empty terms must block before planner/executor/oracle.
        _term_rows = [
            t for t in _list(equation.get("terms") or equation.get("fields"))
            if _text(t) or isinstance(t, dict)
        ]
        if not equation or not _term_rows:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
                "detail": "conservation_requires_non_empty_equation_terms",
            }
        # Prefer JSON field names over cf_* for observer/assertion key alignment.
        _name_by_cf: dict[str, str] = {}
        for _op in _list(expression.get("operands")):
            if isinstance(_op, dict) and _text(_op.get("field_id")) and _text(_op.get("field")):
                _name_by_cf[_text(_op.get("field_id"))] = _text(_op.get("field"))
        _normalized_terms: list[str] = []
        for _t in _term_rows:
            if isinstance(_t, dict):
                _normalized_terms.append(
                    _text(_t.get("field") or _t.get("field_id"))
                )
            else:
                _tt = _text(_t)
                _normalized_terms.append(_name_by_cf.get(_tt, _tt))
        _normalized_terms = [t for t in _normalized_terms if t]
        if not _normalized_terms:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
                "detail": "conservation_requires_non_empty_equation_terms",
            }
        equation = {
            **equation,
            "terms": list(dict.fromkeys(_normalized_terms)),
            "operator": _text(equation.get("operator")) or "unchanged_sum",
        }
        _cons_assertion: dict[str, Any] = {
            "kind": "conservation",
            "equation": equation,
            "operands": _list(expression.get("operands")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
            "rule_id": _text(property_spec.get("invariant_ref")),
        }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "conservation_mutation",
                "protocol_step": "conservation_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            }],
            "observers": [
                {"observer_id": "business_effect"},
                {"observer_id": "entity_state"},
            ],
            "assertion": _cons_assertion,
        }

    if family == "temporal":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "temporal_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "temporal_requires_source_request_example",
            }
        expression = _dict(property_spec.get("expression"))
        window_ms = expression.get("window_ms") or property_spec.get("window_ms")
        # Date-range temporal: expression has date_field/bounds but no window_ms
        date_field = _text(expression.get("date_field") or expression.get("field") or expression.get("start_date"))
        has_date_bounds = bool(
            expression.get("bounds")
            or expression.get("start")
            or expression.get("end")
            or expression.get("min")
            or expression.get("max")
            or expression.get("from")
            or expression.get("to")
        )
        if date_field and has_date_bounds:
            # Date-range temporal boundary experiment
            return {
                "status": "COMPILED",
                "control_plan": [{
                    "step_id": "control_1",
                    "actor_ref": control_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "valid_source_control",
                    "protocol_step": "positive_control",
                    "body": deepcopy(body),
                }],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "temporal_date_boundary_mutation",
                    "protocol_step": "temporal_date_write",
                    "body": deepcopy(body),
                    "date_field": date_field,
                    "property_template": _text(property_spec.get("template")),
                    "invariant_ref": _text(property_spec.get("invariant_ref")),
                }],
                "observers": [{"observer_id": "http_response"}, {"observer_id": "entity_state"}],
                "assertion": {
                    "kind": "temporal_date_boundary",
                    "date_field": date_field,
                    "bounds": expression.get("bounds") or {
                        k: expression[k]
                        for k in ("start", "end", "min", "max", "from", "to")
                        if k in expression
                    },
                },
            }
        if not isinstance(window_ms, (int, float)) or isinstance(window_ms, bool) or window_ms <= 0:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ASSERTION",
                "detail": "temporal_requires_positive_source_window_ms",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "temporal_mutation",
                "protocol_step": "temporal_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            }],
            "observers": [{"observer_id": "temporal_window"}],
            "assertion": {
                "kind": "eventual_consistency",
                "window_ms": window_ms,
            },
        }

    if family == "validation":
        # ── Response-side constraint protocol ──
        # A source rule constraining RESPONSE content (导出结果禁止包含
        # password 或其他认证凭据) binds a read operation and asserts the
        # forbidden field is absent from the observed body — a single-arm
        # observation, never a write mutation. The forbidden fields come
        # from the rule's own text (ASCII identifiers after a prohibition
        # word), so the protocol is language- and industry-neutral.
        _forbidden_fields = _extract_forbidden_response_fields(property_spec)
        if _forbidden_fields and method in {"GET", "HEAD"}:
            return {
                "status": "COMPILED",
                "control_plan": [{
                    "step_id": "control_1",
                    "actor_ref": control_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "response_side_constraint_observation",
                    "protocol_step": "positive_control",
                }],
                "treatment_plan": [],
                "assertion": {
                    "kind": "response_field_absent",
                    "fields": _forbidden_fields,
                },
            }
        parameter_location = _text(property_spec.get("parameter_location")).lower()
        tokens = property_spec.get("field_tokens")
        if (
            not parameter_location
            and isinstance(tokens, list)
            and tokens
            and isinstance(tokens[0], str)
            and str(tokens[0]).startswith("@")
        ):
            parameter_location = str(tokens[0])[1:].lower()
        allows_non_body = parameter_location in {"query", "path", "header"}
        if method not in {"POST", "PUT", "PATCH", "DELETE"} and not (
            allows_non_body and method in {"GET", "HEAD"}
        ):
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "validation_body_protocol_requires_write_operation",
            }
        if allows_non_body and method in {"GET", "HEAD"}:
            # Parameter-only mutations compile through the privacy facade; emit a
            # placeholder COMPILED shell that the facade rewrites with query/path.
            return {
                "status": "COMPILED",
                "control_plan": [{
                    "step_id": "control_1",
                    "actor_ref": control_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "valid_source_control",
                    "protocol_step": "positive_control",
                }],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "single_constraint_mutation",
                    "protocol_step": "single_mutation",
                }],
                "assertion": {
                    "kind": "http_status_class",
                    "expected_class": 4,
                    "compare_field": "status_code",
                },
            }
        control_body, treatment_body, mutation = _validation_protocol_material(
            operation,
            property_spec,
        )
        if not mutation:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "validation_requires_source_example_and_request_schema",
            }
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref,
                "operation_ref": operation_ref,
                "intent": "valid_source_control",
                "protocol_step": "positive_control",
                "body": deepcopy(control_body),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "single_constraint_mutation",
                "protocol_step": "single_mutation",
                "body": deepcopy(treatment_body),
                "mutation": mutation,
            }],
            "assertion": {
                "kind": "http_status_class",
                "expected_class": 4,
                "compare_field": "status_code",
            },
        }

    if family == "state":
        # ── Phase 2: postcondition-driven structured assertion ──
        # When the property_spec carries a postcondition expression, emit a
        # typed postcondition assertion (entity.field must_become expected_value)
        # instead of the generic state_transition assertion.
        _expr = _dict(property_spec.get("expression"))
        _expr_kind = _text(_expr.get("kind"))
        if _expr_kind == "postcondition":
            # ── P0-5: detect field_delta operands for causal verification ──
            _pc_operands = _list(_expr.get("operands"))
            _has_delta_fields = any(
                isinstance(op, dict)
                and (op.get("expected_delta") is not None or _text(op.get("expected_delta_direction")))
                for op in _pc_operands
            )
            _assertion_kind = "field_delta" if _has_delta_fields else "postcondition"
            return {
                "status": "COMPILED",
                "control_plan": [],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "state_transition_treatment",
                    "protocol_step": "treatment",
                }],
                "observers": [
                    {"observer_id": "before_state"},
                    {"observer_id": "after_state"},
                    {"observer_id": "entity_state"},
                ],
                "assertion": {
                    "kind": _assertion_kind,
                    "operator": _text(_expr.get("operator")),
                    "operands": _pc_operands,
                    "fields": _pc_operands if _has_delta_fields else [],
                },
            }
        # ── Cross-entity state consistency: resolve from raw + IR when no explicit states ──
        _state_from = _text(property_spec.get("from_state_ref") or property_spec.get("from_state"))
        _state_to = _text(property_spec.get("to_state_ref") or property_spec.get("to_state"))
        # V1.6.1: lift concrete from/to from expression operands (forbidden_state_transition).
        if not _state_from or not _state_to:
            for _op in _list(_expr.get("operands")):
                if not isinstance(_op, dict):
                    continue
                if not _state_from:
                    _state_from = _text(_op.get("from_state") or _op.get("from_state_ref"))
                if not _state_to:
                    _state_to = _text(_op.get("to_state") or _op.get("to_state_ref"))
        _state_resolved: dict[str, Any] = {}
        if not _state_from and not _state_to and behavior_ir:
            _state_inv = {
                "expression": _expr,
                "description": _text(
                    property_spec.get("template")
                    or _expr.get("raw")
                    or property_spec.get("invariant_ref")
                ),
            }
            _state_rr = resolve_expression_from_invariant(_state_inv, behavior_ir, operation=operation)
            if _state_rr.get("status") == "RESOLVED":
                _state_resolved = _state_rr
        if _state_resolved:
            _st_assertion: dict[str, Any] = {
                "kind": "cross_entity_consistency",
                "structured_expression": _state_resolved.get("expression", {}),
                "entity_bindings": _state_resolved.get("entity_bindings", {}),
                "join_plan": _state_resolved.get("join_plan", {}),
                "observer_requirements": _state_resolved.get("observer_requirements", []),
                "scope_fields": _state_resolved.get("scope_fields", []),
                "expression_type": _state_resolved.get("expression_type", ""),
                "root_entity": _state_resolved.get("root_entity", ""),
                "related_entities": _state_resolved.get("related_entities", []),
            }
            return {
                "status": "COMPILED",
                "control_plan": [],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "cross_entity_state_treatment",
                    "protocol_step": "treatment",
                }],
                "observers": [
                    {"observer_id": "before_state"},
                    {"observer_id": "after_state"},
                    {"observer_id": "entity_state"},
                ],
                "assertion": _st_assertion,
            }
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "state_transition_control",
                "protocol_step": "positive_control",
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "state_transition_treatment",
                "protocol_step": "treatment",
            }],
            "observers": [
                {"observer_id": "before_state"},
                {"observer_id": "after_state"},
            ],
            "assertion": {
                "kind": (
                    "forbidden_state_transition"
                    if _text(_expr.get("kind")) == "forbidden_state_transition"
                    or _text(_expr.get("operator")).lower() == "must_not_transition"
                    else "state_transition"
                ),
                "from_state": _state_from,
                "to_state": _state_to,
                "operator": _text(_expr.get("operator")) or "must_transition",
                "operands": _list(_expr.get("operands")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
                "rule_id": _text(property_spec.get("invariant_ref")),
            },
        }

    if family == "conservation":
        # Dead-path safeguard: the live conservation branch returns earlier.
        # Keep fail-closed here so reordering cannot reintroduce NL guessing.
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
            "detail": "conservation_requires_non_empty_equation_terms",
        }

    write_body: dict[str, Any] = {}
    if (
        family in {"authorization", "isolation", "visibility"}
        and method in {"POST", "PUT", "PATCH"}
    ):
        write_body = source_request_example(operation, sibling_operations=sibling_operations)
        if not write_body and not property_spec.get("defer_write_body_to_runtime"):
            write_body = _minimal_body_from_schema(operation)

    control_plan: list[dict[str, Any]] = []
    if needs_control:
        control_step = {
            "step_id": "control_1",
            "actor_ref": control_actor_ref,
            "operation_ref": operation_ref,
            "intent": "authorized_control",
            "protocol_step": "positive_control",
        }
        if write_body:
            control_step["body"] = deepcopy(write_body)
        control_plan.append(control_step)
    treatment_step = {
        "step_id": "treatment_1",
        "actor_ref": treatment_actor_ref,
        "operation_ref": operation_ref,
        "intent": "treatment",
        "protocol_step": "treatment",
        "property_template": _text(property_spec.get("template")),
    }
    if write_body:
        treatment_step["body"] = deepcopy(write_body)
    ownership_param = _text(property_spec.get("ownership_param"))
    ownership_location = _text(property_spec.get("ownership_param_location")).lower()
    identity_target = _text(property_spec.get("identity_binding_target")) or "user_id"
    if family == "isolation" and ownership_param and identity_target:
        placeholder = "{" + identity_target + "}"
        if ownership_location == "query":
            treatment_step["query"] = {ownership_param: placeholder}
        elif ownership_location == "path":
            treatment_step["path_params"] = {ownership_param: placeholder}
        elif ownership_location == "header":
            treatment_step["headers"] = {ownership_param: placeholder}
        elif ownership_location == "body":
            body = dict(_dict(treatment_step.get("body")))
            # Nested ownership binders use dotted paths from schema walk.
            if "." in ownership_param:
                tokens = [part for part in ownership_param.split(".") if part]
                cursor: Any = body
                for token in tokens[:-1]:
                    nested = cursor.get(token)
                    if not isinstance(nested, dict):
                        nested = {}
                        cursor[token] = nested
                    cursor = nested
                if tokens:
                    cursor[tokens[-1]] = placeholder
            else:
                body[ownership_param] = placeholder
            treatment_step["body"] = body
        else:
            body = dict(_dict(treatment_step.get("body")))
            body[ownership_param] = placeholder
            treatment_step["body"] = body
    return {
        "status": "COMPILED",
        "control_plan": control_plan,
        "treatment_plan": [treatment_step],
    }
