"""Public experiment execution facade.

Governance, graph-proof, account-identity and authorization-comparison adapters
live in ``experiment_executor_governance``. This module keeps the established
public identities and monkeypatch surface while delegating one execution call.
The final public result additionally applies the authorization causal-evidence
gate and SPEC Oracle Validity Gates (with Effect Observation Graph) so an
Oracle candidate cannot leave the execution boundary without the existing
control/treatment/observer/binding receipt chain and non-vacuous
identity/contrast/evidence proof. Passed authorization findings embed that
complete receipt and exact binding proofs before Gate v2 fingerprints the
customer-facing payload.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import experiment_executor_governance as _governance
from .authorization_delivery_gate import (
    AuthorizationDeliveryGateError,
    attach_authorization_delivery_evidence,
)
from .authorization_oracle_causality import (
    enforce_authorization_oracle_causality,
)
from .oracle_validity_gates import enforce_oracle_validity_gates
from .binding_materialization_identity_receipt import (
    BindingMaterializationIdentityError,
    binding_identity_proofs_for_targets,
    seal_binding_materialization_receipts,
)
from .experiment_runtime_support import (
    load_actor_tokens as _runtime_load_actor_tokens,
)

# ── V1.8: Runtime Actor Exploration ──
from .actor_exploration import ActorAttemptOutcome, ActorSelectionMode
from .actor_exploration_runtime import (
    classify_actor_attempt,
    log_exploration_attempted,
    log_exploration_discovered,
    log_exploration_exhausted,
    log_exploration_started,
    record_permission_observation,
    PermissionObservation,
    compute_observation_confidence,
)
from .actor_exploration_execution import (
    apply_actor_execution_overlay,
    exploration_execution_policy,
    exploration_receipt,
    extract_primary_http_attempt_evidence,
    should_continue_actor_exploration,
)


for _name in dir(_governance):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_governance, _name)


_execute_one_governed = _governance.execute_one_experiment
_governed_load_actor_tokens = _governance._identity_safe_load_actor_tokens

# Preserve the historical public identity required by architecture contracts.
# The governed delegate still uses its account-safe loader by default.
load_actor_tokens = _runtime_load_actor_tokens

_HOOK_NAMES = (
    "_http_request",
    "_run_http_step",
    "_resolve_token",
    "execute_governed_control_write",
    "sandbox_write_allowed",
    "materialize_experiment_fixtures",
    "execute_barrier_plans",
    "execute_non_barrier_plans",
    "execute_experiment_cleanup_compensation",
    "execute_database_observer_phase",
    "finalize_experiment_execution",
    "validate_cleanup_plan",
)


def _sync_governance_hooks() -> None:
    """Propagate explicit public injection points without weakening defaults."""
    for name in _HOOK_NAMES:
        value = globals().get(name)
        if value is not None and hasattr(_governance, name):
            setattr(_governance, name, value)
    public_loader = globals().get("load_actor_tokens")
    if public_loader is _runtime_load_actor_tokens:
        _governance.load_actor_tokens = _governed_load_actor_tokens
    elif public_loader is not None:
        _governance.load_actor_tokens = public_loader


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _authorization_binding_targets(
    experiment: dict[str, Any],
) -> list[str]:
    contract = _dict(experiment.get("authorization_comparison_contract"))
    return [
        _text(value)
        for value in _list(contract.get("resource_identity_binding_targets"))
        if _text(value)
    ]


def _verify_authorization_compile_identity(
    result: dict[str, Any],
    experiment: dict[str, Any],
) -> None:
    """Prove the causal receipt was built against the current compiled contract."""
    receipt = _dict(result.get("authorization_causality_receipt"))
    contract = _dict(experiment.get("authorization_comparison_contract"))
    if not receipt or not contract or _text(receipt.get("status")).upper() != "PASSED":
        return
    expected_contract_fingerprint = hashlib.sha256(
        _canonical(contract).encode("utf-8")
    ).hexdigest()
    if _text(receipt.get("comparison_contract_fingerprint")) != expected_contract_fingerprint:
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_comparison_contract_fingerprint_mismatch"
        )
    expected_binding_graph_fingerprint = _text(
        contract.get("shared_binding_graph_fingerprint")
    )
    if (
        not expected_binding_graph_fingerprint
        or _text(receipt.get("compile_binding_graph_fingerprint"))
        != expected_binding_graph_fingerprint
    ):
        raise AuthorizationDeliveryGateError(
            "authorization_delivery_binding_graph_fingerprint_mismatch"
        )


def _seal_authorization_finding_lineage(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Bind the finding to the exact causal campaign/experiment execution."""
    receipt = _dict(result.get("authorization_causality_receipt"))
    finding = _dict(result.get("finding"))
    if _text(receipt.get("status")).upper() != "PASSED" or not finding:
        return result
    output = dict(result)
    sealed = dict(finding)
    for field in (
        "campaign_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
    ):
        expected = _text(receipt.get(field))
        current = _text(sealed.get(field))
        if not expected:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_finding_lineage_missing:{field}"
            )
        if current and current != expected:
            raise AuthorizationDeliveryGateError(
                f"authorization_delivery_finding_lineage_mismatch:{field}"
            )
        sealed[field] = expected
    output["finding"] = sealed
    return output


def _authorization_delivery_failure(
    result: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    """Preserve execution fact while removing an unpublishable finding."""
    blocked = dict(result)
    blocked["finding"] = None
    if _text(blocked.get("status")).upper() not in {
        "BLOCKED",
        "HARNESS_FAILURE",
    }:
        blocked["status"] = "EXECUTED"
    blocked["reason_code"] = "AUTHORIZATION_DELIVERY_EVIDENCE_INVALID"
    blocked["detail"] = str(exc)
    verdict = dict(
        blocked.get("oracle_verdict")
        if isinstance(blocked.get("oracle_verdict"), dict)
        else {}
    )
    verdict.update({
        "status": "INDETERMINATE",
        "verdict": "blocked_experiment",
        "customer_deliverable_candidate": False,
        "authorization_delivery_gate": "INDETERMINATE",
        "authorization_delivery_reason": str(exc),
    })
    blocked["oracle_verdict"] = verdict
    return blocked


def execute_one_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    execution_id: str,
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute through governance, causal validation, then delivery packaging.

    V1.8: When the experiment carries a ``_actor_exploration_plan`` with
    PERMISSION_EXPLORATION mode, the executor iterates through ranked
    candidates, classifies each response, and binds the first usable actor.
    The authorization oracle is disabled during exploration.
    """
    _sync_governance_hooks()
    exp = dict(experiment)
    exp["_observer_runtime_context"] = {
        "root": str(root),
        "project": _text(project),
        "runtime_contract": deepcopy(_dict(runtime_contract)),
    }

    prop = _dict(exp.get("property"))
    exploration_plan_raw = _dict(prop.get("_actor_exploration_plan"))
    exploration_mode = _text(exploration_plan_raw.get("mode"))
    candidate_ids = _list(exploration_plan_raw.get("candidate_ids"))
    max_attempts = int(exploration_plan_raw.get("max_attempts") or 0)
    oracle_enabled = bool(exploration_plan_raw.get("authorization_oracle_enabled", True))

    is_exploration = (
        exploration_mode == ActorSelectionMode.PERMISSION_EXPLORATION.value
        and len(candidate_ids) > 0
        and max_attempts > 0
    )

    ir = _dict(behavior_ir)
    ir_actors = {
        _text(a.get("id") or a.get("actor_id")): a
        for a in _list(ir.get("actors"))
        if isinstance(a, dict)
    }
    ir_ops = {
        _text(o.get("id") or o.get("operation_id")): o
        for o in _list(ir.get("operations"))
        if isinstance(o, dict)
    }

    obligation_id = _text(exp.get("obligation_id"))
    primary_op_id = _text(
        _list(exp.get("required_operations") or [])[0]
        if _list(exp.get("required_operations"))
        else prop.get("operation_ref") or ""
    )
    primary_op = ir_ops.get(primary_op_id, {})

    if is_exploration:
        policy_allowed, policy_max_attempts, policy_reason = (
            exploration_execution_policy(
                operation=primary_op,
                experiment=exp,
                requested_max_attempts=max_attempts,
            )
        )
        if not policy_allowed:
            blocked = {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": _text(exp.get("experiment_id")),
                "obligation_id": obligation_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": (
                    f"runtime_actor_exploration_not_allowed:"
                    f"{primary_op_id}:{policy_reason}"
                ),
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ACTOR",
                    "detail": (
                        f"runtime_actor_exploration_not_allowed:"
                        f"{primary_op_id}:{policy_reason}"
                    ),
                },
            }
            return _finalize_result(
                blocked, exp, behavior_ir, root, project, oracle_enabled=False
            )
        max_attempts = min(max_attempts, policy_max_attempts)

    if not is_exploration:
        result = _execute_one_governed(
            exp, behavior_ir=behavior_ir, root=root, project=project,
            base_url=base_url, runtime_contract=runtime_contract,
            campaign_id=campaign_id, execution_id=execution_id,
            actor_tokens=actor_tokens,
        )
        return _finalize_result(
            result, exp, behavior_ir, root, project,
            oracle_enabled=oracle_enabled,
        )

    log_exploration_started(
        obligation_id=obligation_id,
        operation_id=primary_op_id,
        candidate_count=len(candidate_ids),
        max_attempts=max_attempts,
        authorization_oracle_enabled=False,
    )

    outcomes: dict[str, int] = {}
    last_result: dict[str, Any] | None = None
    discovered_actor_id: str = ""
    attempt_receipts: list[dict[str, Any]] = []
    terminal_exploration_reason = ""
    primary_method = _text(primary_op.get("method")).upper()

    for attempt_index, candidate_id in enumerate(candidate_ids[:max_attempts]):
        candidate_actor = ir_actors.get(candidate_id, {})
        candidate_ref = _text(
            candidate_actor.get("actor_ref")
            or candidate_actor.get("name")
            or candidate_id
        )

        try:
            attempt_exp, overlay_receipt = apply_actor_execution_overlay(
                exp, candidate_id
            )
        except ValueError as exc:
            terminal_exploration_reason = str(exc)
            last_result = {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": _text(exp.get("experiment_id")),
                "obligation_id": obligation_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": terminal_exploration_reason,
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ACTOR",
                    "detail": terminal_exploration_reason,
                },
            }
            break

        attempt_result = _execute_one_governed(
            attempt_exp,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            execution_id=f"{execution_id}_a{attempt_index}",
            actor_tokens=actor_tokens,
        )

        evidence = extract_primary_http_attempt_evidence(
            attempt_result, primary_op_id
        )
        classification = classify_actor_attempt({
            "status_code": evidence.status_code,
            "business_layer_reached": evidence.business_layer_reached,
        })
        status_code = evidence.status_code

        if (
            status_code in {400, 409, 422}
            and not evidence.business_layer_reached
        ):
            classification = ActorAttemptOutcome.INCONCLUSIVE

        result_status = _text(attempt_result.get("status"))
        if not status_code and result_status == "BLOCKED":
            reason = _text(attempt_result.get("reason_code") or "")
            detail = _text(attempt_result.get("detail") or "")
            combined = f"{reason}:{detail}".lower()
            if "401" in combined or "auth" in reason.lower():
                classification = ActorAttemptOutcome.AUTHENTICATION_FAILED
                status_code = 401
            elif (
                "403" in combined
                or "permission" in reason.lower()
                or "forbidden" in combined
            ):
                classification = ActorAttemptOutcome.PERMISSION_DENIED
                status_code = 403
            elif "404" in combined or "not_found" in reason.lower():
                classification = ActorAttemptOutcome.RESOURCE_NOT_VISIBLE
                status_code = 404

        effective_actor = _text(evidence.actor_ref)
        if effective_actor != candidate_id:
            classification = ActorAttemptOutcome.INCONCLUSIVE
            terminal_exploration_reason = (
                "runtime_actor_identity_unproven:"
                f"planned={candidate_id}:effective={effective_actor or 'missing'}"
            )

        log_exploration_attempted(
            actor_ref=candidate_ref,
            attempt_index=attempt_index,
            score=0.0,
            score_reasons=[],
            status_code=status_code,
            outcome=classification.value,
        )

        outcome_key = classification.value
        outcomes[outcome_key] = outcomes.get(outcome_key, 0) + 1

        observation_outcome = _outcome_to_observation(classification)
        if observation_outcome:
            record_permission_observation(PermissionObservation(
                actor_id=candidate_id,
                role_ref=_text(candidate_actor.get("role")),
                operation_id=primary_op_id,
                evidence_ref=_text(
                    attempt_result.get("experiment_id") or execution_id
                ),
                outcome=observation_outcome,
                status_code=status_code,
                confidence=compute_observation_confidence(
                    outcome=observation_outcome,
                    same_context_successes=outcomes.get(
                        "operation_executable", 0
                    ),
                ),
            ))

        discovered = classification in {
            ActorAttemptOutcome.OPERATION_EXECUTABLE,
            ActorAttemptOutcome.BUSINESS_REJECTED,
        }
        continue_allowed, continue_reason = should_continue_actor_exploration(
            method=primary_method,
            outcome=classification.value,
            status_code=status_code,
        )
        if terminal_exploration_reason:
            continue_allowed = False
            continue_reason = terminal_exploration_reason

        attempt_receipts.append(exploration_receipt(
            attempt_index=attempt_index,
            planned_actor_id=candidate_id,
            overlay_receipt=overlay_receipt,
            evidence=evidence,
            outcome=classification.value,
            continued=(not discovered and continue_allowed),
            continue_reason=continue_reason,
        ))

        last_result = attempt_result
        if discovered:
            discovered_actor_id = candidate_id
            log_exploration_discovered(
                actor_ref=candidate_ref,
                status_code=status_code,
                binding_scope="scenario",
                authorization_verdict="unknown_expectation",
            )
            exp = attempt_exp
            exp_prop = _dict(exp.get("property"))
            exp_prop["_actor_exploration_discovered"] = candidate_id
            exp["property"] = exp_prop
            break

        if not continue_allowed:
            if not terminal_exploration_reason:
                terminal_exploration_reason = continue_reason
            break

    if not discovered_actor_id:
        log_exploration_exhausted(
            attempted_actor_count=len(candidate_ids[:max_attempts]),
            outcomes=outcomes,
        )
        if last_result is None:
            last_result = {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": _text(exp.get("experiment_id")),
                "obligation_id": obligation_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": f"runtime_permitted_actor_not_discovered:{primary_op_id}",
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ACTOR",
                    "detail": f"runtime_permitted_actor_not_discovered:{primary_op_id}",
                },
            }
        else:
            last_result["status"] = "BLOCKED"
            last_result["reason_code"] = "BLOCKED_MISSING_ACTOR"
            last_result["detail"] = (
                f"runtime_permitted_actor_not_discovered:{primary_op_id}:"
                + ",".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
                + (
                    f":terminal={terminal_exploration_reason}"
                    if terminal_exploration_reason
                    else ""
                )
            )
            last_result["finding"] = None
            er = _dict(last_result.get("execution_receipt"))
            er["status"] = "BLOCKED"
            er["reason_code"] = "BLOCKED_MISSING_ACTOR"
            last_result["execution_receipt"] = er

        last_result["actor_exploration_receipts"] = list(attempt_receipts)
        return _finalize_result(
            last_result, exp, behavior_ir, root, project,
            oracle_enabled=False,
        )

    last_result["actor_exploration_receipts"] = list(attempt_receipts)
    return _finalize_result(
        last_result, exp, behavior_ir, root, project,
        oracle_enabled=False,
    )


def _outcome_to_observation(classification: Any) -> str | None:
    """Map ActorAttemptOutcome to PermissionObservation outcome string."""
    mapping = {
        "operation_executable": "OBSERVED_ALLOWED",
        "permission_denied": "OBSERVED_DENIED",
        "authentication_failed": "AUTHENTICATION_FAILED",
    }
    value = getattr(classification, "value", str(classification))
    return mapping.get(value)


def _finalize_result(
    result: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    *,
    oracle_enabled: bool = True,
) -> dict[str, Any]:
    """Post-execution gates: oracle causality, validity, delivery packaging.

    When authorization expectation is unknown, authorization causality is
    skipped but the general Oracle Validity Gates still run.
    """
    exp = experiment
    try:
        targets = _authorization_binding_targets(exp)
        prepared = (
            seal_binding_materialization_receipts(result)
            if _dict(exp.get("authorization_comparison_contract"))
            else result
        )

        if not oracle_enabled:
            governed = dict(prepared)
            governed["authorization_causality_receipt"] = {
                "status": "NOT_APPLICABLE",
                "reason": "exploration_mode_authorization_expectation_unknown",
            }
            finding = _dict(governed.get("finding"))
            if finding:
                finding["authorization_verdict"] = "UNKNOWN_EXPECTATION"
                governed["finding"] = finding
            return enforce_oracle_validity_gates(
                result=governed,
                experiment=exp,
            )

        governed = enforce_authorization_oracle_causality(
            result=prepared,
            experiment=exp,
            behavior_ir=behavior_ir,
            account_rows=_governance._test_account_rows(root, project),
        )
        governed = enforce_oracle_validity_gates(
            result=governed,
            experiment=exp,
        )
        causal_passed = (
            _text(
                _dict(governed.get("authorization_causality_receipt")).get(
                    "status"
                )
            ).upper()
            == "PASSED"
        )
        if causal_passed and targets:
            _observer_proved = any(
                isinstance(_obs, dict)
                and _text(_obs.get("observer_id")) == "authorization_comparison"
                and _dict(_obs.get("evidence")).get("same_resource_proven") is True
                for _obs in _list(governed.get("observer_receipts"))
            )
            if not _observer_proved:
                binding_identity_proofs_for_targets(
                    _list(governed.get("binding_materialization_receipts")),
                    targets,
                )
        _verify_authorization_compile_identity(governed, exp)
        packaged = attach_authorization_delivery_evidence(
            governed,
            experiment=exp,
        )
        return _seal_authorization_finding_lineage(packaged)
    except (
        AuthorizationDeliveryGateError,
        BindingMaterializationIdentityError,
    ) as exc:
        return _authorization_delivery_failure(result, exc)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_governance",
        "_name",
        "_execute_one_governed",
        "_governed_load_actor_tokens",
        "_runtime_load_actor_tokens",
    }
)
