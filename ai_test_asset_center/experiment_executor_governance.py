"""Outermost execution-governance facade.

The current account/graph/comparison/cleanup authorities live in
``_experiment_executor_governance_authority_mechanics``. This boundary composes
six independent rules before the governed core can reach transport/finalize:

* batch ``_pre_resolved_bindings`` are discovery/performance hints only;
* every modern frozen experiment must carry the same RequestBuildContract that
  can be rebuilt from its current plans, Binding/Flow contracts and source
  operations;
* RequestBuildContract query/header claims must use the same source/transport
  authorities in a fresh runtime process as they did at compile time;
* barrier zero-transport governance blocks pass through the same request
  first-loss truth boundary as sequential execution;
* fixture/precondition blocks that deliberately clear measured plans remain
  typed blockers after cleanup instead of falling into HARNESS fallback; and
* the sequential transport kernel uses the source-truthful FK guard. Concrete
  values such as ``1`` or ``test`` are never rejected by lexical guessing; only
  surviving harness placeholders/sentinels prove materialization failure.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_executor_governance_authority_mechanics as _authority
from .request_build_contract import (
    STATUS_BLOCKED as REQUEST_BUILD_BLOCKED,
    validate_request_build_contract,
)
from .request_header_transport_authority import (
    install_request_header_transport_authority,
)
from .validation_parameter_authority import (
    install_request_parameter_contract_authority,
)
from .experiment_barrier_request_authority import (
    install_barrier_request_first_loss_authority,
)
from .fixture_measurement_finalizer_authority import (
    install_fixture_measurement_finalizer_authority,
)

# A runtime-only process must rebuild exactly the compile-frozen request
# contract, but it must not import validation-expander/protocol modules merely
# to do so. Install only the query RequestBuildContract authority here, then the
# exact transport-header authority and execution/finalizer bridges.
install_request_parameter_contract_authority()
install_request_header_transport_authority()
install_barrier_request_first_loss_authority()
install_fixture_measurement_finalizer_authority()

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


def _install_strict_fk_request_authority() -> None:
    """Patch the exact imported step-kernel hook without facade duplication."""

    from . import experiment_plan_step_executor_core as step_core
    from .foreign_key_request_authority import (
        foreign_key_materialization_violations,
    )

    step_core._foreign_key_violations = foreign_key_materialization_violations


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

    _install_strict_fk_request_authority()
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
        "_install_strict_fk_request_authority",
    }
)
