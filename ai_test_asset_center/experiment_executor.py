"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

_exec_logger = logging.getLogger("qualibug.execution")

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
from .cleanup_plan_validator import validate_cleanup_plan
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
    """Execute one experiment or return an explicit blocked/harness receipt.

    Strict mode only: preflight failure returns BLOCKED immediately.
    No automatic best-effort degradation is permitted on the formal path.
    """
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
    # Strict preflight — no automatic best-effort retry on the formal path.
    ok, reason, detail = preflight_experiment_executable(
        exp, behavior_ir=behavior_ir, actor_tokens=tokens
    )
    started = time.time()
    if not ok:
        _exec_logger.warning(
            f"Experiment BLOCKED: {eid} obligation={oid} reason={reason}",
            extra={"error_code": "QB-X003" if "ACTOR" in reason else "QB-X004", "context": {
                "experiment_id": eid, "obligation_id": oid,
                "reason_code": reason, "detail": str(detail)[:300],
                "campaign_id": resolved_campaign_id,
            }},
        )
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

    # ── SPEC v1.1 §10 Phase A: Transport-independent proof validation ──
    # For governed writes, verify compile proof exists and is valid before fixture.
    safety = _dict(exp.get("safety_contract"))
    is_governed_write = safety.get("governed_write")
    compile_proof_fingerprint = ""
    if is_governed_write:
        compile_proof = _dict(exp.get("write_reversibility_proof"))
        compile_proof_fingerprint = _text(compile_proof.get("fingerprint"))
        # Phase A: verify proof exists and fingerprint is valid
        if not compile_proof or _text(compile_proof.get("proof_status")) != "PROVEN":
            return {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": "compile_proof_missing_or_invalid",
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": "compile_proof_missing_or_invalid",
                },
            }
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
    # ── Inject pre-resolved bindings into binding_plan before fixture materialization ──
    # Batch-level auto_resolve_bindings resolves path placeholders by calling GET
    # endpoints. Inject those values so the fixture materializer can use them directly
    # instead of re-resolving (which may fail without proper actor context).
    _pre_bindings = _dict(exp.get("_pre_resolved_bindings"))
    if _pre_bindings:
        for _bk, _bv in _pre_bindings.items():
            if _bk in binding_plan and _bv not in (None, ""):
                binding_plan[_bk] = {**binding_plan[_bk], "generated_value": str(_bv), "status": "bound"}
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
    # Merge pre-resolved bindings from batch-level auto-resolution
    _pre_bindings = _dict(exp.get("_pre_resolved_bindings"))
    if _pre_bindings:
        for _bk, _bv in _pre_bindings.items():
            runtime_bindings.setdefault(_bk, _bv)
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

    # ── SPEC v1.1 §10 Phase B: Runtime binding validation ──
    # After fixture materialization, verify proof fingerprint and bindings.
    if is_governed_write:
        runtime_validation = validate_cleanup_plan(
            exp,
            behavior_ir,
            phase="runtime",
            compile_proof_fingerprint=compile_proof_fingerprint,
            runtime_bindings=runtime_bindings,
            binding_receipts=binding_materialization_receipts,
        )
        if not runtime_validation["valid"]:
            _exec_logger.warning(
                f"Experiment BLOCKED at runtime proof validation: {eid} "
                f"reason={runtime_validation['reason_code']}",
                extra={"error_code": "QB-X005", "context": {
                    "experiment_id": eid, "obligation_id": oid,
                    "reason_code": runtime_validation["reason_code"],
                    "detail": str(runtime_validation["detail"])[:300],
                    "campaign_id": resolved_campaign_id,
                }},
            )
            return {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": runtime_validation["reason_code"],
                "detail": runtime_validation["detail"],
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": runtime_validation["reason_code"],
                    "detail": runtime_validation["detail"],
                },
                "runtime_proof_validation": {
                    "status": "INVALID",
                    "reason_code": runtime_validation["reason_code"],
                    "detail": runtime_validation["detail"],
                },
            }
        # Mark runtime proof validation as passed
        exp["runtime_proof_validation"] = {
            "status": "VALID",
            "compile_fingerprint": compile_proof_fingerprint,
            "runtime_fingerprint": _text(
                _dict(runtime_validation.get("proof")).get("fingerprint")
            ),
        }

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

    _exec_logger.info(
        f"Experiment started: {eid} obligation={oid} campaign={resolved_campaign_id}",
        extra={"context": {
            "experiment_id": eid, "obligation_id": oid,
            "campaign_id": resolved_campaign_id,
        }},
    )
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

    # ── Related Entity Observer Execution ──
    # For experiments with structured expressions referencing related entities,
    # execute observers to fetch related entity collections before finalization.
    _has_structured_expr = any(
        isinstance(a, dict) and (
            _dict(a.get("structured_expression"))
            or _text(a.get("kind")) in ("cross_entity_consistency", "limit_constraint", "conservation")
        )
        for a in _list(exp.get("assertions"))
    )
    _exec_logger.debug(
        f"Related entity observer check: {eid} has_structured_expr={_has_structured_expr} "
        f"assertions_count={len(_list(exp.get('assertions')))}",
        extra={"context": {"experiment_id": eid, "obligation_id": oid}},
    )
    if _has_structured_expr:
        try:
            from .related_entity_observer_binder import bind_observer_plan
            from .related_entity_observer_executor import execute_observer_plan

            # Collect observer_requirements from assertions
            _all_observer_reqs: list[dict[str, Any]] = []
            _root_identity_value: Any = None
            _tenant_scope_values: dict[str, Any] = {}
            _relation_key: str = ""

            for _assertion in _list(exp.get("assertions")):
                if not isinstance(_assertion, dict):
                    continue
                _obs_reqs = _list(_assertion.get("observer_requirements"))
                if _obs_reqs:
                    _all_observer_reqs.extend(_obs_reqs)
                    # Extract relation_key from MANY cardinality requirements
                    for _req in _obs_reqs:
                        if isinstance(_req, dict) and _text(_req.get("cardinality")).upper() == "MANY":
                            _rk = _text(_req.get("relation_key"))
                            if _rk:
                                _relation_key = _rk
                                break

            # Extract root identity from multiple sources:
            # 1. Before/after observations using relation_key (real data)
            # 2. Treatment body using relation_key (for conservation experiments)
            # 3. Before/after state body id/uuid
            # 4. Runtime bindings
            _treatment_plan = _list(exp.get("treatment_plan"))
            _before_states = _list(observations.get("before_state_evidence"))
            _after_states = _list(observations.get("after_state_evidence"))

            # Priority 1: Extract from before/after observations (real data)
            if _relation_key and _root_identity_value is None:
                for _state_list in (_after_states, _before_states):
                    if _root_identity_value is not None:
                        break
                    for _state in _state_list:
                        if not isinstance(_state, dict):
                            continue
                        _state_body = _state.get("body")
                        # Handle list body (collection observation)
                        if isinstance(_state_body, list) and _state_body:
                            for _rec in _state_body:
                                if isinstance(_rec, dict) and _relation_key in _rec:
                                    _val = _rec[_relation_key]
                                    # Skip placeholder UUIDs (550e8400 pattern)
                                    if _val and not str(_val).startswith("550e8400"):
                                        _root_identity_value = _val
                                        break
                            if _root_identity_value is not None:
                                break
                        # Handle dict body (single entity)
                        elif isinstance(_state_body, dict) and _relation_key in _state_body:
                            _val = _state_body[_relation_key]
                            if _val and not str(_val).startswith("550e8400"):
                                _root_identity_value = _val
                                break

            # Priority 2: Extract from treatment body
            if _relation_key and _root_identity_value is None and _treatment_plan:
                for _step in _treatment_plan:
                    if not isinstance(_step, dict):
                        continue
                    _body = _dict(_step.get("body"))
                    if _relation_key in _body:
                        _root_identity_value = _body[_relation_key]
                        break

            # Priority 3: Extract id/uuid from after/before state
            if _root_identity_value is None and _after_states:
                _root_body = _dict(_after_states[-1]).get("body")
                if isinstance(_root_body, dict):
                    _root_identity_value = _root_body.get("id") or _root_body.get("uuid")
            if _root_identity_value is None:
                _root_identity_value = runtime_bindings.get("id") or runtime_bindings.get("uuid")

            # Extract tenant scope from root body or treatment body
            if _after_states:
                _root_body = _dict(_after_states[-1]).get("body")
                if isinstance(_root_body, dict):
                    for _sf in ("tenant_id", "owner_id", "department_id"):
                        if _sf in _root_body:
                            _tenant_scope_values[_sf] = _root_body[_sf]
            # Also check treatment body for tenant scope
            if not _tenant_scope_values and _treatment_plan:
                for _step in _treatment_plan:
                    if not isinstance(_step, dict):
                        continue
                    _body = _dict(_step.get("body"))
                    for _sf in ("tenant_id", "owner_id", "department_id"):
                        if _sf in _body:
                            _tenant_scope_values[_sf] = _body[_sf]
                    if _tenant_scope_values:
                        break

            _exec_logger.debug(
                f"Related entity observer reqs: {eid} count={len(_all_observer_reqs)} "
                f"root_identity={_root_identity_value} tenant_scope={list(_tenant_scope_values.keys())}",
                extra={"context": {"experiment_id": eid, "obligation_id": oid}},
            )
            if _all_observer_reqs:
                # Bind observer plan
                _observer_plan = bind_observer_plan(
                    _all_observer_reqs,
                    behavior_ir,
                    root_identity_value=_root_identity_value,
                    tenant_scope_values=_tenant_scope_values,
                )
                _exec_logger.debug(
                    f"Related entity observer plan: {eid} "
                    f"related_observers={len(_observer_plan.get('related_observers', []))} "
                    f"root_observers={len(_observer_plan.get('root_observers', []))} "
                    f"blockers={len(_observer_plan.get('blockers', []))}",
                    extra={"context": {"experiment_id": eid, "obligation_id": oid}},
                )

                # Execute observer plan if we have related observers
                if _observer_plan.get("related_observers"):
                    # Get auth token from actors
                    _auth_token = ""
                    for _actor_ref, _actor_info in actors.items():
                        _token = _text(_dict(_actor_info).get("token"))
                        if _token:
                            _auth_token = _token
                            break
                    if not _auth_token and tokens:
                        _auth_token = next(iter(tokens.values()), "")

                    _exec_result = execute_observer_plan(
                        _observer_plan,
                        base_url,
                        _auth_token,
                        root_identity_value=_root_identity_value,
                        tenant_scope_values=_tenant_scope_values,
                    )

                    # Store results in observations for finalizer
                    observations["related_entity_observations"] = _exec_result.get("related_observations", [])
                    observations["related_entity_multi_state"] = _exec_result.get("multi_entity_state", {})
                    observations["related_entity_trace"] = _exec_result.get("trace", [])
                    observations["related_entity_blockers"] = _exec_result.get("blockers", [])

                    _exec_logger.info(
                        f"Related entity observers: {eid} fetched={len(_exec_result.get('related_observations', []))} "
                        f"blockers={len(_exec_result.get('blockers', []))}",
                        extra={"context": {"experiment_id": eid, "obligation_id": oid}},
                    )
        except Exception as _obs_exc:
            _exec_logger.warning(
                f"Related entity observer execution failed: {eid} error={_obs_exc}",
                extra={"context": {"experiment_id": eid, "obligation_id": oid, "error": str(_obs_exc)}},
            )
            observations["related_entity_observer_error"] = str(_obs_exc)

    # ── Multi-Layer Observation + Cross-Surface Evidence Enrichment ──
    # Extends typed observer system; evidence only, never produces formal findings.
    try:
        from .multi_layer_observation import check_observation_completeness
        from .cross_surface_oracle import detect_emergent_violation

        _ml_obs = _list(observations.get("observer_receipts"))
        _ml_completeness = check_observation_completeness(
            exp, _ml_obs, required_layers=["API"]
        )
        observations["multi_layer_receipt"] = {
            "schema_version": "qualibug.multi-layer-observation.v1",
            "completeness": _ml_completeness,
            "observation_count": len(_ml_obs),
        }
        # Cross-surface emergent violation detection (evidence only)
        if _ml_obs:
            _emergent = detect_emergent_violation(
                experiment_id=eid,
                observations=_ml_obs,
                known_invariants=_list(ir.get("invariants")),
            )
            observations["cross_surface_evidence"] = _emergent
    except Exception as _ml_exc:
        _exec_logger.warning(
            "Multi-layer observation enrichment failed: %s obligation=%s error=%s",
            eid, oid, str(_ml_exc)[:300],
            exc_info=_ml_exc,
        )
        observations["multi_layer_error"] = str(_ml_exc)[:200]

    _final_result = finalize_experiment_execution(
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
    _final_status = _dict(_final_result).get("status", "UNKNOWN")
    _elapsed = int((time.time() - started) * 1000)
    if _final_status == "BLOCKED":
        _exec_logger.warning(
            f"Experiment finished BLOCKED: {eid} ({_elapsed}ms) reason={_dict(_final_result).get('reason_code', '')}",
            extra={"context": {"experiment_id": eid, "obligation_id": oid, "elapsed_ms": _elapsed, "status": _final_status}},
        )
    else:
        _exec_logger.info(
            f"Experiment finished: {eid} status={_final_status} ({_elapsed}ms)",
            extra={"context": {"experiment_id": eid, "obligation_id": oid, "elapsed_ms": _elapsed, "status": _final_status}},
        )
    return _final_result


from .experiment_batch_executor import execute_selected_experiments  # noqa: E402,F401
