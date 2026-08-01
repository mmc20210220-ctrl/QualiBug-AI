"""Project governed implementation operation identity into evaluator behavior candidates.

The product owns both the business-language Behavior IR and its source-governed
implementation bindings. This module joins those existing authorities by stable
``behavior_ref`` before deterministic Ground Truth alignment. It never infers an
endpoint and never mutates the captured product snapshot.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def project_governed_behavior_operation_identity(asset: dict[str, Any]) -> dict[str, Any]:
    """Return an evaluator-local copy enriched only by authoritative BOUND operationIds."""
    projected = deepcopy(asset)
    model = projected.get("enterprise_understanding_model")
    if not isinstance(model, dict):
        model = projected

    refs_by_behavior: dict[str, set[str]] = {}
    for binding in _rows(model.get("behavior_implementation_bindings")):
        behavior_ref = _text(binding.get("behavior_ref"))
        if not behavior_ref:
            continue
        for api_binding in _rows(binding.get("api_operation_bindings")):
            if api_binding.get("authoritative") is not True:
                continue
            if _text(api_binding.get("status")).upper() != "BOUND":
                continue
            operation_id = _text(api_binding.get("operation_id"))
            if operation_id:
                refs_by_behavior.setdefault(behavior_ref, set()).add(operation_id)

    projected_behaviors: list[dict[str, Any]] = []
    for behavior in _rows(model.get("business_behaviors")):
        behavior_ref = _text(behavior.get("behavior_id"))
        operation_refs = sorted(refs_by_behavior.get(behavior_ref, set()))
        if operation_refs:
            raw_names = (
                [
                    str(value).strip()
                    for value in behavior.get("raw_action_names", [])
                    if str(value).strip()
                ]
                if isinstance(behavior.get("raw_action_names"), list)
                else []
            )
            behavior["raw_action_names"] = list(dict.fromkeys([*raw_names, *operation_refs]))
        projected_behaviors.append(behavior)
    model["business_behaviors"] = projected_behaviors
    return projected


__all__ = ["project_governed_behavior_operation_identity"]
