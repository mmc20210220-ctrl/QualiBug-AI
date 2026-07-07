from __future__ import annotations

"""Phase92Q OpenAPI-derived snapshot observer planner.

Phase92P can only validate before/after invariants when the runtime loop has
useful observations.  Phase92Q expands the old single-resource snapshot into a
small, read-only observer set inferred strictly from customer input OpenAPI
materials: primary resource detail, business ledger/history, inventory/balance,
approval/workflow, idempotency projection, and tenant/ownership views.

The planner never reads oracle / ground-truth files.  It returns GET-only
requests that the grounded executor can run before and after a sandbox write.
"""

import re
from pathlib import Path
from typing import Any

from .auto_test_data_factory import load_openapi_from_input
from .real_id_resolver import infer_path_params, normalize_path_placeholders, path_has_placeholders

QUERY_SAFE_RE = re.compile(r"^[A-Za-z0-9_.:\-@]+$")

RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "conservation_probe": (
        "inventory", "stock", "sku", "warehouse", "balance", "account", "wallet",
        "quota", "points", "credit", "ledger", "journal", "transaction", "amount",
        "payment", "settlement", "流水", "库存", "余额", "额度", "积分", "账户",
    ),
    "state_transition_probe": (
        "status", "state", "transition", "history", "timeline", "audit", "event",
        "workflow", "approval", "record", "log", "订单", "审批", "状态", "流转", "历史",
    ),
    "ownership_scope_probe": (
        "tenant", "org", "owner", "user", "member", "scope", "permission", "role",
        "assignment", "mine", "my", "归属", "租户", "组织", "用户", "权限",
    ),
    "auth_boundary_probe": (
        "tenant", "org", "owner", "user", "member", "scope", "permission", "role",
        "profile", "session", "audit", "租户", "组织", "用户", "权限",
    ),
    "idempotency_replay_probe": (
        "idempotency", "business", "external", "event", "request", "callback", "order",
        "payment", "submit", "records", "list", "search", "流水", "事件", "请求", "业务单号",
    ),
    "async_external_event_probe": (
        "external", "event", "callback", "webhook", "message", "queue", "task", "job",
        "idempotency", "records", "事件", "回调", "消息", "任务",
    ),
}

COMMON_OBSERVER_KEYWORDS = (
    "detail", "record", "records", "list", "search", "history", "audit", "events",
    "ledger", "balance", "inventory", "stock", "status", "workflow", "approval",
    "tenant", "owner", "user", "account", "orders", "payments", "items",
)

ID_PARAM_HINT_RE = re.compile(r"(?:^|_)(?:id|uuid|code|no|number|key)$|(?:Id|ID|Uuid|Code|No|Number|Key)$")
TENANT_PARAM_RE = re.compile(r"tenant|org|company|owner|user|member|account", re.I)
SKU_PARAM_RE = re.compile(r"sku|stock|inventory|item|product|goods|warehouse", re.I)
BUSINESS_KEY_RE = re.compile(r"business|order|payment|external|event|request|idempotency|key|no|number|code", re.I)


def _paths(spec: dict[str, Any]) -> dict[str, Any]:
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    if not isinstance(paths, dict):
        return {}
    return {
        normalize_path_placeholders(str(path or "")): ops
        for path, ops in paths.items()
    }


def _canonical_suffix(path: str) -> str:
    p = normalize_path_placeholders(path)
    p = re.sub(r"^/api/v\d+(?:/[^/]+)?", "", p)
    return p or str(path or "")


def _path_tokens(path: str) -> list[str]:
    suffix = _canonical_suffix(path).lower()
    raw = [t for t in re.split(r"[^a-z0-9\u4e00-\u9fff]+", suffix) if t]
    return [t for t in raw if not (t.startswith("{") and t.endswith("}"))]


def _collection_prefix(path: str) -> str:
    parts = normalize_path_placeholders(path).strip("/").split("/")
    out: list[str] = []
    for part in parts:
        if part.startswith("{"):
            break
        if re.search(r"(?:transition|submit|pay|cancel|approve|complete|retry|refund|release|close|confirm|reject)$", part, re.I):
            break
        out.append(part)
    return "/" + "/".join(out) if out else ""


def _operation(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    ops = (_paths(spec).get(path) or {})
    op = ops.get(method.lower()) if isinstance(ops, dict) else None
    return op if isinstance(op, dict) else {}


def _query_parameters(op: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in op.get("parameters") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("in") or "").lower() == "query" and item.get("name"):
            names.append(str(item.get("name")))
    return list(dict.fromkeys(names))[:8]


def _risk_keywords(risk_type: str) -> tuple[str, ...]:
    return RISK_KEYWORDS.get(str(risk_type or ""), ())


def _param_value(name: str, primary_fixture_id: str, seed: str, probe: dict[str, Any]) -> str:
    lname = str(name or "").lower()
    cid = re.sub(r"[^a-z0-9_]+", "_", str(probe.get("candidate_id") or "probe").lower()).strip("_")
    if primary_fixture_id:
        return primary_fixture_id
    if SKU_PARAM_RE.search(lname):
        return f"qb_auto_sku_{cid}_{seed}"
    if TENANT_PARAM_RE.search(lname):
        return f"qb_auto_{lname}_{cid}_{seed}"
    if BUSINESS_KEY_RE.search(lname):
        return f"qb_auto_{lname}_{cid}_{seed}"
    if ID_PARAM_HINT_RE.search(str(name)):
        return f"qb_auto_{cid}_{seed}"
    return f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', lname).strip('_') or 'param'}_{cid}_{seed}"


def _observer_kind(path: str, risk_type: str) -> str:
    text = path.lower()
    if any(k in text for k in ("ledger", "journal", "transaction", "流水")):
        return "business_ledger_projection"
    if any(k in text for k in ("inventory", "stock", "warehouse", "sku", "库存")):
        return "inventory_projection"
    if any(k in text for k in ("balance", "wallet", "account", "quota", "points", "余额", "额度", "积分")):
        return "account_resource_projection"
    if any(k in text for k in ("approval", "workflow", "status", "state", "history", "audit", "events", "审批", "状态", "历史")):
        return "workflow_history_projection"
    if any(k in text for k in ("tenant", "org", "owner", "scope", "permission", "member", "租户", "组织", "归属")):
        return "tenant_ownership_projection"
    if not path_has_placeholders(path):
        if str(risk_type) in {"idempotency_replay_probe", "async_external_event_probe"}:
            return "idempotency_collection_projection"
        return "collection_projection"
    return "primary_resource_detail"


def _evidence_goal(kind: str, risk_type: str) -> str:
    if kind == "inventory_projection":
        return "observe inventory/stock conservation before and after the write"
    if kind == "account_resource_projection":
        return "observe balance/points/quota/amount conservation before and after the write"
    if kind == "business_ledger_projection":
        return "observe ledger/transaction side effects caused by the write"
    if kind == "workflow_history_projection":
        return "observe status/workflow/history changes caused by the write"
    if kind == "tenant_ownership_projection":
        return "observe tenant/owner/scope isolation before and after the write"
    if kind == "idempotency_collection_projection":
        return "observe duplicate side effects by comparing collection growth across replay"
    if str(risk_type) in {"ownership_scope_probe", "auth_boundary_probe"}:
        return "observe whether a boundary probe mutated protected business state"
    return "observe primary business object state before and after the write"


def _base_score(target_path: str, candidate_path: str, risk_type: str, query_names: list[str]) -> int:
    target_tokens = set(_path_tokens(target_path))
    cand_tokens = set(_path_tokens(candidate_path))
    score = len(target_tokens & cand_tokens) * 18
    target_collection = _collection_prefix(target_path)
    if target_collection and _canonical_suffix(candidate_path).startswith(_canonical_suffix(target_collection)):
        score += 28
    target_without_action = re.sub(r"/(?:transition|submit|pay|cancel|approve|complete|retry|refund|release|close|confirm|reject)$", "", _canonical_suffix(target_path), flags=re.I)
    if _canonical_suffix(candidate_path) == target_without_action:
        score += 55
    if path_has_placeholders(candidate_path):
        score += 12
    if not path_has_placeholders(candidate_path) and query_names:
        score += 10
    lower = candidate_path.lower()
    for kw in _risk_keywords(risk_type):
        if kw.lower() in lower:
            score += 14
    for name in query_names:
        lname = name.lower()
        if lname in target_path.lower() or BUSINESS_KEY_RE.search(lname) or TENANT_PARAM_RE.search(lname) or SKU_PARAM_RE.search(lname):
            score += 8
    if any(k in lower for k in COMMON_OBSERVER_KEYWORDS):
        score += 6
    return score


def _build_query(query_names: list[str], primary_fixture_id: str, seed: str, probe: dict[str, Any]) -> dict[str, str]:
    query: dict[str, str] = {}
    for name in query_names[:6]:
        value = _param_value(name, primary_fixture_id, seed, probe)
        if QUERY_SAFE_RE.match(str(value)):
            query[name] = value
    risk = str(probe.get("risk_type") or "")
    if risk in {"idempotency_replay_probe", "async_external_event_probe"}:
        query.setdefault("idempotency_key", f"qb_auto_idem_{seed}")
        query.setdefault("business_key", f"qb_auto_business_key_{seed}")
    if risk in {"ownership_scope_probe", "auth_boundary_probe"}:
        query.setdefault("tenant_id", f"qb_auto_tenant_a_{seed}")
    return query


def _dedupe_observers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        query_tuple = tuple(sorted((str(k), str(v)) for k, v in (item.get("query") or {}).items()))
        key = (str(item.get("method") or "GET").upper(), str(item.get("path") or ""), query_tuple)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out



def _kind_priority(kind: str, risk_type: str) -> int:
    risk = str(risk_type or "")
    table: dict[str, dict[str, int]] = {
        "conservation_probe": {
            "primary_resource_detail": 100,
            "inventory_projection": 96,
            "account_resource_projection": 95,
            "business_ledger_projection": 94,
            "workflow_history_projection": 70,
            "collection_projection": 65,
        },
        "state_transition_probe": {
            "primary_resource_detail": 100,
            "workflow_history_projection": 96,
            "tenant_ownership_projection": 78,
            "business_ledger_projection": 72,
            "collection_projection": 65,
        },
        "ownership_scope_probe": {
            "primary_resource_detail": 100,
            "tenant_ownership_projection": 96,
            "collection_projection": 78,
            "workflow_history_projection": 70,
        },
        "auth_boundary_probe": {
            "primary_resource_detail": 100,
            "tenant_ownership_projection": 96,
            "collection_projection": 78,
            "workflow_history_projection": 70,
        },
        "idempotency_replay_probe": {
            "idempotency_collection_projection": 100,
            "primary_resource_detail": 94,
            "business_ledger_projection": 86,
            "collection_projection": 82,
            "workflow_history_projection": 70,
        },
        "async_external_event_probe": {
            "idempotency_collection_projection": 100,
            "workflow_history_projection": 92,
            "business_ledger_projection": 86,
            "primary_resource_detail": 84,
            "collection_projection": 82,
        },
    }
    return (table.get(risk) or {}).get(kind, 50)


def _select_observers(items: list[dict[str, Any]], risk_type: str, max_observers: int) -> list[dict[str, Any]]:
    ordered = sorted(items, key=lambda x: (-_kind_priority(str(x.get("observer_kind") or ""), risk_type), -int(x.get("score") or 0), str(x.get("path") or "")))
    if max_observers <= 0:
        return ordered
    selected: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()
    for item in ordered:
        kind = str(item.get("observer_kind") or "")
        if kind in seen_kinds:
            continue
        selected.append(item)
        seen_kinds.add(kind)
        if len(selected) >= max_observers:
            return selected
    for item in ordered:
        if item in selected:
            continue
        selected.append(item)
        if len(selected) >= max_observers:
            break
    return selected

def plan_snapshot_observers_for_probe(
    probe: dict[str, Any],
    *,
    input_dir: str | Path | None = None,
    spec: dict[str, Any] | None = None,
    primary_fixture_id: str = "",
    seed: str = "",
    max_observers: int = 5,
) -> dict[str, Any]:
    """Plan read-only before/after observer requests for a sandbox write probe.

    The return payload is intentionally serializable so it can be embedded in the
    auto-fixture bundle and execution report.
    """
    openapi = spec if isinstance(spec, dict) else load_openapi_from_input(input_dir)
    ep = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    target_path = str(ep.get("path") or "")
    risk_type = str(probe.get("risk_type") or "")
    seed = re.sub(r"[^A-Za-z0-9_]+", "_", seed or "phase92q")
    observers: list[dict[str, Any]] = []

    if not openapi or not target_path:
        return {
            "planner": "snapshot_observer_planner_v1_phase92q",
            "observers": [],
            "coverage": [],
            "note": "no OpenAPI paths available for snapshot observer planning",
        }

    for candidate_path, ops in _paths(openapi).items():
        if not isinstance(ops, dict) or "get" not in ops:
            continue
        path = normalize_path_placeholders(str(candidate_path))
        op = _operation(openapi, "GET", path)
        query_names = _query_parameters(op)
        score = _base_score(target_path, path, risk_type, query_names)
        if score < 24:
            continue
        path_params = {name: _param_value(name, primary_fixture_id, seed, probe) for name in infer_path_params(path)}
        kind = _observer_kind(path, risk_type)
        query = _build_query(query_names, primary_fixture_id, seed, probe)
        observers.append({
            "method": "GET",
            "path": path,
            "path_params": path_params,
            "query": query,
            "observer_kind": kind,
            "evidence_goal": _evidence_goal(kind, risk_type),
            "score": score,
            "source": "phase92q_openapi_snapshot_observer_planner",
        })

    observers = _dedupe_observers(observers)
    observers = _select_observers(observers, risk_type, max_observers)
    coverage = list(dict.fromkeys(str(o.get("observer_kind")) for o in observers if o.get("observer_kind")))
    return {
        "planner": "snapshot_observer_planner_v1_phase92q",
        "observers": observers,
        "coverage": coverage,
        "note": "auto-planned from OpenAPI GET observers for before/after business invariant evidence" if observers else "no suitable read-only observer endpoint discovered",
    }
