"""run_real_project_discovery: main discovery orchestration."""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403
from ._common import _html_escape, _join_url, _load_json, _read_text, _safe_project_id, _write_json  # noqa: F401
from ._helpers import *  # noqa: F401,F403
from ._helpers import _append_adapter_issue, _apply_browser_health_probe_policy, _augment_risk_plan_with_browser_health, _build_discovery_funnel, _fetch_json_or_text, _live_mode_or_plan, _login, _safe_rate, _status_suspicious, generate_history_informed_probes, generate_real_project_probes  # noqa: F401
from ._reporting import _fix_for_risk, _impact_for_risk, _render_bug_drafts, render_real_project_report  # noqa: F401


def run_real_project_discovery(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)

    # ── Phase78A: Unified Safe HTTP Transport ──
    from ai_test_asset_center.unified_http_transport import (
        SafeHttpTransport, ExecutionPolicy, set_global_transport,
    )
    env = str(cfg.get('environment') or cfg.get('target_environment') or '')
    policy = ExecutionPolicy(
        environment=env,
        allow_destructive=bool(cfg.get('allow_destructive_tests')),
    )
    transport = SafeHttpTransport(
        policy=policy,
        base_url=str(cfg.get('base_url') or ''),
        default_timeout=int(cfg.get('request_timeout_seconds') or 10),
    )
    set_global_transport(transport)
    # Replace _fetch_json_or_text with transport adapter
    _fetch_json_or_text = transport.fetch_json_or_text

    if policy.is_production:
        return {
            'status': 'BLOCKED_BY_SAFETY',
            'project': project,
            'environment': env,
            'reason': 'Production environment: all HTTP requests blocked by unified SafeHttpTransport.',
            'allowed_actions': ['static_analysis', 'gap_report'],
            'blocked_actions': ['http_request', 'observer', 'polling', 'flow_executor'],
            'http_request_count': 0,
            'http_blocked_count': 0,
            'safety_boundary': {
                'safe_to_proceed': False,
                'violations': [{'rule': 'production_environment_declared', 'severity': 'BLOCKING',
                                'message': f"项目声明了生产环境: '{env}'。QualiBug 只能运行在测试/预发布环境。"}],
                'environment': env,
            },
            'metamorphic_differential_summary': {'execution_mode': 'plan_only'},
            'business_invariant_summary': {'execution_mode': 'plan_only'},
        }

    try:
        enterprise_testops_preflight = build_enterprise_testops_control_plane(project, root, {"target_environment": cfg.get("target_environment")})
    except Exception:
        enterprise_testops_preflight = None
    mode = str(cfg.get("discovery_mode") or "safe").lower()
    if mode == "aggressive" and not bool(cfg.get("allow_destructive_tests")):
        raise ValueError("aggressive 模式必须开启 allow_destructive_tests=true")
    accounts = _load_json(paths["input_dir"] / "test_accounts.json", {})
    safety = execution_safety_verdict(project, cfg, accounts)
    live_execution_allowed = bool(safety.get("safe_to_proceed"))
    onboarding = run_onboarding_check(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    timeout = int(cfg.get("request_timeout_seconds") or 10)
    business_adaptation_profile = build_business_adaptation_profile(project, root)
    try:
        multi_industry_business_profile = load_multi_industry_business_profile(project, root) or build_multi_industry_business_profile(project, root)
    except Exception:
        multi_industry_business_profile = None
    try:
        enterprise_business_knowledge_asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
        enterprise_business_knowledge_evidence_bundle = build_enterprise_knowledge_evidence_bundle(project, root)
    except Exception:
        enterprise_business_knowledge_asset = None
        enterprise_business_knowledge_evidence_bundle = None
    use_risk_plan = bool(cfg.get("use_risk_based_planner", True)) or str(os.environ.get("USE_RISK_BASED_PLANNER", "1")).lower() in {"1", "true", "yes"}
    risk_plan = None
    if use_risk_plan:
        try:
            from ai_test_asset_center.risk_based_probe_planner import build_risk_based_probe_plan, load_risk_based_probe_plan
            risk_plan = load_risk_based_probe_plan(project, root) or build_risk_based_probe_plan(project, root)
        except Exception:
            risk_plan = None
    try:
        universal_defect_mining = load_universal_defect_mining(project, root) or build_universal_defect_mining_profile(project, root)
    except Exception:
        universal_defect_mining = None
    try:
        business_outcome_profile = load_business_outcome_profile(project, root) or build_business_outcome_profile(project, root)
    except Exception:
        business_outcome_profile = None
    try:
        business_reconciliation_profile = load_business_reconciliation_profile(project, root) or build_business_reconciliation_profile(project, root)
    except Exception:
        business_reconciliation_profile = None
    try:
        business_invariant_profile = load_business_invariant_profile(project, root) or build_business_invariant_profile(project, root)
    except Exception:
        business_invariant_profile = None
    try:
        multi_source_reasoning_profile = load_multi_source_reasoning_profile(project, root) or build_multi_source_reasoning_profile(project, root)
    except Exception:
        multi_source_reasoning_profile = None
    try:
        business_lifecycle_profile = load_business_lifecycle_profile(project, root) or build_business_lifecycle_profile(project, root)
    except Exception:
        business_lifecycle_profile = None
    try:
        consistency_isolation_profile = load_consistency_isolation_profile(project, root) or build_consistency_isolation_profile(project, root)
    except Exception:
        consistency_isolation_profile = None
    try:
        metamorphic_differential_profile = load_metamorphic_differential_profile(project, root) or build_metamorphic_differential_profile(project, root)
    except Exception:
        metamorphic_differential_profile = None
    try:
        temporal_data_regression_profile = load_temporal_data_regression_profile(project, root) or build_temporal_data_regression_profile(project, root)
    except Exception:
        temporal_data_regression_profile = None
    try:
        business_causality_profile = load_business_causality_profile(project, root) or build_business_causality_profile(project, root)
    except Exception:
        business_causality_profile = None
    try:
        business_population_profile = load_business_population_constraint_profile(project, root) or build_business_population_constraint_profile(project, root)
    except Exception:
        business_population_profile = None
    try:
        business_event_chain_profile = load_business_event_chain_profile(project, root) or build_business_event_chain_profile(project, root)
    except Exception:
        business_event_chain_profile = None
    try:
        business_saga_compensation_profile = load_business_saga_compensation_profile(project, root) or build_business_saga_compensation_profile(project, root)
    except Exception:
        business_saga_compensation_profile = None
    try:
        confirmed_bug_flywheel_profile = build_confirmed_bug_flywheel(project, root)
    except Exception:
        confirmed_bug_flywheel_profile = None
    try:
        business_assurance_coverage_profile = load_business_assurance_coverage_profile(project, root) or build_business_assurance_coverage_profile(project, root)
    except Exception:
        business_assurance_coverage_profile = None
    try:
        from ai_test_asset_center.business_flow_execution import load_business_flow_execution_result
        business_flow_execution = load_business_flow_execution_result(project, root)
    except Exception:
        business_flow_execution = None
    try:
        from ai_test_asset_center.replay_evidence_sandbox import load_replay_evidence_sandbox
        replay_evidence_sandbox = load_replay_evidence_sandbox(project, root)
    except Exception:
        replay_evidence_sandbox = None
    api_contract_probes = generate_api_contract_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    browser_ui_replay_probes = generate_browser_ui_replay_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    frontend_runtime_probes = generate_frontend_runtime_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    frontend_ux_probes = generate_frontend_ux_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    compatibility_probes = generate_compatibility_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    performance_stability_probes = generate_performance_stability_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    openapi_static_security_probes = generate_openapi_static_security_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
    privacy_compliance_probes = generate_privacy_compliance_probes(
        openapi if isinstance(openapi, dict) else {},
        cfg,
        project,
        root,
        enterprise_testops_control_plane=enterprise_testops_preflight or {},
    )
    document_contract_fuzzing_probes = generate_document_contract_fuzzing_probes(project, root)
    if isinstance(risk_plan, dict) and isinstance(risk_plan.get("selected_probes"), list):
        probes = [dict(p) for p in risk_plan.get("selected_probes", [])]
    else:
        base_probes = generate_real_project_probes(openapi if isinstance(openapi, dict) else {}, cfg)
        adaptive_probes = generate_business_adaptive_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        multi_industry_probes = generate_multi_industry_business_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        enterprise_business_knowledge_probes = generate_enterprise_business_knowledge_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        enterprise_testops_probes = generate_enterprise_testops_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        try:
            from ai_test_asset_center.business_flow_graph import generate_business_flow_probes
            flow_probes = generate_business_flow_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        except Exception:
            flow_probes = []
        history_probes = generate_history_informed_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        universal_probes = generate_universal_defect_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_outcome_probes = generate_business_outcome_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_reconciliation_probes = generate_business_reconciliation_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_invariant_probes = generate_business_invariant_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        multi_source_reasoning_probes = generate_multi_source_reasoning_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_lifecycle_probes = generate_business_lifecycle_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        consistency_isolation_probes = generate_consistency_isolation_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        metamorphic_differential_probes = generate_metamorphic_differential_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        temporal_data_regression_probes = generate_temporal_data_regression_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_causality_probes = generate_business_causality_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_population_probes = generate_business_population_constraint_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_event_chain_probes = generate_business_event_chain_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_saga_compensation_probes = generate_business_saga_compensation_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        business_assurance_coverage_probes = generate_business_assurance_coverage_probes(openapi if isinstance(openapi, dict) else {}, cfg, project, root)
        seen_probe_keys: set[tuple[str, str, str, str]] = set()
        probes = []
        for probe in [
            *business_assurance_coverage_probes,
            *enterprise_testops_probes,
            *enterprise_business_knowledge_probes,
            *multi_industry_probes,
            *business_saga_compensation_probes,
            *business_event_chain_probes,
            *business_population_probes,
            *business_causality_probes,
            *temporal_data_regression_probes,
            *metamorphic_differential_probes,
            *consistency_isolation_probes,
            *business_lifecycle_probes,
            *multi_source_reasoning_probes,
            *business_invariant_probes,
            *business_reconciliation_probes,
            *business_outcome_probes,
            *universal_probes,
            *flow_probes,
            *adaptive_probes,
            *base_probes,
            *history_probes,
            *api_contract_probes,
            *openapi_static_security_probes,
            *privacy_compliance_probes,
            *document_contract_fuzzing_probes,
            *frontend_runtime_probes,
            *frontend_ux_probes,
            *compatibility_probes,
            *performance_stability_probes,
        ]:
            family = resolve_defect_family(probe)
            probe = {**probe, "defect_family": probe.get("defect_family") or family.get("family_id")}
            # Include actor/expected/scenario/context to avoid merging distinct
            # verification scenarios on the same endpoint (e.g. admin vs viewer vs anonymous).
            actor = str(probe.get("actor") or probe.get("actors", [""])[0] if isinstance(probe.get("actors"), list) else "")
            expected = str(probe.get("expected_behavior") or probe.get("expected") or "")[:80]
            scenario = str(probe.get("mutation_scenario") or probe.get("access_context") or "")
            key = (str(probe.get("risk_type")), str(probe.get("method")), str(probe.get("path")), str(probe.get("source")),
                   actor, expected, scenario)
            if key in seen_probe_keys:
                continue
            seen_probe_keys.add(key)
            probes.append(probe)
            if len(probes) >= int(cfg.get("max_probe_count") or 100):
                break
    supplemental_probes = [
        *api_contract_probes,
        *openapi_static_security_probes,
        *privacy_compliance_probes,
        *document_contract_fuzzing_probes,
        *browser_ui_replay_probes,
        *frontend_runtime_probes,
        *frontend_ux_probes,
        *compatibility_probes,
        *performance_stability_probes,
    ]
    seen_probe_keys: set[tuple[str, str, str, str, str, str, str]] = set()
    for probe in probes:
        if isinstance(probe, dict):
            actor = str(probe.get("actor") or (probe.get("actors", [""])[0] if isinstance(probe.get("actors"), list) else ""))
            expected = str(probe.get("expected_behavior") or probe.get("expected") or "")[:80]
            scenario = str(probe.get("mutation_scenario") or probe.get("access_context") or "")
            seen_probe_keys.add((
                str(probe.get("risk_type")), str(probe.get("method")), str(probe.get("path")),
                str(probe.get("source")), actor, expected, scenario,
            ))
    for probe in supplemental_probes:
        if not isinstance(probe, dict):
            continue
        family = resolve_defect_family(probe)
        probe = {**probe, "defect_family": probe.get("defect_family") or family.get("family_id")}
        actor = str(probe.get("actor") or (probe.get("actors", [""])[0] if isinstance(probe.get("actors"), list) else ""))
        expected = str(probe.get("expected_behavior") or probe.get("expected") or "")[:80]
        scenario = str(probe.get("mutation_scenario") or probe.get("access_context") or "")
        key = (str(probe.get("risk_type")), str(probe.get("method")), str(probe.get("path")), str(probe.get("source")),
               actor, expected, scenario)
        if key in seen_probe_keys:
            continue
        seen_probe_keys.add(key)
        probes.append(probe)

    browser_health = browser_ui_capability_health(cfg=cfg, project_id=project, root=root)
    probes = _apply_browser_health_probe_policy(probes, browser_health)
    risk_plan = _augment_risk_plan_with_browser_health(risk_plan, probes, browser_health)
    if isinstance(confirmed_bug_flywheel_profile, dict):
        probes = annotate_probes_with_confirmed_learning(probes, project, root, profile=confirmed_bug_flywheel_profile)
    probes = [{**probe, "defect_family": probe.get("defect_family") or resolve_defect_family(probe).get("family_id")} for probe in probes if isinstance(probe, dict)]

    normal = accounts.get("normal_user") or accounts.get("normal") or accounts.get("user") or {}
    admin = accounts.get("admin") or accounts.get("admin_user") or {}
    normal_login = _login(cfg, normal, timeout) if normal and live_execution_allowed else {"token": None, "response": {"skipped": True, "reason": "safety_boundary_blocked" if normal else "no_test_account"}}
    admin_login = _login(cfg, admin, timeout) if admin and live_execution_allowed else {"token": None, "response": {"skipped": True, "reason": "safety_boundary_blocked" if admin else "no_test_account"}}
    token_by_actor = {"normal_user": normal_login.get("token"), "admin": admin_login.get("token")}

    # ── Multi-module credential routing (enterprise) ──
    service_credential_manager = None
    service_token_by_actor: dict[str, dict[str, str | None]] = {}
    try:
        from ai_test_asset_center.enterprise_credential_manager import EnterpriseCredentialManager
        svc_mgr = EnterpriseCredentialManager(str(project), root)
        svc_mgr.load_legacy_fallback()
        svc_mgr.load_from_env()
        config_path = root / "platform_workspace" / str(project) / "multi_service_config.json"
        if config_path.exists():
            svc_mgr.load_from_file(config_path)
        svc_mgr.load_from_env()
        svc_mgr.login_all_services()
        for s in svc_mgr.store.list_services():
            service_token_by_actor[s] = {
                "admin": svc_mgr.get_token(s, "admin"),
                "normal_user": svc_mgr.get_token(s, "viewer"),
            }
        if service_token_by_actor:
            service_credential_manager = svc_mgr
    except (ImportError, Exception):
        pass  # Multi-module not configured, use legacy single-service

    issues: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    base_url = str(cfg.get("base_url") or "")
    # P0 fix: keep base_url for GET-only probes even when safety gate fails.
    # Only clear it for write operations (POST/PUT/PATCH/DELETE).
    read_only_base_url = base_url  # always available for GET probes
    write_base_url = base_url if live_execution_allowed else ""
    for probe in probes:
        # ── Multi-module: resolve target URL and token by service ──
        probe_service = str(probe.get("service") or probe.get("service_name") or "")
        probe_actor = str(probe.get("actor") or "admin")
        target_url = base_url

        # Resolve service-specific URL and token
        if probe_service and service_token_by_actor:
            svc_tokens = service_token_by_actor.get(probe_service, {})
            svc_token = svc_tokens.get(probe_actor) or svc_tokens.get("admin")
            if svc_token:
                token_by_actor[probe_actor] = svc_token
            if service_credential_manager:
                svc_base = service_credential_manager.get_base_url(probe_service)
                if svc_base:
                    target_url = svc_base
        # P1 fix: Route delegated probes through unified execution bus instead of short-circuiting.
        # GET-only probes from all sources should execute against the test environment.
        # Only truly non-HTTP audit sources (assurance coverage, knowledge lineage) remain delegated.
        delegated_audit_sources = {
            "business_assurance_coverage",   # Phase56: assurance-case audit, no HTTP
            "enterprise_business_knowledge_asset",  # Phase58: knowledge lineage, no HTTP
        }
        source = str(probe.get("source") or "")
        if source in delegated_audit_sources:
            executions.append({"probe_id": probe.get("probe_id"), "probe": probe, "response_status": None, "error": f"delegated_to_{source}", "suspicious": False, "confidence": 0.0, "reason": "non_http_audit_source"})
            continue
        if probe.get("execution_policy") == "candidate_only" or not base_url:
            # P0 fix: GET-only candidate_only probes should still execute against test env.
            # Only block candidate_only if it's a write operation (POST/PUT/PATCH/DELETE).
            is_candidate_only = probe.get("execution_policy") == "candidate_only"
            probe_method = str(probe.get("method") or "GET").upper()
            is_read_only = probe_method in ("GET", "HEAD", "OPTIONS")
            if (is_candidate_only and not is_read_only) or not base_url:
                response = {"ok": False, "status_code": None, "body": "", "error": "candidate_only_or_missing_base_url"}
            else:
                # GET candidate_only: route to P0 execution logic below
                response = None  # signal to continue to execution block
        else:
            response = None  # signal to continue to execution block
        if response is None:
            # P0 fix: differentiate GET vs write. GET probes always allowed on test environments.
            # Write probes (POST/PUT/PATCH/DELETE) require live_execution_allowed.
            probe_method = str(probe.get("method") or "GET").upper()
            is_read_only = probe_method in ("GET", "HEAD", "OPTIONS")
            is_destructive = probe.get("destructive") or (not is_read_only)

            if is_destructive and not live_execution_allowed:
                response = {"ok": False, "status_code": None, "body": "", "error": "write_probe_blocked_by_safety_gate"}
            elif is_destructive and not bool(cfg.get("allow_destructive_tests")):
                response = {"ok": False, "status_code": None, "body": "", "error": "destructive_probe_blocked"}
            else:
                target_url = read_only_base_url if is_read_only else write_base_url
                response = _fetch_json_or_text(_join_url(target_url, str(probe.get("path"))), probe_method, token=token_by_actor.get(str(probe.get("actor"))), timeout=timeout)
        suspicious, confidence, reason = _status_suspicious(probe, response)
        execution = {
            "probe_id": probe["probe_id"],
            "probe": probe,
            "response_status": response.get("status_code"),
            "error": response.get("error"),
            "duration_seconds": response.get("duration_seconds"),
            "suspicious": suspicious,
            "confidence": confidence,
            "reason": reason,
        }
        executions.append(execution)
        if suspicious or response.get("error") in {"candidate_only_or_missing_base_url", "destructive_probe_blocked"}:
            candidate_only = response.get("error") in {"candidate_only_or_missing_base_url", "destructive_probe_blocked"}
            issue = {
                "issue_id": f"ISSUE_{probe['probe_id']}",
                "title": probe.get("title"),
                "defect_family": probe.get("defect_family") or resolve_defect_family(probe).get("family_id"),
                "risk_type": probe.get("risk_type"),
                "severity": probe.get("severity") if not candidate_only else "P2",
                "confidence": confidence if not candidate_only else 0.42,
                "status": "needs_human_review",
                "expected": probe.get("expected"),
                "actual": f"HTTP {response.get('status_code')}" if response.get("status_code") is not None else "未执行 / 候选风险",
                "business_impact": _impact_for_risk(str(probe.get("risk_type"))),
                "suggested_fix": _fix_for_risk(str(probe.get("risk_type"))),
                "qa_feedback_status": "pending",
                "evidence": {
                    "actor": probe.get("actor"),
                    "request": {"method": probe.get("method"), "url": probe.get("path")},
                    "response": {"status_code": response.get("status_code"), "body_excerpt": (response.get("body") or "")[:500], "error": response.get("error")},
                    "reason": reason,
                },
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": probe["probe_id"], "request": issue["evidence"]["request"], "response": issue["evidence"]["response"], "expected": issue["expected"], "actual": issue["actual"], "confidence": issue["confidence"]})

    for adapter_issue in collect_api_contract_issues(project, root, scenario=str(cfg.get("scenario") or "manufacturing")):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_openapi_static_security_issues(openapi if isinstance(openapi, dict) else {}):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_privacy_compliance_issues(
        openapi if isinstance(openapi, dict) else {},
        project_id=project,
        root=root,
        enterprise_testops_control_plane=enterprise_testops_preflight or {},
    ):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_browser_ui_replay_issues(project, root, cfg=cfg, scenario=str(cfg.get("scenario") or "manufacturing")):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_frontend_runtime_issues(project, root, scenario=str(cfg.get("scenario") or "manufacturing"), cfg=cfg):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_frontend_ux_issues(project, root, scenario=str(cfg.get("scenario") or "manufacturing"), cfg=cfg):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_compatibility_issues(cfg, openapi=openapi if isinstance(openapi, dict) else {}, project_id=project, root=root, scenario=str(cfg.get("scenario") or "manufacturing")):
        _append_adapter_issue(issues, evidence_items, adapter_issue)
    for adapter_issue in collect_performance_stability_issues(executions, request_timeout_seconds=timeout):
        _append_adapter_issue(issues, evidence_items, adapter_issue)

    business_outcome_run = None
    try:
        outcome_mode = _live_mode_or_plan(cfg.get("business_outcome_execution_mode"), live_execution_allowed)
        business_outcome_run = run_business_outcome_validation(project, root, options={"execution_mode": outcome_mode})
        for finding in business_outcome_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "export_data_quality",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "导出、报表或对账结果与业务源数据不一致，可能导致运营、财务、审计或客户决策错误。",
                "suggested_fix": "修复导出查询去重、筛选条件与源数据映射，并将该业务结果审计契约加入发布回归。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": ((finding.get("evidence") or {}).get("export_url") or "export")}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_outcome_run = {"error": str(exc)}

    business_reconciliation_run = None
    try:
        reconciliation_mode = _live_mode_or_plan(cfg.get("business_reconciliation_execution_mode"), live_execution_allowed)
        business_reconciliation_run = run_business_reconciliation(project, root, options={"execution_mode": reconciliation_mode})
        for finding in business_reconciliation_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "business_reconciliation",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "统计看板、报表或经营指标与底层业务明细不一致，可能导致财务、运营、审计和管理决策失真。",
                "suggested_fix": "统一统计聚合、筛选和去重口径；将该对账契约加入发布回归并保留异常证据。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("summary_request") or {"method": "GET", "url": "summary"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_reconciliation_run = {"error": str(exc)}

    business_invariant_run = None
    try:
        invariant_mode = _live_mode_or_plan(cfg.get("business_invariant_execution_mode"), live_execution_allowed)
        business_invariant_run = run_business_invariant_mining(project, root, options={"execution_mode": invariant_mode})
        for finding in business_invariant_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "business_invariant",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "业务规则、筛选语义、数据关联或运行时契约被违反，可能导致错误决策、数据污染、漏查或业务链路中断。",
                "suggested_fix": "修复后端查询/聚合/数据完整性逻辑，并把已确认的不变量作为发布回归门禁。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "business_invariant"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_invariant_run = {"error": str(exc)}


    multi_source_reasoning_run = None
    try:
        reasoning_mode = _live_mode_or_plan(cfg.get("multi_source_reasoning_execution_mode"), live_execution_allowed)
        multi_source_reasoning_run = run_multi_source_reasoning(project, root, options={"execution_mode": reasoning_mode})
        for finding in multi_source_reasoning_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "business_reasoning",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "跨系统、页面/API、异常处理或历史数据口径被破坏，可能造成错误业务决策、数据不同步、迁移损坏或线上异常被静默掩盖。",
                "suggested_fix": "修复 Oracle 两侧的契约/同步/参数校验/迁移逻辑，并将已确认规则回灌为企业专属发布回归。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "multi_source_oracle"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        multi_source_reasoning_run = {"error": str(exc)}

    business_lifecycle_run = None
    try:
        lifecycle_mode = _live_mode_or_plan(cfg.get("business_lifecycle_execution_mode"), live_execution_allowed)
        business_lifecycle_run = run_business_lifecycle_reasoning(project, root, options={"execution_mode": lifecycle_mode})
        for finding in business_lifecycle_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "lifecycle_integrity",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "状态机、数据生命周期或事件历史被破坏，可能造成重复扣款、越过审批、终态回写、已删除数据泄漏或流程结果不可追溯。",
                "suggested_fix": "修复状态转换守卫、事件写入与时间线一致性；将确认的生命周期 Oracle 加入发布回归和隔离沙箱状态机测试。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "business_lifecycle"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_lifecycle_run = {"error": str(exc)}

    consistency_isolation_run = None
    try:
        consistency_mode = _live_mode_or_plan(cfg.get("consistency_isolation_execution_mode"), live_execution_allowed)
        consistency_isolation_run = run_consistency_isolation_reasoning(project, root, options={"execution_mode": consistency_mode})
        for finding in consistency_isolation_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "consistency_integrity",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "租户隔离、异步结果、缓存/索引或最终一致性被破坏，可能造成跨客户数据泄漏、任务假成功、页面与事实源不一致或关键业务延迟生效。",
                "suggested_fix": "修复租户查询边界、任务结果状态机、消息/索引幂等与缓存失效机制；把确认的 Oracle 作为发布回归与隔离沙箱并发测试。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "consistency_isolation"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        consistency_isolation_run = {"error": str(exc)}

    metamorphic_differential_run = None
    try:
        metamorphic_mode = _live_mode_or_plan(cfg.get("metamorphic_differential_execution_mode"), live_execution_allowed)
        metamorphic_differential_run = run_metamorphic_differential_reasoning(project, root, options={"execution_mode": metamorphic_mode})
        for finding in metamorphic_differential_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "metamorphic_relation",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "多个有效业务观察之间的关系被破坏，可能表现为筛选组合错误、分页重复/漏数、排序错误或列表详情展示不一致。",
                "suggested_fix": "统一查询解析、排序分页和列表/详情序列化的业务口径；将该变形关系固化为发布前只读回归。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "metamorphic_differential"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        metamorphic_differential_run = {"error": str(exc)}

    temporal_data_regression_run = None
    try:
        temporal_mode = _live_mode_or_plan(cfg.get("temporal_data_regression_execution_mode"), live_execution_allowed)
        temporal_data_regression_run = run_temporal_data_regression_reasoning(project, root, options={"execution_mode": temporal_mode})
        for finding in temporal_data_regression_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "temporal_data_regression",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "新版本或运行结果相对可信基线发生数据语义回归，可能导致历史字段丢失、金额单位错误、关键业务属性被覆盖或主键重复。",
                "suggested_fix": "检查数据迁移、序列化、金额单位、字段映射与索引重建；批准迁移后显式重建基线，并将确认的问题固化为发布前只读回归。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "temporal_data_regression"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        temporal_data_regression_run = {"error": str(exc)}

    business_causality_run = None
    try:
        causality_mode = _live_mode_or_plan(cfg.get("business_causality_execution_mode") or cfg.get("business_causality_conservation_execution_mode"), live_execution_allowed)
        business_causality_run = run_business_causality_conservation(project, root, options={"execution_mode": causality_mode})
        for finding in business_causality_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "business_causality",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "主业务状态与支付、库存、审批、台账等副作用脱节，可能造成漏扣、重复扣款、重复发货、无法对账或财务/库存事实失真。",
                "suggested_fix": "修复事务边界、幂等键、消息消费去重、外键约束和金额计算；将确认的因果 Oracle 固化为发布前只读回归，并在隔离环境验证重放。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("source_request") or {"method": "GET", "url": "business_causality"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_causality_run = {"error": str(exc)}

    business_population_run = None
    try:
        population_mode = _live_mode_or_plan(cfg.get("business_population_constraints_execution_mode") or cfg.get("business_population_constraint_execution_mode"), live_execution_allowed)
        business_population_run = run_business_population_constraints(project, root, options={"execution_mode": population_mode})
        for finding in business_population_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id") or finding.get("finding_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "business_population_constraint",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "多个业务记录共同突破额度、资源容量、时间排班、批次闭合或审批阈值，可能造成资损、资源双占、批量数据丢失和内控失效。",
                "suggested_fix": "在数据库与服务层同时落实复合唯一约束、条件聚合锁、区间排他、批次终态校验和审批门禁；把确认的群体 Oracle 固化为发布前只读回归，并在隔离环境验证并发绕过。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("request") or {"method": "GET", "url": "business_population_constraints"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_population_run = {"error": str(exc)}


    business_event_chain_run = None
    try:
        event_chain_mode = _live_mode_or_plan(cfg.get("business_event_chain_execution_mode") or cfg.get("event_chain_execution_mode"), live_execution_allowed)
        business_event_chain_run = run_business_event_chain_reasoning(project, root, options={"execution_mode": event_chain_mode})
        for finding in business_event_chain_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id") or finding.get("finding_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "event_chain_integrity",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "业务状态、消息投递与消费者结果断链，可能造成漏通知、漏发货、异步任务假成功、重复扣款/库存或死信长期无人处理。",
                "suggested_fix": "修复 outbox 原子写入、消息幂等键、消费者去重/顺序控制、死信诊断与重试上限；把确认的事件 Oracle 固化为发布前只读回归，并在隔离环境验证重放。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("event_request") or {"method": "GET", "url": "business_event_chain"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_event_chain_run = {"error": str(exc)}

    business_saga_compensation_run = None
    try:
        saga_mode = _live_mode_or_plan(cfg.get("business_saga_compensation_execution_mode") or cfg.get("saga_compensation_execution_mode"), live_execution_allowed)
        business_saga_compensation_run = run_business_saga_compensation_reasoning(project, root, options={"execution_mode": saga_mode})
        for finding in business_saga_compensation_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id") or finding.get("finding_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "saga_compensation",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.75,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "失败、取消或回滚后的资金、库存、授权和 Saga 状态未正确收敛，可能造成漏退款、重复退款、库存长期冻结、额度占用、客户投诉与财务对账差异。",
                "suggested_fix": "修复补偿编排的幂等键、状态机终态、金额口径、补偿外键及残留资源释放；将确认的补偿 Oracle 固化为发布前只读回归，并在隔离环境验证重试与取消竞态。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": finding.get("evidence_stability") or {},
                "learning_matches": finding.get("learning_matches") or [],
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": (issue.get("evidence") or {}).get("source_request") or {"method": "GET", "url": "business_saga_compensation"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_saga_compensation_run = {"error": str(exc)}

    business_assurance_coverage_run = None
    try:
        business_assurance_coverage_run = run_business_assurance_coverage(project, root)
        for finding in business_assurance_coverage_run.get("findings") or []:
            issue = {
                "issue_id": finding.get("issue_id") or finding.get("finding_id"),
                "title": finding.get("title"),
                "risk_type": finding.get("risk_type") or "assurance_coverage_gap",
                "severity": finding.get("severity") or "P1",
                "confidence": finding.get("confidence") or 0.99,
                "status": finding.get("status") or "needs_human_review",
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "business_impact": "关键业务失败模型没有可执行 Oracle 保护，当前发布质量无法被可重复证据证明；该项是质量保障缺口，不是已确认线上缺陷。",
                "suggested_fix": "补齐对应业务 Oracle、将其纳入只读/预发执行，并以模型化故障杀伤率和回归证据闭环；写路径验证必须在隔离沙箱执行。",
                "qa_feedback_status": "pending",
                "evidence": finding.get("evidence") or {},
                "evidence_stability": {},
                "learning_matches": [],
                "quality_assurance_gap": True,
            }
            issues.append(issue)
            evidence_items.append({"issue_id": issue["issue_id"], "probe_id": finding.get("contract_id"), "request": {"method": "MODELLED", "url": "business_assurance_coverage"}, "response": issue.get("evidence"), "expected": issue.get("expected"), "actual": issue.get("actual"), "confidence": issue.get("confidence")})
    except Exception as exc:
        business_assurance_coverage_run = {"error": str(exc)}

    issues = [
        enrich_issue_accounting(
            {**issue, "defect_family": issue.get("defect_family") or resolve_defect_family(issue).get("family_id")}
        )
        for issue in issues
        if isinstance(issue, dict)
    ]
    accounting_rows = [
        _accounting
        for _accounting in (
            issue.get("validated_bug_accounting")
            for issue in issues
            if isinstance(issue.get("validated_bug_accounting"), dict)
        )
        if isinstance(_accounting, dict)
    ]
    candidate_findings = [issue for issue in issues if (issue.get("validated_bug_accounting") or {}).get("accounting_state") == "candidate"]
    pending_findings = [issue for issue in issues if (issue.get("validated_bug_accounting") or {}).get("accounting_state") == "pending"]
    validated_bugs = [issue for issue in issues if bool((issue.get("validated_bug_accounting") or {}).get("strict_validated_bug"))]
    validated_bug_count = len(validated_bugs)
    saleable_count = sum(1 for item in accounting_rows if item.get("saleable"))
    coverage_gap_count = sum(1 for item in accounting_rows if item.get("quality_tier") == "coverage_gap")
    unexecuted_count = sum(1 for item in accounting_rows if item.get("quality_tier") == "unexecuted")
    heuristic_or_pending_count = max(
        0,
        len(issues) - validated_bug_count - coverage_gap_count - unexecuted_count,
    )
    verifier_passed_issue_count = sum(1 for item in accounting_rows if item.get("verifier_passed"))
    reproduction_ready_issue_count = sum(1 for item in accounting_rows if item.get("has_reproduction"))
    evidence_ref_ready_issue_count = sum(1 for item in accounting_rows if item.get("has_evidence_refs"))
    high = [i for i in issues if float(i.get("confidence") or 0) >= 0.75]
    medium = [i for i in issues if 0.5 <= float(i.get("confidence") or 0) < 0.75]
    blockers = [
        i
        for i in issues
        if i.get("severity") in {"P0", "P1"}
        and str(((i.get("validated_bug_accounting") or {}).get("accounting_state") or "")) in {"pending", "validated"}
    ]
    risk_dist: dict[str, int] = {}
    for i in issues:
        risk_dist[str(i.get("risk_type") or "unknown")] = risk_dist.get(str(i.get("risk_type") or "unknown"), 0) + 1
    discovery_funnel, discovery_blocker_summary = _build_discovery_funnel(probes, executions, issues, risk_plan)
    data = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "mode": mode,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ground_truth_free": True,
        "known_bugs": None,
        "metrics": {
            "issue_count": len(issues),
            "high_confidence_issues": len(high),
            "medium_confidence_issues": len(medium),
            "needs_human_review": len(issues),
            "candidate_issue_count": len(candidate_findings),
            "pending_finding_count": len(pending_findings),
            "validated_bug_count": validated_bug_count,
            "strict_validated_bug_count": validated_bug_count,
            "saleable_bug_count": saleable_count,
            "quality_tiers": {
                "confirmed_bug": validated_bug_count,
                "coverage_gap": coverage_gap_count,
                "unexecuted": unexecuted_count,
                "heuristic_or_pending": heuristic_or_pending_count,
            },
            "reporting_basis": "saleable_bug",
            "verifier_passed_issue_count": verifier_passed_issue_count,
            "reproduction_ready_issue_count": reproduction_ready_issue_count,
            "evidence_ref_ready_issue_count": evidence_ref_ready_issue_count,
            "validated_bug_discovery_rate": _safe_rate(len(validated_bugs), len(probes)),
            "repro_success_rate": _safe_rate(reproduction_ready_issue_count, verifier_passed_issue_count),
            "evidence_complete_rate": _safe_rate(evidence_ref_ready_issue_count, verifier_passed_issue_count),
            "evidence_completeness": round(sum(1 for e in evidence_items if e.get("request") and e.get("response")) / max(1, len(evidence_items)), 3),
            "suggested_release_blockers": len(blockers),
            "estimated_hours_saved": round(len(probes) * 0.08 + len(issues) * 0.25, 2),
        },
        "risk_distribution": risk_dist,
        "onboarding_ok": onboarding.get("ok"),
        "safety_boundary": safety,
        "probes": probes,
        "business_adaptation_profile": {
            "selected_domains": business_adaptation_profile.get("selected_domains", []),
            "operation_count": business_adaptation_profile.get("operation_count", 0),
            "private_leak_check": business_adaptation_profile.get("private_leak_check", {}),
        },
        "enterprise_business_knowledge_asset": {
            "asset_id": (enterprise_business_knowledge_asset or {}).get("asset_id") if isinstance(enterprise_business_knowledge_asset, dict) else None,
            "summary": (enterprise_business_knowledge_asset or {}).get("summary", {}) if isinstance(enterprise_business_knowledge_asset, dict) else {},
            "module_tree": (enterprise_business_knowledge_asset or {}).get("module_tree", []) if isinstance(enterprise_business_knowledge_asset, dict) else [],
            "rule_library": (enterprise_business_knowledge_asset or {}).get("rule_library", []) if isinstance(enterprise_business_knowledge_asset, dict) else [],
            "permission_matrix": (enterprise_business_knowledge_asset or {}).get("permission_matrix", []) if isinstance(enterprise_business_knowledge_asset, dict) else [],
            "data_dependencies": (enterprise_business_knowledge_asset or {}).get("data_dependencies", []) if isinstance(enterprise_business_knowledge_asset, dict) else [],
            "risk_domains": (enterprise_business_knowledge_asset or {}).get("risk_domains", []) if isinstance(enterprise_business_knowledge_asset, dict) else [],
            "oracle_library": (enterprise_business_knowledge_asset or {}).get("oracle_library", []) if isinstance(enterprise_business_knowledge_asset, dict) else [],
            "evidence_bundle": enterprise_business_knowledge_evidence_bundle or {},
        },
        "multi_industry_business_profile": {
            "summary": (multi_industry_business_profile or {}).get("summary", {}) if isinstance(multi_industry_business_profile, dict) else {},
            "recognized_industries": (multi_industry_business_profile or {}).get("recognized_industries", []) if isinstance(multi_industry_business_profile, dict) else [],
            "modules": (multi_industry_business_profile or {}).get("modules", []) if isinstance(multi_industry_business_profile, dict) else [],
            "business_objects": (multi_industry_business_profile or {}).get("business_objects", []) if isinstance(multi_industry_business_profile, dict) else [],
            "roles": (multi_industry_business_profile or {}).get("roles", []) if isinstance(multi_industry_business_profile, dict) else [],
            "state_machines": (multi_industry_business_profile or {}).get("state_machines", []) if isinstance(multi_industry_business_profile, dict) else [],
            "permission_boundaries": (multi_industry_business_profile or {}).get("permission_boundaries", []) if isinstance(multi_industry_business_profile, dict) else [],
            "data_dependencies": (multi_industry_business_profile or {}).get("data_dependencies", []) if isinstance(multi_industry_business_profile, dict) else [],
            "industry_oracles": (multi_industry_business_profile or {}).get("industry_oracles", []) if isinstance(multi_industry_business_profile, dict) else [],
            "risk_domains": (multi_industry_business_profile or {}).get("risk_domains", []) if isinstance(multi_industry_business_profile, dict) else [],
            "private_leak_check": (multi_industry_business_profile or {}).get("private_leak_check", {}) if isinstance(multi_industry_business_profile, dict) else {},
        },
        "universal_defect_mining_summary": (universal_defect_mining or {}).get("summary", {}) if isinstance(universal_defect_mining, dict) else {},
        "universal_defect_probe_count": len([p for p in probes if p.get("source") == "universal_spec_behavior"]),
        "business_outcome_validation_summary": (business_outcome_run or business_outcome_profile or {}).get("summary", {}) if isinstance((business_outcome_run or business_outcome_profile), dict) else {},
        "business_outcome_export_contract_count": ((business_outcome_profile or {}).get("summary") or {}).get("export_contract_count", 0) if isinstance(business_outcome_profile, dict) else 0,
        "business_outcome_probe_count": len([p for p in probes if p.get("source") == "business_outcome_validation"]),
        "business_outcome_finding_count": len((business_outcome_run or {}).get("findings") or []) if isinstance(business_outcome_run, dict) else 0,
        "business_reconciliation_summary": (business_reconciliation_run or business_reconciliation_profile or {}).get("summary", {}) if isinstance((business_reconciliation_run or business_reconciliation_profile), dict) else {},
        "business_reconciliation_contract_count": ((business_reconciliation_profile or {}).get("summary") or {}).get("reconciliation_contract_count", 0) if isinstance(business_reconciliation_profile, dict) else 0,
        "business_reconciliation_probe_count": len([p for p in probes if p.get("source") == "business_reconciliation"]),
        "business_reconciliation_finding_count": len((business_reconciliation_run or {}).get("findings") or []) if isinstance(business_reconciliation_run, dict) else 0,
        "business_invariant_summary": (business_invariant_run or business_invariant_profile or {}).get("summary", {}) if isinstance((business_invariant_run or business_invariant_profile), dict) else {},
        "business_invariant_contract_count": ((business_invariant_profile or {}).get("summary") or {}).get("invariant_contract_count", 0) if isinstance(business_invariant_profile, dict) else 0,
        "business_invariant_probe_count": len([p for p in probes if p.get("source") == "business_invariant_mining"]),
        "business_invariant_finding_count": len((business_invariant_run or {}).get("findings") or []) if isinstance(business_invariant_run, dict) else 0,
        "multi_source_reasoning_summary": (multi_source_reasoning_run or multi_source_reasoning_profile or {}).get("summary", {}) if isinstance((multi_source_reasoning_run or multi_source_reasoning_profile), dict) else {},
        "multi_source_reasoning_contract_count": ((multi_source_reasoning_profile or {}).get("summary") or {}).get("total_contract_count", 0) if isinstance(multi_source_reasoning_profile, dict) else 0,
        "multi_source_reasoning_probe_count": len([p for p in probes if p.get("source") == "multi_source_business_reasoning"]),
        "multi_source_reasoning_finding_count": len((multi_source_reasoning_run or {}).get("findings") or []) if isinstance(multi_source_reasoning_run, dict) else 0,
        "confirmed_bug_memory_count": (((multi_source_reasoning_run or multi_source_reasoning_profile or {}).get("summary") or {}).get("confirmed_bug_memory_count", 0)) if isinstance((multi_source_reasoning_run or multi_source_reasoning_profile), dict) else 0,
        "confirmed_bug_flywheel_summary": (confirmed_bug_flywheel_profile or {}).get("summary", {}) if isinstance(confirmed_bug_flywheel_profile, dict) else {},
        "confirmed_bug_flywheel_pattern_count": int((((confirmed_bug_flywheel_profile or {}).get("summary") or {}).get("learning_pattern_count") or 0)) if isinstance(confirmed_bug_flywheel_profile, dict) else 0,
        "confirmed_bug_flywheel_pending_promotion_count": int((((confirmed_bug_flywheel_profile or {}).get("summary") or {}).get("pending_promotion_count") or 0)) if isinstance(confirmed_bug_flywheel_profile, dict) else 0,
        "business_lifecycle_summary": (business_lifecycle_run or business_lifecycle_profile or {}).get("summary", {}) if isinstance((business_lifecycle_run or business_lifecycle_profile), dict) else {},
        "business_lifecycle_contract_count": ((business_lifecycle_profile or {}).get("summary") or {}).get("lifecycle_contract_count", 0) if isinstance(business_lifecycle_profile, dict) else 0,
        "business_lifecycle_probe_count": len([p for p in probes if p.get("source") == "business_lifecycle_reasoning"]),
        "business_lifecycle_finding_count": len((business_lifecycle_run or {}).get("findings") or []) if isinstance(business_lifecycle_run, dict) else 0,
        "business_lifecycle_sandbox_candidate_count": (((business_lifecycle_run or business_lifecycle_profile or {}).get("summary") or {}).get("sandbox_transition_candidate_count", 0)) if isinstance((business_lifecycle_run or business_lifecycle_profile), dict) else 0,
        "consistency_isolation_summary": (consistency_isolation_run or consistency_isolation_profile or {}).get("summary", {}) if isinstance((consistency_isolation_run or consistency_isolation_profile), dict) else {},
        "consistency_isolation_contract_count": ((consistency_isolation_profile or {}).get("summary") or {}).get("consistency_contract_count", 0) if isinstance(consistency_isolation_profile, dict) else 0,
        "consistency_isolation_role_access_contract_count": ((consistency_isolation_profile or {}).get("summary") or {}).get("role_access_contract_count", 0) if isinstance(consistency_isolation_profile, dict) else 0,
        "consistency_isolation_probe_count": len([p for p in probes if p.get("source") == "consistency_isolation_reasoning"]),
        "consistency_isolation_finding_count": len((consistency_isolation_run or {}).get("findings") or []) if isinstance(consistency_isolation_run, dict) else 0,
        "consistency_isolation_sandbox_candidate_count": (((consistency_isolation_run or consistency_isolation_profile or {}).get("summary") or {}).get("sandbox_candidate_count", 0)) if isinstance((consistency_isolation_run or consistency_isolation_profile), dict) else 0,
        "metamorphic_differential_summary": (metamorphic_differential_run or metamorphic_differential_profile or {}).get("summary", {}) if isinstance((metamorphic_differential_run or metamorphic_differential_profile), dict) else {},
        "metamorphic_differential_contract_count": ((metamorphic_differential_profile or {}).get("summary") or {}).get("metamorphic_contract_count", 0) if isinstance(metamorphic_differential_profile, dict) else 0,
        "metamorphic_differential_probe_count": len([p for p in probes if p.get("source") == "metamorphic_differential_reasoning"]),
        "metamorphic_differential_finding_count": len((metamorphic_differential_run or {}).get("findings") or []) if isinstance(metamorphic_differential_run, dict) else 0,
        "temporal_data_regression_summary": (temporal_data_regression_run or temporal_data_regression_profile or {}).get("summary", {}) if isinstance((temporal_data_regression_run or temporal_data_regression_profile), dict) else {},
        "temporal_data_regression_contract_count": ((temporal_data_regression_profile or {}).get("summary") or {}).get("temporal_contract_count", 0) if isinstance(temporal_data_regression_profile, dict) else 0,
        "temporal_data_regression_probe_count": len([p for p in probes if p.get("source") == "temporal_data_regression_reasoning"]),
        "temporal_data_regression_finding_count": len((temporal_data_regression_run or {}).get("findings") or []) if isinstance(temporal_data_regression_run, dict) else 0,
        "temporal_data_regression_baseline_established_count": (((temporal_data_regression_run or temporal_data_regression_profile or {}).get("summary") or {}).get("baseline_established_count", 0)) if isinstance((temporal_data_regression_run or temporal_data_regression_profile), dict) else 0,
        "business_causality_summary": (business_causality_run or business_causality_profile or {}).get("summary", {}) if isinstance((business_causality_run or business_causality_profile), dict) else {},
        "business_causality_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("causality_contract_count", 0) if isinstance(business_causality_profile, dict) else 0,
        "business_causality_journal_balance_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("journal_balance_contract_count", 0) if isinstance(business_causality_profile, dict) else 0,
        "business_causality_period_rollforward_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("period_rollforward_contract_count", 0) if isinstance(business_causality_profile, dict) else 0,
        "business_causality_inventory_reservation_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("inventory_reservation_contract_count", 0) if isinstance(business_causality_profile, dict) else 0,
        "business_causality_probe_count": len([p for p in probes if p.get("source") == "business_causality_conservation"]),
        "business_causality_finding_count": len((business_causality_run or {}).get("findings") or []) if isinstance(business_causality_run, dict) else 0,
        "business_population_summary": (business_population_run or business_population_profile or {}).get("summary", {}) if isinstance((business_population_run or business_population_profile), dict) else {},
        "business_population_contract_count": ((business_population_profile or {}).get("summary") or {}).get("population_contract_count", 0) if isinstance(business_population_profile, dict) else 0,
        "business_population_probe_count": len([p for p in probes if p.get("source") == "business_population_constraints"]),
        "business_population_finding_count": len((business_population_run or {}).get("findings") or []) if isinstance(business_population_run, dict) else 0,
        "business_event_chain_summary": (business_event_chain_run or business_event_chain_profile or {}).get("summary", {}) if isinstance((business_event_chain_run or business_event_chain_profile), dict) else {},
        "business_event_chain_contract_count": ((business_event_chain_profile or {}).get("summary") or {}).get("event_chain_contract_count", 0) if isinstance(business_event_chain_profile, dict) else 0,
        "business_event_chain_probe_count": len([p for p in probes if p.get("source") == "business_event_chain_reasoning"]),
        "business_event_chain_finding_count": len((business_event_chain_run or {}).get("findings") or []) if isinstance(business_event_chain_run, dict) else 0,
        "business_saga_compensation_summary": (business_saga_compensation_run or business_saga_compensation_profile or {}).get("summary", {}) if isinstance((business_saga_compensation_run or business_saga_compensation_profile), dict) else {},
        "business_saga_compensation_contract_count": ((business_saga_compensation_profile or {}).get("summary") or {}).get("saga_compensation_contract_count", 0) if isinstance(business_saga_compensation_profile, dict) else 0,
        "business_saga_compensation_probe_count": len([p for p in probes if p.get("source") == "business_saga_compensation_reasoning"]),
        "business_saga_compensation_finding_count": len((business_saga_compensation_run or {}).get("findings") or []) if isinstance(business_saga_compensation_run, dict) else 0,
        "business_assurance_coverage_summary": (business_assurance_coverage_run or business_assurance_coverage_profile or {}).get("summary", {}) if isinstance((business_assurance_coverage_run or business_assurance_coverage_profile), dict) else {},
        "business_assurance_coverage_probe_count": len([p for p in probes if p.get("source") == "business_assurance_coverage"]),
        "business_assurance_coverage_finding_count": len((business_assurance_coverage_run or {}).get("findings") or []) if isinstance(business_assurance_coverage_run, dict) else 0,
        "business_assurance_score": float((((business_assurance_coverage_run or business_assurance_coverage_profile or {}).get("summary") or {}).get("assurance_score") or 0)) if isinstance((business_assurance_coverage_run or business_assurance_coverage_profile), dict) else 0.0,
        "business_assurance_mutation_kill_rate": float((((business_assurance_coverage_run or business_assurance_coverage_profile or {}).get("summary") or {}).get("modeled_mutation_kill_rate") or 0)) if isinstance((business_assurance_coverage_run or business_assurance_coverage_profile), dict) else 0.0,
        "business_adaptive_probe_count": len([p for p in probes if p.get("source") == "business_adaptation_layer"]),
        "enterprise_business_knowledge_center_enabled": bool((enterprise_business_knowledge_asset or {}).get("summary", {}).get("active_source_count", 0)) if isinstance(enterprise_business_knowledge_asset, dict) else False,
        "enterprise_business_knowledge_source_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("active_source_count", 0) if isinstance(enterprise_business_knowledge_asset, dict) else 0,
        "enterprise_business_knowledge_rule_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("rule_count", 0) if isinstance(enterprise_business_knowledge_asset, dict) else 0,
        "enterprise_business_knowledge_oracle_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("oracle_count", 0) if isinstance(enterprise_business_knowledge_asset, dict) else 0,
        "enterprise_business_knowledge_relationship_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("relationship_count", 0) if isinstance(enterprise_business_knowledge_asset, dict) else 0,
        "enterprise_business_knowledge_probe_count": len([p for p in probes if p.get("source") == "enterprise_business_knowledge_asset"]),
        "multi_industry_business_understanding_enabled": bool(multi_industry_business_profile),
        "multi_industry_recognized_industries": ((multi_industry_business_profile or {}).get("summary") or {}).get("recognized_industries", []) if isinstance(multi_industry_business_profile, dict) else [],
        "multi_industry_business_object_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("business_object_count", 0) if isinstance(multi_industry_business_profile, dict) else 0,
        "multi_industry_state_machine_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("state_machine_count", 0) if isinstance(multi_industry_business_profile, dict) else 0,
        "multi_industry_oracle_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("oracle_count", 0) if isinstance(multi_industry_business_profile, dict) else 0,
        "multi_industry_risk_domain_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("risk_domain_count", 0) if isinstance(multi_industry_business_profile, dict) else 0,
        "multi_industry_business_probe_count": len([p for p in probes if p.get("source") == "multi_industry_business_reasoning"]),
        "history_informed_probe_count": len([p for p in probes if p.get("source") == "enterprise_history_rag"]),
        "enterprise_knowledge_probe_count": len([p for p in probes if p.get("source") == "enterprise_knowledge_rag"]),
        "business_flow_scenario_probe_count": len([p for p in probes if p.get("source") == "enterprise_business_flow_graph"]),
        "business_flow_execution_summary": (business_flow_execution or {}).get("summary", {}) if isinstance(business_flow_execution, dict) else {},
        "business_flow_execution_candidate_issue_count": len((business_flow_execution or {}).get("candidate_issues") or []) if isinstance(business_flow_execution, dict) else 0,
        "replay_evidence_summary": (replay_evidence_sandbox or {}).get("summary", {}) if isinstance(replay_evidence_sandbox, dict) else {},
        "replay_evidence_packet_count": len((replay_evidence_sandbox or {}).get("evidence_packets") or []) if isinstance(replay_evidence_sandbox, dict) else 0,
        "replay_evidence_enhanced_issue_count": len((replay_evidence_sandbox or {}).get("candidate_issues_enhanced") or []) if isinstance(replay_evidence_sandbox, dict) else 0,
        "risk_based_planner_enabled": use_risk_plan,
        "risk_based_plan_summary": (risk_plan or {}).get("summary", {}) if isinstance(risk_plan, dict) else {},
        "discovery_funnel": discovery_funnel,
        "discovery_blocker_summary": discovery_blocker_summary,
        "probe_execution_result": executions,
        "issues": issues,
        "suggested_release_blockers": blockers,
    }
    data["browser_ui_health"] = browser_health
    full_spectrum_capability_matrix = build_full_spectrum_capability_matrix(
        probes,
        onboarding=onboarding,
        browser_ui_health=browser_health,
        enterprise_testops_preflight=enterprise_testops_preflight or {},
    )
    data["full_spectrum_capability_matrix"] = full_spectrum_capability_matrix
    family_coverage_report = build_bug_family_coverage_report(
        probes,
        issues,
        capability_rows=list(full_spectrum_capability_matrix.get("rows") or []),
        health_context={"browser_ui_health": browser_health},
    )
    data["bug_family_coverage"] = family_coverage_report
    ui_design_oracle_issues = [i for i in issues if isinstance(i, dict) and i.get("source") == "ui_design_oracle"]
    ui_design_oracle_signal_basis_distribution: dict[str, int] = {bucket: 0 for bucket in UI_DESIGN_ORACLE_SIGNAL_BASIS_BUCKETS}
    for issue in ui_design_oracle_issues:
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        bucket = normalize_ui_design_oracle_signal_basis(evidence.get("confidence_basis"))
        ui_design_oracle_signal_basis_distribution[bucket] = int(ui_design_oracle_signal_basis_distribution.get(bucket, 0) or 0) + 1
    ui_design_oracle_signal_basis_legend = build_ui_design_oracle_signal_basis_legend()
    ui_design_oracle_recommended_actions = recommend_ui_design_oracle_next_actions(ui_design_oracle_signal_basis_distribution, ui_design_oracle_signal_basis_legend)
    ui_design_oracle_action_reasons = build_ui_design_oracle_action_reasons(ui_design_oracle_signal_basis_distribution, ui_design_oracle_signal_basis_legend)
    ui_design_oracle_issue_count = len(ui_design_oracle_issues)
    ui_design_oracle_strong_signal_count = int(ui_design_oracle_signal_basis_distribution.get("testid", 0) or 0)
    ui_design_oracle_weak_signal_count = max(0, int(ui_design_oracle_issue_count) - int(ui_design_oracle_strong_signal_count))
    ui_design_oracle_journey_issue_count = 0
    ui_design_oracle_journey_ids: set[str] = set()
    for issue in ui_design_oracle_issues:
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        journey_id = str(evidence.get("journey_id") or "").strip()
        if not journey_id:
            continue
        ui_design_oracle_journey_issue_count += 1
        ui_design_oracle_journey_ids.add(journey_id)
    ui_design_oracle_journey_missing_count = len(ui_design_oracle_journey_ids)
    ui_design_oracle_journey_oracle_count = 0
    try:
        paths = config_paths(project, root)
        candidates: list[Path] = []
        configured_manifest = str((cfg or {}).get("ui_design_oracle_manifest") or "").strip()
        configured_dir = str((cfg or {}).get("frontend_project_routes_dir") or "").strip()
        if configured_manifest:
            candidates.append(Path(configured_manifest))
        if configured_dir:
            candidates.append(Path(configured_dir) / "ui_design_oracle_manifest.json")
        candidates.extend(
            [
                paths["output_dir"] / "ui_design_oracle_manifest.json",
                (root or paths["output_dir"]) / "platform_outputs" / project / "ui_design_oracle_manifest.json",
                (root or paths["output_dir"]) / "ui_design_oracle_manifest.json",
            ]
        )
        for candidate in candidates:
            payload = _load_json(candidate, {})
            journeys = payload.get("journeys") if isinstance(payload, dict) else None
            if isinstance(journeys, list):
                ui_design_oracle_journey_oracle_count = len([j for j in journeys if isinstance(j, dict)])
                break
    except Exception:
        ui_design_oracle_journey_oracle_count = 0
    ui_design_oracle_journey_covered_count = max(0, int(ui_design_oracle_journey_oracle_count) - int(ui_design_oracle_journey_missing_count))
    data["metrics"].update(
        {
            "api_contract_probe_count": len([p for p in probes if p.get("source") == "api_contract_acceptance"]),
            "browser_ui_replay_probe_count": len([p for p in probes if p.get("source") == "browser_ui_replay"]),
            "frontend_task_journey_probe_count": len([p for p in probes if p.get("source") == "frontend_task_journey"]),
            "frontend_runtime_probe_count": len([
                p
                for p in probes
                if p.get("source") == "frontend_runtime_smoke"
                or p.get("risk_type") == "frontend_execution_runtime"
            ]),
            "frontend_ux_probe_count": len([p for p in probes if p.get("source") == "frontend_ux_adapter"]),
            "compatibility_probe_count": len([p for p in probes if p.get("source") == "compatibility_adapter"]),
            "performance_stability_probe_count": len([p for p in probes if p.get("source") == "performance_stability_adapter"]),
            "privacy_compliance_probe_count": len([p for p in probes if resolve_defect_family(p).get("family_id") == "privacy_compliance"]),
            "privacy_compliance_issue_count": len([i for i in issues if resolve_defect_family(i).get("family_id") == "privacy_compliance"]),
            "ui_design_oracle_issue_count": ui_design_oracle_issue_count,
            "ui_design_oracle_strong_signal_count": ui_design_oracle_strong_signal_count,
            "ui_design_oracle_weak_signal_count": ui_design_oracle_weak_signal_count,
            "ui_design_oracle_signal_basis_distribution": dict(sorted(ui_design_oracle_signal_basis_distribution.items())),
            "ui_design_oracle_signal_basis_legend": dict(ui_design_oracle_signal_basis_legend),
            "ui_design_oracle_signal_basis_recommended_actions": list(ui_design_oracle_recommended_actions),
            "ui_design_oracle_signal_basis_action_reasons": dict(ui_design_oracle_action_reasons),
            "ui_design_oracle_role_signal_count": int(ui_design_oracle_signal_basis_distribution.get("role", 0) or 0),
            "ui_design_oracle_keyword_signal_count": int(ui_design_oracle_signal_basis_distribution.get("keyword", 0) or 0),
            "ui_design_oracle_token_signal_count": int(ui_design_oracle_signal_basis_distribution.get("token", 0) or 0),
            "ui_design_oracle_none_signal_count": int(ui_design_oracle_signal_basis_distribution.get("none", 0) or 0),
            "ui_design_oracle_journey_oracle_count": int(ui_design_oracle_journey_oracle_count),
            "ui_design_oracle_journey_covered_count": int(ui_design_oracle_journey_covered_count),
            "ui_design_oracle_journey_missing_count": int(ui_design_oracle_journey_missing_count),
            "ui_design_oracle_journey_issue_count": int(ui_design_oracle_journey_issue_count),
            "ui_design_oracle_missing_component_count": sum(
                1
                for i in issues
                if i.get("source") == "ui_design_oracle"
                and isinstance(i.get("evidence"), dict)
                and str((i.get("evidence") or {}).get("missing_component") or "").strip()
            ),
            "ui_design_oracle_missing_feedback_count": sum(
                1
                for i in issues
                if i.get("source") == "ui_design_oracle"
                and isinstance(i.get("evidence"), dict)
                and str((i.get("evidence") or {}).get("missing_feedback") or "").strip()
            ),
            "browser_ui_enabled": bool(browser_health.get("enabled")),
            "browser_ui_playwright_importable": bool(browser_health.get("playwright_importable")),
            "browser_ui_browsers_present": bool(browser_health.get("browsers_present")),
            "browser_ui_reason_code": str(browser_health.get("reason_code") or ""),
            "browser_ui_severity": str(browser_health.get("severity") or ""),
            "browser_ui_blocked_probe_count": len([p for p in probes if str(p.get("capability_gate") or "") == "browser_ui_unavailable"]),
            "covered_bug_family_count": int(family_coverage_report.get("covered_family_count", 0) or 0),
            "validated_bug_family_count": int(family_coverage_report.get("validated_family_count", 0) or 0),
            "pending_bug_family_count": int(family_coverage_report.get("pending_family_count", 0) or 0),
            "candidate_only_bug_family_count": int(family_coverage_report.get("candidate_only_family_count", 0) or 0),
        }
    )
    design_oracle_summary = {
        "ui_design_oracle_issue_count": int(data["metrics"].get("ui_design_oracle_issue_count") or 0),
        "ui_design_oracle_strong_signal_count": int(data["metrics"].get("ui_design_oracle_strong_signal_count") or 0),
        "ui_design_oracle_weak_signal_count": int(data["metrics"].get("ui_design_oracle_weak_signal_count") or 0),
        "ui_design_oracle_signal_basis_distribution": data["metrics"].get("ui_design_oracle_signal_basis_distribution") if isinstance(data["metrics"].get("ui_design_oracle_signal_basis_distribution"), dict) else {},
        "ui_design_oracle_signal_basis_legend": data["metrics"].get("ui_design_oracle_signal_basis_legend") if isinstance(data["metrics"].get("ui_design_oracle_signal_basis_legend"), dict) else {},
        "ui_design_oracle_signal_basis_recommended_actions": data["metrics"].get("ui_design_oracle_signal_basis_recommended_actions") if isinstance(data["metrics"].get("ui_design_oracle_signal_basis_recommended_actions"), list) else [],
        "ui_design_oracle_signal_basis_action_reasons": data["metrics"].get("ui_design_oracle_signal_basis_action_reasons") if isinstance(data["metrics"].get("ui_design_oracle_signal_basis_action_reasons"), dict) else {},
        "ui_design_oracle_role_signal_count": int(data["metrics"].get("ui_design_oracle_role_signal_count") or 0),
        "ui_design_oracle_keyword_signal_count": int(data["metrics"].get("ui_design_oracle_keyword_signal_count") or 0),
        "ui_design_oracle_token_signal_count": int(data["metrics"].get("ui_design_oracle_token_signal_count") or 0),
        "ui_design_oracle_none_signal_count": int(data["metrics"].get("ui_design_oracle_none_signal_count") or 0),
        "ui_design_oracle_journey_oracle_count": int(data["metrics"].get("ui_design_oracle_journey_oracle_count") or 0),
        "ui_design_oracle_journey_covered_count": int(data["metrics"].get("ui_design_oracle_journey_covered_count") or 0),
        "ui_design_oracle_journey_missing_count": int(data["metrics"].get("ui_design_oracle_journey_missing_count") or 0),
        "ui_design_oracle_journey_issue_count": int(data["metrics"].get("ui_design_oracle_journey_issue_count") or 0),
        "ui_design_oracle_missing_component_count": int(data["metrics"].get("ui_design_oracle_missing_component_count") or 0),
        "ui_design_oracle_missing_feedback_count": int(data["metrics"].get("ui_design_oracle_missing_feedback_count") or 0),
        "ui_design_oracle_signals_present": bool(int(data["metrics"].get("ui_design_oracle_issue_count") or 0) > 0),
    }
    if isinstance(data.get("risk_based_plan_summary"), dict):
        updated_plan_summary = dict(data["risk_based_plan_summary"])
        updated_plan_summary.update(design_oracle_summary)
        data["risk_based_plan_summary"] = updated_plan_summary
        if isinstance(risk_plan, dict):
            risk_plan["summary"] = updated_plan_summary
    # Phase59 turns raw discovery candidates into quality-scored, deduplicated
    # enterprise defects.  Environment and data-precondition failures are kept
    # observable but do not inflate the high-value Bug count.
    try:
        defect_quality_report = evaluate_defect_quality(issues, project, root)
        issue_lifecycle, fix_verification_plan = build_issue_lifecycle_and_fix_plan(defect_quality_report, project, root)
        explainable_assets = build_explainable_test_assets(
            project, root, enterprise_business_knowledge_asset or {}, probes, defect_quality_report,
            (enterprise_testops_preflight or {}).get("environment_health") or {},
        )
    except Exception:
        defect_quality_report, issue_lifecycle, fix_verification_plan, explainable_assets = {}, {}, {}, {}
    data["enterprise_testops_control_plane"] = {
        "preflight": {
            "target_environment": ((enterprise_testops_preflight or {}).get("environment_health") or {}).get("target_environment"),
            "environment_testable": ((enterprise_testops_preflight or {}).get("environment_health") or {}).get("target_testable"),
            "automatic_data_preparation_ratio": ((enterprise_testops_preflight or {}).get("test_data") or {}).get("automatic_preparation_ratio"),
            "journey_count": len(((enterprise_testops_preflight or {}).get("journey_graph") or {}).get("journeys") or []),
        },
        "defect_quality_summary": (defect_quality_report or {}).get("summary", {}),
        "issue_lifecycle_summary": (issue_lifecycle or {}).get("summary", {}),
        "fix_verification_summary": (fix_verification_plan or {}).get("summary", {}),
        "explainable_asset_counts": {
            "probe_explanations": len((explainable_assets or {}).get("probe_explanations") or []),
            "bug_explanations": len((explainable_assets or {}).get("bug_explanations") or []),
        },
    }
    data["metrics"].update({
        "enterprise_testops_environment_testable": bool(((enterprise_testops_preflight or {}).get("environment_health") or {}).get("target_testable")),
        "enterprise_testops_data_automatic_preparation_ratio": ((enterprise_testops_preflight or {}).get("test_data") or {}).get("automatic_preparation_ratio", 0.0),
        "enterprise_testops_journey_count": len(((enterprise_testops_preflight or {}).get("journey_graph") or {}).get("journeys") or []),
        "enterprise_testops_high_confidence_defect_count": ((defect_quality_report or {}).get("summary") or {}).get("high_confidence_count", 0),
        "enterprise_testops_environment_problem_count": ((defect_quality_report or {}).get("summary") or {}).get("environment_problem_count", 0),
        "enterprise_testops_duplicate_compression_rate": ((defect_quality_report or {}).get("summary") or {}).get("duplicate_compression_rate", 0.0),
    })
    # Phase90: record normalized candidate outcomes for future coverage-aware
    # planning. Raw issues remain candidates; no entry is promoted here.
    try:
        from ai_test_asset_center.business_risk_coverage_map import BusinessRiskCoverageMap
        coverage_outcomes = []
        for issue in issues:
            coverage_outcomes.append({
                "entity": issue.get("entity") or issue.get("resource"),
                "method": (issue.get("request") or {}).get("method") if isinstance(issue.get("request"), dict) else issue.get("method"),
                "path": (issue.get("request") or {}).get("url") if isinstance(issue.get("request"), dict) else issue.get("path"),
                "risk_type": issue.get("risk_type"),
                "verdict": issue.get("verdict") or issue.get("lifecycle_state") or "EVIDENCE_CAPTURED",
                "reason": issue.get("actual") or issue.get("blocker") or "",
            })
        coverage_summary = BusinessRiskCoverageMap(project, root).record_outcomes(coverage_outcomes)
    except Exception as exc:
        coverage_summary = {"error": str(exc)[:300]}
    data["business_risk_coverage_map"] = coverage_summary
    data["metrics"]["business_risk_coverage_entries"] = int(coverage_summary.get("entry_count", 0) or 0) if isinstance(coverage_summary, dict) else 0
    try:
        campaign_report = record_continuous_discovery_campaign_run(
            project,
            root,
            probes,
            issues,
            trigger="scheduled_round",
            run_context={
                "mode": mode,
                "validated_bug_count": len(validated_bugs),
                "pending_finding_count": len(pending_findings),
                "candidate_issue_count": len(candidate_findings),
            },
        )
    except Exception as exc:
        campaign_report = {"status": "unavailable", "error": str(exc)}
    data["continuous_discovery_campaign"] = {
        "campaign": (campaign_report or {}).get("campaign", {}),
        "summary": (campaign_report or {}).get("summary", {}),
        "current_run": (campaign_report or {}).get("current_run", {}),
        "dashboard": (campaign_report or {}).get("dashboard", {}),
        "recommended_frontier": (campaign_report or {}).get("recommended_frontier", []),
        "next_run_plan": (campaign_report or {}).get("next_run_plan", {}),
        "automation": (campaign_report or {}).get("automation", {}),
        "coverage_ledger": (campaign_report or {}).get("coverage_ledger", {}),
        "status": (campaign_report or {}).get("status", "ready"),
    }
    data["metrics"].update(
        {
            "continuous_discovery_campaign_state": ((campaign_report or {}).get("summary") or {}).get("campaign_state", "unknown"),
            "continuous_discovery_coverage_entries": int((((campaign_report or {}).get("summary") or {}).get("coverage_ledger_entry_count")) or 0),
            "continuous_discovery_remaining_frontier_count": int((((campaign_report or {}).get("summary") or {}).get("remaining_actionable_frontier_count")) or 0),
            "continuous_discovery_revalidate_due_count": int((((campaign_report or {}).get("summary") or {}).get("revalidate_due_count")) or 0),
            "continuous_discovery_recommended_frontier_count": int((((campaign_report or {}).get("summary") or {}).get("recommended_frontier_count")) or 0),
            "continuous_discovery_auto_schedule_status": str((((campaign_report or {}).get("automation")) or {}).get("status") or "idle"),
            "continuous_discovery_new_validated_bug_count": int((((campaign_report or {}).get("summary") or {}).get("this_run_new_validated_bug_count")) or 0),
            "continuous_discovery_cumulative_validated_bug_count": int((((campaign_report or {}).get("summary") or {}).get("cumulative_validated_bug_count")) or 0),
            "continuous_discovery_pending_to_validated_conversion_rate": float((((campaign_report or {}).get("summary") or {}).get("pending_to_validated_conversion_rate")) or 0.0),
            "continuous_discovery_remaining_high_value_frontier_count": int((((campaign_report or {}).get("summary") or {}).get("remaining_high_value_uncovered_behavior_count")) or 0),
            "continuous_discovery_revalidation_queue_size": int((((campaign_report or {}).get("summary") or {}).get("revalidation_queue_size")) or 0),
            "continuous_discovery_reporting_basis": str((((campaign_report or {}).get("summary")) or {}).get("reporting_basis") or "validated_bug"),
        }
    )

    probe_items = probes if isinstance(probes, list) else []
    issue_items = issues if isinstance(issues, list) else []
    execution_items = executions if isinstance(executions, list) else []
    executed_request_count = sum(1 for item in execution_items if item.get("executed"))
    blocked_request_count = sum(1 for item in execution_items if item.get("blocked") or item.get("blocked_by_safety"))
    candidate_only_count = sum(
        1 for item in issue_items
        if str(((item.get("evidence") or {}).get("response") or {}).get("error") or "").lower()
        in {"candidate_only_or_missing_base_url", "candidate_only"}
    )
    quality_summary = (defect_quality_report or {}).get("summary") if isinstance(defect_quality_report, dict) else {}
    high_confidence_count = int((quality_summary or {}).get("high_confidence_count") or 0)
    data.update({
        "status": "succeeded",
        "issue_count": len(issue_items),
        "executed_issue_count": len(issue_items) - candidate_only_count,
        "candidate_only_issue_count": candidate_only_count,
        "candidate_issue_count": len(candidate_findings),
        "pending_finding_count": len(pending_findings),
        "validated_bug_count": len(validated_bugs),
        "probe_count": len(probe_items),
        "network_requests": executed_request_count,
        "http_request_count": executed_request_count,
        "http_blocked_count": blocked_request_count,
        "candidate_only_issue_count": candidate_only_count,
        "high_confidence_issue_count": high_confidence_count,
        "output_dir": str(paths["output_dir"]),
        "summary": {
            "project_id": project,
            "project_name": data.get("project_name"),
            "mode": mode,
            "status": "succeeded",
            "issue_count": len(issue_items),
            "candidate_issue_count": len(candidate_findings),
            "pending_finding_count": len(pending_findings),
            "validated_bug_count": len(validated_bugs),
            "reporting_basis": "validated_bug",
            "validated_bug_discovery_rate": _safe_rate(len(validated_bugs), len(probe_items)),
            "repro_success_rate": _safe_rate(reproduction_ready_issue_count, verifier_passed_issue_count),
            "evidence_complete_rate": _safe_rate(evidence_ref_ready_issue_count, verifier_passed_issue_count),
            "probe_count": len(probe_items),
            "network_requests": executed_request_count,
            "http_blocked_count": blocked_request_count,
            "candidate_only_issue_count": candidate_only_count,
            "high_confidence_issue_count": high_confidence_count,
            "verifier_passed_issue_count": verifier_passed_issue_count,
            "reproduction_ready_issue_count": reproduction_ready_issue_count,
            "evidence_ref_ready_issue_count": evidence_ref_ready_issue_count,
            "low_discovery_diagnosis": (discovery_blocker_summary or {}).get("low_discovery_diagnosis", {}),
            "risk_distribution": risk_dist,
            "output_dir": str(paths["output_dir"]),
        },
    })
    data["metrics"].update({
        "issue_count": len(issue_items),
        "executed_issue_count": len(issue_items) - candidate_only_count,
        "candidate_only_issue_count": candidate_only_count,
        "candidate_issue_count": len(candidate_findings),
        "pending_finding_count": len(pending_findings),
        "validated_bug_count": len(validated_bugs),
        "reporting_basis": "validated_bug",
        "verifier_passed_issue_count": verifier_passed_issue_count,
        "reproduction_ready_issue_count": reproduction_ready_issue_count,
        "evidence_ref_ready_issue_count": evidence_ref_ready_issue_count,
        "validated_bug_discovery_rate": _safe_rate(len(validated_bugs), len(probe_items)),
        "repro_success_rate": _safe_rate(reproduction_ready_issue_count, verifier_passed_issue_count),
        "evidence_complete_rate": _safe_rate(evidence_ref_ready_issue_count, verifier_passed_issue_count),
        "probe_count": len(probe_items),
        "network_requests": executed_request_count,
        "http_blocked_count": blocked_request_count,
        "candidate_only_issue_count": candidate_only_count,
        "high_confidence_issue_count": high_confidence_count,
    })

    paths["workspace_dir"].mkdir(parents=True, exist_ok=True)
    paths["output_dir"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["workspace_dir"] / "defect_probes.json", {"items": probes})
    _write_json(paths["workspace_dir"] / "probe_execution_result.json", {"items": executions})
    _write_json(paths["output_dir"] / "discovered_issues.json", {"items": issues, "metrics": data["metrics"], "risk_distribution": risk_dist})
    _write_json(paths["output_dir"] / "evidence_bundle.json", {
        "items": evidence_items,
        "enterprise_testops": data.get("enterprise_testops_control_plane") or {},
        "enterprise_business_knowledge": {
            "asset_id": ((enterprise_business_knowledge_asset or {}).get("asset_id") if isinstance(enterprise_business_knowledge_asset, dict) else None),
            "evidence_bundle": enterprise_business_knowledge_evidence_bundle or {},
            "raw_source_payload_not_embedded": True,
        },
    })
    _write_json(paths["output_dir"] / "real_project_defect_data.json", data)
    (paths["output_dir"] / "bug_drafts.md").write_text(_render_bug_drafts(issues), encoding="utf-8")
    (paths["output_dir"] / "real_project_defect_report.html").write_text(render_real_project_report(data), encoding="utf-8")
    return data

