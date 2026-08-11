"""Batch compiler facade with source-operation and ownership-scope authority.

The established raw compilation/finalization mechanics live in
``_experiment_compiler_base_mechanics``. This boundary adds fail-closed identity
rules before final FlowData freeze:

* stale source locators recover an operation only when METHOD+PATH identifies
  exactly one Behavior IR operation;
* ownership bindings are scoped only after the family protocol has fixed exact
  control/treatment actors; and
* the existing V1.2 Binding Coverage Graph is rebuilt after that scope
  projection. Query-local ownership targets removed from the global binding plan
  therefore cannot remain as stale coverage facts/fingerprints.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from . import _experiment_compiler_base_mechanics as _core
from .binding_coverage_graph import build_binding_coverage_graph
from .ownership_binding_scope_authority import seal_ownership_binding_scopes
from .v12_coverage_recovery_orchestrator import (
    GATE_BLOCKED,
    _evaluate_binding_gate,
)

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

_original_compile_one_in_batch = _core._compile_one_obligation_in_batch
_original_raw_compile_for_obligation = _core._compile_experiment_for_obligation


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


def _ownership_scope_block(
    experiment: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    blocked = deepcopy(experiment)
    blocked["control_plan"] = []
    blocked["treatment_plan"] = []
    blocked["precondition_plan"] = []
    blocked["cleanup_plan"] = []
    details = [
        f"{_text(row.get('target'))}:{_text(row.get('reason_code'))}"
        for row in _list(receipt.get("issues"))
        if isinstance(row, dict)
    ]
    blocked["compile_receipt"] = {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_BINDING",
        "detail": ("ownership_binding_scope:" + ";".join(details))[:1000],
    }
    blocked["ownership_binding_scope_receipt"] = deepcopy(receipt)
    return blocked


def _binding_coverage_block(
    experiment: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    blocked = deepcopy(experiment)
    blocked["control_plan"] = []
    blocked["treatment_plan"] = []
    blocked["precondition_plan"] = []
    blocked["cleanup_plan"] = []
    blocked["compile_receipt"] = {
        "status": "BLOCKED",
        "reason_code": _text(gate.get("reason_code"))
        or "BLOCKED_MISSING_BINDING",
        "detail": (
            "ownership_scope_binding_coverage:"
            + (_text(gate.get("detail")) or "binding_graph_not_valid")
        )[:1000],
    }
    blocked["post_scope_binding_gate_receipt"] = deepcopy(gate)
    return blocked


def _reseal_binding_coverage_after_scope(
    experiment: dict[str, Any],
    *,
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild only the V1.2 module whose semantic input changed.

    The raw compiler already ran the full V1.2 orchestrator. Ownership scope
    projection changes the binding plan (and query-local runtime DAG nodes) but
    does not change cleanup/oracle/observer semantics. Reuse the canonical
    binding graph builder + canonical binding gate, replace that gate receipt,
    and recompute the orchestrator fingerprint with the original gate statuses.
    """

    result = deepcopy(experiment)
    graph = build_binding_coverage_graph(
        experiment=result,
        behavior_ir=behavior_ir,
    )
    gate = _evaluate_binding_gate(graph)
    result["binding_coverage_graph"] = graph
    result["post_scope_binding_gate_receipt"] = deepcopy(gate)
    if _text(gate.get("status")) == GATE_BLOCKED:
        return result, gate

    coverage = deepcopy(_dict(result.get("compile_coverage_receipt")))
    gate_receipts = [
        dict(row)
        for row in _list(coverage.get("gate_receipts"))
        if isinstance(row, dict)
    ]
    replaced = False
    for index, row in enumerate(gate_receipts):
        if _text(row.get("module")) == "binding_coverage_graph":
            gate_receipts[index] = deepcopy(gate)
            replaced = True
            break
    if not replaced:
        gate_receipts.append(deepcopy(gate))

    verdict = _text(coverage.get("verdict")) or "READY"
    binding_fingerprint = _text(graph.get("binding_graph_fingerprint"))
    fp_content = {
        "obligation_id": _text(obligation.get("obligation_id")),
        "verdict": verdict,
        "gates": {
            _text(row.get("module")): _text(row.get("status"))
            for row in gate_receipts
            if _text(row.get("module"))
        },
        "binding_fingerprint": binding_fingerprint,
    }
    orchestrator_fingerprint = hashlib.sha256(
        json.dumps(
            fp_content,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:32]
    coverage.update(
        {
            "verdict": verdict,
            "fingerprint": orchestrator_fingerprint,
            "gate_receipts": gate_receipts,
            "binding_graph_fingerprint": binding_fingerprint,
            "ownership_scope_resealed": True,
        }
    )
    result["compile_coverage_receipt"] = coverage
    return result, gate


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Compile protocol, seal ownership scope/coverage, then final-freeze once."""

    experiment = _original_raw_compile_for_obligation(
        obligation,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )
    if _text(_dict(experiment.get("compile_receipt")).get("status")) != "COMPILED":
        return experiment

    scoped, receipt = seal_ownership_binding_scopes(
        experiment,
        obligation=obligation,
        behavior_ir=behavior_ir,
    )
    if _text(receipt.get("status")) == "BLOCKED":
        return _ownership_scope_block(scoped, receipt)

    resealed, binding_gate = _reseal_binding_coverage_after_scope(
        scoped,
        obligation=obligation,
        behavior_ir=behavior_ir,
    )
    if _text(binding_gate.get("status")) == GATE_BLOCKED:
        return _binding_coverage_block(resealed, binding_gate)

    return _core._finalize_compiled_experiment(
        resealed,
        behavior_ir=behavior_ir,
    )


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


_core.compile_experiment_for_obligation = compile_experiment_for_obligation
_core._compile_one_obligation_in_batch = _compile_one_obligation_in_batch

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
        "_reseal_binding_coverage_after_scope",
        "compile_experiment_for_obligation",
        "compile_experiments",
    }
)
