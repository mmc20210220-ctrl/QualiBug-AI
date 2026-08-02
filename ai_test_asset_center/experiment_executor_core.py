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
from .database_observer_experiment_runtime import execute_database_observer_phase
from .real_id_resolver import bind_entity_fields  # compatibility re-export
from .sandbox_write_executor import (  # noqa: F401
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)

from .experiment_fixture_materializer import materialize_experiment_fixtures
from .experiment_barrier_executor import execute_barrier_plans
from .experiment_plan_lifecycle_adapter import execute_non_barrier_plans
from .experiment_outcome_finalizer import finalize_experiment_execution
from .experiment_cleanup_executor import execute_experiment_cleanup_compensation
from .cleanup_plan_validator import validate_cleanup_plan
from .write_reversibility_contract import build_proof_fingerprint
from .experiment_lifecycle_runtime import (
    attach_lifecycle_ledger,
    attach_lifecycle_to_result,
    new_experiment_lifecycle_ledger,
    record_stage_event,
    record_stage_rows,
    terminal_result_with_lifecycle,
)
from .process_step_execution import ProcessStepLedger
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
    started = time.time()
    lifecycle_ledger = new_experiment_lifecycle_ledger(
        exp,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=resolved_campaign_id,
        run_id=resolved_execution_id,
    )

    def _terminal(
        result: dict[str, Any],
        *,
        phase: str,
        reason_code: str,
    ) -> dict[str, Any]:
        return terminal_result_with_lifecycle(
            lifecycle_ledger,
            result,
            phase=phase,
            reason_code=reason_code,
        )

    tokens = actor_tokens if actor_tokens is not None else load_actor_tokens(
        root, project, base_url=base_url
    )
    # Strict preflight — no automatic best-effort retry on the formal path.
    ok, reason, detail = preflight_experiment_executable(
        exp, behavior_ir=behavior_ir, actor_tokens=tokens
    )
    if not ok:
        _exec_logger.warning(
            f"Experiment BLOCKED: {eid} obligation={oid} reason={reason}",
            extra={"error_code": "QB-X003" if "ACTOR" in reason else "QB-X004", "context": {
                "experiment_id": eid, "obligation_id": oid,
                "reason_code": reason, "detail": str(detail)[:300],
                "campaign_id": resolved_campaign_id,
            }},
        )
        return _terminal(
            {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": reason,
                "detail": detail,
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": reason,
                    "detail": detail,
                },
            },
            phase="preflight",
            reason_code=reason,
        )

    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))

    # ── SPEC v1.2.1 §6.5 + §7.6: Runtime Binding Graph Validation ──
    compile_coverage_receipt = _dict(exp.get("compile_coverage_receipt"))
    compile_binding_fp = _text(compile_coverage_receipt.get("binding_graph_fingerprint"))
    runtime_binding_graph = _dict(exp.get("binding_coverage_graph"))
    runtime_binding_fp = _text(runtime_binding_graph.get("binding_graph_fingerprint"))
    if compile_binding_fp and runtime_binding_fp and compile_binding_fp != runtime_binding_fp:
        return _terminal(
            {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_BINDING_GRAPH_INVALID",
                "detail": (
                    "binding_graph_fingerprint_drift:"
                    f"compile={compile_binding_fp}:runtime={runtime_binding_fp}"
                ),
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_BINDING_GRAPH_INVALID",
                    "detail": "binding_graph_fingerprint_drift",
                },
            },
            phase="runtime_binding_preflight",
            reason_code="BLOCKED_BINDING_GRAPH_INVALID",
        )

    # ── SPEC v1.1.1 §10 Phase A: Three-party fingerprint validation ──
    safety = _dict(exp.get("safety_contract"))
    is_governed_write = safety.get("governed_write")
    compile_proof_fingerprint = ""
    if is_governed_write:
        compile_proof = _dict(exp.get("write_reversibility_proof"))
        if not compile_proof or _text(compile_proof.get("proof_status")) != "PROVEN":
            return _terminal(
                {
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
                },
                phase="cleanup_contract_preflight",
                reason_code="BLOCKED_CLEANUP_CONTRACT_DRIFT",
            )
        compile_receipt = _dict(exp.get("compile_receipt"))
        frozen_fp = _text(compile_receipt.get("write_reversibility_fingerprint"))
        recomputed_fp = build_proof_fingerprint(compile_proof)
        attached_fp = _text(compile_proof.get("fingerprint"))
        if not frozen_fp:
            frozen_fp = attached_fp
        if not frozen_fp or not recomputed_fp:
            return _terminal(
                {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": (
                        "fingerprint_empty:"
                        f"frozen={frozen_fp}:recomputed={recomputed_fp}"
                    ),
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "finding": None,
                    "execution_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                        "detail": "fingerprint_empty",
                    },
                },
                phase="cleanup_contract_preflight",
                reason_code="BLOCKED_CLEANUP_CONTRACT_DRIFT",
            )
        if recomputed_fp != attached_fp:
            return _terminal(
                {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": (
                        "fingerprint_mismatch:"
                        f"recomputed={recomputed_fp}:attached={attached_fp}"
                    ),
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "finding": None,
                    "execution_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                        "detail": "phase_a_recomputed_ne_attached",
                    },
                },
                phase="cleanup_contract_preflight",
                reason_code="BLOCKED_CLEANUP_CONTRACT_DRIFT",
            )
        if frozen_fp != recomputed_fp:
            return _terminal(
                {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": (
                        "fingerprint_mismatch:"
                        f"frozen={frozen_fp}:recomputed={recomputed_fp}"
                    ),
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "finding": None,
                    "execution_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                        "detail": "phase_a_frozen_ne_recomputed",
                    },
                },
                phase="cleanup_contract_preflight",
                reason_code="BLOCKED_CLEANUP_CONTRACT_DRIFT",
            )
        compile_proof_fingerprint = frozen_fp

    steps_out: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    observations: dict[str, Any] = {
        "observer_ids": [],
        "observer_receipts": [],
        "control_succeeded": False,
        "harness_error": False,
    }
    # Expose compiled write_observers so the step executor can fall back to
    # compiler-resolved observation paths when runtime re-derivation fails.
    _compiled_wo = _list(exp.get("write_observers"))
    if _compiled_wo:
        observations["_compiled_write_observers"] = _compiled_wo
    # Expose the full observer list so runtime can distinguish response-only
    # experiments (authorization/validation) from effect-observation experiments.
    _compiled_observers = _list(exp.get("observers"))
    if _compiled_observers:
        observations["_compiled_observers"] = _compiled_observers
    attach_lifecycle_ledger(observations, lifecycle_ledger)
    activation_requirements = contract_activation_requirements(exp)
    lifecycle_ledger.set_required_step_ids(
        [
            _text(step_id)
            for phase in ("control", "treatment")
            for step_id in _list(activation_requirements.get(phase))
            if _text(step_id)
        ]
        or lifecycle_ledger.required_step_ids
    )
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
    _pre_bindings = _dict(exp.get("_pre_resolved_bindings"))
    if _pre_bindings:
        for _bk, _bv in _pre_bindings.items():
            if _bk in binding_plan and _bv not in (None, ""):
                binding_plan[_bk] = {
                    **binding_plan[_bk],
                    "materialized_value": str(_bv),
                    "source_priority": "same_actor_list_read",
                    "status": "bound",
                }
    resolver_actor_ref = ""
    for planned in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if isinstance(planned, dict) and _text(planned.get("actor_ref")):
            resolver_actor_ref = _text(planned.get("actor_ref"))
            break
    resolver_actor = actors.get(resolver_actor_ref) or {}
    resolver_token = _resolve_token(resolver_actor, tokens)

    # Validate the immutable cleanup topology before fixture materialization.
    # Fixture setup is a governed write too; allowing it to run before a
    # changed experiment proves every business write is compensable can leave
    # residue even though the later runtime proof gate correctly blocks.
    preflight_cleanup = validate_cleanup_plan(
        exp,
        behavior_ir,
        phase="compile",
    )
    if not preflight_cleanup["valid"]:
        return _terminal(
            {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": preflight_cleanup["reason_code"],
                "detail": preflight_cleanup["detail"],
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": preflight_cleanup["reason_code"],
                    "detail": preflight_cleanup["detail"],
                },
                "runtime_proof_validation": {
                    "status": "INVALID",
                    "reason_code": preflight_cleanup["reason_code"],
                    "detail": preflight_cleanup["detail"],
                },
            },
            phase="cleanup_contract_preflight",
            reason_code=preflight_cleanup["reason_code"],
        )

    fixture_state = materialize_experiment_fixtures(
        exp=exp,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        started=started,
        actors=actors,
        ops=ops,
        behavior_ir=behavior_ir,
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
        fixture_terminal = dict(
            fixture_state.get("result")
            or {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "finding": None,
            }
        )
        return _terminal(
            fixture_terminal,
            phase="fixture",
            reason_code=_text(
                fixture_terminal.get("reason_code")
                or "BLOCKED_MISSING_FIXTURE"
            ),
        )
    steps_out = list(fixture_state.get("steps_out") or [])
    fixture_receipts = list(fixture_state.get("fixture_receipts") or [])
    binding_materialization_receipts = list(
        fixture_state.get("binding_materialization_receipts") or []
    )
    runtime_bindings = dict(fixture_state.get("runtime_bindings") or {})
    pending_fixture_cleanups = list(
        fixture_state.get("pending_fixture_cleanups") or []
    )
    cleanup_failures = int(fixture_state.get("cleanup_failures") or 0)
    contract_evidence_receipts = list(
        fixture_state.get("contract_evidence_receipts") or []
    )
    record_stage_rows(lifecycle_ledger, fixture_receipts, phase="fixture")
    record_stage_rows(
        lifecycle_ledger,
        binding_materialization_receipts,
        phase="fixture_binding",
    )

    activation_requirements = contract_activation_requirements(
        exp,
        evidence={
            "binding_materialization_receipts": binding_materialization_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
        },
    )
    lifecycle_ledger.set_required_step_ids(
        [
            _text(step_id)
            for phase in ("control", "treatment")
            for step_id in _list(activation_requirements.get(phase))
            if _text(step_id)
        ]
        or lifecycle_ledger.required_step_ids
    )

    # ── SPEC v1.2.2 §10: Runtime Binding Provenance Validation ──
    _compile_graph = _dict(exp.get("binding_coverage_graph"))
    _compile_nodes = _list(_compile_graph.get("nodes"))
    _compile_node_targets: dict[str, dict[str, Any]] = {}
    for n in _compile_nodes:
        if not isinstance(n, dict):
            continue
        for _key in (
            n.get("semantic_name"),
            n.get("binding_id"),
            n.get("target"),
            n.get("name"),
        ):
            _key_text = _text(_key)
            if _key_text:
                _compile_node_targets.setdefault(_key_text, n)
    _FORBIDDEN_RUNTIME_SOURCES = {
        "degraded_synthetic",
        "synthetic_value",
        "random_placeholder",
        "invented",
    }
    _provenance_violations: list[str] = []
    for _bk, _bv in runtime_bindings.items():
        _bv_str = str(_bv or "")
        if _bv_str.startswith("qb_test_") or _bv_str.startswith("qb-test-"):
            _provenance_violations.append(
                f"synthetic_binding_reaching_transport:{_bk}"
            )
            continue
        if _compile_node_targets and _bk not in _compile_node_targets:
            _plan_targets = {
                _text(bp.get("target"))
                for bp in _list(exp.get("binding_plan"))
                if isinstance(bp, dict)
            }
            if _bk in _plan_targets:
                _provenance_violations.append(
                    f"undeclared_runtime_binding:{_bk}"
                )
    for _fr in fixture_receipts:
        _fr_status = _text(_dict(_fr).get("status")).lower()
        if _fr_status in _FORBIDDEN_RUNTIME_SOURCES:
            _provenance_violations.append(
                "forbidden_fixture_source:"
                f"{_text(_dict(_fr).get('target'))}:{_fr_status}"
            )
    if _provenance_violations:
        # Fixture/binding materialization may have performed governed reads or
        # setup writes before the provenance gate rejects a runtime value.  The
        # old early return dropped those steps (and pending fixture cleanups),
        # so the operational receipt falsely reported zero target requests and
        # the evaluator attestation could not reconcile the real network trace.
        # Reuse the normal cleanup authority before returning the blocked result;
        # never discard an already-observed step just because the later gate
        # failed.
        provenance_cleanup = execute_experiment_cleanup_compensation(
            exp=exp,
            steps_out=steps_out,
            observations=observations,
            contract_evidence_receipts=contract_evidence_receipts,
            activation_requirements=activation_requirements,
            pre_transport_block_reasons=[
                "BLOCKED_BINDING_GRAPH_INVALID",
                *_provenance_violations[:5],
            ],
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
        provenance_steps = list(
            provenance_cleanup.get("steps_out") or steps_out
        )
        provenance_observations = dict(
            provenance_cleanup.get("observations") or observations
        )
        provenance_contracts = list(
            provenance_cleanup.get("contract_evidence_receipts")
            or contract_evidence_receipts
        )
        provenance_cleanup_failures = int(
            provenance_cleanup.get("cleanup_failures") or 0
        )
        return _terminal(
            {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_BINDING_GRAPH_INVALID",
                "detail": ";".join(_provenance_violations[:5]),
                "elapsed_ms": int((time.time() - started) * 1000),
                "steps": provenance_steps,
                "observations": provenance_observations,
                "fixture_receipts": fixture_receipts,
                "binding_materialization_receipts": binding_materialization_receipts,
                "contract_evidence_receipts": provenance_contracts,
                "cleanup_failures": provenance_cleanup_failures,
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_BINDING_GRAPH_INVALID",
                    "detail": "runtime_binding_provenance_invalid",
                    "cleanup_failures": provenance_cleanup_failures,
                },
                "runtime_binding_provenance": {
                    "status": "INVALID",
                    "violations": _provenance_violations[:10],
                },
            },
            phase="runtime_binding",
            reason_code="BLOCKED_BINDING_GRAPH_INVALID",
        )

    # ── V1.3.0-A: Fixture Row Lineage (SPEC §6) ──
    _fixture_lineage_receipts: list[dict[str, Any]] = []
    if fixture_receipts:
        from .cleanup_execution_receipt import (
            build_fixture_row_lineage as _build_lineage,
        )

        for _fr in fixture_receipts:
            _fr_d = _dict(_fr)
            _fr_table = _text(
                _fr_d.get("table")
                or _fr_d.get("entity")
                or _fr_d.get("target")
            )
            _fr_pk = _text(
                _fr_d.get("primary_key")
                or _fr_d.get("row_id")
                or _fr_d.get("created_id")
            )
            if _fr_table and _fr_pk:
                _fixture_lineage_receipts.append(
                    _build_lineage(
                        campaign_id=resolved_campaign_id,
                        experiment_id=eid,
                        fixture_id=_text(
                            _fr_d.get("fixture_id")
                            or _fr_d.get("receipt_id")
                        ),
                        step_id=_text(_fr_d.get("step_id")),
                        table=_fr_table,
                        primary_key=_fr_pk,
                    )
                )
    observations["fixture_row_lineage_receipts"] = _fixture_lineage_receipts

    # ── SPEC v1.1 §10 Phase B: Runtime binding validation ──
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
            return _terminal(
                {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": runtime_validation["reason_code"],
                    "detail": runtime_validation["detail"],
                    "elapsed_ms": int((time.time() - started) * 1000),
                    # Fixture materialization may already have performed a
                    # governed read before the runtime proof gate detects
                    # contract drift. Preserve those observed steps so the
                    # operational receipt and evaluator attestation account
                    # for the actual target request.
                    "steps": list(steps_out),
                    "observations": dict(observations),
                    "fixture_receipts": list(fixture_receipts),
                    "binding_materialization_receipts": list(
                        binding_materialization_receipts
                    ),
                    "contract_evidence_receipts": list(
                        contract_evidence_receipts
                    ),
                    "cleanup_failures": cleanup_failures,
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
                },
                phase="cleanup_contract_runtime",
                reason_code=runtime_validation["reason_code"],
            )
        exp["runtime_proof_validation"] = {
            "status": "VALID",
            "compile_fingerprint": compile_proof_fingerprint,
            "runtime_fingerprint": _text(
                _dict(runtime_validation.get("proof")).get("fingerprint")
            ),
        }

    database_before = execute_database_observer_phase(
        exp,
        phase="BEFORE",
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        runtime_bindings=runtime_bindings,
        observations=observations,
        steps_out=steps_out,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    database_before_blocked = bool(database_before.get("blocked"))
    if database_before_blocked:
        database_before_reason = (
            _text(database_before.get("reason_code"))
            or "DATABASE_OBSERVER_BEFORE_PHASE_INCOMPLETE"
        )
        pre_transport_block_reasons.append(database_before_reason)
        observations["database_observer_transport_blocked"] = True
        record_stage_event(
            lifecycle_ledger,
            phase="database_before",
            step_id="database_before",
            status="FAILED",
            receipt_id=database_before_reason,
        )
        barrier_result: dict[str, Any] = {
            "steps": [],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "consumed_barrier_steps": set(),
        }
        plan_result: dict[str, Any] = {
            "steps": [],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "cleanup_failures": cleanup_failures,
            "process_step_ledger": lifecycle_ledger,
        }
    else:
        record_stage_event(
            lifecycle_ledger,
            phase="database_before",
            step_id="database_before",
            status=_text(database_before.get("status")) or "COMPLETED",
            receipt_id=_text(database_before.get("receipt_id")),
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
        barrier_steps = list(barrier_result.get("steps") or [])
        steps_out.extend(barrier_steps)
        record_stage_rows(lifecycle_ledger, barrier_steps, phase="barrier")
        contract_evidence_receipts.extend(
            list(barrier_result.get("contract_evidence_receipts") or [])
        )
        request_bodies_for_cleanup.update(
            dict(barrier_result.get("request_bodies_for_cleanup") or {})
        )
        pre_transport_block_reasons.extend(
            list(barrier_result.get("pre_transport_block_reasons") or [])
        )
        consumed_barrier_steps = set(
            barrier_result.get("consumed_barrier_steps") or set()
        )
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
        cleanup_failures = int(
            plan_result.get("cleanup_failures") or cleanup_failures
        )

    _plan_ledger = plan_result.get("process_step_ledger") or observations.get(
        "process_step_ledger"
    )
    if isinstance(_plan_ledger, ProcessStepLedger):
        lifecycle_ledger = _plan_ledger
        attach_lifecycle_ledger(observations, lifecycle_ledger)

    if not database_before_blocked:
        database_after = execute_database_observer_phase(
            exp,
            phase="AFTER",
            root=root,
            project=project,
            runtime_contract=runtime_contract,
            runtime_bindings=runtime_bindings,
            observations=observations,
            steps_out=steps_out,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
        )
        database_after_status = _text(database_after.get("status"))
        if database_after_status == "INDETERMINATE":
            database_after_reason = (
                _text(database_after.get("reason_code"))
                or "DATABASE_OBSERVER_AFTER_PHASE_INCOMPLETE"
            )
            pre_transport_block_reasons.append(database_after_reason)
            observations["database_observer_after_phase_incomplete"] = True
            record_stage_event(
                lifecycle_ledger,
                phase="database_after",
                step_id="database_after",
                status="FAILED",
                receipt_id=database_after_reason,
            )
        else:
            record_stage_event(
                lifecycle_ledger,
                phase="database_after",
                step_id="database_after",
                status=database_after_status or "COMPLETED",
                receipt_id=_text(database_after.get("receipt_id")),
            )

    _exec_logger.info(
        f"Experiment started: {eid} obligation={oid} campaign={resolved_campaign_id}",
        extra={"context": {
            "experiment_id": eid,
            "obligation_id": oid,
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
        cleanup_result.get("contract_evidence_receipts")
        or contract_evidence_receipts
    )
    cleanup_failures = int(cleanup_result.get("cleanup_failures") or 0)
    cleanup_steps = [
        row
        for row in steps_out
        if isinstance(row, dict) and _text(row.get("phase")) == "cleanup"
    ]
    record_stage_rows(lifecycle_ledger, cleanup_steps, phase="cleanup")
    record_stage_rows(
        lifecycle_ledger,
        _list(observations.get("cleanup_execution_receipts")),
        phase="cleanup",
    )
    attach_lifecycle_ledger(observations, lifecycle_ledger)

    # ── Related Entity Observer Execution ──
    _has_structured_expr = any(
        isinstance(a, dict)
        and (
            _dict(a.get("structured_expression"))
            or _text(a.get("kind"))
            in (
                "cross_entity_consistency",
                "limit_constraint",
                "conservation",
            )
        )
        for a in _list(exp.get("assertions"))
    )
    _exec_logger.debug(
        f"Related entity observer check: {eid} "
        f"has_structured_expr={_has_structured_expr} "
        f"assertions_count={len(_list(exp.get('assertions')))}",
        extra={"context": {"experiment_id": eid, "obligation_id": oid}},
    )
    if _has_structured_expr:
        try:
            from .related_entity_observer_binder import bind_observer_plan
            from .related_entity_observer_executor import execute_observer_plan

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
                    for _req in _obs_reqs:
                        if (
                            isinstance(_req, dict)
                            and _text(_req.get("cardinality")).upper() == "MANY"
                        ):
                            _rk = _text(_req.get("relation_key"))
                            if _rk:
                                _relation_key = _rk
                                break

            _treatment_plan = _list(exp.get("treatment_plan"))
            _before_states = _list(observations.get("before_state_evidence"))
            _after_states = _list(observations.get("after_state_evidence"))

            if _relation_key and _root_identity_value is None:
                for _state_list in (_after_states, _before_states):
                    if _root_identity_value is not None:
                        break
                    for _state in _state_list:
                        if not isinstance(_state, dict):
                            continue
                        _state_body = _state.get("body")
                        if isinstance(_state_body, list) and _state_body:
                            for _rec in _state_body:
                                if (
                                    isinstance(_rec, dict)
                                    and _relation_key in _rec
                                ):
                                    _val = _rec[_relation_key]
                                    if _val and not str(_val).startswith("550e8400"):
                                        _root_identity_value = _val
                                        break
                            if _root_identity_value is not None:
                                break
                        elif (
                            isinstance(_state_body, dict)
                            and _relation_key in _state_body
                        ):
                            _val = _state_body[_relation_key]
                            if _val and not str(_val).startswith("550e8400"):
                                _root_identity_value = _val
                                break

            if (
                _relation_key
                and _root_identity_value is None
                and _treatment_plan
            ):
                for _step in _treatment_plan:
                    if not isinstance(_step, dict):
                        continue
                    _body = _dict(_step.get("body"))
                    if _relation_key in _body:
                        _root_identity_value = _body[_relation_key]
                        break

            if _root_identity_value is None and _after_states:
                _root_body = _dict(_after_states[-1]).get("body")
                if isinstance(_root_body, dict):
                    _root_identity_value = (
                        _root_body.get("id") or _root_body.get("uuid")
                    )
            if _root_identity_value is None:
                _root_identity_value = (
                    runtime_bindings.get("id")
                    or runtime_bindings.get("uuid")
                )

            if _after_states:
                _root_body = _dict(_after_states[-1]).get("body")
                if isinstance(_root_body, dict):
                    for _sf in (
                        "tenant_id",
                        "owner_id",
                        "department_id",
                    ):
                        if _sf in _root_body:
                            _tenant_scope_values[_sf] = _root_body[_sf]
            if not _tenant_scope_values and _treatment_plan:
                for _step in _treatment_plan:
                    if not isinstance(_step, dict):
                        continue
                    _body = _dict(_step.get("body"))
                    for _sf in (
                        "tenant_id",
                        "owner_id",
                        "department_id",
                    ):
                        if _sf in _body:
                            _tenant_scope_values[_sf] = _body[_sf]
                    if _tenant_scope_values:
                        break

            _exec_logger.debug(
                f"Related entity observer reqs: {eid} "
                f"count={len(_all_observer_reqs)} "
                f"root_identity={_root_identity_value} "
                f"tenant_scope={list(_tenant_scope_values.keys())}",
                extra={"context": {"experiment_id": eid, "obligation_id": oid}},
            )
            if _all_observer_reqs:
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

                if _observer_plan.get("related_observers"):
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

                    observations["related_entity_observations"] = (
                        _exec_result.get("related_observations", [])
                    )
                    observations["related_entity_multi_state"] = (
                        _exec_result.get("multi_entity_state", {})
                    )
                    observations["related_entity_trace"] = _exec_result.get(
                        "trace", []
                    )
                    observations["related_entity_blockers"] = _exec_result.get(
                        "blockers", []
                    )

                    _exec_logger.info(
                        f"Related entity observers: {eid} "
                        f"fetched={len(_exec_result.get('related_observations', []))} "
                        f"blockers={len(_exec_result.get('blockers', []))}",
                        extra={"context": {"experiment_id": eid, "obligation_id": oid}},
                    )
        except Exception as _obs_exc:
            _exec_logger.warning(
                f"Related entity observer execution failed: {eid} "
                f"error={_obs_exc}",
                extra={"context": {
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "error": str(_obs_exc),
                }},
            )
            observations["related_entity_observer_error"] = str(_obs_exc)

    # ── Multi-Layer Observation + Cross-Surface Evidence Enrichment ──
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
            eid,
            oid,
            str(_ml_exc)[:300],
            exc_info=_ml_exc,
        )
        observations["multi_layer_error"] = str(_ml_exc)[:200]

    record_stage_event(
        lifecycle_ledger,
        phase="finalizer",
        step_id="finalizer",
        status="READY",
        receipt_id="finalizer_inputs_sealed",
    )
    attach_lifecycle_ledger(observations, lifecycle_ledger)
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
    _final_result = attach_lifecycle_to_result(
        lifecycle_ledger,
        _dict(_final_result),
    )
    _final_status = _dict(_final_result).get("status", "UNKNOWN")
    _elapsed = int((time.time() - started) * 1000)
    if _final_status == "BLOCKED":
        _exec_logger.warning(
            f"Experiment finished BLOCKED: {eid} ({_elapsed}ms) "
            f"reason={_dict(_final_result).get('reason_code', '')}",
            extra={"context": {
                "experiment_id": eid,
                "obligation_id": oid,
                "elapsed_ms": _elapsed,
                "status": _final_status,
            }},
        )
    else:
        _exec_logger.info(
            f"Experiment finished: {eid} status={_final_status} ({_elapsed}ms)",
            extra={"context": {
                "experiment_id": eid,
                "obligation_id": oid,
                "elapsed_ms": _elapsed,
                "status": _final_status,
            }},
        )
    return _final_result


from .experiment_batch_executor import execute_selected_experiments  # noqa: E402,F401
