"""Public single-obligation compiler with explicit fixture/data authority.

The existing semantic compiler remains in ``experiment_compiler_obligation_core``.
This facade supplies final FlowDataRequirement authority, binds Observer subjects
inside the compiled Experiment Contract, persists the one canonical Process
Graph extracted from treatment steps, and isolates one legacy compatibility
projection for graph cleanup. Runtime consumes these compiled identities; it
does not infer a different Observer subject or graph after execution.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from . import experiment_compiler_obligation_core as _core


FLOW_DATA_AUTHORITY = "flow_data_requirement"
OBSERVER_SUBJECT_BINDING_SCHEMA = "qualibug.observer-subject-binding.v1"
_ORIGINAL_PROTOCOL_ATTR = "_qualibug_original_compile_family_protocol"
_ORIGINAL_MAKE_EXPERIMENT_ATTR = "_qualibug_original_make_experiment"
if not hasattr(_core, _ORIGINAL_PROTOCOL_ATTR):
    setattr(
        _core,
        _ORIGINAL_PROTOCOL_ATTR,
        _core.compile_family_protocol,
    )
if not hasattr(_core, _ORIGINAL_MAKE_EXPERIMENT_ATTR):
    setattr(
        _core,
        _ORIGINAL_MAKE_EXPERIMENT_ATTR,
        _core.make_experiment,
    )
_ORIGINAL_COMPILE_FAMILY_PROTOCOL = getattr(
    _core,
    _ORIGINAL_PROTOCOL_ATTR,
)
_ORIGINAL_MAKE_EXPERIMENT = getattr(
    _core,
    _ORIGINAL_MAKE_EXPERIMENT_ATTR,
)


class _AuthorityScopedBehaviorIR(dict):
    """Dict-compatible compile context without adding fingerprinted IR keys."""

    fixture_data_authority: str

    def __init__(
        self,
        source: dict[str, Any],
        *,
        fixture_data_authority: str,
    ) -> None:
        super().__init__(source)
        self.fixture_data_authority = fixture_data_authority


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plan_step_ids(rows: Any) -> list[str]:
    return list(
        dict.fromkeys(
            _text(row.get("step_id"))
            for row in _list(rows)
            if isinstance(row, dict) and _text(row.get("step_id"))
        )
    )


def _bind_observer_subjects(
    *,
    observers: Any,
    control_plan: Any,
    treatment_plan: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    control_ids = _plan_step_ids(control_plan)
    treatment_ids = _plan_step_ids(treatment_plan)
    plan_ids = [*control_ids, *treatment_ids]
    semantic_subject = (
        treatment_ids[-1]
        if treatment_ids
        else control_ids[-1]
        if control_ids
        else ""
    )
    bound: list[dict[str, Any]] = []
    binding_rows: list[dict[str, Any]] = []

    for raw in _list(observers):
        if not isinstance(raw, dict):
            continue
        observer = deepcopy(raw)
        observer_id = _text(observer.get("observer_id"))
        if observer_id == "http_response":
            observer["scope_mode"] = "per_plan_step"
            observer["subject_step_ids"] = list(plan_ids)
            observer["scope_basis"] = "compiled_plan_step_set"
            binding_rows.append(
                {
                    "observer_id": observer_id,
                    "scope_mode": "per_plan_step",
                    "subject_step_ids": list(plan_ids),
                }
            )
        else:
            explicit_subject = _text(
                observer.get("subject_step_id")
                or observer.get("step_id")
            )
            subject = explicit_subject or semantic_subject
            if subject:
                observer["subject_step_id"] = subject
                observer["scope_mode"] = "single_step"
                observer["scope_basis"] = (
                    "observer_declaration"
                    if explicit_subject
                    else "compiled_protocol_final_measurement"
                )
            binding_rows.append(
                {
                    "observer_id": observer_id,
                    "scope_mode": _text(observer.get("scope_mode")),
                    "subject_step_id": _text(
                        observer.get("subject_step_id")
                    ),
                }
            )
        bound.append(observer)

    invalid_rows = [
        row
        for row in binding_rows
        if (
            row.get("scope_mode") == "per_plan_step"
            and not _list(row.get("subject_step_ids"))
        )
        or (
            row.get("scope_mode") == "single_step"
            and not _text(row.get("subject_step_id"))
        )
    ]
    receipt = {
        "schema_version": OBSERVER_SUBJECT_BINDING_SCHEMA,
        "control_step_ids": control_ids,
        "treatment_step_ids": treatment_ids,
        "bindings": binding_rows,
        "binding_count": len(binding_rows),
        "complete": not invalid_rows,
        "invalid_observer_ids": [
            _text(row.get("observer_id")) for row in invalid_rows
        ],
    }
    receipt["binding_hash"] = _stable_hash(
        {
            key: value
            for key, value in receipt.items()
            if key != "binding_hash"
        }
    )
    return bound, receipt


def _subject_bound_make_experiment(
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    observers, binding_receipt = _bind_observer_subjects(
        observers=kwargs.get("observers"),
        control_plan=kwargs.get("control_plan"),
        treatment_plan=kwargs.get("treatment_plan"),
    )
    compile_receipt = dict(_dict(kwargs.get("compile_receipt")))
    if observers:
        compile_receipt.update(
            {
                "observer_subject_binding_schema_version": (
                    OBSERVER_SUBJECT_BINDING_SCHEMA
                ),
                "observer_subject_binding_count": binding_receipt[
                    "binding_count"
                ],
                "observer_subject_binding_hash": binding_receipt[
                    "binding_hash"
                ],
                "observer_subject_binding_complete": binding_receipt[
                    "complete"
                ],
            }
        )
    result = _ORIGINAL_MAKE_EXPERIMENT(
        *args,
        **{
            **kwargs,
            "observers": observers,
            "compile_receipt": compile_receipt,
        },
    )
    if observers:
        result["observer_subject_binding_receipt"] = binding_receipt
    return result


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
        "cleanup_plan": [],
        "graph_cleanup_projection": visible_cleanup,
    }


def _install_core_hooks() -> None:
    _core.compile_family_protocol = _graph_cleanup_compatibility_protocol
    _core.make_experiment = _subject_bound_make_experiment


def _persist_compiled_execution_graph(
    experiment: dict[str, Any],
) -> dict[str, Any]:
    """Promote the canonical step-embedded graph into the Experiment Contract."""
    result = dict(experiment)
    if _text(_dict(result.get("compile_receipt")).get("status")) != "COMPILED":
        return result
    from .process_graph_runtime import extract_execution_graph

    graph, graph_error = extract_execution_graph(
        [
            row
            for row in _list(result.get("treatment_plan"))
            if isinstance(row, dict)
        ]
    )
    if graph_error or not graph:
        return result
    result["execution_graph"] = deepcopy(graph)
    receipt = dict(_dict(result.get("compile_receipt")))
    receipt.update(
        {
            "execution_graph_id": _text(graph.get("execution_graph_id")),
            "process_id": _text(graph.get("process_id")),
            "execution_graph_persisted": True,
        }
    )
    result["compile_receipt"] = receipt
    return result


_install_core_hooks()

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

make_experiment = _subject_bound_make_experiment


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Compile with final-flow data, Observer subject and graph authority."""
    _install_core_hooks()
    scoped_ir = _AuthorityScopedBehaviorIR(
        behavior_ir,
        fixture_data_authority=FLOW_DATA_AUTHORITY,
    )
    compiled = _core.compile_experiment_for_obligation(
        obligation,
        behavior_ir=scoped_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )
    return _persist_compiled_execution_graph(compiled)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_core",
        "_name",
        "_ORIGINAL_COMPILE_FAMILY_PROTOCOL",
        "_ORIGINAL_MAKE_EXPERIMENT",
    }
)
