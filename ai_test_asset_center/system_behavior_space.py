from __future__ import annotations

"""System Behavior Space Model.

Core product goal: given any enterprise system, model its promised behavior
across all observable surfaces, then generate probe candidates for evidence-
backed deviations. Bug families are labels after evidence, not the model's limit.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .business_state_graph import _api_facts, _schema_facts

SYSTEM_BEHAVIOR_SPACE_VERSION = "system_behavior_space.v1"
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_STATE_RE = re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I)

_TENANT_HINTS = ("tenant", "org_id", "organization_id", "company_id", "workspace_id", "account_id")
_SOFT_DELETE_HINTS = ("deleted", "is_deleted", "deleted_at", "removed_at")
_AUDIT_HINTS = ("created_at", "updated_at", "created_by", "updated_by", "trace_id", "request_id", "correlation_id")
_MONEY_HINTS = ("amount", "price", "balance", "fee", "pay", "refund", "total")
_QUANTITY_HINTS = ("quantity", "qty", "stock", "inventory", "remaining", "available")
_STATE_HINTS = ("status", "state", "phase", "stage", "lifecycle")
_TIME_HINTS = ("expires", "expired", "valid_from", "valid_until", "deadline", "effective")


@dataclass
class BehaviorObject:
    entity: str
    surfaces: set[str] = field(default_factory=set)
    api_paths: set[str] = field(default_factory=set)
    db_tables: set[str] = field(default_factory=set)
    ui_routes: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    states: set[str] = field(default_factory=set)
    fields: dict[str, set[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "surfaces": sorted(self.surfaces),
            "api_paths": sorted(self.api_paths),
            "db_tables": sorted(self.db_tables),
            "ui_routes": sorted(self.ui_routes),
            "roles": sorted(self.roles),
            "states": sorted(self.states),
            "fields": {k: sorted(v) for k, v in sorted(self.fields.items())},
        }


@dataclass
class BehaviorPromise:
    promise_id: str
    entity: str
    invariant: str
    surfaces: list[str]
    dimensions: list[str]
    source: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "promise_id": self.promise_id,
            "entity": self.entity,
            "invariant": self.invariant,
            "surfaces": self.surfaces,
            "dimensions": self.dimensions,
            "source": self.source,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ProbeCandidate:
    probe_id: str
    promise_id: str
    entity: str
    objective: str
    surface_plan: list[str]
    required_assets: list[str]
    oracle_intent: list[str]
    priority: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "promise_id": self.promise_id,
            "entity": self.entity,
            "objective": self.objective,
            "surface_plan": self.surface_plan,
            "required_assets": self.required_assets,
            "oracle_intent": self.oracle_intent,
            "priority": round(self.priority, 3),
        }


@dataclass
class SystemBehaviorSpace:
    objects: dict[str, BehaviorObject] = field(default_factory=dict)
    promises: list[BehaviorPromise] = field(default_factory=list)
    probe_candidates: list[ProbeCandidate] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        by_surface: dict[str, int] = {}
        by_dimension: dict[str, int] = {}
        for promise in self.promises:
            for surface in promise.surfaces:
                by_surface[surface] = by_surface.get(surface, 0) + 1
            for dimension in promise.dimensions:
                by_dimension[dimension] = by_dimension.get(dimension, 0) + 1
        return {
            "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
            "model_goal": "Discover evidence-backed violations of system promises across any enterprise system surface; bug families are labels, not limits.",
            "objects": [obj.to_dict() for obj in sorted(self.objects.values(), key=lambda item: item.entity)],
            "promises": [item.to_dict() for item in self.promises],
            "probe_candidates": [item.to_dict() for item in sorted(self.probe_candidates, key=lambda item: (-item.priority, item.entity, item.probe_id))],
            "coverage_gaps": self.coverage_gaps,
            "summary": {
                "object_count": len(self.objects),
                "promise_count": len(self.promises),
                "probe_candidate_count": len(self.probe_candidates),
                "coverage_gap_count": len(self.coverage_gaps),
                "promise_by_surface": dict(sorted(by_surface.items())),
                "promise_by_dimension": dict(sorted(by_dimension.items())),
            },
        }


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _entity(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:80] or "system"


def _obj(space: SystemBehaviorSpace, entity: Any) -> BehaviorObject:
    key = _entity(entity)
    if key not in space.objects:
        space.objects[key] = BehaviorObject(key)
    return space.objects[key]


def _add_field(obj: BehaviorObject, group: str, name: str) -> None:
    if name:
        obj.fields.setdefault(group, set()).add(name)


def _add_promise(space: SystemBehaviorSpace, entity: Any, invariant: str, surfaces: list[str], dimensions: list[str], source: str, confidence: float) -> None:
    clean_entity = _entity(entity)
    clean_surfaces = sorted({s for s in surfaces if s})
    clean_dimensions = sorted({d for d in dimensions if d})
    pid = _stable_id("promise", clean_entity, invariant, ",".join(clean_surfaces), ",".join(clean_dimensions))
    if any(item.promise_id == pid for item in space.promises):
        return
    space.promises.append(BehaviorPromise(pid, clean_entity, invariant, clean_surfaces, clean_dimensions, source, confidence))


def _columns_for_group(columns: list[str], hints: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for col in columns:
        key = re.sub(r"[^a-z0-9_]+", "_", str(col or "").lower())
        if any(hint in key for hint in hints):
            out.append(col)
    return out


def _parse_create_tables(db_schema_text: str) -> list[dict[str, Any]]:
    text = str(db_schema_text or "")
    tables: list[dict[str, Any]] = []
    for match in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([A-Za-z0-9_\.]+)[`\"\]]?\s*\((.*?)\);", text, re.I | re.S):
        table = match.group(1).split(".")[-1]
        body = match.group(2)
        columns: list[str] = []
        unique = False
        foreign_key = False
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            lowered = line.lower()
            if "unique" in lowered:
                unique = True
            if "foreign key" in lowered or " references " in f" {lowered} ":
                foreign_key = True
            if lowered.startswith(("constraint", "primary key", "foreign key", "unique", "key ", "index ")):
                continue
            m = re.match(r"[`\"\[]?([A-Za-z_][A-Za-z0-9_]*)[`\"\]]?\s+", line)
            if m:
                columns.append(m.group(1))
        tables.append({"table": table, "entity": _entity(table), "columns": columns, "unique": unique, "foreign_key": foreign_key})
    return tables


def _roles(accounts: Any, text: str) -> list[str]:
    roles: list[str] = []
    values = list(accounts.values()) if isinstance(accounts, dict) else (accounts if isinstance(accounts, list) else [])
    for item in values:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("name") or item.get("email") or "").strip().lower()
            if role and role not in roles:
                roles.append(role)
    for token in ("admin", "administrator", "owner", "manager", "operator", "auditor", "user", "guest", "管理员", "普通用户", "财务", "运营", "审核员"):
        if token.lower() in str(text or "").lower() and token not in roles:
            roles.append(token)
    return roles


def _ui_routes(ui_materials: Any) -> list[str]:
    if isinstance(ui_materials, str):
        text = ui_materials
    elif isinstance(ui_materials, (dict, list)):
        text = json.dumps(ui_materials, ensure_ascii=False, default=str)
    else:
        text = ""
    routes: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9])(/[A-Za-z0-9_./{}:@?=&%\-]+)", text):
        route = match.group(1).rstrip(".,;，。；")
        if route not in routes and len(route) <= 200:
            routes.append(route)
    return routes[:200]


def _assets(surfaces: list[str]) -> list[str]:
    required: list[str] = []
    if "api" in surfaces:
        required += ["source_bound_api_catalog", "approved_base_url", "actor_credentials_or_token"]
    if "db" in surfaces:
        required += ["database_schema_or_readonly_snapshot_connection", "schema_introspection"]
    if "ui" in surfaces:
        required += ["ui_route_map_or_dom_snapshot", "approved_ui_base_url", "browser_or_page_agent"]
    if "auth" in surfaces:
        required += ["role_account_matrix_or_permission_matrix"]
    if "log" in surfaces:
        required += ["log_or_trace_access"]
    return sorted(set(required))


def _materialize_probe(space: SystemBehaviorSpace, promise: BehaviorPromise) -> None:
    probe_id = _stable_id("probe", promise.promise_id, ",".join(promise.surfaces))
    priority = min(0.98, 0.35 + 0.1 * len(promise.surfaces) + 0.08 * len(promise.dimensions) + promise.confidence * 0.25)
    space.probe_candidates.append(ProbeCandidate(
        probe_id=probe_id,
        promise_id=promise.promise_id,
        entity=promise.entity,
        objective=f"Verify system promise for {promise.entity}: {promise.invariant}",
        surface_plan=promise.surfaces,
        required_assets=_assets(promise.surfaces),
        oracle_intent=[f"promise_violation:{dimension}" for dimension in promise.dimensions],
        priority=priority,
    ))


def build_system_behavior_space(
    prd_text: str = "",
    api_spec_text: str = "",
    db_schema_text: str = "",
    *,
    ui_materials: Any = None,
    accounts: Any = None,
) -> SystemBehaviorSpace:
    space = SystemBehaviorSpace()

    roles = _roles(accounts, prd_text)
    if roles:
        actor = _obj(space, "actor")
        actor.surfaces.add("auth")
        actor.roles.update(roles)
        _add_promise(space, "actor", "Role-bound actions must be authorized consistently across API, UI and data access.", ["auth", "api", "ui", "db"], ["role", "authorization", "cross_surface_consistency"], "role_catalog", 0.65)

    try:
        api_entities, api_states, endpoints = _api_facts(api_spec_text, _STATE_RE)
    except Exception:
        api_entities, api_states, endpoints = {}, {}, []
    for entity, refs in api_entities.items():
        obj = _obj(space, entity)
        obj.surfaces.add("api")
        for state in api_states.get(entity, {}):
            obj.states.add(str(state))
    for endpoint in endpoints:
        method = str(endpoint.get("method") or "").upper() if isinstance(endpoint, dict) else ""
        path = str(endpoint.get("path") or "").strip() if isinstance(endpoint, dict) else ""
        if not path:
            continue
        entity = _entity(endpoint.get("entity") or path.strip("/").split("/")[0])
        obj = _obj(space, entity)
        obj.surfaces.add("api")
        obj.api_paths.add(f"{method} {path}" if method else path)
        if method in _READ_METHODS:
            _add_promise(space, entity, f"Read endpoint {method} {path} must expose only data visible to the current actor, tenant and lifecycle state.", ["api"], ["visibility", "tenant", "role", "lifecycle"], "api_catalog", 0.55)
        if method in _WRITE_METHODS:
            _add_promise(space, entity, f"Write endpoint {method} {path} must preserve business invariants, authorization, idempotency and data consistency.", ["api", "db"], ["authorization", "idempotency", "data_consistency", "state", "side_effect"], "api_catalog", 0.62)

    try:
        db_entities, db_states, dependencies = _schema_facts(db_schema_text, _STATE_RE)
    except Exception:
        db_entities, db_states, dependencies = {}, {}, []
    for entity in db_entities:
        obj = _obj(space, entity)
        obj.surfaces.add("db")
        for state in db_states.get(entity, {}):
            obj.states.add(str(state))
    tables = _parse_create_tables(db_schema_text)
    for table in tables:
        entity = table["entity"]
        obj = _obj(space, entity)
        obj.surfaces.add("db")
        obj.db_tables.add(table["table"])
        columns = list(table["columns"])
        for group, hints in (
            ("tenant", _TENANT_HINTS), ("soft_delete", _SOFT_DELETE_HINTS), ("audit", _AUDIT_HINTS),
            ("money", _MONEY_HINTS), ("quantity", _QUANTITY_HINTS), ("state", _STATE_HINTS), ("time", _TIME_HINTS),
        ):
            for col in _columns_for_group(columns, hints):
                _add_field(obj, group, col)
        if obj.fields.get("tenant"):
            _add_promise(space, entity, f"Table {table['table']} tenant fields must prevent cross-tenant reads, writes and UI/API leakage.", ["db", "api", "ui"], ["tenant", "authorization", "visibility"], "db_schema", 0.72)
        if obj.fields.get("soft_delete"):
            _add_promise(space, entity, f"Soft-deleted records in {table['table']} must not reappear through query, search, UI list or export surfaces.", ["db", "api", "ui"], ["lifecycle", "visibility", "data_consistency"], "db_schema", 0.7)
        if obj.fields.get("audit"):
            _add_promise(space, entity, f"Mutations of {table['table']} must preserve auditability and correlation evidence.", ["db", "api", "log"], ["audit", "traceability", "side_effect"], "db_schema", 0.58)
        if obj.fields.get("money"):
            _add_promise(space, entity, f"Money fields in {table['table']} must remain non-negative, conserved and consistent across API, DB and UI.", ["db", "api", "ui"], ["money", "conservation", "data_consistency"], "db_schema", 0.75)
        if obj.fields.get("quantity"):
            _add_promise(space, entity, f"Quantity fields in {table['table']} must remain non-negative and concurrency-safe across operations.", ["db", "api"], ["quantity", "concurrency", "conservation"], "db_schema", 0.72)
        if obj.fields.get("state"):
            _add_promise(space, entity, f"State fields in {table['table']} must follow allowed lifecycle transitions and cannot drift from API/UI state.", ["db", "api", "ui"], ["state", "lifecycle", "cross_surface_consistency"], "db_schema", 0.68)
        if table.get("foreign_key"):
            _add_promise(space, entity, f"Foreign-key-like fields in {table['table']} must preserve parent/child consistency and avoid orphaned data.", ["db", "api"], ["referential_integrity", "data_consistency"], "db_schema", 0.63)
        if table.get("unique"):
            _add_promise(space, entity, f"Unique constraints in {table['table']} must reject duplicates under retry and concurrency.", ["db", "api"], ["uniqueness", "idempotency", "concurrency"], "db_schema", 0.63)
    for dep in dependencies if isinstance(dependencies, list) else []:
        if isinstance(dep, (list, tuple)) and len(dep) >= 2:
            _add_promise(space, dep[0], f"{dep[0]} must remain consistent with parent object {dep[1]} across lifecycle and data changes.", ["db", "api"], ["referential_integrity", "cross_entity_consistency"], "schema_dependency", 0.58)

    routes = _ui_routes(ui_materials)
    for route in routes:
        entity = _entity(route.strip("/").split("/")[-1] or route.strip("/").split("/")[0] or "ui")
        obj = _obj(space, entity)
        obj.surfaces.add("ui")
        obj.ui_routes.add(route)
        dims = ["visibility", "ui_contract"]
        if any(token in route.lower() for token in ("admin", "manage", "setting", "权限", "管理")):
            dims += ["role", "authorization"]
        _add_promise(space, entity, f"UI route {route} must expose actions and data consistently with role, state and backend contracts.", ["ui", "api"], dims, "ui_material", 0.55)

    for entity, obj in list(space.objects.items()):
        if "api" in obj.surfaces and "db" in obj.surfaces:
            _add_promise(space, entity, "API-visible state and DB-persisted state must not drift after reads, writes, retries or failures.", ["api", "db"], ["cross_surface_consistency", "state", "data_consistency"], "surface_join", 0.7)
        if "ui" in obj.surfaces and "api" in obj.surfaces:
            _add_promise(space, entity, "UI-visible data, buttons and validation must match API behavior and authorization outcomes.", ["ui", "api"], ["ui_api_contract", "authorization", "validation"], "surface_join", 0.66)
        if "ui" in obj.surfaces and "db" in obj.surfaces:
            _add_promise(space, entity, "UI lists, details and exports must not reveal DB records outside lifecycle, tenant or role constraints.", ["ui", "db", "api"], ["visibility", "tenant", "lifecycle", "cross_surface_consistency"], "surface_join", 0.68)

    for promise in list(space.promises):
        _materialize_probe(space, promise)

    if not endpoints:
        space.coverage_gaps.append({"kind": "API_SURFACE_MISSING", "required_asset": "openapi_postman_har_or_gateway_route_log", "reason": "No source-bound API catalog; API/API-DB probes cannot execute."})
    if not tables:
        space.coverage_gaps.append({"kind": "DB_SURFACE_MISSING", "required_asset": "database_schema_or_readonly_connection", "reason": "No schema/table map; DB-only and API-DB consistency probes are limited."})
    if not routes:
        space.coverage_gaps.append({"kind": "UI_SURFACE_MISSING", "required_asset": "ui_route_map_dom_snapshot_design_or_browser_entry", "reason": "No UI route/DOM material; UI behavior promises cannot be explored automatically."})
    if not roles:
        space.coverage_gaps.append({"kind": "ROLE_SURFACE_MISSING", "required_asset": "role_account_matrix_or_permission_matrix", "reason": "No role/actor catalog; authorization and tenant probes are incomplete."})
    return space
