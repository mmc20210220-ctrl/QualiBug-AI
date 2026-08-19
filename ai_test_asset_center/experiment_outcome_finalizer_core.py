"""Experiment outcome finalizer facade with receipt-derived cleanup truth.

The durable finalization implementation lives in
``_experiment_outcome_finalizer_core_mechanics``.  This facade owns two
truthfulness boundaries:

* cleanup harness failures are classified from formal execution/equivalence
  receipts rather than guessed as transport failures; and
* an actually accepted control/treatment write requires restoration evidence
  regardless of risk-family label.  Authorization/validation/isolation/
  visibility experiments often contain a successful control write before a
  rejected treatment.  Such a write may never be treated as read-only merely
  because the treatment was expected to fail.

The exact-scope finalizer composes Observer/Oracle/Cleanup callables by assigning
them on this facade.  Before invoking the extracted mechanics implementation we
mirror those explicit composition points into the mechanics module, because a
function retains the globals of the module where it was defined.  Without this
handoff, the Facade/Mechanics split would silently bypass exact-step evidence
scoping even though the public hook appeared installed.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_outcome_finalizer_core_mechanics as _core
from ._experiment_outcome_finalizer_core_mechanics import *  # noqa: F401,F403

_original_classify_harness_failure = _core._classify_harness_failure
_original_finalize_experiment_execution = _core.finalize_experiment_execution

HARNESS_CLEANUP_FAILURE_UNATTRIBUTED = "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED"
HARNESS_FAILURE_SUBTYPES = tuple(
    dict.fromkeys(
        [
            *_core.HARNESS_FAILURE_SUBTYPES,
            HARNESS_CLEANUP_FAILURE_UNATTRIBUTED,
        ]
    )
)
_COMPOSED_MECHANICS_HOOKS = (
    "observe_experiment_requirements",
    "evaluate_contract_oracle",
    "evaluate_cleanup_equivalence",
)


def __getattr__(name: str) -> Any:
    if name == "evaluate_cleanup_equivalence":
        # Resolved lazily: cleanup_equivalence → cleanup_equivalence_core →
        # _cleanup_equivalence_core_mechanics forms an import cycle with this
        # finalizer chain when imported at module load; by call time every
        # module is fully initialized.
        from .cleanup_equivalence import evaluate_cleanup_equivalence

        return evaluate_cleanup_equivalence
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status_code(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sync_composed_finalizer_hooks() -> None:
    """Mirror supported facade composition points into extracted mechanics.

    ``_experiment_outcome_finalizer_scope_mechanics`` intentionally replaces
    these names on the public facade for the duration of a finalization call.
    The implementation function itself was extracted into ``_core`` and thus
    resolves its globals there; copying only these three documented hooks keeps
    composition working without exposing arbitrary monkeypatch propagation.
    """

    for name in _COMPOSED_MECHANICS_HOOKS:
        hook = globals().get(name)
        if callable(hook):
            setattr(_core, name, hook)


def _cleanup_failure_subtype(observations: dict[str, Any]) -> str:
    """Classify one cleanup failure from formal receipt evidence only."""

    evidence = _dict(observations)
    equivalence = _dict(evidence.get("cleanup_equivalence_receipt"))
    equivalence_status = _text(equivalence.get("equivalence_status")).upper()
    equivalence_reason = _text(equivalence.get("reason_code")).upper()
    if (
        equivalence_status == "NOT_EQUIVALENT"
        or equivalence_reason in {
            "CLEANUP_EQUIVALENCE_FAILED",
            "ENTITY_STILL_PRESENT_AFTER_CLEANUP",
            "FIELD_VALUE_NOT_RESTORED",
            "BUSINESS_STATE_NOT_RESTORED",
        }
    ):
        return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    cleanup = _dict(evidence.get("cleanup_execution_receipt"))
    if not cleanup:
        cleanup_result = _dict(evidence.get("cleanup_result"))
        cleanup = _dict(cleanup_result.get("cleanup_execution_receipt"))
        if not cleanup and _text(cleanup_result.get("schema_version")) == (
            "qualibug.cleanup-execution-receipt.v1"
        ):
            cleanup = cleanup_result

    if cleanup:
        status = _text(cleanup.get("status")).upper()
        reason = _text(cleanup.get("reason_code") or cleanup.get("error")).upper()
        attempted = cleanup.get("attempted") is True
        transport_reached = cleanup.get("transport_reached") is True
        status_code = _status_code(cleanup.get("status_code"))

        if (
            attempted
            and transport_reached is False
            and status_code == 0
        ) or any(
            marker in reason
            for marker in (
                "TRANSPORT",
                "CONNECTION",
                "TIMEOUT",
                "NETWORK",
            )
        ):
            return "HARNESS_CLEANUP_TRANSPORT_FAILED"

        if (
            attempted
            and transport_reached
            and status_code >= 400
        ) or status in {"REJECTED", "RESPONSE_REJECTED"}:
            return "HARNESS_CLEANUP_RESPONSE_REJECTED"

        if any(
            marker in reason
            for marker in (
                "EQUIVALENCE",
                "NOT_RESTORED",
                "STATE_NOT_RESTORED",
            )
        ):
            return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    cleanup_status = _text(evidence.get("cleanup_status")).upper()
    if cleanup_status in {"TRANSPORT_ERROR", "CONNECTION_FAILED"}:
        return "HARNESS_CLEANUP_TRANSPORT_FAILED"
    if cleanup_status in {"REJECTED", "RESPONSE_REJECTED"}:
        return "HARNESS_CLEANUP_RESPONSE_REJECTED"
    if cleanup_status in {"EQUIVALENCE_FAILED", "STATE_NOT_RESTORED"}:
        return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    return HARNESS_CLEANUP_FAILURE_UNATTRIBUTED


def _classify_harness_failure(
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    pre_transport_block_reasons: list[str],
    cleanup_failures: int = 0,
) -> str:
    """Use exact cleanup receipts; delegate every non-cleanup case unchanged."""

    if cleanup_failures:
        return _cleanup_failure_subtype(observations)
    return _original_classify_harness_failure(
        steps_out,
        observations,
        pre_transport_block_reasons,
        cleanup_failures=0,
    )


def _actual_accepted_business_write(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> bool:
    """Whether a control/treatment write actually reached a 2xx accepted state.

    A declared write operation alone is not enough: an expected 4xx treatment
    must not demand cleanup.  Conversely, a successful control write is real
    state-change evidence even when the experiment belongs to a response-only
    family.  Source-declared ephemeral exchanges remain exempt only through the
    compiled cleanup/business-effect contract, never through family names.
    """

    safety = _dict(exp.get("safety_contract"))
    if (
        safety.get("cleanup_not_required") is True
        and _text(safety.get("business_effect_requirement")).upper()
        == "NOT_APPLICABLE"
    ):
        return False

    # A runtime-observed ordering violation (FIFO/FEFO) is produced by the
    # body-field probe re-issuing a read-only decision-endpoint write. The
    # probe is a pure observation — no durable business write occurred, so it
    # is never an "accepted business write" and never demands cleanup.
    for raw in _list(steps_out):
        step = _dict(raw)
        governance = _dict(step.get("governance_receipt"))
        if _dict(governance.get("_undocumented_field_probe")):
            return False

    for raw in _list(steps_out):
        step = _dict(raw)
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if _text(step.get("method")).upper() not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            continue
        governance = _dict(step.get("governance_receipt"))
        if governance.get("accepted") is True:
            return True
        status = _status_code(step.get("status_code") or step.get("status"))
        if 200 <= status < 300:
            return True
    return False


def _precompute_actual_write_cleanup_equivalence(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    runtime_bindings: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the restoration receipt the historical family exemption skipped."""

    if not _actual_accepted_business_write(exp=exp, steps_out=steps_out):
        return None
    proof = _dict(exp.get("write_reversibility_proof"))
    if _text(proof.get("proof_status")).upper() != "PROVEN":
        return None

    cleanup_result = _dict(observations.get("cleanup_result"))
    sealed_after = _dict(observations.get("after_cleanup_observation"))
    if sealed_after and not _dict(cleanup_result.get("after_cleanup_observation")):
        cleanup_result = {
            **cleanup_result,
            "after_cleanup_observation": sealed_after,
            "observation_path": _text(sealed_after.get("path")),
            "after": {
                "status": _status_code(
                    sealed_after.get("status_code") or sealed_after.get("status")
                ),
                "body": sealed_after.get("body"),
            },
        }

    equiv_inputs = _core.build_cleanup_equivalence_inputs(
        exp=exp,
        observations=observations,
        steps_out=steps_out,
        cleanup_result=cleanup_result,
    )
    observations["cleanup_observation_source_trace"] = equiv_inputs[
        "source_trace"
    ]
    cleanup_execution_receipt = _dict(
        equiv_inputs.get("cleanup_execution_receipt")
    )
    if cleanup_execution_receipt:
        observations["cleanup_execution_receipt"] = cleanup_execution_receipt
        if _text(cleanup_execution_receipt.get("receipt_id")):
            observations["cleanup_execution_receipts"] = [
                cleanup_execution_receipt
            ]

    receipt = _core.evaluate_cleanup_equivalence(
        proof=proof,
        before_observation=equiv_inputs["before_observation"],
        after_write_observation=equiv_inputs["after_write_observation"],
        after_cleanup_observation=equiv_inputs["after_cleanup_observation"],
        runtime_bindings=runtime_bindings,
        cleanup_execution_receipt=cleanup_execution_receipt,
    )
    observations["cleanup_equivalence_receipt"] = receipt
    if _text(receipt.get("receipt_id")):
        observations["cleanup_verification_receipts"] = [dict(receipt)]
    return receipt


def _fail_closed_actual_write_cleanup(
    result: dict[str, Any],
    *,
    cleanup_receipt: dict[str, Any] | None,
    cleanup_failures: int,
) -> dict[str, Any]:
    """Remove false restoration/delivery claims when proof is not PASSED."""

    gate, reason = _core._cleanup_equivalence_gate(
        is_governed_write=True,
        cleanup_equivalence_receipt=cleanup_receipt,
    )
    governed = dict(result)
    governed["cleanup_equivalence_receipt"] = cleanup_receipt
    if gate == "PASSED" and cleanup_failures == 0:
        return governed

    reason = reason or (
        "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED"
        if cleanup_failures
        else "BLOCKED_CLEANUP_EQUIVALENCE_MISSING"
    )
    governed["environment_restored"] = False
    governed["finding"] = None
    governed["finding_created"] = False
    governed["finding_filter_reason"] = "environment_not_restored"
    governed["finalizer_block_reason"] = reason
    governed["lifecycle_state"] = (
        _core.LIFECYCLE_CLEANUP_FAILED
        if cleanup_failures
        else _core.LIFECYCLE_EXECUTED_BUT_NOT_RESTORED
    )
    if _text(governed.get("status")) == "EXECUTED":
        governed["status"] = "EXECUTED_BUT_NOT_RESTORED"
    if not _text(governed.get("reason_code")):
        governed["reason_code"] = reason
    if not _text(governed.get("detail")):
        governed["detail"] = reason

    governed["execution_finalization_receipt"] = {}
    execution_receipt = dict(_dict(governed.get("execution_receipt")))
    execution_receipt.update(
        {
            "status": governed.get("status"),
            "environment_restored": False,
            "lifecycle_state": governed["lifecycle_state"],
            "cleanup_equivalence_status": _text(
                _dict(cleanup_receipt).get("equivalence_status")
            )
            or None,
            "harness_failure_reason": (
                reason if cleanup_failures else execution_receipt.get(
                    "harness_failure_reason"
                )
            ),
        }
    )
    governed["execution_receipt"] = execution_receipt
    return governed


def finalize_experiment_execution(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    fixture_receipts: list[dict[str, Any]],
    binding_materialization_receipts: list[dict[str, Any]],
    pre_transport_block_reasons: list[str],
    cleanup_failures: int,
    runtime_bindings: dict[str, Any],
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    eid: str,
    oid: str,
    campaign_id: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    started: float,
) -> dict[str, Any]:
    """Finalize with exact composition and actual-write cleanup authority."""

    # The scope facade installs its callables on this module.  Sync them into
    # the extracted implementation before any precomputation or Oracle work.
    _sync_composed_finalizer_hooks()

    actual_write = _actual_accepted_business_write(
        exp=exp,
        steps_out=steps_out,
    )
    cleanup_receipt: dict[str, Any] | None = None
    if actual_write:
        try:
            cleanup_receipt = _precompute_actual_write_cleanup_equivalence(
                exp=exp,
                steps_out=steps_out,
                observations=observations,
                runtime_bindings=runtime_bindings,
            )
        except Exception as exc:
            observations["actual_write_cleanup_precompute_error"] = (
                f"{type(exc).__name__}:{exc}"
            )[:240]
            cleanup_receipt = None

    result = _original_finalize_experiment_execution(
        exp=exp,
        steps_out=steps_out,
        observations=observations,
        contract_evidence_receipts=contract_evidence_receipts,
        fixture_receipts=fixture_receipts,
        binding_materialization_receipts=binding_materialization_receipts,
        pre_transport_block_reasons=pre_transport_block_reasons,
        cleanup_failures=cleanup_failures,
        runtime_bindings=runtime_bindings,
        ops=ops,
        actors=actors,
        eid=eid,
        oid=oid,
        campaign_id=campaign_id,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        started=started,
    )
    if not actual_write:
        return result
    return _fail_closed_actual_write_cleanup(
        result,
        cleanup_receipt=cleanup_receipt,
        cleanup_failures=cleanup_failures,
    )


_core._classify_harness_failure = _classify_harness_failure
_core.HARNESS_FAILURE_SUBTYPES = HARNESS_FAILURE_SUBTYPES

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED",
        "HARNESS_FAILURE_SUBTYPES",
        "finalize_experiment_execution",
    }
)
