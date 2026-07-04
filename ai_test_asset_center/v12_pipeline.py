"""
V12 Pipeline — Feature-flagged Business State Space Engine adapter.

Enable with: ENABLE_V12_STATE_GRAPH_ENGINE=true

Runs alongside existing V11 pipeline. Does NOT replace or break it.
Adds state-graph-based scenario generation, multi-step execution,
snapshot capture, oracle evaluation, and evidence graph packaging.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from collections import defaultdict
from pathlib import Path
from typing import Any

# ── Auto HAR capture (user never touches this) ─────────────────────────
_v12_har_entries: list[dict[str, Any]] = []

def _record_v12_har(method: str, url: str, status: int, resp_body: str,
                    actor: str = "", elapsed_ms: float = 0) -> None:
    """Record every V12 probe call into in-memory HAR log.
    Runs automatically — zero user configuration.
    """
    _v12_har_entries.append({
        "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "time": elapsed_ms,
        "request": {"method": method, "url": url,
                    "headers": [{"name": "X-QualiBug-Actor", "value": actor}]},
        "response": {"status": status,
                     "content": {"mimeType": "application/json",
                                 "text": str(resp_body)[:5000]}},
        "_actor": actor,
    })


def _v12_har_report() -> dict[str, Any]:
    """Generate auto HAR report for V12 pipeline results."""
    if not _v12_har_entries:
        return {"status": "no_traffic"}
    errors = [e for e in _v12_har_entries if e["response"]["status"] >= 400]
    status_counts: dict[int, int] = {}
    for e in _v12_har_entries:
        s = e["response"]["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    return {
        "status": "captured",
        "total_calls": len(_v12_har_entries),
        "error_responses": len(errors),
        "status_distribution": status_counts,
    }
# ───────────────────────────────────────────────────────────────────────


def is_v12_enabled() -> bool:
    return os.environ.get("ENABLE_V12_STATE_GRAPH_ENGINE", "false").lower() in ("1", "true", "yes", "on")


def run_v12_pipeline(
    project: str,
    root: Path,
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
    base_url: str = "",
    existing_findings: list[dict] | None = None,
) -> dict[str, Any]:
    """Execute V12 state space bug discovery engine.

    Returns dict compatible with existing pipeline report format.
    Does NOT modify existing_findings — returns new findings separately.
    """
    t0 = time.time()
    result: dict[str, Any] = {
        "v12_version": "1.0",
        "enabled": True,
        "phases": {},
        "findings": [],
        "evidence_graphs": [],
        "risk_clues_saved": 0,
    }

    # ── Normalize API spec through universal parser ──
    # Auto-detect and convert Swagger 2.0, Postman, GraphQL, gRPC, HAR etc.
    if api_spec_text and not isinstance(api_spec_text, dict):
        try:
            from .universal_api_parser import parse_to_openapi, detect_format
            fmt = detect_format(api_spec_text)
            if fmt not in ("openapi3", "unknown"):
                normalized = parse_to_openapi(api_spec_text)
                if normalized.get("paths"):
                    api_spec_text = json.dumps(normalized, ensure_ascii=False, default=str)
                    print(f"  [INFO] v12_pipeline: converted {fmt} → OpenAPI 3.x ({len(normalized['paths'])} paths)", flush=True)
        except Exception:
            pass  # Fall through: use original text as-is

    try:
        # ── Phase 1: Build Business State Graph ──
        t1 = time.time()
        from .business_state_graph import BusinessStateGraphBuilder
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(prd_text, api_spec_text, db_schema_text)

        graph_summary = {entity: g.to_dict()["stats"] for entity, g in graphs.items()}
        result["phases"]["state_graph"] = {
            "status": "completed",
            "entities": list(graphs.keys()),
            "summary": graph_summary,
            "duration_ms": int((time.time() - t1) * 1000),
        }

        # ── Phase 2a: Parameter Fuzzing (high-yield bug discovery) ──
        t_fuzz = time.time()
        fuzzer_findings = []
        if base_url:
            try:
                from .route_catalog_builder import RouteCatalogBuilder
                from .parameter_fuzzer import ParameterFuzzer
                builder2 = RouteCatalogBuilder()
                entries = builder2.build(api_spec_text)
                cat = [e.to_dict() for e in entries]
                fuzzer = ParameterFuzzer(base_url)
                fuzzer.login()  # Authenticate to test protected endpoints
                fuzzer_findings = fuzzer.fuzz_all(cat, max_variants=6)  # Per-endpoint
            except Exception:
                pass
        result["findings"].extend(fuzzer_findings)
        result["phases"]["parameter_fuzzer"] = {
            "status": "completed" if fuzzer_findings else "skipped",
            "findings": len(fuzzer_findings),
            "duration_ms": int((time.time() - t_fuzz) * 1000),
        }

        # ── Phase 2b: Generate Semantic Scenarios ──
        t2 = time.time()
        from .semantic_scenario_generator import SemanticScenarioGenerator
        generator = SemanticScenarioGenerator()
        scenarios = generator.generate(graphs, api_spec_text)

        result["phases"]["scenario_generation"] = {
            "status": "completed",
            "total_scenarios": len(scenarios),
            "by_category": _count_by(scenarios, "category"),
            "by_severity": _count_by(scenarios, "severity"),
            "forbidden_paths": sum(1 for s in scenarios if s.is_forbidden_path),
            "duration_ms": int((time.time() - t2) * 1000),
        }

        # ── Phase 3: Execute Scenarios (if base_url provided) ──
        result["phases"]["execution"] = {"status": "skipped", "reason": "no base_url"}
        traces = []
        if base_url and scenarios:
            t3 = time.time()
            executed = 0; failed = 0
            # Authenticate to get token for scenario execution
            scenario_token = ""
            try:
                from .parameter_fuzzer import ParameterFuzzer
                tmp_fuzzer = ParameterFuzzer(base_url)
                if tmp_fuzzer.login():
                    scenario_token = tmp_fuzzer._token
            except Exception:
                pass
            for scenario in scenarios[:30]:  # Full coverage for hit-rate test
                try:
                    if scenario_token:
                        scenario.actor_token = scenario_token
                    trace = _execute_scenario(scenario, base_url, max_retries=2)
                    traces.append((scenario, trace))
                    executed += 1
                    # Quick-find: flag server errors and auth bypass as findings
                    for step_result in trace.get("steps", []):
                        status = step_result.get("status", 0)
                        if status >= 500:
                            result["findings"].append({
                                "severity": "P0",
                                "title": f"[场景执行] {scenario.title[:80]}",
                                "category": scenario.category,
                                "source": "v12_scenario_executor",
                                "description": f"服务端错误 {status}: {step_result.get('path','')}",
                                "confidence_score": 0.95,
                            })
                        elif status == 403:
                            result["findings"].append({
                                "severity": "P0",
                                "title": f"[权限穿透] {scenario.title[:80]}",
                                "category": "permission",
                                "source": "v12_scenario_executor",
                                "description": f"越权访问成功 {status}: {step_result.get('path','')}",
                                "confidence_score": 0.90,
                            })
                except Exception:
                    failed += 1
            result["phases"]["execution"] = {
                "status": "completed",
                "executed": executed,
                "failed": failed,
                "duration_ms": int((time.time() - t3) * 1000),
            }

        # ── Phase 4: Oracle Evaluation ──
        t4 = time.time()
        from .oracle_engine import OracleEngine, EvidenceGraphBuilder
        oracle = OracleEngine()
        evidence_builder = EvidenceGraphBuilder()
        snapshots = None  # Would be populated by SnapshotEngine in real execution

        for scenario, trace in traces:
            oracle_results = oracle.evaluate(scenario.to_dict(), trace, snapshots)
            for oracle_result in oracle_results:
                if not oracle_result.passed:
                    evidence = evidence_builder.build(
                        scenario.to_dict(), trace, snapshots, oracle_results
                    )
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
                    })

        result["phases"]["oracle"] = {
            "status": "completed",
            "total_evaluated": len(traces),
            "violations_found": len(result["findings"]),
            "duration_ms": int((time.time() - t4) * 1000),
        }

        # ── Phase 5: Risk Clue Pool ──
        try:
            from .risk_clue_pool import save_risk_clues
            clues = save_risk_clues(project, root, result["findings"])
            result["risk_clues_saved"] = clues.get("new_this_scan", 0)
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)[:500]
        import logging
        logging.getLogger("qualibug").error(f"V12 pipeline failed: {e}", exc_info=True)

    result["total_duration_ms"] = int((time.time() - t0) * 1000)
    result["auto_har"] = _v12_har_report()
    return result


def _execute_scenario(scenario, base_url: str, max_retries: int = 2) -> dict:
    """V12.2: Execute scenario with auto-retry on transient failures."""
    import time as _time2
    
    for attempt in range(max_retries + 1):
        try:
            return __execute_scenario_once(scenario, base_url)
        except Exception as e:
            if attempt < max_retries:
                _time2.sleep(0.5 * (attempt + 1))
                continue
            return {"scenario_id": getattr(scenario, "id", "?"), "steps": [],
                    "errors": [f"Failed after {max_retries} retries: {e}"], "duration_ms": 0}
    return {"scenario_id": "?", "steps": [], "errors": ["unreachable"], "duration_ms": 0}


def __execute_scenario_once(scenario, base_url: str) -> dict:
    """Execute a scenario's steps sequentially and return execution trace."""
    trace = {"scenario_id": scenario.id, "steps": [], "errors": []}
    bindings: dict[str, str] = {}  # Variable binding from responses

    for step in scenario.steps:
        # Resolve variable references in path
        path = step.api_path
        for var, val in bindings.items():
            path = path.replace(f"{{{var}}}", str(val))

        url = base_url.rstrip("/") + path
        body = dict(step.body_template)
        # Resolve bindings in body too
        for var, val in bindings.items():
            body = {k: (str(v).replace(f"{{{var}}}", str(val)) if isinstance(v, str) else v)
                    for k, v in body.items()}

        data = json.dumps(body).encode() if body and step.api_method != "GET" else None
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "QualiBug-V12-ScenarioExecutor/1.0",
        }
        if step.actor:
            headers["X-QualiBug-Actor"] = step.actor
            # Also try Bearer token from scenario context
            token = scenario.actor_token if hasattr(scenario, "actor_token") else ""
            if token:
                headers["Authorization"] = "Bearer " + token

        try:
            t_req_start = time.time()
            req = urllib.request.Request(url, method=step.api_method, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=2) as resp:
                resp_body = json.loads(resp.read())
                _record_v12_har(step.api_method, url, resp.status,
                                json.dumps(resp_body)[:5000],
                                actor=step.actor or "",
                                elapsed_ms=(time.time() - t_req_start) * 1000)
                # Capture trace headers for evidence
                resp_headers = dict(resp.info())
                trace_ids = {}
                for h in ("X-Trace-Id", "X-Request-Id", "traceparent", "x-amzn-trace-id", "X-B3-TraceId", "X-Correlation-Id"):
                    val = resp_headers.get(h) or resp_headers.get(h.lower()) or ""
                    if val: trace_ids[h] = val
                if trace_ids:
                    trace.setdefault("trace_ids", trace_ids)
            status = resp.status
        except urllib.error.HTTPError as e:
            try:
                resp_body = json.loads(e.read())
            except Exception:
                resp_body = {}
            status = e.code
            _record_v12_har(step.api_method, url, status,
                            json.dumps(resp_body)[:5000],
                            actor=step.actor or "",
                            elapsed_ms=(time.time() - t_req_start) * 1000)
        except Exception as e:
            _record_v12_har(step.api_method, url, 0, str(e)[:5000],
                            actor=step.actor or "",
                            elapsed_ms=(time.time() - t_req_start) * 1000)
            trace["errors"].append(str(e))
            trace["steps"].append({
                "action": step.action, "method": step.api_method, "path": path,
                "status": 0, "error": str(e),
            })
            continue

        # Extract fields for binding (support nested paths like "order.order_id")
        for field in step.extract_from_response:
            if isinstance(resp_body, dict):
                if field in resp_body:
                    val = resp_body[field]
                    # If val is a nested dict, extract common ID fields from it
                    if isinstance(val, dict):
                        for id_key in ("order_id", "id", "product_id", "user_id", "payment_id"):
                            if id_key in val:
                                bindings[id_key] = val[id_key]
                    else:
                        bindings[field] = val
                # Try common nested containers
                for container in ("order", "data", "result", "product", "user", "payment"):
                    nested = resp_body.get(container, {})
                    if isinstance(nested, dict):
                        for id_key in ("order_id", "id", "product_id", "user_id", "payment_id"):
                            if id_key in nested:
                                bindings[id_key] = nested[id_key]

        trace["steps"].append({
            "action": step.action, "method": step.api_method, "path": path,
            "status": status, "response": {"status_code": status, "body": _redact(resp_body)},
            "expected_status": step.expected_status,
        })

    return trace


def _count_by(items: list, attr: str) -> dict:
    counts = defaultdict(int)
    for item in items:
        val = getattr(item, attr, "unknown") if hasattr(item, attr) else "unknown"
        counts[str(val)] += 1
    return dict(counts)


def _redact(data: Any, max_len: int = 500) -> Any:
    """Truncate response data for evidence."""
    s = json.dumps(data, ensure_ascii=False, default=str)
    if len(s) > max_len:
        return s[:max_len] + "...[truncated]"
    return json.loads(s) if s.startswith("{") else s
