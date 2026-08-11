"""Public source-backed process protocol compiler.

The existing core remains the graph normalization and topology authority.
Compensation remains visible for diagnostics while the graph write contract is
the executable cleanup authority. Observer-backed state waits and event
transitions reuse the existing graph scheduler and bounded readback kernel.

When a compiled graph contains source-declared message/callback transitions,
the protocol selects the existing Assertion DSL's combined process/async
assertion. No event-specific experiment runner is introduced.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import multi_step_protocol_core as _core
from .process_graph_async_transition_observer import (
    ASSERTION_KIND as ASYNC_ASSERTION_KIND,
    OBSERVER_ID as ASYNC_OBSERVER_ID,
    install_process_graph_async_transition_surface,
)
from .process_graph_wait_contract import (
    STATUS_COMPILED as WAIT_STATUS_COMPILED,
    compile_process_graph_wait_contracts,
)
from .process_step_observer import (
    OBSERVER_ID as PROCESS_STEP_OBSERVER_ID,
    install_process_step_surface,
)

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)


def _graph_owned_cleanup(result: dict[str, Any]) -> dict[str, Any]:
    if str(result.get("status") or "").strip() != "COMPILED":
        return result
    graph = deepcopy(result.get("execution_graph") or {})
    if not graph:
        return result
    declared_cleanup = [
        deepcopy(row)
        for row in list(result.get("cleanup_plan") or [])
        if isinstance(row, dict)
    ]
    graph["declared_cleanup_steps"] = deepcopy(declared_cleanup)
    graph["cleanup_authority"] = "process_graph_write_contract"
    treatment_plan = [
        deepcopy(row)
        for row in list(result.get("treatment_plan") or [])
        if isinstance(row, dict)
    ]
    waits_by_target = _core._dict(graph.get("wait_contracts_by_target"))
    for step in treatment_plan:
        step_id = _core._text(step.get("step_id"))
        step["_execution_graph"] = deepcopy(graph)
        wait_contract = _core._dict(waits_by_target.get(step_id))
        if wait_contract:
            step["wait_contract"] = deepcopy(wait_contract)
    return {
        **result,
        "execution_graph": graph,
        "treatment_plan": treatment_plan,
        "cleanup_plan": declared_cleanup,
        "graph_cleanup_projection": deepcopy(declared_cleanup),
    }


def _async_contract_present(graph: dict[str, Any]) -> bool:
    return int(
        _core._dict(graph.get("wait_runtime_contract")).get(
            "event_transition_count"
        )
        or 0
    ) > 0


def _async_protocol_observers() -> list[dict[str, str]]:
    install_process_step_surface()
    install_process_graph_async_transition_surface()
    return [
        {"observer_id": "http_response"},
        {"observer_id": "after_state"},
        {"observer_id": PROCESS_STEP_OBSERVER_ID},
        {"observer_id": ASYNC_OBSERVER_ID},
    ]


def _resume_wait_capable_result(
    result: dict[str, Any],
    *,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Resume only the core's explicit wait/async runtime-capability block."""
    if _core._text(result.get("status")) != "BLOCKED":
        return result
    if _core._text(result.get("reason_code")) != (
        _core.MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE
    ):
        return result
    graph = _core._dict(result.get("execution_graph"))
    blockers = {
        _core._text(value)
        for value in _core._list(graph.get("runtime_blockers"))
        if _core._text(value)
    }
    if not blockers or not blockers.issubset(
        {"wait_observer_scheduler", "async_edge_scheduler"}
    ):
        return result

    wait_result = compile_process_graph_wait_contracts(
        graph,
        behavior_ir=_core._dict(envelope.get("behavior_ir")),
    )
    if _core._text(wait_result.get("status")) != WAIT_STATUS_COMPILED:
        semantic_reason = _core._text(wait_result.get("reason_code"))
        semantic_reasons = [
            _core._text(value)
            for value in _core._list(
                wait_result.get("semantic_reason_codes")
            )
            if _core._text(value)
        ]
        if semantic_reason and semantic_reason not in semantic_reasons:
            semantic_reasons.insert(0, semantic_reason)
        blocked = _core._blocked(
            _core.MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE,
            _core._text(wait_result.get("detail"))
            or "observer_backed_wait_contract_not_compiled",
            graph,
        )
        blocked["semantic_reason_code"] = semantic_reason
        blocked["semantic_reason_codes"] = semantic_reasons
        blocked["wait_contract_compile_receipt"] = {
            "status": _core._text(wait_result.get("status")),
            "reason_code": semantic_reason,
            "semantic_reason_codes": semantic_reasons,
            "detail": _core._text(wait_result.get("detail")),
            "issues": deepcopy(_core._list(wait_result.get("issues"))),
        }
        return blocked

    compiled_graph = _core._dict(wait_result.get("graph"))
    treatment_plan = _core._treatment_plan(compiled_graph)
    cleanup_plan = _core._explicit_cleanup_plan(compiled_graph)
    prop = _core._dict(envelope.get("property_spec"))
    expected_order = _core._list(prop.get("expected_order"))
    if (
        not expected_order
        and not _core._list(compiled_graph.get("fork_groups"))
        and not _core._list(compiled_graph.get("join_groups"))
        and len(_core._list(compiled_graph.get("start_node_refs"))) == 1
    ):
        expected_order = list(compiled_graph.get("topological_order") or [])
    source_refs = _core._list(prop.get("source_refs")) or _core._list(
        compiled_graph.get("source_refs")
    )
    family = _core._text(envelope.get("risk_family"))
    event_transition = _async_contract_present(compiled_graph)
    assertion_kind = ASYNC_ASSERTION_KIND if event_transition else "process_completion"
    observers = (
        _async_protocol_observers()
        if event_transition
        else [
            {"observer_id": "http_response"},
            {"observer_id": "after_state"},
        ]
    )
    return {
        "status": "COMPILED",
        "execution_graph": compiled_graph,
        "control_plan": [],
        "treatment_plan": treatment_plan,
        "cleanup_plan": cleanup_plan,
        "assertion": {
            "kind": assertion_kind,
            "expected_steps": list(
                compiled_graph.get("topological_order") or []
            ),
            "expected_order": expected_order,
            "execution_graph_id": _core._text(
                compiled_graph.get("execution_graph_id")
            ),
            "event_transition_count": int(
                _core._dict(compiled_graph.get("wait_runtime_contract")).get(
                    "event_transition_count"
                )
                or 0
            ),
        },
        "observers": observers,
        "per_step_evidence": True,
        "requires_state_precondition": bool(prop.get("from_state")),
        "expected_order": expected_order,
        "source_refs": source_refs,
        "wait_runtime_contract": deepcopy(
            compiled_graph.get("wait_runtime_contract") or {}
        ),
        "_registry_protocol_id": (
            f"{family}:{_core.TEMPLATE_MULTI_STEP_PROCESS}"
        ),
    }


def compile_multi_step_process_protocol(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    core_result = _core.compile_multi_step_process_protocol(envelope)
    resumed = _resume_wait_capable_result(core_result, envelope=envelope)
    return _graph_owned_cleanup(resumed)


def compile_state_chain_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result
    graph = _core._dict(result.get("execution_graph"))
    result["assertion"] = {
        **(result.get("assertion") or {}),
        "kind": (
            ASYNC_ASSERTION_KIND
            if _async_contract_present(graph)
            else "step_sequence_order"
        ),
    }
    result["_registry_protocol_id"] = (
        f"state:{_core.TEMPLATE_STATE_CHAIN_PROCESS}"
    )
    return result


def compile_sequence_verification_protocol(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result
    graph = _core._dict(result.get("execution_graph"))
    if (
        _core._list(graph.get("fork_groups"))
        or _core._list(graph.get("join_groups"))
        or len(_core._list(graph.get("start_node_refs"))) != 1
        or not _core._list(result.get("expected_order"))
    ):
        return _core._blocked(
            _core.MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE,
            "sequence_total_order_not_source_declared",
            graph,
        )
    result["assertion"] = {
        **(result.get("assertion") or {}),
        "kind": (
            ASYNC_ASSERTION_KIND
            if _async_contract_present(graph)
            else "step_sequence_order"
        ),
    }
    result["_registry_protocol_id"] = (
        f"process:{_core.TEMPLATE_SEQUENCE_VERIFICATION}"
    )
    return result


def register_v150_multi_step_protocols() -> list[str]:
    """Register public wrappers in the existing protocol registry."""
    from .experiment_protocol_registry import register_family_protocol

    install_process_step_surface()
    install_process_graph_async_transition_surface()
    return [
        register_family_protocol(
            "process",
            _core.TEMPLATE_MULTI_STEP_PROCESS,
            compiler=compile_multi_step_process_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="process_completion",
            emits_control=False,
            per_step_evidence=True,
        ),
        register_family_protocol(
            "state",
            _core.TEMPLATE_STATE_CHAIN_PROCESS,
            compiler=compile_state_chain_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="step_sequence_order",
            emits_control=False,
            per_step_evidence=True,
        ),
        register_family_protocol(
            "process",
            _core.TEMPLATE_SEQUENCE_VERIFICATION,
            compiler=compile_sequence_verification_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="step_sequence_order",
            emits_control=False,
            per_step_evidence=True,
        ),
    ]


__all__ = sorted(
    {
        *[name for name in dir(_core) if not name.startswith("__")],
        "compile_multi_step_process_protocol",
        "compile_state_chain_protocol",
        "compile_sequence_verification_protocol",
        "register_v150_multi_step_protocols",
    }
)
