from __future__ import annotations

"""System Behavior Space Model.

This is the product-core model QualiBug-AI needs before it can move beyond
"API tests" or a fixed bug-family list.  It turns the customer's project inputs
(API, DB schema, UI material, roles/accounts and business prose) into one open
behavior universe:

    business object × role × state × operation × page × API × table/field
    × data constraint × time/concurrency × tenant × external side-effect

Bug families are NOT the source of truth here.  A defect is any evidence-backed
violation of a system promise discovered on one or more surfaces.
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

_TENANT_FIELDS = {"tenant_id", "tenantid", "org_id", "organization_id", "company_id", "workspace_id", "account_id"}
_OWNER_FIELDS = {"user_id", "owner_id", "creator_id", "created_by", "customer_id", "member_id"}
_SOFT_DELETE_FIELDS = {"deleted_at", "deleted", "is_deleted", "delete_time", "removed_at"}
_AUDIT_FIELDS = {"created_at", "updated_at", "created_by", "updated_by", "trace_id", "request_id", "correlation_id"}
_MONEY_FIELDS = {"amount", "total", "total_amount", "price", "fee", "balance", "payable", "paid_amount", "refund_amount"}
_QUANTITY_FIELDS = {"quantity", "qty", "stock", "inventory", "remaining", "reserved", "available"}
_STATE_FIELDS = {"status", "state", "phase", "stage", "lifecycle"}
_TIME_FIELDS = {"expires_at", "expired_at", "valid_from", "valid_until", "effective_at", "deadline", "started_at", "ended_at"}


@dataclass
class BehaviorObject:
    entity: str
    surfaces: set[str] = field(default_factory=set)
    api_paths: set[str] = field(default_factory=set)
    db_tables: set[str] = field(default_factory=set)
    ui_routes: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    states: set[str] = field(default_factory=set)
    fields: dict[str, list[str]] = field(default_factory=dict)
    source_refs: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "surfaces": sorted(self.surfaces),
            "api_paths": sorted(self.api_paths),
            "db_tables": sorted(self.db_tables),
            "ui_routes": sorted(self.ui_routes),
            "roles": sorted(self.roles),
            "states": sorted(self.states),
            "fields": {key: sorted(set(values)) for key, values in sorted(self.fields.items())},
            "source_refs": self.source_refs[:20],
        }


@dataclass
class BehaviorPromise:
    promise_id: str
    entity: str
    invariant: str
    surfaces: list[str]
    dimensions: list[str]
    source: str
    confidence: float = 0.5
    source_refs: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promise_id": self.promise_id,
            "entity": self.entity,
            "invariant": self.invariant,
            "surfaces": self.surfaces,
            "dimensions": self.dimensions,
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
            "source_refs": self.source_refs[:20],
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
    priority: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "promise_id": self.promise_id,
            "entity": self.entity,
            "objective": self.objective,
            "surface_plan": self.surface_plan,
            "required_assets": self.required_assets,
            "oracle_intent": self.oracle_intent,
            "priority": round(float(self.priority), 3),
        }


@dataclass
class SystemBehaviorSpace:
    objects: dict[str, BehaviorObject] = field(default_factory=dict)
    promises: list[BehaviorPromise] = field(default_factory=list)
    probe_candidates: list[ProbeCandidate] = field(default_factory=list)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        by_surface: dict[str, int] = {}
        for promise in self.promises:
            for surface in promise.surfaces:
                by_surface[surface] = by_surface.get(surface, 0) + 1
        by_dimension: dict[str, int] = {}
        for promise in self.promises:
            for dimension in promise.dimensions:
                by_dimension[dimension] = by_dimension.get(dimension, 0) + 1
        return {
            "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
            "model_goal": "Discover evidence-backed violations of system promises across any enterprise system surface; bug families are labels, not limits.",
            "objects": [item.to_dict() for item in sorted(self.objects.values(), key=lambda x: x.entity)],
            "promises": [item.to_dict() for item in self.promises],
            "probe_candidates": [item.to_dict() for item in sorted(self.probe_candidates, key=lambda x: (-x.priority, x.entity, x.probe_id))],
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


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def _object(space: SystemBehaviorSpace, entity: Any) -> BehaviorObject:
    key = _entity(entity)
    if key not in space.objects:
        space.objects[key] = BehaviorObject(entity=key)
    return space.objects[key]


def _add_fields(obj: BehaviorObject, group: str, values: list[str]) -> None:
    clean = [str(value).strip() for value in values if str(value).strip()]
    if clean:
        obj.fields.setdefault(group, [])
        obj.fields[group].extend(clean)


def _promise(space: SystemBehaviorSpace, entity: str, invariant: str, surfaces: list[str], dimensions: list[str], source: str, confidence: float = 0.5, refs: list[dict[str, str]] | None = None) -> None:
    pid = _stable_id("promise", entity, invariant, ",".join(sorted(surfaces)), ",".join(sorted(dimensions)))
    if any(existing.promise_id == pid for existing in space.promises):
        return
    space.promises.append(BehaviorPromise(
        promise_id=pid,
        entity=_entity(entity),
        invariant=str(invariant).strip(),
        surfaces=sorted(set(surfaces)),
        dimensions=sorted(set(dimensions)),
        source=source,
        confidence=confidence,
        source_refs=list(refs or []),
    ))


def _classify_columns(columns: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        "tenant": [], "owner": [], "soft_delete": [], "audit": [], "money": [],
        "quantity": [], "state": [], "time": [], "identity": [], "foreign_key": [], "unique": [],
    }
    for raw in columns:
        key = _field_key(raw)
        if not key:
            continue
        if key == "id" or key.endswith("_id"):
            result["identity"].append(raw)
        if key in _TENANT_FIELDS or "tenant" in key:
            result["tenant"].append(raw)
        if key in _OWNER_FIELDS or key.endswith("owner"):
            result["owner"].append(raw)
        if key in _SOFT_DELETE_FIELDS or "deleted" in key:
            result["soft_delete"].append(raw)
        if key in _AUDIT_FIELDS or key.endswith("_by") or key.endswith("_at"):
            result["audit"].append(raw)
        if key in _MONEY_FIELDS or any(token in key for token in ("amount", "price", "balance", "fee", "pay")):
            result["money"].append(raw)
        if key in _QUANTITY_FIELDS or any(token in key for token in ("stock", "qty", "quantity", "inventory")):
            result["quantity"].append(raw)
        if key in _STATE_FIELDS or any(token in key for token in ("status", "state", "phase", "stage")):
            result["state"].append(raw)
        if key in _TIME_FIELDS or key.endswith("_time") or key.endswith("_date") or key.endswith("_until"):
            result["time"].append(raw)
        if key.endswith("_id") and key not in _TENANT_FIELDS and key not in _OWNER_FIELDS:
            result["foreign_key"].append(raw)
        if "unique" in key:
            result["unique"].append(raw)
    return {key: values for key, values in result.items() if values}


def _parse_create_tables(db_schema_text: str) -> list[dict[str, Any]]:
    text = str(db_schema_text or "")
    tables: list[dict[str, Any]] = []
    for match in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([A-Za-z0-9_\.]+)[`\"\]]?\s*\((.*?)\);", text, re.I | re.S):
        table = match.group(1).split(".")[-1]
        body = match.group(2)
        columns: list[str] = []
        unique_constraints: list[str] = []
        foreign_keys: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line:
                continue
            lowered = line.lower()
            if lowered.startswith(("constraint", "primary key", "foreign key", "unique", "key ", "index ")):
                if "unique" in lowered:
                    unique_constraints.append(line[:240])
                if "foreign key" in lowered or "references" in lowered:
                    foreign_keys.append(line[:240])
                continue
            col_match = re.match(r"[`\"\[]?([A-Za-z_][A-Za-z0-9_]*)[`\"\]]?\s+", line)
            if not col_match:
                continue
            column = col_match.group(1)
            columns.append(column)
            if " unique" in f" {lowered} ":
                unique_constraints.append(column)
            if " references " in f" {lowered} ":
                foreign_keys.append(column)
        tables.append({"table": table, "entity": _entity(table), "columns": columns, "unique": unique_constraints, "foreign_keys": foreign_keys})
    return tables


def _roles_from_accounts(accounts: Any) -> list[str]:
    roles: list[str] = []
    values = accounts.values() if isinstance(accounts, dict) else accounts
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("name") or item.get("email") or "").strip().lower()
        if role and role not in roles:
            roles.append(role)
    return roles


def _roles_from_text(text: str) -> list[str]:
    roles = []
    for token in ("admin", "administrator", "owner", "manager", "operator", "auditor", "user", "guest", "buyer", "seller", "qa", "财务", "管理员", "普通用户", "运营", "审核员"):
        if token.lower() in str(text or "").lower() and token not in roles:
            roles.append(token)
    return roles


def _ui_routes(ui_materials: Any) -> list[str]:
    text = ""
    if isinstance(ui_materials, str):
        text = ui_materials
    elif isinstance(ui_materials, dict):
        text = json.dumps(ui_materials, ensure_ascii=False, default=str)
    elif isinstance(ui_materials, list):
        text = json.dumps(ui_materials, ensure_ascii=False, default=str)
    routes = []
    for match in re.finditer(r"(?<![A-Za-z0-9])(/[A-Za-z0-9_./{}:@?=&%\-]+)", text):
        route = match.group(1).rstrip(".,;，。；")
        if route not in routes and len(route) <= 200:
            routes.append(route)
    return routes[:200]


def _surface_required_assets(surface_plan: list[str]) -> list[str]:
    assets: list[str] = []
    if "api" in surface_plan:
        assets.extend(["source_bound_api_catalog", "approved_base_url", "actor_credentials_or_token"])
    if "db" in surface_plan:
        assets.extend(["readonly_or_snapshot_db_connection", "schema_introspection"])
    if "ui" in surface_plan:
        assets.extend(["approved_ui_base_url", "browser_or_page_agent", "role_bound_ui_account"])
    if "async" in surface_plan:
        assets.extend(["event_log_or_message_queue_observation"])
    return sorted(set(assets))


def _probe(space: SystemBehaviorSpace, promise: BehaviorPromise) -> None:
    objective = f"Verify system promise for {promise.entity}: {promise.invariant}"
    oracle_intent = [f"promise_violation:{dimension}" for dimension in promise.dimensions]
    pid = _stable_id("probe", promise.promise_id, ",".join(promise.surfaces))
    if any(item.probe_id == pid for item in space.probe_candidates):
        return
    priority = min(0.98, 0.35 + 0.1 * len(promise.surfaces) + 0.08 * len(promise.dimensions) + float(promise.confidence) * 0.25)
    space.probe_candidates.append(ProbeCandidate(
        probe_id=pid,
        promise_id=promise.promise_id,
        entity=promise.entity,
        objective=objective,
        surface_plan=promise.surfaces,
        required_assets=_surface_required_assets(promise.surfaces),
        oracle_intent=oracle_intent,
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

    roles = sorted(set(_roles_from_accounts(accounts) + _roles_from_text(prd_text)))
    if roles:
        actor_obj = _object(space, "actor")
        actor_obj.surfaces.add("auth")
        actor_obj.roles.update(roles)
        _promise(space, "actor", "Role-bound actions must be authorized consistently across API, UI and data access.", ["auth", "api", "ui", "db"], ["role", "authorization", "cross_surface_consistency"], "role_catalog", 0.65)

    try:
        api_entities, api_states, endpoints = _api_facts(api_spec_text, _STATE_RE)
    except Exception:
        api_entities, api_states, endpoints = {}, {}, []
    for entity, refs in api_entities.items():
        obj = _object(space, entity)
        obj.surfaces.add("api")
        obj.source_refs.extend([dict(ref) for ref in refs if isinstance(ref, dict)])
        for state in api_states.get(entity, {}):
            obj.states.add(str(state))
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        method = str(endpoint.get("method") or "").upper()
        path = str(endpoint.get("path") or "").strip()
        entity = _entity(endpoint.get("entity") or path.strip("/").split("/")[0] or "system")
        if not path:
            continue
        obj = _object(space, entity)
        obj.surfaces.add("api")
        obj.api_paths.add(f"{method} {path}" if method else path)
        if method in _READ_METHODS:
            _promise(space, entity, f"Read endpoint {method} {path} must expose only data visible to the current actor, tenant and lifecycle state.", ["api"], ["visibility", "tenant", "role", "lifecycle"], "api_catalog", 0.55)
        if method in _WRITE_METHODS:
            _promise(space, entity, f"Write endpoint {method} {path} must preserve business invariants, authorization, idempotency and data consistency.", ["api", "db"], ["authorization", "idempotency", "data_consistency", "state", "side_effect"], "api_catalog", 0.62)

    try:
        db_entities, db_states, dependencies = _schema_facts(db_schema_text, _STATE_RE)
    except Exception:
        db_entities, db_states, dependencies = {}, {}, []
    for entity, refs in db_entities.items():
        obj = _object(space, entity)
        obj.surfaces.add("db")
        obj.source_refs.extend([dict(ref) for ref in refs if isinstance(ref, dict)])
        for state in db_states.get(entity, {}):
            obj.states.add(str(state))
    for table_info in _parse_create_tables(db_schema_text):
        entity = _entity(table_info["entity"])
        obj = _object(space, entity)
        obj.surfaces.add("db")
        obj.db_tables.add(str(table_info["table"]))
        groups = _classify_columns(list(table_info.get("columns") or []))
        if table_info.get("unique"):
            groups.setdefault("unique", []).extend([str(item) for item in table_info["unique"]])
        if table_info.get("foreign_keys"):
            groups.setdefault("foreign_key", []).extend([str(item) for item in table_info["foreign_keys"]])
        for group, values in groups.items():
            _add_fields(obj, group, values)
        if groups.get("tenant"):
            _promise(space, entity, f"Table {table_info['table']} tenant fields must prevent cross-tenant reads, writes and UI/API leakage.", ["db", "api", "ui"], ["tenant", "authorization", "visibility"], "db_schema", 0.72)
        if groups.get("soft_delete"):
            _promise(space, entity, f"Soft-deleted records in {table_info['table']} must not reappear through query, search, UI list or export surfaces.", ["db", "api", "ui"], ["lifecycle", "visibility", "data_consistency"], "db_schema", 0.7)
        if groups.get("audit"):
            _promise(space, entity, f"Mutations of {table_info['table']} must preserve auditability and correlation evidence.", ["db", "api", "log"], ["audit", "traceability", "side_effect"], "db_schema", 0.58)
        if groups.get("money"):
            _promise(space, entity, f"Money fields in {table_info['table']} must remain non-negative, conserved and consistent across API, DB and UI.", ["db", "api", "ui"], ["money", "conservation", "data_consistency"], "db_schema", 0.75)
        if groups.get("quantity"):
            _promise(space, entity, f"Quantity fields in {table_info['table']} must remain non-negative and concurrency-safe across operations.", ["db", "api"], ["quantity", "concurrency", "conservation"], "db_schema", 0.72)
        if groups.get("state"):
            _promise(space, entity, f"State fields in {table_info['table']} must follow allowed lifecycle transitions and cannot drift from API/UI state.", ["db", "api", "ui"], ["state", "lifecycle", "cross_surface_consistency"], "db_schema", 0.68)
        if groups.get("foreign_key"):
            _promise(space, entity, f"Foreign-key-like fields in {table_info['table']} must preserve parent/child consistency and avoid orphaned data.", ["db", "api"], ["referential_integrity", "data_consistency"], "db_schema", 0.63)
        if groups.get("unique"):
            _promise(space, entity, f"Unique constraints in {table_info['table']} must reject duplicates under retry and concurrency.", ["db", "api"], ["uniqueness", "idempotency", "concurrency"], "db_schema", 0.63)
    for child, parent, ref in dependencies if isinstance(dependencies, list) else []:
        _promise(space, child, f"{child} must remain consistent with parent object {parent} across lifecycle and data changes.", ["db", "api"], ["referential_integrity", "cross_entity_consistency"], "schema_dependency", 0.58, [ref] if isinstance(ref, dict) else [])

    for route in _ui_routes(ui_materials):
        entity = _entity(route.strip("/").split("/")[-1] or route.strip("/").split("/")[0] or "ui")
        obj = _object(space, entity)
        obj.surfaces.add("ui")
        obj.ui_routes.add(route)
        dims = ["visibility", "ui_contract"]
        if any(token in route.lower() for token in ("admin", "manage", "setting", "权限", "管理")):
            dims.extend(["role", "authorization"])
        _promise(space, entity, f"UI route {route} must expose actions and data consistently with role, state and backend contracts.", ["ui", "api"], dims, "ui_material", 0.55)

    # Cross-surface promises are the key upgrade: the model is not API/DB/UI silos.
    for entity, obj in list(space.objects.items()):
        if "api" in obj.surfaces and "db" in obj.surfaces:
            _promise(space, entity, "API-visible state and DB-persisted state must not drift after reads, writes, retries or failures.", ["api", "db"], ["cross_surface_consistency", "state", "data_consistency"], "surface_join", 0.7)
        if "ui" in obj.surfaces and "api" in obj.surfaces:
            _promise(space, entity, "UI-visible data, buttons and validation must match API behavior and authorization outcomes.", ["ui", "api"], ["ui_api_contract", "authorization", "validation"], "surface_join", 0.66)
        if "ui" in obj.surfaces and "db" in obj.surfaces:
            _promise(space, entity, "UI lists, details and exports must not reveal DB records outside lifecycle, tenant or role constraints.", ["ui", "db", "api"], ["visibility", "tenant", "lifecycle", "cross_surface_consistency"], "surface_join", 0.68)

    for promise in list(space.promises):
        _probe(space, promise)

    if not endpoints:
        space.coverage_gaps.append({"kind": "API_SURFACE_MISSING", "required_asset": "openapi_postman_har_or_gateway_route_log", "reason": "No source-bound API catalog; API/API-DB probes cannot execute."})
    if not _parse_create_tables(db_schema_text):
        space.coverage_gaps.append({"kind": "DB_SURFACE_MISSING", "required_asset": "database_schema_or_readonly_connection", "reason": "No schema/table map; DB-only and API-DB consistency probes are limited."})
    if not _ui_routes(ui_materials):
        space.coverage_gaps.append({"kind": "UI_SURFACE_MISSING", "required_asset": "ui_route_map_dom_snapshot_design_or_browser_entry", "reason": "No UI route/DOM material; UI behavior promises cannot be explored automatically."})
    if not roles:
        space.coverage_gaps.append({"kind": "ROLE_SURFACE_MISSING", "required_asset": "role_account_matrix_or_permission_matrix", "reason": "No role/actor catalog; authorization and tenant probes are incomplete."})

    return space
