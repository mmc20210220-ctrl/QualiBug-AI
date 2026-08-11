"""Batch compiler facade with unique source-operation identity recovery.

The established single/batch compilation and finalization mechanics live in
``_experiment_compiler_base_mechanics``.  A source locator such as
``POST /api/orders`` is useful for recovering a stale operation id only when it
identifies exactly one Behavior IR operation. Exact string equality is not a
license to choose the first duplicate node: different IR operation ids may carry
different entity, permission, fact-lineage, or observer relations.

This facade therefore admits locator recovery only for one unique operation id.
Multiple exact method/path matches, or multiple locators resolving to different
operation ids, are explicit ``BLOCKED_MISSING_OPERATION`` ambiguity rather than
source-order selection.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import _experiment_compiler_base_mechanics as _core

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

_original_compile_one_in_batch = _core._compile_one_obligation_in_batch


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


def _operation_ref_from_obligation(obligation: dict[str, Any]) -> str:
    prop = _dict(obligation.get("property"))
    return (
        next(
            (
                _text(value)
                for value in _list(obligation.get("required_operations"))
                if _text(value)
            ),
            "",
        )
        or _text(prop.get("operation_ref"))
    )


def _locator_operation_candidates(
    obligation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Return exact operation ids named by source api_operation locators."""

    candidate_ids: set[str] = set()
    locators: list[str] = []
    for raw in _list(obligation.get("source_refs")):
        source = _dict(raw)
        if _text(source.get("kind")) != "api_operation":
            continue
        locator = _text(source.get("locator"))
        if not locator:
            continue
        parts = locator.split(None, 1)
        if len(parts) != 2:
            continue
        method = parts[0].upper()
        path = _core.normalize_path_placeholders(parts[1].strip())
        if not method or not path.startswith("/"):
            continue
        locators.append(f"{method} {path}")
        for operation_id, raw_operation in operations.items():
            operation = _dict(raw_operation)
            if (
                _text(operation.get("method")).upper() == method
                and _core.normalize_path_placeholders(
                    _text(operation.get("path") or operation.get("raw_path"))
                )
                == path
            ):
                candidate_ids.add(_text(operation_id))
    return sorted(candidate_ids), sorted(set(locators))


def _mark_ambiguous_operation_block(
    obligation: dict[str, Any],
    *,
    candidate_ids: list[str],
    locators: list[str],
    blocked: list[dict[str, Any]],
) -> None:
    obligation_id = _text(obligation.get("obligation_id")) or "unknown_obligation"
    detail = (
        "ambiguous_source_operation_locator:"
        + "|".join(locators)
        + ":candidates="
        + ",".join(candidate_ids)
    )[:1000]
    experiment = _core.blocked_experiment(
        obligation_id,
        "BLOCKED_MISSING_OPERATION",
        detail,
    )
    experiment["operation_identity_ambiguity_receipt"] = {
        "schema_version": "qualibug.operation-identity-ambiguity.v1",
        "status": "BLOCKED",
        "reason_code": "AMBIGUOUS_SOURCE_OPERATION_IDENTITY",
        "source_locators": list(locators),
        "candidate_operation_ids": list(candidate_ids),
        "source_order_selection_allowed": False,
    }
    blocked.append(experiment)
    obligation.update(
        {
            "compile_status": "BLOCKED",
            "expanded_experiment_count": 1,
            "compiled_experiment_count": 0,
            "blocked_experiment_count": 1,
            "abstract_experiment_count": 0,
            "block_reason": "BLOCKED_MISSING_OPERATION",
        }
    )


def _sync_compile_status(source: dict[str, Any], target: dict[str, Any]) -> None:
    for field in (
        "compile_status",
        "expanded_experiment_count",
        "compiled_experiment_count",
        "blocked_experiment_count",
        "abstract_experiment_count",
        "block_reason",
    ):
        if field in source:
            target[field] = source[field]


def _compile_one_obligation_in_batch(
    obl: Any,
    *,
    operations: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    environment_type: str,
    policy_version: str,
    compiler: Any,
    available_adapters: Any,
    compiled: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    abstract: list[dict[str, Any]],
) -> None:
    if not isinstance(obl, dict):
        return

    operation_ref = _operation_ref_from_obligation(obl)
    if operation_ref and operation_ref in operations:
        return _original_compile_one_in_batch(
            obl,
            operations=operations,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
            compiler=compiler,
            available_adapters=available_adapters,
            compiled=compiled,
            blocked=blocked,
            abstract=abstract,
        )

    candidates, locators = _locator_operation_candidates(obl, operations)
    if len(candidates) > 1:
        _mark_ambiguous_operation_block(
            obl,
            candidate_ids=candidates,
            locators=locators,
            blocked=blocked,
        )
        return

    if len(candidates) == 1:
        # Feed the unique recovered identity to the historical compiler without
        # rewriting the source obligation's semantic contract. Only compile
        # status/count fields are projected back to the original obligation.
        working = deepcopy(obl)
        working["required_operations"] = [candidates[0]]
        _original_compile_one_in_batch(
            working,
            operations=operations,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
            compiler=compiler,
            available_adapters=available_adapters,
            compiled=compiled,
            blocked=blocked,
            abstract=abstract,
        )
        _sync_compile_status(working, obl)
        return

    # No exact recovery exists. Preserve the historical missing-operation path;
    # it will produce the established diagnostics rather than inventing identity.
    _original_compile_one_in_batch(
        obl,
        operations=operations,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        compiler=compiler,
        available_adapters=available_adapters,
        compiled=compiled,
        blocked=blocked,
        abstract=abstract,
    )


# The mechanics batch loop resolves this helper from its own module globals.
_core._compile_one_obligation_in_batch = _compile_one_obligation_in_batch

compile_experiment_for_obligation = _core.compile_experiment_for_obligation
compile_experiments = _core.compile_experiments

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_compile_one_obligation_in_batch",
        "_locator_operation_candidates",
        "compile_experiment_for_obligation",
        "compile_experiments",
    }
)
