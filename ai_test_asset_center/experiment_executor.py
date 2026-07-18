"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .contract_oracles import contract_activation_requirements
from .real_id_resolver import bind_entity_fields  # compatibility re-export
from .sandbox_write_executor import (  # noqa: F401
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)

from .experiment_fixture_materializer import materialize_experiment_fixtures
from .experiment_barrier_executor import execute_barrier_plans
from .experiment_plan_executor import execute_non_barrier_plans
from .experiment_outcome_finalizer import finalize_experiment_execution
from .experiment_cleanup_executor import execute_experiment_cleanup_compensation
from .experiment_cleanup import (  # noqa: F401
    _cleanup_compensates_created_resource,
    _cleanup_restores_governed_write,
    _cleanup_restores_mutated_fields,
    _entity_matches_identity,
    _entity_rows,
    _governed_write_attempts,
    _governed_write_changed_state,
    _meaningful_observation_state,
    _primary_resource_identity_candidates,
    _rejected_writes_left_state_unchanged,
    _resource_identity_candidates,
    _server_managed_field,
    _single_entity_for_restoration,
    _without_server_managed_fields,
)
from .experiment_runtime_support import (  # noqa: F401
    _BODY_PLACEHOLDER_RE,
    _WRITE_METHODS,
    _body_contains_scalar,
    _canonical_json,
    _declared_effect_observer_available,
    _declared_observation_path,
    _dict,
    _documented_routes,
    _governance_audit_receipt_id,
    _index_by_id,
    _inverse_delta_cleanup_body,
    _list,
    _observation_state,
    _operation_for_observation_path,
    _request_example,
    _resolve_token,
    _response_bound_observation_path,
    _run_http_step,
    _runtime_entity_candidates,
    _scalar_body_bindings,
    _select_fixture_actor,
    _select_runtime_binding,
    _sha256,
    _stable_id,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
    load_actor_tokens,
    preflight_experiment_executable,
)


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
    """Execute one experiment or return an explicit blocked/harness receipt."""
    exp = _dict(experiment)
    eid = _text(exp.get("experiment_id"))
    oid = _text(exp.get("obligation_id"))
    resolved_campaign_id = _text(campaign_id)
    resolved_execution_id = _text(execution_id)
    if not resolved_campaign_id or not resolved_execution_id:
        raise ValueError("experiment execution campaign_id and execution_id are required")
    exp["campaign_id"] = resolved_campaign_id
    exp["execution_id"] = resolved_execution_id
    tokens = actor_tokens if actor_tokens is not None else load_actor_tokens(root, project)
    ok, reason, detail = preflight_experiment_executable(exp, behavior_ir=behavior_ir, actor_tokens=tokens)
    started = time.time()
    if not ok:
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": "BLOCKED",
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "finding": None,
            "execution_receipt": {"status": "BLOCKED", "reason_code": reason, "detail": detail},
        }

    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    steps_out: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    observations: dict[str, Any] = {
        "observer_ids": [],
        "observer_receipts": [],
        "control_succeeded": False,
        "harness_error": False,
    }
    activation_requirements = contract_activation_requirements(exp)
    contract_evidence_receipts: list[dict[str, Any]] = []
    cleanup_failures = 0
    pre_transport_block_reasons: list[str] = []
    fixture_receipts: list[dict[str, Any]] = []
    binding_materialization_receipts: list[dict[str, Any]] = []
    runtime_bindings: dict[str, Any] = {}
    pending_fixture_cleanups: list[dict[str, Any]] = []
    binding_plan = {
        _text(item.get("target")): item
        for item in _list(exp.get("binding_plan"))
        if isinstance(item, dict) and _text(item.get("target"))
    }
    resolver_actor_ref = ""
    for planned in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if isinstance(planned, dict) and _text(planned.get("actor_ref")):
            resolver_actor_ref = _text(planned.get("actor_ref"))
            break
    resolver_actor = actors.get(resolver_actor_ref) or {}
    resolver_token = _resolve_token(resolver_actor, tokens)

    fixture_state = materialize_experiment_fixtures(
        exp=exp,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        started=started,
        actors=actors,
        ops=ops,
        tokens=tokens,
        binding_plan=binding_plan,
        resolver_actor_ref=resolver_actor_ref,
        resolver_token=resolver_token,
        activation_requirements=activation_requirements,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        campaign_id=resolved_campaign_id,
    )
    if fixture_state.get("status") == "terminal":
        return dict(fixture_state.get("result") or {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_FIXTURE",
            "finding": None,
        })
    steps_out = list(fixture_state.get("steps_out") or [])
    fixture_receipts = list(fixture_state.get("fixture_receipts") or [])
    binding_materialization_receipts = list(
        fixture_state.get("binding_materialization_receipts") or []
    )
    runtime_bindings = dict(fixture_state.get("runtime_bindings") or {})
    pending_fixture_cleanups = list(fixture_state.get("pending_fixture_cleanups") or [])
    cleanup_failures = int(fixture_state.get("cleanup_failures") or 0)
    contract_evidence_receipts = list(
        fixture_state.get("contract_evidence_receipts") or []
    )

    # A forced disposable fixture is a source-resolver fallback, not proof that
    # setup ran. Runtime binding receipts freeze whether cleanup responsibility
    # actually materialized; missing or ambiguous evidence remains fail-closed.
    activation_requirements = contract_activation_requirements(
        exp,
        evidence={
            "binding_materialization_receipts": binding_materialization_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
        },
    )

    barrier_result = execute_barrier_plans(
        control_plan=_list(exp.get("control_plan")),
        treatment_plan=_list(exp.get("treatment_plan")),
        actors=actors,
        ops=ops,
        tokens=tokens,
        runtime_bindings=runtime_bindings,
        activation_requirements=activation_requirements,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=resolved_campaign_id,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        observations=observations,
    )
    steps_out.extend(list(barrier_result.get("steps") or []))
    contract_evidence_receipts.extend(
        list(barrier_result.get("contract_evidence_receipts") or [])
    )
    request_bodies_for_cleanup.update(
        dict(barrier_result.get("request_bodies_for_cleanup") or {})
    )
    pre_transport_block_reasons.extend(
        list(barrier_result.get("pre_transport_block_reasons") or [])
    )
    consumed_barrier_steps = set(barrier_result.get("consumed_barrier_steps") or set())
    control_plan = _list(exp.get("control_plan"))
    treatment_plan = _list(exp.get("treatment_plan"))
    plan_result = execute_non_barrier_plans(
        control_plan=control_plan,
        treatment_plan=treatment_plan,
        consumed_barrier_steps=consumed_barrier_steps,
        actors=actors,
        ops=ops,
        tokens=tokens,
        runtime_bindings=runtime_bindings,
        activation_requirements=activation_requirements,
        observations=observations,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=resolved_campaign_id,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        cleanup_failures=cleanup_failures,
    )
    steps_out.extend(list(plan_result.get("steps") or []))
    contract_evidence_receipts.extend(
        list(plan_result.get("contract_evidence_receipts") or [])
    )
    request_bodies_for_cleanup.update(
        dict(plan_result.get("request_bodies_for_cleanup") or {})
    )
    pre_transport_block_reasons.extend(
        list(plan_result.get("pre_transport_block_reasons") or [])
    )
    cleanup_failures = int(plan_result.get("cleanup_failures") or cleanup_failures)

    cleanup_result = execute_experiment_cleanup_compensation(
        exp=exp,
        steps_out=steps_out,
        observations=observations,
        contract_evidence_receipts=contract_evidence_receipts,
        activation_requirements=activation_requirements,
        pre_transport_block_reasons=pre_transport_block_reasons,
        request_bodies_for_cleanup=request_bodies_for_cleanup,
        runtime_bindings=runtime_bindings,
        pending_fixture_cleanups=pending_fixture_cleanups,
        cleanup_failures=cleanup_failures,
        ops=ops,
        actors=actors,
        tokens=tokens,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=resolved_campaign_id,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
    )
    steps_out = list(cleanup_result.get("steps_out") or steps_out)
    observations = dict(cleanup_result.get("observations") or observations)
    contract_evidence_receipts = list(
        cleanup_result.get("contract_evidence_receipts") or contract_evidence_receipts
    )
    cleanup_failures = int(cleanup_result.get("cleanup_failures") or 0)
    return finalize_experiment_execution(
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
        campaign_id=resolved_campaign_id,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        started=started,
    )


from .experiment_batch_executor import execute_selected_experiments  # noqa: E402,F401
