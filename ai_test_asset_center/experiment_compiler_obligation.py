"""Public single-obligation compiler with explicit fixture/data authority.

The existing semantic compiler remains in ``experiment_compiler_obligation_core``.
This facade supplies final FlowDataRequirement authority and isolates one legacy
compatibility projection: graph protocols may expose a flat cleanup plan for
diagnostics, but the legacy core must not use operation-id comparison as its
cleanup proof. The final process-graph write contract reconstructs and validates
the executable, source-step-scoped cleanup plan.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import experiment_compiler_obligation_core as _core


FLOW_DATA_AUTHORITY = "flow_data_requirement"
_ORIGINAL_COMPILE_FAMILY_PROTOCOL = _core.compile_family_protocol


class _AuthorityScopedBehaviorIR(dict):
    """Dict-compatible compile context without adding fingerprinted IR keys."""

    fixture_data_authority: str

    def __init__(self, source: dict[str, Any], *, fixture_data_authority: str) -> None:
        super().__init__(source)
        self.fixture_data_authority = fixture_data_authority


def _text(value: Any) -> str:
    return str(value or "").strip()


def _graph_cleanup_compatibility_protocol(**kwargs: Any) -> dict[str, Any]:
    result = _ORIGINAL_COMPILE_FAMILY_PROTOCOL(**kwargs)
    graph = result.get("execution_graph")
    if (
        _text(result.get("status")) != "COMPILED"
        or not isinstance(graph, dict)
        or _text(graph.get("cleanup_authority"))
        != "process_graph_write_contract"
    ):
        return result
    visible_cleanup = [
        deepcopy(row)
        for row in list(result.get("cleanup_plan") or [])
        if isinstance(row, dict)
    ]
    return {
        **result,
        # The old core compares cleanup operation ids to source write ids. That
        # representation cannot express graph source_step_id/system/observer
        # scope and is not a safety authority.
        "cleanup_plan": [],
        "graph_cleanup_projection": visible_cleanup,
    }


# Core global lookup now resolves this deterministic compatibility adapter.
# It affects only graph protocols carrying an explicit graph cleanup authority.
_core.compile_family_protocol = _graph_cleanup_compatibility_protocol

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Compile with final-flow fixture/data planning as the only authority."""
    scoped_ir = _AuthorityScopedBehaviorIR(
        behavior_ir,
        fixture_data_authority=FLOW_DATA_AUTHORITY,
    )
    return _core.compile_experiment_for_obligation(
        obligation,
        behavior_ir=scoped_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_core",
        "_name",
        "_ORIGINAL_COMPILE_FAMILY_PROTOCOL",
    }
)
