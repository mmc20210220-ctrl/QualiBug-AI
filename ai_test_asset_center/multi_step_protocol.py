"""Public source-backed process protocol compiler.

The existing core remains the graph normalization and topology authority.
Compensation remains visible on the direct protocol result for diagnostics and
regression contracts, while the graph marks the final
``process_graph_write_contract`` as the executable cleanup authority. The
single-obligation facade removes the flat compatibility projection only while
its legacy core assembles an experiment.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import multi_step_protocol_core as _core

for _name in dir(_core):
    if not _name.startswith("__"):
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
    for step in treatment_plan:
        step["_execution_graph"] = deepcopy(graph)
    return {
        **result,
        "execution_graph": graph,
        "treatment_plan": treatment_plan,
        "cleanup_plan": declared_cleanup,
        "graph_cleanup_projection": deepcopy(declared_cleanup),
    }


def compile_multi_step_process_protocol(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    return _graph_owned_cleanup(
        _core.compile_multi_step_process_protocol(envelope)
    )


def compile_state_chain_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result
    result["assertion"] = {
        **(result.get("assertion") or {}),
        "kind": "step_sequence_order",
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
    result["assertion"] = {
        **(result.get("assertion") or {}),
        "kind": "step_sequence_order",
    }
    result["_registry_protocol_id"] = (
        f"process:{_core.TEMPLATE_SEQUENCE_VERIFICATION}"
    )
    return result


def register_v150_multi_step_protocols() -> list[str]:
    """Register public wrappers in the existing protocol registry."""
    from .experiment_protocol_registry import register_family_protocol
    from .process_step_observer import install_process_step_surface

    install_process_step_surface()
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
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "compile_multi_step_process_protocol",
        "compile_state_chain_protocol",
        "compile_sequence_verification_protocol",
        "register_v150_multi_step_protocols",
    }
)
