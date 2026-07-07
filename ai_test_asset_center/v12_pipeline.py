"""V12 source-grounded behavior pipeline with enterprise Campaign governance.

A Campaign is an auditable project scope, environment reference and source
snapshot. Planning can proceed without a target; any runtime traffic additionally
requires a valid source contract and a time-bounded execution approval issued for
the resolved Campaign.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .enterprise_campaign import (
    EnterpriseCampaign,
    EnterpriseCampaignStore,
    has_real_confirmation_receipt,
    source_snapshot_hash,
)

_v12_har_entries: list[dict[str, Any]] = []
_SENSITIVE = {"authorization", "token", "password", "secret", "cookie", "api_key", "apikey"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _record_v12_har(method: str, url: str, status: int, body: Any, actor: str = "", elapsed_ms: float = 0.0) -> None:
    _v12_har_entries.append({
        "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "time": elapsed_ms,
        "request": {"method": method, "url": url},
        "response": {"status": status, "content": {"mimeType": "application/json", "text": str(body)[:5000]}},
        "_actor": actor,
    })


def _v12_har_report() -> dict[str, Any]:
    if not _v12_har_entries:
        return {"status": "no_traffic"}
    counts: dict[int, int] = {}
    entries: list[dict[str, Any]] = []
    for item in _v12_har_entries:
        status = int(item.get("response", {}).get("status") or 0)
        counts[status] = counts.get(status, 0) + 1
        content = item.get("response", {}).get("content", {})
        entries.append({
            "startedDateTime": item.get("startedDateTime"),
            "time": item.get("time"),
            "request": item.get("request"),
            "response": {"status": status, "body": str(content.get("text") if isinstance(content, dict) else content)[:2000]},
            "_actor": item.get("_actor", ""),
        })
    return {
        "status": "captured",
        "total_calls": len(entries),
        "error_responses": sum(count for status, count in counts.items() if status >= 400),
        "status_distribution": counts,
        "entries": entries,
    }


def is_v12_enabled() -> bool:
    return os.environ.get("ENABLE_V12_STATE_GRAPH_ENGINE", "false").lower() in {"1", "true", "yes", "on"}


def _scenario_executable(scenario: Any) -> bool:
    return bool(getattr(scenario, "steps", []) or []) and str(getattr(scenario, "execution_policy", "") or "") in {
        "safe_read_only",
        "approved_sandbox_write",
        "runtime_approved",
    }


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _behavior_slice_settings() -> dict[str, int]:
    try:
        from .policy_wiring import get_policy_value
        budget = get_policy_value("execution", "max_behavior_slices_per_round", 15)
        round_number = get_policy_value("execution", "incremental_discovery_round", 1)
        round_limit = get_policy_value("execution", "incremental_discovery_round_limit", 3)
    except Exception:
        budget, round_number, round_limit = 15, 1, 3
    return {
        "slice_budget": _as_int(os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", budget), 15, 1, 15),
        "round_number": _as_int(os.environ.get("QUALIBUG_DISCOVERY_ROUND", round_number), 1, 1, 12),
        "round_limit": _as_int(os.environ.get("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", round_limit), 3, 1, 12),
    }


def _source_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _source_manifest_details(context: dict[str, Any], source_text: Any) -> tuple[dict[str, str], list[str]]:
    manifest = _dict(context.get("source_manifest"))
    source_id = str(manifest.get("source_id") or "").strip()
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    source_origin = str(manifest.get("source_origin") or "declared_manifest").strip()
    actual_hash = hashlib.sha256(_source_text(source_text).encode("utf-8")).hexdigest()
    issues: list[str] = []
    if not source_id or not source_hash:
        issues.append("SOURCE_PROVENANCE_MISSING")
    elif not _SHA256_RE.fullmatch(source_hash):
        issues.append("SOURCE_HASH_INVALID")
    elif source_hash != actual_hash:
        issues.append("SOURCE_HASH_MISMATCH")
    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "source_origin": source_origin[:80],
        "source_version_id": str(manifest.get("source_version_id") or "")[:80],
    }, issues


def _runtime_contract(context: dict[str, Any], base_url: str, source_text: Any) -> dict[str, Any]:
    """Direct callers cannot bypass source, scope, environment, or hash approval."""
    manifest, source_issues = _source_manifest_details(context, source_text)
    if not base_url:
        return {
            "status": "plan_only",
            "reason": "runtime_target_missing",
            "approved_base_url": "",
            "source_manifest": manifest,
            "source_issues": source_issues,
        }
    missing = list(source_issues)
    if not str(context.get("scope_id") or "").strip():
        missing.append("CAMPAIGN_SCOPE_MISSING")
    if not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        missing.append("ENVIRONMENT_REFERENCE_MISSING")
    if missing:
        return {
            "status": "blocked",
            "reason": "runtime_contract_missing",
            "missing_requirements": sorted(set(missing)),
            "approved_base_url": "",
            "source_manifest": manifest,
        }
    return {
        "status": "approved",
        "reason": "",
        "missing_requirements": [],
        "approved_base_url": str(base_url).rstrip("/"),
        "source_manifest": manifest,
    }


def _execution_approval_contract(context: dict[str, Any], campaign: EnterpriseCampaign, base_url: str, root: Path) -> dict[str, Any]:
    """Verify an approval only after the canonical Campaign identity is known."""
    if not base_url:
        return {"status": "not_required", "reason": "runtime_target_missing"}
    approval_id = str(context.get("execution_approval_id") or "").strip()
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip()
    if not approval_id:
        return {"status": "blocked", "code": "EXECUTION_APPROVAL_MISSING", "execution_mode": execution_mode}
    try:
        from .execution_approvals import verify_execution_approval
        verdict = verify_execution_approval(
            campaign.project_id,
            approval_id,
            root=root,
            campaign_id=campaign.campaign_id,
            scope_id=campaign.scope_id,
            environment_ref=campaign.environment_ref,
            source_hash=campaign.source_hash,
            target_base_url=base_url,
            execution_mode=execution_mode,
        )
    except Exception as exc:
        return {"status": "blocked", "code": f"EXECUTION_APPROVAL_VERIFICATION_ERROR:{type(exc).__name__}", "execution_mode": execution_mode}
    if verdict.get("valid") is not True:
        return {"status": "blocked", "code": str(verdict.get("code") or "EXECUTION_APPROVAL_INVALID"), "execution_mode": execution_mode}
    approval = _dict(verdict.get("approval"))
    return {
        "status": "approved",
        "approval_id": approval_id,
        "approval_hash": str(approval.get("approval_hash") or ""),
        "execution_mode": execution_mode,
        "expires_at_utc": str(approval.get("expires_at_utc") or ""),
    }


def _slice_ledger_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "v12_behavior_slice_ledger.json"


def _load_persisted_slice_history(root: Path, project: str) -> list[dict[str, Any]]:
    path = _slice_ledger_path(root, project)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    return [{"behavior_slice_ledger": payload}] if isinstance(payload, dict) else []


def _persist_slice_ledger(root: Path, project: str, ledger: dict[str, Any]) -> None:
    path = _slice_ledger_path(root, project)
    safe = {
        "campaign_id": str(ledger.get("campaign_id") or ""),
        "campaign_status": str(ledger.get("campaign_status") or ""),
        "scope_id": str(ledger.get("scope_id") or ""),
        "source_snapshot_hash": str(ledger.get("source_snapshot_hash") or ""),
        "project": str(project),
        "round": int(ledger.get("round") or 0),
        "round_limit": int(ledger.get("round_limit") or 0),
        "slice_budget": int(ledger.get("slice_budget") or 0),
        "selection_mode": str(ledger.get("selection_mode") or ""),
        "selected_slice_ids": [str(value) for value in ledger.get("selected_slice_ids", []) if str(value)],
        "attempted_slice_ids": [str(value) for value in ledger.get("attempted_slice_ids", []) if str(value)],
        "confirmed_slice_ids": [],
        "next_round": ledger.get("next_round"),
        "stop_reason": str(ledger.get("stop_reason") or ""),
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _slice_history(history: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    attempted: set[str] = set()
    confirmed: set[str] = set()
    for item in history or []:
        if not isinstance(item, dict):
            continue
        ledger = item.get("behavior_slice_ledger")
        if isinstance(ledger, dict):
            attempted.update(str(value) for value in ledger.get("selected_slice_ids", []) if str(value))
            attempted.update(str(value) for value in ledger.get("attempted_slice_ids", []) if str(value))
        slice_id = str(item.get("behavior_slice_id") or item.get("source_slice_id") or item.get("slice_id") or "").strip()
        if slice_id:
            attempted.add(slice_id)
        if slice_id and has_real_confirmation_receipt(item):
            confirmed.add(slice_id)
    return attempted, confirmed


def _selection_result(*, status: str, stop_reason: str, selected: list[dict[str, Any]], pending: list[dict[str, Any]], attempted: set[str], confirmed: set[str], next_round: int | None, selection_mode: str) -> dict[str, Any]:
    return {
        "status": status,
        "stop_reason": stop_reason,
        "selected": selected,
        "selected_slice_ids": [str(item.get("slice_id") or "") for item in selected],
        "next_round": next_round,
        "remaining_slice_count": max(0, len(pending) - len(selected)),
        "attempted_slice_ids": sorted(attempted),
        "confirmed_slice_ids": sorted(confirmed),
        "selection_mode": selection_mode,
    }


def _schedule_behavior_slices(slices: list[dict[str, Any]], settings: dict[str, int], history: list[dict[str, Any]] | None) -> dict[str, Any]:
    attempted, confirmed = _slice_history(history)
    all_slices = [item for item in slices if isinstance(item, dict) and str(item.get("slice_id") or "")]
    pending = [item for item in all_slices if str(item["slice_id"]) not in confirmed]
    round_number, round_limit, budget = int(settings["round_number"]), int(settings["round_limit"]), int(settings["slice_budget"])
    if not all_slices:
        return _selection_result(status="stopped", stop_reason="no_source_bound_behavior_slices", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    if not pending:
        return _selection_result(status="stopped", stop_reason="all_source_bound_slices_confirmed", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    if round_number > round_limit:
        return _selection_result(status="stopped", stop_reason="configured_round_limit_reached", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_limit")
    unattempted = [item for item in pending if str(item["slice_id"]) not in attempted]
    if attempted:
        if not unattempted:
            return _selection_result(status="stopped", stop_reason="all_pending_slices_attempted_needs_new_evidence_or_policy", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="history_exhausted")
        selected = unattempted[:budget]
        remaining = len(unattempted) - len(selected)
        return _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_final_unattempted_slice_batch", selected=selected, pending=unattempted, attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="next_unattempted_after_history")
    offset = (round_number - 1) * budget
    selected = pending[offset:offset + budget]
    if not selected:
        return _selection_result(status="stopped", stop_reason="no_remaining_slice_in_configured_round", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_paging")
    remaining = len(pending) - offset - len(selected)
    return _selection_result(status="planned", stop_reason="slice_budget_reached" if remaining else "selected_final_available_slice_batch", selected=selected, pending=pending[offset:], attempted=attempted, confirmed=confirmed, next_round=round_number + 1 if remaining and round_number < round_limit else None, selection_mode="round_paging")


def _active_policy_version() -> str:
    try:
        from .policy_registry import get_policy_registry
        return str(getattr(get_policy_registry().get_active(), "policy_version", "") or "")
    except Exception:
        return ""


def _campaign_context(project: str, prd_text: str, api_spec_text: str, db_schema_text: str, base_url: str, settings: dict[str, int], context: dict[str, Any], root: Path, submitted_api_spec_text: Any) -> tuple[EnterpriseCampaign, EnterpriseCampaignStore, str]:
    policy_version = str(context.get("policy_version") or _active_policy_version())[:120]
    scope_id = str(context.get("scope_id") or f"project_scope_{hashlib.sha256(project.encode()).hexdigest()[:12]}")[:160]
    environment_ref = str(context.get("environment_ref") or context.get("target_environment") or (f"target_{hashlib.sha256(base_url.encode()).hexdigest()[:16]}" if base_url else "unbound_environment"))[:160]
    snapshot = source_snapshot_hash(prd_text, api_spec_text, db_schema_text, scope_id, environment_ref)
    source_manifest, source_issues = _source_manifest_details(context, submitted_api_spec_text)
    candidate = EnterpriseCampaign.create(
        project,
        scope_id,
        environment_ref,
        snapshot,
        source_id=source_manifest["source_id"] if not source_issues else "",
        source_hash=source_manifest["source_hash"] if not source_issues else "",
        policy_version=policy_version,
        slice_budget=settings["slice_budget"],
        automatic_round_limit=settings["round_limit"],
    )
    store = EnterpriseCampaignStore(root, project)
    campaign, mode = store.open_or_create(candidate)
    campaign.slice_budget = min(campaign.slice_budget, settings["slice_budget"], 15)
    campaign.automatic_round_limit = min(campaign.automatic_round_limit, settings["round_limit"], 12)
    return campaign, store, mode


def _count_by(items: list[Any], field: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[str(getattr(item, field, "") or "unknown")] += 1
    return dict(counts)


def run_v12_pipeline(project: str, root: Path, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "", base_url: str = "", existing_findings: list[dict] | None = None, campaign_context: dict[str, Any] | None = None) -> dict[str, Any]:
    global _v12_har_entries
    _v12_har_entries = []
    started = time.time()
    context = _dict(campaign_context)
    submitted_api_spec_text = api_spec_text
    runtime_contract = _runtime_contract(context, base_url, submitted_api_spec_text)
    approved_base_url = str(runtime_contract.get("approved_base_url") or "")
    result: dict[str, Any] = {
        "v12_version": "2.5",
        "enabled": True,
        "phases": {},
        "findings": [],
        "evidence_graphs": [],
        "risk_clues_saved": 0,
        "behavior_slice_ledger": {},
        "campaign": {},
        "runtime_contract": runtime_contract,
    }
    ledger_for_persistence: dict[str, Any] | None = None
    if api_spec_text and not isinstance(api_spec_text, dict):
        try:
            from .universal_api_parser import detect_format, parse_to_openapi
            if detect_format(api_spec_text) not in {"openapi3", "unknown"}:
                normalized = parse_to_openapi(api_spec_text)
                if normalized.get("paths"):
                    api_spec_text = json.dumps(normalized, ensure_ascii=False, default=str)
        except Exception:
            pass
    try:
        graph_started = time.time()
        from .business_state_graph import BusinessStateGraphBuilder
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(prd_text, api_spec_text, db_schema_text)
        behavior_contract = builder.behavior_contract()
        settings = _behavior_slice_settings()
        campaign, campaign_store, campaign_mode = _campaign_context(project, prd_text, api_spec_text, db_schema_text, approved_base_url, settings, context, root, submitted_api_spec_text)
        approval = _execution_approval_contract(context, campaign, approved_base_url, root)
        if approved_base_url and approval.get("status") != "approved":
            runtime_contract = {
                **runtime_contract,
                "status": "blocked",
                "reason": "execution_approval_required",
                "missing_requirements": [str(approval.get("code") or "EXECUTION_APPROVAL_MISSING")],
                "approved_base_url": "",
                "execution_approval": approval,
            }
            approved_base_url = ""
        else:
            runtime_contract = {**runtime_contract, "execution_approval": approval}
        result["runtime_contract"] = runtime_contract
        if campaign.status in {"coverage_deferred", "completed", "blocked"}:
            selection = _selection_result(status="stopped", stop_reason=f"campaign_{campaign.status}", selected=[], pending=behavior_contract["slices"], attempted=set(campaign.attempted_slice_ids), confirmed=set(campaign.confirmation_receipts), next_round=None, selection_mode="campaign_terminal")
        else:
            history: list[dict[str, Any]] = [campaign.history_item()]
            if existing_findings is not None:
                history.extend(item for item in existing_findings if isinstance(item, dict))
            elif not campaign.attempted_slice_ids:
                history.extend(_load_persisted_slice_history(root, project))
            if "QUALIBUG_DISCOVERY_ROUND" not in os.environ and campaign.round_count:
                settings["round_number"] = min(campaign.round_count + 1, campaign.automatic_round_limit + 1)
            settings["slice_budget"] = min(settings["slice_budget"], campaign.slice_budget)
            settings["round_limit"] = min(settings["round_limit"], campaign.automatic_round_limit)
            selection = _schedule_behavior_slices(behavior_contract["slices"], settings, history)
        selected_ids = set(selection["selected_slice_ids"])
        result["campaign"] = {**campaign.public_contract(), "campaign_mode": campaign_mode}
        result["behavior_slice_ledger"] = {
            "campaign_id": campaign.campaign_id,
            "campaign_status": campaign.status,
            "scope_id": campaign.scope_id,
            "source_snapshot_hash": campaign.source_snapshot_hash,
            "project": project,
            "round": settings["round_number"],
            "round_limit": settings["round_limit"],
            "slice_budget": settings["slice_budget"],
            "selection_mode": selection["selection_mode"],
            "selected_slice_ids": selection["selected_slice_ids"],
            "attempted_slice_ids": selection["attempted_slice_ids"],
            "confirmed_slice_ids": selection["confirmed_slice_ids"],
            "next_round": selection["next_round"],
            "stop_reason": selection["stop_reason"],
        }
        ledger_for_persistence = dict(result["behavior_slice_ledger"])
        result["phases"]["state_graph"] = {
            "status": "completed",
            "entities": sorted(graphs),
            "summary": {name: graph.to_dict()["stats"] for name, graph in graphs.items()},
            "behavior_slices": behavior_contract["summary"],
            "coverage_gaps": behavior_contract["coverage_gaps"],
            "duration_ms": int((time.time() - graph_started) * 1000),
        }
        result["phases"]["incremental_discovery"] = {
            "status": selection["status"],
            "round": settings["round_number"],
            "round_limit": settings["round_limit"],
            "slice_budget": settings["slice_budget"],
            "selection_mode": selection["selection_mode"],
            "selected_slices": selection["selected"],
            "remaining_slice_count": selection["remaining_slice_count"],
            "next_round": selection["next_round"],
            "stop_reason": selection["stop_reason"],
            "campaign_id": campaign.campaign_id,
            "campaign_status": campaign.status,
        }
        selected_paths = {str(path) for item in selection["selected"] for path in item.get("endpoints", []) if str(path)}
        fuzz_started = time.time()
        fuzzer_findings: list[dict[str, Any]] = []
        if approved_base_url and selected_paths and selection["status"] == "planned":
            try:
                from .parameter_fuzzer import ParameterFuzzer
                from .route_catalog_builder import RouteCatalogBuilder
                catalog = [entry.to_dict() for entry in RouteCatalogBuilder().build(api_spec_text)]
                scoped_catalog = [entry for entry in catalog if str(entry.get("path") or "") in selected_paths]
                fuzzer_findings = ParameterFuzzer(approved_base_url, allow_write=False).fuzz_all(scoped_catalog, max_variants=6)
                for finding in fuzzer_findings:
                    if isinstance(finding, dict):
                        matching = next((item for item in selection["selected"] if str(finding.get("path") or "") in item.get("endpoints", [])), None)
                        if matching:
                            finding.update({"behavior_slice_id": matching["slice_id"], "discovery_round": settings["round_number"], "campaign_id": campaign.campaign_id, "execution_status": "executed", "confirmation_status": "candidate"})
            except Exception:
                pass
        result["findings"].extend(fuzzer_findings)
        fuzzer_reason = "selected_source_bound_read_routes_only" if selected_paths and approved_base_url else (runtime_contract.get("reason") or "no_selected_source_bound_read_routes")
        result["phases"]["parameter_fuzzer"] = {
            "status": "completed" if approved_base_url and selected_paths and selection["status"] == "planned" else "skipped",
            "reason": fuzzer_reason,
            "findings": len(fuzzer_findings),
            "execution_policy": "documented_read_only_only",
            "slice_scoped": True,
            "duration_ms": int((time.time() - fuzz_started) * 1000),
        }
        scenario_started = time.time()
        from .semantic_scenario_generator import SemanticScenarioGenerator
        runtime_scenario_contract = _dict(context.get("runtime_scenario_contract"))
        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            api_spec_text,
            active_slice_ids=selected_ids,
            discovery_round=settings["round_number"],
            runtime_scenario_contract=runtime_scenario_contract,
        )
        executable = [scenario for scenario in scenarios if _scenario_executable(scenario)]
        plan_only = [scenario for scenario in scenarios if scenario not in executable]
        result["phases"]["scenario_generation"] = {
            "status": "completed" if selection["status"] == "planned" else "stopped",
            "total_scenarios": len(scenarios),
            "executable_scenarios": len(executable),
            "plan_only_scenarios": len(plan_only),
            "runtime_contract_scenarios": sum(1 for scenario in executable if getattr(scenario, "behavior_slice_kind", "") == "runtime_contract"),
            "runtime_scenario_contract_present": bool(runtime_scenario_contract),
            "by_category": _count_by(scenarios, "category"),
            "by_severity": _count_by(scenarios, "severity"),
            "forbidden_paths": sum(1 for scenario in scenarios if getattr(scenario, "is_forbidden_path", False)),
            "selected_slice_ids": selection["selected_slice_ids"],
            "duration_ms": int((time.time() - scenario_started) * 1000),
        }
        result["plan_only_scenarios"] = [scenario.to_dict() for scenario in plan_only]
        traces: list[tuple[Any, dict[str, Any]]] = []
        if selection["status"] != "planned":
            result["phases"]["execution"] = {"status": "stopped", "reason": selection["stop_reason"], "planned_only": 0, "executed": 0}
        elif base_url and not approved_base_url:
            result["phases"]["execution"] = {"status": "blocked", "reason": str(runtime_contract.get("reason") or "runtime_contract_missing"), "missing_requirements": runtime_contract.get("missing_requirements", []), "planned_only": len(plan_only), "executed": 0}
        elif not approved_base_url:
            result["phases"]["execution"] = {"status": "skipped", "reason": "no_base_url", "planned_only": len(plan_only), "executed": 0}
        elif not executable:
            result["phases"]["execution"] = {"status": "plan_only", "reason": "fixture_actor_cleanup_contract_required", "planned_only": len(plan_only), "executed": 0}
        else:
            execution_started, failed = time.time(), 0
            for scenario in executable:
                try:
                    trace = _execute_scenario(scenario, approved_base_url, max_retries=2)
                    traces.append((scenario, trace))
                    for step in trace.get("steps", []):
                        if int(step.get("status") or 0) >= 500:
                            result["findings"].append({
                                "severity": "P0",
                                "title": f"[场景执行错误] {str(getattr(scenario, 'title', 'scenario'))[:80]}",
                                "category": getattr(scenario, "category", "scenario_flow"),
                                "source": "v12_scenario_executor",
                                "description": f"服务端错误 HTTP{step.get('status')}: {step.get('path', '')}",
                                "confidence_score": 0.80,
                                "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
                                "discovery_round": settings["round_number"],
                                "campaign_id": campaign.campaign_id,
                                "execution_status": "executed",
                                "confirmation_status": "candidate",
                                "evidence": {"calls": [{"call": f"{step.get('method', '')} {step.get('path', '')}", "results": {"execution": {"status": step.get("status"), "body": step.get("response", {}).get("body", {})}}}]},
                            })
                except Exception:
                    failed += 1
            result["phases"]["execution"] = {"status": "completed", "executed": len(traces), "failed": failed, "planned_only": len(plan_only), "duration_ms": int((time.time() - execution_started) * 1000)}
        oracle_started = time.time()
        from .oracle_engine import EvidenceGraphBuilder, OracleEngine
        oracle, evidence_builder = OracleEngine(), EvidenceGraphBuilder()
        for scenario, trace in traces:
            for oracle_result in oracle.evaluate(scenario.to_dict(), trace, None):
                if oracle_result.passed:
                    continue
                evidence = evidence_builder.build(scenario.to_dict(), trace, None, [oracle_result])
                result["evidence_graphs"].append(evidence.to_dict())
                result["findings"].append({
                    "severity": oracle_result.severity,
                    "title": f"[V12 {oracle_result.oracle_name}] {scenario.title}",
                    "category": scenario.category,
                    "source": "v12_state_graph",
                    "description": oracle_result.explanation,
                    "confidence_score": oracle_result.confidence,
                    "evidence_id": evidence.evidence_id,
                    "oracle": oracle_result.to_dict(),
                    "behavior_slice_id": getattr(scenario, "behavior_slice_id", ""),
                    "discovery_round": settings["round_number"],
                    "campaign_id": campaign.campaign_id,
                    "execution_status": "executed",
                    "confirmation_status": "candidate",
                })
        result["phases"]["oracle"] = {"status": "completed", "total_evaluated": len(traces), "violations_found": len(result["findings"]), "duration_ms": int((time.time() - oracle_started) * 1000)}
        try:
            from .risk_clue_pool import save_risk_clues
            result["risk_clues_saved"] = save_risk_clues(project, root, result["findings"]).get("new_this_scan", 0)
        except Exception:
            pass
        execution_status = str(result["phases"]["execution"].get("status") or "")
        campaign.record_cycle(round_number=settings["round_number"], selection=selection, findings=result["findings"], coverage_gap_count=len(behavior_contract["coverage_gaps"]), execution_status=execution_status)
        campaign_store.save(campaign)
        result["campaign"] = {**campaign.public_contract(), "campaign_mode": campaign_mode}
        result["behavior_slice_ledger"]["campaign_status"] = campaign.status
        result["phases"]["incremental_discovery"]["campaign_status"] = campaign.status
    except Exception as exc:
        result["error"] = str(exc)[:500]
    if ledger_for_persistence is not None and not result.get("error"):
        try:
            _persist_slice_ledger(root, project, ledger_for_persistence)
        except Exception:
            pass
    result["total_duration_ms"] = int((time.time() - started) * 1000)
    result["auto_har"] = _v12_har_report()
    return result


def _execute_scenario(scenario: Any, base_url: str, max_retries: int = 2) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            return __execute_scenario_once(scenario, base_url)
        except Exception as exc:
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": [f"failed_after_retries:{exc}"], "duration_ms": 0}
    return {"scenario_id": "?", "steps": [], "errors": ["unreachable"], "duration_ms": 0}


def __execute_scenario_once(scenario: Any, base_url: str) -> dict[str, Any]:
    trace: dict[str, Any] = {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": []}
    bindings: dict[str, Any] = {}
    for step in getattr(scenario, "steps", []) or []:
        method = str(getattr(step, "api_method", "") or "").upper()
        path = str(getattr(step, "api_path", "") or "")
        if not method or not path.startswith("/"):
            trace["errors"].append("invalid_source_bound_step")
            continue
        path, body = _replace(path, bindings), _replace(getattr(step, "body_template", {}) or {}, bindings)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body and method not in {"GET", "HEAD"} else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        token, actor = str(getattr(scenario, "actor_token", "") or ""), str(getattr(step, "actor", "") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started, url = time.time(), base_url.rstrip("/") + path
        try:
            request = urllib.request.Request(url, method=method, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(300_000).decode("utf-8", errors="replace")
                status, response_body, response_headers = int(response.status), _json_or_text(raw), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
            status, response_body, response_headers = int(exc.code), _json_or_text(raw), dict(exc.headers.items()) if exc.headers else {}
        except Exception as exc:
            status, response_body, response_headers = 0, {"error": str(exc)}, {}
            trace["errors"].append(str(exc))
        _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
        for field in getattr(step, "extract_from_response", []) or []:
            value = _extract(response_body, str(field))
            if value not in (None, "", [], {}):
                bindings[str(field)] = value
        trace["steps"].append({
            "action": getattr(step, "action", ""),
            "method": method,
            "path": path,
            "status": status,
            "response": {"status_code": status, "headers": _redact(response_headers), "body": _redact(response_body)},
            "expected_status": getattr(step, "expected_status", 0),
        })
    return trace


def _replace(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, bindings) for item in value]
    if isinstance(value, str):
        result = value
        for key, item in bindings.items():
            result = result.replace("{" + key + "}", str(item))
        return result
    return value


def _extract(body: Any, field: str) -> Any:
    current = body
    for part in str(field or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw or "null")
    except Exception:
        return raw[:5000]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("***" if any(token in str(key).lower() for token in _SENSITIVE) else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
