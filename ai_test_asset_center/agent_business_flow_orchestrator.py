from __future__ import annotations

"""Phase76 multi-step business-flow orchestration for the Agent Loop.

This module extends the persistent Agent Loop from single document contracts to
explicit, reproducible business flows.  It deliberately does *not* turn PRD
text into autonomous production writes:

* PRD/API inference can only create candidate flow hypotheses.
* Executable flows require an explicit project mapping with roles, paths,
  fixture identifiers, expected outcomes and a disposable-sandbox approval.
* Every flow is persisted in the existing canonical discovery ledger as one
  experiment.  No second workflow database is introduced.
* A runtime observation becomes evidence, not a confirmed defect.  Existing
  human review is still required before a regression guard is generated.

The generic step grammar lets a project describe its own stateful business
flow without hard-coding MES, ERP, WMS, finance or SaaS semantics into the
platform.  The Agent can infer the need for a flow, but the enterprise decides
which confirmed workflow and fixture mappings are safe to execute.
"""

import copy
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .agent_discovery_loop import (
    build_agent_discovery_loop,
    load_agent_discovery_experiments,
    record_agent_discovery_evidence,
    record_agent_discovery_experiment_result,
    upsert_agent_discovery_experiment,
    upsert_agent_discovery_item,
)
from .agent_experiment_runner import (
    _accepted,
    _dotted,
    _execute_fixture_plan,
    _fixture_catalog,
    _fixture_headers,
    _ordered_fixtures,
)
from .concurrency_async_sandbox import _http
from .document_contract_fuzzing import execute_document_contracts
from .real_project_onboarding import ROOT, _join_url, _safe_project_id, config_paths, load_real_project_config
from .cognitive_memory_graph import CognitiveMemoryGraph, GraphContextComposer, RiskFrontierPlanner

PHASE = "phase76_agent_business_flow_orchestrator"
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_TOKEN_RE = re.compile(r"\$\{([^}]+)\}")
_HTTP_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(`?)(/[^\s`]+)\2", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _redact(value: Any, limit: int = 6000) -> Any:
    """Persist structural evidence while excluding credentials and raw payloads."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(marker in lower for marker in ("password", "token", "authorization", "api_key", "secret", "cookie")):
                clean[str(key)] = "[REDACTED]"
            else:
                clean[str(key)] = _redact(item, limit)
        return clean
    if isinstance(value, list):
        return [_redact(item, limit) for item in value[:80]]
    return str(value or "")[:limit]


def _output_dir(project: str, root: Path) -> Path:
    return root / "platform_outputs" / project / "agent_discovery_loop"


def _input_texts(project: str, root: Path) -> tuple[str, str]:
    input_dir = config_paths(project, root)["input_dir"]
    prd_path = input_dir / "prd.md"
    prd = prd_path.read_text(encoding="utf-8", errors="replace") if prd_path.exists() else ""
    docs = [path for path in input_dir.glob("*.md") if path.name.lower() not in {"prd.md", "readme.md"}]
    if not docs:
        return prd, ""
    docs.sort(key=lambda path: (sum(path.read_text(encoding="utf-8", errors="replace").upper().count(token) for token in _WRITE_METHODS | {"GET"}), path.stat().st_size), reverse=True)
    return prd, docs[0].read_text(encoding="utf-8", errors="replace")


def _agent_section(cfg: dict[str, Any]) -> dict[str, Any]:
    for key in ("agent_discovery_loop", "agent_loop", "autonomous_discovery"):
        value = cfg.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _flow_section(cfg: dict[str, Any]) -> dict[str, Any]:
    agent = _agent_section(cfg)
    for value in (
        agent.get("business_flow_catalog"),
        agent.get("business_flows"),
        cfg.get("business_flow_catalog"),
        cfg.get("business_flows"),
    ):
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"flows": value}
    return {}


def _configured_flows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    section = _flow_section(cfg)
    rows = section.get("flows") or section.get("contracts") or []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("enabled") is False:
            continue
        flow = copy.deepcopy(raw)
        flow_id = str(flow.get("flow_id") or flow.get("id") or "").strip()
        if not flow_id or flow_id in seen:
            continue
        seen.add(flow_id)
        flow["flow_id"] = flow_id
        result.append(flow)
    return result


def _route_stem(path: str) -> str:
    parts = [part for part in str(path).split("/") if part and not part.startswith("{")]
    if not parts:
        return "/"
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    return "/" + parts[0]


def infer_business_flow_candidates(project_id: str = "real_project_demo", root: Path | None = None) -> list[dict[str, Any]]:
    """Infer candidate lifecycle flows from public API documents, never executable.

    A generic REST surface with one create route and one or more nested action
    routes is evidence that a stateful flow exists.  It is not evidence of its
    legal transitions, valid payloads, or safe cleanup; hence every inferred
    result remains candidate-only until explicitly mapped in project config.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    _, api_text = _input_texts(project, root)
    rows: list[tuple[str, str]] = []
    for match in _HTTP_METHOD_RE.finditer(api_text):
        method = match.group(1).upper()
        path = match.group(3).rstrip(".,。)")
        rows.append((method, path))
    grouped: dict[str, list[tuple[str, str]]] = {}
    for method, path in rows:
        grouped.setdefault(_route_stem(path), []).append((method, path))
    candidates: list[dict[str, Any]] = []
    for stem, routes in sorted(grouped.items()):
        creates = [path for method, path in routes if method == "POST" and "{" not in path]
        actions = [path for method, path in routes if "{" in path and path.rstrip("/") != stem.rstrip("/")]
        if not creates or not actions:
            continue
        fingerprint = _hash({"stem": stem, "creates": creates, "actions": actions}, 28)
        candidates.append({
            "candidate_id": f"FLOW_CAND_{fingerprint}",
            "title": f"候选业务流：{stem}",
            "source": "document_flow_inference",
            "source_ref": fingerprint,
            "flow_stem": stem,
            "create_paths": sorted(set(creates)),
            "action_paths": sorted(set(actions)),
            "risk_type": "business_flow_state_transition",
            "oracle_family": "state_consistency",
            "severity": "P1",
            "evidence_strength": "contract_inferred",
            "execution_policy": "candidate_only",
            "status": "needs_business_flow_mapping",
            "why_review_required": "API routes prove a stateful workflow surface but not legal transitions, fixture semantics, or safe cleanup.",
        })
    return candidates[:80]


def _flow_fixture_plan(flow: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    raw_ids = flow.get("fixture_ids") or flow.get("fixtures") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    requested = {str(value).strip() for value in raw_ids if str(value).strip()}
    catalog = _fixture_catalog(cfg)
    fixtures, errors = _ordered_fixtures(catalog, requested)
    return {
        "required": bool(requested),
        "ready": not errors,
        "fixture_ids": sorted(requested),
        "fixtures": fixtures,
        "field_bindings": [],
        "blocking_reasons": errors,
    }


def _step_blockers(step: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    kind = str(step.get("kind") or "request").lower()
    if kind not in {"request", "snapshot"}:
        blockers.append("unsupported_step_kind")
        return blockers
    method = str(step.get("method") or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        blockers.append("unsupported_http_method")
    path = str(step.get("path") or "")
    if not path.startswith("/"):
        blockers.append("step_path_required")
    role = str(step.get("role") or cfg.get("default_role") or "")
    headers = _fixture_headers(cfg, {"role": role})
    if not headers:
        blockers.append(f"role_headers_missing:{role or 'default_role'}")
    return blockers


def _flow_scenario(item: dict[str, Any], flow: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    steps = [copy.deepcopy(step) for step in (flow.get("steps") or []) if isinstance(step, dict)]
    writes = any(str(step.get("method") or "GET").upper() in _WRITE_METHODS for step in steps)
    flow_id = str(flow["flow_id"])
    blockers: list[str] = []
    if not steps:
        blockers.append("flow_steps_required")
    seen_step_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        row = copy.deepcopy(step)
        step_id = str(row.get("step_id") or row.get("id") or f"step_{index:02d}")
        if step_id in seen_step_ids:
            blockers.append(f"duplicate_step_id:{step_id}")
        seen_step_ids.add(step_id)
        row["step_id"] = step_id
        row["kind"] = str(row.get("kind") or ("snapshot" if row.get("snapshot_id") else "request")).lower()
        row["method"] = str(row.get("method") or "GET").upper()
        row["role"] = str(row.get("role") or cfg.get("default_role") or "")
        blockers.extend(_step_blockers(row, cfg))
        normalized.append(row)
    fixture_plan = _flow_fixture_plan(flow, cfg)
    blockers.extend(fixture_plan.get("blocking_reasons") or [])
    from .shared_test_environment import compile_cleanup_plan
    cleanup_plan = compile_cleanup_plan(flow, writes=writes)
    blockers.extend(cleanup_plan.get("blockers") or [])
    policy = "sandbox_required" if writes else "safe_read_only"
    return {
        "scenario_id": f"FLOW_SCN_{_hash([item.get('item_id'), flow_id, normalized], 28)}",
        "ledger_item_id": item.get("item_id"),
        "flow_id": flow_id,
        "title": str(flow.get("title") or item.get("title") or flow_id),
        "risk_type": str(flow.get("risk_type") or "business_flow_state_transition"),
        "severity": str(flow.get("severity") or item.get("severity") or "P1").upper(),
        "execution_policy": policy,
        "scenario_kind": "multi_step_business_flow",
        "fixture_plan": fixture_plan,
        "steps": normalized,
        "assertions": [copy.deepcopy(row) for row in (flow.get("assertions") or []) if isinstance(row, dict)],
        "preconditions": {
            "sandbox_must_be_disposable": writes,
            "approved_sandbox_execution": writes,
            "explicit_flow_mapping": True,
            "all_steps_have_authorised_role_headers": not any(reason.startswith("role_headers_missing:") for reason in blockers),
        },
        "cleanup": {
            **cleanup_plan,
            "never_attempted_outside_disposable_sandbox": writes,
            "direct_delete_cleanup_not_supported": True,
            "explicit_compensation_or_restore_supported": True,
            "requires_explicit_mapping": writes,
        },
        "blocking_reasons": sorted(set(blockers)),
        "flow": copy.deepcopy(flow),
        "governance": {
            "flow_mapping_must_be_explicit": True,
            "document_inference_alone_is_candidate_only": True,
            "no_target_request_during_compilation": True,
            "writes_require_existing_disposable_sandbox_gate": True,
            "formal_bug_requires_runtime_evidence_and_human_verdict": True,
        },
    }



def _phase91_flow_graph_context(
    graph: CognitiveMemoryGraph,
    flow: dict[str, Any],
    *,
    writes: bool,
    run_id: str = "",
    policy_version: str = "",
) -> dict[str, Any]:
    """Attach a bounded, read-only graph context to an explicitly mapped flow.

    Flow configuration remains authoritative.  Graph facts may guide review and
    ordering but cannot create writes, grant approval, or relax cleanup gates.
    """
    flow_id = str(flow.get("flow_id") or "flow")
    risk_type = str(flow.get("risk_type") or "business_flow_state_transition")
    steps = [dict(step) for step in (flow.get("steps") or []) if isinstance(step, dict)]
    first = steps[0] if steps else {}
    api = f"{str(first.get('method') or 'GET').upper()} {str(first.get('path') or '')}".strip()
    flow_ref = graph.upsert_node(
        "Flow", f"configured:{flow_id}", str(flow.get("title") or flow_id),
        source="business_flow_catalog", source_ref=flow_id, confidence="evidenced",
        payload={"flow_id": flow_id, "risk_type": risk_type, "writes": writes, "api": api},
        run_id=run_id, policy_version=policy_version,
    )
    gap_ref = graph.upsert_node(
        "CoverageGap", f"flow:{flow_id}:{risk_type}", f"Risk coverage: {flow_id} · {risk_type}",
        source="business_flow_catalog", source_ref=flow_id, confidence="inferred",
        payload={
            "api": api,
            "risk_type": risk_type,
            "business_impact": 0.82 if writes else 0.58,
            "uncertainty": 0.72,
            "coverage_gap": 1.0,
            "available_observer_quality": 0.65 if flow.get("assertions") else 0.45,
            "execution_cost": 0.65 if writes else 0.28,
            "duplicate_risk": 0.2,
            "safety_risk": 0.85 if writes else 0.15,
            "cleanup_status": "READY",
            "state": "READY_FOR_DISCOVERY",
        },
        run_id=run_id, policy_version=policy_version,
    )
    graph.add_edge(flow_ref.node_id, gap_ref.node_id, "related_to", source="business_flow_catalog", source_ref=flow_id,
                   confidence="evidenced", run_id=run_id, policy_version=policy_version)
    context = GraphContextComposer(graph).compose(
        {"api": api, "risk_type": risk_type, "flow_id": flow_id},
        high_risk_write=bool(writes),
    )
    frontier = RiskFrontierPlanner(graph).rank(limit=30)
    selected = next((row for row in frontier if row.get("risk_surface_id") == gap_ref.node_id), None)
    return {
        "mode": "shadow",
        "system_of_record": "cognitive_memory_graph.sqlite3",
        "target": context.get("target") or {},
        "context_refs": context.get("context_refs") or [],
        "context_char_count": len(str(context.get("rendered_context") or "")),
        "frontier": selected or {},
        "flow_graph_node_id": flow_ref.node_id,
        "risk_surface_id": gap_ref.node_id,
        "writes_remain_governed_by_existing_safety_and_cleanup_gates": True,
    }

def compile_agent_business_flow_pack(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile inferred candidates and explicit flows into ledger-backed packets."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = dict(options or {})
    cfg = load_real_project_config(project, root)
    environment_id = str(options.get("environment_id") or options.get("environment") or "test")
    graph = CognitiveMemoryGraph(project, environment_id, root)
    build_agent_discovery_loop(project, root, {
        "actor": "agent_flow_orchestrator",
        "max_next_actions": int(options.get("max_next_actions") or 24),
        "environment_id": environment_id,
    })

    candidates: list[dict[str, Any]] = []
    for candidate in infer_business_flow_candidates(project, root):
        candidates.append(upsert_agent_discovery_item(
            project,
            item_type="business_flow_hypothesis",
            title=str(candidate["title"]),
            source="document_flow_inference",
            source_ref=str(candidate["candidate_id"]),
            severity=str(candidate["severity"]),
            payload=candidate,
            evidence_strength="contract_inferred",
            root=root,
            actor="agent_flow_inference",
        ))

    experiments: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    max_flows = max(1, min(int(options.get("max_flows") or 24), 100))
    for flow in _configured_flows(cfg)[:max_flows]:
        flow_id = str(flow["flow_id"])
        writes = any(str(step.get("method") or "GET").upper() in _WRITE_METHODS for step in (flow.get("steps") or []) if isinstance(step, dict))
        policy = "sandbox_required" if writes else "safe_read_only"
        item = upsert_agent_discovery_item(
            project,
            item_type="business_flow_experiment",
            title=str(flow.get("title") or f"业务流验证：{flow_id}"),
            source="business_flow_catalog",
            source_ref=flow_id,
            severity=str(flow.get("severity") or "P1"),
            payload={
                "flow_id": flow_id,
                "risk_type": str(flow.get("risk_type") or "business_flow_state_transition"),
                "oracle_family": str(flow.get("oracle_family") or "business_flow"),
                "execution_policy": policy,
                "configured_by": str(flow.get("configured_by") or "business_owner"),
                "source_candidate_id": flow.get("source_candidate_id"),
            },
            evidence_strength="contract_inferred",
            root=root,
            actor="agent_flow_compiler",
        )
        scenario = _flow_scenario(item, flow, cfg)
        try:
            scenario["cognitive_graph"] = _phase91_flow_graph_context(
                graph, flow, writes=writes, run_id=str(item.get("item_id") or ""),
            )
        except Exception as exc:
            # The graph may inform a flow but may never make a mapped flow unsafe
            # or unusable.  Record the degradation transparently.
            scenario["cognitive_graph"] = {"mode": "off", "error": f"{type(exc).__name__}: {exc}", "degraded": True}
        state = "COMPILED" if policy == "safe_read_only" else "BLOCKED_BY_APPROVAL"
        if (scenario.get("cleanup") or {}).get("status") == "BLOCKED_BY_CLEANUP":
            state = "BLOCKED_BY_CLEANUP"
        elif scenario.get("blocking_reasons"):
            state = "BLOCKED_BY_FIXTURE"
        experiment = upsert_agent_discovery_experiment(
            project,
            str(item["item_id"]),
            scenario,
            experiment_type="multi_step_business_flow",
            state=state,
            executor="agent_business_flow_orchestrator",
            root=root,
            actor="agent_flow_compiler",
        )
        experiments.append({
            "experiment_id": experiment.get("experiment_id"),
            "item_id": item.get("item_id"),
            "flow_id": flow_id,
            "state": experiment.get("state"),
            "scenario": scenario,
        })

    report = {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "summary": {
            "inferred_candidate_count": len(candidates),
            "configured_flow_count": len(experiments),
            "safe_read_flow_count": sum(1 for row in experiments if row["scenario"].get("execution_policy") == "safe_read_only"),
            "sandbox_flow_count": sum(1 for row in experiments if row["scenario"].get("execution_policy") == "sandbox_required"),
            "fixture_blocked_flow_count": sum(1 for row in experiments if row.get("state") == "BLOCKED_BY_FIXTURE"),
            "skipped_count": len(skipped),
        },
        "candidates": candidates,
        "flows": experiments,
        "skipped": skipped,
        "cognitive_graph": {
            "mode": "shadow",
            "environment_id": environment_id,
            "stats": graph.stats(),
            "flow_context_count": sum(1 for row in experiments if isinstance((row.get("scenario") or {}).get("cognitive_graph"), dict)),
            "graph_cannot_authorize_execution": True,
        },
        "governance": {
            "single_canonical_state_store": "agent_discovery_loop.sqlite3",
            "unknown_bug_total_not_used": True,
            "inferred_flows_are_candidate_only": True,
            "explicit_flow_mapping_required_before_execution": True,
            "writes_remain_blocked_without_existing_sandbox_approval": True,
        },
    }
    out = _output_dir(project, root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "compiled_business_flow_pack.json").write_text(json.dumps(_redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _lookup(context: dict[str, Any], token: str, run_key: str) -> str:
    token = token.strip()
    if token == "run_key":
        return run_key
    if token.startswith("fixture."):
        value = context.get("fixture", {}).get(token.split(".", 1)[1])
    elif token.startswith("flow."):
        value = context.get("flow", {}).get(token.split(".", 1)[1])
    elif token.startswith("snapshot."):
        value = context.get("snapshot", {}).get(token.split(".", 1)[1])
    else:
        value = context.get(token)
    if value is None:
        raise KeyError(f"unresolved_template:{token}")
    return str(value)


def _render(value: Any, context: dict[str, Any], run_key: str) -> Any:
    if isinstance(value, dict):
        return {str(key): _render(item, context, run_key) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, context, run_key) for item in value]
    if not isinstance(value, str):
        return value
    return _TOKEN_RE.sub(lambda match: _lookup(context, match.group(1), run_key), value)


def _step_headers(cfg: dict[str, Any], role: str) -> dict[str, str]:
    return _fixture_headers(cfg, {"role": role})


def _compact_response(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("payload")
    return {
        "status_code": response.get("status_code"),
        "accepted": _accepted(response),
        "error": response.get("error"),
        "payload_hash": _hash(_redact(payload), 32),
        "payload_type": type(payload).__name__,
    }


def _record_captures(step: dict[str, Any], response: dict[str, Any], context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    captures = step.get("captures") or step.get("capture") or {}
    if not isinstance(captures, dict):
        return missing
    for key, dotted in captures.items():
        value = _dotted(response.get("payload"), str(dotted))
        if value in {None, ""}:
            missing.append(str(key))
        else:
            context.setdefault("flow", {})[str(key)] = value
    return missing


def _value(snapshot: dict[str, Any], dotted: str) -> Any:
    return _dotted(snapshot.get("payload"), dotted)


def _assertions(assertions: list[dict[str, Any]], snapshots: dict[str, dict[str, Any]], context: dict[str, Any], severity: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, assertion in enumerate(assertions, start=1):
        kind = str(assertion.get("kind") or assertion.get("type") or "").lower()
        before_name = str(assertion.get("before") or assertion.get("left_snapshot") or "")
        after_name = str(assertion.get("after") or assertion.get("right_snapshot") or "")
        path = str(assertion.get("path") or assertion.get("field") or "")
        if kind in {"snapshot_path_unchanged", "snapshot_path_equal"}:
            before = _value(snapshots.get(before_name, {}), path)
            after = _value(snapshots.get(after_name, {}), path)
            if before != after:
                findings.append({
                    "finding_id": f"FLOW_ASSERT_{_hash([before_name, after_name, path, index])}",
                    "title": str(assertion.get("title") or f"业务流快照不一致：{path}"),
                    "severity": str(assertion.get("severity") or severity),
                    "risk_type": str(assertion.get("risk_type") or "state_consistency"),
                    "expected": "snapshot value remains unchanged",
                    "actual": "snapshot value changed after a failed/forbidden action",
                    "evidence": {"before_snapshot": before_name, "after_snapshot": after_name, "field": path, "before_hash": _hash(before), "after_hash": _hash(after)},
                })
        elif kind == "snapshot_numeric_delta":
            before = _value(snapshots.get(before_name, {}), path)
            after = _value(snapshots.get(after_name, {}), path)
            expected_delta = assertion.get("expected_delta", 0)
            try:
                delta = float(after) - float(before)
                expected = float(expected_delta)
            except (TypeError, ValueError):
                continue
            if abs(delta - expected) > float(assertion.get("tolerance", 0.000001)):
                findings.append({
                    "finding_id": f"FLOW_ASSERT_{_hash([before_name, after_name, path, index])}",
                    "title": str(assertion.get("title") or f"业务流数量守恒失败：{path}"),
                    "severity": str(assertion.get("severity") or severity),
                    "risk_type": str(assertion.get("risk_type") or "conservation_check"),
                    "expected": f"numeric delta = {expected}",
                    "actual": f"numeric delta = {round(delta, 8)}",
                    "evidence": {"before_snapshot": before_name, "after_snapshot": after_name, "field": path, "expected_delta": expected, "actual_delta": round(delta, 8)},
                })
        elif kind == "flow_value_present":
            key = str(assertion.get("key") or "")
            if key and context.get("flow", {}).get(key) in {None, ""}:
                findings.append({
                    "finding_id": f"FLOW_ASSERT_{_hash([key, index])}",
                    "title": str(assertion.get("title") or f"业务流未产生必需标识：{key}"),
                    "severity": str(assertion.get("severity") or severity),
                    "risk_type": str(assertion.get("risk_type") or "business_outcome_validation"),
                    "expected": f"flow captures {key}",
                    "actual": "required capture missing",
                    "evidence": {"capture_key": key},
                })
    return findings


def _runtime_finding(step: dict[str, Any], response: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any] | None:
    expect = step.get("expect") if isinstance(step.get("expect"), dict) else {}
    expected_accepted = expect.get("accepted")
    accepted = _accepted(response)
    status_codes = expect.get("status_codes") or expect.get("allowed_status_codes") or []
    status_bad = bool(status_codes) and response.get("status_code") not in {int(code) for code in status_codes if str(code).isdigit()}
    # A positive setup step failure is an execution blocker by default, not a
    # product bug.  A negative or explicitly assertion-bearing step becomes a
    # finding only when the documented expectation is violated.
    violation = False
    if isinstance(expected_accepted, bool):
        violation = accepted != expected_accepted
    if status_bad:
        violation = True
    if not violation:
        return None
    if expected_accepted is True and not bool(step.get("failure_is_bug")):
        return None
    return {
        "finding_id": f"FLOW_STEP_{_hash([scenario.get('scenario_id'), step.get('step_id'), response.get('status_code')])}",
        "title": str(step.get("title") or scenario.get("title") or "业务流约束未被执行"),
        "severity": str(step.get("severity") or scenario.get("severity") or "P1"),
        "risk_type": str(step.get("risk_type") or scenario.get("risk_type") or "business_flow_state_transition"),
        "expected": expect.get("description") or ("request is rejected" if expected_accepted is False else "request is accepted"),
        "actual": f"HTTP {response.get('status_code')} accepted={accepted}",
        "evidence": {"step_id": step.get("step_id"), **_compact_response(response)},
    }


def _verify_with_semantic_verifier(
    scenario: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
    context: dict[str, Any],
    steps: list[dict[str, Any]],
    cfg: dict[str, Any],
    project_id: str | None = None,
    run_key: str = "",
) -> list[dict[str, Any]] | None:
    """Phase78B: Route flow verification through SemanticStateVerifier.

    Returns findings list on success, None to fall back to old _assertions().
    """
    assertions = scenario.get("assertions") or []
    if not assertions:
        return []  # No assertions = no findings

    try:
        from .semantic_state_verifier import SemanticStateVerifier
        from .state_observer_registry import StateObserver, CanonicalStateSnapshot
        from .state_projection_engine import StateProjectionEngine
        from .proof_obligation_compiler import ProofObligationCompiler
        from .business_invariant_evaluator import BusinessInvariantEvaluator
        from .evidence_graph_builder import EvidenceGraphBuilder
    except ImportError:
        return None  # Phase77 modules not available → fallback

    observer = StateObserver(redact_sensitive=True)
    proj_engine = StateProjectionEngine()
    compiler = ProofObligationCompiler()
    evaluator = BusinessInvariantEvaluator()
    graph_builder = EvidenceGraphBuilder()

    base_url = str(cfg.get("base_url") or "").rstrip("/")
    verifier = SemanticStateVerifier(
        project_id=project_id or str(cfg.get("project_id") or "unknown"),
        base_url=base_url,
        redact_sensitive=True,
    )

    findings: list[dict[str, Any]] = []
    severity = str(scenario.get("severity") or "P1")

    # ── Map old snapshots to CanonicalStateSnapshots ──
    canonical_snapshots: dict[str, CanonicalStateSnapshot] = {}
    for snap_name, snap_data in snapshots.items():
        payload = snap_data.get("payload") or {}
        status = snap_data.get("status_code", 0)
        snap = observer.observe_from_http(
            payload, status,
            endpoint=f"flow_snapshot/{snap_name}",
            observer_id=snap_name,
            entity_alias="primary",
        )
        # Apply projection from scenario observers if available
        observers_cfg = scenario.get("observers") or []
        for obs_cfg in observers_cfg:
            if isinstance(obs_cfg, dict) and obs_cfg.get("id") == snap_name:
                proj_map = obs_cfg.get("projection") or {}
                if proj_map:
                    snap = observer.apply_projection(snap, proj_map, payload)
                break
        canonical_snapshots[snap_name] = snap

    # ── Compile assertions to ProofObligations ──
    obligations = compiler.compile_from_flow({
        "flow_id": scenario.get("flow_id", ""),
        "steps": steps,
        "assertions": assertions,
    })

    if not obligations:
        return None  # No obligations compiled → fallback

    # ── Evaluate each obligation ──
    for obl in obligations:
        before_name = obl.assertion_config.get("before", "")
        after_name = obl.assertion_config.get("after", "")
        before_snap = canonical_snapshots.get(before_name)
        after_snap = canonical_snapshots.get(after_name)

        if not before_snap or not after_snap:
            findings.append({
                "finding_id": f"SEM_{_hash([obl.obligation_id, 'missing_snapshot'], 16)}",
                "title": f"Snapshot missing for {obl.kind}: before={before_name}, after={after_name}",
                "severity": severity,
                "risk_type": obl.kind,
                "expected": "Both before and after snapshots must be available",
                "actual": f"before={'present' if before_snap else 'missing'}, after={'present' if after_snap else 'missing'}",
                "verdict": "INSUFFICIENT_INSTRUMENTATION",
                "missing_capability": "snapshot_observer",
                "actionable_next_step": f"Configure observer for {'before' if not before_snap else 'after'} snapshot",
                "evidence": {"obligation_id": obl.obligation_id, "kind": obl.kind},
            })
            continue

        # Convert compiler obligation to evaluator obligation
        eval_obl = _to_eval_obligation(obl)
        result = evaluator.evaluate(eval_obl, before_snap, after_snap)

        # Convert verifier result to finding
        finding = {
            "finding_id": f"SEM_{_hash([obl.obligation_id, result.verdict], 16)}",
            "title": f"[{obl.kind}] {obl.description[:120]}",
            "severity": obl.severity or severity,
            "risk_type": obl.kind,
            "expected": obl.description,
            "actual": result.detail,
            "verdict": "CONFIRMED" if not result.passed else "FALSIFIED",
            "evidence": {
                "obligation_id": obl.obligation_id,
                "kind": obl.kind,
                "before_snapshot": before_name,
                "after_snapshot": after_name,
                "invariant_verdict": result.verdict,
                "computed": result.computed,
                "failed_fields": result.failed_fields,
            },
        }
        findings.append(finding)

    # ── Build Evidence Graph ──
    try:
        graph = graph_builder.build_from_verification(
            hypothesis={"title": scenario.get("title", scenario.get("flow_id", "")), "severity": severity},
            obligations=[{"obligation_id": o.obligation_id, "kind": o.kind, "description": o.description,
                         "severity": o.severity, "entity_alias": o.entity_alias} for o in obligations],
            before_snapshot=list(canonical_snapshots.values())[0] if canonical_snapshots else None,
            after_snapshot=list(canonical_snapshots.values())[-1] if len(canonical_snapshots) > 1 else None,
            results=[{
                "obligation_id": f["evidence"].get("obligation_id", ""),
                "kind": f["evidence"].get("kind", ""),
                "verdict": f["evidence"].get("invariant_verdict", ""),
                "passed": f["verdict"] == "FALSIFIED",
                "detail": f["actual"],
            } for f in findings],
            verdict="COMPLETED",
        )
        # Attach graph reference to first finding for ledger traceability
        if findings:
            findings[0]["evidence_graph_ref"] = graph.graph_id if hasattr(graph, 'graph_id') else str(_hash(str(graph), 16))
    except Exception:
        pass  # Evidence graph is best-effort

    # ── Phase83B: Adversarial Validation Gate ──
    # Route every finding through deterministic disprover + schema validator
    # before allowing CONFIRMED verdict. Prevents auto-confirmation.
    try:
        from .business_adversarial_validator import deterministic_disprove
        from .business_finding_schema_validator import validate_finding
        from .finding_deduplicator import build_fingerprint
        from .independent_evidence_verifier import verify_evidence_integrity

        enriched_findings: list[dict[str, Any]] = []
        for f in findings:
            # Convert verifier finding to business finding schema format
            evidence = f.get("evidence") or {}
            bf = {
                "finding_id": f.get("finding_id", ""),
                "hypothesis_id": evidence.get("obligation_id", ""),
                "project_id": project_id or str(cfg.get("project_id", "real_project_demo")),
                "verdict": "CANDIDATE",  # Will be updated by adversarial validator
                "title": f.get("title", "")[:300],
                "root_cause_candidate": f.get("actual", "")[:500],
                "entrypoint": {
                    "flow_id": scenario.get("flow_id", ""),
                    "action_type": evidence.get("kind", ""),
                },
                "entity_binding": {
                    "entity_alias": "primary",
                    "entity_type": evidence.get("kind", "unknown"),
                    "entity_id": f.get("finding_id", ""),
                    "tenant_id": project_id or "default",
                    "binding_confidence": 0.8,
                },
                "before_snapshot_ref": evidence.get("before_snapshot", ""),
                "action_evidence_ref": f.get("evidence_graph_ref", ""),
                "after_snapshot_ref": evidence.get("after_snapshot", ""),
                "observer_refs": [],
                "violated_invariant": {
                    "kind": evidence.get("kind", "state"),
                    "definition": f.get("expected", "")[:300],
                    "result": f.get("actual", "")[:300],
                    "evidence_ref": evidence.get("obligation_id", ""),
                },
                "reproduction": {
                    "flow_id": scenario.get("flow_id", ""),
                    "expected_observation": f.get("expected", ""),
                },
                "cleanup": {
                    "run_id": run_key or "",
                    "status": "NOT_APPLICABLE",
                    "evidence_ref": "",
                },
                "adversarial_validation": {
                    "deterministic_result": "",
                    "disprover_result": "NOT_RUN",
                    "counterarguments": [],
                },
                "evidence_refs": [evidence.get("obligation_id", "")],
            }

            # Run deterministic disprover
            det_result = deterministic_disprove(bf)
            bf["adversarial_validation"] = {
                "deterministic_result": det_result["result"],
                "disprover_result": "NOT_RUN",
                "counterarguments": det_result.get("counterarguments", [])[:5],
                "unresolved_questions": det_result.get("unresolved", [])[:5],
                "disprover_source": "deterministic",
            }

            # Update verdict based on deterministic result
            if det_result["result"] == "DETERMINISTIC_DISPROOF":
                bf["verdict"] = "REJECTED"
            elif det_result["result"] == "DETERMINISTIC_INSUFFICIENT_EVIDENCE":
                bf["verdict"] = "NEEDS_MORE_EVIDENCE"
            elif det_result["result"] == "DETERMINISTIC_CONFLICT":
                bf["verdict"] = "NEEDS_MORE_EVIDENCE"
            else:
                # Deterministic pass — validate schema
                schema_ok = validate_finding(bf)
                if schema_ok["valid"]:
                    ev_check = verify_evidence_integrity(bf)
                    if ev_check["passed"]:
                        bf["verdict"] = "VALIDATED_CANDIDATE"
                    else:
                        bf["verdict"] = "NEEDS_MORE_EVIDENCE"
                else:
                    bf["verdict"] = schema_ok.get("verdict", "NEEDS_MORE_EVIDENCE")

            # Preserve original evidence for backward compatibility
            bf["_original_evidence"] = evidence
            bf["_fingerprint"] = build_fingerprint(bf)
            enriched_findings.append(bf)

        findings = enriched_findings
    except ImportError:
        pass  # Phase83B modules may not be available
    except Exception:
        pass  # Adversarial validation is best-effort — never crash the flow

    return findings if findings else []


def _to_eval_obligation(obl) -> Any:
    """Convert ProofObligationCompiler format to BusinessInvariantEvaluator format."""
    from .business_invariant_evaluator import ProofObligation as EvalObl
    ac = obl.assertion_config or {}
    return EvalObl(
        obligation_id=obl.obligation_id,
        kind=obl.kind,
        title=obl.description,
        severity=obl.severity,
        fields=ac.get("fields", []),
        allowed_transitions=ac.get("allowed_transitions", {}),
        expected_delta=float(ac.get("expected_delta", 0)),
        tolerance=float(ac.get("tolerance", 1e-6)),
        expression=str(ac.get("expression", "")),
        eventually_timeout=float(ac.get("timeout_seconds", ac.get("eventually_timeout", 10))),
        eventually_poll_interval=float(ac.get("interval_seconds", ac.get("eventually_poll_interval", 1))),
        eventually_field=str(ac.get("target_state", "")),
    )


def _execute_flow(
    cfg: dict[str, Any],
    scenario: dict[str, Any],
    run_key: str,
    *,
    project_id: str = "real_project_demo",
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    from .shared_test_environment import (
        DirtyTestEnvironmentGuard, TestRunSession, execute_cleanup_plan,
        persist_session, register_response,
    )
    writes = any(str(step.get("method") or "GET").upper() in _WRITE_METHODS for step in (scenario.get("steps") or []))
    environment_id = str(cfg.get("environment_id") or cfg.get("target_environment") or "test")
    guard = DirtyTestEnvironmentGuard(root, project_id, environment_id)
    if writes:
        try:
            guard.assert_writable()
        except RuntimeError as exc:
            return {"status": "blocked", "blockers": ["DIRTY_TEST_ENVIRONMENT", str(exc)], "findings": [], "steps": [], "fixture_receipts": [], "cleanup": {"run_id": run_key, "status": "CLEANUP_FAILED", "evidence_ref": "", "reason": str(exc)}}
    session = TestRunSession(project_id=project_id, environment_id=environment_id, run_id=run_key)
    persist_session(root, session)
    fixture_context, fixture_receipts = _execute_fixture_plan(cfg, scenario.get("fixture_plan") or {}, run_key)
    fixture_failed = any(receipt.get("status") == "blocked" or not receipt.get("accepted", True) for receipt in fixture_receipts)
    if fixture_failed:
        session.status = "BLOCKED_BY_FIXTURE"
        session.cleanup_status = "NOT_APPLICABLE"
        evidence_path = persist_session(root, session)
        return {"status": "blocked", "blockers": ["fixture_precondition_failed"], "findings": [], "steps": [], "fixture_receipts": fixture_receipts, "cleanup": {"run_id": run_key, "status": "NOT_APPLICABLE", "evidence_ref": str(evidence_path)}}
    context: dict[str, Any] = {"fixture": fixture_context, "flow": {}, "snapshot": {}}
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    snapshots: dict[str, dict[str, Any]] = {}
    steps: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    blockers: list[str] = []
    for step in scenario.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "step")
        try:
            path = str(_render(step.get("path") or "", context, run_key))
            body = _render(step.get("body") or {}, context, run_key) if str(step.get("method") or "GET").upper() in _WRITE_METHODS else None
        except KeyError as exc:
            blockers.append(str(exc))
            steps.append({"step_id": step_id, "status": "blocked", "reason": str(exc)})
            break
        method = str(step.get("method") or "GET").upper()
        role = str(step.get("role") or cfg.get("default_role") or "")
        headers = _step_headers(cfg, role)
        if not headers:
            blockers.append(f"role_headers_missing:{role or 'default_role'}")
            steps.append({"step_id": step_id, "status": "blocked", "reason": blockers[-1]})
            break
        response = _http(_join_url(base_url, path), method, body=body, headers=headers)
        compact = _compact_response(response)
        register_response(session, method=method, path=path, response=response)
        receipt = {"step_id": step_id, "kind": step.get("kind"), "method": method, "path": path, "role": role, **compact}
        missing_captures = _record_captures(step, response, context)
        if missing_captures:
            receipt["missing_captures"] = missing_captures
        snapshot_id = str(step.get("snapshot_id") or step.get("snapshot") or "")
        if snapshot_id:
            snapshots[snapshot_id] = {"payload": response.get("payload"), "status_code": response.get("status_code")}
            context["snapshot"][snapshot_id] = _hash(_redact(response.get("payload")), 20)
            receipt["snapshot_id"] = snapshot_id
        finding = _runtime_finding(step, response, scenario)
        if finding:
            findings.append(finding)
        steps.append(receipt)
        expected = step.get("expect") if isinstance(step.get("expect"), dict) else {}
        expected_accepted = expected.get("accepted")
        if expected_accepted is True and not _accepted(response) and not bool(step.get("continue_on_failure")):
            blockers.append(f"positive_precondition_failed:{step_id}")
            break
        if missing_captures and not bool(step.get("continue_on_missing_capture")):
            blockers.append(f"required_capture_missing:{step_id}")
            break
    if not blockers:
        # Phase78B: Route through SemanticStateVerifier (with old-assertion fallback)
        semantic_findings = _verify_with_semantic_verifier(
            scenario, snapshots, context, steps, cfg, project_id=None, run_key=run_key)
        if semantic_findings is not None:
            findings.extend(semantic_findings)
        else:
            # Fallback: old Phase76 assertions
            findings.extend(_assertions(
                scenario.get("assertions") or [], snapshots, context,
                str(scenario.get("severity") or "P1")))
    cleanup_plan = scenario.get("cleanup") if isinstance(scenario.get("cleanup"), dict) else {"status": "READY", "strategy": "none", "actions": []}
    def _cleanup_executor(action: dict[str, Any]) -> dict[str, Any]:
        method = str(action.get("method") or "").upper()
        path = str(action.get("path") or "")
        role = str(action.get("role") or cfg.get("default_role") or "")
        headers = _step_headers(cfg, role)
        response = _http(_join_url(base_url, path), method, body=action.get("body"), headers=headers)
        return {"accepted": _accepted(response), "status_code": response.get("status_code"), "path": path, "method": method}

    cleanup_result = execute_cleanup_plan(
        session, cleanup_plan, root=root, executor=_cleanup_executor if writes else None, guard=guard
    )
    if cleanup_result.get("status") != "CLEAN":
        blockers.append("cleanup_failed")
    return {
        "status": "blocked" if blockers else "completed",
        "blockers": blockers,
        "findings": findings,
        "steps": steps,
        "fixture_receipts": fixture_receipts,
        "cleanup": {"run_id": run_key, **cleanup_result},
        "test_data_registry": [record.__dict__ for record in session.records],
        "snapshot_hashes": {name: _hash(_redact(value.get("payload")), 32) for name, value in snapshots.items()},
        "flow_context_keys": sorted(context.get("flow", {}).keys()),
    }


def run_agent_business_flow_pack(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute explicitly mapped flows only after the existing sandbox gate passes."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = dict(options or {})
    pack = compile_agent_business_flow_pack(project, root, options)
    cfg = load_real_project_config(project, root)
    environment_id = str(options.get("environment_id") or options.get("environment") or "test")
    graph = CognitiveMemoryGraph(project, environment_id, root)
    graph_updates: dict[str, Any] = {"cleanup_records": 0, "cleanup_frontier_blocks": 0, "errors": []}
    precheck = execute_document_contracts({"contracts": []}, cfg, options=options)
    receipts: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for flow in pack.get("flows") or []:
        scenario = flow.get("scenario") if isinstance(flow.get("scenario"), dict) else {}
        experiment_id = str(flow.get("experiment_id") or "")
        item_id = str(flow.get("item_id") or "")
        if precheck.get("status") == "blocked" or not bool((precheck.get("safety") or {}).get("safe_to_proceed")):
            result = {"status": "blocked", "blockers": sorted(set(list(precheck.get("blockers") or []) + ["shared_safety_boundary_blocked"])), "findings": [], "steps": [], "fixture_receipts": []}
        elif flow.get("state") in {"BLOCKED_BY_FIXTURE", "BLOCKED_BY_CLEANUP"}:
            result = {"status": "blocked", "blockers": scenario.get("blocking_reasons") or [str(flow.get("state"))], "findings": [], "steps": [], "fixture_receipts": [], "cleanup": {"run_id": "", "status": "CLEANUP_FAILED" if flow.get("state") == "BLOCKED_BY_CLEANUP" else "NOT_APPLICABLE", "evidence_ref": ""}}
        else:
            run_key = f"flow-{_hash([project, experiment_id, _now()], 12)}"
            result = _execute_flow(cfg, scenario, run_key, project_id=project, root=root)
        state = "BLOCKED" if result.get("status") == "blocked" else ("EVIDENCE_CAPTURED" if result.get("findings") else "EXECUTED")
        receipt = record_agent_discovery_experiment_result(project, experiment_id, {
            "status": result.get("status"),
            "flow_id": scenario.get("flow_id"),
            "finding_count": len(result.get("findings") or []),
            "findings": result.get("findings") or [],
            "blockers": result.get("blockers") or [],
            "steps": result.get("steps") or [],
            "fixture_receipts": result.get("fixture_receipts") or [],
            "cleanup": result.get("cleanup") or {},
            "test_data_registry": result.get("test_data_registry") or [],
            "snapshot_hashes": result.get("snapshot_hashes") or {},
        }, state=state, root=root, actor="agent_business_flow_orchestrator")
        receipts.append(receipt)
        result_row = {"flow_id": scenario.get("flow_id"), "experiment_id": experiment_id, **result}
        results.append(result_row)
        try:
            cleanup = result.get("cleanup") or {}
            cleanup_update = graph.record_cleanup(
                run_id=str(cleanup.get("run_id") or f"flow:{experiment_id}"),
                status=str(cleanup.get("status") or "NOT_APPLICABLE"),
                evidence_ref=str(cleanup.get("evidence_ref") or ""),
                target={"api": str(((scenario.get("steps") or [{}])[0] if scenario.get("steps") else {}).get("path") or ""), "risk_type": str(scenario.get("risk_type") or "")},
            )
            graph_updates["cleanup_records"] += 1
            graph_updates["cleanup_frontier_blocks"] += int(cleanup_update.get("updated_frontier_count") or 0)
        except Exception as exc:
            graph_updates["errors"].append(f"cleanup_graph_update:{type(exc).__name__}")
        for finding in result.get("findings") or []:
            evidence = {
                "evidence_strength": "runtime_strong",
                "flow_id": scenario.get("flow_id"),
                "scenario_id": scenario.get("scenario_id"),
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "step_evidence": finding.get("evidence") or {},
                "snapshot_hashes": result.get("snapshot_hashes") or {},
            }
            promoted.append(record_agent_discovery_evidence(project, item_id, evidence, root=root, actor="agent_business_flow_orchestrator"))
    # ── Phase83B: Adversarial Validation Pass ──
    adversarial_report: dict[str, Any] = {"enabled": True, "validated": 0, "rejected": 0}
    try:
        from .business_finding_registry import validate_and_register_findings, register_in_ledger
        all_flow_findings: list[dict[str, Any]] = []
        for res in results:
            for f in (res.get("findings") or []):
                bf = {
                    "finding_id": f.get("hypothesis_id") or f"FLO_{_hash([f.get('title',''), _now()], 12)}",
                    "hypothesis_id": f.get("hypothesis_id", ""),
                    "project_id": project,
                    "verdict": "CANDIDATE",
                    "title": str(f.get("title", ""))[:300],
                    "root_cause_candidate": str(f.get("actual") or f.get("expected", ""))[:500],
                    "entrypoint": {
                        "flow_id": res.get("flow_id", ""),
                        "action_type": f.get("risk_type", "state"),
                        "actor_role": "agent",
                    },
                    "entity_binding": {
                        "entity_alias": res.get("flow_id", "unknown"),
                        "entity_type": (f.get("evidence") or {}).get("kind", "resource"),
                        "entity_id": f.get("hypothesis_id", "UNKNOWN"),
                        "tenant_id": project,
                        "binding_confidence": 0.7,
                    },
                    "before_snapshot_ref": (f.get("evidence") or {}).get("before_snapshot", ""),
                    "action_evidence_ref": f"flow_{res.get('flow_id','')}",
                    "after_snapshot_ref": (f.get("evidence") or {}).get("after_snapshot", ""),
                    "observer_refs": [],
                    "violated_invariant": {
                        "kind": f.get("risk_type", "state"),
                        "definition": str(f.get("expected", ""))[:300],
                        "result": str(f.get("actual", ""))[:300],
                        "evidence_ref": f.get("hypothesis_id", ""),
                    },
                    "reproduction": {
                        "flow_id": res.get("flow_id", ""),
                        "expected_observation": str(f.get("expected", ""))[:300],
                    },
                    "cleanup": {
                        "run_id": str((res.get("cleanup") or {}).get("run_id") or f"flow_{_hash([project, res.get('flow_id','')], 8)}"),
                        "status": str((res.get("cleanup") or {}).get("status") or "NOT_APPLICABLE"),
                        "evidence_ref": str((res.get("cleanup") or {}).get("evidence_ref") or ""),
                    },
                    "adversarial_validation": {"deterministic_result": "", "disprover_result": "NOT_RUN", "counterarguments": []},
                    "evidence_refs": [f.get("hypothesis_id", "")],
                }
                all_flow_findings.append(bf)
        if all_flow_findings:
            rejection_path = str(root / "platform_workspace" / project / "defect_discovery" / "adversarial_rejection_memory.json")
            adv_result = validate_and_register_findings(
                all_flow_findings,
                project_id=project,
                rejection_memory_path=rejection_path,
                enable_llm_disprover=False,  # deterministic only in flow path
                workspace_root=root,
            )
            registered = register_in_ledger(adv_result, project_id=project, root=root)
            adversarial_report = {
                "enabled": True,
                "validated": adv_result["meta"]["validated_candidates"],
                "rejected": adv_result["meta"]["rejected"],
                "needs_evidence": adv_result["meta"]["needs_more_evidence"],
                "blocked": adv_result["meta"]["blocked"],
                "rejection_rate": adv_result["meta"]["rejection_rate"],
                "ledger_registered": registered,
            }
    except Exception:
        adversarial_report = {"enabled": True, "error": "adversarial_pass_failed", "validated": 0}
    report = {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "execution": {
            "status": "blocked" if precheck.get("status") == "blocked" or not bool((precheck.get("safety") or {}).get("safe_to_proceed")) else "completed",
            "blockers": precheck.get("blockers") or [],
            "executed_flow_count": sum(1 for row in results if row.get("status") == "completed"),
            "evidence_capture_count": len(promoted),
        },
        "adversarial_validation": adversarial_report,
        "results": results,
        "receipt_count": len(receipts),
        "cognitive_graph": {**graph_updates, "stats": graph.stats(), "mode": "shadow", "cleanup_updates_do_not_auto_confirm_findings": True},
        "governance": {
            "delegates_safety_to_document_contract_executor": True,
            "direct_delete_cleanup_not_supported": True,
            "explicit_compensation_or_restore_supported": True,
            "runtime_evidence_still_requires_human_verdict": True,
            "does_not_auto_confirm_or_create_regression_guard": True,
            "unknown_bug_total_not_used": True,
        },
    }
    out = _output_dir(project, root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "business_flow_execution_receipt.json").write_text(json.dumps(_redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def load_agent_business_flow_receipts(project_id: str = "real_project_demo", root: Path | None = None) -> list[dict[str, Any]]:
    """Return canonical experiment records; flow receipts are not a parallel store."""
    return [row for row in load_agent_discovery_experiments(project_id, root) if row.get("experiment_type") == "multi_step_business_flow"]
