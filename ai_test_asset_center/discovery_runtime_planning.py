"""Discovery planning: API ops, actors, Behavior IR, obligations, experiments.

Extracted from ``discovery_runtime``. ``build_discovery_plan`` remains the
immutable planning authority for the experiment-candidate mainline. Symbols are
re-exported from ``discovery_runtime`` for compatibility.
"""
from __future__ import annotations

import json
import logging
import os
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
from .credential_crypto import CredentialDecryptionError
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
    _parse_test_accounts_md,
    configured_runtime_accounts,
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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _api_operations(
    api_spec_text: str,
    *,
    submitted_source_text: str = "",
) -> list[dict[str, Any]]:
    from .universal_api_parser import build_api_operations_from_text

    try:
        return build_api_operations_from_text(
            api_spec_text,
            submitted_source_text=submitted_source_text,
        )
    except ValueError as exc:
        raise MainlineContractError(str(exc)) from exc


def _runtime_actors(root: Path, project: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    accounts_path = root / "platform_inputs" / project / "test_accounts.json"
    payload: Any = {}
    if accounts_path.exists():
        try:
            payload = json.loads(accounts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MainlineContractError(
                f"test_actor_catalog_invalid:{type(exc).__name__}"
            ) from exc
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        raw_rows = payload.get("accounts") or payload.get("actors") or payload.get("users")
        if raw_rows is None:
            rows = [
                {**value, "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict)
                and key not in {"schema", "schema_version", "meta"}
            ]
        elif isinstance(raw_rows, list):
            rows = raw_rows
        else:
            raise MainlineContractError("test_actor_catalog_rows_invalid")
    else:
        raise MainlineContractError("test_actor_catalog_root_invalid")
    if not rows:
        # Credentials saved through the enterprise settings route are the
        # authoritative role-to-login binding when no legacy JSON catalog is
        # present. This keeps the account identity exact and avoids translating
        # display labels from TEST_ACCOUNTS.md into source role identities.
        try:
            rows = configured_runtime_accounts(root, project)
        except CredentialDecryptionError as exc:
            # A project may carry an encrypted credential snapshot produced by a
            # different control-plane key.  Do not turn a usable, source-declared
            # TEST_ACCOUNTS corpus into an anonymous run, but also do not hide a
            # credential failure: the fallback is allowed only when it has an
            # exact role, login identity, and password from the source corpus.
            _planning_logger.error(
                "runtime_credential_config_decryption_failed project=%s "
                "source_fallback=registered_test_data_or_TEST_ACCOUNTS.md "
                "error_type=%s",
                project,
                type(exc).__name__,
                exc_info=True,
            )
            source_rows = _parse_test_accounts_md(root, project)
            rows = [
                row
                for row in source_rows
                if isinstance(row, dict)
                and _text(row.get("role") or row.get("name") or row.get("id"))
                and _text(
                    row.get("email")
                    or row.get("username")
                    or row.get("account")
                    or row.get("mobile")
                    or row.get("phone")
                )
                and _text(row.get("password") or row.get("pass"))
            ]
            if not rows:
                raise
            context["runtime_credential_resolution"] = {
                "status": "source_backed_fallback",
                "configured_status": "decryption_failed",
                "source": "registered_test_data_or_TEST_ACCOUNTS.md",
                "error_type": type(exc).__name__,
                "account_count": len(rows),
            }
    if not rows:
        # Keep the existing Markdown parser as a source-backed fallback for
        # projects that have not configured a service credential manager. A
        # localized display label remains exactly as declared; it is never
        # guessed to be an English role.
        rows = _parse_test_accounts_md(root, project)
    for row in rows:
        if not isinstance(row, dict):
            raise MainlineContractError("test_actor_catalog_row_invalid")
        # Prefer the role observed from the authenticated identity over a
        # display/localized role label.  Permission relations are keyed by the
        # source role identity; using only a translated display label severs
        # the source-permitted actor -> runtime credential lineage and leaves
        # otherwise executable obligations blocked on a missing actor.
        role = _text(
            row.get("authenticated_role")
            or row.get("role")
            or row.get("name")
            or row.get("id")
        )
        if not role:
            raise MainlineContractError("test_actor_role_missing")
        account_ref = _text(
            row.get("account_ref")
            or row.get("email")
            or row.get("username")
            or row.get("id")
            or role
        )
        actors.append({
            "role": role,
            "account_ref": account_ref,
            "tenant": row.get("tenant") or row.get("scope"),
            "secret_ref": f"secret_ref:test_accounts:{account_ref}",
            "status": _text(row.get("status") or "active"),
        })
    scenario_actor = _dict(_dict(context.get("runtime_scenario_contract")).get("actor"))
    declared_role = _text(
        scenario_actor.get("role")
        or scenario_actor.get("name")
        or scenario_actor.get("id")
    )
    if declared_role and not any(_text(row.get("role")) == declared_role for row in actors):
        actors.append({
            "role": declared_role,
            "secret_ref": f"secret_ref:context:{declared_role}",
            "status": "active",
        })
    return actors


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
        merge_knowledge_asset_overlay,
    )

    asset = build_enterprise_business_knowledge_asset(inputs.project, inputs.root)
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
        _space_exploration_report = {
            "invariant_count": _inv_graph.size,
            "operator_count": _op_registry.size,
            "applicable_operators": len(_applicable_ops),
            "combinations_generated": len(_combos) if isinstance(_combos, list) else 0,
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
    experiment_pack = compile_experiments(
        obligations,
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
    obligation_plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=by_obligation,
        behavior_ir=behavior_ir,
        budget=budget,
        historical_yield=historical_yield,
        historical_receipt_ids=(
            [_text(history_receipt.get("receipt_id"))]
            if (
                history_match_status
                in {"MATCHED", "MATCHED_HISTORY_HAS_NO_FAMILY_METRICS"}
                and _text(history_receipt.get("receipt_id"))
            )
            else []
        ),
        cold_start_reason=history_match_status,
    )

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
    # Non-authoritative fact_ref projection for first-loss join. Must not alter
    # compile/selection/execution decisions.
    from .fact_first_loss_ledger import attach_fact_refs_to_planning_artifacts

    _fact_exp_ledger = _dict(asset.get("fact_experimentability_ledger"))
    _fact_ref_attach_receipt = attach_fact_refs_to_planning_artifacts(
        obligations=obligations,
        experiments=all_experiments,
        fact_experimentability_ledger=_fact_exp_ledger,
    )
    return DiscoveryPlanningBundle(
        mainline_run=contract,
        behavior_ir=behavior_ir,
        obligations={
            **obligation_pack,
            "obligations": obligations,
            "obligation_identity_receipt": _obligation_identity_receipt,
            "behavior_ir_coverage_report": coverage_report,
            "state_audit_report": state_audit_report,
            "conflict_report": conflict_report,
            "fact_ref_attach_receipt": _fact_ref_attach_receipt,
            "adapter_surface_install_receipt": adapter_surface_install_receipt,
        },
        experiments={
            **experiment_pack,
            "all_experiments": all_experiments,
            "by_obligation": by_obligation,
            "obligation_plan": obligation_plan,
            "planning_budget_receipt": budget_receipt,
            "agent_intent_plan": agent_intent_plan,
            "agent_semantic_link_receipt": agent_semantic_link_receipt,
            "runtime_source_overlay_receipt": dict(
                _dict(asset.get("runtime_source_overlay"))
            ),
            "behavior_ir_input_receipt": behavior_ir_input_receipt,
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

