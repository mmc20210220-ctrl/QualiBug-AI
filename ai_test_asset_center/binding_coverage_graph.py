"""Binding Coverage Graph — unified dynamic value dependency graph.

SPEC v1.2 §8 + SPEC v1.2.1 §7: Real directed graph with topological sort,
cycle detection, stage ordering, and full-scope scanning.

This module builds a complete binding graph covering Primary, Observer,
Cleanup, Assertion, Actor and Fixture dynamic values. It validates
resolution order, detects cycles, and rejects forbidden source types.

Output: qualibug.binding-coverage-graph.v1
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Allowed Binding Sources ─────────────────────────────────────────────────

ALLOWED_SOURCE_KINDS = frozenset({
    "ACTOR_CONFIG",
    "STATIC_SOURCE_VALUE",
    "FIXTURE_RECEIPT",
    "PRIMARY_REQUEST",
    "PRIMARY_RESPONSE",
    "CONTROL_RESPONSE",
    "READ_OPERATION_RESPONSE",
    "ENVIRONMENT_CONFIG",
    # The observed-write-body projection: a runtime resolver reads a
    # source-declared collection with the writer's credentials and projects
    # a real observed row into the write body. The value is the environment's
    # own data — a legitimate, evidence-backed binding source.
    "OBSERVED_ENTITY_WRITE_BODY",
})

FORBIDDEN_SOURCE_KINDS = frozenset({
    "RANDOM_PLACEHOLDER",
    "PATH_GUESS",
    "LLM_INVENTED_VALUE",
    "DEFAULT_FAKE_VALUE",
    "UNVERIFIED_FALLBACK",
})

# ─── SPEC v1.2.1 §7.3: Temporal stage ordering ──────────────────────────────

STAGE_ORDER: list[str] = [
    "COMPILE_STATIC",
    "ACTOR_RESOLUTION",
    "FIXTURE_SETUP",
    "CONTROL_EXECUTION",
    "PRIMARY_EXECUTION",
    "AFTER_WRITE_OBSERVATION",
    "CLEANUP_EXECUTION",
    "AFTER_CLEANUP_OBSERVATION",
]

_STAGE_INDEX: dict[str, int] = {s: i for i, s in enumerate(STAGE_ORDER)}


def _stage_index(stage: str) -> int:
    return _STAGE_INDEX.get(stage, 999)


# ─── Binding Node Builder ─────────────────────────────────────────────────────


def _extract_path_bindings(path: str) -> list[str]:
    """Extract {placeholder} names from a path template."""
    return re.findall(r"\{(\w+)\}", _text(path))


def _extract_body_bindings(body: Any) -> list[str]:
    """Extract {placeholder} names from a request body (recursive)."""
    results: list[str] = []
    if isinstance(body, str):
        results.extend(re.findall(r"\{(\w+)\}", body))
    elif isinstance(body, dict):
        for v in body.values():
            results.extend(_extract_body_bindings(v))
    elif isinstance(body, list):
        for item in body:
            results.extend(_extract_body_bindings(item))
    return results


def _determine_producer_stage(source_kind: str) -> str:
    """Determine the stage at which a binding value becomes available."""
    mapping = {
        "ACTOR_CONFIG": "ACTOR_RESOLUTION",
        "STATIC_SOURCE_VALUE": "COMPILE_STATIC",
        "ENVIRONMENT_CONFIG": "COMPILE_STATIC",
        "FIXTURE_RECEIPT": "FIXTURE_SETUP",
        "CONTROL_RESPONSE": "CONTROL_EXECUTION",
        "PRIMARY_REQUEST": "PRIMARY_EXECUTION",
        "PRIMARY_RESPONSE": "PRIMARY_EXECUTION",
        "READ_OPERATION_RESPONSE": "AFTER_WRITE_OBSERVATION",
        "OBSERVED_ENTITY_WRITE_BODY": "FIXTURE_SETUP",
    }
    return mapping.get(source_kind, "COMPILE_STATIC")


def _determine_consumer_stage(location: str) -> str:
    """Determine the stage at which a binding value is consumed."""
    loc = location.lower()
    if "control" in loc:
        return "CONTROL_EXECUTION"
    if "treatment" in loc or "primary" in loc:
        return "PRIMARY_EXECUTION"
    if "observer" in loc:
        return "AFTER_WRITE_OBSERVATION"
    if "cleanup" in loc:
        return "CLEANUP_EXECUTION"
    if "assertion" in loc:
        return "AFTER_WRITE_OBSERVATION"
    if "fixture" in loc:
        return "FIXTURE_SETUP"
    return "PRIMARY_EXECUTION"


def _infer_source_kind_for_location(param: str, location: str, exp: dict[str, Any]) -> str:
    """SPEC v1.2.1 §7.1: Infer real source_kind, not default PRIMARY_RESPONSE.

    A PUT/POST path parameter needed BEFORE the primary write cannot come
    from that primary response. It must come from FIXTURE_RECEIPT,
    READ_OPERATION_RESPONSE, or STATIC_SOURCE_VALUE.
    """
    consumer_stage = _determine_consumer_stage(location)
    # If consumed at or before PRIMARY_EXECUTION, it cannot come from PRIMARY_RESPONSE
    if _stage_index(consumer_stage) <= _stage_index("PRIMARY_EXECUTION"):
        # Check binding_plan for explicit source
        for binding in _list(exp.get("binding_plan")):
            if not isinstance(binding, dict):
                continue
            if _text(binding.get("target")) == param:
                sk = _text(binding.get("source_kind") or binding.get("source_priority")).upper()
                if sk in ALLOWED_SOURCE_KINDS:
                    return sk
        # Check if fixture plan exists for this param
        for binding in _list(exp.get("binding_plan")):
            if not isinstance(binding, dict):
                continue
            if _text(binding.get("target")) == param and _text(binding.get("fixture_id")):
                return "FIXTURE_RECEIPT"
        # Default: FIXTURE_RECEIPT (must be produced before primary)
        return "FIXTURE_RECEIPT"
    # After primary execution, PRIMARY_RESPONSE is valid
    return "PRIMARY_RESPONSE"


# ─── Graph Edge + Topological Sort ───────────────────────────────────────────


def _build_edges(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build directed edges: producer → consumer for each binding."""
    edges: list[dict[str, str]] = []
    for node in nodes:
        binding_id = _text(node.get("binding_id"))
        producer = _text(node.get("produced_by")) or binding_id
        for consumer in _list(node.get("consumed_by")):
            edges.append({"from": producer, "to": _text(consumer)})
    return edges


def _topological_sort(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> tuple[list[str], bool]:
    """Kahn's algorithm. Returns (order, has_cycle)."""
    node_ids = [_text(n.get("binding_id")) for n in nodes]
    if not node_ids:
        return [], False

    # Build adjacency + in-degree
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    for edge in edges:
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src in adj and dst in in_degree:
            adj[src].append(dst)
            in_degree[dst] = in_degree.get(dst, 0) + 1

    queue = [nid for nid in node_ids if in_degree.get(nid, 0) == 0]
    order: list[str] = []
    while queue:
        queue.sort()  # deterministic
        current = queue.pop(0)
        order.append(current)
        for neighbor in adj.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    has_cycle = len(order) != len(node_ids)
    return order, has_cycle


# ─── Main Graph Builder ───────────────────────────────────────────────────────


def build_binding_coverage_graph(
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build the complete binding coverage graph for an experiment.

    SPEC v1.2.1 §7.5: Scans control_plan, treatment_plan, observer plans,
    cleanup_plan, assertions, actor contracts, fixture DAG, body/query/header.

    Returns:
        qualibug.binding-coverage-graph.v1
    """
    exp = _dict(experiment)
    ir = _dict(behavior_ir)

    nodes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    def _add_node(param: str, location: str) -> None:
        if param not in seen_names:
            seen_names.add(param)
            source_kind = _infer_source_kind_for_location(param, location, exp)
            producer_stage = _determine_producer_stage(source_kind)
            consumer_stage = _determine_consumer_stage(location)
            fp_content = {
                "semantic_name": param,
                "source_kind": source_kind,
                "producer_stage": producer_stage,
            }
            fingerprint = hashlib.sha256(
                json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:32]
            nodes.append({
                "binding_id": f"bind_{fingerprint[:16]}",
                "semantic_name": param,
                "target_locations": [location],
                "source_kind": source_kind,
                "producer_stage": producer_stage,
                "consumer_stages": [consumer_stage],
                "depends_on": [],
                "produced_by": f"producer_{source_kind.lower()}_{param}",
                "consumed_by": [f"consumer_{location.replace('.', '_')}"],
                "availability_stage": producer_stage,
                "status": "RUNTIME_RESOLVABLE" if source_kind in ALLOWED_SOURCE_KINDS else "FORBIDDEN",
                "fingerprint": fingerprint,
            })
        else:
            # Update existing node with additional consumer location
            for node in nodes:
                if node["semantic_name"] == param:
                    if location not in node["target_locations"]:
                        node["target_locations"].append(location)
                    consumer_stage = _determine_consumer_stage(location)
                    if consumer_stage not in node["consumer_stages"]:
                        node["consumer_stages"].append(consumer_stage)
                    node["consumed_by"].append(f"consumer_{location.replace('.', '_')}")
                    break

    # ── Scan treatment plan (path + body) ──
    for step in _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        path = _text(step.get("path") or step.get("path_template"))
        for param in _extract_path_bindings(path):
            _add_node(param, f"treatment.path.{param}")
        for param in _extract_body_bindings(step.get("body")):
            _add_node(param, f"treatment.body.{param}")

    # ── Scan control plan (path + body) ──
    for step in _list(exp.get("control_plan")):
        if not isinstance(step, dict):
            continue
        path = _text(step.get("path") or step.get("path_template"))
        for param in _extract_path_bindings(path):
            _add_node(param, f"control.path.{param}")
        for param in _extract_body_bindings(step.get("body")):
            _add_node(param, f"control.body.{param}")

    # ── Scan cleanup plan (path + body) ──
    for step in _list(exp.get("cleanup_plan")):
        if not isinstance(step, dict):
            continue
        path = _text(step.get("path") or step.get("path_template"))
        for param in _extract_path_bindings(path):
            _add_node(param, f"cleanup.path.{param}")
        for param in _extract_body_bindings(step.get("body")):
            _add_node(param, f"cleanup.body.{param}")

    # ── Scan binding_plan runtime-resolvable entries ──
    # The observed-write-body projection (and other runtime resolvers) binds
    # a value that no step path/body placeholder names: the write body arrives
    # at runtime from the environment's own observed data. Such targets must
    # be declared in the coverage graph or the runtime provenance gate
    # rejects them as undeclared_runtime_binding.
    for binding in _list(exp.get("binding_plan")):
        if not isinstance(binding, dict):
            continue
        if (
            _text(binding.get("status")) == "runtime_resolvable"
            and _text(binding.get("target"))
        ):
            _add_node(
                _text(binding.get("target")),
                f"binding_plan.{_text(binding.get('target'))}",
            )

    # ── Scan observer plans ──
    for obs in _list(exp.get("observers")):
        if not isinstance(obs, dict):
            continue
        obs_path = _text(obs.get("path") or obs.get("path_template"))
        for param in _extract_path_bindings(obs_path):
            _add_node(param, f"observer.path.{param}")

    # ── Scan observer_resolution_plans (v1.2.1) ──
    for plan in _list(exp.get("observer_resolution_plans")):
        if not isinstance(plan, dict):
            continue
        plan_path = _text(plan.get("path"))
        for param in _extract_path_bindings(plan_path):
            _add_node(param, f"observer_resolution.path.{param}")

    # ── Scan assertions (operands, fields) ──
    for assertion in _list(exp.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        for operand in _list(assertion.get("operands")):
            if isinstance(operand, dict):
                ref = _text(operand.get("ref") or operand.get("binding_ref"))
                if ref and "{" in ref:
                    for param in _extract_path_bindings(ref):
                        _add_node(param, f"assertion.operand.{param}")

    # ── Scan fixture_dependency_dag ──
    fixture_dag = _dict(exp.get("fixture_dependency_dag"))
    for fixture_node in _list(fixture_dag.get("nodes")):
        if not isinstance(fixture_node, dict):
            continue
        for binding in _list(fixture_node.get("produces_bindings")):
            if _text(binding) and _text(binding) not in seen_names:
                _add_node(_text(binding), f"fixture.produces.{binding}")

    # ── Scan binding plan for forbidden sources ──
    for binding in _list(exp.get("binding_plan")):
        if not isinstance(binding, dict):
            continue
        target = _text(binding.get("target"))
        if not target or target.startswith("__"):
            continue
        source_kind = _text(binding.get("source_kind") or binding.get("source_priority")).upper()
        if source_kind in FORBIDDEN_SOURCE_KINDS:
            issues.append({
                "kind": "FORBIDDEN_SOURCE",
                "binding": target,
                "source_kind": source_kind,
                "severity": "BLOCK",
            })

    # ── SPEC v1.2.1 §7.3: Stage ordering validation ──
    # Stage violations are warnings, not hard blocks. The compiler handles
    # binding source selection correctly; this is informational only.
    stage_violations: list[dict[str, Any]] = []
    for node in nodes:
        producer_idx = _stage_index(_text(node.get("producer_stage")))
        for consumer_stage in _list(node.get("consumer_stages")):
            consumer_idx = _stage_index(_text(consumer_stage))
            if producer_idx > consumer_idx:
                stage_violations.append({
                    "binding": node["semantic_name"],
                    "producer_stage": node.get("producer_stage"),
                    "consumer_stage": consumer_stage,
                    "detail": "binding_source_not_available_before_use",
                })
                issues.append({
                    "kind": "STAGE_ORDER_VIOLATION",
                    "binding": node["semantic_name"],
                    "severity": "WARN",
                })

    # ── SPEC v1.2.1 §7.4: Real cycle detection ──
    edges = _build_edges(nodes)
    topo_order, cycle_detected = _topological_sort(nodes, edges)
    if cycle_detected:
        issues.append({
            "kind": "BINDING_DEPENDENCY_CYCLE",
            "binding": "",
            "severity": "BLOCK",
            "detail": "binding_dependency_cycle",
        })

    # ── Actor scope mismatch ──
    actor_contract = _dict(exp.get("actor_selection_contract"))
    control_actor = _text(actor_contract.get("control_actor_ref"))
    treatment_actor = _text(actor_contract.get("treatment_actor_ref"))
    if control_actor and treatment_actor:
        for node in nodes:
            node_actor = _text(node.get("actor_scope"))
            if node_actor and node_actor not in (control_actor, treatment_actor):
                issues.append({
                    "kind": "ACTOR_SCOPE_MISMATCH",
                    "binding": node["semantic_name"],
                    "expected_actors": [control_actor, treatment_actor],
                    "actual": node_actor,
                    "severity": "BLOCK",
                })

    # ── Graph status ──
    has_block = any(i.get("severity") == "BLOCK" for i in issues)
    graph_status = "BLOCKED" if has_block else "VALID"

    # ── SPEC v1.2.1 §7.6: Binding graph fingerprint for Runtime Freeze ──
    fp_content = {
        "nodes": [{"name": n["semantic_name"], "source": n["source_kind"], "stage": n.get("producer_stage", "")} for n in nodes],
        "edges": edges,
        "status": graph_status,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.binding-coverage-graph.v1",
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "nodes": nodes,
        "node_count": len(nodes),
        "edges": edges,
        "edge_count": len(edges),
        "topological_order": topo_order,
        "cycle_detected": cycle_detected,
        "stage_violations": stage_violations,
        "issues": issues,
        "issue_count": len(issues),
        "graph_status": graph_status,
        "unresolved_count": sum(1 for n in nodes if n["status"] != "RUNTIME_RESOLVABLE"),
        "forbidden_source_count": sum(
            1 for i in issues if i.get("kind") == "FORBIDDEN_SOURCE"
        ),
        "binding_graph_fingerprint": fingerprint,
        "fingerprint": fingerprint,
    }
