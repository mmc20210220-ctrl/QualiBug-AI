"""Strengthen Runtime Materialization matching with source operation identity.

The base bridge already owns the single matching decision.  This additive installer only narrows
its candidate set using the exact interface/operation identities already preserved by Behavior IR
and Runtime Materialization, then delegates to the original matcher for lineage, path and actor
checks.  It also reconstructs a path shape from approved PATH draft values so a concrete draft such
as ``/orders/ORD-1001/ship`` remains comparable to ``/orders/{order_id}/ship`` without retaining or
copying the value into the Experiment contract.
"""
from __future__ import annotations

import functools
from typing import Any
from urllib.parse import quote

from . import runtime_materialization_experiment_bridge as _bridge

_INSTALL_MARKER = "__qualibug_source_operation_materialization_match_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_ref(value: Any) -> str:
    return _text(value).casefold()


def _candidate_operation_refs(row: dict[str, Any]) -> set[str]:
    request = _dict(row.get("request_draft"))
    action = _dict(row.get("action_entry"))
    return {
        ref
        for ref in (
            _norm_ref(request.get("interface_id")),
            _norm_ref(request.get("operation_id")),
            _norm_ref(action.get("interface_id")),
            _norm_ref(action.get("operation_id")),
            _norm_ref(action.get("operationId")),
        )
        if ref
    }


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw in _list(_dict(behavior_ir).get("operations")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        for value in (
            row.get("id"),
            row.get("node_id"),
            row.get("operation_id"),
            row.get("operationId"),
            *_list(row.get("source_operation_refs")),
        ):
            ref = _norm_ref(value)
            if ref:
                index[ref] = row
    return index


def _experiment_operation_refs(
    experiment: dict[str, Any], behavior_ir: dict[str, Any]
) -> set[str]:
    refs: set[str] = set()
    containers = [
        experiment,
        _dict(experiment.get("compile_receipt")),
        *_list(experiment.get("control_plan")),
        *_list(experiment.get("treatment_plan")),
        *_list(experiment.get("observation_plan")),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in (
            "operation_ref",
            "operation_id",
            "operationId",
            "interface_id",
            "action_ref",
        ):
            ref = _norm_ref(container.get(key))
            if ref:
                refs.add(ref)
    index = _operation_index(behavior_ir)
    for ref in list(refs):
        row = index.get(ref)
        if not row:
            continue
        for value in (
            row.get("id"),
            row.get("node_id"),
            row.get("operation_id"),
            row.get("operationId"),
            *_list(row.get("source_operation_refs")),
        ):
            normalized = _norm_ref(value)
            if normalized:
                refs.add(normalized)
    return refs


def _path_shape_candidate(row: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    request = dict(_dict(updated.get("request_draft")))
    path = _text(
        request.get("path_draft")
        or request.get("path_template")
        or request.get("path")
    )
    if not path:
        return updated
    for raw in _list(updated.get("request_value_bindings")):
        if not isinstance(raw, dict) or _text(raw.get("location")).upper() != "PATH":
            continue
        field = _text(raw.get("field"))
        if not field or raw.get("draft_value_present") is not True:
            continue
        value = raw.get("draft_value")
        rendered = quote(str(value), safe="{}:_-")
        if rendered:
            path = path.replace(rendered, f"{{{field}}}")
        raw_text = _text(value)
        if raw_text:
            path = path.replace(raw_text, f"{{{field}}}")
    request["path_draft"] = path
    updated["request_draft"] = request
    return updated


def install_runtime_materialization_operation_matching() -> None:
    # The batch wrapper covers every correct fail-closed early return that never reaches Finalizer.
    # It changes no execution decision and is installed here because this installer is already the
    # one mainline entry invoked by the existing Experiment facade.
    from .runtime_materialization_batch_lineage import (
        install_runtime_materialization_batch_lineage,
    )

    install_runtime_materialization_batch_lineage()
    original = getattr(_bridge, "_match_materialization", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(
        experiment: dict[str, Any],
        *,
        behavior_ir: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        prepared = [
            _path_shape_candidate(row)
            for row in candidates
            if isinstance(row, dict)
        ]
        experiment_refs = _experiment_operation_refs(experiment, behavior_ir)
        if experiment_refs:
            exact = [
                row
                for row in prepared
                if _candidate_operation_refs(row).intersection(experiment_refs)
            ]
            if exact:
                prepared = exact
        return original(
            experiment,
            behavior_ir=behavior_ir,
            candidates=prepared,
        )

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    _bridge._match_materialization = wrapped


__all__ = ["install_runtime_materialization_operation_matching"]
