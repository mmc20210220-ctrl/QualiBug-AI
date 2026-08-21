"""Observation-driven Behavior IR expansion between immutable planning rounds."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .adaptive_discovery_planner import build_agent_intent_plan, plan_obligation_round
from .behavior_ir import build_behavior_ir_from_knowledge_asset
from .discovery_runtime_planning import _target_service_name_from_base_url
from .experiment_compiler import compile_experiments
from .fixture_dag import attach_fixture_dag_to_experiments
from .obligation_compiler import compile_obligations_from_behavior_ir
from .runtime_interface_discovery import merge_runtime_discovered_operations
from .runtime_probe_contract_derivation import (
    derive_runtime_probe_contracts,
    probe_observations_from_receipts,
)
from .source_performance_contract_binding import bind_source_performance_contracts
from .source_stability_contract_binding import bind_source_stability_contracts


ROUND_RECEIPT_SCHEMA = "qualibug.behavior-ir-expansion-round.v1"
_VARIANT_RE = re.compile(r"^(.+?)__v_[a-f0-9]+$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_key(operation: dict[str, Any]) -> tuple[str, str]:
    return _text(operation.get("method")).upper(), _text(operation.get("path"))


def _experiments_by_obligation(
    experiment_pack: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    experiments = [
        dict(row)
        for row in (
            _list(experiment_pack.get("experiments"))
            + _list(experiment_pack.get("blocked_experiments"))
        )
        if isinstance(row, dict)
    ]
    indexed: dict[str, dict[str, Any]] = {}
    for experiment in experiments:
        obligation_id = _text(experiment.get("obligation_id"))
        if obligation_id:
            indexed[obligation_id] = experiment
        expanded_from = _text(experiment.get("expanded_from_obligation_id"))
        if expanded_from:
            indexed.setdefault(expanded_from, experiment)
        variant = _VARIANT_RE.match(obligation_id)
        if variant:
            indexed.setdefault(variant.group(1), experiment)
    return experiments, indexed


def _selected_rows(
    obligations: list[dict[str, Any]],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    agent_intent_plan: dict[str, Any],
    planning_round: int,
) -> list[dict[str, Any]]:
    intents = {
        _text(row.get("obligation_id")): dict(row)
        for row in _list(agent_intent_plan.get("intents"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    rows: list[dict[str, Any]] = []
    for obligation in obligations:
        obligation_id = _text(obligation.get("obligation_id"))
        if obligation_id not in intents:
            continue
        experiment = _dict(experiments_by_obligation.get(obligation_id))
        intent = _dict(intents.get(obligation_id))
        adapters = [
            _text(value)
            for value in _list(intent.get("execution_adapters"))
            if _text(value)
        ]
        rows.append({
            **obligation,
            "candidate_id": _text(obligation.get("candidate_id")) or obligation_id,
            "experiment_id": _text(experiment.get("experiment_id")),
            "adapter": (
                adapters[0]
                if len(adapters) == 1
                else "multi_surface"
                if adapters
                else "unavailable"
            ),
            "execution_adapters": adapters,
            "agent_intent_id": _text(intent.get("intent_id")),
            "planning_round": planning_round,
            "operation_refs": list(obligation.get("required_operations") or []),
            "actor_refs": list(obligation.get("required_actors") or []),
            "behavior_ir_refs": list(obligation.get("relation_refs") or []),
            "selection_status": "SELECTED",
        })
    return rows


def _round_receipt(
    *,
    planning_round: int,
    input_behavior_ir_id: str,
    output_behavior_ir_id: str,
    observation_receipts: list[dict[str, Any]],
    discovered_operations: list[dict[str, Any]],
    new_obligation_count: int,
    recompiled_obligation_ids: list[str] | None = None,
    selected_count: int,
    stop_reason: str,
) -> dict[str, Any]:
    recompiled_ids = sorted(
        _text(value)
        for value in (recompiled_obligation_ids or [])
        if _text(value)
    )
    receipt = {
        "schema_version": ROUND_RECEIPT_SCHEMA,
        "planning_round": planning_round,
        "input_behavior_ir_id": input_behavior_ir_id,
        "output_behavior_ir_id": output_behavior_ir_id,
        "observation_receipt_fingerprints": sorted(
            _text(row.get("receipt_fingerprint"))
            for row in observation_receipts
            if _text(row.get("receipt_fingerprint"))
        ),
        "discovered_operations": [
            {"method": key[0], "path": key[1]}
            for key in sorted(_operation_key(row) for row in discovered_operations)
        ],
        "discovered_operation_count": len(discovered_operations),
        "new_obligation_count": new_obligation_count,
        "recompiled_obligation_count": len(recompiled_ids),
        "recompiled_obligation_ids": recompiled_ids,
        "recompile_authority": (
            "blocked_round0_without_target_request"
            if recompiled_ids
            else "none"
        ),
        "selected_count": selected_count,
        "stop_reason": stop_reason,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    return receipt


def expand_behavior_ir_from_runtime_observations(
    *,
    initial_behavior_ir: dict[str, Any],
    existing_obligation_ids: set[str],
    recompile_obligation_ids: set[str] | None = None,
    knowledge_asset: dict[str, Any],
    documented_operations: list[dict[str, Any]],
    observation_receipts: list[dict[str, Any]],
    project_id: str,
    source_snapshot_hash: str,
    runtime_actors: list[dict[str, Any]],
    environment_type: str,
    policy_version: str,
    budget: int,
    planning_round: int,
    planning_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild IR from proven runtime facts and compile only new obligations."""

    if isinstance(budget, bool) or budget <= 0:
        raise ValueError("behavior_ir_expansion_budget_invalid")
    if isinstance(planning_round, bool) or planning_round < 2:
        raise ValueError("behavior_ir_expansion_round_invalid")
    initial_id = _text(initial_behavior_ir.get("model_id"))
    if not initial_id:
        raise ValueError("behavior_ir_expansion_input_model_id_missing")

    recompile_ids = {
        _text(value)
        for value in (recompile_obligation_ids or set())
        if _text(value)
    }
    merged_operations = merge_runtime_discovered_operations(
        documented_operations,
        observation_receipts,
    )
    # ── 档位 D breadth closure (runtime-probe contract derivation) ──
    # Open-class bug families (performance_latency / stability_reliability)
    # were only reachable from source-declared contracts.  The governed surface
    # probe already recorded per-sample status + latency for the discovered
    # endpoints (P2 instrumentation); derive the SAME formal-contract asset
    # keys from those observations so the discovered operations can yield
    # open-class obligations in this expansion round.  Reuses
    # bind_source_*_contracts + the contract-row builders — no new wheel.
    _probe_obs = probe_observations_from_receipts(observation_receipts)
    _runtime_probe_receipt: dict[str, Any] = {
        "schema_version": "qualibug.runtime-probe-contract-derivation.v1",
        "status": "SKIPPED",
        "reason": "no_probe_observations",
    }
    if _probe_obs:
        knowledge_asset, _runtime_probe_receipt = derive_runtime_probe_contracts(
            knowledge_asset,
            operations=merged_operations,
            runtime_observations=_probe_obs,
            runtime_actors=runtime_actors,
        )

    def _bind_runtime_probe_contracts(ir: dict[str, Any]) -> dict[str, Any]:
        """Add performance/stability invariants derived from runtime probes.

        ``build_behavior_ir_from_knowledge_asset`` does not bind source
        contracts, so the invariants must be attached here with the same
        binders ``build_discovery_plan`` uses.  Idempotent across rounds:
        binders dedup by contract_id.
        """
        ir, _perf = bind_source_performance_contracts(ir, knowledge_asset)
        ir, _stab = bind_source_stability_contracts(ir, knowledge_asset)
        return ir

    documented_keys = {
        _operation_key(row)
        for row in documented_operations
        if isinstance(row, dict)
    }
    discovered_operations = [
        dict(row)
        for row in merged_operations
        if _operation_key(row) not in documented_keys
    ]
    # Single-service scope: the expansion recompiles the IR for a run pinned to
    # one approved base_url. Derive the target service the same way planning
    # does and scope the compile so other services' operations never become
    # executable obligations here (they would 404 against this base_url and
    # surface as routing artifacts, not defects).
    _expansion_target_service = _target_service_name_from_base_url(
        _text(_dict(planning_context).get("base_url"))
    )
    # Recompile-only feedback may reopen BLOCKED/ABSTRACT obligations after
    # materialization capabilities improve, even when no new operation identity
    # was discovered. New-operation stagnation still applies when neither path
    # has work.
    if not discovered_operations and not recompile_ids:
        receipt = _round_receipt(
            planning_round=planning_round,
            input_behavior_ir_id=initial_id,
            output_behavior_ir_id=initial_id,
            observation_receipts=observation_receipts,
            discovered_operations=[],
            new_obligation_count=0,
            recompiled_obligation_ids=[],
            selected_count=0,
            stop_reason="no_new_runtime_operations",
        )
        return {
            "status": "STAGNATED",
            "behavior_ir": dict(initial_behavior_ir),
            "delta_obligations": [],
            "recompile_obligations": [],
            "experiment_pack": {"experiments": [], "blocked_experiments": []},
            "all_experiments": [],
            "by_obligation": {},
            "obligation_plan": {},
            "agent_intent_plan": {},
            "selected_rows": [],
            "round_receipt": receipt,
        }

    if discovered_operations:
        behavior_ir = build_behavior_ir_from_knowledge_asset(
            knowledge_asset,
            project_id=project_id,
            source_snapshot_hash=source_snapshot_hash,
            api_operations=merged_operations,
            runtime_actors=runtime_actors,
        )
        behavior_ir = _bind_runtime_probe_contracts(behavior_ir)
        obligation_pack = compile_obligations_from_behavior_ir(
            behavior_ir,
            root=_text(_dict(planning_context).get("root")),
            project=_text(_dict(planning_context).get("project")),
            target_service_name=_expansion_target_service,
        )
        all_obligations = [
            dict(row)
            for row in _list(obligation_pack.get("obligations"))
            if isinstance(row, dict)
            and _text(row.get("obligation_id"))
        ]
    else:
        # Recompile-only: keep the immutable IR identity and retry named
        # obligations against the same operation set + planning materialization.
        behavior_ir = dict(initial_behavior_ir)
        behavior_ir = _bind_runtime_probe_contracts(behavior_ir)
        obligation_pack = compile_obligations_from_behavior_ir(
            behavior_ir,
            root=_text(_dict(planning_context).get("root")),
            project=_text(_dict(planning_context).get("project")),
            target_service_name=_expansion_target_service,
        )
        all_obligations = [
            dict(row)
            for row in _list(obligation_pack.get("obligations"))
            if isinstance(row, dict)
            and _text(row.get("obligation_id"))
        ]
    available_ids = {
        _text(row.get("obligation_id")) for row in all_obligations
    }
    recompile_ids &= available_ids & {
        _text(value) for value in existing_obligation_ids if _text(value)
    }
    recompile_obligations = []
    for row in all_obligations:
        obligation_id = _text(row.get("obligation_id"))
        if obligation_id not in recompile_ids:
            continue
        retry = dict(row)
        # Compilation must observe the expanded IR, not carry the round-0
        # compiler status forward as if it were a new result.
        for key in (
            "compile_status",
            "block_reason",
            "expanded_experiment_count",
            "compiled_experiment_count",
            "blocked_experiment_count",
        ):
            retry.pop(key, None)
        retry["recompile_from_obligation_id"] = obligation_id
        recompile_obligations.append(retry)
    delta_obligations = [
        row
        for row in all_obligations
        if _text(row.get("obligation_id")) not in existing_obligation_ids
    ] if discovered_operations else []
    round_obligations = [*recompile_obligations, *delta_obligations]
    if not round_obligations:
        receipt = _round_receipt(
            planning_round=planning_round,
            input_behavior_ir_id=initial_id,
            output_behavior_ir_id=_text(behavior_ir.get("model_id")) or initial_id,
            observation_receipts=observation_receipts,
            discovered_operations=discovered_operations,
            new_obligation_count=0,
            recompiled_obligation_ids=[],
            selected_count=0,
            stop_reason="recompile_ids_unavailable",
        )
        return {
            "status": "STAGNATED",
            "behavior_ir": behavior_ir,
            "delta_obligations": [],
            "recompile_obligations": [],
            "experiment_pack": {"experiments": [], "blocked_experiments": []},
            "all_experiments": [],
            "by_obligation": {},
            "obligation_plan": {},
            "agent_intent_plan": {},
            "selected_rows": [],
            "round_receipt": receipt,
        }
    experiment_pack = compile_experiments(
        round_obligations,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=policy_version,
        planning_context=planning_context,
    )
    experiment_pack = attach_fixture_dag_to_experiments(
        experiment_pack,
        behavior_ir=behavior_ir,
    )
    all_experiments, by_obligation = _experiments_by_obligation(experiment_pack)
    obligation_plan = plan_obligation_round(
        round_obligations,
        experiments_by_obligation=by_obligation,
        budget=budget,
    )
    agent_intent_plan = build_agent_intent_plan(
        obligation_plan,
        obligations=round_obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=behavior_ir,
    )
    all_selected_rows = _selected_rows(
        round_obligations,
        experiments_by_obligation=by_obligation,
        agent_intent_plan=agent_intent_plan,
        planning_round=planning_round,
    )
    selected_rows = [
        row
        for row in all_selected_rows
        if _text(row.get("obligation_id")) not in recompile_ids
    ]
    recompile_selected_rows = [
        row
        for row in all_selected_rows
        if _text(row.get("obligation_id")) in recompile_ids
    ]
    if delta_obligations:
        stop_reason = "round_ready"
    elif recompile_obligations and not discovered_operations:
        stop_reason = "recompile_without_new_operations"
    elif recompile_obligations:
        stop_reason = "runtime_operations_created_no_new_obligations"
    else:
        stop_reason = "runtime_operations_created_no_new_obligations"
    receipt = _round_receipt(
        planning_round=planning_round,
        input_behavior_ir_id=initial_id,
        output_behavior_ir_id=_text(behavior_ir.get("model_id")),
        observation_receipts=observation_receipts,
        discovered_operations=discovered_operations,
        new_obligation_count=len(delta_obligations),
        recompiled_obligation_ids=sorted(recompile_ids),
        selected_count=int(obligation_plan.get("selected_count") or 0),
        stop_reason=stop_reason,
    )
    return {
        "status": "EXPANDED" if discovered_operations else "RECOMPILED",
        "behavior_ir": behavior_ir,
        "delta_obligations": delta_obligations,
        "recompile_obligations": recompile_obligations,
        "round_obligations": round_obligations,
        "experiment_pack": experiment_pack,
        "all_experiments": all_experiments,
        "by_obligation": by_obligation,
        "obligation_plan": obligation_plan,
        "agent_intent_plan": agent_intent_plan,
        "selected_rows": selected_rows,
        "recompile_selected_rows": recompile_selected_rows,
        "round_receipt": receipt,
    }
