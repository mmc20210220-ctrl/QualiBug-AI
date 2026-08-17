"""Discovery planning: API ops, actors, Behavior IR, obligations, experiments.

Extracted from ``discovery_runtime``. ``build_discovery_plan`` remains the
immutable planning authority for the experiment-candidate mainline. Symbols are
re-exported from ``discovery_runtime`` for compatibility.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

_planning_logger = logging.getLogger("qualibug.discovery_planning")

from .adaptive_discovery_planner import (
    build_agent_intent_plan,
    plan_obligation_round,
)
from .adaptive_planning_history import (
    build_planning_budget_receipt,
    build_planning_history_receipt,
    finalize_planning_budget_receipt,
    select_matching_historical_yield,
)
from .agent_semantic_linker import (
    RECEIPT_SCHEMA as AGENT_SEMANTIC_LINK_RECEIPT_SCHEMA,
    enrich_knowledge_asset_with_agent_relationships,
)
from .behavior_ir import build_behavior_ir_from_knowledge_asset
from .discovery_mainline import (
    DiscoveryMainlineInputs,
    DiscoveryPlanningBundle,
)
from .discovery_mainline_contract import (
    MainlineContractError,
    MainlineRunContract,
    build_mainline_run_contract,
)
from .experiment_compiler import compile_experiments
from .experiment_runtime_support import (
    run_environment_preflight,
)
from .fixture_dag import attach_fixture_dag_to_experiments
from .obligation_compiler import compile_obligations_from_behavior_ir
from .pipeline_slices import _auto_scale_slice_budget
from .runtime_interface_discovery import (
    load_runtime_interface_discovery_budget,
    plan_runtime_interface_candidates,
)
from .test_obligation import dedupe_obligations, json_fingerprint
from .discovery_runtime_planning_actors import (  # noqa: F401
    _api_operations,
    _runtime_actors,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _source_snapshot_hash(context: dict[str, Any]) -> str:
    manifest = context.get("source_manifest")
    if not isinstance(manifest, dict):
        return ""
    return _text(manifest.get("source_hash"))


def _campaign_object(handle: Any) -> Any:
    campaign = _dict(handle).get("campaign") if isinstance(handle, dict) else handle
    if campaign is None or not _text(getattr(campaign, "campaign_id", "")):
        raise MainlineContractError("mainline_campaign_object_missing")
    return campaign


def _campaign_store(handle: Any) -> Any:
    store = _dict(handle).get("store") if isinstance(handle, dict) else None
    if store is None or not callable(getattr(store, "save", None)):
        raise MainlineContractError("mainline_campaign_store_missing")
    return store



def _contract(inputs: DiscoveryMainlineInputs, campaign_id: str) -> MainlineRunContract:
    context = inputs.campaign_context
    return build_mainline_run_contract(
        mainline_authority=_text(context.get("mainline_authority")),
        run_id=_text(context.get("run_id")),
        campaign_id=campaign_id,
        target_id=_text(context.get("target_id")),
        environment_id=_text(context.get("environment_id")),
        policy_version=_text(context.get("policy_version")),
        evaluation_mode=_text(context.get("evaluation_mode")),
        source_snapshot_hash=_source_snapshot_hash(context),
    )


# ── P0-1: multi-arm Experiment Bundle derivation (extracted from
# build_discovery_plan so the identity contract is directly testable) ──
# Each selected unit compiles ONCE (representative); the remaining role
# variants become execution arms by actor-rebinding the representative's
# compiled experiments. Fail-closed: an arm that cannot be derived (stale
# actor references, actor not in representative, cap exceeded) falls back
# to a normal independent compile — no variant is ever lost.
#
# Identity contract (the ``compiled_experiment_mismatch`` guard in
# ``build_agent_intent_plan``): every row added to
# ``obligation_plan["selected"]`` must reference exactly the experiment
# registered under its obligation_id in ``by_obligation``, and that
# experiment must be COMPILED. A variant that already has its own compiled
# experiment is bound to its own experiment (never double-derived — a second
# compiled experiment claiming one obligation id would split the identity);
# derived arms are force-indexed so the plan row and the identity index can
# never diverge; fallback-compiled variants enter the same compiled pool and
# index, and only COMPILED ones are selected (undeliverable ones stay honest
# BLOCKED and never reach the intent gate).


def _arm_compile_status(row: Any) -> str:
    row = _dict(row)
    status = _text(
        _dict(row.get("compile_receipt")).get("status")
    ).upper()
    if not status:
        status = _text(row.get("compile_status")).upper()
    return status


def _append_arm_selected_row(
    obligation_plan: dict[str, Any],
    *,
    obligation_id: str,
    experiment: dict[str, Any],
    unit_id: str,
    index: int,
    origin: str,
    score: float,
    obligations_by_id: dict[str, dict[str, Any]],
) -> None:
    obligation = obligations_by_id.get(obligation_id) or {}
    prop = _dict(experiment.get("property"))
    if not prop:
        prop = _dict(obligation.get("property"))
    obligation_plan["selected"] = list(_list(obligation_plan.get("selected"))) + [{
        "obligation_id": obligation_id,
        "risk_family": _text(
            experiment.get("risk_family") or obligation.get("risk_family")
        ),
        "path_prefix": _text(prop.get("operation_path_prefix")),
        "operation_key": "",
        "score": score,
        "experiment_id": _text(experiment.get("experiment_id")),
        "coverage_unit_id": unit_id,
        "arm_index": index,
        "arm_origin": origin,
    }]


def derive_unit_execution_arms(
    *,
    obligation_plan: dict[str, Any],
    units: list[dict[str, Any]],
    obligations_by_id: dict[str, dict[str, Any]],
    experiment_pack: dict[str, Any],
    all_experiments: list[dict[str, Any]],
    by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    environment_type: str,
    policy_version: str,
    available_adapters: Any,
    planning_context: dict[str, Any],
    compile_callback: Any = None,
) -> dict[str, Any]:
    """Derive execution arms for the selected units' remaining variants.

    Mutates ``obligation_plan`` (selected rows), ``experiment_pack``
    (experiments + compiled_count), ``all_experiments`` and
    ``by_obligation`` (compiled pool + identity index). Returns the arm
    receipt. ``compile_callback`` is the fallback compiler (defaults to
    ``compile_experiments``); tests inject a stub to exercise the
    fail-closed fallback path without a compiler run.
    """
    from .coverage_unit_registry import (
        MAX_ARMS_PER_UNIT as _UNIT_ARM_CAP,
        derive_arm_experiment,
    )

    compile_one = compile_callback or compile_experiments

    unit_by_id = {
        _text(unit.get("coverage_unit_id")): unit
        for unit in units
        if _text(unit.get("coverage_unit_id"))
    }
    _experiment_rows = [
        dict(row)
        for row in _list(experiment_pack.get("experiments"))
        if isinstance(row, dict)
    ]
    arm_experiments: list[dict[str, Any]] = []
    arm_fallback_obligation_ids: list[str] = []
    arm_fallback_unit_ids: dict[str, str] = {}
    arm_fallback_indexes: dict[str, int] = {}
    arm_derived_count = 0
    arm_own_compile_count = 0
    arm_failed_count = 0
    arm_capped_count = 0
    for unit_row in _list(obligation_plan.get("selected_units")):
        unit = unit_by_id.get(_text(unit_row.get("coverage_unit_id")))
        if unit is None:
            continue
        rep_id = _text(unit_row.get("obligation_id"))
        unit_id = _text(unit.get("coverage_unit_id"))
        variant_ids = [
            _text(oid)
            for oid in _list(unit.get("obligation_ids"))
            if _text(oid) and _text(oid) != rep_id
        ]
        if not variant_ids:
            continue
        # Representative's own compiled experiments (primary + its input
        # variants) are the arm sources: same semantic surface, actor-only
        # difference.
        rep_experiments = [
            exp
            for exp in _experiment_rows
            if _text(exp.get("obligation_id")) == rep_id
            or _text(exp.get("expanded_from_obligation_id")) == rep_id
            or _text(exp.get("obligation_id") or "").startswith(f"{rep_id}__v_")
        ]
        if not rep_experiments:
            for variant_id in variant_ids:
                arm_fallback_obligation_ids.append(variant_id)
                arm_fallback_unit_ids[variant_id] = unit_id
            continue
        for index, variant_id in enumerate(variant_ids[:_UNIT_ARM_CAP]):
            variant = obligations_by_id.get(variant_id)
            if variant is None:
                continue
            existing = by_obligation.get(variant_id)
            if _arm_compile_status(existing) == "COMPILED":
                # The variant already has its own compiled experiment (e.g.
                # representative-compile fallback): deriving an arm would
                # create a second compiled experiment claiming the same
                # obligation id, and the plan row would reference the arm
                # while the identity index keeps the old experiment —
                # ``compiled_experiment_mismatch``. Bind the plan row to the
                # variant's own experiment instead (zero coverage loss).
                arm_own_compile_count += 1
                _append_arm_selected_row(
                    obligation_plan,
                    obligation_id=variant_id,
                    experiment=existing,
                    unit_id=unit_id,
                    index=index,
                    origin="own_compile",
                    score=_num(variant.get("confidence")),
                    obligations_by_id=obligations_by_id,
                )
                continue
            derived_any = False
            for rep_exp in rep_experiments:
                arm, _receipt = derive_arm_experiment(
                    rep_exp,
                    variant,
                    coverage_unit_id=unit_id,
                    representative_obligation_id=rep_id,
                    arm_index=index,
                )
                if arm is None:
                    break
                derived_any = True
                arm_experiments.append(arm)
            if derived_any:
                arm_derived_count += 1
            else:
                arm_failed_count += 1
                arm_fallback_obligation_ids.append(variant_id)
                arm_fallback_unit_ids[variant_id] = unit_id
                arm_fallback_indexes[variant_id] = index
        if len(variant_ids) > _UNIT_ARM_CAP:
            arm_capped_count += len(variant_ids) - _UNIT_ARM_CAP
            for variant_id in variant_ids[_UNIT_ARM_CAP:]:
                arm_fallback_obligation_ids.append(variant_id)
                arm_fallback_unit_ids[variant_id] = unit_id
    # Fail-closed fallback compile for undeliverable arms
    fallback_compiled_selected_count = 0
    if arm_fallback_obligation_ids:
        fallback_arm_obligations = [
            obligations_by_id[oid]
            for oid in dict.fromkeys(arm_fallback_obligation_ids)
            if oid in obligations_by_id
        ]
        if fallback_arm_obligations:
            arm_fallback_pack = compile_one(
                fallback_arm_obligations,
                behavior_ir=behavior_ir,
                environment_type=environment_type,
                policy_version=policy_version,
                available_adapters=available_adapters,
                planning_context=planning_context,
            )
            for key in ("experiments", "blocked_experiments", "abstract_experiments"):
                experiment_pack[key] = list(_list(experiment_pack.get(key))) + list(
                    _list(arm_fallback_pack.get(key))
                )
            experiment_pack["compiled_count"] = int(
                experiment_pack.get("compiled_count") or 0
            ) + int(arm_fallback_pack.get("compiled_count") or 0)
            # 回退路径完整: fallback-compiled experiments must enter the same
            # compiled pool + identity index as every other compiled experiment
            # (execution reads by_obligation), and every COMPILED one is
            # selected this round — the unit was selected, its variant keeps an
            # executable face, zero coverage loss. Experiments that still came
            # back non-COMPILED stay honest BLOCKED: they are never added to
            # selected and can never pass the intent gate.
            for fallback_exp in _list(arm_fallback_pack.get("experiments")):
                if not isinstance(fallback_exp, dict):
                    continue
                fallback_oid = _text(fallback_exp.get("obligation_id"))
                if not fallback_oid:
                    continue
                by_obligation[fallback_oid] = fallback_exp
                all_experiments.append(dict(fallback_exp))
                if _arm_compile_status(fallback_exp) == "COMPILED":
                    fallback_compiled_selected_count += 1
                    _append_arm_selected_row(
                        obligation_plan,
                        obligation_id=fallback_oid,
                        experiment=fallback_exp,
                        unit_id=_text(arm_fallback_unit_ids.get(fallback_oid)),
                        index=int(arm_fallback_indexes.get(fallback_oid) or 0),
                        origin="fallback_compile",
                        score=_num(
                            _dict(obligations_by_id.get(fallback_oid)).get("confidence")
                        ),
                        obligations_by_id=obligations_by_id,
                    )
            _planning_logger.info(
                "coverage_unit_arm_fallback_compile obligations=%s compiled=%s selected=%s",
                len(fallback_arm_obligations),
                arm_fallback_pack.get("compiled_count"),
                fallback_compiled_selected_count,
            )
    # Merge arms into the experiment index and the selected set
    if arm_experiments:
        experiment_pack["experiments"] = (
            list(_list(experiment_pack.get("experiments"))) + arm_experiments
        )
        experiment_pack["compiled_count"] = int(
            experiment_pack.get("compiled_count") or 0
        ) + len(arm_experiments)
        all_experiments.extend(dict(arm) for arm in arm_experiments)
        for arm in arm_experiments:
            arm_id = _text(arm.get("obligation_id"))
            if arm_id:
                # Force-index: the arm is the authoritative compiled face for
                # this obligation id in this round, so the identity index can
                # never diverge from the selected row's experiment_id.
                by_obligation[arm_id] = arm
            _append_arm_selected_row(
                obligation_plan,
                obligation_id=arm_id,
                experiment=arm,
                unit_id=_text(arm.get("coverage_unit_id")),
                index=int(arm.get("arm_index") or 0),
                origin="derived",
                score=float(arm.get("arm_index") or 0),
                obligations_by_id=obligations_by_id,
            )
    arm_receipt = {
        "schema_version": "qualibug.coverage-unit-arm-receipt.v1",
        "status": "APPLIED",
        "arms_derived": arm_derived_count,
        "arm_experiment_count": len(arm_experiments),
        "arm_own_compile_count": arm_own_compile_count,
        "arm_failed_count": arm_failed_count,
        "arm_capped_count": arm_capped_count,
        "arm_fallback_compile_count": len(dict.fromkeys(arm_fallback_obligation_ids)),
        "arm_fallback_compiled_selected_count": fallback_compiled_selected_count,
        "max_arms_per_unit": _UNIT_ARM_CAP,
    }
    _planning_logger.info(
        "coverage_unit_arms status=APPLIED derived=%s own_compile=%s experiments=%s fallback=%s selected=%s",
        arm_derived_count,
        arm_own_compile_count,
        len(arm_experiments),
        arm_receipt["arm_fallback_compile_count"],
        fallback_compiled_selected_count,
    )
    return arm_receipt


def build_discovery_plan(
    inputs: DiscoveryMainlineInputs,
    campaign_handle: Any,
) -> DiscoveryPlanningBundle:
    """Compile one immutable Behavior IR -> Obligation -> Experiment plan."""

    campaign = _campaign_object(campaign_handle)
    contract = _contract(inputs, _text(campaign.campaign_id))
    from .enterprise_knowledge_center import (
        build_enterprise_business_knowledge_asset,
        build_runtime_source_knowledge_overlay,
        load_enterprise_business_knowledge_asset,
        merge_knowledge_asset_overlay,
        project_knowledge_world_model,
    )

    # Test-suite kill switch for the semantic recall channel: mirrors
    # QUALIBUG_MAINLINE_REASONER_DISABLED and QUALIBUG_AGENT_SEMANTIC_LINKING_DISABLED
    # so deterministic unit tests never reach a live provider. Production default
    # remains augment (default-ON) when the env var is absent.
    _semantic_recall_disabled = (
        str(os.environ.get("QUALIBUG_SEMANTIC_EXTRACTION_DISABLED") or "")
        .strip()
        .lower()
        in {"1", "true", "yes"}
    )

    asset = load_enterprise_business_knowledge_asset(
        inputs.project,
        inputs.root,
    ) or build_enterprise_business_knowledge_asset(
        inputs.project,
        inputs.root,
        options={
            # SPEC §12/§13: rule-extraction mode is operator-declared via
            # campaign context. Default augment (default-ON comprehension):
            # validated explicit LLM rule candidates are promoted through the
            # deterministic evidence/no-conflict/traceability gates. An explicit
            # rule_promotion_gates_met=False is the kill switch → shadow.
            "semantic_rule_extraction_mode": (
                "off"
                if _semantic_recall_disabled
                else _text(
                    inputs.campaign_context.get("semantic_rule_extraction_mode")
                )
                or "augment"
            ),
            "rule_promotion_gates_met": inputs.campaign_context.get(
                "rule_promotion_gates_met"
            ),
            "enable_semantic_extraction": (
                False
                if _semantic_recall_disabled
                else inputs.campaign_context.get("enable_semantic_extraction")
                if inputs.campaign_context.get("enable_semantic_extraction")
                is not None
                else True
            ),
        },
    )
    runtime_source_overlay = build_runtime_source_knowledge_overlay(
        prd_text=inputs.prd_text,
        api_spec_text=_text(
            inputs.campaign_context.get("_source_verification_text")
        ) or inputs.api_spec_text,
        db_schema_text=inputs.db_schema_text,
    )
    asset = merge_knowledge_asset_overlay(asset, runtime_source_overlay)
    # Structurize any new rules from the overlay that lack causal chains
    from .enterprise_knowledge_center import _structurize_rule_causal_chains
    asset["rule_library"] = _structurize_rule_causal_chains(
        asset.get("rule_library") or []
    )
    # ── Semantic contract binding (interface-documented contract rules) ──
    # Per-endpoint contract lines (关键契约/业务约束) inside the API document
    # are parsed as plain rules but the authoritative rule→interface channels
    # cannot bind pure-Chinese statements, so state/idempotency/conservation
    # semantics never reach an executable operation. The adapter re-attaches
    # them to the interface they are documented at (section line range /
    # verbatim containment / CJK action terms), structures conservation
    # equations and state preconditions, and binds state-machine transitions
    # to the operations whose contracts mention the TO state. Pure enrichment:
    # it never invents rules, operations, actors or fields; every edge carries
    # a named evidence channel. Runs before the Behavior IR build so the
    # existing rule_to_interface channel and compiler consume the semantics.
    from .enterprise_knowledge_center.semantic_contract_binding import (
        apply_semantic_contract_binding,
    )

    asset = apply_semantic_contract_binding(
        asset,
        api_spec_text=_text(
            inputs.campaign_context.get("_source_verification_text")
        ) or inputs.api_spec_text,
    )
    agent_semantic_link_receipt: dict[str, Any] = {
        "schema_version": AGENT_SEMANTIC_LINK_RECEIPT_SCHEMA,
        "status": "NOT_REQUESTED",
        "reason_code": "agent_semantic_linking_disabled",
        "accepted_relationship_count": 0,
    }
    semantic_linking_enabled = inputs.campaign_context.get(
        "agent_semantic_linking_enabled",
        False,
    )
    if not isinstance(semantic_linking_enabled, bool):
        raise MainlineContractError("agent_semantic_linking_enabled_not_boolean")
    if semantic_linking_enabled:
        asset, agent_semantic_link_receipt = (
            enrich_knowledge_asset_with_agent_relationships(asset)
        )
    operations = _api_operations(
        inputs.api_spec_text,
        submitted_source_text=_text(
            inputs.campaign_context.get("_source_verification_text")
        ),
    )
    runtime_interface_discovery_enabled = inputs.campaign_context.get(
        "runtime_interface_discovery_enabled",
        False,
    )
    if not isinstance(runtime_interface_discovery_enabled, bool):
        raise MainlineContractError(
            "runtime_interface_discovery_enabled_not_boolean"
        )
    runtime_interface_discovery_plan: dict[str, Any] = {}
    if runtime_interface_discovery_enabled:
        configured_budget = inputs.campaign_context.get(
            "runtime_interface_discovery_budget"
        )
        budget_value = (
            load_runtime_interface_discovery_budget()
            if configured_budget is None
            else configured_budget
        )
        if (
            isinstance(budget_value, bool)
            or not isinstance(budget_value, int)
            or not 1 <= budget_value <= 5000
        ):
            raise MainlineContractError(
                "runtime_interface_discovery_budget_invalid"
            )
        runtime_interface_discovery_plan = plan_runtime_interface_candidates(
            operations,
            action_markers=None,
            max_candidates=budget_value,
        )
    runtime_actors = _runtime_actors(
        inputs.root,
        inputs.project,
        inputs.campaign_context,
    )

    # ── Source-contract auto-derivation (four-link breadth closure) ──
    # Detect explicit latency / stability / event contracts that already exist
    # in the source material (operation descriptions, PRD/API text) and bind
    # them verbatim into the asset, so performance/stability/event defect
    # classes become reachable without a manually declared JSON contract.
    # Derivation is extraction only: every contract is anchored to an exact
    # verbatim statement and an exact operation + actor identity; anything
    # unprovable is skipped with a receipt entry.  Never invents budgets,
    # fields, or claims the source does not state.  Execution still requires
    # the operator-declared adapter and approved runtime contract.
    contract_derivation_receipt: dict[str, Any] = {
        "schema_version": "qualibug.contract-auto-derivation.v1",
        "status": "SKIPPED",
        "reason": "derivation_not_run",
    }
    try:
        from .contract_auto_derivation import derive_source_contracts

        asset, contract_derivation_receipt = derive_source_contracts(
            asset,
            prd_text=inputs.prd_text,
            api_spec_text=_text(
                inputs.campaign_context.get("_source_verification_text")
            ) or inputs.api_spec_text,
            operations=operations,
            runtime_actors=runtime_actors,
        )
    except Exception as exc:
        contract_derivation_receipt = {
            "schema_version": "qualibug.contract-auto-derivation.v1",
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
        }
    # Resolve adapter capability BEFORE the IR is built, so the IR's observation
    # surfaces and the experiment compiler's adapter set come from one computation.
    # They used to disagree: the IR hardcoded db_snapshot as unavailable while this
    # same resolver returned db_sql for a project with a declared database, and the
    # observer gate reads the IR -- so data-layer assertions blocked as
    # BLOCKED_MISSING_OBSERVER against a database the product could query.
    from .adapter_capability import (
        observation_surfaces_for_adapters,
        resolve_available_adapters,
    )

    _available_adapters = resolve_available_adapters(
        inputs.root,
        inputs.project,
        _dict(inputs.campaign_context.get("_runtime_contract")),
    )

    # ── Adapter-declared observation surfaces ──
    # A customer-declared database turns the db_sql adapter on. The persistence
    # surface (observer + assertion kinds + risk family) is installed HERE, on the
    # planning authority, so the obligation compiler, the observer gate and the
    # executor answer one question: an entity with a source-declared table compiles
    # into a persistence observation only when the surface is actually installed.
    # Idempotent; registers without opening any connection.
    adapter_surface_install_receipt = {
        "schema_version": "qualibug.adapter-surface-install.v1",
        "status": "NOT_REQUESTED",
        "adapters": sorted(_available_adapters),
        "installed": {},
    }
    if "db_sql" in _available_adapters:
        from .persistence_assertions import install_persistence_surface

        adapter_surface_install_receipt.update({
            "status": "INSTALLED",
            "installed": install_persistence_surface(),
        })

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id=inputs.project,
        source_snapshot_hash=_text(
            _dict(inputs.campaign_context.get("source_manifest")).get("source_hash")
        ),
        api_operations=operations,
        runtime_actors=runtime_actors,
        available_surfaces=observation_surfaces_for_adapters(_available_adapters),
    )
    invariants = [
        row
        for row in _list(behavior_ir.get("invariants"))
        if isinstance(row, dict)
    ]
    invariant_binding_receipt = {
        "schema_version": "qualibug.invariant-operation-binding.v2",
        "status": "SOURCE_IDENTITY_ONLY",
        "heuristic_binding_enabled": False,
        "explicitly_bound_count": sum(
            1 for row in invariants if _list(row.get("operation_refs"))
        ),
        "unbound_count": sum(
            1 for row in invariants if not _list(row.get("operation_refs"))
        ),
        "semantic_linking_enabled": semantic_linking_enabled,
        "reason_code": "EXACT_SOURCE_OR_AGENT_SEMANTIC_IDENTITY_REQUIRED",
    }

    behavior_ir_input_receipt = {
        "schema_version": "qualibug.behavior-ir-input-receipt.v1",
        "knowledge_asset_id": _text(asset.get("asset_id")),
        "runtime_source_overlay": dict(
            _dict(asset.get("runtime_source_overlay"))
        ),
        "api_operation_count": len(operations),
        "runtime_actor_count": len(runtime_actors),
        "ui_source_spec_count": len(_list(asset.get("ui_design_specs"))),
        "runtime_interface_discovery_enabled": runtime_interface_discovery_enabled,
        "invariant_operation_binding": invariant_binding_receipt,
    }

    # ── Binding Closure: construct unified binding ledger from Behavior IR ──
    from .binding_ledger import BindingLedger
    from .binding_builder import build_all_bindings
    from .binding_conflict_resolver import detect_and_resolve_all

    _binding_ledger = BindingLedger(project_id=inputs.project)
    _binding_build_receipt = build_all_bindings(behavior_ir, _binding_ledger)
    _binding_conflict_receipt = detect_and_resolve_all(
        _binding_ledger, strategy="evidence_priority"
    )

    obligation_pack = compile_obligations_from_behavior_ir(
        behavior_ir,
        root=str(inputs.root),
        project=inputs.project,
    )
    obligations = [
        dict(row)
        for row in _list(obligation_pack.get("obligations"))
        if isinstance(row, dict)
    ]

    # ── Behavior IR coverage gap → source-backed obligations ──
    # Augment obligations with coverage-driven obligations for Behavior IR
    # nodes that have zero existing obligation coverage. This is a single-variable
    # optimization: it only adds obligations, does not change compilation,
    # execution, oracle, or evaluation.
    coverage_report: dict[str, Any] = {}
    try:
        from .behavior_ir_hypothesis_coverage import (
            compute_obligation_coverage_gaps,
            build_source_backed_coverage_obligations,
        )

        coverage_gaps = compute_obligation_coverage_gaps(behavior_ir, obligations)
        coverage_obligations = build_source_backed_coverage_obligations(
            behavior_ir,
            coverage_gaps,
        )
        if coverage_obligations:
            obligations.extend(coverage_obligations)
        coverage_report = {
            "coverage_obligations_added": len(coverage_obligations),
            "total_obligations_after_coverage": len(obligations),
            "coverage_gap": {
                "total_nodes": coverage_gaps.get("total_count", 0),
                "covered": coverage_gaps.get("covered_count", 0),
                "uncovered": coverage_gaps.get("uncovered_count", 0),
                "coverage_rate": coverage_gaps.get("coverage_rate"),
                "uncovered_by_family": coverage_gaps.get("uncovered_by_family", {}),
            },
        }
    except Exception as exc:
        # Coverage enrichment is a progressive enhancement; its failure must not
        # abort the mainline. Log and continue with original obligations.
        coverage_report = {
            "coverage_obligations_added": 0,
            "coverage_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    # ── Exhaustive obligation matrix (Phase 4.1) ──
    # Generate comprehensive obligations from Behavior IR structure:
    # auth matrix, boundary validation, state integrity, isolation,
    # idempotency, conservation, invariant checks.
    matrix_report: dict[str, Any] = {}

    # ── Read-only state audit obligations (Phase 3-A) ──
    # For invariants with no operation binding, map entity type to GET
    # endpoint and generate read-only audit obligations. Safe: no writes.
    state_audit_report: dict[str, Any] = {}
    try:
        from .state_audit_planner import build_readonly_state_audit_obligations

        audit_obligations = build_readonly_state_audit_obligations(behavior_ir)
        if audit_obligations:
            existing_sigs = {
                _text(o.get("obligation_id")) for o in obligations if isinstance(o, dict)
            }
            new_audit = [
                ao for ao in audit_obligations
                if _text(ao.get("obligation_id")) not in existing_sigs
            ]
            obligations.extend(new_audit)
            state_audit_report = {
                "state_audit_obligations_generated": len(audit_obligations),
                "state_audit_obligations_added": len(new_audit),
                "total_obligations_after_state_audit": len(obligations),
            }
    except Exception as exc:
        state_audit_report = {
            "state_audit_obligations_added": 0,
            "state_audit_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    # ── Account-enumeration guard obligations (anonymous identity queries) ──
    # For identity-locator GET/HEAD operations with no declared permit/deny
    # relation (anonymous-reachable by definition), generate single-arm
    # privacy guard obligations: the anonymous response must not carry account
    # attributes. Read-only by construction; flows through the existing
    # response-side privacy field-policy channel.
    account_enumeration_report: dict[str, Any] = {}
    try:
        from .account_enumeration_guard import (
            build_account_enumeration_guard_obligations,
        )

        guard_obligations = build_account_enumeration_guard_obligations(behavior_ir)
        if guard_obligations:
            existing_sigs = {
                _text(o.get("obligation_id")) for o in obligations if isinstance(o, dict)
            }
            new_guards = [
                go for go in guard_obligations
                if _text(go.get("obligation_id")) not in existing_sigs
            ]
            obligations.extend(new_guards)
            account_enumeration_report = {
                "account_enumeration_obligations_generated": len(guard_obligations),
                "account_enumeration_obligations_added": len(new_guards),
                "total_obligations_after_account_enumeration_guard": len(obligations),
            }
    except Exception as exc:
        account_enumeration_report = {
            "account_enumeration_obligations_added": 0,
            "account_enumeration_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    # ── Credential-gated write guard obligations (unauthenticated writes) ──
    # For WRITE operations whose own contract demands verification-based
    # authentication (回调必须验签 / 必须完成验证码或等价身份校验) but declares
    # no permit/deny relation (anonymous-reachable by definition), generate
    # single-arm authorization guard obligations: the anonymous write must be
    # rejected. The treatment body aims the identity-locator field at a real
    # runtime account and a callback surface's status field carries the
    # success literal — the forged state-change shape. Rejection-only by
    # construction; flows through the authorization protocol channel with the
    # dedicated credential_gated_write template.
    credential_gated_write_report: dict[str, Any] = {}
    try:
        from .credential_gated_write_guard import (
            build_credential_gated_write_guard_obligations,
        )

        credential_guards = build_credential_gated_write_guard_obligations(behavior_ir)
        if credential_guards:
            existing_sigs = {
                _text(o.get("obligation_id"))
                for o in obligations
                if isinstance(o, dict)
            }
            new_credential_guards = [
                go
                for go in credential_guards
                if _text(go.get("obligation_id")) not in existing_sigs
            ]
            obligations.extend(new_credential_guards)
            credential_gated_write_report = {
                "credential_gated_write_obligations_generated": len(credential_guards),
                "credential_gated_write_obligations_added": len(new_credential_guards),
                "total_obligations_after_credential_gated_write_guard": len(obligations),
            }
    except Exception as exc:
        credential_gated_write_report = {
            "credential_gated_write_obligations_added": 0,
            "credential_gated_write_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    # ── Credential-boundary guard obligations (auth/backdoor surfaces) ──
    # Authentication/backdoor defects are runtime divergences between an
    # endpoint's DECLARED access contract and its observed enforcement: the
    # source states the correct contract (权限：管理员 / 必须完成验证码校验), so
    # no rule→operation binding can surface them. Two structure-only arms:
    #  - Arm A: a role-declared credential surface (token/password/login/
    #    impersonate/debug-sign …) must REJECT anonymous callers — an accepted
    #    anonymous call lets anyone mint/impersonate/change credentials.
    #  - Arm B: a declared verification-code login must actually verify the
    #    code — a wrong code must be rejected (any-code-login weakness).
    # Both are rejection-only single-arm obligations flowing through the
    # existing credential_gated_write / validation protocol channels; no
    # endpoint names, no industry terms (driven by the operation's own
    # contract text + generic security vocabulary).
    credential_boundary_report: dict[str, Any] = {}
    try:
        from .credential_boundary_guard import (
            build_credential_boundary_guard_obligations,
        )

        boundary_guards = build_credential_boundary_guard_obligations(behavior_ir)
        if boundary_guards:
            existing_sigs = {
                _text(o.get("obligation_id"))
                for o in obligations
                if isinstance(o, dict)
            }
            new_boundary_guards = [
                go
                for go in boundary_guards
                if _text(go.get("obligation_id")) not in existing_sigs
            ]
            obligations.extend(new_boundary_guards)
            credential_boundary_report = {
                "credential_boundary_obligations_generated": len(boundary_guards),
                "credential_boundary_obligations_added": len(new_boundary_guards),
                "total_obligations_after_credential_boundary_guard": len(obligations),
            }
    except Exception as exc:
        credential_boundary_report = {
            "credential_boundary_obligations_added": 0,
            "credential_boundary_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    # ── Cross-document conflict obligations ──
    # Consume conflicts detected by enterprise_knowledge_center between
    # different source documents. Each conflict becomes a test obligation
    # targeting the contradiction (e.g. field required in PRD but nullable
    # in DB schema → test that the API enforces the declared constraint).
    conflict_report: dict[str, Any] = {}
    try:
        from .behavior_ir_hypothesis_coverage import _stable_id as _cov_stable_id

        cross_conflicts = _list(asset.get("cross_document_conflicts"))
        conflict_obligations: list[dict[str, Any]] = []
        for conflict in cross_conflicts:
            if not isinstance(conflict, dict):
                continue
            conflict_type = _text(conflict.get("conflict_type"))
            entity = _text(conflict.get("entity"))
            if not conflict_type or not entity:
                continue
            # Map conflict type to risk family for obligation routing
            risk_family_map = {
                "field_required_mismatch": "consistency",
                "permission_contradiction": "authorization",
                # Never emit bare compile-family ``invariant`` — that path has no
                # assertion kind and dies as invariant_assertion_kind_missing.
                "rule_contradiction": "validation",
            }
            risk_family = risk_family_map.get(conflict_type, "consistency")
            obl_id = _cov_stable_id(
                "obl", "cross_doc_conflict", conflict_type, entity,
            )
            from .test_obligation import make_obligation as _make_conflict_obligation

            conflict_obligations.append(
                _make_conflict_obligation(
                    risk_family=risk_family,
                    subject_refs=[entity, conflict_type],
                    property_spec={
                        "template": "cross_document_conflict",
                        "conflict_type": conflict_type,
                        "entity": entity,
                        "hypothesis": (
                            f"Cross-document conflict ({conflict_type}): "
                            f"{conflict.get('detail', entity)}"
                        ),
                        "_strategy": "cross_document_conflict",
                    },
                    required_actors=[],
                    required_operations=[],
                    required_observers=["http_response"],
                    cleanup_requirement={"required": False},
                    source_refs=[
                        {
                            "source_id": _text(conflict.get("source_a")) or "unknown",
                            "locator": entity,
                            "kind": "cross_document_conflict",
                        },
                        {
                            "source_id": _text(conflict.get("source_b")) or "unknown",
                            "locator": entity,
                            "kind": "cross_document_conflict",
                        },
                    ],
                    confidence=0.4,
                    obligation_id=obl_id,
                )
            )
            conflict_obligations[-1]["derivation"] = "cross_document_conflict"
            conflict_obligations[-1]["conflict_type"] = conflict_type
            conflict_obligations[-1]["entity"] = entity
        if conflict_obligations:
            existing_sigs = {
                _text(o.get("obligation_id")) for o in obligations if isinstance(o, dict)
            }
            new_conflicts = [
                co for co in conflict_obligations
                if _text(co.get("obligation_id")) not in existing_sigs
            ]
            obligations.extend(new_conflicts)
            conflict_report = {
                "conflict_obligations_generated": len(conflict_obligations),
                "conflict_obligations_added": len(new_conflicts),
                "total_obligations_after_conflicts": len(obligations),
            }
    except Exception as exc:
        conflict_report = {
            "conflict_obligations_added": 0,
            "conflict_error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    # ── Mainline LLM Reasoner augmentation ──
    # Comprehension is the measured bottleneck of the discovery harness: the
    # deterministic obligation compiler only expresses what the Behavior IR
    # already encodes. The 11-engine Reasoner (stage_reason_all_v2) was only
    # reachable from side loops, so the mainline never consumed LLM business
    # reasoning and discovery breadth was structurally capped.
    #
    # Wiring contract: hypotheses NEVER become obligations directly. They must
    # bind to a documented endpoint and join the Behavior IR through exact
    # source-declared relations (hypothesis_slice_bridge +
    # obligation_source_adapter). Unbound hypotheses are dropped and counted
    # in the receipt, so this path adds comprehension without weakening any
    # source-grounding, fail-closed, or evidence rule.
    mainline_reasoner_report: dict[str, Any] = {
        "schema_version": "qualibug.mainline-reasoner-receipt.v1",
        "status": "NOT_REQUESTED",
        "hypotheses_generated": 0,
        "obligations_added": 0,
    }
    mainline_reasoner_enabled = inputs.campaign_context.get(
        "mainline_reasoner_enabled",
        True,
    )
    if not isinstance(mainline_reasoner_enabled, bool):
        raise MainlineContractError("mainline_reasoner_enabled_not_boolean")
    if (
        mainline_reasoner_enabled
        and not str(
            os.environ.get("QUALIBUG_MAINLINE_REASONER_DISABLED") or ""
        ).strip().lower()
        in {"1", "true", "yes"}
    ):
        try:
            from .stage_reason_all_v2 import collect_reasoner_hypotheses

            _reasoner_api_text = _text(
                inputs.campaign_context.get("_source_verification_text")
            ) or inputs.api_spec_text
            _reasoner_start = time.perf_counter()
            # Comprehension bridge: project the source-derived knowledge asset
            # into the Reasoner's business-world contract.  Without this the
            # 11 engines see only truncated raw PRD/API text and every
            # business_world prompt slot degrades to ``{}``: a directly
            # observable code-path break independent of historical scores.
            _reasoner_world = project_knowledge_world_model(asset)
            _reasoner_hypotheses, _reasoner_meta = collect_reasoner_hypotheses(
                inputs.prd_text,
                _reasoner_api_text,
                reader_output=_reasoner_world,
                project_id=inputs.project,
                root=inputs.root,
            )
            _reasoner_world_model_report = {
                "entities": len(_reasoner_world.get("entities") or []),
                "documented_rules": len(_reasoner_world.get("documented_rules") or []),
                "semantic_hypotheses": len(
                    _reasoner_world.get("semantic_hypotheses") or []
                ),
                "state_machines": len(_reasoner_world.get("state_machines") or []),
                "roles": len(_reasoner_world.get("roles") or []),
                "relationships": len(_reasoner_world.get("relationships") or []),
                "permissions": sum(
                    len(row.get("permissions") or [])
                    for row in (_reasoner_world.get("roles") or [])
                    if isinstance(row, dict)
                ),
                "contradictions": len(_reasoner_world.get("contradictions") or []),
                "gaps": len(_reasoner_world.get("gaps") or []),
                # Comprehension-bridge truncation must stay visible: the
                # projection receipt records budgets + projected-vs-total
                # counts + named reason codes, so a rule set larger than the
                # bridge budget is never silently dropped from the scan receipt.
                "projection_receipt": _reasoner_world.get("projection_receipt") or {},
            }
            mainline_reasoner_report = {
                "schema_version": "qualibug.mainline-reasoner-receipt.v1",
                "status": _text(_reasoner_meta.get("status")) or "empty",
                "hypotheses_generated": len(_reasoner_hypotheses),
                "provider_meta": {
                    key: _reasoner_meta.get(key)
                    for key in (
                        "reason",
                        "error_class",
                        "error_code",
                        "total_engines",
                        "successful_engine_count",
                        "failed_engine_count",
                        "failed_engine_names",
                        "engine_error_class_counts",
                        "max_hypotheses_per_engine",
                        # Learning-loop observability (closed-loop consumption
                        # state must be visible in the scan receipt, not
                        # dropped at the reasoner boundary).
                        "learned_memory_receipt",
                        "engine_attention_receipt",
                        "fact_retrieval_receipt",
                        "semantic_dedup_receipt",
                        "graph_context",
                    )
                    if key in _reasoner_meta
                },
                "elapsed_seconds": round(
                    time.perf_counter() - _reasoner_start, 3
                ),
                "obligations_added": 0,
                "world_model": _reasoner_world_model_report,
                "bridge_funnel": {},
            }
            if _reasoner_hypotheses:
                from .hypothesis_slice_bridge import hypotheses_to_obligations

                _adapted, _bridge_funnel = hypotheses_to_obligations(
                    _reasoner_hypotheses,
                    api_endpoints=operations,
                    behavior_ir=behavior_ir,
                    origin="mainline_reasoner",
                )
                _existing_ids = {
                    _text(o.get("obligation_id"))
                    for o in obligations
                    if isinstance(o, dict)
                }
                _reasoner_obligations = []
                for _r_obl in _list(_adapted.get("obligations")):
                    if not isinstance(_r_obl, dict):
                        continue
                    _r_obl = dict(_r_obl)
                    _r_obl["derivation"] = "mainline_reasoner"
                    if (
                        _text(_r_obl.get("obligation_id"))
                        and _text(_r_obl.get("obligation_id"))
                        in _existing_ids
                    ):
                        continue
                    _reasoner_obligations.append(_r_obl)
                    _existing_ids.add(_text(_r_obl.get("obligation_id")))
                obligations.extend(_reasoner_obligations)
                mainline_reasoner_report["obligations_added"] = len(
                    _reasoner_obligations
                )
                mainline_reasoner_report["bridge_funnel"] = {
                    key: _bridge_funnel.get(key)
                    for key in (
                        "input",
                        "bound",
                        "dropped_no_endpoint",
                        "adapted_obligation_count",
                        "adapter_coverage_gap_count",
                    )
                    if key in _bridge_funnel
                }
                mainline_reasoner_report["adapter_coverage_gaps"] = [
                    dict(gap)
                    for gap in _list(_adapted.get("coverage_gaps"))[:50]
                    if isinstance(gap, dict)
                ]
                mainline_reasoner_report[
                    "total_obligations_after_reasoner"
                ] = len(obligations)
                _planning_logger.info(
                    "mainline_reasoner_augmentation status=%s hypotheses=%s "
                    "bound=%s obligations_added=%s",
                    mainline_reasoner_report["status"],
                    len(_reasoner_hypotheses),
                    mainline_reasoner_report["bridge_funnel"].get("bound"),
                    mainline_reasoner_report["obligations_added"],
                )
        except Exception as exc:
            # Degradation is never silent: the FAILED receipt travels with the
            # planning bundle into product artifacts and the error is logged
            # with a traceback. The deterministic obligation pool remains.
            _planning_logger.error(
                "mainline_reasoner_augmentation_failed %s: %s",
                type(exc).__name__,
                str(exc)[:300],
                exc_info=exc,
            )
            mainline_reasoner_report = {
                "schema_version": "qualibug.mainline-reasoner-receipt.v1",
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "obligations_added": 0,
            }

    # Obligation identity is the planning SSOT. Several enrichments above add
    # rows after the compiler's own dedupe pass; leaving those rows in place
    # creates multiple formal rows for one immutable obligation and makes every
    # downstream count appear more complete than the ledger actually is.
    raw_obligation_count = len(obligations)
    id_fingerprints: dict[str, set[str]] = {}
    missing_id_count = 0
    for obligation in obligations:
        if not isinstance(obligation, dict):
            continue
        obligation_id = _text(obligation.get("obligation_id"))
        if not obligation_id:
            missing_id_count += 1
            continue
        id_fingerprints.setdefault(obligation_id, set()).add(
            json_fingerprint(obligation)
        )
    conflicting_duplicate_ids = sorted(
        obligation_id
        for obligation_id, fingerprints in id_fingerprints.items()
        if len(fingerprints) > 1
    )
    if conflicting_duplicate_ids:
        _planning_logger.error(
            "Conflicting duplicate obligation identities detected: %s",
            conflicting_duplicate_ids[:20],
            extra={
                "conflicting_duplicate_count": len(conflicting_duplicate_ids),
                "raw_obligation_count": raw_obligation_count,
            },
        )
        raise MainlineContractError(
            "obligation_identity_conflict:"
            + ",".join(conflicting_duplicate_ids[:20])
        )
    id_counts = Counter(
        _text(obligation.get("obligation_id"))
        for obligation in obligations
        if isinstance(obligation, dict)
        and _text(obligation.get("obligation_id"))
    )
    duplicate_ids = sorted(
        obligation_id
        for obligation_id, count in id_counts.items()
        if count > 1
    )
    obligations = dedupe_obligations(obligations)
    _obligation_identity_receipt = {
        "schema_version": "qualibug.obligation-identity-receipt.v1",
        "authority": "ai_test_asset_center.test_obligation.dedupe_obligations",
        "status": (
            "FAILED_SAFE"
            if missing_id_count
            else "DEDUPLICATED"
            if duplicate_ids
            else "PASS"
        ),
        "input_row_count": raw_obligation_count,
        "unique_count": len(obligations),
        "duplicate_count": raw_obligation_count - len(obligations),
        "duplicate_ids": duplicate_ids[:50],
        "missing_id_count": missing_id_count,
        "conflicting_duplicate_ids": [],
    }
    if duplicate_ids:
        _planning_logger.warning(
            "Deduplicated repeated obligation identities before experiment compilation",
            extra={
                "duplicate_count": raw_obligation_count - len(obligations),
                "duplicate_ids": duplicate_ids[:50],
            },
        )

    # ── Source-confidence gate ──
    # Obligations derived from low-confidence sources (e.g. OCR-parsed
    # documents with confidence < 0.5) are downweighted: they remain in the
    # pool but are pushed to the bottom of execution priority. This prevents
    # noisy OCR artifacts from consuming execution budget ahead of
    # high-confidence electronic-source obligations.
    _LOW_CONFIDENCE_THRESHOLD = 0.5
    _low_conf_count = 0
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        # Check source_refs for confidence signals
        source_refs = obl.get("source_refs") or []
        min_conf = 1.0
        for ref in source_refs:
            if isinstance(ref, dict):
                ref_conf = ref.get("confidence")
                if ref_conf is not None:
                    min_conf = min(min_conf, float(ref_conf))
        # Also check obligation-level confidence (set by obligation compiler
        # from IR node confidence: invariant × operation × actor)
        obl_conf = obl.get("confidence")
        if obl_conf is not None:
            min_conf = min(min_conf, float(obl_conf))
        # Also check obligation-level source_confidence
        src_conf = obl.get("source_confidence")
        if src_conf is not None:
            min_conf = min(min_conf, float(src_conf))
        if min_conf < _LOW_CONFIDENCE_THRESHOLD:
            # Mark as low-confidence so the planner deprioritizes it
            obl["_low_confidence_source"] = True
            obl["_source_confidence"] = min_conf
            _low_conf_count += 1
        else:
            obl["_source_confidence"] = min_conf
    # Sort: high-confidence obligations first, low-confidence last
    if _low_conf_count > 0:
        obligations.sort(
            key=lambda o: (1 if isinstance(o, dict) and o.get("_low_confidence_source") else 0)
        )

    # ── P0-1: Canonical obligation keys + Coverage Units ──
    # Semantic uniquification starts at the obligation layer: every obligation
    # (compiler output, coverage/reasoner/audit/guard/conflict augmentations)
    # carries its canonical key + coverage unit id, and the pool is merged into
    # Coverage Units (SPEC §2.1/§2.2). The planner below then budgets by unit
    # (defect surface) instead of by variant, and each selected unit compiles
    # once into a multi-arm experiment bundle (SPEC §2.3). Fail-closed: any
    # failure keeps the existing obligation-variant path with a visible receipt.
    coverage_unit_receipt: dict[str, Any] = {
        "schema_version": "qualibug.coverage-unit-registry.v1",
        "status": "NOT_APPLIED",
        "reason": "unit_build_skipped",
    }
    units: list[dict[str, Any]] = []
    try:
        from .coverage_unit_registry import (
            attach_canonical_obligation_keys,
            build_coverage_units,
        )

        obligations = attach_canonical_obligation_keys(
            obligations, behavior_ir=behavior_ir
        )
        unit_pack = build_coverage_units(obligations, behavior_ir=behavior_ir)
        units = [
            dict(row)
            for row in _list(unit_pack.get("coverage_units"))
            if isinstance(row, dict)
        ]
        coverage_unit_receipt = {
            "schema_version": _text(unit_pack.get("schema_version")),
            "status": "APPLIED",
            "obligation_count": int(unit_pack.get("obligation_count") or 0),
            "unit_count": int(unit_pack.get("unit_count") or 0),
            "collapsed_variant_count": int(unit_pack.get("collapsed_variant_count") or 0),
            "average_variants_per_unit": unit_pack.get("average_variants_per_unit"),
            "max_variants_per_unit": int(unit_pack.get("max_variants_per_unit") or 0),
        }
        _planning_logger.info(
            "coverage_units_built units=%s obligations=%s collapsed=%s",
            coverage_unit_receipt["unit_count"],
            coverage_unit_receipt["obligation_count"],
            coverage_unit_receipt["collapsed_variant_count"],
        )
    except Exception as exc:
        _planning_logger.error(
            "coverage_unit_build_failed %s: %s",
            type(exc).__name__,
            str(exc)[:300],
            exc_info=exc,
        )
        coverage_unit_receipt = {
            "schema_version": "qualibug.coverage-unit-registry.v1",
            "status": "FAILED",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    obligations_by_id_for_units: dict[str, dict[str, Any]] = {}
    unit_by_obligation_id: dict[str, dict[str, Any]] = {}
    for unit in units:
        for oid in _list(unit.get("obligation_ids")):
            unit_by_obligation_id.setdefault(_text(oid), unit)
    for obl in obligations:
        if isinstance(obl, dict) and _text(obl.get("obligation_id")):
            obligations_by_id_for_units[_text(obl.get("obligation_id"))] = obl
    representative_obligations: list[dict[str, Any]] = []
    for unit in units:
        rep_id = _text(unit.get("representative_obligation_id"))
        rep = obligations_by_id_for_units.get(rep_id)
        if rep is not None:
            representative_obligations.append(rep)
    planning_authority = "coverage_unit" if units else "obligation"

    # ── Space Coordinate Annotation + Exploration Infrastructure ──
    from .space_coordinate import coordinate_from_obligation
    from .invariant_graph import build_default_invariant_graph
    from .exploration_operator_registry import (
        ExplorationOperatorRegistry,
        check_all_applicability,
    )
    from .combination_generator import generate_combinations

    _space_exploration_report: dict[str, Any] = {}
    try:
        for _s_obl in obligations:
            if isinstance(_s_obl, dict):
                _s_obl["space_coordinate"] = coordinate_from_obligation(
                    _s_obl, behavior_ir
                )
        _inv_graph = build_default_invariant_graph(
            behavior_ir, project_id=inputs.project
        )
        _op_registry = ExplorationOperatorRegistry(project_id=inputs.project)
        _op_registry.register_defaults()
        _applicability = check_all_applicability(
            _op_registry, behavior_ir=behavior_ir
        )
        _applicable_ops = [
            r for r in _applicability if r.get("applicable")
        ]
        _combos = generate_combinations(
            _applicable_ops,
            max_level=3,
            max_combinations=100,
            behavior_ir=behavior_ir,
        )
        from ai_test_asset_center.space_dimension_registry import SpaceDimensionRegistry

        _dim_registry = SpaceDimensionRegistry(project_id=inputs.project)
        _dim_registry.register_defaults()
        _space_exploration_report = {
            "invariant_count": _inv_graph.size,
            "operator_count": _op_registry.size,
            "applicable_operators": len(_applicable_ops),
            "combinations_generated": len(_combos) if isinstance(_combos, list) else 0,
            "dimension_count": _dim_registry.size,
            "dimension_types": _dim_registry.all_types(),
            "dimension_domains": _dim_registry.all_domains(),
        }
    except Exception as _space_exc:
        _planning_logger.warning(
            "Space exploration enrichment failed: %s: %s",
            type(_space_exc).__name__, str(_space_exc)[:300],
            exc_info=_space_exc,
        )
        _space_exploration_report = {
            "error": f"{type(_space_exc).__name__}: {str(_space_exc)[:200]}",
        }

    environment_type = _text(
        inputs.campaign_context.get("environment_type")
        or inputs.campaign_context.get("environment_kind")
    ).lower()
    # Observation adapters this target may be observed through, resolved from
    # customer-declared configuration rather than hardcoded. Every call site previously
    # pinned {"http_api"}, so a registered non-http observer could never be used on the
    # main chain -- it compiled only in a test that passed the wider set by hand. The
    # resolver adds an adapter only when the customer declared the thing it observes and
    # falls back to the http_api baseline otherwise, so the failure direction is always
    # "fewer adapters".
    # Reuse the set resolved before the IR build rather than recomputing it. Two
    # computations of the same declaration can drift, and this pair drifting is exactly
    # what made the IR and the compiler disagree about the database.
    #
    # The runtime contract lives under campaign_context["_runtime_contract"], not as an
    # attribute on inputs -- reading it as an attribute silently yielded None and made the
    # contract-declared adapter path dead.
    _runtime_contract_for_materialization = _dict(
        inputs.campaign_context.get("_runtime_contract")
    )
    # ── P0-1: compile once per Coverage Unit (representative obligation) ──
    # The full obligation pool is 5001+ rows whose variants differ mainly by
    # actor/source-rule/input; compiling every variant multiplies the compile
    # phase ~Nx (run16: 28344 compiled experiments from 5001 obligations).
    # Compiling the unit representative once preserves every semantic surface
    # (the representative's own input variants still expand inside the batch);
    # the other variants of SELECTED units become cheap actor-rebinding arms
    # after planning. Fail-closed: units whose representative does not compile
    # fall back to compiling all their variants individually (today's behavior
    # for that unit), so no executable variant is ever lost.
    compile_input = representative_obligations if representative_obligations else obligations
    experiment_pack = compile_experiments(
        compile_input,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=_text(inputs.campaign_context.get("policy_version")),
        available_adapters=_available_adapters,
        planning_context={
            "root": inputs.root,
            "project": inputs.project,
            "base_url": _text(
                _runtime_contract_for_materialization.get("approved_base_url")
                or inputs.approved_base_url
            ),
            "campaign_id": _text(inputs.campaign_context.get("campaign_id")),
            "available_adapters": _available_adapters,
            "environment_type": environment_type,
            "runtime_contract": _runtime_contract_for_materialization,
        },
    )
    representative_compile_receipt = {
        "status": "APPLIED",
        "compile_input_count": len(compile_input),
        "obligation_pool_count": len(obligations),
        "unit_count": len(units),
        "compiled_count": int(experiment_pack.get("compiled_count") or 0),
        "blocked_count": int(experiment_pack.get("blocked_count") or 0),
        "abstract_count": int(experiment_pack.get("abstract_count") or 0),
    }
    # ── Fail-closed fallback: units whose representative did not compile ──
    fallback_variants: list[dict[str, Any]] = []
    for unit in units:
        rep_id = _text(unit.get("representative_obligation_id"))
        rep = obligations_by_id_for_units.get(rep_id) or {}
        if _text(rep.get("compile_status")).upper() == "COMPILED":
            continue
        for oid in _list(unit.get("obligation_ids")):
            if _text(oid) == rep_id:
                continue
            variant = obligations_by_id_for_units.get(_text(oid))
            if variant is not None:
                fallback_variants.append(variant)
    if fallback_variants:
        fallback_pack = compile_experiments(
            fallback_variants,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=_text(inputs.campaign_context.get("policy_version")),
            available_adapters=_available_adapters,
            planning_context={
                "root": inputs.root,
                "project": inputs.project,
                "base_url": _text(
                    _runtime_contract_for_materialization.get("approved_base_url")
                    or inputs.approved_base_url
                ),
                "campaign_id": _text(inputs.campaign_context.get("campaign_id")),
                "available_adapters": _available_adapters,
                "environment_type": environment_type,
                "runtime_contract": _runtime_contract_for_materialization,
            },
        )
        for key in ("experiments", "blocked_experiments", "abstract_experiments"):
            experiment_pack[key] = list(_list(experiment_pack.get(key))) + list(
                _list(fallback_pack.get(key))
            )
        experiment_pack["compiled_count"] = int(experiment_pack.get("compiled_count") or 0) + int(
            fallback_pack.get("compiled_count") or 0
        )
        experiment_pack["blocked_count"] = int(experiment_pack.get("blocked_count") or 0) + int(
            fallback_pack.get("blocked_count") or 0
        )
        experiment_pack["abstract_count"] = int(experiment_pack.get("abstract_count") or 0) + int(
            fallback_pack.get("abstract_count") or 0
        )
        # ── Fail-closed representative promotion ──
        # A unit whose representative did not compile is still selectable when
        # one of its variants compiled: promote the highest-confidence compiled
        # variant to representative (deterministic), so the unit keeps a
        # compile-ready face and no executable variant is lost from selection.
        promoted_count = 0
        for unit in units:
            rep_id = _text(unit.get("representative_obligation_id"))
            rep = obligations_by_id_for_units.get(rep_id) or {}
            if _text(rep.get("compile_status")).upper() == "COMPILED":
                continue
            compiled_variants = [
                oid
                for oid in _list(unit.get("obligation_ids"))
                if _text(
                    _dict(obligations_by_id_for_units.get(_text(oid))).get("compile_status")
                ).upper()
                == "COMPILED"
            ]
            if not compiled_variants:
                continue
            ordered = sorted(
                compiled_variants,
                key=lambda oid: (
                    -float(obligations_by_id_for_units[oid].get("confidence") or 0.0),
                    oid,
                ),
            )
            unit["representative_obligation_id"] = ordered[0]
            unit["obligation_ids"] = [
                _text(oid)
                for oid in _list(unit.get("obligation_ids"))
                if _text(oid)
            ]
            promoted_count += 1
        representative_compile_receipt.update({
            "fallback_variant_compile_count": len(fallback_variants),
            "fallback_compiled_count": int(fallback_pack.get("compiled_count") or 0),
            "representative_promoted_count": promoted_count,
        })
        _planning_logger.info(
            "coverage_unit_compile_fallback variants=%s compiled=%s promoted=%s",
            len(fallback_variants),
            fallback_pack.get("compiled_count"),
            promoted_count,
        )
    experiment_pack = attach_fixture_dag_to_experiments(
        experiment_pack,
        behavior_ir=behavior_ir,
    )
    all_experiments = [
        dict(row)
        for row in (
            _list(experiment_pack.get("experiments"))
            + _list(experiment_pack.get("blocked_experiments"))
            + _list(experiment_pack.get("abstract_experiments"))
        )
        if isinstance(row, dict)
    ]
    by_obligation = {}
    import re as _re
    _VARIANT_RE = _re.compile(r"^(.+?)__v_[a-f0-9]+$")
    for row in all_experiments:
        if not isinstance(row, dict):
            continue
        oid = _text(row.get("obligation_id"))
        if oid:
            by_obligation[oid] = row
        # Variant experiments use IDs like obl_xxx__v_yyy; also index by original
        _expanded_from = _text(row.get("expanded_from_obligation_id"))
        if _expanded_from and _expanded_from not in by_obligation:
            by_obligation[_expanded_from] = row
        # Also parse variant ID pattern to extract original
        _vm = _VARIANT_RE.match(oid) if oid else None
        if _vm:
            _original = _vm.group(1)
            if _original not in by_obligation:
                by_obligation[_original] = row

    # ── Binding Completeness Gate: explicit pre-execution binding check ──
    from .binding_completeness_gate import gate_or_block as _binding_gate_check

    _binding_blocked_obls: list[str] = []
    _binding_gate_checked = 0
    for _g_oid, _g_obl in zip(
        [_text(o.get("obligation_id")) for o in obligations if isinstance(o, dict)],
        [o for o in obligations if isinstance(o, dict)],
    ):
        if not _g_oid:
            continue
        _g_exp = by_obligation.get(_g_oid)
        if not isinstance(_g_exp, dict):
            continue
        _g_compile = _dict(_g_exp.get("compile_receipt"))
        _g_status = _text(_g_compile.get("status")).upper()
        if _g_status == "COMPILED":
            continue  # Never downgrade already-compiled experiments
        _binding_gate_checked += 1
        _g_passed, _g_reason = _binding_gate_check(
            _binding_ledger, obligation=_g_obl, behavior_ir=behavior_ir
        )
        if not _g_passed and _g_status != "BLOCKED":
            _g_exp["compile_receipt"] = {
                **_g_compile,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": _g_reason,
            }
            _binding_blocked_obls.append(_g_oid)

    # ── B1 fix: probe_status derived from actual runtime contract state ──
    _probe_contract = _dict(inputs.campaign_context.get("_runtime_contract"))
    _probe_contract_approved = bool(_text(_probe_contract.get("approved_base_url")))
    _probe_status = (
        "PROBES_NOT_INVOKED_CONTRACT_APPROVED"
        if _probe_contract_approved
        else "PROBES_NOT_INVOKED_NO_APPROVED_CONTRACT"
    )
    _binding_closure_receipt: dict[str, Any] = {
        "schema_version": "qualibug.binding-closure-receipt.v1",
        "total_bindings": _binding_ledger.size,
        "coverage_summary": _binding_ledger.coverage_summary(),
        "build_receipt": _binding_build_receipt,
        "conflict_receipt": {
            "total_conflicts": _binding_conflict_receipt.get("total_conflicts", 0),
            "resolved": _binding_conflict_receipt.get("resolved", 0),
        },
        "gate_checked": _binding_gate_checked,
        "gate_blocked": len(_binding_blocked_obls),
        "blocked_obligations": _binding_blocked_obls[:50],
        "probe_status": _probe_status,
    }

    # ── P0-1: Pre-transport executability proof (before budget selection) ──
    # Compile status alone does not prove an experiment can reach transport:
    # binding dimensions may stay incomplete even when the compile receipt says
    # COMPILED (run27: budget slots spent on obligations whose required
    # business steps never ran because bindings could not materialize). The
    # binding completeness gate is therefore applied to COMPILED obligations as
    # an EXECUTABILITY PROOF, and budget selection may only spend slots on
    # obligations that pass it. Non-executable obligations remain in the plan
    # with an explicit reason code — never silently dropped, never occupying a
    # budget slot.
    from .binding_completeness_gate import (
        check_binding_completeness as _binding_exec_check,
    )

    _pre_transport_blocked: list[dict[str, Any]] = []
    _pre_transport_checked = 0
    for _g_oid, _g_obl in zip(
        [_text(o.get("obligation_id")) for o in obligations if isinstance(o, dict)],
        [o for o in obligations if isinstance(o, dict)],
    ):
        if not _g_oid:
            continue
        _g_exp = by_obligation.get(_g_oid)
        if not isinstance(_g_exp, dict):
            continue
        _g_compile = _dict(_g_exp.get("compile_receipt"))
        _g_status = _text(_g_compile.get("status")).upper()
        if _g_status != "COMPILED":
            continue
        _pre_transport_checked += 1
        _exec_check = _binding_exec_check(
            _binding_ledger, obligation=_g_obl, behavior_ir=behavior_ir
        )
        if _exec_check.get("gate_passed") is False:
            _blocked_dims = _list(_exec_check.get("blocked_dimensions"))
            _reason = ";".join(
                f"{_text(_dict(d).get('dimension'))}:{_text(_dict(d).get('reason'))}"
                for d in _blocked_dims[:5]
            ) or "BINDING_INCOMPLETE"
            _g_obl["pre_transport_executable"] = False
            _g_obl["pre_transport_block_reason"] = _reason
            _g_exp["pre_transport_executable"] = False
            _g_exp["pre_transport_block_reason"] = _reason
            _pre_transport_blocked.append({
                "obligation_id": _g_oid,
                "reason": _reason,
                "blocked_dimensions": [
                    {
                        "dimension": _text(_dict(d).get("dimension")),
                        "reason": _text(_dict(d).get("reason")),
                    }
                    for d in _blocked_dims
                ],
            })
    _pre_transport_receipt: dict[str, Any] = {
        "schema_version": "qualibug.pre-transport-executability.v1",
        "compiled_checked": _pre_transport_checked,
        "executable_count": _pre_transport_checked - len(_pre_transport_blocked),
        "blocked_count": len(_pre_transport_blocked),
        "blocked_obligations": _pre_transport_blocked[:100],
        "authority": "binding_completeness_gate",
        "changes_budget_selection": True,
    }

    campaign_budget = int(getattr(campaign, "slice_budget", 0) or 0)
    if campaign_budget <= 0:
        raise MainlineContractError("obligation_budget_invalid")
    compiled_pool_size = sum(
        1
        for row in by_obligation.values()
        if _text(_dict(_dict(row).get("compile_receipt")).get("status")).upper()
        == "COMPILED"
        or _text(_dict(row).get("compile_status")).upper() == "COMPILED"
    )
    # Bind the run budget from the compiled obligation pool using the same
    # auto-scaler as behavior-slice scheduling. An explicit operator env
    # override (already reflected in campaign.slice_budget) must not be raised.
    env_override = str(
        os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND") or ""
    ).strip()
    if env_override:
        budget = campaign_budget
    else:
        budget = max(campaign_budget, _auto_scale_slice_budget(compiled_pool_size))
    if budget != campaign_budget:
        campaign.slice_budget = budget
    budget_receipt = build_planning_budget_receipt(budget)
    policy_identity = {
        "policy_id": _text(inputs.campaign_context.get("policy_id")),
        "policy_version": _text(inputs.campaign_context.get("policy_version")),
        "strategy_fingerprint": _text(
            inputs.campaign_context.get("strategy_fingerprint")
        ),
    }
    history_receipt = _dict(
        inputs.campaign_context.get("adaptive_planning_history_receipt")
    )
    historical_yield, history_match_status = (
        select_matching_historical_yield(
            history_receipt,
            expected_policy_identity=policy_identity,
        )
        if history_receipt
        else ({}, "NO_MATCHING_HISTORY")
    )
    if history_match_status == "MATCHED" and not historical_yield:
        history_match_status = "MATCHED_HISTORY_HAS_NO_FAMILY_METRICS"
    # ── Closed-loop READ consumption ──
    # learned_knowledge is loaded at scan start from the SQLite knowledge base
    # and carried through campaign_context. Consume it here as a bounded ranking
    # boost for compiled obligations; it never changes budget or compile state.
    from .learning_knowledge_consumption import (
        build_learning_consumption_receipt,
        build_learned_boost_index,
    )

    _learning_boost_index = build_learned_boost_index(
        inputs.campaign_context.get("learned_knowledge")
    )
    if _learning_boost_index.get("status") == "LOAD_FAILED":
        _planning_logger.warning(
            "learned_knowledge_load_failed consumption_degraded failure=%s",
            _learning_boost_index.get("load_failure"),
        )
    # ── P0-1: plan by Coverage Unit (budget counts defect surfaces) ──
    # ``plan_coverage_unit_round`` selects units — duplicate role/source/input
    # variants of one surface consume ONE budget slot. Selected units then
    # expand their remaining variants into actor-rebinding arms (multi-arm
    # Experiment Bundle: one compile per unit, N execution arms). Fail-closed:
    # any unit-planning failure falls back to the obligation-variant planner.
    _history_receipt_ids = (
        [_text(history_receipt.get("receipt_id"))]
        if (
            history_match_status
            in {"MATCHED", "MATCHED_HISTORY_HAS_NO_FAMILY_METRICS"}
            and _text(history_receipt.get("receipt_id"))
        )
        else []
    )
    obligation_plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=behavior_ir,
        budget=budget,
        historical_yield=historical_yield,
        historical_receipt_ids=_history_receipt_ids,
        cold_start_reason=history_match_status,
        learned_boost_index=_learning_boost_index,
    )
    if planning_authority == "coverage_unit":
        try:
            from .adaptive_discovery_planner import plan_coverage_unit_round

            obligation_plan = plan_coverage_unit_round(
                units,
                obligations_by_id=obligations_by_id_for_units,
                experiments_by_obligation=by_obligation,
                behavior_ir=behavior_ir,
                budget=budget,
                historical_yield=historical_yield,
                historical_receipt_ids=_history_receipt_ids,
                cold_start_reason=history_match_status,
                learned_boost_index=_learning_boost_index,
            )
            planning_authority = "coverage_unit"
        except Exception as exc:
            _planning_logger.error(
                "coverage_unit_planning_failed falling_back_to_obligation_planning %s: %s",
                type(exc).__name__,
                str(exc)[:300],
                exc_info=exc,
            )
            planning_authority = "obligation"

    # ── P0-1: multi-arm Experiment Bundle derivation for selected units ──
    # Each selected unit compiles ONCE (representative); the remaining role
    # variants become execution arms by actor-rebinding the representative's
    # compiled experiments. Fail-closed: an arm that cannot be derived (stale
    # actor references, actor not in representative, cap exceeded) falls back
    # to a normal independent compile — no variant is ever lost. The identity
    # contract (every selected row references the compiled experiment
    # registered under its obligation_id) is enforced inside
    # ``derive_unit_execution_arms`` so the intent gate never sees a
    # spurious ``compiled_experiment_mismatch``.
    arm_receipt: dict[str, Any] = {
        "schema_version": "qualibug.coverage-unit-arm-receipt.v1",
        "status": "NOT_APPLIED",
        "reason": "obligation_planning",
    }
    if planning_authority == "coverage_unit":
        arm_receipt = derive_unit_execution_arms(
            obligation_plan=obligation_plan,
            units=units,
            obligations_by_id=obligations_by_id_for_units,
            experiment_pack=experiment_pack,
            all_experiments=all_experiments,
            by_obligation=by_obligation,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=_text(inputs.campaign_context.get("policy_version")),
            available_adapters=_available_adapters,
            planning_context={
                "root": inputs.root,
                "project": inputs.project,
                "base_url": _text(
                    _runtime_contract_for_materialization.get("approved_base_url")
                    or inputs.approved_base_url
                ),
                "campaign_id": _text(inputs.campaign_context.get("campaign_id")),
                "available_adapters": _available_adapters,
                "environment_type": environment_type,
                "runtime_contract": _runtime_contract_for_materialization,
            },
        )
    _boosted_rows = [
        {
            "obligation_id": _text(row.get("obligation_id")),
            "risk_family": _text(row.get("risk_family")),
            "boost_factor": float(
                _dict(row.get("learned_boost")).get("boost_factor") or 1.0
            ),
            "matches": _list(_dict(row.get("learned_boost")).get("matches")),
        }
        for source in (
            _list(obligation_plan.get("selected")),
            _list(obligation_plan.get("pending_next_round")),
        )
        for row in source
        if isinstance(row, dict) and _dict(row.get("learned_boost")).get("matches")
    ]
    _learning_consumption_receipt = build_learning_consumption_receipt(
        _learning_boost_index, boosted_rows=_boosted_rows
    )
    _planning_logger.info(
        "learned_knowledge_consumed status=%s patterns=%s obligations_boosted=%s",
        _learning_consumption_receipt.get("status"),
        _learning_consumption_receipt.get("pattern_count"),
        _learning_consumption_receipt.get("obligations_boosted"),
    )

    # ── Binding-experience READ: reorder resolver candidates by verified
    # prior success (execution-changing surface of the learning loop).
    # Additive only: reorders an experiment's existing source-declared
    # resolver list; never adds sources, never changes binding status,
    # budgets, gates, or compile state.
    try:
        from .binding_experience_learning import apply_binding_experience_reorder

        _binding_reorder_receipt = apply_binding_experience_reorder(
            by_obligation,
            inputs.campaign_context.get("learned_knowledge"),
        )
    except Exception as exc:
        _binding_reorder_receipt = {
            "schema_version": "qualibug.binding-experience-read.v1",
            "status": "FAILED",
            "load_failure": f"{type(exc).__name__}:{str(exc)[:120]}",
            "reordered_count": 0,
            "plans_scanned": 0,
            "authority": "resolver_priority_reorder_only_no_new_sources",
        }

    # ── Coverage-Guided Reorder (within selected set only, no budget change) ──
    from .coverage_guided_scheduler import CoverageGuidedScheduler

    _reorder_receipt: dict[str, Any] = {"reordered": False}
    try:
        _selected_rows = _list(obligation_plan.get("selected"))
        if len(_selected_rows) > 1:
            _cov_scheduler = CoverageGuidedScheduler(
                project_id=inputs.project, budget=budget
            )
            _reorder_candidates = [
                {
                    "obligation_id": _text(r.get("obligation_id")),
                    "operators": [],
                    "categories": [_text(r.get("risk_family"))],
                    "priority_score": float(r.get("priority_score") or 0.5),
                }
                for r in _selected_rows
                if isinstance(r, dict)
            ]
            _reorder_result = _cov_scheduler.select_next_batch(
                _reorder_candidates, batch_size=len(_reorder_candidates)
            )
            _reordered_ids = [
                _text(e.get("obligation_id"))
                for e in _list(_reorder_result.get("selected_experiments"))
                if isinstance(e, dict)
            ]
            if _reordered_ids and _reordered_ids != [
                _text(r.get("obligation_id")) for r in _selected_rows
            ]:
                _row_map = {
                    _text(r.get("obligation_id")): r
                    for r in _selected_rows
                    if isinstance(r, dict)
                }
                obligation_plan["selected"] = [
                    _row_map[rid] for rid in _reordered_ids if rid in _row_map
                ]
                _reorder_receipt = {
                    "reordered": True,
                    "original_count": len(_selected_rows),
                    "reordered_count": len(_reordered_ids),
                }
    except Exception as _reorder_exc:
        _planning_logger.warning(
            "Coverage-guided reorder failed: %s: %s",
            type(_reorder_exc).__name__, str(_reorder_exc)[:300],
            exc_info=_reorder_exc,
        )
        _reorder_receipt = {
            "reordered": False,
            "error": f"{type(_reorder_exc).__name__}: {str(_reorder_exc)[:200]}",
        }

    budget_receipt = finalize_planning_budget_receipt(
        budget_receipt,
        consumed_budget=int(obligation_plan.get("selected_count") or 0),
        stop_condition=_text(obligation_plan.get("stop_condition")),
    )
    # ── P0-3: Environment preflight ──
    _runtime_contract = dict(_dict(inputs.campaign_context.get("_runtime_contract")))
    _preflight_base_url = _text(_runtime_contract.get("approved_base_url"))
    preflight_receipt = run_environment_preflight(
        root=inputs.root,
        project=inputs.project,
        base_url=_preflight_base_url,
        obligation_plan=obligation_plan,
        behavior_ir=behavior_ir,
        runtime_contract=_runtime_contract,
    )
    agent_intent_plan = build_agent_intent_plan(
        obligation_plan,
        obligations=obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=behavior_ir,
    )
    # Fact lineage is produced in the mainline: invariants carry the exact
    # fact identity at IR construction (rule.semantic_contract.fact_id) and
    # obligations inherit it inside the obligation compiler. This attach is the
    # authoritative verification pass — it rebuilds refs from the exact
    # structural chain (now including the production channel), records
    # first-loss diagnostics for every accepted fact, and never widens
    # lineage. It runs after selection so it cannot alter decisions; the
    # production-side refs are what compile/selection/execution see.
    from .fact_first_loss_ledger import attach_fact_refs_to_planning_artifacts

    _fact_exp_ledger = _dict(asset.get("fact_experimentability_ledger"))
    _fact_ref_attach_receipt = attach_fact_refs_to_planning_artifacts(
        obligations=obligations,
        experiments=all_experiments,
        fact_experimentability_ledger=_fact_exp_ledger,
        behavior_ir=behavior_ir,
        knowledge_asset=asset,
    )
    # ── Unified LLM comprehension authority (observation layer) ──
    # The three comprehension channels (semantic-link binding, semantic rule
    # recall, mainline reasoner) each emit their own receipt. Fold them into one
    # ``qualibug.llm-comprehension-authority.v1`` record so the whole
    # comprehension funnel is observable in one place. Pure aggregation over
    # already-produced receipts; never calls the LLM or mutates the asset.
    comprehension_authority_receipt: dict[str, Any] = {
        "schema_version": "qualibug.llm-comprehension-authority.v1",
        "status": "NOT_BUILT",
    }
    try:
        from .llm_comprehension_authority import (
            build_comprehension_authority_receipt,
        )

        comprehension_authority_receipt = build_comprehension_authority_receipt(
            knowledge_asset=asset,
            semantic_link_receipt=agent_semantic_link_receipt,
            mainline_reasoner_report=mainline_reasoner_report,
        )
    except Exception as exc:
        comprehension_authority_receipt = {
            "schema_version": "qualibug.llm-comprehension-authority.v1",
            "status": "FAILED",
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    return DiscoveryPlanningBundle(
        mainline_run=contract,
        behavior_ir=behavior_ir,
        obligations={
            **obligation_pack,
            "obligations": obligations,
            "obligation_identity_receipt": _obligation_identity_receipt,
            "behavior_ir_coverage_report": coverage_report,
            "state_audit_report": state_audit_report,
            "account_enumeration_report": account_enumeration_report,
            "credential_gated_write_report": credential_gated_write_report,
            "credential_boundary_report": credential_boundary_report,
            "conflict_report": conflict_report,
            "mainline_reasoner_report": mainline_reasoner_report,
            "fact_ref_attach_receipt": _fact_ref_attach_receipt,
            "adapter_surface_install_receipt": adapter_surface_install_receipt,
        },
        experiments={
            **experiment_pack,
            "all_experiments": all_experiments,
            "by_obligation": by_obligation,
            "obligation_plan": obligation_plan,
            "planning_budget_receipt": budget_receipt,
            "pre_transport_executability_receipt": _pre_transport_receipt,
            "agent_intent_plan": agent_intent_plan,
            "coverage_unit_receipt": coverage_unit_receipt,
            "coverage_unit_compile_receipt": representative_compile_receipt,
            "coverage_unit_arm_receipt": arm_receipt,
            "planning_authority": planning_authority,
            "agent_semantic_link_receipt": agent_semantic_link_receipt,
            "comprehension_authority_receipt": comprehension_authority_receipt,
            "runtime_source_overlay_receipt": dict(
                _dict(asset.get("runtime_source_overlay"))
            ),
            "behavior_ir_input_receipt": behavior_ir_input_receipt,
            "contract_derivation_receipt": contract_derivation_receipt,
            "runtime_interface_discovery_enabled": (
                runtime_interface_discovery_enabled
            ),
            "runtime_interface_discovery_plan": (
                runtime_interface_discovery_plan
            ),
            "runtime_contract": dict(
                _dict(inputs.campaign_context.get("_runtime_contract"))
            ),
            "preflight_receipt": preflight_receipt,
            "learning_consumption_receipt": _learning_consumption_receipt,
            "binding_experience_receipt": _binding_reorder_receipt,
            "binding_closure_receipt": _binding_closure_receipt,
            "space_exploration_receipt": {
                "schema_version": "qualibug.space-exploration-receipt.v1",
                **_space_exploration_report,
                "coverage_reorder": _reorder_receipt,
            },
            "fact_experimentability_ledger_fingerprint": _text(
                _fact_exp_ledger.get("ledger_fingerprint")
            ),
            # Public reference-only ledger for post-run first-loss accounting.
            # Not a second Business World Model; items cite Canonical fact_refs.
            "fact_experimentability_ledger": dict(_fact_exp_ledger),
            "fact_ref_attach_receipt": _fact_ref_attach_receipt,
            "knowledge_asset_id": _text(asset.get("asset_id")),
            # Immutable in-memory inputs for observation-driven round 2. These
            # private keys are intentionally excluded from product artifacts.
            "_knowledge_asset": asset,
            "_documented_operations": operations,
            "_runtime_actors": runtime_actors,
            "_environment_type": environment_type,
            "_planning_budget": budget,
            "_planning_policy_identity": policy_identity,
        },
    )

