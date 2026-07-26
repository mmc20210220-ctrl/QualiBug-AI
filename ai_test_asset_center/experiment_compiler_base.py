"""Compile TestObligations into ExecutableExperiments or explicit blocks.

Single-obligation compile lives in ``experiment_compiler_obligation``; binding
helpers live in ``experiment_compiler_support``. This module keeps batch
``compile_experiments`` and re-exports the public compile surface.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .real_id_resolver import normalize_path_placeholders
from .validation_obligation_expander import expand_validation_obligation
from .experiment_compiler_support import (  # noqa: F401
    _actor_is_executable,
    _compensates_create_operation,
    _field_key,
    _index_by_id,
    _inverse_delta_cleanup_spec,
    _is_unresolvable_actor_secret_ref,
    _operation_entity_refs,
    _post_action_can_restore_named_terminal_field,
    _resolve_state_compile_context,
    _source_declared_control_fixture_binding,
    _source_request_example,
    _state_match_token,
    _state_semantic_value,
)
from .experiment_compiler_obligation import (  # noqa: F401
    BLOCK_REASONS,
    SCHEMA_VERSION,
    blocked_experiment,
    compile_experiment_for_obligation,
    make_experiment,
    stable_experiment_id,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def compile_experiments(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    compile_one: Callable[..., dict[str, Any]] | None = None,
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    compiler = compile_one or compile_experiment_for_obligation
    compiled: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    operations = _index_by_id(_list(_dict(behavior_ir).get("operations")))
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        prop = _dict(obl.get("property"))
        operation_ref = (
            next(
                (
                    _text(value)
                    for value in _list(obl.get("required_operations"))
                    if _text(value)
                ),
                "",
            )
            or _text(prop.get("operation_ref"))
        )
        # Resolve mismatched operation IDs by method+path from source locators
        if operation_ref and operation_ref not in operations:
            _src_locators = [
                _text(s.get("locator")) for s in _list(obl.get("source_refs"))
                if isinstance(s, dict) and _text(s.get("kind")) == "api_operation"
                and _text(s.get("locator"))
            ]
            for loc in _src_locators:
                parts = loc.split(None, 1)
                if len(parts) == 2:
                    loc_method, loc_path = parts[0].upper(), parts[1].strip()
                    for ir_id, ir_op in operations.items():
                        if (isinstance(ir_op, dict)
                            and _text(ir_op.get("method")).upper() == loc_method
                            and normalize_path_placeholders(_text(ir_op.get("path") or ir_op.get("raw_path")))
                            == normalize_path_placeholders(loc_path)):
                            operation_ref = ir_id
                            break
                if operation_ref in operations:
                    break
        variants = expand_validation_obligation(
            obl,
            operation=operations.get(operation_ref) or {},
        )
        variant_compiled = 0
        variant_blocked = 0
        for variant in variants:
            experiment = compiler(
                variant,
                behavior_ir=behavior_ir,
                environment_type=environment_type,
                policy_version=policy_version,
                available_adapters=available_adapters,
            )
            receipt = _dict(experiment.get("compile_receipt"))
            if _text(receipt.get("status")) == "COMPILED":
                compiled.append(experiment)
                variant["compile_status"] = "COMPILED"
                variant_compiled += 1
            else:
                blocked.append(experiment)
                variant["compile_status"] = "BLOCKED"
                variant["block_reason"] = receipt.get("reason_code")
                variant_blocked += 1
        obl["compile_status"] = "COMPILED" if variant_compiled else "BLOCKED"
        obl["expanded_experiment_count"] = len(variants)
        obl["compiled_experiment_count"] = variant_compiled
        obl["blocked_experiment_count"] = variant_blocked
        if not variant_compiled and blocked:
            obl["block_reason"] = _dict(blocked[-1].get("compile_receipt")).get(
                "reason_code"
            )
    return {
        "schema_version": "qualibug.experiment-compile.v1",
        "compiled_count": len(compiled),
        "blocked_count": len(blocked),
        "experiments": compiled,
        "blocked_experiments": blocked,
        "block_reason_counts": _count_reasons(blocked),
    }


def _count_reasons(blocked: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in blocked:
        code = (
            _text(_dict(item.get("compile_receipt")).get("reason_code"))
            or "UNKNOWN"
        )
        counts[code] = counts.get(code, 0) + 1
    return counts
