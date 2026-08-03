"""System Behavior Space slice materialization for BusinessStateGraph.

Owns source-grounded slice generation attached through first-class BSG
hooks. The private-pilot patch module remains the thin installer.
"""
from __future__ import annotations

from typing import Any

from ai_test_asset_center import business_state_graph as _bsg
from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
)

_SAFE_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_KNOWN_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}

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
        # ── Endpoints for observe steps must be safe-read (GET/HEAD/OPTIONS) ──
        # POST/PUT/DELETE paths generate write steps only; including them in
        # `endpoints` causes the scenario generator to create invalid GET-on-POST
        # observe steps → 404 noise.
        endpoints: list[str] = []
        for route in routes:
            method = str(route.get("method") or "").upper()
            path = str(route.get("path") or "")
            if method in _SAFE_READ_METHODS and path and path not in endpoints:
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
            "source_refs": [{"kind": "system_behavior_space", "source_type": "system_behavior_space", "locator": promise_id, "quote": invariant[:500]}],
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
            # ── Server-verified route check ──
            # Route reachability is evaluated by the executor, not this model.
            # Source-bound routes are compiled without any benchmark whitelist.
            # Governed execution produces the authoritative reachability receipt.
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

