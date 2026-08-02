"""Compile TestObligations into frozen ExecutableExperiments.

The existing single-obligation compiler remains the semantic authority. This
module applies final process-graph write safety, flow/readback, and state-
precondition freezes after that compiler returns, then keeps the batch
compilation and public re-export surface.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from .real_id_resolver import normalize_path_placeholders
from .validation_obligation_expander import expand_validation_obligation
from .experiment_compile_freezer import freeze_compiled_experiment
from .state_precondition_compile_freezer import freeze_state_precondition_fields
from .process_graph_write_contract import finalize_process_graph_write_contract
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
from .abstract_experiment import is_capability_gap_reason
from .experiment_compiler_obligation import (  # noqa: F401
    BLOCK_REASONS,
    SCHEMA_VERSION,
    blocked_experiment,
    compile_experiment_for_obligation as _compile_experiment_for_obligation,
    make_experiment,
    stable_experiment_id,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _blocked_copy(
    experiment: dict[str, Any],
    *,
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    blocked = deepcopy(experiment)
    blocked["control_plan"] = []
    blocked["treatment_plan"] = []
    blocked["cleanup_plan"] = []
    blocked["compile_receipt"] = {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
    }
    return blocked


def _block_uncovered_graph_precondition_writes(
    experiment: dict[str, Any], behavior_ir: dict[str, Any]
) -> dict[str, Any]:
    contract = _dict(experiment.get("process_graph_write_contract"))
    if not _list(contract.get("write_step_ids")):
        return experiment
    operations = _index_by_id(_list(_dict(behavior_ir).get("operations")))
    uncovered: list[str] = []
    for row in _list(experiment.get("precondition_plan")):
        if not isinstance(row, dict):
            continue
        step_id = _text(row.get("step_id") or row.get("id"))
        operation_ref = _text(row.get("operation_ref"))
        operation = _dict(operations.get(operation_ref))
        method = _text(row.get("method") or operation.get("method")).upper()
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            uncovered.append(
                step_id or operation_ref or "unknown_precondition_write"
            )
    if not uncovered:
        return experiment
    return _blocked_copy(
        experiment,
        reason_code="BLOCKED_STEP_CLEANUP_UNCOVERED",
        detail=(
            "graph_precondition_writes_not_in_global_reverse_cleanup:"
            + ",".join(uncovered)
        ),
    )


def _finalize_compiled_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    graph_safe = finalize_process_graph_write_contract(
        experiment,
        behavior_ir,
    )
    graph_safe = _block_uncovered_graph_precondition_writes(
        graph_safe,
        behavior_ir,
    )
    if _text(_dict(graph_safe.get("compile_receipt")).get("status")) != "COMPILED":
        return graph_safe
    flow_frozen = freeze_compiled_experiment(
        graph_safe,
        behavior_ir=behavior_ir,
    )
    return freeze_state_precondition_fields(flow_frozen)


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Compile one obligation and freeze final cross-plan requirements."""
    experiment = _compile_experiment_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )
    return _finalize_compiled_experiment(
        experiment,
        behavior_ir=behavior_ir,
    )


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
    abstract: list[dict[str, Any]] = []
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
        if operation_ref and operation_ref not in operations:
            source_locators = [
                _text(source.get("locator"))
                for source in _list(obl.get("source_refs"))
                if isinstance(source, dict)
                and _text(source.get("kind")) == "api_operation"
                and _text(source.get("locator"))
            ]
            for locator in source_locators:
                parts = locator.split(None, 1)
                if len(parts) == 2:
                    locator_method, locator_path = parts[0].upper(), parts[1].strip()
                    for ir_id, ir_op in operations.items():
                        if (
                            isinstance(ir_op, dict)
                            and _text(ir_op.get("method")).upper()
                            == locator_method
                            and normalize_path_placeholders(
                                _text(ir_op.get("path") or ir_op.get("raw_path"))
                            )
                            == normalize_path_placeholders(locator_path)
                        ):
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
        variant_abstract = 0
        for variant in variants:
            experiment = compiler(
                variant,
                behavior_ir=behavior_ir,
                environment_type=environment_type,
                policy_version=policy_version,
                available_adapters=available_adapters,
            )
            # A custom compile_one callback must still pass through the same
            # deterministic final freezes. All finalizers are idempotent.
            experiment = _finalize_compiled_experiment(
                experiment,
                behavior_ir=behavior_ir,
            )
            receipt = _dict(experiment.get("compile_receipt"))
            status = _text(receipt.get("status")).upper()
            reason = _text(receipt.get("reason_code"))
            if status == "COMPILED":
                compiled.append(experiment)
                variant["compile_status"] = "COMPILED"
                variant_compiled += 1
            elif status == "ABSTRACT" or (
                status == "BLOCKED" and is_capability_gap_reason(reason)
            ):
                if status != "ABSTRACT":
                    from .abstract_experiment import promote_blocked_to_abstract

                    experiment = promote_blocked_to_abstract(experiment, variant)
                abstract.append(experiment)
                variant["compile_status"] = "ABSTRACT"
                variant["block_reason"] = reason
                variant_abstract += 1
            else:
                blocked.append(experiment)
                variant["compile_status"] = "BLOCKED"
                variant["block_reason"] = receipt.get("reason_code")
                variant_blocked += 1
        if variant_compiled:
            obl["compile_status"] = "COMPILED"
        elif variant_abstract:
            obl["compile_status"] = "ABSTRACT"
        else:
            obl["compile_status"] = "BLOCKED"
        obl["expanded_experiment_count"] = len(variants)
        obl["compiled_experiment_count"] = variant_compiled
        obl["blocked_experiment_count"] = variant_blocked
        obl["abstract_experiment_count"] = variant_abstract
        if not variant_compiled:
            source = abstract[-1] if variant_abstract and abstract else (
                blocked[-1] if blocked else {}
            )
            obl["block_reason"] = _dict(source.get("compile_receipt")).get(
                "reason_code"
            )
    return {
        "schema_version": "qualibug.experiment-compile.v1",
        "compiled_count": len(compiled),
        "blocked_count": len(blocked),
        "abstract_count": len(abstract),
        "experiments": compiled,
        "blocked_experiments": blocked,
        "abstract_experiments": abstract,
        "block_reason_counts": _count_reasons(blocked + abstract),
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
