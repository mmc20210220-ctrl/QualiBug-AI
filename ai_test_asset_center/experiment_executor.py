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
from .actor_exploration import ActorSelectionMode
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

    # ── V1.8: Extract exploration plan ──
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

    # ── Build behavior IR actor index for observation recording ──
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

    # ── Single attempt for explicit-permission or non-exploration ──
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

    # ═══════════════════════════════════════════════════════════════
    # ── PERMISSION_EXPLORATION: Multi-Candidate Loop (Step 7) ──
    # ═══════════════════════════════════════════════════════════════

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

    for attempt_index, candidate_id in enumerate(candidate_ids[:max_attempts]):
        candidate_actor = ir_actors.get(candidate_id, {})
        candidate_ref = _text(
            candidate_actor.get("actor_ref")
            or candidate_actor.get("name")
            or candidate_id
        )

        # Update the experiment's actor_ref for this attempt
        attempt_exp = deepcopy(exp)
        attempt_prop = _dict(attempt_exp.get("property"))
        attempt_prop["actor_ref"] = candidate_id
        attempt_exp["property"] = attempt_prop
        # Also update required_actors if present
        if _list(attempt_exp.get("required_actors")):
            attempt_exp["required_actors"] = [candidate_id]

        # Execute with this candidate
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

        # Classify the attempt
        exec_receipt = _dict(attempt_result.get("execution_receipt"))
        http_result = {
            "status_code": int(
                exec_receipt.get("status_code")
                or exec_receipt.get("status")
                or 0
            ),
        }
        classification = classify_actor_attempt(http_result)
        status_code = http_result["status_code"]

        # Derive actual status from the result
        result_status = _text(attempt_result.get("status"))
        if result_status == "BLOCKED":
            # Use the reason_code to refine classification
            reason = _text(attempt_result.get("reason_code") or "")
            detail = _text(attempt_result.get("detail") or "")
            combined = f"{reason}:{detail}".lower()
            if "401" in combined or "auth" in reason.lower():
                classification = classify_actor_attempt({"status_code": 401})
                status_code = 401
            elif "403" in combined or "permission" in reason.lower() or "forbidden" in combined:
                classification = classify_actor_attempt({"status_code": 403})
                status_code = 403
            elif "404" in combined or "not_found" in reason.lower():
                classification = classify_actor_attempt({"status_code": 404})
                status_code = 404

        # Log this attempt
        log_exploration_attempted(
            actor_ref=candidate_ref,
            attempt_index=attempt_index,
            score=0.0,  # score tracked in compile-time plan
            score_reasons=[],
            status_code=status_code,
            outcome=classification.value,
        )

        # Track outcomes
        outcome_key = classification.value
        outcomes[outcome_key] = outcomes.get(outcome_key, 0) + 1

        # Record permission observation
        observation_outcome = _outcome_to_observation(classification)
        if observation_outcome:
            record_permission_observation(PermissionObservation(
                actor_id=candidate_id,
                role_ref=_text(candidate_actor.get("role")),
                operation_id=primary_op_id,
                evidence_ref=_text(attempt_result.get("experiment_id") or execution_id),
                outcome=observation_outcome,
                status_code=status_code,
                confidence=compute_observation_confidence(
                    outcome=observation_outcome,
                    same_context_successes=outcomes.get("operation_executable", 0),
                ),
            ))

        # Check if this actor is usable
        if classification.value in ("operation_executable", "business_rejected"):
            # ── Actor discovered! (Step 10) ──
            discovered_actor_id = candidate_id
            log_exploration_discovered(
                actor_ref=candidate_ref,
                status_code=status_code,
                binding_scope="scenario",
                authorization_verdict="unknown_expectation",
            )
            last_result = attempt_result

            # Update the original experiment's property for the final result
            exp_prop = _dict(exp.get("property"))
            exp_prop["actor_ref"] = candidate_id
            exp_prop["_actor_exploration_discovered"] = candidate_id
            exp["property"] = exp_prop
            break

        if classification.value in (
            "authentication_failed",
            "permission_denied",
            "resource_not_visible",
            "infrastructure_failed",
            "inconclusive",
        ):
            last_result = attempt_result
            continue

        # Unknown classification — preserve result but don't break
        last_result = attempt_result

    # ── All candidates exhausted? ──
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
            # Update reason on the last result
            last_result["status"] = "BLOCKED"
            last_result["reason_code"] = "BLOCKED_MISSING_ACTOR"
            last_result["detail"] = (
                f"runtime_permitted_actor_not_discovered:{primary_op_id}:"
                + ",".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
            )
            last_result["finding"] = None
            er = _dict(last_result.get("execution_receipt"))
            er["status"] = "BLOCKED"
            er["reason_code"] = "BLOCKED_MISSING_ACTOR"
            last_result["execution_receipt"] = er

        return _finalize_result(
            last_result, exp, behavior_ir, root, project,
            oracle_enabled=False,  # exploration mode: oracle OFF
        )

    # ── Actor discovered: continue original experiment (Step 10) ──
    # The last_result already contains the full experiment execution
    # with the discovered actor. Now apply post-execution gates.
    return _finalize_result(
        last_result, exp, behavior_ir, root, project,
        oracle_enabled=False,  # exploration mode: oracle OFF (Step 9)
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

    V1.8: When *oracle_enabled* is False (exploration mode), the authorization
    oracle is skipped and the verdict is set to UNKNOWN_EXPECTATION.
    """
    exp = experiment
    try:
        targets = _authorization_binding_targets(exp)
        prepared = (
            seal_binding_materialization_receipts(result)
            if _dict(exp.get("authorization_comparison_contract"))
            else result
        )

        # ── V1.8: Oracle protection (Step 9) ──
        if not oracle_enabled:
            # Exploration mode — no authorization verdict allowed
            governed = dict(prepared)
            governed["authorization_causality_receipt"] = {
                "status": "NOT_APPLICABLE",
                "reason": "exploration_mode_oracle_disabled",
            }
            finding = _dict(governed.get("finding"))
            if finding:
                finding["authorization_verdict"] = "UNKNOWN_EXPECTATION"
                governed["finding"] = finding
            return governed

        governed = enforce_authorization_oracle_causality(
            result=prepared,
            experiment=exp,
            behavior_ir=behavior_ir,
            account_rows=_governance._test_account_rows(root, project),
        )
        # SPEC §7.6–7.7: Effect Observation Graph + Oracle Validity Gates
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