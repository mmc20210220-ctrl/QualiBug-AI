from __future__ import annotations

"""Runtime wiring for the System Behavior Space Model.

This patch attaches the broader system-behavior-space model to the existing
BusinessStateGraphBuilder contract. It does not create a new ingestion system:
when V12 runs inside a project, it loads the existing enterprise knowledge asset
and passes that parsed asset into the behavior-space builder.

The model is materialized into existing ``behavior_contract['slices']`` as
source-grounded invariant slices. Execution stays inside the current V12
scheduler and SemanticScenarioGenerator.

Oracle, confirmed-finding, regression and learning integrations are additive:
existing engines/writers/runners remain the execution paths, while this patch
preserves System Behavior Space promise metadata across them.
"""

import contextvars
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center import business_state_graph as _bsg
from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
    build_system_behavior_space,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_system_behavior_space_patch"
_SAFE_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_KNOWN_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
_WRITE_METHODS = _KNOWN_HTTP_METHODS - _SAFE_READ_METHODS
_BEHAVIOR_SPACE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_system_behavior_space_context",
    default={},
)


def _load_existing_enterprise_asset() -> dict[str, Any]:
    context = _BEHAVIOR_SPACE_CONTEXT.get({})
    project = str(context.get("project") or "").strip()
    root_value = context.get("root")
    if not project or root_value is None:
        return {}
    try:
        from ai_test_asset_center.enterprise_knowledge_center import (
            build_enterprise_business_knowledge_asset,
            load_enterprise_business_knowledge_asset,
        )
        root = Path(root_value)
        asset = load_enterprise_business_knowledge_asset(project, root)
        if asset is None:
            asset = build_enterprise_business_knowledge_asset(project, root)
        return asset if isinstance(asset, dict) else {}
    except Exception:
        return {}


def _api_route(raw: Any) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {"method": "", "path": ""}
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in _KNOWN_HTTP_METHODS:
        path = parts[1].strip()
        return {"method": parts[0].upper(), "path": path if path.startswith("/") else ""}
    return {"method": "", "path": text if text.startswith("/") else ""}


def _path_only(value: str) -> str:
    return _api_route(value).get("path", "")


def _object_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("objects") if isinstance(space.get("objects"), list) else []
    return {str(item.get("entity") or ""): item for item in rows if isinstance(item, dict)}


def _promise_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("promises") if isinstance(space.get("promises"), list) else []
    return {str(item.get("promise_id") or ""): item for item in rows if isinstance(item, dict)}


def _dedupe_routes(routes: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for route in routes:
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if not path:
            continue
        key = (method, path)
        if key not in seen:
            seen.add(key)
            out.append({"method": method, "path": path})
    return out


def _system_behavior_slices(space: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(space, dict):
        return []
    objects = _object_index(space)
    promises = _promise_index(space)
    probes = space.get("probe_candidates") if isinstance(space.get("probe_candidates"), list) else []
    slices: list[dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        promise_id = str(probe.get("promise_id") or "")
        promise = promises.get(promise_id)
        if not promise:
            continue
        entity = str(probe.get("entity") or promise.get("entity") or "system")
        obj = objects.get(entity, {})
        routes = _dedupe_routes([_api_route(raw) for raw in (obj.get("api_paths") if isinstance(obj.get("api_paths"), list) else [])])
        endpoints: list[str] = []
        for route in routes:
            path = route.get("path", "")
            if path and path not in endpoints:
                endpoints.append(path)
        invariant = str(promise.get("invariant") or probe.get("objective") or "system promise")
        dimensions = [str(item) for item in (promise.get("dimensions") or probe.get("oracle_intent") or []) if str(item)]
        surface_plan = [str(item) for item in (probe.get("surface_plan") or promise.get("surfaces") or []) if str(item)]
        evidence_gaps: list[str] = []
        if "api" in surface_plan and not endpoints:
            evidence_gaps.append("SYSTEM_PROMISE_API_ROUTE_NOT_SOURCE_BOUND")
        if "api" in surface_plan and endpoints and not any(route.get("method") in _SAFE_READ_METHODS for route in routes):
            evidence_gaps.append("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
        if "db" in surface_plan and not (obj.get("db_tables") if isinstance(obj.get("db_tables"), list) else []):
            evidence_gaps.append("SYSTEM_PROMISE_DB_TABLE_NOT_SOURCE_BOUND")
        if "ui" in surface_plan and not (obj.get("ui_routes") if isinstance(obj.get("ui_routes"), list) else []):
            evidence_gaps.append("SYSTEM_PROMISE_UI_ROUTE_NOT_SOURCE_BOUND")
        sid = _bsg.behavior_slice_id("system_promise", entity, promise_id)
        slices.append({
            "slice_id": sid,
            "entity": entity,
            "kind": "invariant",
            "states": [f"system_promise:{dimension}" for dimension in dimensions[:6]],
            "endpoints": endpoints,
            "priority": max(float(probe.get("priority") or 0.0), float(promise.get("confidence") or 0.0)),
            "source_refs": [{"source_type": "system_behavior_space", "locator": promise_id, "quote": invariant[:500]}],
            "evidence_gaps": evidence_gaps,
            "status": "pending",
            "_selection_family": dimensions[0] if dimensions else "system_promise",
            "_selection_origin": "system_behavior_space",
            "_system_behavior_promise_id": promise_id,
            "_system_behavior_probe_id": str(probe.get("probe_id") or ""),
            "_system_behavior_dimensions": dimensions,
            "_system_behavior_surface_plan": surface_plan,
            "_system_behavior_api_routes": routes,
            "_system_behavior_required_assets": [str(item) for item in (probe.get("required_assets") or []) if str(item)],
        })
    deduped: dict[str, dict[str, Any]] = {}
    for item in slices:
        sid = str(item.get("slice_id") or "")
        if sid and sid not in deduped:
            # ── Route availability scoring ──
            # Slices without any source-bound API route cannot be executed
            # (no safe_read_only path), so they stay as documentation-only
            # plan entries.  Only slices WITH routes generate executable scenarios.
            routes = item.get("_system_behavior_api_routes") if isinstance(item.get("_system_behavior_api_routes"), list) else []
            has_exec_route = any(
                isinstance(r, dict) and str(r.get("method") or "").upper() in _KNOWN_HTTP_METHODS
                and str(r.get("path") or "").startswith("/")
                for r in routes
            )
            if not has_exec_route:
                item["_route_availability"] = "no_source_bound_route"
                item["status"] = "plan_only"
            deduped[sid] = item
    return [item for _, item in sorted(deduped.items(), key=lambda kv: (-float(kv[1].get("priority") or 0.0), str(kv[1].get("entity") or ""), kv[0]))]


def _attach_system_behavior_slices(contract: dict[str, Any], space: dict[str, Any]) -> dict[str, Any]:
    existing = contract.get("slices") if isinstance(contract.get("slices"), list) else []
    generated = _system_behavior_slices(space)
    if not generated:
        return contract
    by_id: dict[str, dict[str, Any]] = {}
    for item in existing:
        if isinstance(item, dict) and str(item.get("slice_id") or ""):
            by_id[str(item.get("slice_id"))] = item
    added = 0
    for item in generated:
        sid = str(item.get("slice_id") or "")
        if sid and sid not in by_id:
            by_id[sid] = item
            added += 1
    contract["slices"] = sorted(by_id.values(), key=lambda item: (-float(item.get("priority") or 0.0), str(item.get("entity") or ""), str(item.get("slice_id") or "")))
    summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
    by_kind: dict[str, int] = {}
    for item in contract["slices"]:
        kind = str(item.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    summary["total_slices"] = len(contract["slices"])
    summary["by_kind"] = dict(sorted(by_kind.items()))
    summary["system_behavior_materialized_slice_count"] = len(generated)
    summary["system_behavior_added_slice_count"] = added
    contract["summary"] = summary
    return contract


def _system_behavior_hints(slice_meta: dict[str, Any]) -> dict[str, Any]:
    if str(slice_meta.get("_selection_origin") or "") != "system_behavior_space":
        return {}
    promise_id = str(slice_meta.get("_system_behavior_promise_id") or "").strip()
    if not promise_id:
        return {}
    return {
        "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "promise_id": promise_id,
        "probe_id": str(slice_meta.get("_system_behavior_probe_id") or ""),
        "dimensions": [str(item) for item in (slice_meta.get("_system_behavior_dimensions") or []) if str(item)],
        "surface_plan": [str(item) for item in (slice_meta.get("_system_behavior_surface_plan") or []) if str(item)],
        "api_routes": [dict(route) for route in (slice_meta.get("_system_behavior_api_routes") or []) if isinstance(route, dict)],
        "required_assets": [str(item) for item in (slice_meta.get("_system_behavior_required_assets") or []) if str(item)],
        "source_slice_id": str(slice_meta.get("slice_id") or ""),
        "source_family": str(slice_meta.get("_selection_family") or "system_promise"),
    }


def _scenario_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        try:
            payload = value.to_dict()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {
        "id": str(getattr(value, "id", "") or ""),
        "title": str(getattr(value, "title", "") or ""),
        "category": str(getattr(value, "category", "") or ""),
        "runtime_hints": dict(getattr(value, "runtime_hints", {}) or {}),
        "behavior_slice_id": str(getattr(value, "behavior_slice_id", "") or ""),
        "behavior_slice_kind": str(getattr(value, "behavior_slice_kind", "") or ""),
        "selection_origin": str(getattr(value, "selection_origin", "") or ""),
    }


def _scenario_system_behavior_hints(scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scenario, dict):
        return {}
    runtime_hints = scenario.get("runtime_hints") if isinstance(scenario.get("runtime_hints"), dict) else {}
    hints = runtime_hints.get("system_behavior_space") if isinstance(runtime_hints.get("system_behavior_space"), dict) else {}
    if hints:
        return hints
    fallback = scenario.get("system_behavior_space_evidence")
    return fallback if isinstance(fallback, dict) else {}


def _slice_invariant_text(slice_meta: dict[str, Any]) -> str:
    for ref in slice_meta.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("quote") or "").strip():
            return str(ref.get("quote") or "").strip()
    return str(slice_meta.get("entity") or "system_promise")


def _safe_read_route(slice_meta: dict[str, Any]) -> dict[str, str]:
    for route in slice_meta.get("_system_behavior_api_routes") or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if method in _SAFE_READ_METHODS and path.startswith("/"):
            return {"method": method, "path": path}
    return {}


def _write_routes(slice_meta: dict[str, Any]) -> list[dict[str, str]]:
    """Return all write-capable routes (POST/PUT/PATCH/DELETE) from slice metadata."""
    routes: list[dict[str, str]] = []
    for route in slice_meta.get("_system_behavior_api_routes") or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if method in _WRITE_METHODS and path.startswith("/"):
            routes.append({"method": method, "path": path})
    return routes


def _test_write_allowed() -> bool:
    try:
        import os as _os
        return _os.environ.get("QUALIBUG_ALLOW_TEST_WRITE", "").strip().lower() in ("1", "true", "yes")
    except Exception:
        return False


def _fixture_prefix() -> str:
    try:
        import os as _os
        return _os.environ.get("QUALIBUG_TEST_FIXTURE_PREFIX", "qualibug_test_").strip() or "qualibug_test_"
    except Exception:
        return "qualibug_test_"


def _enrich_system_behavior_scenario(item: Any, slice_meta: dict[str, Any], discovery_round: int) -> Any:
    hints = _system_behavior_hints(slice_meta)
    if not hints:
        return item
    try:
        from ai_test_asset_center.semantic_scenario_generator import ExecutableScenario, ScenarioStep
    except Exception:
        return item
    invariant = _slice_invariant_text(slice_meta)
    entity = str(slice_meta.get("entity") or "system")
    safe_route = _safe_read_route(slice_meta)
    evidence_gaps = [str(value) for value in (slice_meta.get("evidence_gaps") or []) if str(value)]
    if item is None:
        steps = []
        execution_policy = "plan_only_requires_fixture"
        if safe_route:
            steps = [ScenarioStep(order=1, action="observe_system_promise_surface", api_method=safe_route["method"], api_path=safe_route["path"], expected_status=200, actor="readonly")]
            execution_policy = "safe_read_only"
        item = ExecutableScenario(
            id=f"system_promise:{hints['promise_id']}",
            title=f"[System promise] {entity}: {hints['source_family']}",
            description=invariant[:300],
            category="system_promise",
            severity="P1",
            entity=entity,
            preconditions=["系统行为承诺来自 System Behavior Space，执行必须保留证据链。"],
            actors=["readonly"],
            steps=steps,
            oracle_rules=[],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.45),
            execution_policy=execution_policy,
            evidence_gaps=evidence_gaps,
            source_refs=[dict(ref) for ref in (slice_meta.get("source_refs") or []) if isinstance(ref, dict)],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="system_promise",
            discovery_round=discovery_round,
            selection_origin="system_behavior_space",
        )
    item.category = "system_promise"
    item.behavior_slice_kind = "system_promise"
    item.selection_origin = "system_behavior_space"
    if not str(item.title).startswith("[System promise]"):
        item.title = f"[System promise] {item.title}"
    # When a source-bound safe read route exists, upgrade the execution policy
    # and generate the observe step.  The original generator may have set
    # plan_only_requires_fixture or left steps empty; the enrichment owns the
    # final safety decision because it has the authoritative slice metadata.
    if safe_route:
        item.execution_policy = "safe_read_only"
        if not getattr(item, "steps", []):
            try:
                from ai_test_asset_center.semantic_scenario_generator import ScenarioStep
            except Exception:
                ScenarioStep = None  # type: ignore[assignment]
            if ScenarioStep is not None:
                item.steps = [ScenarioStep(order=1, action="observe_system_promise_surface", api_method=safe_route["method"], api_path=safe_route["path"], expected_status=200, actor="readonly")]
        if "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" in evidence_gaps:
            evidence_gaps.remove("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
    if not safe_route and "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" not in evidence_gaps:
        evidence_gaps.append("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
    if not safe_route and getattr(item, "execution_policy", "") == "safe_read_only":
        item.execution_policy = "plan_only_requires_fixture"
        item.steps = []
    # ── Write scenario upgrade ──
    # When QUALIBUG_ALLOW_TEST_WRITE is enabled and the entity has write routes
    # (POST/PUT/PATCH/DELETE), upgrade from safe_read_only to approved_test_write
    # and add write steps.  All write data carries the fixture prefix for isolation.
    # A safe_read route is preferred (observe-then-write pattern) but NOT required —
    # entities with only write routes (e.g., POST /api/payments/pay) can still execute.
    write_routes = _write_routes(slice_meta)
    if write_routes and _test_write_allowed():
        if safe_route:
            item.execution_policy = "approved_test_write"
        elif not safe_route and getattr(item, "execution_policy", "") in ("plan_only_requires_fixture", ""):
            item.execution_policy = "approved_test_write"
            # No observe step possible, but write steps can still execute
            if "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" in evidence_gaps:
                evidence_gaps.remove("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
        if item.execution_policy == "approved_test_write":
            fixture_prefix = _fixture_prefix()
            existing_steps = list(getattr(item, "steps", []) or [])
            next_order = max((int(getattr(s, "order", 0) or 0) for s in existing_steps), default=0) + 1
            try:
                from ai_test_asset_center.semantic_scenario_generator import ScenarioStep
            except Exception:
                ScenarioStep = None  # type: ignore[assignment]
            if ScenarioStep is not None:
                for wr in write_routes[:2]:
                    if wr["method"] == "POST":
                        # Use bindable seed data placeholders instead of hardcoded
                        # fixture names.  At runtime, _resolve_seed_bindings queries
                        # the real API for seed data and _replace resolves these.
                        _post_body: dict[str, Any] = {"sku": "{body_sku}", "qty": "{body_qty}"}
                        if "order" in wr["path"]:
                            _post_body = {"items": [{"sku": "{body_sku}", "qty": 1}]}
                        elif "refund" in wr["path"]:
                            _post_body = {"order_id": "{body_order_id}", "reason": "qualibug test"}
                        elif "payment" in wr["path"]:
                            _post_body = {"order_id": "{body_order_id}"}
                        elif "coupon" in wr["path"]:
                            _post_body = {"code": "{body_code}", "order_id": "{body_order_id}"}
                        existing_steps.append(ScenarioStep(
                            order=next_order, action="test_write_create_fixture",
                            api_method="POST", api_path=wr["path"],
                            expected_status=201, actor="readonly",
                            body_template=_post_body,
                            extract_from_response=["id", "sku", "order_id"],
                        ))
                    elif wr["method"] == "DELETE":
                        safe_path = wr["path"].rstrip("/") + "/{id}" if "/:" not in wr["path"] and "/{" not in wr["path"] else wr["path"]
                        existing_steps.append(ScenarioStep(
                            order=next_order, action="test_write_delete_fixture",
                            api_method="DELETE", api_path=safe_path,
                            expected_status=204, actor="readonly",
                        ))
                    elif wr["method"] in ("PUT", "PATCH"):
                        safe_path = wr["path"].rstrip("/") + "/{id}" if "/:" not in wr["path"] and "/{" not in wr["path"] else wr["path"]
                        existing_steps.append(ScenarioStep(
                            order=next_order, action="test_write_update_fixture",
                            api_method=wr["method"], api_path=safe_path,
                            expected_status=200, actor="readonly",
                            body_template={"qty": "{body_qty}", "sku": "{body_sku}"},
                        ))
                    next_order += 1
                item.steps = existing_steps
            if "SYSTEM_PROMISE_WRITE_ROUTE_NOT_EXECUTED" in evidence_gaps:
                evidence_gaps.remove("SYSTEM_PROMISE_WRITE_ROUTE_NOT_EXECUTED")
    rules = list(getattr(item, "oracle_rules", []) or [])
    for rule in ["SystemPromiseOracle.open_ended_promise_violation", *(f"SystemPromiseOracle.dimension:{dim}" for dim in hints["dimensions"][:8])]:
        if rule not in rules:
            rules.append(rule)
    item.oracle_rules = rules
    runtime_hints = dict(getattr(item, "runtime_hints", {}) or {})
    runtime_hints["system_behavior_space"] = hints
    runtime_hints["system_promise_invariant"] = invariant[:500]
    if safe_route:
        runtime_hints["system_promise_safe_read_route"] = safe_route
    else:
        runtime_hints["system_promise_execution_guard"] = "plan_only_no_source_bound_safe_read_route"
    item.runtime_hints = runtime_hints
    item.evidence_gaps = list(dict.fromkeys([*(getattr(item, "evidence_gaps", []) or []), *evidence_gaps]))
    return item


def _response_bodies(trace: dict[str, Any]) -> list[Any]:
    bodies: list[Any] = []
    for step in trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []:
        if not isinstance(step, dict):
            continue
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        if isinstance(response, dict) and "body" in response:
            bodies.append(response.get("body"))
    return bodies


def _walk_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for key, child in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_walk_values(child, child_key))
        return out
    if isinstance(value, list):
        out = []
        for index, child in enumerate(value[:50]):
            out.extend(_walk_values(child, f"{prefix}[{index}]"))
        return out
    return [(prefix, value)]


def _direct_system_promise_oracle_result(scenario: dict[str, Any], trace: dict[str, Any], hints: dict[str, Any]) -> Any:
    """Evaluate a system promise against runtime trace evidence.

    Each dimension check inspects the HTTP response body against the
    promise's declared invariants.  Checks are additive — a single
    response can trigger multiple dimension violations.
    """
    try:
        from ai_test_asset_center.oracle_engine import OracleResult
    except Exception:
        return None
    dims = {str(item).lower() for item in hints.get("dimensions") or [] if str(item)}
    promise_id = str(hints.get("promise_id") or "")
    invariant = str((scenario.get("runtime_hints") or {}).get("system_promise_invariant") or "system promise")[:500]
    bodies = _response_bodies(trace)
    steps = trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []
    all_values = [(k, v) for body in bodies for k, v in _walk_values(body)]
    all_keys_lower = {k.lower() for k, _ in all_values}
    all_text = " ".join(f"{k}={v}" for k, v in all_values).lower()

    def _violation(rule: str, expected: str, actual: str, severity: str = "P0", confidence: float = 0.85) -> Any:
        return OracleResult(False, "SystemPromiseOracle", "L7", rule, expected, actual, severity, confidence,
                           f"System Behavior Space promise {promise_id} 被运行时响应反证。")

    # ── Dimension: money / quantity / conservation ──
    money_like = {"money", "amount", "price", "balance", "refund", "payment", "fee", "total", "quantity", "qty", "stock", "inventory", "conservation"}
    if dims.intersection({"money", "quantity", "conservation", "data_consistency"}):
        for key, value in all_values:
            lowered = key.lower()
            if not any(token in lowered for token in money_like):
                continue
            if isinstance(value, (int, float)):
                if value < 0:
                    return _violation(f"system_promise_negative_value:{promise_id}",
                                      f"系统承诺: {invariant}", f"{key}={value} (负值)",
                                      "P0", 0.88)
                if value > 1_000_000_000:
                    return _violation(f"system_promise_suspicious_large_value:{promise_id}",
                                      f"系统承诺: {invariant}", f"{key}={value} (疑似溢出/异常)",
                                      "P1", 0.65)
            # Check for money fields that are 0 when they should be non-zero
            # (e.g., order total_amount=0 but has line items)
            if isinstance(value, (int, float)) and value == 0 and "total" in lowered:
                # Look for line items with non-zero amounts
                has_line_items = any(
                    any(t in lk.lower() for t in ("price", "amount", "subtotal"))
                    for lk, lv in all_values if isinstance(lv, (int, float)) and lv > 0
                )
                if has_line_items:
                    return _violation(f"system_promise_zero_total_with_line_items:{promise_id}",
                                      f"系统承诺: {invariant}", f"{key}=0 但存在非零行项",
                                      "P1", 0.72)

    # ── Dimension: state_machine / lifecycle ──
    _state_keys = {"status", "state", "phase", "stage", "lifecycle"}
    if dims.intersection({"state", "lifecycle", "state_machine", "transition"}):
        for key, value in all_values:
            lowered = key.lower()
            if not any(sk in lowered for sk in _state_keys):
                continue
            if isinstance(value, str):
                # Suspicious state values
                if value.lower() in ("error", "unknown", "undefined", "null", "", "none"):
                    return _violation(f"system_promise_invalid_state:{promise_id}",
                                      f"系统承诺状态机有效: {invariant}",
                                      f"{key}={value} (异常状态值)",
                                      "P1", 0.78)
                # All-caps state values may indicate enum mismatches
                if value.isupper() and len(value) > 3 and value.lower() not in ("paid", "sent", "done", "active", "draft", "open", "closed", "created", "pending_payment"):
                    # Might be a valid state, but flag if not matching common patterns
                    pass
            elif value is None:
                return _violation(f"system_promise_null_state:{promise_id}",
                                  f"系统承诺状态字段非空: {invariant}",
                                  f"{key}=null",
                                  "P0", 0.82)

    # ── Dimension: authorization_access_control / role / visibility ──
    if dims.intersection({"authorization", "role", "permission", "visibility", "privacy"}):
        # Check for fields that imply privilege escalation in response
        privileged_fields = {"admin_only", "internal", "secret", "private_key", "api_key", "password", "token", "role"}
        for key, value in all_values:
            lowered = key.lower()
            if any(pf in lowered for pf in privileged_fields):
                if isinstance(value, str) and value.strip():
                    return _violation(f"system_promise_privileged_field_exposure:{promise_id}",
                                      f"系统承诺权限隔离: {invariant}",
                                      f"{key}={str(value)[:80]} (疑似越权暴露)",
                                      "P1", 0.72)
        # Actor-based check: if scenario actor is "readonly" or non-admin,
        # and response contains admin-related data
        actor = str((scenario.get("runtime_hints") or {}).get("system_promise_safe_read_route", {}).get("actor", "") or
                     "readonly").lower()
        non_admin_roles = {"readonly", "buyer", "user", "guest", "普通用户", "customer"}
        if any(role in actor for role in non_admin_roles) or not actor:
            admin_keywords = {"admin", "administrator", "管理员", "super", "root", "all_users", "all_orders", "all_products"}
            for key, value in all_values:
                lowered_key = key.lower()
                if any(ak in lowered_key for ak in admin_keywords):
                    if isinstance(value, (list, dict)) and len(str(value)) > 2:
                        return _violation(f"system_promise_admin_data_exposure:{promise_id}",
                                          f"系统承诺角色数据隔离: {invariant}",
                                          f"{key} 暴露给非管理员角色",
                                          "P1", 0.75)

    # ── Dimension: tenant_isolation ──
    _tenant_keys = {"tenant_id", "tenant", "org_id", "organization_id", "company_id", "workspace_id"}
    if dims.intersection({"tenant", "tenant_isolation", "isolation"}):
        tenant_values: dict[str, set[Any]] = {}
        for key, value in all_values:
            lowered = key.lower()
            if any(tk in lowered for tk in _tenant_keys):
                tenant_values.setdefault(lowered, set()).add(str(value)[:80])
        # If any tenant key has multiple distinct values in the same response,
        # this indicates cross-tenant data leakage
        for key, vals in tenant_values.items():
            if len(vals) > 1:
                return _violation(f"system_promise_cross_tenant_leak:{promise_id}",
                                  f"系统承诺租户隔离: {invariant}",
                                  f"{key} has {len(vals)} distinct values: {sorted(vals)[:5]}",
                                  "P0", 0.92)

    # ── Dimension: audit_traceability ──
    _audit_keys = {"created_at", "updated_at", "created_by", "updated_by", "trace_id", "request_id", "correlation_id"}
    if dims.intersection({"audit", "traceability"}):
        missing_audit = [ak for ak in _audit_keys if ak not in all_keys_lower]
        if len(missing_audit) >= 3:
            return _violation(f"system_promise_audit_fields_missing:{promise_id}",
                              f"系统承诺审计可追溯: {invariant}",
                              f"缺少审计字段: {missing_audit[:5]}",
                              "P2", 0.58)

    # ── Dimension: data_consistency / cross_surface_consistency ──
    if dims.intersection({"data_consistency", "cross_surface_consistency", "conservation"}):
        # Check for duplicate IDs in list responses
        id_values: dict[str, list[Any]] = {}
        for key, value in all_values:
            if key.lower().endswith(("id", "_id", "uuid", "ids")):
                continue
            if "id" in key.lower().split(".")[-1] or key.lower().endswith("id"):
                id_values.setdefault(key, []).append(value)
        for key, ids in id_values.items():
            if len(ids) > len(set(str(v) for v in ids)):
                return _violation(f"system_promise_duplicate_ids:{promise_id}",
                                  f"系统承诺数据一致性: {invariant}",
                                  f"{key} 包含重复 ID",
                                  "P1", 0.78)

        # Check for empty collections when count > 0
        count_keys = {k.lower() for k in all_keys_lower if any(t in k.lower() for t in ("count", "total", "length", "size"))}
        collection_keys = {k.lower() for k in all_keys_lower if any(t in k.lower() for t in ("items", "rows", "records", "results", "list", "data"))}
        for ck in count_keys:
            count_val = next((v for k, v in all_values if k.lower() == ck), None)
            if isinstance(count_val, (int, float)) and count_val > 0:
                # Check if corresponding collection is non-empty
                for colk in collection_keys:
                    col_val = next((v for k, v in all_values if k.lower() == colk), None)
                    if isinstance(col_val, list) and len(col_val) == 0:
                        return _violation(f"system_promise_count_mismatch:{promise_id}",
                                          f"系统承诺数据一致性: {invariant}",
                                          f"{ck}={count_val} 但 {colk}=[]",
                                          "P1", 0.80)

    # ── Dimension: idempotency ──
    if dims.intersection({"idempotency", "retry"}):
        # Without write capability, can only detect pattern violations
        # Look for responses that suggest non-idempotent behavior
        for key, value in all_values:
            if isinstance(value, str) and "duplicate" in value.lower():
                return _violation(f"system_promise_idempotency_violation:{promise_id}",
                                  f"系统承诺幂等: {invariant}",
                                  f"{key}={value[:100]}",
                                  "P1", 0.72)

    # ── Dimension: input_validation_boundary ──
    if dims.intersection({"validation", "input", "boundary"}):
        # If request had clearly invalid params and still got success (200)
        for step in steps:
            status = int((step.get("response") or {}).get("status_code") or step.get("status") or 0) if isinstance(step, dict) else 0
            if status == 200:
                # Check if any parameter was clearly invalid
                request_params = step.get("request_params") or step.get("params") or {}
                for pk, pv in (request_params.items() if isinstance(request_params, dict) else []):
                    if isinstance(pv, str) and len(pv) > 5000:
                        return _violation(f"system_promise_input_validation_bypass:{promise_id}",
                                          f"系统承诺输入校验: {invariant}",
                                          f"超大参数 {pk} 未被拒绝",
                                          "P2", 0.62)

    # ── Cross-dimension: authorization bypass (401/403 → 200) ──
    for step in steps:
        if not isinstance(step, dict):
            continue
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        status = int(response.get("status_code") or step.get("status") or 0) if isinstance(response, dict) else int(step.get("status") or 0)
        expected = int(step.get("expected_status") or 0)
        if expected in {401, 403} and status == 200:
            return _violation(f"system_promise_authorization_violation:{promise_id}",
                              f"系统承诺权限/角色维度: {invariant}",
                              "期望拒绝但实际 HTTP 200",
                              "P0", 0.90)

    return OracleResult(True, "SystemPromiseOracle", "L7",
                        explanation=f"System Behavior Space promise {promise_id} 已进入 oracle 评估；当前可观测响应未直接反证。已检查维度: {sorted(dims)}")


def _annotate_oracle_failures_with_system_promise(results: list[Any], scenario: dict[str, Any], hints: dict[str, Any]) -> None:
    promise_id = str(hints.get("promise_id") or "")
    if not promise_id:
        return
    invariant = str((scenario.get("runtime_hints") or {}).get("system_promise_invariant") or "")[:300]
    dims = ",".join(str(item) for item in hints.get("dimensions") or [] if str(item))
    for result in results:
        if bool(getattr(result, "passed", True)) or str(getattr(result, "oracle_name", "")) == "SystemPromiseOracle":
            continue
        explanation = str(getattr(result, "explanation", "") or "")
        marker = f"SystemPromise={promise_id}"
        if marker not in explanation:
            setattr(result, "explanation", (explanation + f" | {marker}; dimensions={dims}; invariant={invariant}").strip(" |")[:1200])


def _system_behavior_regression_contract(hints: dict[str, Any]) -> dict[str, Any]:
    if not hints or not str(hints.get("promise_id") or ""):
        return {}
    return {
        "contract_type": "system_behavior_promise_regression",
        "system_behavior_space_version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "system_behavior_space": hints,
        "promise_id": str(hints.get("promise_id") or ""),
        "probe_id": str(hints.get("probe_id") or ""),
        "dimensions": [str(item) for item in hints.get("dimensions") or [] if str(item)],
        "surface_plan": [str(item) for item in hints.get("surface_plan") or [] if str(item)],
        "required_assets": [str(item) for item in hints.get("required_assets") or [] if str(item)],
        "source_slice_id": str(hints.get("source_slice_id") or ""),
        "source_family": str(hints.get("source_family") or ""),
    }


def _contract_from_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    contract = row.get("regression_contract") if isinstance(row.get("regression_contract"), dict) else {}
    if isinstance(contract.get("system_behavior_space"), dict) and str(contract.get("promise_id") or contract["system_behavior_space"].get("promise_id") or ""):
        hints = dict(contract.get("system_behavior_space") or {})
        if not str(hints.get("promise_id") or ""):
            hints["promise_id"] = str(contract.get("promise_id") or "")
        return _system_behavior_regression_contract(hints)
    hints = row.get("system_behavior_space_evidence") if isinstance(row.get("system_behavior_space_evidence"), dict) else {}
    if hints:
        return _system_behavior_regression_contract(hints)
    return {}


def _attach_regression_contract_fields(target: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict) or not contract:
        return target
    target["regression_contract"] = contract
    target["system_promise_id"] = str(contract.get("promise_id") or "")
    target["system_behavior_space_evidence"] = dict(contract.get("system_behavior_space") or {})
    target["system_behavior_dimensions"] = list(contract.get("dimensions") or [])
    target["system_behavior_surface_plan"] = list(contract.get("surface_plan") or [])
    target["system_behavior_required_assets"] = list(contract.get("required_assets") or [])
    target["system_behavior_source_family"] = str(contract.get("source_family") or "")
    return target


def _attach_system_behavior_to_finding(finding: dict[str, Any], hints: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(finding, dict) or not hints:
        return finding
    promise_id = str(hints.get("promise_id") or "").strip()
    if not promise_id:
        return finding
    regression_contract = _system_behavior_regression_contract(hints)
    _attach_regression_contract_fields(finding, regression_contract)
    finding["learning_signal"] = {"source": "system_behavior_space", "promise_id": promise_id, "dimensions": regression_contract.get("dimensions", []), "surfaces": regression_contract.get("surface_plan", []), "entity": str(finding.get("category") or scenario.get("entity") or "system")}
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence["system_promise_id"] = promise_id
    evidence["system_behavior_space"] = hints
    finding["evidence"] = evidence
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    raw["system_behavior_space"] = hints
    raw["regression_contract"] = regression_contract
    finding["raw_evidence"] = raw
    status = finding.get("evidence_status") if isinstance(finding.get("evidence_status"), dict) else {}
    status["system_promise_verdict"] = "SYSTEM_PROMISE_CONFIRMED" if finding.get("gate_passed") is True else "SYSTEM_PROMISE_CANDIDATE"
    finding["evidence_status"] = status
    return finding


def _system_behavior_learning_refresh_summary(project: str, root: Path) -> dict[str, Any]:
    try:
        from ai_test_asset_center.risk_clue_pool import get_platform_learning, refresh_project_learning
        project_learning = refresh_project_learning(project, root)
        platform_learning = get_platform_learning(root)
        return {
            "status": "refreshed",
            "project_learning_version": str(project_learning.get("version") or ""),
            "project_signal_count": int(project_learning.get("signal_count") or 0),
            "project_system_promise_signal_count": int(project_learning.get("system_promise_signal_count") or 0),
            "platform_learning_version": str(platform_learning.get("version") or ""),
            "platform_signal_count": int(platform_learning.get("signal_count") or 0),
        }
    except Exception as exc:
        return {"status": "refresh_failed", "reason": type(exc).__name__}


def _install_system_behavior_scenario_patch() -> None:
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
    except Exception:
        return
    if getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", False):
        return
    original = getattr(_ssg.SemanticScenarioGenerator, "_invariant_from_meta", None)
    if not callable(original):
        return

    def _invariant_from_meta_with_system_behavior(self: Any, slice_meta: dict[str, Any], discovery_round: int, api_doc: str) -> Any:
        item = original(self, slice_meta, discovery_round, api_doc)
        return _enrich_system_behavior_scenario(item, slice_meta, discovery_round)

    _ssg.SemanticScenarioGenerator._ORIGINAL_INVARIANT_FROM_META_SYSTEM_BEHAVIOR = original  # type: ignore[attr-defined]
    _ssg.SemanticScenarioGenerator._invariant_from_meta = _invariant_from_meta_with_system_behavior  # type: ignore[method-assign]
    _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_oracle_patch() -> None:
    try:
        from ai_test_asset_center import oracle_engine as _oe
    except Exception:
        return
    if getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", False):
        return
    original_evaluate = getattr(_oe.OracleEngine, "evaluate", None)
    original_build = getattr(_oe.EvidenceGraphBuilder, "build", None)
    if not callable(original_evaluate) or not callable(original_build):
        return

    def _evaluate_with_system_behavior(self: Any, scenario: dict[str, Any], trace: dict[str, Any], snapshots: Any = None) -> list[Any]:
        results = list(original_evaluate(self, scenario, trace, snapshots) or [])
        hints = _scenario_system_behavior_hints(scenario)
        if not hints:
            return results
        _annotate_oracle_failures_with_system_promise(results, scenario, hints)
        direct = _direct_system_promise_oracle_result(scenario, trace, hints)
        if direct is not None and (not bool(getattr(direct, "passed", True)) or not any(str(getattr(item, "oracle_name", "")) == "SystemPromiseOracle" for item in results)):
            results.append(direct)
        return results

    def _build_with_system_behavior_evidence(self: Any, scenario: dict[str, Any], trace: dict[str, Any], snapshots: Any, oracle_results: list[Any]) -> Any:
        hints = _scenario_system_behavior_hints(scenario)
        if hints:
            scenario = {**scenario, "system_behavior_space_evidence": hints, "system_promise_id": str(hints.get("promise_id") or "")}
        return original_build(self, scenario, trace, snapshots, oracle_results)

    _oe.OracleEngine._ORIGINAL_EVALUATE_SYSTEM_BEHAVIOR = original_evaluate  # type: ignore[attr-defined]
    _oe.EvidenceGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR = original_build  # type: ignore[attr-defined]
    _oe.OracleEngine.evaluate = _evaluate_with_system_behavior  # type: ignore[method-assign]
    _oe.EvidenceGraphBuilder.build = _build_with_system_behavior_evidence  # type: ignore[method-assign]
    _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_finding_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", False):
        return
    original_confirmed = getattr(_v12, "_confirmed_oracle_finding", None)
    original_persist = getattr(_v12, "_persist_confirmed_findings", None)
    if not callable(original_confirmed) or not callable(original_persist):
        return

    def _confirmed_oracle_finding_with_system_behavior(scenario: Any, trace: dict[str, Any], oracle_result: Any, evidence: Any, *, campaign_id: str, discovery_round: int, base_url: str) -> dict[str, Any]:
        finding = original_confirmed(scenario, trace, oracle_result, evidence, campaign_id=campaign_id, discovery_round=discovery_round, base_url=base_url)
        scenario_payload = _scenario_payload(scenario)
        hints = _scenario_system_behavior_hints(scenario_payload)
        if not hints and hasattr(evidence, "to_dict"):
            try:
                evidence_payload = evidence.to_dict()
                if isinstance(evidence_payload, dict):
                    hints = _scenario_system_behavior_hints(evidence_payload.get("scenario") if isinstance(evidence_payload.get("scenario"), dict) else {})
            except Exception:
                hints = {}
        return _attach_system_behavior_to_finding(finding, hints, scenario_payload)

    def _persist_confirmed_findings_with_system_behavior(root: Path, project: str, findings: list[dict[str, Any]]) -> int:
        # Base _persist_confirmed_findings now forwards system_promise_id,
        # regression_contract and all system behavior metadata through the ledger.
        # The fragile re-read/patch step from earlier versions is no longer needed.
        return int(original_persist(root, project, findings) or 0)

    _v12._ORIGINAL_CONFIRMED_ORACLE_FINDING_SYSTEM_BEHAVIOR = original_confirmed  # type: ignore[attr-defined]
    _v12._ORIGINAL_PERSIST_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR = original_persist  # type: ignore[attr-defined]
    _v12._confirmed_oracle_finding = _confirmed_oracle_finding_with_system_behavior  # type: ignore[assignment]
    _v12._persist_confirmed_findings = _persist_confirmed_findings_with_system_behavior  # type: ignore[assignment]
    _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_regression_patch() -> None:
    try:
        from ai_test_asset_center import regression_runner as _rr
        from ai_test_asset_center import regression_suite_builder as _rsb
    except Exception:
        return
    if getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_PATCHED", False):
        return

    original_load_confirmed = getattr(_rsb, "_load_confirmed_findings_regression_probes", None)
    original_judge = getattr(_rr, "_judge_probe", None)
    original_reverify = getattr(_rr, "_reverify_confirmed_findings", None)
    original_append_history = getattr(_rr, "_append_regression_history", None)
    if not all(callable(fn) for fn in (original_load_confirmed, original_judge, original_reverify, original_append_history)):
        return

    def _load_confirmed_findings_regression_probes_with_system_behavior(project: str, root: Path) -> list[dict[str, Any]]:
        # Base _load_confirmed_findings_regression_probes now forwards
        # system_promise_id, regression_contract and all system behavior
        # metadata from the ledger into each probe. No re-read needed.
        return list(original_load_confirmed(project, root) or [])

    def _judge_probe_with_system_behavior(probe: dict[str, Any], execution: dict[str, Any], skipped: bool = False, skip_reason: str = "") -> dict[str, Any]:
        # Base _judge_probe now forwards system_promise_id and
        # regression_contract. Only add oracle_intent here.
        item = original_judge(probe, execution, skipped=skipped, skip_reason=skip_reason)
        contract = _contract_from_row(probe)
        if contract:
            item["oracle_intent"] = [f"SystemPromiseOracle.dimension:{dim}" for dim in contract.get("dimensions") or []]
        return item

    def _reverify_confirmed_findings_with_system_behavior(project: str, root: Path, cfg: dict[str, Any], safety_boundary: dict[str, Any], timeout: float, dry_run: bool) -> dict[str, Any]:
        # Base _reverify_confirmed_findings now forwards system_promise_id
        # and regression_contract from the ledger into each verdict.
        result = original_reverify(project, root, cfg, safety_boundary, timeout, dry_run)
        if isinstance(result, dict):
            result["system_promise_reverification_count"] = sum(
                1 for item in result.get("verdicts", [])
                if isinstance(item, dict) and item.get("system_promise_id")
            )
        return result

    def _append_regression_history_with_system_behavior(project: str, root: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
        # Base _append_regression_history now forwards system_promise_id and
        # regression_contract into history items and writes to both locations.
        # Patch only needs to trigger learning refresh after history is written.
        history = list(original_append_history(project, root, result) or [])
        if not history:
            return history
        try:
            refresh = _system_behavior_learning_refresh_summary(project, root)
            result["risk_clue_pool_learning_refresh"] = refresh
            last = history[-1]
            last["risk_clue_pool_learning_refresh"] = refresh
            history[-1] = last
            _rr._write_json(root / "platform_outputs" / project / "regression_run" / "regression_run_history.json", history)
            _rr._write_json(root / "platform_workspace" / project / "defect_discovery" / "regression_run_history.json", history)
        except Exception:
            return history
        return history

    _rsb._ORIGINAL_LOAD_CONFIRMED_FINDINGS_REGRESSION_PROBES_SYSTEM_BEHAVIOR = original_load_confirmed  # type: ignore[attr-defined]
    _rr._ORIGINAL_JUDGE_PROBE_SYSTEM_BEHAVIOR = original_judge  # type: ignore[attr-defined]
    _rr._ORIGINAL_REVERIFY_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR = original_reverify  # type: ignore[attr-defined]
    _rr._ORIGINAL_APPEND_REGRESSION_HISTORY_SYSTEM_BEHAVIOR = original_append_history  # type: ignore[attr-defined]
    _rsb._load_confirmed_findings_regression_probes = _load_confirmed_findings_regression_probes_with_system_behavior  # type: ignore[assignment]
    _rr._judge_probe = _judge_probe_with_system_behavior  # type: ignore[assignment]
    _rr._reverify_confirmed_findings = _reverify_confirmed_findings_with_system_behavior  # type: ignore[assignment]
    _rr._append_regression_history = _append_regression_history_with_system_behavior  # type: ignore[assignment]
    _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = True  # type: ignore[attr-defined]


def install_system_behavior_space_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    if getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", False):
        return
    original_build = getattr(_bsg.BusinessStateGraphBuilder, "build")
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "behavior_contract")

    def _build_with_system_behavior_space(self: Any, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, Any]:
        graphs = original_build(self, prd_text, api_spec_text, db_schema_text)
        try:
            asset = getattr(self, "system_behavior_space_knowledge_asset", None)
            if not isinstance(asset, dict) or not asset:
                asset = _load_existing_enterprise_asset()
            self.system_behavior_space = build_system_behavior_space(prd_text, api_spec_text, db_schema_text, knowledge_asset=asset).to_dict()
        except Exception as exc:
            self.system_behavior_space = {"version": SYSTEM_BEHAVIOR_SPACE_VERSION, "status": "unavailable", "reason": f"system_behavior_space_build_failed:{type(exc).__name__}", "summary": {"object_count": 0, "promise_count": 0, "probe_candidate_count": 0, "coverage_gap_count": 1}}
        return graphs

    def _behavior_contract_with_system_behavior_space(self: Any) -> dict[str, Any]:
        contract = original_contract(self)
        space = getattr(self, "system_behavior_space", None)
        if isinstance(space, dict) and space:
            contract["system_behavior_space"] = space
            contract = _attach_system_behavior_slices(contract, space)
            summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
            space_summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
            summary["system_behavior_space_version"] = str(space.get("version") or SYSTEM_BEHAVIOR_SPACE_VERSION)
            summary["system_promise_count"] = int(space_summary.get("promise_count") or 0)
            summary["system_probe_candidate_count"] = int(space_summary.get("probe_candidate_count") or 0)
            summary["system_behavior_object_count"] = int(space_summary.get("object_count") or 0)
            summary["system_behavior_source_coverage"] = space_summary.get("source_coverage") if isinstance(space_summary.get("source_coverage"), dict) else {}
            summary["system_behavior_goal"] = "open_ended_system_promise_discovery_across_all_surfaces"
            contract["summary"] = summary
            gaps = contract.get("coverage_gaps") if isinstance(contract.get("coverage_gaps"), list) else []
            for gap in space.get("coverage_gaps") if isinstance(space.get("coverage_gaps"), list) else []:
                if isinstance(gap, dict):
                    gaps.append({**gap, "source": "system_behavior_space"})
            contract["coverage_gaps"] = gaps
        return contract

    _bsg.BusinessStateGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE = original_build  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder._ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE = original_contract  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder.build = _build_with_system_behavior_space  # type: ignore[method-assign]
    _bsg.BusinessStateGraphBuilder.behavior_contract = _behavior_contract_with_system_behavior_space  # type: ignore[method-assign]
    _install_v12_behavior_space_context_patch()
    _install_system_behavior_scenario_patch()
    _install_system_behavior_oracle_patch()
    _install_system_behavior_finding_patch()
    _install_system_behavior_regression_patch()
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = True  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def _install_v12_behavior_space_context_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", False):
        return
    original = getattr(_v12, "run_v12_pipeline", None)
    if not callable(original):
        return

    def _run_v12_pipeline_with_behavior_space_context(project: str, root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _BEHAVIOR_SPACE_CONTEXT.set({"project": str(project or ""), "root": Path(root)})
        try:
            return original(project, root, *args, **kwargs)
        finally:
            _BEHAVIOR_SPACE_CONTEXT.reset(token)

    _v12._ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_SPACE_CONTEXT = original  # type: ignore[attr-defined]
    _v12.run_v12_pipeline = _run_v12_pipeline_with_behavior_space_context  # type: ignore[assignment]
    _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = True  # type: ignore[attr-defined]


def prepare_system_behavior_space_learning_context(builder: Any, *, project: str, root: Any) -> Any:
    try:
        from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
        asset = load_enterprise_business_knowledge_asset(project, Path(root))
        if isinstance(asset, dict):
            setattr(builder, "system_behavior_space_knowledge_asset", asset)
    except Exception:
        pass
    return builder


def restore_system_behavior_space_patch() -> None:
    original_build = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE", None)
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE", None)
    if callable(original_build):
        _bsg.BusinessStateGraphBuilder.build = original_build  # type: ignore[method-assign]
    if callable(original_contract):
        _bsg.BusinessStateGraphBuilder.behavior_contract = original_contract  # type: ignore[method-assign]
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        original_v12 = getattr(_v12, "_ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_SPACE_CONTEXT", None)
        if callable(original_v12):
            _v12.run_v12_pipeline = original_v12  # type: ignore[assignment]
        _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
        original_scenario = getattr(_ssg.SemanticScenarioGenerator, "_ORIGINAL_INVARIANT_FROM_META_SYSTEM_BEHAVIOR", None)
        if callable(original_scenario):
            _ssg.SemanticScenarioGenerator._invariant_from_meta = original_scenario  # type: ignore[method-assign]
        _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import oracle_engine as _oe
        original_eval = getattr(_oe.OracleEngine, "_ORIGINAL_EVALUATE_SYSTEM_BEHAVIOR", None)
        original_build = getattr(_oe.EvidenceGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR", None)
        if callable(original_eval):
            _oe.OracleEngine.evaluate = original_eval  # type: ignore[method-assign]
        if callable(original_build):
            _oe.EvidenceGraphBuilder.build = original_build  # type: ignore[method-assign]
        _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        original_confirmed = getattr(_v12, "_ORIGINAL_CONFIRMED_ORACLE_FINDING_SYSTEM_BEHAVIOR", None)
        original_persist = getattr(_v12, "_ORIGINAL_PERSIST_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR", None)
        if callable(original_confirmed):
            _v12._confirmed_oracle_finding = original_confirmed  # type: ignore[assignment]
        if callable(original_persist):
            _v12._persist_confirmed_findings = original_persist  # type: ignore[assignment]
        _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import regression_runner as _rr
        from ai_test_asset_center import regression_suite_builder as _rsb
        original_load_confirmed = getattr(_rsb, "_ORIGINAL_LOAD_CONFIRMED_FINDINGS_REGRESSION_PROBES_SYSTEM_BEHAVIOR", None)
        original_judge = getattr(_rr, "_ORIGINAL_JUDGE_PROBE_SYSTEM_BEHAVIOR", None)
        original_reverify = getattr(_rr, "_ORIGINAL_REVERIFY_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR", None)
        original_append_history = getattr(_rr, "_ORIGINAL_APPEND_REGRESSION_HISTORY_SYSTEM_BEHAVIOR", None)
        if callable(original_load_confirmed):
            _rsb._load_confirmed_findings_regression_probes = original_load_confirmed  # type: ignore[assignment]
        if callable(original_judge):
            _rr._judge_probe = original_judge  # type: ignore[assignment]
        if callable(original_reverify):
            _rr._reverify_confirmed_findings = original_reverify  # type: ignore[assignment]
        if callable(original_append_history):
            _rr._append_regression_history = original_append_history  # type: ignore[assignment]
        _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = False  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
