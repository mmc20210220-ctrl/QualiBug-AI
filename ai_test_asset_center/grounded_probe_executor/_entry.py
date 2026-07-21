"""Main entry point: run_grounded_probe_executor."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403
from ._evidence_delivery import *  # noqa: F401,F403
from ._core import *  # noqa: F401,F403
from ._evidence_scoreboard import *  # noqa: F401,F403
from ._reproduction import *  # noqa: F401,F403

def run_grounded_probe_executor(
    *,
    probe_plan_path: str | Path,
    out_dir: str | Path,
    base_url: str = "",
    probe_config: str | Path | None = None,
    execute_readonly: bool = False,
    allow_write_sandbox: bool = False,
    approval_id: str = "",
    max_probes: int = 0,
    timeout_seconds: float = 10.0,
    input_dir: str | Path | None = None,
) -> dict[str, Any]:
    plan_path = Path(probe_plan_path).resolve()
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    config = _load_config(probe_config)
    if input_dir and not config.get("input_dir"):
        config["input_dir"] = str(input_dir)
    base = str(base_url or config.get("base_url") or os.environ.get("QUALIBUG_TARGET_BASE_URL") or "").rstrip("/")
    config = _materialize_account_auth(config, base, timeout_seconds)
    original_probes = list(plan.get("probes") or [])
    bug_discovery_expansion = expand_bug_discovery_probes(plan, input_dir=config.get("input_dir"), config=config)
    probes = original_probes + list(bug_discovery_expansion.get("probes") or [])
    runtime_rerun_selection: dict[str, Any]
    probes, runtime_rerun_selection = _apply_runtime_rerun_selection(probes, config)

    # ── Coupon validation enrichment (pre-scan, DB-driven) ──
    # If the customer has a database DSN, query real expired / inactive /
    # category-mismatched coupons and inject probes that submit them against the
    # validate endpoint.  These are fully generic — the coupon codes come from
    # the DB, the expected rejection (4xx) is encoded in the HTTP contract, so
    # any 2xx acceptance of a known-invalid coupon is a real money/rule defect.
    _db_dsn = os.environ.get("QUALIBUG_DB_DSN", "")
    if _db_dsn:
        try:
            import math
            try:
                import psycopg2
                conn = psycopg2.connect(_db_dsn)
                cur = conn.cursor()

                def _row(sql: str, params: tuple = ()) -> dict[str, Any]:
                    cur.execute(sql, params)
                    r = cur.fetchone()
                    if r is None:
                        return {}
                    return dict(zip([d[0] for d in cur.description], r))

                def _product(*, skip_category: str = "") -> dict[str, Any]:
                    if skip_category:
                        return _row("SELECT sku, category, price, status FROM products WHERE COALESCE(status,'') IN ('ON_SALE','ACTIVE') AND COALESCE(price,0)>0 AND COALESCE(category,'')<>%s ORDER BY price DESC, sku ASC LIMIT 1", (skip_category,))
                    return _row("SELECT sku, category, price, status FROM products WHERE COALESCE(status,'') IN ('ON_SALE','ACTIVE') AND COALESCE(price,0)>0 ORDER BY price DESC, sku ASC LIMIT 1")

                def _qty(min_order: float, price_val: float) -> int:
                    return max(1, int(math.ceil(max(float(min_order or 0), float(price_val or 0.01)) / max(float(price_val or 0.01), 0.01))))

                coupon_cases: dict[str, dict[str, Any]] = {}
                # expired
                expired = _row("SELECT code, min_order_amount, category_scope, status, expires_at FROM coupons WHERE expires_at IS NOT NULL AND expires_at<NOW() ORDER BY expires_at ASC, code ASC LIMIT 1")
                if expired:
                    prod = _product()
                    if prod:
                        coupon_cases["expired_coupon_must_be_invalid"] = {
                            "body": {"code": str(expired.get("code") or ""), "items": [{"sku": str(prod.get("sku") or ""), "qty": _qty(float(expired.get("min_order_amount") or 0), float(prod.get("price") or 0)), "price": float(prod.get("price") or 0)}], "totalAmount": round(_qty(float(expired.get("min_order_amount") or 0), float(prod.get("price") or 0)) * float(prod.get("price") or 0), 2)},
                            "coupon_code": str(expired.get("code") or ""),
                        }
                # inactive
                inactive = _row("SELECT code, min_order_amount, category_scope, status, expires_at FROM coupons WHERE COALESCE(status,'')<>'ACTIVE' ORDER BY expires_at ASC NULLS LAST, code ASC LIMIT 1")
                if inactive:
                    prod = _product()
                    if prod:
                        coupon_cases["inactive_coupon_must_be_invalid"] = {
                            "body": {"code": str(inactive.get("code") or ""), "items": [{"sku": str(prod.get("sku") or ""), "qty": _qty(float(inactive.get("min_order_amount") or 0), float(prod.get("price") or 0)), "price": float(prod.get("price") or 0)}], "totalAmount": round(_qty(float(inactive.get("min_order_amount") or 0), float(prod.get("price") or 0)) * float(prod.get("price") or 0), 2)},
                            "coupon_code": str(inactive.get("code") or ""),
                        }
                # category mismatch
                mismatched = _row("SELECT code, min_order_amount, category_scope, status, expires_at FROM coupons WHERE COALESCE(status,'')='ACTIVE' AND category_scope IS NOT NULL AND (expires_at IS NULL OR expires_at>=NOW()) ORDER BY min_order_amount DESC NULLS LAST, code ASC LIMIT 10")
                if mismatched:
                    prod = _product(skip_category=str(mismatched.get("category_scope") or ""))
                    if prod:
                        coupon_cases["coupon_category_scope_must_match"] = {
                            "body": {"code": str(mismatched.get("code") or ""), "items": [{"sku": str(prod.get("sku") or ""), "qty": _qty(float(mismatched.get("min_order_amount") or 0), float(prod.get("price") or 0)), "price": float(prod.get("price") or 0)}], "totalAmount": round(_qty(float(mismatched.get("min_order_amount") or 0), float(prod.get("price") or 0)) * float(prod.get("price") or 0), 2)},
                            "coupon_code": str(mismatched.get("code") or ""),
                        }
                try:
                    conn.close()
                except Exception as _close_exc:
                    logger.warning("coupon probe: failed to close DB connection: %s: %s", type(_close_exc).__name__, _close_exc)
            except Exception as _coupon_exc:
                logger.error(
                    "coupon probe generation failed; dropping coupon_cases to avoid poisoning the plan. error=%s: %s",
                    type(_coupon_exc).__name__,
                    _coupon_exc,
                )
                coupon_cases = {}
            for label, case in coupon_cases.items():
                if not isinstance(case, dict) or not case.get("body"):
                    continue
                coupon_code = str(case.get("coupon_code") or "")
                coupon_probe = {
                    "candidate_id": f"CUP-{label}",
                    "risk_type": "business_rule_probe",
                    "endpoint": {"method": "POST", "path": "/api/coupons/validate"},
                    "execution_policy": "runtime_approved",
                    "actors": ["buyer"],
                    "probe_plan": {
                        "steps": [f"Submit known-{label.replace('_',' ')} coupon {coupon_code} for validation"],
                        "expected_status": [400, 422, 404],
                        "focus_rule": label,
                    },
                    "required_evidence": ["request_response_pair", "status_code"],
                    "source_refs": [
                        {"file": "coupon_validation_samples", "section": label, "kind": "db_sampled_test_case"},
                        {"file": "api.md", "section": "POST /api/coupons/validate", "kind": "endpoint_contract"},
                    ],
                    "grounding_basis": {"source": "db_sampled_coupon", "label": label, "api_path": "/api/coupons/validate"},
                    "validation_priority": 0,
                }
                # Attach the concrete request body so the probe has real data.
                coupon_probe["_coupon_body"] = dict(case.get("body") or {})
                probes.append(coupon_probe)
        except Exception:
            pass  # coupon probes best-effort

    # ── Multi-actor permission probe expansion ──
    # For every auth_boundary / role_downgrade write probe in the plan that only
    # tests a single actor, create sibling probes for EVERY other available actor
    # (including admin).  A buyer succeeding on an admin-only endpoint is a
    # validated permission bug.  Fully generic: actors come from the resolved
    # account headers, paths from the probe plan, expected rejection is 4xx.
    resolved_actors = list((config.get("_resolved_account_headers") or {}).keys())
    # filter to meaningful role tokens only
    privileged_roles = [a for a in resolved_actors if a not in ("anonymous", "disabled", "disabled_buyer", "user")]
    if len(privileged_roles) >= 3:
        seen_endpoints: set[tuple[str, str]] = set()
        originals = list(probes)
        for p in originals:
            rt = str(p.get("risk_type") or "")
            if rt not in ("auth_boundary_probe", "role_downgrade_auth_boundary_probe"):
                continue
            ep = p.get("endpoint") or {}
            method = str(ep.get("method") or "").upper()
            path = str(ep.get("path") or "")
            if method not in WRITE_METHODS:
                continue
            key = (method, path)
            if key in seen_endpoints:
                continue
            seen_endpoints.add(key)
            for actor in privileged_roles:
                # Don't duplicate the original probe's actor if it already exists
                existing_actors = [str(a).strip().lower() for a in (p.get("actors") or [])]
                if actor in existing_actors:
                    continue
                perm_probe = dict(p)
                perm_probe["candidate_id"] = f"{p.get('candidate_id','')}-ACTOR-{actor}"
                perm_probe["actors"] = [actor]
                pp = dict(p.get("probe_plan") or {})
                pp["expected_status"] = [401, 403, 404]
                pp["steps"] = [f"Actor {actor} attempts {method} {path} — must be rejected unless role permits"]
                perm_probe["probe_plan"] = pp
                perm_probe["execution_policy"] = "runtime_approved"
                perm_probe["source_refs"] = list(p.get("source_refs") or []) + [
                    {"file": "multi_actor_expansion", "section": f"{actor}_on_{method}_{path}", "kind": "role_coverage"}
                ]
                probes.append(perm_probe)
    # ── end multi-actor expansion ──

    if max_probes and max_probes > 0:
        before_max = len(probes)
        probes = probes[:max_probes]
        if runtime_rerun_selection.get("enabled"):
            runtime_rerun_selection["selected_probe_count_before_max_probes"] = before_max
            runtime_rerun_selection["selected_probe_count"] = len(probes)
            runtime_rerun_selection["max_probes_applied"] = int(max_probes)
    options = {"execute_readonly": execute_readonly, "allow_write_sandbox": allow_write_sandbox, "approval_id": approval_id}
    preflight = run_runtime_onboarding_preflight(
        plan={**plan, "probes": probes},
        config=config,
        base_url=base,
        execute_readonly=execute_readonly,
        allow_write_sandbox=allow_write_sandbox,
        timeout_seconds=timeout_seconds,
        requester=_http_request,
    )
    runtime_capability_matrix = build_runtime_probe_capability_matrix(probes, preflight)
    onboarding_remediation_kit = build_onboarding_remediation_kit(preflight, runtime_capability_matrix)

    decisions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    write_observations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for probe in probes:
        decision = _decide_probe(probe, base_url=base, config=config, options=options)
        d = asdict(decision)
        decisions.append(d)
        if decision.decision == "execute_readonly":
            obs = _execute_read_probe(probe, decision, config, base, timeout_seconds)
            observations.append(obs)
            if (obs.get("verification") or {}).get("verdict") == "validated_candidate":
                findings.append(_finding_from_observation(obs, len(findings) + 1, "runtime_http_evidence_from_document_grounded_probe"))
        elif decision.decision == "execute_write_sandbox":
            probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
            if decision.risk_type == "business_flow_sequence_probe" or isinstance(probe_plan.get("flow_scenario"), dict):
                obs = _execute_flow_probe(probe, decision, config, base, timeout_seconds)
            else:
                obs = _execute_write_probe(probe, decision, config, base, timeout_seconds)
            write_observations.append(obs)
            if (obs.get("verification") or {}).get("verdict") == "validated_candidate":
                findings.append(_finding_from_observation(obs, len(findings) + 1, "runtime_http_evidence_from_document_grounded_sandbox_write_probe"))

    by_decision: dict[str, int] = {}
    for d in decisions:
        by_decision[d.get("decision", "unknown")] = by_decision.get(d.get("decision", "unknown"), 0) + 1
    all_obs = observations + write_observations
    protected = sum(1 for o in all_obs if ((o.get("verification") or {}).get("verdict") == "falsified_or_protected"))
    needs_more = sum(1 for o in all_obs if ((o.get("verification") or {}).get("verdict") == "needs_more_evidence"))
    fixture_setup_count = sum(len(o.get("fixture_receipts") or []) for o in write_observations)
    fixture_cleanup_count = sum(len(o.get("cleanup_receipts") or []) for o in write_observations)
    snapshot_count = sum(len((o.get("snapshots") or {}).get("before") or []) + len((o.get("snapshots") or {}).get("after") or []) for o in write_observations)
    snapshot_observer_kinds = sorted({
        str(s.get("observer_kind"))
        for o in write_observations
        for phase in ("before", "after")
        for s in ((o.get("snapshots") or {}).get(phase) or [])
        if isinstance(s, dict) and s.get("observer_kind")
    })
    customer_delivery_index = build_customer_delivery_index(findings)
    probe_outcomes = _build_probe_outcomes(decisions, observations, write_observations, findings)
    probe_outcome_counts = {
        outcome: sum(1 for item in probe_outcomes if item.get("outcome") == outcome)
        for outcome in sorted({str(item.get("outcome")) for item in probe_outcomes if item.get("outcome")})
    }
    report = {
        "engine": "grounded_probe_executor_v41_phase93z",
        "mode": "document_grounded_probe_execution",
        "strict_no_peek": True,
        "created_at": _now(),
        "project_id": plan.get("project_id"),
        "probe_plan": str(plan_path),
        "base_url_configured": bool(base),
        "execute_readonly": bool(execute_readonly),
        "allow_write_sandbox": bool(allow_write_sandbox),
        "approval_id_present": bool(approval_id),
        "runtime_rerun_selection": runtime_rerun_selection,
        "governance": {
            "input_only": True,
            "oracle_files_read": False,
            "strict_document_grounding_required": os.environ.get("QUALIBUG_STRICT_PROBE_GROUNDING", "1") != "0",
            "write_probe_execution": "requires_test_environment_execution_enabled_plus_production_guard_and_document_grounding",
            "runtime_findings_require_http_evidence": True,
            "write_request_bodies_invented_by_engine": False,
            "write_request_bodies_generated_from_openapi_by_qualibug": bool(_auto_fixture_enabled(config)),
            "auto_test_data_generated_by_qualibug": bool(_auto_fixture_enabled(config)),
            "auto_fixture_setup_and_cleanup_supported": True,
            "before_after_business_invariant_auto_judgement": True,
            "snapshot_observer_planner_enabled": bool(_auto_fixture_enabled(config)),
            "phase92q_multi_observer_snapshots": True,
            "phase92r_observer_response_semantic_joiner": True,
            "phase92s_cross_observer_conservation_reconciler": True,
            "phase92t_customer_ready_evidence_packaging": True,
            "phase92u_customer_impact_triage": True,
            "phase92v_customer_delivery_index": True,
            "phase92w_reproduction_artifact_backlinks": True,
            "phase92x_fix_verification_lifecycle_loop": True,
            "phase92y_stable_finding_lifecycle_registry": True,
            "phase92z_remediation_verification_artifact": True,
            "phase93a_runtime_onboarding_preflight": True,
            "phase93b_probe_runtime_capability_matrix": True,
            "phase93c_onboarding_remediation_kit": True,
            "phase93d_runtime_execution_runbook": True,
            "phase93e_runtime_evidence_readiness_sla_gate": True,
            "phase93f_runtime_sla_execution_policy": True,
            "phase93g_sla_gap_auto_prioritizer": True,
            "phase93h_onboarding_patch_safety_validator": True,
            "phase93i_write_sandbox_approval_packet": True,
            "phase93j_commercial_handoff_bundle": True,
            "phase93k_commercial_handoff_acceptance_gate": True,
            "phase93l_handoff_secret_audit": True,
            "phase93m_handoff_archive_manifest_and_immutable_run_receipt": True,
            "phase93n_immutable_handoff_receipt_comparison": True,
            "phase93o_commercial_rerun_audit_gate": True,
            "phase93p_commercial_evidence_lineage_dashboard": True,
            "phase93q_commercial_lineage_reviewer_signoff": True,
            "phase93r_commercial_closure_acceptance_ledger": True,
            "phase93s_commercial_audit_event_stream": True,
            "phase93t_commercial_audit_export_adapters": True,
            "phase93u_commercial_audit_export_import_gate": True,
            "phase93v_commercial_external_tracker_reconciliation": True,
            "phase93w_external_tracker_closure_sync_policy": True,
            "phase93x_external_tracker_sync_payload_builder": True,
            "phase93y_external_tracker_sync_payload_gate": True,
            "phase93z_external_tracker_sync_receipt_ledger": True,
            "phase94a_business_state_machine_auto_exploration": True,
            "phase94b_multistep_business_flow_composition": True,
            "phase94c_high_value_business_mutation_probe_generation": True,
            "phase94d_concurrency_race_probe_planning": True,
            "customer_business_data_input_required": False,
            "customer_auth_input_mode": "username_password_accounts_preferred",
            "raw_tokens_required_from_customer": False,
        },
        "auth_runtime": config.get("_auth_runtime") or {},
        "summary": {
            "probe_count": len(probes),
            "original_probe_count": len(original_probes),
            "runtime_rerun_selection_enabled": bool(runtime_rerun_selection.get("enabled")),
            "runtime_evidence_carry_forward_supported": True,
            "runtime_rerun_selection_status": runtime_rerun_selection.get("status"),
            "runtime_rerun_selected_probe_count": runtime_rerun_selection.get("selected_probe_count", len(probes)),
            "runtime_rerun_skipped_probe_count": runtime_rerun_selection.get("skipped_probe_count", 0),
            "runtime_rerun_missing_candidate_count": len(runtime_rerun_selection.get("missing_candidate_ids") or []),
            "runtime_carry_forward_status": "not_built",
            "runtime_carry_forward_candidate_count": 0,
            "runtime_carry_forward_reproduction_count": 0,
            "runtime_carry_forward_probe_ledger_count": 0,
            "runtime_progress_delta_status": "not_built",
            "runtime_progress_delta_regression_count": 0,
            "runtime_progress_delta_resolved_gap_count": 0,
            "runtime_progress_delta_new_gap_count": 0,
            "runtime_promotion_gate_status": "not_built",
            "runtime_promotion_gate_ready": False,
            "runtime_promotion_gate_blocker_count": 0,
            "runtime_promotion_gate_approved_candidate_count": 0,
            "runtime_delivery_manifest_status": "not_built",
            "runtime_delivery_manifest_ready": False,
            "runtime_delivery_manifest_hashed_required_artifact_count": 0,
            "runtime_delivery_manifest_missing_required_artifact_count": 0,
            "runtime_delivery_manifest_baseline_id": "",
            "runtime_delivery_manifest_verification_status": "not_built",
            "runtime_delivery_manifest_verified": False,
            "runtime_delivery_manifest_verification_failed_required_artifact_count": 0,
            "phase94_added_probe_count": int(bug_discovery_expansion.get("added_probe_count") or 0),
            "phase94_added_p0_probe_count": int(bug_discovery_expansion.get("added_p0_probe_count") or 0),
            "phase94_multistep_flow_scenario_count": int(bug_discovery_expansion.get("multistep_flow_scenario_count") or 0),
            "executed_readonly_count": len(observations),
            "executed_write_sandbox_count": len(write_observations),
            "blocked_count": by_decision.get("blocked", 0),
            "dry_run_count": by_decision.get("dry_run_only", 0),
            "validated_candidate_count": len(findings),
            "protected_count": protected,
            "needs_more_evidence_count": needs_more,
            "probe_outcome_count": len(probe_outcomes),
            "probe_outcome_counts": probe_outcome_counts,
            "auto_fixture_setup_request_count": fixture_setup_count,
            "auto_fixture_cleanup_request_count": fixture_cleanup_count,
            "auto_snapshot_request_count": snapshot_count,
            "auto_snapshot_observer_kinds": snapshot_observer_kinds,
            "auto_snapshot_observer_kind_count": len(snapshot_observer_kinds),
            "semantic_joined_observer_graph_count": sum(1 for o in write_observations if (((o.get("verification") or {}).get("business_invariant_evaluation") or {}).get("semantic_observer_graph") or {}).get("engine") == "observer_response_semantic_joiner_v1_phase92r"),
            "cross_observer_conservation_checked_count": sum(1 for o in write_observations for r in ((((o.get("verification") or {}).get("business_invariant_evaluation") or {}).get("results") or [])) if isinstance(r, dict) and r.get("kind") == "cross_observer_conservation_reconciliation"),
            "customer_ready_evidence_package_count": sum(1 for f in findings if (f.get("evidence_package") or {}).get("engine") == "runtime_finding_evidence_packager_v1_phase92t"),
            "strong_evidence_finding_count": sum(1 for f in findings if f.get("evidence_grade") == "strong"),
            "critical_finding_count": sum(1 for f in findings if f.get("severity") == "critical"),
            "high_finding_count": sum(1 for f in findings if f.get("severity") == "high"),
            "by_priority": {p: sum(1 for f in findings if f.get("priority") == p) for p in sorted({str(f.get("priority")) for f in findings if f.get("priority")})},
            "by_decision": by_decision,
            "onboarding_preflight_status": preflight.get("status"),
            "onboarding_preflight_blocking_count": len(preflight.get("blocking_reasons") or []),
            "onboarding_preflight_warning_count": len(preflight.get("warning_reasons") or []),
            "ready_for_p0_p1_runtime_validation": bool(preflight.get("ready_for_p0_p1_runtime_validation")),
            "runtime_ready_probe_count": runtime_capability_matrix.get("runtime_ready_probe_count", 0),
            "runtime_degraded_probe_count": runtime_capability_matrix.get("degraded_probe_count", 0),
            "runtime_capability_blocked_probe_count": runtime_capability_matrix.get("blocked_probe_count", 0),
            "onboarding_remediation_action_count": onboarding_remediation_kit.get("action_count", 0),
            "onboarding_remediation_p0_action_count": onboarding_remediation_kit.get("p0_action_count", 0),
            "runtime_runbook_step_count": 0,
            "runtime_evidence_readiness_score": 0,
            "runtime_evidence_sla_gate_passed": False,
            "runtime_execution_integrity_score": 0,
            "runtime_scoreboard_binding_success_rate": 0,
            "runtime_scoreboard_fixture_setup_success_rate": 0,
            "runtime_scoreboard_cleanup_success_rate": 0,
            "runtime_scoreboard_snapshot_success_rate": 0,
            "runtime_scoreboard_top_gap_count": 0,
            "runtime_probe_ledger_entry_count": 0,
            "runtime_probe_ledger_customer_ready_count": 0,
            "runtime_probe_ledger_evidence_gap_count": 0,
            "runtime_sla_must_run_count": 0,
            "runtime_sla_blocked_before_sla_count": 0,
            "runtime_sla_gap_prioritized_action_count": 0,
            "runtime_sla_estimated_score_after_top_actions": 0,
            "onboarding_patch_safety_issue_count": 0,
            "onboarding_patch_safe_to_send": False,
            "write_sandbox_approval_required": False,
            "write_sandbox_approval_ready": False,
            "commercial_handoff_status": "not_built",
            "commercial_handoff_blocker_count": 0,
            "commercial_handoff_artifact_count": 0,
            "commercial_handoff_acceptance_status": "not_built",
            "commercial_handoff_acceptance_gate_passed": False,
            "commercial_handoff_acceptance_violation_count": 0,
            "commercial_handoff_secret_audit_status": "not_built",
            "commercial_handoff_secret_audit_issue_count": 0,
            "commercial_handoff_safe_for_customer": False,
            "commercial_handoff_secret_redaction_plan_status": "not_built",
            "commercial_handoff_secret_redaction_action_count": 0,
            "commercial_handoff_redacted_runtime_evidence_status": "not_built",
            "commercial_handoff_redacted_runtime_evidence_safe": False,
            "commercial_handoff_redacted_runtime_evidence_action_count": 0,
            "handoff_archive_manifest_status": "not_built",
            "handoff_archive_hashed_artifact_count": 0,
            "handoff_archive_missing_required_artifact_count": 0,
            "immutable_run_receipt_status": "not_built",
            "immutable_run_lineage_id": "",
            "handoff_receipt_comparison_status": "not_built",
            "handoff_receipt_change_count": 0,
            "handoff_receipt_lineage_match": False,
            "handoff_rerun_audit_status": "not_built",
            "handoff_rerun_closure_allowed": False,
            "handoff_rerun_audit_blocker_count": 0,
            "commercial_evidence_lineage_dashboard_status": "not_built",
            "commercial_evidence_lineage_closure_claim_state": "not_built",
            "commercial_evidence_lineage_changed_hash_count": 0,
            "commercial_lineage_reviewer_signoff_status": "not_built",
            "commercial_lineage_reviewer_signoff_required": False,
            "commercial_lineage_reviewer_signoff_item_count": 0,
            "commercial_closure_acceptance_ledger_status": "not_built",
            "commercial_closure_acceptance_ledger_entry_count": 0,
            "commercial_audit_event_stream_status": "not_built",
            "commercial_audit_event_count": 0,
            "commercial_audit_export_status": "not_built",
            "commercial_audit_jira_issue_count": 0,
            "commercial_audit_linear_issue_count": 0,
            "commercial_audit_csv_row_count": 0,
            "commercial_closure_external_tracking_key_count": 0,
            "commercial_audit_import_gate_status": "not_built",
            "commercial_audit_import_ready": False,
            "commercial_audit_import_violation_count": 0,
            "commercial_audit_import_placeholder_count": 0,
            "commercial_external_tracker_reconciliation_status": "not_built",
            "commercial_external_tracker_reconciliation_entry_count": 0,
        },
        "customer_delivery_index": customer_delivery_index,
        "bug_discovery_expansion": bug_discovery_expansion,
        "onboarding_preflight": preflight,
        "runtime_capability_matrix": runtime_capability_matrix,
        "onboarding_remediation_kit": onboarding_remediation_kit,
        "decisions": annotate_decisions_with_capability(decisions, runtime_capability_matrix),
        "observations": observations,
        "write_observations": write_observations,
        "probe_outcomes": probe_outcomes,
        "findings": findings,
    }

    report_path = output / "grounded_probe_execution_report.json"
    md_path = output / "grounded_probe_execution_report.md"
    ps1_path = output / "grounded_probe_repro.ps1"
    pytest_path = output / "grounded_probe_regression_pytest.py"
    remediation_json_path = output / "grounded_probe_remediation_verification.json"
    remediation_md_path = output / "grounded_probe_remediation_verification.md"
    preflight_path = output / "grounded_probe_onboarding_preflight.json"
    capability_matrix_path = output / "grounded_probe_runtime_capability_matrix.json"
    onboarding_remediation_json_path = output / "grounded_probe_onboarding_remediation_kit.json"
    onboarding_remediation_md_path = output / "grounded_probe_onboarding_remediation_kit.md"
    runtime_runbook_json_path = output / "grounded_probe_runtime_execution_runbook.json"
    runtime_runbook_md_path = output / "grounded_probe_runtime_execution_runbook.md"
    readiness_sla_json_path = output / "grounded_probe_runtime_evidence_readiness_sla_gate.json"
    readiness_sla_md_path = output / "grounded_probe_runtime_evidence_readiness_sla_gate.md"
    runtime_scoreboard_json_path = output / "grounded_probe_runtime_evidence_scoreboard.json"
    runtime_scoreboard_md_path = output / "grounded_probe_runtime_evidence_scoreboard.md"
    runtime_probe_ledger_json_path = output / "grounded_probe_runtime_evidence_probe_ledger.json"
    runtime_probe_ledger_md_path = output / "grounded_probe_runtime_evidence_probe_ledger.md"
    runtime_repro_pack_json_path = output / "grounded_probe_runtime_customer_reproduction_pack.json"
    runtime_repro_pack_md_path = output / "grounded_probe_runtime_customer_reproduction_pack.md"
    runtime_remediation_plan_json_path = output / "grounded_probe_runtime_evidence_remediation_plan.json"
    runtime_remediation_plan_md_path = output / "grounded_probe_runtime_evidence_remediation_plan.md"
    runtime_carry_forward_json_path = output / "grounded_probe_runtime_evidence_carry_forward.json"
    runtime_carry_forward_md_path = output / "grounded_probe_runtime_evidence_carry_forward.md"
    runtime_progress_delta_json_path = output / "grounded_probe_runtime_evidence_progress_delta.json"
    runtime_progress_delta_md_path = output / "grounded_probe_runtime_evidence_progress_delta.md"
    runtime_promotion_gate_json_path = output / "grounded_probe_runtime_evidence_promotion_gate.json"
    runtime_promotion_gate_md_path = output / "grounded_probe_runtime_evidence_promotion_gate.md"
    runtime_delivery_manifest_json_path = output / "grounded_probe_runtime_evidence_customer_delivery_manifest.json"
    runtime_delivery_manifest_md_path = output / "grounded_probe_runtime_evidence_customer_delivery_manifest.md"
    runtime_delivery_manifest_verification_json_path = output / "grounded_probe_runtime_evidence_delivery_manifest_verification.json"
    runtime_delivery_manifest_verification_md_path = output / "grounded_probe_runtime_evidence_delivery_manifest_verification.md"
    runtime_sla_policy_json_path = output / "grounded_probe_runtime_sla_execution_policy.json"
    runtime_sla_policy_md_path = output / "grounded_probe_runtime_sla_execution_policy.md"
    runtime_sla_gap_json_path = output / "grounded_probe_runtime_sla_gap_prioritizer.json"
    runtime_sla_gap_md_path = output / "grounded_probe_runtime_sla_gap_prioritizer.md"
    onboarding_patch_safety_json_path = output / "grounded_probe_onboarding_patch_safety_validation.json"
    onboarding_patch_safety_md_path = output / "grounded_probe_onboarding_patch_safety_validation.md"
    write_sandbox_approval_json_path = output / "grounded_probe_write_sandbox_approval_packet.json"
    write_sandbox_approval_md_path = output / "grounded_probe_write_sandbox_approval_packet.md"
    commercial_handoff_json_path = output / "grounded_probe_commercial_handoff_bundle.json"
    commercial_handoff_md_path = output / "grounded_probe_commercial_handoff_bundle.md"
    commercial_handoff_acceptance_json_path = output / "grounded_probe_commercial_handoff_acceptance_gate.json"
    commercial_handoff_acceptance_md_path = output / "grounded_probe_commercial_handoff_acceptance_gate.md"
    handoff_secret_audit_json_path = output / "grounded_probe_commercial_handoff_secret_audit.json"
    handoff_secret_audit_md_path = output / "grounded_probe_commercial_handoff_secret_audit.md"
    handoff_secret_redaction_plan_json_path = output / "grounded_probe_commercial_handoff_secret_redaction_plan.json"
    handoff_secret_redaction_plan_md_path = output / "grounded_probe_commercial_handoff_secret_redaction_plan.md"
    handoff_redacted_runtime_evidence_json_path = output / "grounded_probe_commercial_handoff_redacted_runtime_evidence.json"
    handoff_redacted_runtime_evidence_md_path = output / "grounded_probe_commercial_handoff_redacted_runtime_evidence.md"
    handoff_archive_manifest_json_path = output / "grounded_probe_handoff_archive_manifest.json"
    handoff_archive_manifest_md_path = output / "grounded_probe_handoff_archive_manifest.md"
    immutable_run_receipt_json_path = output / "grounded_probe_immutable_run_receipt.json"
    immutable_run_receipt_md_path = output / "grounded_probe_immutable_run_receipt.md"
    handoff_receipt_comparison_json_path = output / "grounded_probe_handoff_receipt_comparison.json"
    handoff_receipt_comparison_md_path = output / "grounded_probe_handoff_receipt_comparison.md"
    handoff_rerun_audit_gate_json_path = output / "grounded_probe_handoff_rerun_audit_gate.json"
    handoff_rerun_audit_gate_md_path = output / "grounded_probe_handoff_rerun_audit_gate.md"
    commercial_evidence_lineage_json_path = output / "grounded_probe_commercial_evidence_lineage_dashboard.json"
    commercial_evidence_lineage_md_path = output / "grounded_probe_commercial_evidence_lineage_dashboard.md"
    commercial_lineage_signoff_json_path = output / "grounded_probe_commercial_lineage_reviewer_signoff_packet.json"
    commercial_lineage_signoff_md_path = output / "grounded_probe_commercial_lineage_reviewer_signoff_packet.md"
    commercial_closure_ledger_json_path = output / "grounded_probe_commercial_closure_acceptance_ledger.json"
    commercial_closure_ledger_md_path = output / "grounded_probe_commercial_closure_acceptance_ledger.md"
    commercial_audit_event_stream_json_path = output / "grounded_probe_commercial_audit_event_stream.json"
    commercial_audit_event_stream_md_path = output / "grounded_probe_commercial_audit_event_stream.md"
    commercial_audit_exports_json_path = output / "grounded_probe_commercial_audit_exports.json"
    commercial_audit_exports_md_path = output / "grounded_probe_commercial_audit_exports.md"
    commercial_audit_ledger_csv_path = output / "grounded_probe_commercial_audit_ledger.csv"
    jira_issue_import_json_path = output / "grounded_probe_jira_issue_import.json"
    linear_issue_import_json_path = output / "grounded_probe_linear_issue_import.json"
    reviewer_packet_export_md_path = output / "grounded_probe_reviewer_packet_export.md"
    commercial_audit_import_gate_json_path = output / "grounded_probe_commercial_audit_import_gate.json"
    commercial_audit_import_gate_md_path = output / "grounded_probe_commercial_audit_import_gate.md"
    commercial_external_tracker_reconciliation_json_path = output / "grounded_probe_commercial_external_tracker_reconciliation.json"
    commercial_external_tracker_reconciliation_md_path = output / "grounded_probe_commercial_external_tracker_reconciliation.md"
    external_tracker_closure_sync_policy_json_path = output / "grounded_probe_external_tracker_closure_sync_policy.json"
    external_tracker_closure_sync_policy_md_path = output / "grounded_probe_external_tracker_closure_sync_policy.md"
    external_tracker_sync_payloads_json_path = output / "grounded_probe_external_tracker_sync_payloads.json"
    external_tracker_sync_payloads_md_path = output / "grounded_probe_external_tracker_sync_payloads.md"
    external_tracker_sync_payload_gate_json_path = output / "grounded_probe_external_tracker_sync_payload_gate.json"
    external_tracker_sync_payload_gate_md_path = output / "grounded_probe_external_tracker_sync_payload_gate.md"
    external_tracker_sync_receipt_ledger_json_path = output / "grounded_probe_external_tracker_sync_receipt_ledger.json"
    external_tracker_sync_receipt_ledger_md_path = output / "grounded_probe_external_tracker_sync_receipt_ledger.md"
    bug_discovery_expansion_path = output / "grounded_probe_phase94_bug_discovery_expansion.json"
    report["outputs"] = {
        "execution_report": str(report_path),
        "execution_report_md": str(md_path),
        "repro_ps1": str(ps1_path),
        "regression_pytest": str(pytest_path),
        "remediation_verification_json": str(remediation_json_path),
        "remediation_verification_md": str(remediation_md_path),
        "onboarding_preflight_json": str(preflight_path),
        "runtime_capability_matrix_json": str(capability_matrix_path),
        "onboarding_remediation_kit_json": str(onboarding_remediation_json_path),
        "onboarding_remediation_kit_md": str(onboarding_remediation_md_path),
        "runtime_execution_runbook_json": str(runtime_runbook_json_path),
        "runtime_execution_runbook_md": str(runtime_runbook_md_path),
        "runtime_evidence_readiness_sla_gate_json": str(readiness_sla_json_path),
        "runtime_evidence_readiness_sla_gate_md": str(readiness_sla_md_path),
        "runtime_evidence_scoreboard_json": str(runtime_scoreboard_json_path),
        "runtime_evidence_scoreboard_md": str(runtime_scoreboard_md_path),
        "runtime_evidence_probe_ledger_json": str(runtime_probe_ledger_json_path),
        "runtime_evidence_probe_ledger_md": str(runtime_probe_ledger_md_path),
        "runtime_customer_reproduction_pack_json": str(runtime_repro_pack_json_path),
        "runtime_customer_reproduction_pack_md": str(runtime_repro_pack_md_path),
        "runtime_evidence_remediation_plan_json": str(runtime_remediation_plan_json_path),
        "runtime_evidence_remediation_plan_md": str(runtime_remediation_plan_md_path),
        "runtime_evidence_carry_forward_json": str(runtime_carry_forward_json_path),
        "runtime_evidence_carry_forward_md": str(runtime_carry_forward_md_path),
        "runtime_evidence_progress_delta_json": str(runtime_progress_delta_json_path),
        "runtime_evidence_progress_delta_md": str(runtime_progress_delta_md_path),
        "runtime_evidence_promotion_gate_json": str(runtime_promotion_gate_json_path),
        "runtime_evidence_promotion_gate_md": str(runtime_promotion_gate_md_path),
        "runtime_evidence_customer_delivery_manifest_json": str(runtime_delivery_manifest_json_path),
        "runtime_evidence_customer_delivery_manifest_md": str(runtime_delivery_manifest_md_path),
        "runtime_evidence_delivery_manifest_verification_json": str(runtime_delivery_manifest_verification_json_path),
        "runtime_evidence_delivery_manifest_verification_md": str(runtime_delivery_manifest_verification_md_path),
        "runtime_sla_execution_policy_json": str(runtime_sla_policy_json_path),
        "runtime_sla_execution_policy_md": str(runtime_sla_policy_md_path),
        "runtime_sla_gap_prioritizer_json": str(runtime_sla_gap_json_path),
        "runtime_sla_gap_prioritizer_md": str(runtime_sla_gap_md_path),
        "onboarding_patch_safety_validation_json": str(onboarding_patch_safety_json_path),
        "onboarding_patch_safety_validation_md": str(onboarding_patch_safety_md_path),
        "write_sandbox_approval_packet_json": str(write_sandbox_approval_json_path),
        "write_sandbox_approval_packet_md": str(write_sandbox_approval_md_path),
        "commercial_handoff_bundle_json": str(commercial_handoff_json_path),
        "commercial_handoff_bundle_md": str(commercial_handoff_md_path),
        "commercial_handoff_acceptance_gate_json": str(commercial_handoff_acceptance_json_path),
        "commercial_handoff_acceptance_gate_md": str(commercial_handoff_acceptance_md_path),
        "commercial_handoff_secret_audit_json": str(handoff_secret_audit_json_path),
        "commercial_handoff_secret_audit_md": str(handoff_secret_audit_md_path),
        "commercial_handoff_secret_redaction_plan_json": str(handoff_secret_redaction_plan_json_path),
        "commercial_handoff_secret_redaction_plan_md": str(handoff_secret_redaction_plan_md_path),
        "commercial_handoff_redacted_runtime_evidence_json": str(handoff_redacted_runtime_evidence_json_path),
        "commercial_handoff_redacted_runtime_evidence_md": str(handoff_redacted_runtime_evidence_md_path),
        "handoff_archive_manifest_json": str(handoff_archive_manifest_json_path),
        "handoff_archive_manifest_md": str(handoff_archive_manifest_md_path),
        "immutable_run_receipt_json": str(immutable_run_receipt_json_path),
        "immutable_run_receipt_md": str(immutable_run_receipt_md_path),
        "handoff_receipt_comparison_json": str(handoff_receipt_comparison_json_path),
        "handoff_receipt_comparison_md": str(handoff_receipt_comparison_md_path),
        "handoff_rerun_audit_gate_json": str(handoff_rerun_audit_gate_json_path),
        "handoff_rerun_audit_gate_md": str(handoff_rerun_audit_gate_md_path),
        "commercial_evidence_lineage_dashboard_json": str(commercial_evidence_lineage_json_path),
        "commercial_evidence_lineage_dashboard_md": str(commercial_evidence_lineage_md_path),
        "commercial_lineage_reviewer_signoff_packet_json": str(commercial_lineage_signoff_json_path),
        "commercial_lineage_reviewer_signoff_packet_md": str(commercial_lineage_signoff_md_path),
        "commercial_closure_acceptance_ledger_json": str(commercial_closure_ledger_json_path),
        "commercial_closure_acceptance_ledger_md": str(commercial_closure_ledger_md_path),
        "commercial_audit_event_stream_json": str(commercial_audit_event_stream_json_path),
        "commercial_audit_event_stream_md": str(commercial_audit_event_stream_md_path),
        "commercial_audit_exports_json": str(commercial_audit_exports_json_path),
        "commercial_audit_exports_md": str(commercial_audit_exports_md_path),
        "commercial_audit_ledger_csv": str(commercial_audit_ledger_csv_path),
        "jira_issue_import_json": str(jira_issue_import_json_path),
        "linear_issue_import_json": str(linear_issue_import_json_path),
        "reviewer_packet_export_md": str(reviewer_packet_export_md_path),
        "commercial_audit_import_gate_json": str(commercial_audit_import_gate_json_path),
        "commercial_audit_import_gate_md": str(commercial_audit_import_gate_md_path),
        "commercial_external_tracker_reconciliation_json": str(commercial_external_tracker_reconciliation_json_path),
        "commercial_external_tracker_reconciliation_md": str(commercial_external_tracker_reconciliation_md_path),
        "external_tracker_closure_sync_policy_json": str(external_tracker_closure_sync_policy_json_path),
        "external_tracker_closure_sync_policy_md": str(external_tracker_closure_sync_policy_md_path),
        "external_tracker_sync_payloads_json": str(external_tracker_sync_payloads_json_path),
        "external_tracker_sync_payloads_md": str(external_tracker_sync_payloads_md_path),
        "external_tracker_sync_payload_gate_json": str(external_tracker_sync_payload_gate_json_path),
        "external_tracker_sync_payload_gate_md": str(external_tracker_sync_payload_gate_md_path),
        "external_tracker_sync_receipt_ledger_json": str(external_tracker_sync_receipt_ledger_json_path),
        "external_tracker_sync_receipt_ledger_md": str(external_tracker_sync_receipt_ledger_md_path),
        "phase94_bug_discovery_expansion_json": str(bug_discovery_expansion_path),
    }
    _write_json(bug_discovery_expansion_path, bug_discovery_expansion)
    _write_json(preflight_path, preflight)
    _write_json(capability_matrix_path, runtime_capability_matrix)
    _write_json(onboarding_remediation_json_path, onboarding_remediation_kit)
    onboarding_remediation_md_path.write_text(render_onboarding_remediation_markdown(onboarding_remediation_kit), encoding="utf-8")
    report["runtime_execution_runbook"] = build_runtime_execution_runbook(report)
    report["summary"]["runtime_runbook_step_count"] = len(report["runtime_execution_runbook"].get("steps") or [])
    _write_json(runtime_runbook_json_path, report["runtime_execution_runbook"])
    runtime_runbook_md_path.write_text(render_runtime_execution_runbook_markdown(report["runtime_execution_runbook"]), encoding="utf-8")
    report["runtime_evidence_carry_forward"] = _build_runtime_evidence_carry_forward(config, runtime_rerun_selection)
    report["summary"]["runtime_carry_forward_status"] = report["runtime_evidence_carry_forward"].get("status")
    report["summary"]["runtime_carry_forward_candidate_count"] = len(report["runtime_evidence_carry_forward"].get("carried_forward_candidate_ids") or [])
    report["summary"]["runtime_carry_forward_reproduction_count"] = report["runtime_evidence_carry_forward"].get("carried_forward_reproduction_count", 0)
    report["summary"]["runtime_carry_forward_probe_ledger_count"] = report["runtime_evidence_carry_forward"].get("carried_forward_probe_ledger_count", 0)
    _write_json(runtime_carry_forward_json_path, report["runtime_evidence_carry_forward"])
    runtime_carry_forward_md_path.write_text(_render_runtime_evidence_carry_forward_markdown(report["runtime_evidence_carry_forward"]), encoding="utf-8")
    report["runtime_evidence_readiness_sla_gate"] = build_runtime_evidence_readiness_sla_gate(report)
    report["summary"]["runtime_evidence_readiness_score"] = report["runtime_evidence_readiness_sla_gate"].get("commercial_readiness_score", 0)
    report["summary"]["runtime_evidence_sla_gate_passed"] = bool(report["runtime_evidence_readiness_sla_gate"].get("sla_gate_passed"))
    _write_json(readiness_sla_json_path, report["runtime_evidence_readiness_sla_gate"])
    readiness_sla_md_path.write_text(render_runtime_evidence_readiness_markdown(report["runtime_evidence_readiness_sla_gate"]), encoding="utf-8")
    report["runtime_evidence_scoreboard"] = _build_runtime_evidence_scoreboard(report)
    report["summary"]["runtime_execution_integrity_score"] = report["runtime_evidence_scoreboard"].get("execution_integrity_score", 0)
    report["summary"]["runtime_scoreboard_binding_success_rate"] = report["runtime_evidence_scoreboard"].get("runtime_binding_success_rate", 0)
    report["summary"]["runtime_scoreboard_fixture_setup_success_rate"] = report["runtime_evidence_scoreboard"].get("fixture_setup_success_rate", 0)
    report["summary"]["runtime_scoreboard_cleanup_success_rate"] = report["runtime_evidence_scoreboard"].get("cleanup_success_rate", 0)
    report["summary"]["runtime_scoreboard_snapshot_success_rate"] = report["runtime_evidence_scoreboard"].get("snapshot_success_rate", 0)
    report["summary"]["runtime_scoreboard_execution_coverage_rate"] = report["runtime_evidence_scoreboard"].get("execution_coverage_rate", 0)
    report["summary"]["runtime_scoreboard_target_response_rate"] = report["runtime_evidence_scoreboard"].get("target_response_rate", 0)
    report["summary"]["runtime_scoreboard_oracle_resolution_rate"] = report["runtime_evidence_scoreboard"].get("oracle_resolution_rate", 0)
    report["summary"]["runtime_scoreboard_top_gap_count"] = len(report["runtime_evidence_scoreboard"].get("top_failure_or_gap_reasons") or {})
    report["summary"]["runtime_scoreboard_recommended_action_count"] = len(report["runtime_evidence_scoreboard"].get("recommended_next_actions") or [])
    maturity = report["runtime_evidence_scoreboard"].get("evidence_maturity") if isinstance(report["runtime_evidence_scoreboard"].get("evidence_maturity"), dict) else {}
    report["summary"]["runtime_scoreboard_evidence_maturity_level"] = maturity.get("level")
    report["summary"]["runtime_scoreboard_customer_ready"] = bool(maturity.get("customer_ready"))
    _write_json(runtime_scoreboard_json_path, report["runtime_evidence_scoreboard"])
    runtime_scoreboard_md_path.write_text(_render_runtime_evidence_scoreboard_markdown(report["runtime_evidence_scoreboard"]), encoding="utf-8")
    report["runtime_evidence_probe_ledger"] = _build_runtime_evidence_probe_ledger(report)
    report["summary"]["runtime_probe_ledger_entry_count"] = report["runtime_evidence_probe_ledger"].get("entry_count", 0)
    report["summary"]["runtime_probe_ledger_customer_ready_count"] = report["runtime_evidence_probe_ledger"].get("customer_ready_probe_count", 0)
    report["summary"]["runtime_probe_ledger_carried_forward_count"] = report["runtime_evidence_probe_ledger"].get("carried_forward_probe_count", 0)
    report["summary"]["runtime_probe_ledger_evidence_gap_count"] = report["runtime_evidence_probe_ledger"].get("evidence_gap_probe_count", 0)
    _write_json(runtime_probe_ledger_json_path, report["runtime_evidence_probe_ledger"])
    runtime_probe_ledger_md_path.write_text(_render_runtime_evidence_probe_ledger_markdown(report["runtime_evidence_probe_ledger"]), encoding="utf-8")
    report["runtime_customer_reproduction_pack"] = _build_runtime_customer_reproduction_pack(report)
    report["summary"]["runtime_reproduction_pack_finding_count"] = report["runtime_customer_reproduction_pack"].get("finding_count", 0)
    report["summary"]["runtime_reproduction_pack_customer_ready_count"] = report["runtime_customer_reproduction_pack"].get("customer_ready_reproduction_count", 0)
    report["summary"]["runtime_reproduction_pack_carried_forward_count"] = report["runtime_customer_reproduction_pack"].get("carried_forward_reproduction_count", 0)
    report["summary"]["runtime_reproduction_pack_status"] = report["runtime_customer_reproduction_pack"].get("status")
    _write_json(runtime_repro_pack_json_path, report["runtime_customer_reproduction_pack"])
    runtime_repro_pack_md_path.write_text(_render_runtime_customer_reproduction_pack_markdown(report["runtime_customer_reproduction_pack"]), encoding="utf-8")
    report["runtime_evidence_remediation_plan"] = _build_runtime_evidence_remediation_plan(report)
    report["summary"]["runtime_remediation_plan_status"] = report["runtime_evidence_remediation_plan"].get("status")
    report["summary"]["runtime_remediation_plan_p0_group_count"] = report["runtime_evidence_remediation_plan"].get("p0_group_count", 0)
    report["summary"]["runtime_remediation_plan_queued_candidate_count"] = report["runtime_evidence_remediation_plan"].get("queued_candidate_count", 0)
    _write_json(runtime_remediation_plan_json_path, report["runtime_evidence_remediation_plan"])
    runtime_remediation_plan_md_path.write_text(_render_runtime_evidence_remediation_plan_markdown(report["runtime_evidence_remediation_plan"]), encoding="utf-8")
    report["runtime_evidence_progress_delta"] = _build_runtime_evidence_progress_delta(config, report)
    report["summary"]["runtime_progress_delta_status"] = report["runtime_evidence_progress_delta"].get("status")
    report["summary"]["runtime_progress_delta_regression_count"] = len(report["runtime_evidence_progress_delta"].get("regressions") or [])
    report["summary"]["runtime_progress_delta_resolved_gap_count"] = len(report["runtime_evidence_progress_delta"].get("resolved_gap_types") or [])
    report["summary"]["runtime_progress_delta_new_gap_count"] = len(report["runtime_evidence_progress_delta"].get("new_gap_types") or [])
    _write_json(runtime_progress_delta_json_path, report["runtime_evidence_progress_delta"])
    runtime_progress_delta_md_path.write_text(_render_runtime_evidence_progress_delta_markdown(report["runtime_evidence_progress_delta"]), encoding="utf-8")
    report["runtime_evidence_promotion_gate"] = _build_runtime_evidence_promotion_gate(report)
    report["summary"]["runtime_promotion_gate_status"] = report["runtime_evidence_promotion_gate"].get("status")
    report["summary"]["runtime_promotion_gate_ready"] = bool(report["runtime_evidence_promotion_gate"].get("promotion_ready"))
    report["summary"]["runtime_promotion_gate_blocker_count"] = len(report["runtime_evidence_promotion_gate"].get("blockers") or [])
    report["summary"]["runtime_promotion_gate_approved_candidate_count"] = report["runtime_evidence_promotion_gate"].get("approved_customer_ready_candidate_count", 0)
    _write_json(runtime_promotion_gate_json_path, report["runtime_evidence_promotion_gate"])
    runtime_promotion_gate_md_path.write_text(_render_runtime_evidence_promotion_gate_markdown(report["runtime_evidence_promotion_gate"]), encoding="utf-8")
    report["runtime_evidence_customer_delivery_manifest"] = _build_runtime_evidence_customer_delivery_manifest(report)
    report["summary"]["runtime_delivery_manifest_status"] = report["runtime_evidence_customer_delivery_manifest"].get("status")
    report["summary"]["runtime_delivery_manifest_ready"] = bool(report["runtime_evidence_customer_delivery_manifest"].get("customer_ready"))
    report["summary"]["runtime_delivery_manifest_hashed_required_artifact_count"] = report["runtime_evidence_customer_delivery_manifest"].get("hashed_required_artifact_count", 0)
    report["summary"]["runtime_delivery_manifest_missing_required_artifact_count"] = report["runtime_evidence_customer_delivery_manifest"].get("missing_required_artifact_count", 0)
    report["summary"]["runtime_delivery_manifest_baseline_id"] = report["runtime_evidence_customer_delivery_manifest"].get("delivery_baseline_id", "")
    _write_json(runtime_delivery_manifest_json_path, report["runtime_evidence_customer_delivery_manifest"])
    runtime_delivery_manifest_md_path.write_text(_render_runtime_evidence_customer_delivery_manifest_markdown(report["runtime_evidence_customer_delivery_manifest"]), encoding="utf-8")
    report["runtime_evidence_delivery_manifest_verification"] = _build_runtime_evidence_delivery_manifest_verification(config, report)
    report["summary"]["runtime_delivery_manifest_verification_status"] = report["runtime_evidence_delivery_manifest_verification"].get("status")
    report["summary"]["runtime_delivery_manifest_verified"] = bool(report["runtime_evidence_delivery_manifest_verification"].get("verified"))
    report["summary"]["runtime_delivery_manifest_verification_failed_required_artifact_count"] = report["runtime_evidence_delivery_manifest_verification"].get("failed_required_artifact_count", 0)
    _write_json(runtime_delivery_manifest_verification_json_path, report["runtime_evidence_delivery_manifest_verification"])
    runtime_delivery_manifest_verification_md_path.write_text(_render_runtime_evidence_delivery_manifest_verification_markdown(report["runtime_evidence_delivery_manifest_verification"]), encoding="utf-8")
    report["runtime_sla_execution_policy"] = build_runtime_sla_execution_policy(report)
    report["summary"]["runtime_sla_must_run_count"] = report["runtime_sla_execution_policy"].get("must_run_for_sla_count", 0)
    report["summary"]["runtime_sla_blocked_before_sla_count"] = report["runtime_sla_execution_policy"].get("blocked_before_sla_count", 0)
    _write_json(runtime_sla_policy_json_path, report["runtime_sla_execution_policy"])
    runtime_sla_policy_md_path.write_text(render_runtime_sla_execution_policy_markdown(report["runtime_sla_execution_policy"]), encoding="utf-8")
    report["runtime_sla_gap_prioritizer"] = build_runtime_sla_gap_prioritizer(report)
    report["summary"]["runtime_sla_gap_prioritized_action_count"] = report["runtime_sla_gap_prioritizer"].get("action_count", 0)
    report["summary"]["runtime_sla_estimated_score_after_top_actions"] = report["runtime_sla_gap_prioritizer"].get("estimated_readiness_score_after_top_actions", 0)
    _write_json(runtime_sla_gap_json_path, report["runtime_sla_gap_prioritizer"])
    runtime_sla_gap_md_path.write_text(render_runtime_sla_gap_prioritizer_markdown(report["runtime_sla_gap_prioritizer"]), encoding="utf-8")
    report["onboarding_patch_safety_validation"] = validate_onboarding_patch_safety(report)
    report["summary"]["onboarding_patch_safety_issue_count"] = report["onboarding_patch_safety_validation"].get("issue_count", 0)
    report["summary"]["onboarding_patch_safe_to_send"] = bool(report["onboarding_patch_safety_validation"].get("safe_to_send_to_customer"))
    _write_json(onboarding_patch_safety_json_path, report["onboarding_patch_safety_validation"])
    onboarding_patch_safety_md_path.write_text(render_onboarding_patch_safety_markdown(report["onboarding_patch_safety_validation"]), encoding="utf-8")
    report["write_sandbox_approval_packet"] = build_write_sandbox_approval_packet(report)
    report["summary"]["write_sandbox_approval_required"] = bool(report["write_sandbox_approval_packet"].get("write_approval_required"))
    report["summary"]["write_sandbox_approval_ready"] = bool(report["write_sandbox_approval_packet"].get("ready_for_customer_approval"))
    _write_json(write_sandbox_approval_json_path, report["write_sandbox_approval_packet"])
    write_sandbox_approval_md_path.write_text(render_write_sandbox_approval_markdown(report["write_sandbox_approval_packet"]), encoding="utf-8")
    report = link_reproduction_assets(report)
    previous_report = _load_previous_execution_report(config)
    report = attach_fix_verification_loop(report, previous_report=previous_report)
    report = apply_lifecycle_registry(report, previous_report=previous_report)
    fix_index = report.get("fix_verification_loop_index") or {}
    lifecycle_index = report.get("finding_lifecycle_registry") or {}
    report["summary"]["fix_verification_required_count"] = fix_index.get("verification_required_finding_count", 0)
    report["summary"]["closed_by_rerun_count"] = fix_index.get("closed_by_rerun_count", 0)
    report["summary"]["still_open_after_rerun_count"] = fix_index.get("still_open_after_rerun_count", 0)
    report["summary"]["reopened_finding_count"] = fix_index.get("reopened_finding_count", 0)
    report["summary"]["stable_lifecycle_match_count"] = lifecycle_index.get("stable_match_count", 0)
    remediation_artifact = build_remediation_verification_artifact(report)
    report["remediation_verification_artifact"] = remediation_artifact
    report["summary"]["remediation_work_item_count"] = remediation_artifact.get("work_item_count", 0)
    _write_json(remediation_json_path, remediation_artifact)
    remediation_md_path.write_text(render_remediation_markdown(remediation_artifact), encoding="utf-8")
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["summary"]["commercial_handoff_status"] = report["commercial_handoff_bundle"].get("status")
    report["summary"]["commercial_handoff_blocker_count"] = (report["commercial_handoff_bundle"].get("executive_summary") or {}).get("handoff_blocker_count", 0)
    report["summary"]["commercial_handoff_artifact_count"] = len(report["commercial_handoff_bundle"].get("artifact_manifest") or [])
    _write_json(commercial_handoff_json_path, report["commercial_handoff_bundle"])
    commercial_handoff_md_path.write_text(render_commercial_handoff_markdown(report["commercial_handoff_bundle"]), encoding="utf-8")
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)
    report["summary"]["commercial_handoff_acceptance_status"] = report["commercial_handoff_acceptance_gate"].get("status")
    report["summary"]["commercial_handoff_acceptance_gate_passed"] = bool(report["commercial_handoff_acceptance_gate"].get("acceptance_gate_passed"))
    report["summary"]["commercial_handoff_acceptance_violation_count"] = report["commercial_handoff_acceptance_gate"].get("violation_count", 0)
    _write_json(commercial_handoff_acceptance_json_path, report["commercial_handoff_acceptance_gate"])
    commercial_handoff_acceptance_md_path.write_text(render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]), encoding="utf-8")
    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["summary"]["commercial_handoff_secret_audit_status"] = report["commercial_handoff_secret_audit"].get("status")
    report["summary"]["commercial_handoff_secret_audit_issue_count"] = report["commercial_handoff_secret_audit"].get("issue_count", 0)
    report["summary"]["commercial_handoff_safe_for_customer"] = bool(report["commercial_handoff_secret_audit"].get("safe_for_customer_handoff"))
    _write_json(handoff_secret_audit_json_path, report["commercial_handoff_secret_audit"])
    handoff_secret_audit_md_path.write_text(render_handoff_secret_audit_markdown(report["commercial_handoff_secret_audit"]), encoding="utf-8")
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(report, report["commercial_handoff_secret_audit"])
    report["summary"]["commercial_handoff_secret_redaction_plan_status"] = report["commercial_handoff_secret_redaction_plan"].get("status")
    report["summary"]["commercial_handoff_secret_redaction_action_count"] = report["commercial_handoff_secret_redaction_plan"].get("action_count", 0)
    _write_json(handoff_secret_redaction_plan_json_path, report["commercial_handoff_secret_redaction_plan"])
    handoff_secret_redaction_plan_md_path.write_text(render_handoff_secret_redaction_plan_markdown(report["commercial_handoff_secret_redaction_plan"]), encoding="utf-8")
    report["commercial_handoff_redacted_runtime_evidence"] = build_handoff_redacted_runtime_evidence_pack(
        report,
        report["commercial_handoff_secret_audit"],
        report["commercial_handoff_secret_redaction_plan"],
    )
    report["summary"]["commercial_handoff_redacted_runtime_evidence_status"] = report["commercial_handoff_redacted_runtime_evidence"].get("status")
    report["summary"]["commercial_handoff_redacted_runtime_evidence_safe"] = bool(report["commercial_handoff_redacted_runtime_evidence"].get("safe_for_customer_handoff_after_redaction"))
    report["summary"]["commercial_handoff_redacted_runtime_evidence_action_count"] = report["commercial_handoff_redacted_runtime_evidence"].get("applied_action_count", 0)
    _write_json(handoff_redacted_runtime_evidence_json_path, report["commercial_handoff_redacted_runtime_evidence"])
    handoff_redacted_runtime_evidence_md_path.write_text(render_handoff_redacted_runtime_evidence_markdown(report["commercial_handoff_redacted_runtime_evidence"]), encoding="utf-8")

    # Refresh handoff artifacts after secret audit/redaction planning so the
    # customer-facing handoff bundle and acceptance gate reflect P0 redaction
    # blockers discovered late in the reporting pipeline.
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["summary"]["commercial_handoff_status"] = report["commercial_handoff_bundle"].get("status")
    report["summary"]["commercial_handoff_blocker_count"] = (report["commercial_handoff_bundle"].get("executive_summary") or {}).get("handoff_blocker_count", 0)
    report["summary"]["commercial_handoff_artifact_count"] = len(report["commercial_handoff_bundle"].get("artifact_manifest") or [])
    _write_json(commercial_handoff_json_path, report["commercial_handoff_bundle"])
    commercial_handoff_md_path.write_text(render_commercial_handoff_markdown(report["commercial_handoff_bundle"]), encoding="utf-8")
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)
    report["summary"]["commercial_handoff_acceptance_status"] = report["commercial_handoff_acceptance_gate"].get("status")
    report["summary"]["commercial_handoff_acceptance_gate_passed"] = bool(report["commercial_handoff_acceptance_gate"].get("acceptance_gate_passed"))
    report["summary"]["commercial_handoff_acceptance_violation_count"] = report["commercial_handoff_acceptance_gate"].get("violation_count", 0)
    _write_json(commercial_handoff_acceptance_json_path, report["commercial_handoff_acceptance_gate"])
    commercial_handoff_acceptance_md_path.write_text(render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]), encoding="utf-8")

    report["handoff_archive_manifest"] = build_handoff_archive_manifest(report)
    report["immutable_run_receipt"] = report["handoff_archive_manifest"].get("immutable_run_receipt") or {}
    report["summary"]["handoff_archive_manifest_status"] = report["handoff_archive_manifest"].get("status")
    report["summary"]["handoff_archive_hashed_artifact_count"] = report["handoff_archive_manifest"].get("hashed_artifact_count", 0)
    report["summary"]["handoff_archive_missing_required_artifact_count"] = report["handoff_archive_manifest"].get("missing_required_artifact_count", 0)
    report["summary"]["immutable_run_receipt_status"] = report["immutable_run_receipt"].get("receipt_status")
    report["summary"]["immutable_run_lineage_id"] = report["immutable_run_receipt"].get("run_lineage_id", "")
    _write_json(handoff_archive_manifest_json_path, report["handoff_archive_manifest"])
    handoff_archive_manifest_md_path.write_text(render_handoff_archive_manifest_markdown(report["handoff_archive_manifest"]), encoding="utf-8")
    _write_json(immutable_run_receipt_json_path, report["immutable_run_receipt"])
    immutable_run_receipt_md_path.write_text(render_immutable_run_receipt_markdown(report["immutable_run_receipt"]), encoding="utf-8")
    report["handoff_receipt_comparison"] = compare_immutable_run_receipts(report, previous_report=previous_report)
    report["summary"]["handoff_receipt_comparison_status"] = report["handoff_receipt_comparison"].get("status")
    report["summary"]["handoff_receipt_change_count"] = report["handoff_receipt_comparison"].get("change_count", 0)
    report["summary"]["handoff_receipt_lineage_match"] = bool(report["handoff_receipt_comparison"].get("lineage_match"))
    _write_json(handoff_receipt_comparison_json_path, report["handoff_receipt_comparison"])
    handoff_receipt_comparison_md_path.write_text(render_handoff_receipt_comparison_markdown(report["handoff_receipt_comparison"]), encoding="utf-8")
    report["handoff_rerun_audit_gate"] = build_handoff_rerun_audit_gate(report)
    report["summary"]["handoff_rerun_audit_status"] = report["handoff_rerun_audit_gate"].get("status")
    report["summary"]["handoff_rerun_closure_allowed"] = bool(report["handoff_rerun_audit_gate"].get("closure_verification_allowed"))
    report["summary"]["handoff_rerun_audit_blocker_count"] = report["handoff_rerun_audit_gate"].get("blocker_count", 0)
    _write_json(handoff_rerun_audit_gate_json_path, report["handoff_rerun_audit_gate"])
    handoff_rerun_audit_gate_md_path.write_text(render_handoff_rerun_audit_gate_markdown(report["handoff_rerun_audit_gate"]), encoding="utf-8")
    report["commercial_evidence_lineage_dashboard"] = build_commercial_evidence_lineage_dashboard(report)
    report["summary"]["commercial_evidence_lineage_dashboard_status"] = report["commercial_evidence_lineage_dashboard"].get("status")
    report["summary"]["commercial_evidence_lineage_closure_claim_state"] = report["commercial_evidence_lineage_dashboard"].get("closure_claim_state")
    report["summary"]["commercial_evidence_lineage_changed_hash_count"] = report["commercial_evidence_lineage_dashboard"].get("changed_or_missing_hash_count", 0)
    _write_json(commercial_evidence_lineage_json_path, report["commercial_evidence_lineage_dashboard"])
    commercial_evidence_lineage_md_path.write_text(render_commercial_evidence_lineage_dashboard_markdown(report["commercial_evidence_lineage_dashboard"]), encoding="utf-8")
    report["commercial_lineage_reviewer_signoff_packet"] = build_commercial_lineage_reviewer_signoff_packet(report)
    report["summary"]["commercial_lineage_reviewer_signoff_status"] = report["commercial_lineage_reviewer_signoff_packet"].get("status")
    report["summary"]["commercial_lineage_reviewer_signoff_required"] = bool(report["commercial_lineage_reviewer_signoff_packet"].get("signoff_required"))
    report["summary"]["commercial_lineage_reviewer_signoff_item_count"] = report["commercial_lineage_reviewer_signoff_packet"].get("signoff_item_count", 0)
    _write_json(commercial_lineage_signoff_json_path, report["commercial_lineage_reviewer_signoff_packet"])
    commercial_lineage_signoff_md_path.write_text(render_commercial_lineage_reviewer_signoff_markdown(report["commercial_lineage_reviewer_signoff_packet"]), encoding="utf-8")
    report["commercial_closure_acceptance_ledger"] = build_commercial_closure_acceptance_ledger(report)
    report["summary"]["commercial_closure_acceptance_ledger_status"] = report["commercial_closure_acceptance_ledger"].get("status")
    report["summary"]["commercial_closure_acceptance_ledger_entry_count"] = report["commercial_closure_acceptance_ledger"].get("ledger_entry_count", 0)
    _write_json(commercial_closure_ledger_json_path, report["commercial_closure_acceptance_ledger"])
    commercial_closure_ledger_md_path.write_text(render_commercial_closure_acceptance_ledger_markdown(report["commercial_closure_acceptance_ledger"]), encoding="utf-8")
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)
    report["summary"]["commercial_audit_event_stream_status"] = report["commercial_audit_event_stream"].get("status")
    report["summary"]["commercial_audit_event_count"] = report["commercial_audit_event_stream"].get("event_count", 0)
    _write_json(commercial_audit_event_stream_json_path, report["commercial_audit_event_stream"])
    commercial_audit_event_stream_md_path.write_text(render_commercial_audit_event_stream_markdown(report["commercial_audit_event_stream"]), encoding="utf-8")
    report["commercial_audit_export_adapters"] = build_commercial_audit_export_adapters(report)
    report["summary"]["commercial_audit_export_status"] = report["commercial_audit_export_adapters"].get("status")
    report["summary"]["commercial_audit_jira_issue_count"] = report["commercial_audit_export_adapters"].get("jira_issue_count", 0)
    report["summary"]["commercial_audit_linear_issue_count"] = report["commercial_audit_export_adapters"].get("linear_issue_count", 0)
    report["summary"]["commercial_audit_csv_row_count"] = report["commercial_audit_export_adapters"].get("csv_row_count", 0)
    report["summary"]["commercial_closure_external_tracking_key_count"] = report["commercial_audit_export_adapters"].get("closure_tracking_key_count", 0)
    _write_json(commercial_audit_exports_json_path, report["commercial_audit_export_adapters"])
    commercial_audit_exports_md_path.write_text(render_commercial_audit_exports_markdown(report["commercial_audit_export_adapters"]), encoding="utf-8")
    commercial_audit_ledger_csv_path.write_text(render_csv_audit_ledger(report["commercial_audit_export_adapters"]), encoding="utf-8")
    _write_json(jira_issue_import_json_path, report["commercial_audit_export_adapters"].get("jira_issue_import") or [])
    _write_json(linear_issue_import_json_path, report["commercial_audit_export_adapters"].get("linear_issue_import") or [])
    reviewer_packet_export_md_path.write_text(str(report["commercial_audit_export_adapters"].get("reviewer_packet_markdown") or ""), encoding="utf-8")
    report["commercial_audit_export_import_gate"] = build_commercial_audit_export_import_gate(report)
    report["summary"]["commercial_audit_import_gate_status"] = report["commercial_audit_export_import_gate"].get("status")
    report["summary"]["commercial_audit_import_ready"] = bool(report["commercial_audit_export_import_gate"].get("import_ready"))
    report["summary"]["commercial_audit_import_violation_count"] = report["commercial_audit_export_import_gate"].get("violation_count", 0)
    report["summary"]["commercial_audit_import_placeholder_count"] = report["commercial_audit_export_import_gate"].get("placeholder_count", 0)
    _write_json(commercial_audit_import_gate_json_path, report["commercial_audit_export_import_gate"])
    commercial_audit_import_gate_md_path.write_text(render_commercial_audit_import_gate_markdown(report["commercial_audit_export_import_gate"]), encoding="utf-8")
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)
    report["summary"]["commercial_external_tracker_reconciliation_status"] = report["commercial_external_tracker_reconciliation"].get("status")
    report["summary"]["commercial_external_tracker_reconciliation_entry_count"] = report["commercial_external_tracker_reconciliation"].get("entry_count", 0)
    _write_json(commercial_external_tracker_reconciliation_json_path, report["commercial_external_tracker_reconciliation"])
    commercial_external_tracker_reconciliation_md_path.write_text(render_commercial_external_tracker_reconciliation_markdown(report["commercial_external_tracker_reconciliation"]), encoding="utf-8")
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)
    report["summary"]["external_tracker_closure_sync_status"] = report["external_tracker_closure_sync_policy"].get("status")
    report["summary"]["external_tracker_closure_sync_policy_count"] = report["external_tracker_closure_sync_policy"].get("sync_policy_count", 0)
    report["summary"]["external_tracker_closure_sync_ready_count"] = (report["external_tracker_closure_sync_policy"].get("status_counts") or {}).get("sync_ready_to_mark_resolved", 0)
    _write_json(external_tracker_closure_sync_policy_json_path, report["external_tracker_closure_sync_policy"])
    external_tracker_closure_sync_policy_md_path.write_text(render_external_tracker_closure_sync_policy_markdown(report["external_tracker_closure_sync_policy"]), encoding="utf-8")
    report["external_tracker_sync_payloads"] = build_external_tracker_sync_payloads(report)
    report["summary"]["external_tracker_sync_payload_status"] = report["external_tracker_sync_payloads"].get("status")
    report["summary"]["external_tracker_jira_transition_payload_count"] = report["external_tracker_sync_payloads"].get("jira_transition_payload_count", 0)
    report["summary"]["external_tracker_linear_update_payload_count"] = report["external_tracker_sync_payloads"].get("linear_update_payload_count", 0)
    report["summary"]["external_tracker_sync_hold_item_count"] = report["external_tracker_sync_payloads"].get("hold_item_count", 0)
    _write_json(external_tracker_sync_payloads_json_path, report["external_tracker_sync_payloads"])
    external_tracker_sync_payloads_md_path.write_text(render_external_tracker_sync_payloads_markdown(report["external_tracker_sync_payloads"]), encoding="utf-8")
    report["external_tracker_sync_payload_gate"] = validate_external_tracker_sync_payloads(report)
    report["summary"]["external_tracker_sync_payload_gate_status"] = report["external_tracker_sync_payload_gate"].get("status")
    report["summary"]["external_tracker_sync_payload_import_ready"] = bool(report["external_tracker_sync_payload_gate"].get("payload_import_ready"))
    report["summary"]["external_tracker_sync_payload_gate_violation_count"] = report["external_tracker_sync_payload_gate"].get("violation_count", 0)
    _write_json(external_tracker_sync_payload_gate_json_path, report["external_tracker_sync_payload_gate"])
    external_tracker_sync_payload_gate_md_path.write_text(render_external_tracker_sync_payload_gate_markdown(report["external_tracker_sync_payload_gate"]), encoding="utf-8")
    report["external_tracker_sync_receipt_ledger"] = build_external_tracker_sync_receipt_ledger(report)
    report["summary"]["external_tracker_sync_receipt_status"] = report["external_tracker_sync_receipt_ledger"].get("status")
    report["summary"]["external_tracker_sync_receipt_entry_count"] = report["external_tracker_sync_receipt_ledger"].get("sync_receipt_entry_count", 0)
    report["summary"]["external_tracker_sync_confirmed_count"] = (report["external_tracker_sync_receipt_ledger"].get("receipt_status_counts") or {}).get("sync_applied_confirmed", 0)
    _write_json(external_tracker_sync_receipt_ledger_json_path, report["external_tracker_sync_receipt_ledger"])
    external_tracker_sync_receipt_ledger_md_path.write_text(render_external_tracker_sync_receipt_ledger_markdown(report["external_tracker_sync_receipt_ledger"]), encoding="utf-8")
    _write_json(report_path, report)
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    ps1_path.write_text(_render_repro_ps1(report), encoding="utf-8")
    pytest_path.write_text(_render_pytest(report), encoding="utf-8")
    return report
