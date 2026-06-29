from __future__ import annotations

"""Automatic test-data factory for document-grounded probes.

Phase92O commercial rule
------------------------
Customers provide a test/staging URL and accounts.  QualiBug must create its own
``qb_auto_*`` test data, bind generated IDs into negative probes, capture
before/after snapshots when the target exposes read APIs, plan Phase92Q
read-only observer sets, and produce cleanup intent/receipts.  The factory only reads ``projects/<project>/input`` materials
(OpenAPI/API/requirements) and never reads oracle/ground_truth/BUG_MATRIX/seed or
answer files.
"""

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

BLOCKED_INPUT_PART_RE = re.compile(r"(?:oracle|ground[_-]?truth|bug[_-]?matrix|answer|solution|seed)", re.I)
PATH_PARAM_RE = re.compile(r"\{([^{}]+)\}")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
FIXTURE_BACKED_READ_RISKS = {
    "auth_boundary_probe",
    "anonymous_auth_boundary_probe",
    "cross_tenant_auth_boundary_probe",
    "role_downgrade_auth_boundary_probe",
}
MUTATION_FIELD_RE = {
    "resource": re.compile(r"(?:amount|price|balance|quota|point|credit|stock|inventory|quantity|qty|limit|total|积分|额度|余额|库存|金额|数量)", re.I),
    "tenant": re.compile(r"(?:tenant|org|owner|user|account|member|customer|object|resource|租户|组织|归属|用户)", re.I),
    "idempotency": re.compile(r"(?:idempotency|business[_-]?key|request[_-]?id|external[_-]?event[_-]?id|event[_-]?id|dedupe|幂等|业务键)", re.I),
    "state": re.compile(r"(?:status|state|stage|phase|from[_-]?status|target[_-]?status|状态)", re.I),
}
MUTATION_DEFAULT_FIELD = {
    "resource": "amount",
    "tenant": "tenant_id",
    "idempotency": "idempotency_key",
    "state": "status",
}


def _now_seed() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:6]


def _contains_blocked_path(path: Path, root: Path) -> bool:
    try:
        rel = str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        rel = str(path)
    return bool(BLOCKED_INPUT_PART_RE.search(rel))


def load_openapi_from_input(input_dir: str | Path | None) -> dict[str, Any]:
    if not input_dir:
        return {}
    root = Path(input_dir).resolve()
    if not root.exists():
        return {}
    for name in ("openapi.json", "swagger.json"):
        p = root / name
        if p.exists() and not _contains_blocked_path(p, root):
            try:
                return json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
            except Exception:
                return {}
    for name in ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"):
        p = root / name
        if p.exists() and not _contains_blocked_path(p, root):
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8", errors="replace") or "{}") or {}
            except Exception:
                return {}
    return {}


def _resolve_ref(ref: str, spec: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    cur: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(part.replace("~1", "/").replace("~0", "~"))
    return cur if isinstance(cur, dict) else {}


def _canonical_suffix(path: str) -> str:
    p = str(path or "")
    p = re.sub(r"^/api/v\d+(?:/[^/]+)?", "", p)
    return p or str(path or "")


def _path_tokens(path: str) -> list[str]:
    suffix = _canonical_suffix(path)
    return [t for t in re.split(r"/", suffix.strip("/")) if t and not t.startswith("{")]


def _collection_prefix(path: str) -> str:
    parts = str(path or "").strip("/").split("/")
    out: list[str] = []
    for part in parts:
        if part.startswith("{"):
            break
        if re.search(r"(?:transition|submit|pay|cancel|approve|complete|retry|refund|release|close)$", part, re.I):
            break
        out.append(part)
    return "/" + "/".join(out) if out else ""


def _spec_server_base_path(spec: dict[str, Any]) -> str:
    servers = spec.get("servers") if isinstance(spec, dict) else {}
    if not isinstance(servers, list):
        return ""
    for item in servers:
        if not isinstance(item, dict):
            continue
        raw_url = str(item.get("url") or "").strip()
        if not raw_url:
            continue
        parsed = urlparse(raw_url)
        path = str(parsed.path or "").strip()
        if path and path != "/":
            return path.rstrip("/")
    return ""


def _materialize_spec_path(spec: dict[str, Any], path: str) -> str:
    resolved = str(path or "").strip()
    if not resolved:
        return ""
    if "://" in resolved:
        return resolved
    if not resolved.startswith("/"):
        resolved = "/" + resolved
    base_path = _spec_server_base_path(spec)
    if not base_path:
        return resolved
    if resolved == base_path or resolved.startswith(base_path + "/"):
        return resolved
    return base_path + resolved


def _operation(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    if not isinstance(paths, dict):
        return {}
    op = (paths.get(path) or {}).get(method.lower())
    if isinstance(op, dict):
        return op
    suffix = _canonical_suffix(path)
    for candidate_path, ops in paths.items():
        if _canonical_suffix(str(candidate_path)) == suffix and isinstance(ops, dict):
            op = ops.get(method.lower())
            if isinstance(op, dict):
                return op
    return {}


def _schema_for_endpoint(spec: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    op = _operation(spec, method, path)
    content = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}) if isinstance(op, dict) else {}
    schema = content.get("schema") if isinstance(content, dict) else {}
    if isinstance(schema, dict) and schema.get("$ref"):
        return _resolve_ref(str(schema.get("$ref")), spec)
    return schema if isinstance(schema, dict) else {}


def _schema_value(name: str, schema: dict[str, Any], seed: str) -> Any:
    if not isinstance(schema, dict):
        return f"qb_auto_{name}_{seed}"
    if schema.get("$ref"):
        return f"qb_auto_{name}_{seed}"
    if "enum" in schema and isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    typ = schema.get("type")
    if not typ and "properties" in schema:
        typ = "object"
    lname = str(name or "value").lower()
    if typ == "object":
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = list(schema.get("required") or [])
        keys = list(dict.fromkeys(required + list(props.keys())[:10]))[:20]
        return {str(k): _schema_value(str(k), props.get(k) if isinstance(props.get(k), dict) else {"type": "string"}, seed) for k in keys} or {"name": f"qb_auto_object_{seed}"}
    if typ == "array":
        return [_schema_value(name + "_item", schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}, seed)]
    if typ in {"integer", "number"}:
        if any(x in lname for x in ("qty", "quantity", "count", "stock", "inventory")):
            return 1
        if any(x in lname for x in ("amount", "price", "balance", "total")):
            return 1
        if "version" in lname:
            return 1
        return 1
    if typ == "boolean":
        return True
    if "email" in lname:
        return f"qb-auto-{seed}@qualibug.local"
    if any(x in lname for x in ("phone", "mobile")):
        return "15500000000"
    if any(x in lname for x in ("date", "time")):
        return "2026-01-01T00:00:00Z"
    if "status" in lname or "state" in lname:
        return "qb_auto_state"
    if any(x in lname for x in ("id", "code", "key", "no", "number")):
        return f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', lname).strip('_')}_{seed}"
    if "name" in lname:
        return f"qb_auto_name_{seed}"
    return f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', lname).strip('_') or 'value'}_{seed}"



def _mutation_plan(probe: dict[str, Any]) -> dict[str, Any]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    mutation = plan.get("mutation") if isinstance(plan.get("mutation"), dict) else {}
    return mutation if isinstance(mutation, dict) else {}


def _materialize_mutation_value(raw_value: Any, selector: str, seed: str) -> Any:
    """Convert mutation placeholders into concrete qb_auto values.

    Probe planning may intentionally use symbolic placeholders such as
    ``<SAME_AS_PREVIOUS_ATTEMPT>``.  The runtime factory must turn them into
    deterministic disposable values so the probe actually exercises the
    intended boundary instead of silently falling back to a generic body.
    """
    if not isinstance(raw_value, str):
        return raw_value
    marker = raw_value.strip()
    if marker in {"<SAME_AS_PREVIOUS_ATTEMPT>", "<SAME_KEY_DIFFERENT_PAYLOAD>"}:
        return f"qb_auto_idempotency_key_{seed}"
    if marker == "qb_auto_tenant_b_foreign_object":
        return f"qb_auto_tenant_b_foreign_object_{seed}"
    if marker == "qb_auto_owner_b":
        return f"qb_auto_owner_b_{seed}"
    if marker == "":
        return ""
    return raw_value


def _set_matching_mutation_fields(value: Any, selector_re: re.Pattern[str], mutation_value: Any, prefix: str = "") -> list[str]:
    applied: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if selector_re.search(str(key)) and not isinstance(child, (dict, list)):
                value[key] = mutation_value
                applied.append(path)
                continue
            applied.extend(_set_matching_mutation_fields(child, selector_re, mutation_value, path))
    elif isinstance(value, list):
        for idx, child in enumerate(value[:20]):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            applied.extend(_set_matching_mutation_fields(child, selector_re, mutation_value, path))
    return applied[:20]


def _ensure_mutation_field(body: dict[str, Any], selector: str, mutation_value: Any) -> str:
    field = MUTATION_DEFAULT_FIELD.get(selector) or "qualibug_mutation_value"
    body[field] = mutation_value
    return field


def _apply_mutation_to_body(body: dict[str, Any], probe: dict[str, Any], seed: str) -> tuple[dict[str, Any], dict[str, Any]]:
    mutation = _mutation_plan(probe)
    if not mutation:
        return body, {"applied": False, "reason": "no_probe_plan_mutation"}
    selector = str(mutation.get("field_selector") or "").strip().lower()
    selector_re = MUTATION_FIELD_RE.get(selector)
    mutation_value = _materialize_mutation_value(mutation.get("value"), selector, seed)
    if not selector_re:
        return body, {
            "applied": False,
            "reason": f"unsupported_mutation_field_selector:{selector or 'missing'}",
            "mutation_kind": mutation.get("mutation_kind"),
        }
    applied_fields = _set_matching_mutation_fields(body, selector_re, mutation_value)
    fallback_fields: list[str] = []
    if not applied_fields:
        fallback_fields.append(_ensure_mutation_field(body, selector, mutation_value))
    body["qualibug_mutation_trace"] = {
        "mutation_kind": mutation.get("mutation_kind"),
        "field_selector": selector,
        "applied_fields": applied_fields or fallback_fields,
        "fallback_fields_added": fallback_fields,
    }
    return body, {
        "applied": True,
        "mutation_kind": mutation.get("mutation_kind"),
        "field_selector": selector,
        "requested_value": mutation.get("value"),
        "materialized_value_preview": mutation_value if isinstance(mutation_value, (int, float, bool)) else str(mutation_value)[:120],
        "applied_fields": applied_fields or fallback_fields,
        "fallback_fields_added": fallback_fields,
    }


def _risk_augmented_body(probe: dict[str, Any], seed: str, path_params: dict[str, Any]) -> dict[str, Any]:
    risk = str(probe.get("risk_type") or "")
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    object_id = str(next(iter(path_params.values()), f"qb_auto_object_{seed}"))
    body: dict[str, Any] = {}
    if risk in {"ownership_scope_probe", "auth_boundary_probe", "cross_tenant_auth_boundary_probe", "role_downgrade_auth_boundary_probe"}:
        body.update({
            "tenant_id": f"qb_auto_tenant_a_{seed}",
            "object_id": object_id,
            "owner_user_id": f"qb_auto_owner_b_{seed}",
            "org_id": f"qb_auto_org_a_{seed}",
        })
    if risk == "state_transition_probe":
        mutation = _mutation_plan(probe)
        terminal = mutation.get("value") if mutation.get("field_selector") == "state" and mutation.get("value") else (plan.get("terminal_states") or ["cancelled"])[0]
        body.update({
            "id": object_id,
            "object_id": object_id,
            "order_id": object_id,
            "from_status": terminal,
            "status": terminal,
            "target_status": "paid" if str(terminal).lower() != "paid" else "cancelled",
            "action": "submit",
        })
    if risk in {"idempotency_replay_probe", "async_external_event_probe"}:
        body.update({
            "id": object_id,
            "business_key": f"qb_auto_business_key_{seed}",
            "idempotency_key": f"qb_auto_idem_{seed}",
            "external_event_id": f"qb_auto_event_{seed}",
        })
    if risk == "conservation_probe":
        body.update({
            "id": object_id,
            "sku_id": object_id if "sku" in object_id.lower() else f"qb_auto_sku_{seed}",
            "quantity": 1,
            "amount": 1,
        })
    body.setdefault("qualibug_test_run_id", f"qb_auto_run_{seed}")
    return body


def _paths(spec: dict[str, Any]) -> dict[str, Any]:
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    return paths if isinstance(paths, dict) else {}


def _score_related_path(target: str, candidate: str) -> int:
    target_tokens = set(_path_tokens(target))
    cand_tokens = set(_path_tokens(candidate))
    score = len(target_tokens & cand_tokens) * 10
    cp = _collection_prefix(target)
    if cp and _canonical_suffix(candidate).startswith(_canonical_suffix(cp)):
        score += 20
    if PATH_PARAM_RE.search(candidate):
        score += 5
    return score


def _find_create_endpoint(spec: dict[str, Any], target_path: str) -> str:
    best: tuple[int, str] = (0, "")
    target_collection = _collection_prefix(target_path)
    for p, ops in _paths(spec).items():
        if not isinstance(ops, dict) or "post" not in ops:
            continue
        if PATH_PARAM_RE.search(str(p)):
            continue
        score = _score_related_path(target_path, str(p))
        if target_collection and _canonical_suffix(str(p)) == _canonical_suffix(target_collection):
            score += 50
        if re.search(r"/(create|new|seed|test)", str(p), re.I):
            score += 10
        if score > best[0]:
            best = (score, str(p))
    return best[1] if best[0] >= 20 else ""


def _find_read_endpoint(spec: dict[str, Any], target_path: str) -> str:
    best: tuple[int, str] = (0, "")
    for p, ops in _paths(spec).items():
        if not isinstance(ops, dict) or "get" not in ops:
            continue
        if not PATH_PARAM_RE.search(str(p)):
            continue
        score = _score_related_path(target_path, str(p))
        if _canonical_suffix(str(p)) == _canonical_suffix(target_path):
            score += 40
        if score > best[0]:
            best = (score, str(p))
    return best[1] if best[0] >= 15 else ""


def _find_delete_endpoint(spec: dict[str, Any], target_path: str) -> str:
    best: tuple[int, str] = (0, "")
    for p, ops in _paths(spec).items():
        if not isinstance(ops, dict) or "delete" not in ops:
            continue
        if not PATH_PARAM_RE.search(str(p)):
            continue
        score = _score_related_path(target_path, str(p))
        if _canonical_suffix(str(p)) == _canonical_suffix(target_path):
            score += 40
        if score > best[0]:
            best = (score, str(p))
    return best[1] if best[0] >= 15 else ""




def _fixture_backed_read_probe(probe: dict[str, Any], method: str, path: str) -> bool:
    if str(method or "").upper() not in READ_METHODS:
        return False
    if not PATH_PARAM_RE.search(str(path or "")):
        return False
    risk = str(probe.get("risk_type") or "")
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    return risk in FIXTURE_BACKED_READ_RISKS or isinstance(plan.get("auth_boundary"), dict)

def _bind_path_params(path: str, generated_id: str) -> dict[str, str]:
    return {name: generated_id for name in PATH_PARAM_RE.findall(path)}


def _make_setup_body(spec: dict[str, Any], create_path: str, seed: str, generated_id: str, probe: dict[str, Any]) -> dict[str, Any]:
    schema = _schema_for_endpoint(spec, "POST", create_path)
    body = _schema_value("fixture_body", schema, seed) if schema else {}
    if not isinstance(body, dict):
        body = {"value": body}
    # Give the target every common ID/name field it might accept.  Test targets
    # commonly accept client-generated IDs in disposable fixtures; if they ignore
    # these fields, response IDs are still captured in receipts.
    body.update({
        "id": generated_id,
        "object_id": generated_id,
        "order_id": generated_id,
        "sku_id": generated_id if str(probe.get("risk_type")) == "conservation_probe" else body.get("sku_id", generated_id),
        "name": f"qb_auto_fixture_{seed}",
        "status": "cancelled" if str(probe.get("risk_type")) == "state_transition_probe" else body.get("status", "active"),
        "qualibug_test_run_id": f"qb_auto_run_{seed}",
    })
    return body


def build_auto_fixture_for_probe(probe: dict[str, Any], *, input_dir: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return concrete synthetic test data and lifecycle obligations for a probe.

    The bundle includes request body/path params, optional setup requests (create
    disposable ``qb_auto`` objects), inferred before/after snapshot reads, cleanup
    intent, and a receipt.  It is still subject to executor production guards.
    """
    cfg = config or {}
    ep = probe.get("endpoint") or {}
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    cid = str(probe.get("candidate_id") or "probe")
    seed = re.sub(r"[^A-Za-z0-9_]+", "_", f"{cid}_{_now_seed()}")
    spec = load_openapi_from_input(input_dir or cfg.get("input_dir") or cfg.get("project_input_dir"))

    generated_id = f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', cid.lower()).strip('_')}_{uuid.uuid4().hex[:8]}"
    path_params = _bind_path_params(path, generated_id)
    schema = _schema_for_endpoint(spec, method, path)
    body = _schema_value("request_body", schema, seed) if schema else {}
    if not isinstance(body, dict):
        body = {"value": body}
    body.update(_risk_augmented_body(probe, seed, path_params))
    body, mutation_application = _apply_mutation_to_body(body, probe, seed)

    headers: dict[str, str] = {}
    risk = str(probe.get("risk_type") or "")
    if risk in {"idempotency_replay_probe", "async_external_event_probe"}:
        headers["Idempotency-Key"] = f"qb_auto_idem_{seed}"
    if risk == "ownership_scope_probe":
        headers.setdefault("X-Tenant-Id", f"qb_auto_tenant_a_{seed}")

    setup_requests: list[dict[str, Any]] = []
    cleanup_requests: list[dict[str, Any]] = []
    snapshots: dict[str, Any] = {"before": [], "after": [], "note": "no suitable OpenAPI read endpoint discovered"}
    observer_plan: dict[str, Any] = {"planner": "snapshot_observer_planner_v1_phase92q", "observers": [], "coverage": []}

    fixture_backed_read = _fixture_backed_read_probe(probe, method, path)
    if spec and (method in WRITE_METHODS or fixture_backed_read):
        create_path = _materialize_spec_path(spec, _find_create_endpoint(spec, path))
        read_path = path if method in READ_METHODS and PATH_PARAM_RE.search(path) else _materialize_spec_path(spec, _find_read_endpoint(spec, path))
        delete_path = _materialize_spec_path(spec, _find_delete_endpoint(spec, path))
        if create_path:
            setup_requests.append({
                "purpose": "create_disposable_qb_auto_fixture",
                "method": "POST",
                "path": create_path,
                "body": _make_setup_body(spec, create_path, seed, generated_id, probe),
                "bind_response_id_to": list(PATH_PARAM_RE.findall(path)) or ["id"],
            })

        # Phase92Q: plan multiple read-only observers instead of relying on only
        # the direct resource detail endpoint.  The older single read fallback is
        # preserved for compatibility if the planner cannot find anything.
        try:
            from .snapshot_observer_planner import plan_snapshot_observers_for_probe

            observer_plan = plan_snapshot_observers_for_probe(
                probe,
                spec=spec,
                primary_fixture_id=generated_id,
                seed=seed,
                max_observers=int((cfg.get("auto_fixture") or {}).get("max_snapshot_observers") or cfg.get("max_snapshot_observers") or 5),
            )
        except Exception as exc:  # pragma: no cover - defensive fallback for customer specs
            observer_plan = {"planner": "snapshot_observer_planner_v1_phase92q", "observers": [], "coverage": [], "error": f"{type(exc).__name__}: {exc}"}

        planned_observers = [r for r in (observer_plan.get("observers") or []) if isinstance(r, dict)]
        if planned_observers:
            snapshots = {
                "before": planned_observers,
                "after": planned_observers,
                "note": observer_plan.get("note") or "auto-planned from OpenAPI GET observers",
                "planner": observer_plan.get("planner"),
                "coverage": observer_plan.get("coverage") or [],
            }
        elif read_path:
            snapshot_req = {"method": "GET", "path": read_path, "path_params": _bind_path_params(read_path, generated_id), "observer_kind": "primary_resource_detail", "source": "phase92q_fallback_direct_read_endpoint"}
            snapshots = {"before": [snapshot_req], "after": [snapshot_req], "note": "auto-inferred from OpenAPI GET resource endpoint", "planner": observer_plan.get("planner"), "coverage": ["primary_resource_detail"]}
        if delete_path:
            cleanup_requests.append({"method": "DELETE", "path": delete_path, "path_params": _bind_path_params(delete_path, generated_id), "purpose": "cleanup_qb_auto_fixture"})

    return {
        "mode": "auto_generated_by_qualibug",
        "candidate_id": cid,
        "request_body": body,
        "path_params": path_params,
        "headers": headers,
        "mutation_application": mutation_application,
        "setup_requests": setup_requests,
        "snapshots": snapshots,
        "cleanup_requests": cleanup_requests,
        "receipt": {
            "generated_by": "QualiBug auto_test_data_factory",
            "seed": seed,
            "primary_fixture_id": generated_id,
            "input_openapi_used": bool(spec),
            "schema_used": bool(schema),
            "mutation_applied": bool(mutation_application.get("applied")),
            "mutation_kind": mutation_application.get("mutation_kind"),
            "mutation_applied_fields": mutation_application.get("applied_fields") or [],
            "setup_request_count": len(setup_requests),
            "snapshot_request_count": len(snapshots.get("before") or []) + len(snapshots.get("after") or []),
            "snapshot_observer_planner": observer_plan.get("planner"),
            "snapshot_observer_coverage": snapshots.get("coverage") or observer_plan.get("coverage") or [],
            "cleanup_request_count": len(cleanup_requests),
            "fixture_backed_read_probe": bool(fixture_backed_read),
            "customer_supplied_business_data_required": False,
        },
    }
