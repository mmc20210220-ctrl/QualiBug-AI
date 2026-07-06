"""V12 source-grounded, incrementally sliced behavior pipeline.

The public ``run_v12_pipeline`` entry point is retained. A run selects a
bounded set of source-derived behavior slices and never invents a route, actor,
fixture or cleanup contract. Missing prerequisites remain plan-only coverage
obligations and cannot become confirmed defects.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

_v12_har_entries: list[dict[str, Any]] = []
_SENSITIVE = {"authorization", "token", "password", "secret", "cookie", "api_key", "apikey"}


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
        text = str(content.get("text") if isinstance(content, dict) else content)[:2000]
        entries.append({
            "startedDateTime": item.get("startedDateTime"),
            "time": item.get("time"),
            "request": item.get("request"),
            "response": {"status": status, "body": text},
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
    policy = str(getattr(scenario, "execution_policy", "") or "")
    steps = getattr(scenario, "steps", []) or []
    return bool(steps) and policy in {"safe_read_only", "approved_sandbox_write", "runtime_approved"}


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _behavior_slice_settings() -> dict[str, int]:
    """Resolve replaceable scheduling policy without widening the 15-item cap."""
    try:
        from .policy_wiring import get_policy_value
        configured_budget = get_policy_value("execution", "max_behavior_slices_per_round", 15)
        configured_round = get_policy_value("execution", "incremental_discovery_round", 1)
        configured_limit = get_policy_value("execution", "incremental_discovery_round_limit", 3)
    except Exception:
        configured_budget, configured_round, configured_limit = 15, 1, 3
    budget = _as_int(os.environ.get("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", configured_budget), 15, 1, 15)
    round_number = _as_int(os.environ.get("QUALIBUG_DISCOVERY_ROUND", configured_round), 1, 1, 12)
    round_limit = _as_int(os.environ.get("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", configured_limit), 3, 1, 12)
    return {"slice_budget": budget, "round_number": round_number, "round_limit": round_limit}


def _slice_ledger_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "v12_behavior_slice_ledger.json"


def _load_persisted_slice_history(root: Path, project: str) -> list[dict[str, Any]]:
    """Restore only the small, source-free scheduling ledger for this project."""
    path = _slice_ledger_path(root, project)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    return [{"behavior_slice_ledger": payload}]


def _persist_slice_ledger(root: Path, project: str, ledger: dict[str, Any]) -> None:
    """Persist scheduler state; it contains IDs and outcomes, never raw evidence."""
    path = _slice_ledger_path(root, project)
    safe = {
        "project": str(project),
        "round": int(ledger.get("round") or 0),
        "round_limit": int(ledger.get("round_limit") or 0),
        "slice_budget": int(ledger.get("slice_budget") or 0),
        "selection_mode": str(ledger.get("selection_mode") or ""),
        "selected_slice_ids": [str(value) for value in ledger.get("selected_slice_ids", []) if str(value)],
        "attempted_slice_ids": [str(value) for value in ledger.get("attempted_slice_ids", []) if str(value)],
        "confirmed_slice_ids": [str(value) for value in ledger.get("confirmed_slice_ids", []) if str(value)],
        "next_round": ledger.get("next_round"),
        "stop_reason": str(ledger.get("stop_reason") or ""),
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def _slice_history(existing_findings: list[dict[str, Any]] | None) -> tuple[set[str], set[str]]:
    """Read only explicit slice identities; never infer completion from prose."""
    attempted: set[str] = set()
    confirmed: set[str] = set()
    for item in existing_findings or []:
        if not isinstance(item, dict):
            continue
        ledger = item.get("behavior_slice_ledger")
        if isinstance(ledger, dict):
            attempted.update(str(value) for value in ledger.get("selected_slice_ids", []) if str(value))
            attempted.update(str(value) for value in ledger.get("attempted_slice_ids", []) if str(value))
            confirmed.update(str(value) for value in ledger.get("confirmed_slice_ids", []) if str(value))
        slice_id = str(item.get("behavior_slice_id") or item.get("source_slice_id") or item.get("slice_id") or "").strip()
        if not slice_id:
            continue
        attempted.add(slice_id)
        execution_status = str(item.get("execution_status") or "").lower()
        confirmation_status = str(item.get("confirmation_status") or item.get("verdict") or "").lower()
        gate_passed = bool(item.get("gate_passed"))
        if execution_status == "executed" and confirmation_status in {"confirmed", "validated_candidate"} and gate_passed:
            confirmed.add(slice_id)
    return attempted, confirmed


def _selection_result(
    *,
    status: str,
    stop_reason: str,
    selected: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    attempted: set[str],
    confirmed: set[str],
    next_round: int | None,
    selection_mode: str,
) -> dict[str, Any]:
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


def _schedule_behavior_slices(
    slices: list[dict[str, Any]],
    settings: dict[str, int],
    existing_findings: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Select a bounded source slice batch without rerunning stale plan-only work.

    Explicit rounds page through the initial slice set. When a persisted ledger is
    supplied, previously selected but unconfirmed slices are treated as attempted;
    the scheduler advances to the next unattempted slice. Once all pending slices
    have been attempted, it stops and asks for new evidence, fixtures or policy
    rather than replaying stale plan-only candidates indefinitely.
    """
    attempted, confirmed = _slice_history(existing_findings)
    all_slices = [item for item in slices if isinstance(item, dict) and str(item.get("slice_id") or "")]
    pending = [item for item in all_slices if str(item["slice_id"]) not in confirmed]
    round_number = int(settings["round_number"])
    round_limit = int(settings["round_limit"])
    budget = int(settings["slice_budget"])

    if not all_slices:
        return _selection_result(status="stopped", stop_reason="no_source_bound_behavior_slices", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    if not pending:
        return _selection_result(status="stopped", stop_reason="all_source_bound_slices_confirmed", selected=[], pending=[], attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="none")
    if round_number > round_limit:
        return _selection_result(status="stopped", stop_reason="configured_round_limit_reached", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_limit")

    unattempted = [item for item in pending if str(item["slice_id"]) not in attempted]
    if attempted:
        if not unattempted:
            return _selection_result(
                status="stopped",
                stop_reason="all_pending_slices_attempted_needs_new_evidence_or_policy",
                selected=[],
                pending=pending,
                attempted=attempted,
                confirmed=confirmed,
                next_round=None,
                selection_mode="history_exhausted",
            )
        selected = unattempted[:budget]
        remaining = max(0, len(unattempted) - len(selected))
        next_round = round_number + 1 if remaining > 0 and round_number < round_limit else None
        return _selection_result(
            status="planned",
            stop_reason="slice_budget_reached" if remaining > 0 else "selected_final_unattempted_slice_batch",
            selected=selected,
            pending=unattempted,
            attempted=attempted,
            confirmed=confirmed,
            next_round=next_round,
            selection_mode="next_unattempted_after_history",
        )

    offset = (round_number - 1) * budget
    selected = pending[offset:offset + budget]
    if not selected:
        return _selection_result(status="stopped", stop_reason="no_remaining_slice_in_configured_round", selected=[], pending=pending, attempted=attempted, confirmed=confirmed, next_round=None, selection_mode="round_paging")
    remaining = max(0, len(pending) - offset - len(selected))
    next_round = round_number + 1 if remaining > 0 and round_number < round_limit else None
    return _selection_result(
        status="planned",
        stop_reason="slice_budget_reached" if remaining > 0 else "selected_final_available_slice_batch",
        selected=selected,
        pending=pending[offset:],
        attempted=attempted,
        confirmed=confirmed,
        next_round=next_round,
        selection_mode="round_paging",
    )


def run_v12_pipeline(
    project: str,
    root: Path,
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
    base_url: str = "",
    existing_findings: list[dict] | None = None,
) -> dict[str, Any]:
    global _v12_har_entries
    _v12_har_entries = []
    started = time.time()
    result: dict[str, Any] = {
        "v12_version": "2.1",
        "enabled": True,
        "phases": {},
        "findings": [],
        "evidence_graphs": [],
        "risk_clues_saved": 0,
        "behavior_slice_ledger": {},
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
        history_items = existing_findings if existing_findings is not None else _load_persisted_slice_history(root, project)
        history_source = "explicit_findings" if existing_findings is not None else "persisted_ledger"
        selection = _schedule_behavior_slices(behavior_contract["slices"], settings, history_items)
        selected_ids = set(selection["selected_slice_ids"])
        result["behavior_slice_ledger"] = {
            "project": project,
            "history_source": history_source,
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
        }

        selected_paths = {
            str(path)
            for item in selection["selected"]
            for path in item.get("endpoints", [])
            if str(path)
        }
        fuzz_started = time.time()
        fuzzer_findings: list[dict[str, Any]] = []
        if base_url and selected_paths:
            try:
                from .parameter_fuzzer import ParameterFuzzer
                from .route_catalog_builder import RouteCatalogBuilder

                catalog = [entry.to_dict() for entry in RouteCatalogBuilder().build(api_spec_text)]
                scoped_catalog = [entry for entry in catalog if str(entry.get("path") or "") in selected_paths]
                fuzzer_findings = ParameterFuzzer(base_url, allow_write=False).fuzz_all(scoped_catalog, max_variants=6)
                for finding in fuzzer_findings:
                    if not isinstance(finding, dict):
                        continue
                    path = str(finding.get("path") or "")
                    matching = next((item for item in selection["selected"] if path and path in item.get("endpoints", [])), None)
                    if matching:
                        finding["behavior_slice_id"] = matching["slice_id"]
                        finding["discovery_round"] = settings["round_number"]
            except Exception:
                pass
        result["findings"].extend(fuzzer_findings)
        result["phases"]["parameter_fuzzer"] = {
            "status": "completed" if base_url and selected_paths else "skipped",
            "reason": "selected_source_bound_read_routes_only" if selected_paths else "no_selected_source_bound_read_routes",
            "findings": len(fuzzer_findings),
            "execution_policy": "documented_read_only_only",
            "slice_scoped": True,
            "duration_ms": int((time.time() - fuzz_started) * 1000),
        }

        scenario_started = time.time()
        from .semantic_scenario_generator import SemanticScenarioGenerator

        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            api_spec_text,
            active_slice_ids=selected_ids,
            discovery_round=settings["round_number"],
        )
        executable = [scenario for scenario in scenarios if _scenario_executable(scenario)]
        plan_only = [scenario for scenario in scenarios if scenario not in executable]
        result["phases"]["scenario_generation"] = {
            "status": "completed" if selection["status"] == "planned" else "stopped",
            "total_scenarios": len(scenarios),
            "executable_scenarios": len(executable),
            "plan_only_scenarios": len(plan_only),
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
        elif not base_url:
            result["phases"]["execution"] = {"status": "skipped", "reason": "no_base_url", "planned_only": len(plan_only), "executed": 0}
        elif not executable:
            result["phases"]["execution"] = {"status": "plan_only", "reason": "fixture_actor_cleanup_contract_required", "planned_only": len(plan_only), "executed": 0}
        else:
            execution_started = time.time()
            failed = 0
            for scenario in executable:
                try:
                    trace = _execute_scenario(scenario, base_url, max_retries=2)
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
                                "evidence": {"calls": [{"call": f"{step.get('method', '')} {step.get('path', '')}", "results": {"execution": {"status": step.get("status"), "body": step.get("response", {}).get("body", {})}}}]},
                            })
                except Exception:
                    failed += 1
            result["phases"]["execution"] = {
                "status": "completed",
                "executed": len(traces),
                "failed": failed,
                "planned_only": len(plan_only),
                "duration_ms": int((time.time() - execution_started) * 1000),
            }

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
                })
        result["phases"]["oracle"] = {
            "status": "completed",
            "total_evaluated": len(traces),
            "violations_found": len(result["findings"]),
            "duration_ms": int((time.time() - oracle_started) * 1000),
        }
        try:
            from .risk_clue_pool import save_risk_clues
            result["risk_clues_saved"] = save_risk_clues(project, root, result["findings"]).get("new_this_scan", 0)
        except Exception:
            pass
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
        path = _replace(path, bindings)
        body = _replace(getattr(step, "body_template", {}) or {}, bindings)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body and method not in {"GET", "HEAD"} else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        token = str(getattr(scenario, "actor_token", "") or "")
        actor = str(getattr(step, "actor", "") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started = time.time()
        url = base_url.rstrip("/") + path
        try:
            request = urllib.request.Request(url, method=method, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(300_000).decode("utf-8", errors="replace")
                status = int(response.status)
                response_body = _json_or_text(raw)
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
            status = int(exc.code)
            response_body = _json_or_text(raw)
            response_headers = dict(exc.headers.items()) if exc.headers else {}
        except Exception as exc:
            status = 0
            response_body = {"error": str(exc)}
            response_headers = {}
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
    text = str(value) if isinstance(value, str) else value
    if not isinstance(text, str):
        return text
    for key, item in bindings.items():
        text = text.replace("{" + key + "}", str(item))
    return text


def _extract(value: Any, field: str) -> Any:
    if not field:
        return None
    current = value
    if "." in field:
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    if isinstance(value, dict):
        if field in value:
            return value[field]
        for item in value.values():
            found = _extract(item, field)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract(item, field)
            if found is not None:
                return found
    return None


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw[:5000]


def _count_by(items: list[Any], attr: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items:
        result[str(getattr(item, attr, "unknown"))] += 1
    return dict(result)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("<REDACTED>" if str(key).lower().replace("-", "_") in _SENSITIVE else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:25]]
    text = str(value)
    return text[:1000] + "…" if len(text) > 1000 else value
