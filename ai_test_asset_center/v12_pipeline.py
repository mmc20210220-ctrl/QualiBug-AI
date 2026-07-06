"""V12 source-grounded behavior pipeline.

The existing pipeline keeps its public ``run_v12_pipeline`` entry point, but it
only executes scenarios that already carry an explicit runtime execution
contract. Source-derived plans without fixture, actor and cleanup contracts are
reported as plan-only coverage obligations, never as executed tests.
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
        entries.append({"startedDateTime": item.get("startedDateTime"), "time": item.get("time"), "request": item.get("request"), "response": {"status": status, "body": text}, "_actor": item.get("_actor", "")})
    return {"status": "captured", "total_calls": len(entries), "error_responses": sum(count for status, count in counts.items() if status >= 400), "status_distribution": counts, "entries": entries}


def is_v12_enabled() -> bool:
    return os.environ.get("ENABLE_V12_STATE_GRAPH_ENGINE", "false").lower() in {"1", "true", "yes", "on"}


def _scenario_executable(scenario: Any) -> bool:
    policy = str(getattr(scenario, "execution_policy", "") or "")
    steps = getattr(scenario, "steps", []) or []
    return bool(steps) and policy in {"safe_read_only", "approved_sandbox_write", "runtime_approved"}


def run_v12_pipeline(project: str, root: Path, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "", base_url: str = "", existing_findings: list[dict] | None = None) -> dict[str, Any]:
    global _v12_har_entries
    _v12_har_entries = []
    started = time.time()
    result: dict[str, Any] = {"v12_version": "2.0", "enabled": True, "phases": {}, "findings": [], "evidence_graphs": [], "risk_clues_saved": 0}
    if api_spec_text and not isinstance(api_spec_text, dict):
        try:
            from .universal_api_parser import detect_format, parse_to_openapi
            if detect_format(api_spec_text) not in {"openapi3", "unknown"}:
                normalized = parse_to_openapi(api_spec_text)
                if normalized.get("paths"): api_spec_text = json.dumps(normalized, ensure_ascii=False, default=str)
        except Exception:
            pass
    try:
        graph_started = time.time()
        from .business_state_graph import BusinessStateGraphBuilder
        graphs = BusinessStateGraphBuilder().build(prd_text, api_spec_text, db_schema_text)
        result["phases"]["state_graph"] = {"status": "completed", "entities": sorted(graphs), "summary": {name: graph.to_dict()["stats"] for name, graph in graphs.items()}, "duration_ms": int((time.time() - graph_started) * 1000)}

        fuzz_started = time.time(); fuzzer_findings: list[dict[str, Any]] = []
        if base_url:
            try:
                from .parameter_fuzzer import ParameterFuzzer
                from .route_catalog_builder import RouteCatalogBuilder
                catalog = [entry.to_dict() for entry in RouteCatalogBuilder().build(api_spec_text)]
                fuzzer_findings = ParameterFuzzer(base_url, allow_write=False).fuzz_all(catalog, max_variants=6)
            except Exception:
                pass
        result["findings"].extend(fuzzer_findings)
        result["phases"]["parameter_fuzzer"] = {"status": "completed" if base_url else "skipped", "findings": len(fuzzer_findings), "execution_policy": "documented_read_only_only", "duration_ms": int((time.time() - fuzz_started) * 1000)}

        scenario_started = time.time()
        from .semantic_scenario_generator import SemanticScenarioGenerator
        scenarios = SemanticScenarioGenerator().generate(graphs, api_spec_text)
        executable = [scenario for scenario in scenarios if _scenario_executable(scenario)]
        plan_only = [scenario for scenario in scenarios if scenario not in executable]
        result["phases"]["scenario_generation"] = {"status": "completed", "total_scenarios": len(scenarios), "executable_scenarios": len(executable), "plan_only_scenarios": len(plan_only), "by_category": _count_by(scenarios, "category"), "by_severity": _count_by(scenarios, "severity"), "forbidden_paths": sum(1 for scenario in scenarios if getattr(scenario, "is_forbidden_path", False)), "duration_ms": int((time.time() - scenario_started) * 1000)}
        result["plan_only_scenarios"] = [scenario.to_dict() for scenario in plan_only]

        traces: list[tuple[Any, dict[str, Any]]] = []
        if not base_url:
            result["phases"]["execution"] = {"status": "skipped", "reason": "no_base_url", "planned_only": len(plan_only)}
        elif not executable:
            result["phases"]["execution"] = {"status": "plan_only", "reason": "fixture_actor_cleanup_contract_required", "planned_only": len(plan_only), "executed": 0}
        else:
            execution_started = time.time(); failed = 0
            for scenario in executable:
                try:
                    trace = _execute_scenario(scenario, base_url, max_retries=2)
                    traces.append((scenario, trace))
                    for step in trace.get("steps", []):
                        if int(step.get("status") or 0) >= 500:
                            result["findings"].append({"severity": "P0", "title": f"[场景执行错误] {str(getattr(scenario, 'title', 'scenario'))[:80]}", "category": getattr(scenario, "category", "scenario_flow"), "source": "v12_scenario_executor", "description": f"服务端错误 HTTP{step.get('status')}: {step.get('path', '')}", "confidence_score": 0.80, "evidence": {"calls": [{"call": f"{step.get('method', '')} {step.get('path', '')}", "results": {"execution": {"status": step.get("status"), "body": step.get("response", {}).get("body", {})}}}]}})
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
                result["findings"].append({"severity": oracle_result.severity, "title": f"[V12 {oracle_result.oracle_name}] {scenario.title}", "category": scenario.category, "source": "v12_state_graph", "description": oracle_result.explanation, "confidence_score": oracle_result.confidence, "evidence_id": evidence.evidence_id, "oracle": oracle_result.to_dict()})
        result["phases"]["oracle"] = {"status": "completed", "total_evaluated": len(traces), "violations_found": len(result["findings"]), "duration_ms": int((time.time() - oracle_started) * 1000)}
        try:
            from .risk_clue_pool import save_risk_clues
            result["risk_clues_saved"] = save_risk_clues(project, root, result["findings"]).get("new_this_scan", 0)
        except Exception:
            pass
    except Exception as exc:
        result["error"] = str(exc)[:500]
    result["total_duration_ms"] = int((time.time() - started) * 1000)
    result["auto_har"] = _v12_har_report()
    return result


def _execute_scenario(scenario: Any, base_url: str, max_retries: int = 2) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            return __execute_scenario_once(scenario, base_url)
        except Exception as exc:
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1)); continue
            return {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": [f"failed_after_retries:{exc}"], "duration_ms": 0}
    return {"scenario_id": "?", "steps": [], "errors": ["unreachable"], "duration_ms": 0}


def __execute_scenario_once(scenario: Any, base_url: str) -> dict[str, Any]:
    trace: dict[str, Any] = {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": []}; bindings: dict[str, Any] = {}
    for step in getattr(scenario, "steps", []) or []:
        method, path = str(getattr(step, "api_method", "") or "").upper(), str(getattr(step, "api_path", "") or "")
        if not method or not path.startswith("/"):
            trace["errors"].append("invalid_source_bound_step"); continue
        path = _replace(path, bindings); body = _replace(getattr(step, "body_template", {}) or {}, bindings)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body and method not in {"GET", "HEAD"} else None
        headers = {"Accept": "application/json"}
        if data is not None: headers["Content-Type"] = "application/json"
        token = str(getattr(scenario, "actor_token", "") or "")
        actor = str(getattr(step, "actor", "") or "")
        if token: headers["Authorization"] = f"Bearer {token}"
        started = time.time(); url = base_url.rstrip("/") + path
        try:
            request = urllib.request.Request(url, method=method, data=data, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(300_000).decode("utf-8", errors="replace"); status = int(response.status); response_body = _json_or_text(raw); response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""; status = int(exc.code); response_body = _json_or_text(raw); response_headers = dict(exc.headers.items()) if exc.headers else {}
        except Exception as exc:
            status = 0; response_body = {"error": str(exc)}; response_headers = {}; trace["errors"].append(str(exc))
        _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
        for field in getattr(step, "extract_from_response", []) or []:
            value = _extract(response_body, str(field))
            if value not in (None, "", [], {}): bindings[str(field)] = value
        trace["steps"].append({"action": getattr(step, "action", ""), "method": method, "path": path, "status": status, "response": {"status_code": status, "headers": _redact(response_headers), "body": _redact(response_body)}, "expected_status": getattr(step, "expected_status", 0)})
    return trace


def _replace(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict): return {key: _replace(item, bindings) for key, item in value.items()}
    if isinstance(value, list): return [_replace(item, bindings) for item in value]
    text = str(value) if isinstance(value, str) else value
    if not isinstance(text, str): return text
    for key, item in bindings.items(): text = text.replace("{" + key + "}", str(item))
    return text

def _extract(value: Any, field: str) -> Any:
    if not field: return None
    current = value
    if "." in field:
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current: current = None; break
            current = current[part]
        if current is not None: return current
    if isinstance(value, dict):
        if field in value: return value[field]
        for item in value.values():
            found = _extract(item, field)
            if found is not None: return found
    elif isinstance(value, list):
        for item in value:
            found = _extract(item, field)
            if found is not None: return found
    return None

def _json_or_text(raw: str) -> Any:
    try: return json.loads(raw)
    except Exception: return raw[:5000]
def _count_by(items: list[Any], attr: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items: result[str(getattr(item, attr, "unknown"))] += 1
    return dict(result)
def _redact(value: Any) -> Any:
    if isinstance(value, dict): return {str(key): ("<REDACTED>" if str(key).lower().replace("-", "_") in _SENSITIVE else _redact(item)) for key, item in value.items()}
    if isinstance(value, list): return [_redact(item) for item in value[:25]]
    text = str(value)
    return text[:1000] + "…" if len(text) > 1000 else value
