"""Protocol facade with fail-closed operation, mutation and assertion authority.

The historical protocol facade lives in ``_experiment_protocols_mechanics``.
This layer preserves registered/built-in semantics while enforcing three final
compile authorities:

* every transport step must reference a source operation whose HTTP method is
  declared and must not drift from that method;
* validation mutations need an explicit source/schema basis; and
* every COMPILED protocol must resolve an assertion kind. A generic HTTP status
  check never stands in for a missing Oracle.
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


def _protocol_operation_contract_problem(
    *,
    result: dict[str, Any],
    operation: dict[str, Any],
    operation_ref: str,
    behavior_ir: dict[str, Any] | None,
) -> str:
    """Return a problem when a compiled step invents or drifts operation truth."""

    if _text(result.get("status")) != "COMPILED":
        return ""
    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    supplied = _dict(operation)
    supplied_ref = _text(
        supplied.get("id") or supplied.get("operation_id") or operation_ref
    )
    if supplied_ref and supplied:
        operations.setdefault(supplied_ref, supplied)

    for phase in ("control", "treatment"):
        for raw in _list(result.get(f"{phase}_plan")):
            step = _dict(raw)
            if not step or _text(step.get("protocol_step")) == "ui_open":
                continue
            step_ref = _text(step.get("operation_ref"))
            source = _dict(operations.get(step_ref))
            if not step_ref or not source:
                return f"protocol_operation_unresolved:{phase}:{step_ref or 'missing'}"
            source_method = _text(source.get("method")).upper()
            if not source_method:
                return f"protocol_operation_method_missing:{phase}:{step_ref}"
            step_method = _text(step.get("method")).upper()
            if step_method and step_method != source_method:
                return (
                    f"protocol_operation_method_drift:{phase}:{step_ref}:"
                    f"step={step_method}:source={source_method}"
                )
    return ""


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
        # Closed token lists missed common phrasings (库存必须≥0 / 不能小于
        # 零 / cannot be negative …) and rejected 763 schema-inferred
        # negative-value probes in CMP_77d5dfe1 r7. Symbolic numeric-bound
        # detection generalizes the declaration match while staying
        # fail-closed: text that does not express a zero/negative boundary
        # still rejects.
        compact = re.sub(r"\s+", "", text)
        negative_declared = any(
            token in text.lower()
            for token in (
                "非负", "不能为负", "不得为负", "不允许为负",
                "non-negative", "nonnegative", "must not be negative",
                "cannot be negative", "should not be negative",
                "positive", "greater than zero",
            )
        ) or bool(
            re.search(r"(?:不能|不得|不可|不允许|禁止|避免).{0,6}(?:为|是|出现|小于|低于|少于)?(?:负|零)", compact)
        ) or bool(
            re.search(r"(?:大于|高于|超过|至少|最低|最少)(?:等于)?0", compact)
        ) or bool(
            re.search(r"(?:≥|>=|>|＞)\s*0", compact)
        )
        if constraint == "semantic:negative_value":
            return negative_declared
        # zero_quantity: additionally accepts explicit nonzero declarations
        zero_declared = any(
            token in text.lower()
            for token in (
                "不能为0", "不得为0", "不允许为0", "非0", "不为0",
                "非零", "nonzero", "non-zero", "not zero",
                "must not be zero",
            )
        ) or bool(
            re.search(r"(?:≠|!=|<>)\s*0", compact)
        ) or bool(
            re.search(r"(?:不能|不得|不可|不允许).{0,4}等于?\s*0", compact)
        )
        return negative_declared or zero_declared
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
    """A COMPILED protocol must have a real Oracle authority."""

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

    operation_problem = _protocol_operation_contract_problem(
        result=result,
        operation=operation,
        operation_ref=operation_ref,
        behavior_ir=behavior_ir,
    )
    if operation_problem:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": operation_problem,
            "operation_authority_gate": {
                "status": "BLOCKED",
                "reason_code": operation_problem,
                "implicit_method_default_allowed": False,
            },
        }

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
            result["operation_authority_gate"] = {
                "status": "PASS",
                "implicit_method_default_allowed": False,
            }
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
            result["operation_authority_gate"] = {
                "status": "PASS",
                "implicit_method_default_allowed": False,
            }
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
        "operation_authority_gate": {
            "status": "PASS",
            "implicit_method_default_allowed": False,
        },
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
        "_protocol_operation_contract_problem",
    }
)
