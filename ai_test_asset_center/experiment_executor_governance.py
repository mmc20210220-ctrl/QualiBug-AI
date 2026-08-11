"""Outermost execution-governance facade.

The current account/graph/comparison/cleanup authorities live in
``_experiment_executor_governance_authority_mechanics``. This boundary composes
two independent rules before the governed core can reach transport:

* batch ``_pre_resolved_bindings`` are discovery/performance hints only; and
* every modern frozen experiment must carry the same RequestBuildContract that
  can be rebuilt from its current plans, Binding/Flow contracts and source
  operations. Missing/drifted/blocked contracts terminate as a binding GAP,
  never as ``HARNESS_REQUEST_BUILD_FAILED``.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_executor_governance_authority_mechanics as _authority
from .request_build_contract import (
    STATUS_BLOCKED as REQUEST_BUILD_BLOCKED,
    validate_request_build_contract,
)

for _name in dir(_authority):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_authority, _name)

_original_execute_one_experiment = _authority.execute_one_experiment


def __getattr__(name: str) -> Any:
    return getattr(_authority, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_authority)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _formal_experiment_without_raw_prebindings(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    exp = dict(_dict(experiment))
    raw = dict(_dict(exp.pop("_pre_resolved_bindings", {})))
    targets = sorted(
        _text(target)
        for target, value in raw.items()
        if _text(target) and value not in (None, "", [], {})
    )
    diagnostic = {
        "schema_version": "qualibug.pre-resolved-binding-diagnostic.v1",
        "present": bool(raw),
        "target_count": len(targets),
        "targets": targets,
        "formal_binding_authority": False,
        "values_forwarded_to_transport": False,
        "reason": (
            "raw_batch_prebinding_has_no_materialization_receipt"
            if raw
            else "not_present"
        ),
    }
    if raw:
        exp["pre_resolution_diagnostic"] = dict(diagnostic)
    return exp, diagnostic


def _request_build_runtime_gate(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Validate modern request-build authority or identify an old artifact."""

    exp = _dict(experiment)
    flow = _dict(exp.get("flow_data_execution_contract"))
    freeze = _dict(exp.get("compile_freeze_receipt"))
    modern_frozen = bool(
        _text(flow.get("status")).upper() == "FROZEN"
        or _text(freeze.get("status")).upper() == "FROZEN"
    )
    if not modern_frozen:
        return {
            "status": "NOT_APPLICABLE",
            "reason_code": "",
        }
    return validate_request_build_contract(
        exp,
        behavior_ir=behavior_ir,
    )


def _request_contract_terminal(
    experiment: dict[str, Any],
    gate: dict[str, Any],
    *,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    reason = _text(gate.get("reason_code")) or "REQUEST_BUILD_CONTRACT_BLOCKED"
    detail = (
        "request_build_runtime_authority:"
        + reason
        + ":stored="
        + _text(gate.get("stored_fingerprint"))
        + ":current="
        + _text(gate.get("current_fingerprint"))
    )[:1000]
    result = {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": _text(_dict(experiment).get("experiment_id")),
        "obligation_id": _text(_dict(experiment).get("obligation_id")),
        "status": "BLOCKED",
        "reason_code": "BLOCKED_MISSING_BINDING",
        "detail": detail,
        "steps": [],
        "finding": None,
        "cleanup_failures": 0,
        "request_build_runtime_gate_receipt": dict(gate),
        "execution_receipt": {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": detail,
            "write_request_attempt_count": 0,
            "request_reached_transport": False,
            "cleanup_failures": 0,
        },
    }
    if diagnostic.get("present"):
        result["pre_resolution_diagnostic"] = diagnostic
    return result


def execute_one_experiment(
    experiment: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    governed, diagnostic = _formal_experiment_without_raw_prebindings(experiment)
    behavior_ir = _dict(kwargs.get("behavior_ir"))
    gate = _request_build_runtime_gate(
        governed,
        behavior_ir=behavior_ir,
    )
    if _text(gate.get("status")) == REQUEST_BUILD_BLOCKED:
        return _request_contract_terminal(
            governed,
            gate,
            diagnostic=diagnostic,
        )

    result = _original_execute_one_experiment(governed, **kwargs)
    output = dict(_dict(result))
    output["request_build_runtime_gate_receipt"] = gate
    if diagnostic["present"]:
        output["pre_resolution_diagnostic"] = diagnostic
    return output


__all__ = sorted(
    {
        *[
            name
            for name in dir(_authority)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "execute_one_experiment",
        "_formal_experiment_without_raw_prebindings",
        "_request_build_runtime_gate",
    }
)
