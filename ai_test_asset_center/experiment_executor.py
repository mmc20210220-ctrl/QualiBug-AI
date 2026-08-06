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

from .actor_exploration import ActorAttemptOutcome, ActorSelectionMode
from .actor_exploration_runtime import (
    PermissionObservation,
    build_executable_candidates,
    classify_actor_attempt,
    compute_observation_confidence,
    get_scan_observations,
    log_exploration_attempted,
    log_exploration_discovered,
    log_exploration_exhausted,
    log_exploration_started,
    observation_success_counts,
    permission_context_fingerprint,
    record_permission_observation,
)
from .actor_exploration_execution import (
    apply_actor_execution_overlay,
    exploration_execution_policy,
    exploration_receipt,
    extract_primary_http_attempt_evidence,
    should_continue_actor_exploration,
)


ACTOR_EXECUTION_PLAN_SCHEMA = "qualibug.actor-execution-plan.v1"
_LEGACY_ACTOR_PLAN_KEY = "_actor_exploration_plan"


for _name in dir(_governance):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_governance, _name)


_execute_one_governed = _governance.execute_one_experiment
_governed_load_actor_tokens = _governance._identity_safe_load_actor_tokens
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


def _experiment_property(experiment: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(_dict(experiment).get("property"))
    if direct:
        return direct
    for assertion in _list(_dict(experiment).get("assertions")):
        if isinstance(assertion, dict) and _dict(assertion.get("property")):
            return _dict(assertion.get("property"))
    return {}


def _actor_execution_plan(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Read and verify the compiler-sealed actor plan."""

    exp = _dict(experiment)
    direct = dict(_dict(exp.get("actor_execution_plan")))
    if direct:
        if _text(direct.get("schema_version")) != ACTOR_EXECUTION_PLAN_SCHEMA:
            return {}, "actor_execution_plan_schema_invalid"
        expected_hash = _text(direct.get("plan_hash"))
        hash_input = {
            key: value for key, value in direct.items() if key != "plan_hash"
        }
        actual_hash = hashlib.sha256(
            _canonical(hash_input).encode("utf-8")
        ).hexdigest()
        if not expected_hash or expected_hash != actual_hash:
            return {}, "actor_execution_plan_hash_mismatch"
        return direct, ""

    legacy_locations = []
    direct_property = _dict(exp.get("property"))
    if direct_property:
        legacy_locations.append(direct_property)
    for assertion in _list(exp.get("assertions")):
        if isinstance(assertion, dict):
            legacy_locations.append(_dict(assertion.get("property")))
    for prop in legacy_locations:
        legacy = dict(_dict(prop.get(_LEGACY_ACTOR_PLAN_KEY)))
        if not legacy:
            continue
        candidates = list(dict.fromkeys(
            _text(value)
            for value in _list(legacy.get("candidate_ids"))
            if _text(value)
        ))
        if not _text(legacy.get("mode")) or not candidates:
            return {}, "legacy_actor_execution_plan_incomplete"
        return {
            **legacy,
            "source_actor_id": candidates[0],
            "candidate_ids": candidates,
            "authority": "legacy_assertion_metadata",
        }, ""
    return {}, ""


def _primary_operation_ref(
    experiment: dict[str, Any],
    semantic_property: dict[str, Any],
) -> str:
    required = [
        _text(value)
        for value in _list(_dict(experiment).get("required_operations"))
        if _text(value)
    ]
    if required:
        return required[0]
    property_ref = _text(semantic_property.get("operation_ref"))
    if property_ref:
        return property_ref
    for step in [
        *_list(_dict(experiment).get("treatment_plan")),
        *_list(_dict(experiment).get("control_plan")),
    ]:
        if isinstance(step, dict) and _text(step.get("operation_ref")):
            return _text(step.get("operation_ref"))
    return ""


def _actor_runtime_context(
    experiment: dict[str, Any],
    operation: dict[str, Any],
    semantic_property: dict[str, Any],
) -> dict[str, Any]:
    """Project source/fixture context into the existing candidate scorer."""

    prop = _dict(semantic_property)
    owner = _text(
        prop.get("resource_owner_actor_id")
        or prop.get("owner_actor_ref")
        or prop.get("control_actor_ref")
    )
    creator = _text(
        prop.get("resource_creator_actor_id")
        or prop.get("created_by_actor_ref")
    )
    for binding in _list(_dict(experiment).get("binding_plan")):
        if not isinstance(binding, dict):
            continue
        owner = owner or _text(
            binding.get("owner_actor_ref")
            or binding.get("fixture_owner_actor_ref")
        )
        creator = creator or _text(binding.get("created_by_actor_ref"))
    previous_actor = ""
    for step in reversed(_list(_dict(experiment).get("precondition_plan"))):
        if isinstance(step, dict) and _text(step.get("actor_ref")):
            previous_actor = _text(step.get("actor_ref"))
            break
    entity_refs = [
        _text(value)
        for value in _list(_dict(operation).get("entity_refs"))
        if _text(value)
    ]
    resource_type = _text(operation.get("resource_type")) or (
        entity_refs[0] if entity_refs else ""
    )
    return {
        "resource_creator_actor_id": creator,
        "resource_owner_actor_id": owner,
        "previous_step_actor_id": previous_actor,
        "resource_tenant_id": _text(
            prop.get("resource_tenant_id")
            or prop.get("tenant_id")
            or prop.get("tenant_scope")
        ),
        "resource_type": resource_type,
        "resource_state": _text(
            prop.get("resource_state")
            or prop.get("from_state")
            or prop.get("from_state_ref")
        ),
        "ownership_required": "true" if owner else "false",
    }


def _resource_identity_fingerprint(result: dict[str, Any]) -> str:
    """Derive a non-secret resource identity proof from existing receipt hashes."""

    fingerprints: list[str] = []
    for key in (
        "binding_materialization_receipts",
        "fixture_receipts",
        "contract_evidence_receipts",
    ):
        for raw in _list(_dict(result).get(key)):
            if not isinstance(raw, dict):
                continue
            for field in (
                "resource_identity_fingerprint",
                "binding_identity_fingerprint",
                "identity_fingerprint",
                "value_fingerprint",
                "created_identity_fingerprint",
            ):
                value = _text(raw.get(field))
                if value:
                    fingerprints.append(value)
    if not fingerprints:
        return ""
    return hashlib.sha256(
        _canonical(sorted(set(fingerprints))).encode("utf-8")
    ).hexdigest()


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
    receipt = _dict(result.get("authorization_causality_receipt"))
    finding = _dict(result.get("finding"))
    if _text(receipt.get("status")).upper() != "PASSED" or not finding:
        return result
    output = dict(result)
    sealed = dict(finding)
    for field in ("campaign_id", "obligation_id", "experiment_id", "execution_id"):
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
    blocked = dict(result)
    blocked["finding"] = None
    if _text(blocked.get("status")).upper() not in {"BLOCKED", "HARNESS_FAILURE"}:
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
    """Execute one compiler-sealed plan through bounded contextual exploration."""

    _sync_governance_hooks()
    exp = dict(experiment)
    exp["_observer_runtime_context"] = {
        "root": str(root),
        "project": _text(project),
        "runtime_contract": deepcopy(_dict(runtime_contract)),
    }

    semantic_property = _experiment_property(exp)
    exploration_plan_raw, exploration_plan_error = _actor_execution_plan(exp)
    exploration_mode = _text(exploration_plan_raw.get("mode"))
    candidate_ids = [
        _text(value)
        for value in _list(exploration_plan_raw.get("candidate_ids"))
        if _text(value)
    ]
    try:
        max_attempts = int(exploration_plan_raw.get("max_attempts") or 0)
    except (TypeError, ValueError):
        max_attempts = 0
        exploration_plan_error = "actor_execution_plan_max_attempts_invalid"
    oracle_enabled = bool(
        exploration_plan_raw.get("authorization_oracle_enabled", True)
    )
    exploratory_modes = {
        ActorSelectionMode.PERMISSION_EXPLORATION.value,
        ActorSelectionMode.OBSERVED_PERMISSION.value,
    }
    is_exploration = (
        not exploration_plan_error
        and exploration_mode in exploratory_modes
        and bool(candidate_ids)
        and max_attempts > 0
    )

    ir = _dict(behavior_ir)
    ir_actors = {
        _text(actor.get("id") or actor.get("actor_id")): actor
        for actor in _list(ir.get("actors"))
        if isinstance(actor, dict)
    }
    ir_ops = {
        _text(operation.get("id") or operation.get("operation_id")): operation
        for operation in _list(ir.get("operations"))
        if isinstance(operation, dict)
    }
    obligation_id = _text(exp.get("obligation_id"))
    primary_op_id = _primary_operation_ref(exp, semantic_property)
    primary_op = ir_ops.get(primary_op_id, {})

    if exploration_plan_error:
        blocked = {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": _text(exp.get("experiment_id")),
            "obligation_id": obligation_id,
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": exploration_plan_error,
            "finding": None,
            "execution_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": exploration_plan_error,
            },
        }
        return _finalize_result(
            blocked, exp, behavior_ir, root, project, oracle_enabled=False
        )

    runtime_context = _actor_runtime_context(exp, primary_op, semantic_property)
    context_fingerprint = permission_context_fingerprint(runtime_context)
    scoped_observations: list[PermissionObservation] = []
    ranking_rows: list[dict[str, Any]] = []
    ranking_by_actor: dict[str, Any] = {}

    if is_exploration:
        scoped_observations = get_scan_observations(
            campaign_id=campaign_id,
            project_id=project,
            environment_ref=base_url,
            runtime_context=runtime_context,
        )
        candidate_actor_map = {
            actor_id: ir_actors[actor_id]
            for actor_id in candidate_ids
            if actor_id in ir_actors
        }
        ranked = build_executable_candidates(
            candidate_actor_map,
            operation=primary_op,
            runtime_context=runtime_context,
            permission_observations=scoped_observations,
            permitted_actor_ids=set(candidate_ids),
        )
        candidate_ids = [candidate.actor_id for candidate in ranked]
        ranking_by_actor = {
            candidate.actor_id: candidate for candidate in ranked
        }
        ranking_rows = [
            {
                "actor_id": candidate.actor_id,
                "score": candidate.score,
                "score_reasons": list(candidate.score_reasons),
            }
            for candidate in ranked
        ]
        if not candidate_ids:
            exploration_plan_error = "runtime_actor_candidate_missing"
        else:
            max_attempts = min(max_attempts, len(candidate_ids))

    if exploration_plan_error:
        blocked = {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": _text(exp.get("experiment_id")),
            "obligation_id": obligation_id,
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": exploration_plan_error,
            "finding": None,
            "execution_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": exploration_plan_error,
            },
            "actor_exploration_ranking": ranking_rows,
        }
        return _finalize_result(
            blocked, exp, behavior_ir, root, project, oracle_enabled=False
        )

    if is_exploration:
        policy_allowed, policy_max_attempts, policy_reason = exploration_execution_policy(
            operation=primary_op,
            experiment=exp,
            requested_max_attempts=max_attempts,
        )
        if not policy_allowed:
            detail = (
                f"runtime_actor_exploration_not_allowed:"
                f"{primary_op_id}:{policy_reason}"
            )
            blocked = {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": _text(exp.get("experiment_id")),
                "obligation_id": obligation_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": detail,
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ACTOR",
                    "detail": detail,
                },
                "actor_exploration_ranking": ranking_rows,
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
    discovered_actor_id = ""
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
        candidate_rank = ranking_by_actor.get(candidate_id)
        candidate_score = float(getattr(candidate_rank, "score", 0.0) or 0.0)
        candidate_score_reasons = list(
            getattr(candidate_rank, "score_reasons", []) or []
        )

        try:
            attempt_exp, overlay_receipt = apply_actor_execution_overlay(exp, candidate_id)
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
        evidence = extract_primary_http_attempt_evidence(attempt_result, primary_op_id)
        classification = classify_actor_attempt({
            "status_code": evidence.status_code,
            "business_layer_reached": evidence.business_layer_reached,
        })
        status_code = evidence.status_code
        if status_code in {400, 409, 422} and not evidence.business_layer_reached:
            classification = ActorAttemptOutcome.INCONCLUSIVE

        result_status = _text(attempt_result.get("status"))
        if not status_code and result_status == "BLOCKED":
            reason = _text(attempt_result.get("reason_code"))
            detail = _text(attempt_result.get("detail"))
            combined = f"{reason}:{detail}".lower()
            if "401" in combined or "auth" in reason.lower():
                classification = ActorAttemptOutcome.AUTHENTICATION_FAILED
                status_code = 401
            elif "403" in combined or "permission" in reason.lower() or "forbidden" in combined:
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
            score=candidate_score,
            score_reasons=candidate_score_reasons,
            status_code=status_code,
            outcome=classification.value,
        )
        outcome_key = classification.value
        outcomes[outcome_key] = outcomes.get(outcome_key, 0) + 1

        resource_identity = _resource_identity_fingerprint(attempt_result)
        observation_outcome = _outcome_to_observation(classification)
        if observation_outcome:
            prior_same, prior_different = observation_success_counts(
                observations=scoped_observations,
                actor_id=candidate_id,
                operation_id=primary_op_id,
                context_fingerprint=context_fingerprint,
                resource_identity_fingerprint=resource_identity,
            )
            current_success = observation_outcome == "OBSERVED_ALLOWED"
            owner_id = _text(runtime_context.get("resource_owner_actor_id"))
            observation = PermissionObservation(
                actor_id=candidate_id,
                role_ref=_text(candidate_actor.get("role")),
                operation_id=primary_op_id,
                evidence_ref=_text(
                    attempt_result.get("experiment_id")
                    or attempt_result.get("execution_id")
                    or execution_id
                ),
                outcome=observation_outcome,
                campaign_id=_text(campaign_id),
                project_id=_text(project),
                environment_ref=_text(base_url),
                context_fingerprint=context_fingerprint,
                resource_identity_fingerprint=resource_identity or None,
                resource_type=_text(runtime_context.get("resource_type")) or None,
                tenant_id=_text(runtime_context.get("resource_tenant_id")) or None,
                ownership=(
                    "owner" if owner_id and candidate_id == owner_id
                    else "non_owner" if owner_id
                    else None
                ),
                resource_state=_text(runtime_context.get("resource_state")) or None,
                status_code=status_code,
                confidence=compute_observation_confidence(
                    outcome=observation_outcome,
                    same_context_successes=prior_same + (1 if current_success else 0),
                    different_instance_successes=prior_different,
                ),
            )
            record_permission_observation(observation)
            scoped_observations.append(observation)

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

        attempt_receipt = exploration_receipt(
            attempt_index=attempt_index,
            planned_actor_id=candidate_id,
            overlay_receipt=overlay_receipt,
            evidence=evidence,
            outcome=classification.value,
            continued=(not discovered and continue_allowed),
            continue_reason=continue_reason,
        )
        attempt_receipt.update({
            "candidate_score": candidate_score,
            "candidate_score_reasons": candidate_score_reasons,
            "selection_context_fingerprint": context_fingerprint,
        })
        attempt_receipts.append(attempt_receipt)
        last_result = attempt_result

        if discovered:
            discovered_actor_id = candidate_id
            log_exploration_discovered(
                actor_ref=candidate_ref,
                status_code=status_code,
                binding_scope="campaign_context",
                authorization_verdict="unknown_expectation",
            )
            exp = attempt_exp
            break
        if not continue_allowed:
            terminal_exploration_reason = terminal_exploration_reason or continue_reason
            break

    summary_base = {
        "compiled_plan_hash": _text(exploration_plan_raw.get("plan_hash")),
        "compiled_mode": exploration_mode,
        "runtime_candidate_ranking": ranking_rows,
        "selection_context_fingerprint": context_fingerprint,
        "observation_scope": {
            "project_id": _text(project),
            "campaign_id": _text(campaign_id),
            "environment_ref": _text(base_url),
        },
        "attempted_actor_count": len(attempt_receipts),
        "outcomes": dict(outcomes),
    }

    if not discovered_actor_id:
        log_exploration_exhausted(len(attempt_receipts), outcomes)
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
                + ",".join(f"{key}={value}" for key, value in sorted(outcomes.items()))
                + (f":terminal={terminal_exploration_reason}" if terminal_exploration_reason else "")
            )
            last_result["finding"] = None
            execution_receipt = dict(_dict(last_result.get("execution_receipt")))
            execution_receipt.update({
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": last_result["detail"],
            })
            last_result["execution_receipt"] = execution_receipt
        last_result["actor_exploration_receipts"] = list(attempt_receipts)
        last_result["actor_exploration_summary"] = {
            **summary_base,
            "status": "EXHAUSTED",
            "selected_actor_id": "",
            "terminal_reason": terminal_exploration_reason,
        }
        return _finalize_result(
            last_result, exp, behavior_ir, root, project, oracle_enabled=False
        )

    last_result["actor_exploration_receipts"] = list(attempt_receipts)
    last_result["actor_exploration_summary"] = {
        **summary_base,
        "status": "ACTOR_DISCOVERED",
        "selected_actor_id": discovered_actor_id,
        "terminal_reason": "",
    }
    return _finalize_result(
        last_result, exp, behavior_ir, root, project, oracle_enabled=False
    )


def _outcome_to_observation(classification: Any) -> str | None:
    mapping = {
        "operation_executable": "OBSERVED_ALLOWED",
        "business_rejected": "OBSERVED_ALLOWED",
        "permission_denied": "OBSERVED_DENIED",
        "authentication_failed": "AUTHENTICATION_FAILED",
    }
    return mapping.get(getattr(classification, "value", str(classification)))


def _finalize_result(
    result: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    *,
    oracle_enabled: bool = True,
) -> dict[str, Any]:
    """Run normal validity gates while suppressing unknown auth expectations."""

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
            _text(_dict(governed.get("authorization_causality_receipt")).get("status")).upper()
            == "PASSED"
        )
        if causal_passed and targets:
            observer_proved = any(
                isinstance(observation, dict)
                and _text(observation.get("observer_id")) == "authorization_comparison"
                and _dict(observation.get("evidence")).get("same_resource_proven") is True
                for observation in _list(governed.get("observer_receipts"))
            )
            if not observer_proved:
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
    except (AuthorizationDeliveryGateError, BindingMaterializationIdentityError) as exc:
        return _authorization_delivery_failure(result, exc)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {
        "_governance",
        "_name",
        "_execute_one_governed",
        "_governed_load_actor_tokens",
        "_runtime_load_actor_tokens",
    }
)
