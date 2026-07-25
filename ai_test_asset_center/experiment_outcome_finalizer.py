"""Observer evaluation, contract oracle, and finding packaging for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Runs after
fixture/barrier/plan/cleanup and always returns the terminal
``qualibug.experiment-execution.v1`` outcome. Never authors an independent
customer-delivery decision.
"""
from __future__ import annotations

import time
from typing import Any

from .assertion_dsl import materialize_assertion
from .contract_oracles import (
    evaluate_contract_oracle,
    mark_as_internal_clue,
)
from .experiment_runtime_support import (
    _dict,
    _list,
    _sha256,
    _text,
)
from .observation_completeness import (
    CrossEntityObservation,
    ObservationRequirement,
    OracleInputCompletenessProof,
    ORACLE_INPUT_INCOMPLETE,
    STATUS_INDETERMINATE as _OBS_STATUS_INDETERMINATE,
)
from .observer_contracts_base import observe_experiment_requirements
from .runtime_binding_materializer import materialize_path as _materialize_path
from .cleanup_equivalence import evaluate_cleanup_equivalence


def _extract_related_from_body(body: Any, entity_name: str) -> Any:
    """Extract related entity data from a response body.

    Searches for nested objects/arrays matching the entity name (singular/plural).
    Returns the matched data or None.
    """
    if not isinstance(body, dict) or not entity_name:
        return None
    # Direct key match (plural or singular)
    if entity_name in body:
        return body[entity_name]
    # Try singular form (strip trailing 's')
    singular = entity_name.rstrip("s") if entity_name.endswith("s") else entity_name + "s"
    if singular in body:
        return body[singular]
    # Search nested dicts for entity-named keys
    normalized = entity_name.lower().replace("-", "_").replace(" ", "_")
    for key, value in body.items():
        norm_key = key.lower().replace("-", "_").replace(" ", "_")
        if norm_key == normalized or norm_key == normalized.rstrip("s") or norm_key + "s" == normalized:
            return value
    return None


def _pre_transport_reason_code(reasons: list[str]) -> str:
    combined = " ".join(_text(reason).upper() for reason in reasons)
    if "TARGET_POLICY" in combined or "READ_ONLY" in combined:
        return "BLOCKED_TARGET_POLICY"
    if "OBSERVER" in combined:
        return "BLOCKED_MISSING_OBSERVER"
    if "OPERATION" in combined:
        return "BLOCKED_MISSING_OPERATION"
    if "ACTOR" in combined:
        return "BLOCKED_MISSING_ACTOR"
    if "FIXTURE" in combined:
        return "BLOCKED_MISSING_FIXTURE"
    return "BLOCKED_MISSING_BINDING"


# ── P0-9: Harness failure sub-classification (SPEC §11) ──
HARNESS_FAILURE_SUBTYPES = (
    # Runtime transport failures
    "HARNESS_CLEANUP_TRANSPORT_FAILED",
    "HARNESS_CLEANUP_RESPONSE_REJECTED",
    "HARNESS_CLEANUP_EQUIVALENCE_FAILED",
    "HARNESS_OBSERVER_TRANSPORT_FAILED",
    "HARNESS_OBSERVER_RESPONSE_UNREADABLE",
    "HARNESS_RUNTIME_BINDING_LOST",
    "HARNESS_ASYNC_CONVERGENCE_TIMEOUT",
    "HARNESS_BARRIER_EXECUTION_FAILED",
    # Legacy subtypes (kept for backward compatibility)
    "HARNESS_REQUEST_BUILD_FAILED",
    "HARNESS_PARAMETER_BINDING_FAILED",
    "HARNESS_AUTH_INJECTION_FAILED",
    "HARNESS_ROUTE_FAILED",
    "HARNESS_CONNECTION_FAILED",
    "HARNESS_RESPONSE_PARSE_FAILED",
    "HARNESS_PRE_OBSERVATION_FAILED",
    "HARNESS_POST_OBSERVATION_FAILED",
)


# ────────────────────────────────────────────────────────────────────────────
# P0-7: Runtime evidence level progression
# ────────────────────────────────────────────────────────────────────────────

# Evidence levels in ascending order of confidence
RUNTIME_EVIDENCE_LEVELS = (
    "DOCUMENT_DERIVED",      # Rule derived from documents only
    "OBSERVED_ONCE",         # Rule observed once at runtime
    "OBSERVED_REPEATED",     # Rule observed multiple times
    "RUNTIME_CONFIRMED",     # Rule confirmed through controlled experiment
)

_EVIDENCE_LEVEL_ORDER = {level: idx for idx, level in enumerate(RUNTIME_EVIDENCE_LEVELS)}


def upgrade_evidence_level(current_level: str, observation_count: int = 1) -> str:
    """Upgrade evidence level based on observation count.

    Progression: DOCUMENT_DERIVED → OBSERVED_ONCE → OBSERVED_REPEATED → RUNTIME_CONFIRMED

    Args:
        current_level: Current evidence level
        observation_count: Number of times the rule has been observed

    Returns:
        Upgraded evidence level
    """
    current_level = (current_level or "DOCUMENT_DERIVED").upper()
    if current_level not in _EVIDENCE_LEVEL_ORDER:
        current_level = "DOCUMENT_DERIVED"

    current_idx = _EVIDENCE_LEVEL_ORDER[current_level]

    # Determine target level based on observations
    if observation_count >= 3:
        target_level = "RUNTIME_CONFIRMED"
    elif observation_count >= 2:
        target_level = "OBSERVED_REPEATED"
    elif observation_count >= 1:
        target_level = "OBSERVED_ONCE"
    else:
        target_level = current_level

    target_idx = _EVIDENCE_LEVEL_ORDER.get(target_level, 0)

    # Only upgrade, never downgrade
    if target_idx > current_idx:
        return target_level
    return current_level


def compute_evidence_confidence(evidence_level: str, base_confidence: float = 0.5) -> float:
    """Compute confidence score based on evidence level.

    Args:
        evidence_level: Current evidence level
        base_confidence: Base confidence from document derivation

    Returns:
        Adjusted confidence score (0.0 - 1.0)
    """
    level_boosts = {
        "DOCUMENT_DERIVED": 0.0,
        "OBSERVED_ONCE": 0.15,
        "OBSERVED_REPEATED": 0.25,
        "RUNTIME_CONFIRMED": 0.40,
    }
    boost = level_boosts.get((evidence_level or "").upper(), 0.0)
    return min(0.98, base_confidence + boost)


def _classify_harness_failure(
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    pre_transport_block_reasons: list[str],
    cleanup_failures: int = 0,
) -> str:
    """Classify why the experiment resulted in a harness failure.

    SPEC §11: Only real runtime failures may remain as HARNESS_FAILED.
    Semantic plan errors must have been caught pre-transport as BLOCKED.
    """
    combined = " ".join(_text(r).upper() for r in pre_transport_block_reasons)

    # Barrier / concurrency failures
    if "BARRIER" in combined:
        return "HARNESS_BARRIER_EXECUTION_FAILED"
    if observations.get("harness_error") and _text(
        observations.get("barrier_timeline")
    ):
        return "HARNESS_BARRIER_EXECUTION_FAILED"

    # Cleanup runtime failures (only after real transport)
    if cleanup_failures:
        # Distinguish transport vs equivalence failures
        cleanup_status = _text(observations.get("cleanup_status")).upper()
        if cleanup_status in ("TRANSPORT_ERROR", "CONNECTION_FAILED"):
            return "HARNESS_CLEANUP_TRANSPORT_FAILED"
        if cleanup_status in ("REJECTED", "RESPONSE_REJECTED"):
            return "HARNESS_CLEANUP_RESPONSE_REJECTED"
        if cleanup_status in ("EQUIVALENCE_FAILED", "STATE_NOT_RESTORED"):
            return "HARNESS_CLEANUP_EQUIVALENCE_FAILED"
        return "HARNESS_CLEANUP_TRANSPORT_FAILED"

    # Observer runtime failures
    if "OBSERVER" in combined:
        return "HARNESS_OBSERVER_TRANSPORT_FAILED"

    # Binding lost at runtime (was resolved at compile, lost during execution)
    if "BINDING" in combined or "PLACEHOLDER" in combined or "PARAMETER" in combined:
        return "HARNESS_RUNTIME_BINDING_LOST"

    # Auth / actor runtime failures
    if "ACTOR" in combined or "TOKEN" in combined or "AUTH" in combined:
        return "HARNESS_AUTH_INJECTION_FAILED"

    # Route failures
    if "OPERATION" in combined or "ROUTE" in combined:
        return "HARNESS_ROUTE_FAILED"

    # Check steps for connection/timeout errors
    for step in steps_out:
        if not isinstance(step, dict):
            continue
        error = _text(step.get("error"))
        status = int(step.get("status_code") or 0)
        if "timeout" in error.lower() or "convergence" in error.lower():
            return "HARNESS_ASYNC_CONVERGENCE_TIMEOUT"
        if "connection" in error.lower():
            return "HARNESS_CONNECTION_FAILED"
        if status in (404, 503):
            return "HARNESS_ROUTE_FAILED"

    # Observer response unreadable
    for step in steps_out:
        if not isinstance(step, dict):
            continue
        if _text(step.get("phase")).endswith("observation"):
            if int(step.get("status_code") or 0) == 0:
                return "HARNESS_OBSERVER_RESPONSE_UNREADABLE"

    # Fallback: generic harness failure
    if observations.get("harness_error"):
        return "HARNESS_REQUEST_BUILD_FAILED"
    return "HARNESS_CONNECTION_FAILED"


def _run_cross_entity_observation_completeness(
    *,
    exp: dict[str, Any],
    observations: dict[str, Any],
    before_states: list[dict[str, Any]],
    after_states: list[dict[str, Any]],
    eid: str,
    oid: str,
) -> dict[str, Any]:
    """Production integration adapter for observation_completeness.py.

    Called from finalize_experiment_execution() when structured expressions
    require multi-entity observation. Compiles observation requirements from
    assertion structure, validates completeness of available data, builds
    scope proof / snapshot pair / delta reconstruction, and gates Oracle.

    Activation is purely structural (assertion kind / structured_expression).
    No project-specific entity names, rule IDs, or benchmark data.
    """
    from dataclasses import asdict as _asdict

    _ce = CrossEntityObservation()
    result: dict[str, Any] = {
        "blocked": False,
        "blocked_reason": "",
        "proof": None,
        "observation_requirement": None,
        "scope_proof": None,
        "snapshot_pair": None,
        "deltas": None,
    }

    # ── Step 1: Compile Observation Requirement from assertion structure ──
    # Derive oracle expression from assertions that have structured_expression
    _oracle_expr = _compile_oracle_expression_from_assertions(exp)
    if not _oracle_expr:
        # No structured oracle expression found; cannot compile requirement.
        # This is not a block — the existing path handles it.
        return result

    _internal_rule_id = _text(exp.get("obligation_id") or oid)
    _requirement = _ce.compile_requirement(
        internal_rule_id=_internal_rule_id,
        oracle_expression=_oracle_expr,
        experiment_id=eid,
    )
    result["observation_requirement"] = _safe_serialize(_requirement)

    # Validate requirement compiled correctly
    _valid, _blocked_reason = _ce.compiler.validate_requirement(_requirement)
    if not _valid:
        result["blocked"] = True
        result["blocked_reason"] = _blocked_reason
        result["proof"] = _safe_serialize(_ce.completeness_gate.build_proof(
            internal_rule_id=_internal_rule_id,
            experiment_id=eid,
            requirement=_requirement,
            root_before=None,
            root_after=None,
            related_before=None,
            related_after=None,
            scope_proof=None,
            snapshot_pair=None,
        ))
        return result

    # ── Step 2: Extract available observation data ──
    _root_before = before_states[-1].get("body") if before_states else None
    _root_after = after_states[-1].get("body") if after_states else None
    # Related entity data from active observer execution
    _related_multi = _dict(observations.get("related_entity_multi_state"))
    _related_before: dict | None = None
    _related_after: dict | None = None
    if _related_multi:
        # Use first available related entity state
        for _rkey, _rstate in _related_multi.items():
            if not isinstance(_rstate, dict):
                continue
            _records = _rstate.get("records", [])
            if _records:
                # For collection observers, use aggregate as related data
                _related_before = _rstate
                _related_after = _rstate
                break
    # Also check multi_entity_state for nested related data
    _multi_entity = _dict(observations.get("multi_entity_state"))
    if _related_before is None and _multi_entity:
        _rel_data = _multi_entity.get("related")
        if isinstance(_rel_data, dict):
            _related_before = _rel_data.get("before")
            _related_after = _rel_data.get("after")

    # ── Step 3: Resolve Relation Scope and build Scope Proof ──
    _scope_proof_obj = None
    if _requirement.related_entities and _root_after and isinstance(_root_after, dict):
        _root_id = str(_root_after.get("id") or _root_after.get("uuid") or "")
        _tenant = str(_root_after.get("tenant_id") or "")
        for _rel_spec in _requirement.related_entities:
            _scope = _ce.resolve_relation_scope(
                root_instance_id=_root_id,
                root_data=_root_after,
                rel_spec=_rel_spec,
                tenant_id=_tenant,
            )
            # Verify scope against observed related data
            _observed_related = _related_after if isinstance(_related_after, dict) else {}
            _scope_proof_obj = _ce.scope_resolver.verify_scope(
                _scope, _observed_related
            )
            result["scope_proof"] = _safe_serialize(_scope_proof_obj)
            break  # first related entity scope

    # ── Step 4: Build Snapshot Pair ──
    _snapshot_pair_obj = None
    if _root_before and _root_after and isinstance(_root_before, dict) and isinstance(_root_after, dict):
        _root_entity_type = ""
        if _requirement.root_entity:
            _root_entity_type = _requirement.root_entity.entity_type
        _root_id = str(_root_after.get("id") or _root_after.get("uuid") or eid)
        _tenant = str(_root_after.get("tenant_id") or "")
        _snapshot_pair_obj = _ce.build_snapshot_pair(
            root_entity_type=_root_entity_type,
            root_instance_id=_root_id,
            before_data=_root_before,
            after_data=_root_after,
            tenant_id=_tenant,
        )
        result["snapshot_pair"] = _safe_serialize(_snapshot_pair_obj)

    # ── Step 5: Reconstruct Deltas ──
    _deltas_list = []
    if _root_before and _root_after and isinstance(_root_before, dict) and isinstance(_root_after, dict):
        _op_inputs = _extract_operation_inputs(exp)
        _deltas_list = _ce.reconstruct_deltas(
            requirement=_requirement,
            root_before=_root_before,
            root_after=_root_after,
            related_before=_related_before if isinstance(_related_before, dict) else None,
            related_after=_related_after if isinstance(_related_after, dict) else None,
            operation_inputs=_op_inputs,
        )
        result["deltas"] = [_safe_serialize(d) for d in _deltas_list]

    # ── Step 6: Completeness Gate ──
    _proof = _ce.gate_oracle(
        requirement=_requirement,
        root_before=_root_before if isinstance(_root_before, dict) else None,
        root_after=_root_after if isinstance(_root_after, dict) else None,
        related_before=_related_before if isinstance(_related_before, dict) else None,
        related_after=_related_after if isinstance(_related_after, dict) else None,
        scope_proof=_scope_proof_obj,
        snapshot_pair=_snapshot_pair_obj,
    )
    result["proof"] = _safe_serialize(_proof)

    _gate_decision = _ce.completeness_gate.gate_oracle_call(_proof)
    if _gate_decision != "PROCEED":
        result["blocked"] = True
        result["blocked_reason"] = _proof.blocked_reason or ORACLE_INPUT_INCOMPLETE

    return result


def _compile_oracle_expression_from_assertions(exp: dict[str, Any]) -> dict[str, Any]:
    """Derive an oracle expression dict from experiment assertions.

    Scans assertions for structured_expression or multi-entity kind markers.
    Returns a dict compatible with ObservationRequirementCompiler input.
    Returns empty dict if no cross-entity structure detected.
    """
    _assertions = _list(exp.get("assertions"))
    _root_entity: dict[str, Any] = {}
    _related_entities: list[dict[str, Any]] = []
    _checks: list[dict[str, Any]] = []
    _operation_inputs: list[str] = []

    for _a in _assertions:
        if not isinstance(_a, dict):
            continue
        _se = _dict(_a.get("structured_expression"))
        _kind = _text(_a.get("kind"))
        if not _se and _kind not in ("cross_entity_consistency", "limit_constraint", "conservation"):
            continue

        # Extract root entity from entity_bindings or structured_expression
        _bindings = _dict(_a.get("entity_bindings"))
        _root_name = _text(_a.get("root_entity"))
        if _se.get("root_entity"):
            _re = _dict(_se.get("root_entity"))
            _root_entity = {
                "type": _text(_re.get("type") or _re.get("entity")),
                "fields": _list(_re.get("fields")),
            }
        elif _root_name:
            _root_entity = {"type": _root_name, "fields": []}
        elif _bindings:
            for _alias, _info in _bindings.items():
                if not isinstance(_info, dict):
                    continue
                if _alias == "root" or _text(_info.get("cardinality")).upper() != "MANY":
                    _root_entity = {
                        "type": _text(_info.get("entity_name")),
                        "fields": _list(_info.get("required_fields")),
                    }
                    break

        # Extract related entities - prefer structured_expression (full info)
        for _rel in _list(_se.get("related_entities")):
            if isinstance(_rel, dict):
                _rel_type = _text(_rel.get("type") or _rel.get("entity") or _rel.get("entity_name"))
                if _rel_type and not any(r.get("type") == _rel_type for r in _related_entities):
                    _related_entities.append({
                        "type": _rel_type,
                        "fields": _list(_rel.get("fields") or _rel.get("required_fields")),
                        "relation_id": _text(_rel.get("relation_id") or _rel.get("relation", {}).get("key") if isinstance(_rel.get("relation"), dict) else _rel.get("relation_id")),
                        "correlation_keys": _list(_rel.get("correlation_keys")),
                        "cardinality": _text(_rel.get("cardinality") or "one"),
                        "identifier_source": _text(_rel.get("identifier_source")),
                        "aggregation": _text(_rel.get("aggregation")),
                    })
        # Supplement from assertion-level related_entities (may lack fields)
        for _rel in _list(_a.get("related_entities")):
            if isinstance(_rel, dict):
                _rel_type = _text(_rel.get("entity") or _rel.get("entity_name") or _rel.get("type"))
                if _rel_type and not any(r.get("type") == _rel_type for r in _related_entities):
                    _related_entities.append({
                        "type": _rel_type,
                        "fields": _list(_rel.get("required_fields") or _rel.get("fields")),
                        "relation_id": _text(_rel.get("relation_id")),
                        "correlation_keys": _list(_rel.get("correlation_keys")),
                        "cardinality": _text(_rel.get("cardinality") or "one"),
                        "identifier_source": _text(_rel.get("identifier_source")),
                    })
            elif isinstance(_rel, str) and _rel:
                if not any(r.get("type") == _rel for r in _related_entities):
                    _related_entities.append({"type": _rel, "fields": []})
        # Also check observer_requirements for related entity info
        for _obs_req in _list(_a.get("observer_requirements")):
            if not isinstance(_obs_req, dict):
                continue
            _ent_name = _text(_obs_req.get("entity_name"))
            if _ent_name and not any(r.get("type") == _ent_name for r in _related_entities):
                _related_entities.append({
                    "type": _ent_name,
                    "fields": _list(_obs_req.get("required_fields")),
                    "cardinality": _text(_obs_req.get("cardinality") or "one"),
                    "relation_id": _text(_obs_req.get("relation_key")),
                    "identifier_source": _text(_obs_req.get("identifier_source")),
                })

        # Extract checks from structured_expression
        if _se.get("checks"):
            _checks.extend(_list(_se.get("checks")))
        # Infer delta check from kind
        if _kind == "conservation" and not _checks:
            _checks.append({"type": "delta", "entity": _text(_root_entity.get("type")), "field": "", "formula": "after - before"})

        # Extract operation inputs
        if _se.get("operation_inputs"):
            _operation_inputs.extend(_list(_se.get("operation_inputs")))

    if not _root_entity and not _related_entities:
        return {}

    return {
        "root_entity": _root_entity,
        "related_entities": _related_entities,
        "checks": _checks,
        "operation_inputs": _operation_inputs,
    }


def _extract_operation_inputs(exp: dict[str, Any]) -> dict[str, Any]:
    """Extract operation input values from treatment plan body."""
    _inputs: dict[str, Any] = {}
    for _step in _list(exp.get("treatment_plan")):
        if not isinstance(_step, dict):
            continue
        _body = _dict(_step.get("body"))
        if _body:
            _inputs.update(_body)
            break
    return _inputs


def _safe_serialize(obj: Any) -> dict[str, Any]:
    """Safely serialize a dataclass or dict to a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        from dataclasses import asdict
        return asdict(obj)
    except Exception:
        return {"repr": str(obj)}


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
    """Evaluate declared observers and package the terminal execution outcome."""
    # A declared observer is satisfied only by an OBSERVED typed receipt.
    observations["execution_steps"] = steps_out
    observations["campaign_id"] = resolved_campaign_id
    observations["execution_id"] = resolved_execution_id
    observations["binding_materialization_receipts"] = binding_materialization_receipts
    observer_receipts = observe_experiment_requirements(
        exp,
        observations=observations,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    observations["observer_receipts"] = observer_receipts
    observed_ids: list[str] = []
    for receipt in observer_receipts:
        observer_id = _text(receipt.get("observer_id"))
        if _text(receipt.get("status")).upper() == "OBSERVED" and observer_id:
            observed_ids.append(observer_id)
            observations[observer_id] = True
            observations[observer_id + "_observation"] = receipt
        if observer_id == "authorization_comparison":
            observations.update(_dict(receipt.get("evidence")))
            observations["observer_receipt"] = receipt
        elif observer_id == "business_effect":
            observations.update(_dict(receipt.get("evidence")))
            observations["business_effect_observer_receipt"] = receipt
        elif observer_id == "entity_state":
            observations.update(_dict(receipt.get("evidence")))
            observations["entity_state_observer_receipt"] = receipt
        elif observer_id in {"before_state", "after_state", "final_state"}:
            observations.update(_dict(receipt.get("evidence")))
            observations[observer_id + "_observer_receipt"] = receipt
        elif observer_id == "barrier_timeline":
            observations.update(_dict(receipt.get("evidence")))
            observations["barrier_timeline_observer_receipt"] = receipt
        elif observer_id == "temporal_window":
            observations.update(_dict(receipt.get("evidence")))
            observations["temporal_window_observer_receipt"] = receipt
        elif observer_id in {"typed_assertion", "source_invariant"}:
            observations.update(_dict(receipt.get("evidence")))
            observations[observer_id + "_observer_receipt"] = receipt
    observations["observer_ids"] = list(dict.fromkeys(observed_ids))
    observations["contract_evidence_receipts"] = list(contract_evidence_receipts)
    observations["fixture_receipts"] = list(fixture_receipts)
    observations["source_refs"] = [
        dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)
    ]
    observations["cleanup_failures"] = cleanup_failures

    # Idempotency evidence: compare control vs treatment responses for dual-write protocols.
    control_steps = [s for s in steps_out if isinstance(s, dict) and s.get("phase") == "control"]
    treatment_steps = [s for s in steps_out if isinstance(s, dict) and s.get("phase") == "treatment"]
    if control_steps and treatment_steps:
        control_statuses = [s.get("status_code") for s in control_steps if s.get("status_code")]
        treatment_statuses = [s.get("status_code") for s in treatment_steps if s.get("status_code")]
        if control_statuses and treatment_statuses:
            observations["dual_2xx"] = all(
                200 <= s < 300 for s in control_statuses + treatment_statuses
            )
            observations["control_statuses"] = control_statuses
            observations["treatment_statuses"] = treatment_statuses
            observations["idempotency_check"] = {
                "control_succeeded": all(200 <= s < 300 for s in control_statuses),
                "treatment_succeeded": all(200 <= s < 300 for s in treatment_statuses),
                "both_succeeded": observations["dual_2xx"],
            }

    # ── P0-10: Extract before/after state from governed writes ──
    # For causal/conservation obligations, the governed write captures
    # before_state and after_state via GET on the observation_path.
    _before_states: list[dict[str, Any]] = []
    _after_states: list[dict[str, Any]] = []
    for step in steps_out:
        if not isinstance(step, dict):
            continue
        gov = _dict(step.get("governance_receipt"))
        if not gov:
            continue
        before = _dict(gov.get("before"))
        after = _dict(gov.get("after"))
        if before and int(before.get("status") or 0) > 0:
            _before_states.append({
                "path": _text(gov.get("observation_path") or step.get("observation_path")),
                "status": int(before.get("status") or 0),
                "body": before.get("body"),
                "phase": _text(step.get("phase")),
            })
        if after and int(after.get("status") or 0) > 0:
            _after_states.append({
                "path": _text(gov.get("observation_path") or step.get("observation_path")),
                "status": int(after.get("status") or 0),
                "body": after.get("body"),
                "phase": _text(step.get("phase")),
            })
    if _before_states:
        observations["before_state_evidence"] = _before_states
        observations["before_state"] = _before_states[-1].get("body")
    if _after_states:
        observations["after_state_evidence"] = _after_states
        observations["after_state"] = _after_states[-1].get("body")
    # For causal obligations: ensure before/after comparison is available
    _risk = _text(exp.get("risk_family") or "")
    if _risk in ("causal_postcondition", "conservation", "state_transition", "state"):
        observations["causal_observation_ready"] = bool(_before_states and _after_states)
        observations["observation_coverage"] = {
            "before_count": len(_before_states),
            "after_count": len(_after_states),
            "risk_family": _risk,
        }

    # ── Multi-entity observation collection for structured expressions ──
    _has_structured_expr = any(
        isinstance(a, dict) and (
            _dict(a.get("structured_expression"))
            or _text(a.get("kind")) in ("cross_entity_consistency", "limit_constraint")
        )
        for a in _list(exp.get("assertions"))
    )
    if _has_structured_expr:
        _multi_entity: dict[str, Any] = {}
        # Build root entity state from before/after
        _root_before = _before_states[-1].get("body") if _before_states else None
        _root_after = _after_states[-1].get("body") if _after_states else None
        # Extract entity bindings from assertions to identify root/related
        for _assertion in _list(exp.get("assertions")):
            if not isinstance(_assertion, dict):
                continue
            # ── Read entity_bindings (produced by protocol compiler) ──
            _bindings = _dict(_assertion.get("entity_bindings"))
            _root_entity = _text(_assertion.get("root_entity"))
            _related_entities = _list(_assertion.get("related_entities"))
            # Fallback: derive root/related from entity_bindings
            if not _root_entity and _bindings:
                for _alias, _info in _bindings.items():
                    _ent_name = _text(_info.get("entity_name")) if isinstance(_info, dict) else _text(_info)
                    if _alias == "root" or (_info.get("cardinality") if isinstance(_info, dict) else "") != "MANY":
                        if not _root_entity:
                            _root_entity = _ent_name
                            # Store under both entity name and "root" alias
                            _multi_entity["root"] = {"before": _root_before, "after": _root_after}
                            if _ent_name:
                                _multi_entity[_ent_name] = {"before": _root_before, "after": _root_after}
                    else:
                        _related_entities.append({"entity": _ent_name, "alias": _alias})
            elif _root_entity:
                _multi_entity[_root_entity] = {
                    "before": _root_before,
                    "after": _root_after,
                }
                _multi_entity["root"] = {
                    "before": _root_before,
                    "after": _root_after,
                }
            # Try to extract related entity data from response bodies
            for _rel in _related_entities:
                _rel_name = _text(_rel) if isinstance(_rel, str) else _text(_rel.get("entity")) if isinstance(_rel, dict) else ""
                _rel_alias = _text(_rel.get("alias")) if isinstance(_rel, dict) else ""
                if not _rel_name:
                    continue
                # Search for related data in before/after bodies
                _rel_before = _extract_related_from_body(_root_before, _rel_name)
                _rel_after = _extract_related_from_body(_root_after, _rel_name)
                if _rel_before is not None or _rel_after is not None:
                    _multi_entity[_rel_name] = {
                        "before": _rel_before,
                        "after": _rel_after,
                    }
                    # Also store under alias and generic "related" key
                    if _rel_alias:
                        _multi_entity[_rel_alias] = {"before": _rel_before, "after": _rel_after}
                    _multi_entity.setdefault("related", {"before": _rel_before, "after": _rel_after})

        # ── Merge related entity observations from active observer execution ──
        _related_multi_state = _dict(observations.get("related_entity_multi_state"))
        if _related_multi_state:
            for _key, _state in _related_multi_state.items():
                if not isinstance(_state, dict):
                    continue
                _records = _state.get("records", [])
                # Store collection data for assertion DSL
                _multi_entity[_key] = {
                    "before": _records,  # Collection is same before/after for read-only
                    "after": _records,
                    "records": _records,
                    "record_count": _state.get("record_count", len(_records)),
                    "pagination": _state.get("pagination", {}),
                    "collection_status": _state.get("collection_status", ""),
                }

        if _multi_entity:
            observations["multi_entity_state"] = _multi_entity

    # ── Cross-Entity Observation Completeness Strategy ──
    # Activates automatically when structured expressions require multi-entity
    # observation. Validates Oracle input completeness BEFORE oracle call.
    # Single-entity rules bypass this entirely (no overhead).
    _completeness_proof: dict[str, Any] | None = None
    _completeness_gate_blocked = False
    _completeness_blocked_reason = ""
    if _has_structured_expr:
        _ce_result = _run_cross_entity_observation_completeness(
            exp=exp,
            observations=observations,
            before_states=_before_states,
            after_states=_after_states,
            eid=eid,
            oid=oid,
        )
        _completeness_proof = _ce_result.get("proof")
        _completeness_gate_blocked = _ce_result.get("blocked", False)
        _completeness_blocked_reason = _text(_ce_result.get("blocked_reason"))
        # Store all completeness artifacts in observations for trace
        if _ce_result.get("observation_requirement"):
            observations["cross_entity_observation_requirement"] = _ce_result["observation_requirement"]
        if _ce_result.get("scope_proof"):
            observations["cross_entity_scope_proof"] = _ce_result["scope_proof"]
        if _ce_result.get("snapshot_pair"):
            observations["cross_entity_snapshot_pair"] = _ce_result["snapshot_pair"]
        if _ce_result.get("deltas"):
            observations["cross_entity_deltas"] = _ce_result["deltas"]
        if _completeness_proof:
            observations["cross_entity_completeness_proof"] = _completeness_proof
        observations["cross_entity_completeness_gate_blocked"] = _completeness_gate_blocked

    # Materialize + evaluate typed assertions via contract oracle.
    assertions = []
    for raw in _list(exp.get("assertions")):
        if isinstance(raw, dict):
            assertions.append(materialize_assertion(raw, observations=observations))
    exp_for_oracle = dict(exp)
    exp_for_oracle["assertions"] = assertions
    # ── Completeness Gate: block Oracle when input is incomplete ──
    if _completeness_gate_blocked:
        # Do NOT call Oracle with incomplete inputs. Return INDETERMINATE.
        verdict = {
            "status": "INDETERMINATE",
            "verdict": "blocked_experiment",
            "reason_codes": [_completeness_blocked_reason or ORACLE_INPUT_INCOMPLETE],
            "assertions": [],
            "missing_requirements": [_completeness_blocked_reason or ORACLE_INPUT_INCOMPLETE],
            "oracle_blocked_by_completeness_gate": True,
            "completeness_proof": _completeness_proof,
        }
    else:
        verdict = evaluate_contract_oracle(experiment=exp_for_oracle, evidence=observations)

    # ── P0-10: Build field-level oracle trace ──
    oracle_trace: list[dict[str, Any]] = []
    for _ar in _list(verdict.get("assertions")):
        if not isinstance(_ar, dict):
            continue
        _ar_kind = _text(_ar.get("kind"))
        _ar_expected = _ar.get("expected") if isinstance(_ar.get("expected"), dict) else {}
        _ar_actual = _ar.get("actual") if isinstance(_ar.get("actual"), dict) else {}
        trace_entry: dict[str, Any] = {
            "rule_id": _text(_ar.get("assertion_id") or _ar.get("receipt_id")),
            "kind": _ar_kind,
            "result": _text(_ar.get("status")),
            "reason_code": _text(_ar.get("reason_code")),
        }
        if _ar_kind == "field_delta":
            # Causal oracle trace: per-field before/after/delta
            trace_entry["observed_fields"] = _list(_ar_actual.get("field_results"))
            trace_entry["expected_fields"] = _list(_ar_expected.get("fields"))
        elif _ar_kind == "conservation":
            # Conservation trace: terms, before/after sums, per-term values
            trace_entry["terms"] = _list(_ar_expected.get("terms"))
            trace_entry["operator"] = _text(_ar_expected.get("operator"))
            trace_entry["before_sum"] = _ar_actual.get("before_sum")
            trace_entry["after_sum"] = _ar_actual.get("after_sum")
            trace_entry["before_values"] = _ar_actual.get("before")
            trace_entry["after_values"] = _ar_actual.get("after")
        elif _ar_kind == "state_transition":
            # State transition trace: from/to/observed
            trace_entry["from_state"] = _text(_ar_expected.get("before"))
            trace_entry["to_state"] = _text(_ar_expected.get("after"))
            trace_entry["observed_before"] = _ar_actual.get("before")
            trace_entry["observed_after"] = _ar_actual.get("after")
        else:
            trace_entry["expected"] = _ar_expected
            trace_entry["actual"] = _ar_actual
        oracle_trace.append(trace_entry)
    observations["oracle_trace"] = oracle_trace

    # ── SPEC v1.1 §15: Cleanup Equivalence Evaluation ──
    # For governed writes, evaluate whether cleanup restored business state.
    cleanup_equivalence_receipt: dict[str, Any] | None = None
    safety = _dict(exp.get("safety_contract"))
    is_governed_write = safety.get("governed_write")
    if is_governed_write:
        proof = _dict(exp.get("write_reversibility_proof"))
        if proof and _text(proof.get("proof_status")) == "PROVEN":
            # Extract observations for equivalence evaluation
            before_obs = _dict(observations.get("before_state_observation"))
            after_write_obs = _dict(observations.get("after_state_observation"))
            after_cleanup_obs = _dict(observations.get("final_state_observation"))
            cleanup_exec_receipt = _dict(observations.get("cleanup_execution_receipt"))
            # If no explicit cleanup receipt, check if cleanup succeeded
            if not cleanup_exec_receipt:
                cleanup_exec_receipt = {
                    "succeeded": cleanup_failures == 0,
                    "status_code": observations.get("cleanup_status_code"),
                }
            cleanup_equivalence_receipt = evaluate_cleanup_equivalence(
                proof=proof,
                before_observation=before_obs,
                after_write_observation=after_write_obs,
                after_cleanup_observation=after_cleanup_obs,
                runtime_bindings=runtime_bindings,
                cleanup_execution_receipt=cleanup_exec_receipt,
            )
            observations["cleanup_equivalence_receipt"] = cleanup_equivalence_receipt

    finding = None
    # Execution status reflects actual HTTP activity, not oracle assessment.
    # Oracle verdict is advisory evidence quality metadata.
    has_http = any(
        isinstance(s, dict) and isinstance(s.get("status_code"), int) and s["status_code"] > 0
        for s in steps_out
    )
    status = "EXECUTED" if has_http else "HARNESS_FAILURE"
    # ── P0-9: Fine-grained harness failure classification ──
    harness_failure_reason = ""
    if not has_http:
        harness_failure_reason = _classify_harness_failure(
            steps_out, observations, pre_transport_block_reasons,
            cleanup_failures=cleanup_failures,
        )
    if pre_transport_block_reasons:
        # Pre-transport blocks (binding placeholders, unresolved paths) mean the
        # request never reached the target — this is a BLOCKED outcome, not a
        # harness failure in the evidence infrastructure.
        status = "BLOCKED"
        reason = _pre_transport_reason_code(pre_transport_block_reasons)
        detail = ",".join(dict.fromkeys(pre_transport_block_reasons))
        # ── Diagnostic: log block reasons to stderr ──
        import sys as _sys_diag
        print(f"[DIAG] {oid}: reason={reason}, details={detail[:500]}", file=_sys_diag.stderr)
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": status,
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "steps": steps_out,
            "fixture_receipts": fixture_receipts,
            "binding_materialization_receipts": binding_materialization_receipts,
            "observer_receipts": observer_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
            "oracle_verdict": verdict,
            "finding": None,
            "cleanup_failures": cleanup_failures,
            "execution_receipt": {
                "status": status,
                "reason_code": reason,
                "detail": detail,
            },
        }
    if verdict.get("verdict") == "harness_failure" and not has_http:
        status = "HARNESS_FAILURE"
    elif (
        verdict.get("verdict") == "blocked_experiment"
        or verdict.get("status") == "INDETERMINATE"
    ):
        status = "BLOCKED"
        reason = "BLOCKED_MISSING_OBSERVER"
        detail = ",".join(_list(verdict.get("missing_requirements"))[:8])
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": status,
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "steps": steps_out,
            "fixture_receipts": fixture_receipts,
            "binding_materialization_receipts": binding_materialization_receipts,
            "observer_receipts": observer_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
            "oracle_verdict": verdict,
            "finding": None,
            "cleanup_failures": cleanup_failures,
            "execution_receipt": {"status": status, "reason_code": reason, "detail": detail},
        }
    elif (
        verdict.get("status") == "VIOLATION"
        and verdict.get("customer_deliverable_candidate") is True
        and verdict.get("verdict") == "customer_deliverable_defect_candidate"
    ):
        failed = _list(verdict.get("failed_assertions"))
        first = _dict(failed[0] if failed else {})
        treatment_plan = [step for step in _list(exp.get("treatment_plan")) if isinstance(step, dict)]
        control_plan = [step for step in _list(exp.get("control_plan")) if isinstance(step, dict)]
        primary_plan_step = _dict(treatment_plan[0] if treatment_plan else control_plan[0] if control_plan else {})
        primary_op = ops.get(_text(primary_plan_step.get("operation_ref"))) or {}
        primary_method = _text(primary_op.get("method") or "GET").upper()
        treatment_observation = _dict(observations.get("treatment_observation"))
        primary_path = _text(
            treatment_observation.get("path")
            or _materialize_path(
                _text(primary_op.get("path") or primary_op.get("raw_path")),
                runtime_bindings,
            )
        )
        treatment_actor = actors.get(_text(primary_plan_step.get("actor_ref"))) or {}
        treatment_role = _text(treatment_actor.get("role") or primary_plan_step.get("actor_ref"))
        control_step = _dict(control_plan[0] if control_plan else {})
        control_actor = actors.get(_text(control_step.get("actor_ref"))) or {}
        control_role = _text(control_actor.get("role") or control_step.get("actor_ref"))
        assertion_kind = _text(first.get("kind") or "contract")
        assertion_contract = _dict(
            next(
                (
                    value
                    for value in _list(exp.get("assertions"))
                    if isinstance(value, dict)
                    and _text(value.get("assertion_id"))
                    == _text(first.get("assertion_id"))
                ),
                _dict(_list(exp.get("assertions"))[0])
                if _list(exp.get("assertions"))
                else {},
            )
        )
        property_spec = _dict(assertion_contract.get("property"))
        control_observation = _dict(observations.get("control_observation"))
        observed_status = int(treatment_observation.get("status_code") or 0)
        observed_body = treatment_observation.get("body")
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reproduction_steps = [
            f"{_text(step.get('method')).upper()} {_text(step.get('path'))} -> HTTP {int(step.get('status_code') or 0)}"
            for step in steps_out
            if isinstance(step, dict) and _text(step.get("method")) and _text(step.get("path"))
        ]
        description = _text(
            first.get("error")
            or f"control={control_role or 'unspecified'} succeeded; treatment={treatment_role or 'unspecified'} violated the typed assertion"
        )
        actual_payload = first.get("actual") if isinstance(first.get("actual"), dict) else {}
        after_values = actual_payload.get("after_values")
        before_values = actual_payload.get("before_values")
        if isinstance(after_values, dict) and after_values:
            description = (
                f"{description}; observed entity numerics before={before_values!s} "
                f"after={after_values!s}"
            )
        finding = {
            "severity": "P1",
            "title": f"[ContractOracle] {assertion_kind}: {treatment_role or 'actor'} {primary_method} {primary_path}",
            "category": assertion_kind,
            "risk_family": (
                _text(exp.get("risk_family"))
                or (
                    "isolation"
                    if _text(first.get("assertion_id") or assertion_contract.get("assertion_id"))
                    == "assert_isolation"
                    or _text(property_spec.get("template") or assertion_contract.get("template"))
                    == "owner_viewer_isolation"
                    else ""
                )
                or (
                    "authorization"
                    if assertion_kind == "owner_tenant_visibility"
                    else assertion_kind
                )
            ),
            "source": "experiment_contract_oracle",
            "description": description,
            "confidence_score": 0.85,
            "experiment_id": eid,
            "obligation_id": oid,
            "campaign_id": campaign_id,
            "source_refs": [dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)],
            "timestamp": timestamp,
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "gate_passed": False,
            "bug_status": "suspected",
            "customer_delivery_status": "candidate",
            "oracle": {
                "oracle_name": "ContractOracle",
                "oracle_tier": "contract",
                "customer_deliverable": False,
                "customer_deliverable_candidate": True,
                "verdict": verdict.get("verdict"),
                "status": verdict.get("status"),
                "receipt_id": verdict.get("receipt_id"),
                "activation_receipt_id": verdict.get("activation_receipt_id"),
            },
            "oracle_receipt_id": verdict.get("receipt_id"),
            "activation_receipt_id": verdict.get("activation_receipt_id"),
            "expected": first.get("expected"),
            "actual": first.get("actual"),
            "evidence": {
                "request": f"{primary_method} {primary_path}",
                "response": f"HTTP {observed_status}",
                "target": primary_path,
                "actor": treatment_role,
                "timestamp": timestamp,
                "reproduction_steps": reproduction_steps,
                "execution_semantics": "read_only" if primary_method in {"GET", "HEAD", "OPTIONS"} else "governed_write",
                "control_succeeded": observations.get("control_succeeded"),
                "effect_count": observations.get("effect_count"),
                "invariant_held": observations.get("invariant_held"),
                "assertion": first,
                "cleanup_status": observations.get("cleanup_status"),
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "timestamp": timestamp,
                "request_raw": {"method": primary_method, "path": primary_path, "actor": treatment_role},
                "response_raw": {"status_code": observed_status, "body": observed_body},
                "db_snapshot": {
                    "before": control_observation.get("body"),
                    "after": observed_body,
                    "assertion": first,
                },
                "control_actor": control_role,
                "treatment_actor": treatment_role,
                "steps": steps_out[:20],
                "observations": {
                    k: observations[k]
                    for k in (
                        "status_code",
                        "control_succeeded",
                        "viewer_can_access",
                        "leak_detected",
                        "cleanup_status",
                    )
                    if k in observations
                },
            },
            "reproduction": {
                "method": primary_method,
                "path": primary_path,
                "actor": treatment_role,
                "reproduction_steps": reproduction_steps,
            },
            "reproduction_steps": reproduction_steps,
            "evidence_quality": {
                "level": "executed_candidate",
                "score": 0,
                "can_reproduce": False,
                "evidence_strength": "typed_contract_violation_pending_gate",
            },
            "evidence_status": {
                "semantic_verdict": "ASSERTION_VIOLATION",
                "business_evidence_status": "PENDING_DELIVERY_GATE",
                "final_review_status": "PENDING_DELIVERY_GATE",
                "missing_requirements": ["independent_delivery_gate_receipt"],
            },
            "final_review_status": "PENDING_DELIVERY_GATE",
            "business_evidence_status": "PENDING_DELIVERY_GATE",
            "failed_assertions": failed,
            "cleanup_failures": cleanup_failures,
            "contract_evidence_receipt_ids": [
                _text(receipt.get("receipt_id"))
                for receipt in contract_evidence_receipts
            ],
        }
        # The executor authors evidence and a candidate only.  It never authors
        # the independent delivery decision that consumes this receipt chain.
        if cleanup_failures:
            finding = mark_as_internal_clue(finding, reason="cleanup_compensation_failed")
    elif verdict.get("verdict") == "executed_clue":
        finding = mark_as_internal_clue(
            {
                "severity": "P2",
                "title": f"[ContractOracle clue] {oid}",
                "source": "experiment_contract_oracle",
                "experiment_id": eid,
                "obligation_id": oid,
                "campaign_id": campaign_id,
                "execution_status": "executed",
                "oracle": {"oracle_name": "ContractOracle", "oracle_tier": "internal_clue"},
                "evidence": {"demotion_reason": verdict.get("demotion_reason")},
                "raw_evidence": {"has_real_evidence": True, "steps": steps_out[:10]},
            },
            reason=_text(verdict.get("demotion_reason") or "executed_clue"),
        )

    # ── P0-12: Finding filter reason ──
    _finding_created = finding is not None
    _finding_filter_reason = ""
    if not _finding_created:
        if status == "HARNESS_FAILURE":
            _finding_filter_reason = f"environment_error:{harness_failure_reason or 'harness_failure'}"
        elif status == "BLOCKED":
            _finding_filter_reason = "environment_blocked"
        elif _text(verdict.get("status")) == "PROPERTY_HELD":
            _finding_filter_reason = "oracle_property_held"
        elif _text(verdict.get("status")) == "INDETERMINATE":
            _finding_filter_reason = "oracle_indeterminate"
        elif _text(verdict.get("status")) == "BLOCKED":
            _finding_filter_reason = "oracle_blocked"
        else:
            _finding_filter_reason = "no_violation_detected"

    return {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": eid,
        "obligation_id": oid,
        "status": status,
        "reason_code": harness_failure_reason if status == "HARNESS_FAILURE" else "",
        "detail": harness_failure_reason if status == "HARNESS_FAILURE" else "",
        "elapsed_ms": int((time.time() - started) * 1000),
        "steps": steps_out,
        "fixture_receipts": fixture_receipts,
        "binding_materialization_receipts": binding_materialization_receipts,
        "observer_receipts": observer_receipts,
        "contract_evidence_receipts": contract_evidence_receipts,
        "oracle_verdict": verdict,
        "finding": finding,
        "finding_created": _finding_created,
        "finding_filter_reason": _finding_filter_reason,
        "cleanup_failures": cleanup_failures,
        "cleanup_equivalence_receipt": cleanup_equivalence_receipt,
        "execution_receipt": {
            "status": status,
            "steps": len(steps_out),
            "cleanup_failures": cleanup_failures,
            "binding_materialization_receipts": len(binding_materialization_receipts),
            "verdict": verdict.get("verdict"),
            "harness_failure_reason": harness_failure_reason or None,
            "finding_created": _finding_created,
            "finding_filter_reason": _finding_filter_reason or None,
            "cleanup_equivalence_status": _text(
                cleanup_equivalence_receipt.get("equivalence_status")
            ) if cleanup_equivalence_receipt else None,
        },
    }

