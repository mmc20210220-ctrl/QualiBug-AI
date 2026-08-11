"""Protocol facade with fail-closed mutation and assertion authority.

The historical protocol facade lives in ``_experiment_protocols_mechanics``.
This layer preserves registered/built-in semantics while enforcing two final
compile authorities: validation mutations need an explicit source/schema basis,
and every COMPILED protocol must resolve an assertion kind either from its own
result or from the compiler's declared family map. A generic HTTP status check is
never allowed to stand in for a missing Oracle.
"""
from __future__ import annotations

import re
from typing import Any

from . import _experiment_protocols_mechanics as _core
from ._experiment_protocols_mechanics import *  # noqa: F401,F403

_original_compile_family_protocol = _core.compile_family_protocol

_SOURCE_RUNTIME_MUTATION_CLASSES = frozenset({
    "runtime_entity_state_violation",
    "runtime_amount_boundary_violation",
    "runtime_scope_violation",
    "runtime_account_state_violation",
})
_SOURCE_RULE_MUTATION_SOURCES = frozenset({
    "account_state_precondition",
})


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_semantic_text(property_spec: dict[str, Any]) -> str:
    prop = _dict(property_spec)
    expression = _dict(prop.get("expression"))
    return "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            prop.get("source_intent"),
            prop.get("description"),
            prop.get("source_rule_statement"),
        )
        if _text(value)
    )


def _source_bound_property(property_spec: dict[str, Any]) -> bool:
    prop = _dict(property_spec)
    return any(
        _text(prop.get(field))
        for field in (
            "invariant_ref",
            "source_rule_ref",
            "source_rule_statement",
            "source_intent",
        )
    )


def _mutation_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _list(result.get("treatment_plan")):
        if not isinstance(raw, dict):
            continue
        mutation = _dict(raw.get("mutation"))
        if mutation:
            rows.append(mutation)
    return rows


def _semantic_constraint_declared(
    constraint: str,
    semantic_text: str,
) -> bool:
    """Whether the source statement itself declares this semantic mutation."""

    constraint = _text(constraint).lower()
    text = _text(semantic_text).lower()
    if not constraint or not text:
        return False

    if "sql_injection_probe" in constraint:
        return any(
            token in text
            for token in (
                "参数化", "拼接", "注入", "sql", "parameterized",
                "injection", "concatenat",
            )
        )
    if "verification_code_mismatch" in constraint:
        return any(
            token in text
            for token in (
                "验证码", "校验码", "短信码", "otp",
                "verification code", "sms code",
            )
        )
    if "enum_value_not_allowed" in constraint:
        return bool(
            re.search(
                r"(?:只能|仅能|仅|必须|只|only|must)",
                text,
                re.IGNORECASE,
            )
        )
    if constraint in {"semantic:negative_value", "semantic:zero_quantity"}:
        return any(
            token in text
            for token in (
                "非负", "不能为负", "不得为负", "不允许为负",
                "大于0", "大于 0", "正数", "不能为0", "不得为0",
                "non-negative", "nonnegative", "must not be negative",
                "positive", "greater than zero",
            )
        )
    if constraint == "semantic:weak_password":
        return any(
            token in text
            for token in (
                "密码长度", "密码强度", "密码复杂", "口令长度",
                "password length", "password strength", "password complexity",
            )
        )
    if constraint == "semantic:invalid_email_format":
        return any(token in text for token in ("邮箱格式", "邮件格式", "email format"))
    if constraint == "semantic:invalid_phone_format":
        return any(token in text for token in ("手机号格式", "电话格式", "phone format", "mobile format"))
    if constraint == "semantic:invalid_date":
        return any(token in text for token in ("日期格式", "时间格式", "date format", "time format"))
    return False


def _assertion_authority_problem(
    *,
    result: dict[str, Any],
    risk_family: str,
) -> str:
    """A COMPILED protocol must have a real Oracle authority.

    The semantic compiler historically ended its selection with ``or
    'http_status'``. Combined with the generic protocol fallback, a newly
    registered family could therefore become canonical without declaring any
    assertion semantics and still look executable. The family registry stays
    open, but execution is blocked until either the protocol emits a concrete
    assertion kind or the compiler family map explicitly owns one.
    """

    if _text(result.get("status")) != "COMPILED":
        return ""
    if _text(_dict(result.get("assertion")).get("kind")):
        return ""
    family = _text(risk_family)
    if not family:
        return "protocol_assertion_kind_missing:empty_family"
    try:
        from . import experiment_compiler_obligation_core as _compiler_core

        mapped = _text(_compiler_core._FAMILY_ASSERTION_KIND.get(family))
    except Exception:  # pragma: no cover - import cycle stays fail-closed
        mapped = ""
    if mapped:
        return ""
    return f"protocol_assertion_kind_missing:{family}"


def _validation_authority_problem(
    *,
    result: dict[str, Any],
    property_spec: dict[str, Any],
) -> str:
    """Return a reason when a compiled validation mutation lacks authority."""

    if _text(result.get("status")) != "COMPILED":
        return ""
    prop = _dict(property_spec)
    explicit_constraint = _text(prop.get("validation_constraint"))
    explicit_source = _text(prop.get("validation_constraint_source"))
    if explicit_constraint:
        if explicit_source and explicit_source not in {
            "request_schema",
            "source_invariant",
        }:
            return "validation_constraint_lineage_invalid"
        return ""

    mutations = _mutation_rows(result)
    if not mutations:
        return "" if _source_bound_property(prop) else (
            "validation_protocol_has_no_constraint_or_source_authority"
        )

    semantic_text = _source_semantic_text(prop)
    source_bound = _source_bound_property(prop)
    for mutation in mutations:
        mutation_class = _text(mutation.get("class"))
        source = _text(mutation.get("source"))
        constraint = _text(mutation.get("constraint"))

        if mutation_class in _SOURCE_RUNTIME_MUTATION_CLASSES:
            if source_bound and semantic_text:
                continue
            return f"runtime_validation_mutation_lacks_source_rule:{mutation_class}"

        if source in _SOURCE_RULE_MUTATION_SOURCES:
            if source_bound and semantic_text:
                continue
            return f"source_validation_mutation_lacks_rule:{source}"

        if source == "inferred_from_example":
            return "request_example_inference_not_validation_authority"

        if source == "request_schema":
            if constraint.startswith("semantic:"):
                if source_bound and _semantic_constraint_declared(
                    constraint,
                    semantic_text,
                ):
                    continue
                return (
                    "schema_field_semantic_inference_not_authoritative:"
                    + constraint
                )
            return (
                "schema_constraint_requires_explicit_validation_projection:"
                + (constraint or "unknown")
            )

        return "validation_mutation_authority_unknown"
    return ""


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
    result = _original_compile_family_protocol(
        risk_family=risk_family,
        operation=operation,
        operation_ref=operation_ref,
        control_actor_ref=control_actor_ref,
        treatment_actor_ref=treatment_actor_ref,
        property_spec=property_spec,
        behavior_ir=behavior_ir,
    )

    assertion_problem = _assertion_authority_problem(
        result=result,
        risk_family=risk_family,
    )
    if assertion_problem:
        return {
            "status": "BLOCKED",
            "reason_code": "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
            "detail": assertion_problem,
            "assertion_authority_gate": {
                "status": "BLOCKED",
                "reason_code": assertion_problem,
                "generic_http_status_fallback_allowed": False,
            },
        }

    if _text(risk_family) != "validation":
        if _text(result.get("status")) == "COMPILED":
            result = dict(result)
            result["assertion_authority_gate"] = {
                "status": "PASS",
                "generic_http_status_fallback_allowed": False,
            }
        return result

    problem = _validation_authority_problem(
        result=result,
        property_spec=property_spec,
    )
    if not problem:
        if _text(result.get("status")) == "COMPILED":
            result = dict(result)
            result["assertion_authority_gate"] = {
                "status": "PASS",
                "generic_http_status_fallback_allowed": False,
            }
            result["validation_authority_gate"] = {
                "status": "PASS",
                "heuristic_request_example_authority": False,
                "schema_cross_product_enabled": False,
            }
        return result
    return {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_BINDING",
        "detail": problem,
        "assertion_authority_gate": {
            "status": "PASS",
            "generic_http_status_fallback_allowed": False,
        },
        "validation_authority_gate": {
            "status": "BLOCKED",
            "reason_code": problem,
            "heuristic_request_example_authority": False,
            "schema_cross_product_enabled": False,
        },
    }


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "compile_family_protocol",
    }
)
