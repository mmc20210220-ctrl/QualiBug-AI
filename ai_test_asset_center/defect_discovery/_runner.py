"""DefectDiscoveryRunner: probe execution, bug discovery, evidence collection."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_test_asset_center.adaptive_probe_optimizer import build_learned_probe_policy

from ._common import *  # noqa: F401,F403
from ._model import *  # noqa: F401,F403
from ._scenarios import *  # noqa: F401,F403
from ._probes import *  # noqa: F401,F403
from ._probes import build_invariants, build_lightweight_business_adaptation_profile, generate_defect_probes, load_business_adaptation_profile, load_high_value_attack_plan, load_high_value_capability_assessment, load_high_value_capability_trend, load_high_value_pattern_memory, load_risk_learning_profile, normalize_discovery_mode, step  # noqa: F401


class DefectDiscoveryRunner:
    def __init__(self, config: DiscoveryConfig):
        from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
        self.config = config
        self.workspace = config.workspace_root / config.project / "defect_discovery"
        self.output = config.output_root / config.project / "defect_discovery"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict:
        from ._reporting import build_bug_drafts, build_business_risk_radar, build_cluster_fix_verification_plan, build_enterprise_bug_triage_matrix, build_enterprise_release_gate_decision, build_high_value_attack_plan, build_high_value_capability_assessment, build_high_value_capability_trend, build_high_value_issue_clusters, build_high_value_pattern_memory, build_high_value_repro_evidence_pack, build_high_value_self_improvement_report, build_high_value_summary, build_oracle_coverage_summary, build_report, build_risk_learning_profile, roi_metrics, write_json  # lazy
        from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
        from ._scenarios import build_enterprise_user_preparation_guide, build_execution_readiness_plan, build_scenario_data_orchestration, evaluate_scenario_coverage  # lazy: avoid circular import
        prd = read_text(self.config.public_artifacts / "prd.md")
        openapi = read_json(self.config.public_artifacts / "openapi.json")
        sut_config = read_json(self.config.public_artifacts / "sut_config.json")
        accounts = read_json(self.config.public_artifacts / "test_accounts.json")
        business_model = infer_business_model(prd, openapi, accounts)
        business_model["_source_prd"] = prd
        previous_capability_assessment = load_high_value_capability_assessment(self.workspace, self.output)
        previous_capability_trend = load_high_value_capability_trend(self.workspace, self.output)
        business_model["high_value_pattern_memory"] = load_high_value_pattern_memory(self.workspace)
        business_model["risk_learning_profile"] = load_risk_learning_profile(self.workspace, self.output)
        business_model["high_value_attack_plan"] = load_high_value_attack_plan(self.workspace, self.output)
        business_model["high_value_capability_assessment"] = previous_capability_assessment
        business_model["business_adaptation_profile"] = load_business_adaptation_profile(self.workspace, self.config.output_root, self.config.project)
        if not business_model["business_adaptation_profile"]:
            business_model["business_adaptation_profile"] = build_lightweight_business_adaptation_profile(business_model)
        business_knowledge_model = load_business_knowledge_model(self.config)
        business_model = enrich_business_model_with_knowledge(business_model, business_knowledge_model)
        rules = extract_business_rules(prd, openapi)
        invariants = build_invariants(rules)
        discovery_mode = normalize_discovery_mode(self.config.discovery_mode)
        probe_policy_profile = normalize_probe_policy_profile(os.environ.get("PROBE_POLICY_PROFILE"), discovery_mode)
        probes = generate_defect_probes(invariants, business_model, discovery_mode)
        scenario_coverage = evaluate_scenario_coverage(business_model["business_scenarios"], probes, accounts)
        readiness = build_execution_readiness_plan(business_model, scenario_coverage, probes, accounts)
        data_orchestration = build_scenario_data_orchestration(readiness, accounts)
        user_preparation = build_enterprise_user_preparation_guide(readiness, data_orchestration)
        probe_strategy = {
            "mode": "zero_config_auto_probe_generation",
            "discovery_mode": discovery_mode,
            "probe_policy_profile": probe_policy_profile,
            "allowed_probe_sources": sorted(allowed_sources_for_policy(probe_policy_profile, discovery_mode)),
            "manual_industry_pack_required": False,
            "business_knowledge_enabled": business_model["enterprise_knowledge"]["enabled"],
            "business_knowledge_source": business_model["enterprise_knowledge"]["source"],
            "business_knowledge_module_count": business_model["enterprise_knowledge"]["module_count"],
            "business_knowledge_risk_count": business_model["enterprise_knowledge"]["risk_count"],
            "business_knowledge_rule_count": business_model["enterprise_knowledge"]["rule_count"],
            "high_value_memory_enabled": bool((business_model.get("high_value_pattern_memory") or {}).get("top_patterns")),
            "high_value_memory_pattern_count": int((business_model.get("high_value_pattern_memory") or {}).get("pattern_count") or 0),
            "risk_learning_profile_enabled": bool(business_model.get("risk_learning_profile")),
            "risk_learning_profile_sample_count": int((business_model.get("risk_learning_profile") or {}).get("learned_from_findings") or 0),
            "high_value_attack_plan_enabled": bool(business_model.get("high_value_attack_plan")),
            "high_value_attack_plan_focus_count": int((business_model.get("high_value_attack_plan") or {}).get("total_focus_risks") or 0),
            "high_value_capability_assessment_enabled": bool(business_model.get("high_value_capability_assessment")),
            "high_value_capability_gap_count": len((business_model.get("high_value_capability_assessment") or {}).get("capability_gaps") or []),
            "business_adaptation_enabled": bool(business_model.get("business_adaptation_profile")),
            "business_adaptation_domain_count": len((business_model.get("business_adaptation_profile") or {}).get("selected_domains") or []),
            "inferred_industry": business_model["industry"],
            "business_object_count": len(business_model["business_objects"]),
            "operation_count": len(business_model["operations"]),
            "auto_invariant_count": len(business_model["inferred_invariants"]),
            "semantic_graph_nodes": len(business_model["semantic_graph"]["nodes"]),
            "semantic_graph_edges": len(business_model["semantic_graph"]["edges"]),
            "state_machine_count": len(business_model["state_machines"]),
            "data_lineage_count": len(business_model["data_lineage"]),
            "entity_dependency_count": len(business_model["entity_dependencies"]),
            "business_scenario_count": len(business_model["business_scenarios"]),
            "scenario_coverage_rate": scenario_coverage["coverage_rate"],
            "executable_scenario_rate": scenario_coverage["executable_rate"],
            "auto_preparable_scenarios": readiness["execution_readiness_plan"]["auto_preparable_scenarios"],
            "auto_orchestratable_scenarios": data_orchestration["auto_orchestratable_scenarios"],
            "enterprise_user_action_count": user_preparation["user_action_count"],
            "testability_gap_count": len(readiness["testability_gaps"]),
            "probe_count": len(probes),
            "probe_execution_budget": os.environ.get("PROBE_EXECUTION_BUDGET", ""),
            "probe_parallel_workers": os.environ.get("PROBE_PARALLEL_WORKERS", "1"),
            "probe_timeout_ms": os.environ.get("PROBE_TIMEOUT_MS", "8000"),
            "probe_budget_policy_path": os.environ.get("PROBE_BUDGET_POLICY_PATH", ""),
            "generic_probe_count": sum(1 for p in probes if p.get("source") == "generic_auto"),
            "journey_probe_count": sum(1 for p in probes if p.get("source") == "journey_auto"),
            "feedback_learning_probe_count": sum(1 for p in probes if p.get("source") == "feedback_learning"),
            "adaptive_policy_probe_count": sum(1 for p in probes if p.get("source") == "adaptive_policy"),
            "business_knowledge_probe_count": sum(1 for p in probes if p.get("source") == "business_knowledge"),
            "business_adaptation_probe_count": sum(1 for p in probes if p.get("source") == "business_adaptation_layer"),
            "high_value_memory_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory"),
            "high_value_memory_expansion_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory" and p.get("memory_context", {}).get("memory_variant") == "semantic_expansion"),
            "risk_learning_profile_probe_count": sum(1 for p in probes if p.get("source") == "risk_learning_profile"),
            "high_value_attack_plan_probe_count": sum(1 for p in probes if p.get("source") == "high_value_attack_plan"),
            "capability_gap_probe_count": sum(1 for p in probes if p.get("source") == "capability_gap"),
            "oracle_gap_probe_count": sum(1 for p in probes if p.get("source") == "oracle_gap"),
            "feedback_adjusted_probe_count": sum(1 for p in probes if p.get("source") == "feedback_adjusted"),
            "rag_enhanced_probe_count": sum(1 for p in probes if p.get("source") == "rag_enhanced"),
            "probe_policy_profile": probe_policy_profile,
            "allowed_probe_sources": sorted(allowed_sources_for_policy(probe_policy_profile, discovery_mode)),
            "note": "Probe sources are derived from project inputs and policy data. PROBE_POLICY_PROFILE controls the source mix.",
        }
        write_json(self.workspace / "business_model.json", business_model)
        write_json(self.workspace / "business_scenarios.json", business_model["business_scenarios"])
        write_json(self.workspace / "scenario_coverage.json", scenario_coverage)
        write_json(self.workspace / "test_data_requirements.json", {"items": readiness["test_data_requirements"]})
        write_json(self.workspace / "testability_gaps.json", {"items": readiness["testability_gaps"]})
        write_json(self.workspace / "execution_readiness_plan.json", readiness["execution_readiness_plan"])
        write_json(self.workspace / "scenario_data_orchestration.json", data_orchestration)
        write_json(self.workspace / "enterprise_user_preparation_guide.json", user_preparation)
        write_json(self.workspace / "business_rules.json", rules)
        write_json(self.workspace / "invariants.json", invariants)
        write_json(self.workspace / "defect_probes.json", probes)
        write_json(self.workspace / "business_knowledge_probes.json", {"count": sum(1 for p in probes if p.get("source") == "business_knowledge"), "items": [p for p in probes if p.get("source") == "business_knowledge"]})
        write_json(self.workspace / "business_adaptation_probes.json", {"count": sum(1 for p in probes if p.get("source") == "business_adaptation_layer"), "items": [p for p in probes if p.get("source") == "business_adaptation_layer"]})
        write_json(self.workspace / "high_value_memory_probes.json", {"count": sum(1 for p in probes if p.get("source") == "high_value_memory"), "items": [p for p in probes if p.get("source") == "high_value_memory"]})
        write_json(self.workspace / "risk_learning_profile_probes.json", {"count": sum(1 for p in probes if p.get("source") == "risk_learning_profile"), "items": [p for p in probes if p.get("source") == "risk_learning_profile"]})
        write_json(self.workspace / "high_value_attack_plan_probes.json", {"count": sum(1 for p in probes if p.get("source") == "high_value_attack_plan"), "items": [p for p in probes if p.get("source") == "high_value_attack_plan"]})
        write_json(self.workspace / "capability_gap_probes.json", {"count": sum(1 for p in probes if p.get("source") == "capability_gap"), "items": [p for p in probes if p.get("source") == "capability_gap"]})
        write_json(self.workspace / "oracle_gap_probes.json", {"count": sum(1 for p in probes if p.get("source") == "oracle_gap"), "items": [p for p in probes if p.get("source") == "oracle_gap"]})
        write_json(self.workspace / "feedback_learning_probes.json", {"count": sum(1 for p in probes if p.get("source") == "feedback_learning"), "items": [p for p in probes if p.get("source") == "feedback_learning"]})
        write_json(self.workspace / "adaptive_policy_probes.json", {"count": sum(1 for p in probes if p.get("source") == "adaptive_policy"), "items": [p for p in probes if p.get("source") == "adaptive_policy"]})
        write_json(self.workspace / "probe_generation_strategy.json", probe_strategy)
        client = HttpClient(sut_config["base_url"])
        execution = self.execute_probes(client, accounts, probes)
        discovered = select_discovered_bugs(execution)
        candidate_findings = [to_discovered_bug(item) for item in execution if item["assertion_result"] == "failed"]
        high_value_summary = build_high_value_summary(discovered)
        oracle_coverage_summary = build_oracle_coverage_summary(probes, discovered)
        high_value_pattern_memory = build_high_value_pattern_memory(discovered)
        risk_learning_profile = build_risk_learning_profile(discovered, high_value_summary, probe_strategy, high_value_pattern_memory)
        high_value_attack_plan = build_high_value_attack_plan(discovered, high_value_summary, oracle_coverage_summary, risk_learning_profile)
        high_value_issue_clusters = build_high_value_issue_clusters(discovered)
        business_risk_radar = build_business_risk_radar(high_value_summary, high_value_issue_clusters, oracle_coverage_summary)
        enterprise_release_gate_decision = build_enterprise_release_gate_decision(high_value_summary, high_value_issue_clusters, oracle_coverage_summary, high_value_attack_plan)
        cluster_fix_verification_plan = build_cluster_fix_verification_plan(enterprise_release_gate_decision, high_value_issue_clusters)
        high_value_capability_assessment = build_high_value_capability_assessment(high_value_summary, oracle_coverage_summary, high_value_issue_clusters, enterprise_release_gate_decision, cluster_fix_verification_plan, probe_strategy)
        high_value_self_improvement_report = build_high_value_self_improvement_report(previous_capability_assessment, high_value_capability_assessment, high_value_summary, probe_strategy)
        high_value_capability_trend = build_high_value_capability_trend(previous_capability_trend, high_value_capability_assessment, high_value_self_improvement_report, probe_strategy)
        bundle = [to_evidence(item) for item in execution]
        high_value_repro_evidence_pack = build_high_value_repro_evidence_pack(discovered, high_value_issue_clusters, business_risk_radar, cluster_fix_verification_plan, bundle)
        enterprise_bug_triage_matrix = build_enterprise_bug_triage_matrix(high_value_issue_clusters, business_risk_radar, enterprise_release_gate_decision, high_value_repro_evidence_pack)
        data = {
            "project": self.config.project,
            "discovery_mode": discovery_mode,
            "probe_policy_profile": probe_policy_profile,
            "rules": len(rules),
            "business_model": {
                "industry": business_model["industry"],
                "objects": len(business_model["business_objects"]),
                "operations": len(business_model["operations"]),
                "auto_invariants": len(business_model["inferred_invariants"]),
                "semantic_graph_nodes": len(business_model["semantic_graph"]["nodes"]),
                "semantic_graph_edges": len(business_model["semantic_graph"]["edges"]),
                "state_machines": len(business_model["state_machines"]),
                "data_lineage": len(business_model["data_lineage"]),
                "entity_dependencies": len(business_model["entity_dependencies"]),
                "business_scenarios": len(business_model["business_scenarios"]),
                "scenario_coverage_rate": scenario_coverage["coverage_rate"],
                "executable_scenario_rate": scenario_coverage["executable_rate"],
                "auto_preparable_scenarios": readiness["execution_readiness_plan"]["auto_preparable_scenarios"],
                "auto_orchestratable_scenarios": data_orchestration["auto_orchestratable_scenarios"],
                "enterprise_user_action_count": user_preparation["user_action_count"],
                "testability_gaps": len(readiness["testability_gaps"]),
                "business_knowledge_enabled": business_model["enterprise_knowledge"]["enabled"],
                "business_knowledge_modules": business_model["enterprise_knowledge"]["module_count"],
                "business_knowledge_risks": business_model["enterprise_knowledge"]["risk_count"],
                "business_knowledge_rules": business_model["enterprise_knowledge"]["rule_count"],
                "manual_industry_pack_required": False,
            },
            "invariants": len(invariants),
            "probes": len(probes),
            "business_knowledge_probe_count": sum(1 for p in probes if p.get("source") == "business_knowledge"),
            "business_adaptation_probe_count": sum(1 for p in probes if p.get("source") == "business_adaptation_layer"),
            "high_value_memory_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory"),
            "high_value_memory_expansion_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory" and p.get("memory_context", {}).get("memory_variant") == "semantic_expansion"),
            "risk_learning_profile_probe_count": sum(1 for p in probes if p.get("source") == "risk_learning_profile"),
            "high_value_attack_plan_probe_count": sum(1 for p in probes if p.get("source") == "high_value_attack_plan"),
            "capability_gap_probe_count": sum(1 for p in probes if p.get("source") == "capability_gap"),
            "oracle_gap_probe_count": sum(1 for p in probes if p.get("source") == "oracle_gap"),
            "generic_probe_count": sum(1 for p in probes if p.get("source") == "generic_auto"),
            "journey_probe_count": sum(1 for p in probes if p.get("source") == "journey_auto"),
            "feedback_learning_probe_count": sum(1 for p in probes if p.get("source") == "feedback_learning"),
            "adaptive_policy_probe_count": sum(1 for p in probes if p.get("source") == "adaptive_policy"),
            "rag_enhanced_probe_count": sum(1 for p in probes if p.get("source") == "rag_enhanced"),
            "discovered_bugs": discovered,
            "candidate_findings": len(candidate_findings),
            "deduplicated_findings": max(0, len(candidate_findings) - len(discovered)),
            "high_value_summary": high_value_summary,
            "oracle_coverage_summary": oracle_coverage_summary,
            "high_value_pattern_memory": high_value_pattern_memory,
            "risk_learning_profile": risk_learning_profile,
            "high_value_attack_plan": high_value_attack_plan,
            "high_value_issue_clusters": high_value_issue_clusters,
            "business_risk_radar": business_risk_radar,
            "enterprise_release_gate_decision": enterprise_release_gate_decision,
            "cluster_fix_verification_plan": cluster_fix_verification_plan,
            "high_value_repro_evidence_pack": high_value_repro_evidence_pack,
            "enterprise_bug_triage_matrix": enterprise_bug_triage_matrix,
            "high_value_capability_assessment": high_value_capability_assessment,
            "high_value_self_improvement_report": high_value_self_improvement_report,
            "high_value_capability_trend": high_value_capability_trend,
            "evidence_bundle": bundle,
            "scenario_coverage": scenario_coverage,
            "test_data_requirements": readiness["test_data_requirements"],
            "testability_gaps": readiness["testability_gaps"],
            "execution_readiness_plan": readiness["execution_readiness_plan"],
            "scenario_data_orchestration": data_orchestration,
            "enterprise_user_preparation_guide": user_preparation,
            "roi_metrics": roi_metrics(len(probes), len(discovered)),
        }
        write_json(self.workspace / "probe_execution_result.json", execution)
        write_json(self.workspace / "high_value_pattern_memory.json", high_value_pattern_memory)
        write_json(self.workspace / "risk_learning_profile.json", risk_learning_profile)
        write_json(self.workspace / "high_value_attack_plan.json", high_value_attack_plan)
        write_json(self.workspace / "high_value_issue_clusters.json", high_value_issue_clusters)
        write_json(self.workspace / "business_risk_radar.json", business_risk_radar)
        write_json(self.workspace / "enterprise_release_gate_decision.json", enterprise_release_gate_decision)
        write_json(self.workspace / "cluster_fix_verification_plan.json", cluster_fix_verification_plan)
        write_json(self.workspace / "high_value_repro_evidence_pack.json", high_value_repro_evidence_pack)
        write_json(self.workspace / "enterprise_bug_triage_matrix.json", enterprise_bug_triage_matrix)
        write_json(self.workspace / "high_value_capability_assessment.json", high_value_capability_assessment)
        write_json(self.workspace / "high_value_self_improvement_report.json", high_value_self_improvement_report)
        write_json(self.workspace / "high_value_capability_trend.json", high_value_capability_trend)
        write_json(self.workspace / "fix_regression_probes.json", cluster_fix_verification_plan["regression_probes"])
        write_json(self.workspace / "audit_logs.json", {"private_paths_accessed": [], "blocked_tokens": list(PRIVATE_BLOCKLIST)})
        write_json(self.output / "discovered_bugs.json", {"count": len(discovered), "discovery_mode": discovery_mode, "probe_policy_profile": probe_policy_profile, "bugs": discovered})
        write_json(self.output / "high_value_defect_summary.json", high_value_summary)
        write_json(self.output / "oracle_coverage_summary.json", oracle_coverage_summary)
        write_json(self.output / "high_value_pattern_memory.json", high_value_pattern_memory)
        write_json(self.output / "risk_learning_profile.json", risk_learning_profile)
        write_json(self.output / "high_value_attack_plan.json", high_value_attack_plan)
        write_json(self.output / "high_value_issue_clusters.json", high_value_issue_clusters)
        write_json(self.output / "business_risk_radar.json", business_risk_radar)
        write_json(self.output / "enterprise_release_gate_decision.json", enterprise_release_gate_decision)
        write_json(self.output / "cluster_fix_verification_plan.json", cluster_fix_verification_plan)
        write_json(self.output / "high_value_repro_evidence_pack.json", high_value_repro_evidence_pack)
        write_json(self.output / "enterprise_bug_triage_matrix.json", enterprise_bug_triage_matrix)
        write_json(self.output / "high_value_capability_assessment.json", high_value_capability_assessment)
        write_json(self.output / "high_value_self_improvement_report.json", high_value_self_improvement_report)
        write_json(self.output / "high_value_capability_trend.json", high_value_capability_trend)
        write_json(self.output / "candidate_findings.json", {"count": len(candidate_findings), "bugs": candidate_findings})
        write_json(self.output / "evidence_bundle.json", {"count": len(bundle), "items": bundle})
        write_json(self.output / "defect_discovery_data.json", data)
        write_json(self.output / "roi_metrics.json", data["roi_metrics"])
        (self.output / "bug_drafts.md").write_text(build_bug_drafts(discovered), encoding="utf-8")
        (self.output / "defect_discovery_report.html").write_text(build_report(data), encoding="utf-8")
        return data

    def execute_probes(self, client: HttpClient, accounts: dict, probes: list[dict]) -> list[dict]:
        from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
        workers_raw = os.environ.get("PROBE_PARALLEL_WORKERS", "1").strip()
        try:
            max_workers = max(1, int(workers_raw or "1"))
        except Exception:
            max_workers = 1
        timeout_raw = os.environ.get("PROBE_TIMEOUT_MS", "8000").strip()
        try:
            timeout_ms = max(1000, int(timeout_raw or "8000"))
        except Exception:
            timeout_ms = 8000

        def run_one(index_item: tuple[int, dict]) -> tuple[int, dict]:
            from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
            index, item = index_item
            local_client = HttpClient(client.base_url)
            start = time.time()
            try:
                local_client.request("POST", "/reset")
                tokens = login_accounts(local_client, accounts)
                result = execute_probe(local_client, tokens, item)
                result["execution_status"] = "completed"
            except Exception as exc:
                result = {
                    "probe": item,
                    "request": {"method": item.get("method"), "path": item.get("path")},
                    "response": {"status_code": 0, "body": {"error": str(exc)[:500]}, "duration_ms": 0},
                    "expected": item.get("expected"),
                    "actual": str(exc)[:500],
                    "assertion_result": "error",
                    "bug_signal": "probe execution error",
                    "confidence": 0.0,
                    "execution_status": "error",
                }
            result["execution_duration_ms"] = round((time.time() - start) * 1000, 2)
            result["execution_worker_mode"] = "parallel" if max_workers > 1 else "sequential"
            result["execution_timeout_ms"] = timeout_ms
            return index, result

        if max_workers <= 1:
            return [run_one((idx, item))[1] for idx, item in enumerate(probes)]

        # Phase11: opt-in parallel probe runner. For shared-state SUTs this is
        # intended for benchmark profiling and should be used with moderate worker
        # counts or isolated SUT instances. Results are restored to original probe
        # order so evaluator output remains stable.
        results: list[dict | None] = [None] * len(probes)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(run_one, (idx, item)) for idx, item in enumerate(probes)]
            for fut in as_completed(futures):
                idx, result = fut.result()
                results[idx] = result
        return [r for r in results if r is not None]


def login_accounts(client: HttpClient, accounts: dict) -> dict[str, str]:
    from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
    del client
    tokens: dict[str, str] = {}
    for account in accounts.get("accounts", []):
        if not isinstance(account, dict):
            continue
        status = str(account.get("status") or account.get("account_status") or "active").lower()
        if status in {"disabled", "locked", "inactive", "suspended"}:
            continue
        token = str(account.get("access_token") or account.get("token") or "").strip()
        role = str(account.get("role") or "").strip()
        username = str(account.get("username") or account.get("account_ref") or "").strip()
        if not token or not role:
            continue
        tokens.setdefault(role, token)
        if username:
            tokens[username] = token
    return tokens


def execute_probe(client: HttpClient, tokens: dict[str, str], item: dict) -> dict:
    from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
    method = str(item.get("method") or "GET").upper()
    steps = [step for step in item.get("steps", []) if isinstance(step, dict)]
    has_write = method in {"POST", "PUT", "PATCH", "DELETE"} or any(
        str(step.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        for step in steps
    )
    if has_write:
        return {
            "probe": item,
            "request": {
                "method": method,
                "path": str(item.get("path") or ""),
                "body": None,
                "actor": str(item.get("actor") or ""),
                "sent": False,
            },
            "response": {"status_code": 0, "body": {}},
            "expected": item.get("expected"),
            "actual": "legacy write path is not governed by Experiment Executor",
            "assertion_result": "blocked",
            "execution_status": "blocked",
            "reason_code": "BLOCKED_UNGOVERNED_LEGACY_WRITE",
            "bug_signal": item.get("bug_signal"),
            "confidence": 0.0,
        }
    path = str(item.get("path") or "")
    if "{" in path or "}" in path or ":" in path:
        # ── Best-effort placeholder resolution ──
        # Try to substitute path placeholders with generated test values
        # rather than immediately blocking. Enterprise APIs often have path
        # parameters that cannot be pre-resolved from documented endpoints.
        resolved_path = path
        try:
            from ai_test_asset_center.real_id_resolver_base import normalize_path_placeholders, infer_path_params
            from ai_test_asset_center.enterprise_test_data_engine import _generate_value, _detect_field_semantic
            normalized = normalize_path_placeholders(path)
            params = infer_path_params(normalized)
            for param in params:
                semantic = _detect_field_semantic(param)
                value = str(_generate_value(semantic.get("generator", "numeric_id"), param))
                resolved_path = resolved_path.replace("{" + param + "}", value)
                resolved_path = resolved_path.replace(":" + param, value)
            if resolved_path != path:
                item = {**item, "path": resolved_path}
            else:
                return {
                    "probe": item,
                    "request": {"method": method, "path": path, "body": None, "sent": False},
                    "response": {"status_code": 0, "body": {}},
                    "expected": item.get("expected"),
                    "actual": "source path binding is unresolved",
                    "assertion_result": "blocked",
                    "execution_status": "blocked",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "bug_signal": item.get("bug_signal"),
                    "confidence": 0.0,
                }
        except Exception:
            pass
        if "{" in resolved_path or "}" in resolved_path or ":" in resolved_path:
            return {
                "probe": item,
                "request": {"method": method, "path": path, "body": None, "sent": False},
                "response": {"status_code": 0, "body": {}},
                "expected": item.get("expected"),
                "actual": "source path binding is unresolved",
                "assertion_result": "blocked",
                "execution_status": "blocked",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "bug_signal": item.get("bug_signal"),
                "confidence": 0.0,
            }
        item = {**item, "path": resolved_path}
    actor = str(item.get("actor") or "")
    token = None if actor in {"anonymous", "system"} else tokens.get(actor) or tokens.get("normal_user")
    return execute_generic_probe(client, token, item, {})

def execute_generic_probe(client: HttpClient, token: str | None, item: dict, body: dict) -> dict:
    from ._model import DiscoveryConfig, HttpClient, enrich_business_model_with_knowledge, extract_business_rules, infer_business_model, load_business_knowledge_model, read_json, read_text  # lazy: avoid circular import
    response = client.request(item["method"], item["path"], token=token, body=body if item["method"] != "GET" else None)
    risk = item["risk_type"]
    if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation"}:
        failed = response["status_code"] < 400
        actual = response["status_code"]
    elif risk == "idempotency":
        second = client.request(item["method"], item["path"], token=token, body=body if item["method"] != "GET" else None)
        failed = idempotency_changed(response, second)
        actual = {"first": response["body"], "second": second["body"]}
        response = second
    elif risk == "time_concurrency":
        failed = response["status_code"] >= 500 or business_signal_suspicious(response["body"])
        actual = {"status_code": response["status_code"], "body": response["body"]}
    elif risk in {"money_consistency", "stock_consistency", "coupon_abuse", "state_flow", "state_consistency", "refund_abuse", "payment_callback", "batch_import", "notification_risk", "integration_risk"}:
        # For blind probes, a successful response to an adversarial input is itself
        # a signal when the probe expected rejection (400/403/409). Business payload
        # signals are still used for consistency checks that return 200.
        failed = (item.get("expected_status") in {400, 401, 403, 409} and response["status_code"] < 400) or (response["status_code"] < 400 and business_signal_suspicious(response["body"]))
        actual = {"status_code": response["status_code"], "body": response["body"]}
    elif risk in {"approval_bypass", "audit_compliance", "search_report", "configuration_risk", "privilege_escalation", "locked_account_bypass"}:
        failed = response["status_code"] < 400
        actual = {"status_code": response["status_code"], "body": response["body"]}
    else:
        failed = response["status_code"] != item["expected_status"]
        actual = response["status_code"]
    return {"probe": item, "request": {"method": item["method"], "path": item["path"], "body": body, "actor": item["actor"]}, "response": response, "expected": item["expected"], "actual": actual, "assertion_result": "failed" if failed else "passed", "bug_signal": item["bug_signal"], "confidence": 0.84 if failed else 0.62}


def _is_identity_key(key: str) -> bool:
    """Generic identity key detection (industry-neutral)."""
    k = key.lower()
    return k == "id" or k.endswith("_id") or k.endswith("id") and len(k) > 2


def _is_numeric_semantic_key(key: str) -> bool:
    """Generic numeric/amount/balance key detection (industry-neutral)."""
    k = key.lower()
    numeric_suffixes = ("_amount", "amount", "_balance", "balance", "_total", "total",
                        "_count", "count", "_qty", "qty", "_quantity", "quantity",
                        "_stock", "stock", "_value", "value", "_sum", "sum",
                        "_paid", "_refunded", "_due", "_fee", "_price", "_cost")
    return any(k.endswith(s) or k == s.lstrip("_") for s in numeric_suffixes)


def idempotency_changed(first: dict, second: dict) -> bool:
    a = first.get("body") or {}
    b = second.get("body") or {}
    # Generic identity key comparison
    for key in set(list(a.keys()) + list(b.keys())):
        if _is_identity_key(key):
            if a.get(key) and b.get(key) and a.get(key) != b.get(key):
                return True
    # Generic numeric field comparison
    for key in set(list(a.keys()) + list(b.keys())):
        if _is_numeric_semantic_key(key):
            if isinstance(a.get(key), (int, float)) and isinstance(b.get(key), (int, float)) and a.get(key) != b.get(key):
                return True
    return first.get("status_code") in {200, 201} and second.get("status_code") in {200, 201} and a != b


def business_signal_suspicious(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    # Generic: check all numeric-semantic keys for negative values
    for key, value in body.items():
        if _is_numeric_semantic_key(key) and isinstance(value, (int, float)) and value < 0:
            return True
    status = str(body.get("status") or "").lower()
    if status in {"cancelled_pending"}:
        return True
    return any(key in body for key in ("warning", "bug_signal"))


def body_for_probe(item: dict) -> dict:
    example = item.get("request_example")
    return dict(example) if isinstance(example, dict) else {}


def generic_body_for_probe(item: dict) -> dict:
    return body_for_probe(item)


def predicted_template_for_probe(p: dict) -> str:
    explicit = str(p.get("predicted_template_id") or "").strip()
    if explicit:
        return explicit
    risk = re.sub(r"[^A-Z0-9]+", "_", str(p.get("risk_type") or "generic").upper()).strip("_")
    method = re.sub(r"[^A-Z0-9]+", "_", str(p.get("method") or "UNKNOWN").upper()).strip("_")
    path = str(p.get("path") or "").split("?", 1)[0]
    route = re.sub(r"[^A-Z0-9]+", "_", path.upper()).strip("_") or "ROOT"
    return f"SOURCE_{risk or 'GENERIC'}_{method or 'UNKNOWN'}_{route[:80]}"


def evidence_signature_for(item: dict) -> str:
    p = item["probe"]
    status = item.get("response", {}).get("status_code")
    return f"{p.get('risk_type')}|{p.get('actor')}|{p.get('method')}|{str(p.get('path','')).split('?')[0]}|{status}"


def business_object_for_api(api: str) -> str:
    path = api.split(" ", 1)[-1].split("?", 1)[0].strip().lower()
    infrastructure = {"api", "service", "services", "admin", "manage", "manager", "console"}
    for segment in path.strip("/").split("/"):
        if not segment or segment.startswith(("{", ":")):
            continue
        if segment in infrastructure or re.fullmatch(r"v\d+", segment):
            continue
        return segment
    return "root"


# Generic path-verb patterns for operation type inference (industry-neutral)
_PATH_ACTION_VERB_RE = re.compile(
    r"/(cancel|close|void|approve|reject|activate|deactivate|suspend|resume|"
    r"enable|disable|archive|restore|publish|unpublish|submit|withdraw|"
    r"confirm|verify|validate|process|execute|trigger|send|notify|"
    r"assign|unassign|transfer|move|copy|clone|duplicate|merge|split|"
    r"start|stop|pause|continue|retry|abort|complete|finalize|"
    r"lock|unlock|freeze|unfreeze|open|reopen|escalate|resolve)\b",
    re.I,
)


def operation_for_method(method: str, path: str) -> str:
    """Infer generic operation type from HTTP method and path verbs.

    Industry-neutral: uses only generic action verbs, no domain-specific terms.
    """
    method_upper = method.upper()
    # Check for explicit action verb in path
    match = _PATH_ACTION_VERB_RE.search(path.lower())
    if match:
        return match.group(1).lower()
    # Fall back to HTTP method semantics
    if method_upper == "GET":
        return "view"
    if method_upper == "POST":
        # Check if path suggests creation vs action
        if re.search(r"/(create|new|add|register|signup)\b", path.lower()):
            return "create"
        return "create"
    if method_upper == "PUT":
        return "replace"
    if method_upper == "PATCH":
        return "update"
    if method_upper == "DELETE":
        return "delete"
    return method.lower()


def to_discovered_bug(item: dict) -> dict:
    from ._reporting import high_value_profile, value_tier  # lazy
    p = item["probe"]
    profile = high_value_profile(item)
    score = profile["total_score"]
    api = p.get("api_template") or f"{p['method']} {p['path'].split('?')[0]}"
    bug = {
        "discovered_bug_id": f"DISC_{p['probe_id']}",
        "probe_id": p["probe_id"],
        "title": p["title"],
        "risk_type": p["risk_type"],
        "predicted_risk_type": p["risk_type"],
        "predicted_template_id": predicted_template_for_probe(p),
        "severity": p["severity"],
        "related_apis": [api],
        "affected_api": api,
        "actor": p.get("actor"),
        "operation": operation_for_method(p.get("method", ""), p.get("path", "")),
        "business_object": business_object_for_api(api),
        "expected": item["expected"],
        "actual": item["actual"],
        "bug_signal": item["bug_signal"],
        "evidence_signature": evidence_signature_for(item),
        "confidence": item["confidence"],
        "bug_value_score": score,
        "high_value_profile": profile,
        "value_tier": value_tier(score),
        "evidence_ref": p["probe_id"],
        "discovery_mode": os.environ.get("DEFECT_DISCOVERY_MODE", "blind"),
        "probe_policy_profile": normalize_probe_policy_profile(os.environ.get("PROBE_POLICY_PROFILE"), os.environ.get("DEFECT_DISCOVERY_MODE", "blind")),
        "probe_source": p.get("source"),
    }
    context = item.get("business_context") or p.get("business_context")
    if context:
        bug["business_context"] = context
    memory_context = item.get("memory_context") or p.get("memory_context")
    if memory_context:
        bug["memory_context"] = memory_context
    risk_learning_context = item.get("risk_learning_context") or p.get("risk_learning_context")
    if risk_learning_context:
        bug["risk_learning_context"] = risk_learning_context
    adaptation_context = item.get("business_adaptation_context") or p.get("business_adaptation_context")
    if adaptation_context:
        bug["business_adaptation_context"] = adaptation_context
    oracle_gap_context = item.get("oracle_gap_context") or p.get("oracle_gap_context")
    if oracle_gap_context:
        bug["oracle_gap_context"] = oracle_gap_context
    capability_gap_context = item.get("capability_gap_context") or p.get("capability_gap_context")
    if capability_gap_context:
        bug["capability_gap_context"] = capability_gap_context
    attack_plan_context = item.get("attack_plan_context") or p.get("attack_plan_context")
    if attack_plan_context:
        bug["attack_plan_context"] = attack_plan_context
    return bug


def select_discovered_bugs(execution: list[dict]) -> list[dict]:
    failed = [item for item in execution if item["assertion_result"] == "failed"]
    promoted: list[dict] = []
    covered_exact: set[tuple[str, ...]] = set()
    covered_base_by_pattern: set[tuple[str, str]] = set()
    source_order = {"business_knowledge": 0, "business_adaptation_layer": 1, "risk_learning_profile": 2, "high_value_attack_plan": 3, "capability_gap": 4, "oracle_gap": 5, "high_value_memory": 6, "pattern_library": 7, "feedback_learning": 8, "feedback_adjusted": 9, "adaptive_policy": 10, "rag_enhanced": 11, "generic_auto": 12}
    allowed_sources = set(source_order)
    for item in sorted(failed, key=lambda x: source_order.get(x["probe"].get("source"), 9)):
        source = item["probe"].get("source")
        if source not in allowed_sources:
            continue
        if source == "generic_auto" and item["probe"].get("risk_type") == "time_concurrency":
            continue
        bug = to_discovered_bug(item)
        base_key = discovery_key(bug)
        if source in {"business_knowledge", "business_adaptation_layer", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "high_value_memory", "pattern_library", "feedback_learning", "feedback_adjusted", "adaptive_policy", "rag_enhanced"}:
            exact_key = (bug.get("risk_type", ""), bug.get("related_apis", [""])[0], bug.get("probe_id", ""))
            if exact_key in covered_exact:
                continue
            promoted.append(bug)
            covered_exact.add(exact_key)
            if source == "pattern_library":
                covered_base_by_pattern.add(base_key)
            continue
        if source == "generic_auto" and base_key in covered_base_by_pattern:
            continue
        exact_key = base_key
        if exact_key in covered_exact:
            continue
        promoted.append(bug)
        covered_exact.add(exact_key)
    return promoted

def discovery_key(bug: dict) -> tuple[str, str]:
    api = bug.get("related_apis", [""])[0]
    return bug.get("risk_type", ""), api


def to_evidence(item: dict) -> dict:
    evidence = {"probe_id": item["probe"]["probe_id"], "request": item["request"], "response": {"status_code": item["response"]["status_code"], "body_excerpt": json.dumps(item["response"]["body"], ensure_ascii=False)[:1000]}, "expected": item["expected"], "actual": item["actual"], "assertion_result": item["assertion_result"], "bug_signal": item["bug_signal"], "confidence": item["confidence"]}
    if item.get("business_context"):
        evidence["business_context"] = item["business_context"]
    elif item.get("probe", {}).get("business_context"):
        evidence["business_context"] = item["probe"]["business_context"]
    if item.get("memory_context"):
        evidence["memory_context"] = item["memory_context"]
    elif item.get("probe", {}).get("memory_context"):
        evidence["memory_context"] = item["probe"]["memory_context"]
    if item.get("risk_learning_context"):
        evidence["risk_learning_context"] = item["risk_learning_context"]
    elif item.get("probe", {}).get("risk_learning_context"):
        evidence["risk_learning_context"] = item["probe"]["risk_learning_context"]
    if item.get("business_adaptation_context"):
        evidence["business_adaptation_context"] = item["business_adaptation_context"]
    elif item.get("probe", {}).get("business_adaptation_context"):
        evidence["business_adaptation_context"] = item["probe"]["business_adaptation_context"]
    if item.get("oracle_gap_context"):
        evidence["oracle_gap_context"] = item["oracle_gap_context"]
    elif item.get("probe", {}).get("oracle_gap_context"):
        evidence["oracle_gap_context"] = item["probe"]["oracle_gap_context"]
    if item.get("capability_gap_context"):
        evidence["capability_gap_context"] = item["capability_gap_context"]
    elif item.get("probe", {}).get("capability_gap_context"):
        evidence["capability_gap_context"] = item["probe"]["capability_gap_context"]
    if item.get("attack_plan_context"):
        evidence["attack_plan_context"] = item["attack_plan_context"]
    elif item.get("probe", {}).get("attack_plan_context"):
        evidence["attack_plan_context"] = item["probe"]["attack_plan_context"]
    if "journey_steps" in item:
        evidence["journey_steps"] = [
            {
                "step": step_item["step"],
                "request": step_item["request"],
                "response": {
                    "status_code": step_item["response"]["status_code"],
                    "body_excerpt": json.dumps(step_item["response"]["body"], ensure_ascii=False)[:1000],
                },
            }
            for step_item in item["journey_steps"]
        ]
    return evidence


