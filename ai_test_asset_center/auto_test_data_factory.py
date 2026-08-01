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
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .real_id_resolver import infer_path_params, normalize_path_placeholders, path_has_placeholders
from .openapi_spec_utils import (
    load_openapi_from_input,
    merge_openapi_specs as _merge_openapi_specs,
)

BLOCKED_INPUT_PART_RE = re.compile(r"(?:oracle|ground[_-]?truth|bug[_-]?matrix|answer|solution|seed)", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
FIXTURE_BACKED_READ_RISKS = {
    "auth_boundary_probe",
    "anonymous_auth_boundary_probe",
    "cross_tenant_auth_boundary_probe",
    "role_downgrade_auth_boundary_probe",
    "ownership_scope_probe",
}
MUTATION_FIELD_RE = {
    "resource": re.compile(r"(?:amount|price|balance|quota|point|credit|quantity|qty|limit|total|count|sum|金额|数量|总量|限额)", re.I),
    "tenant": re.compile(r"(?:tenant|org|owner|user|account|member|resource|租户|组织|归属|用户)", re.I),
    "idempotency": re.compile(r"(?:idempotency|business[_-]?key|request[_-]?id|external[_-]?event[_-]?id|event[_-]?id|dedupe|幂等|业务键)", re.I),
    "state": re.compile(r"(?:status|state|stage|phase|from[_-]?status|target[_-]?status|状态)", re.I),
}
MUTATION_DEFAULT_FIELD = {
    "resource": "amount",
    "tenant": "tenant_id",
    "idempotency": "idempotency_key",
    "state": "status",
}
SQL_CREATE_TABLE_RE = re.compile(r"CREATE TABLE\s+([A-Za-z_][\w]*)\s*\((.*?)\);", re.I | re.S)
SQL_REFERENCE_RE = re.compile(r"\bREFERENCES\s+([A-Za-z_][\w]*)\s*\(", re.I)
SQL_SKIP_COLUMN_PREFIX_RE = re.compile(r"^(?:constraint|primary\s+key|foreign\s+key|unique|check)\b", re.I)


def _now_seed() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:6]


def _contains_blocked_path(path: Path, root: Path) -> bool:
    try:
        rel = str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        rel = str(path)
    return bool(BLOCKED_INPUT_PART_RE.search(rel))


def _normalize_openapi_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return spec
    normalized_paths: dict[str, Any] = {}
    for raw_path, operations in paths.items():
        normalized_paths[normalize_path_placeholders(str(raw_path or ""))] = operations
    normalized = dict(spec)
    normalized["paths"] = normalized_paths
    return normalized


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
    p = normalize_path_placeholders(path)
    # Strip only protocol/version scaffolding.  Removing one additional path
    # segment erased the actual resource for normal ``/api/v1/orders`` APIs and
    # allowed cleanup matching to drift to unrelated resources.
    p = re.sub(r"^/api/v\d+", "", p)
    return p or str(path or "")


def _path_tokens(path: str) -> list[str]:
    suffix = _canonical_suffix(path)
    return [t for t in re.split(r"/", suffix.strip("/")) if t and not (t.startswith("{") and t.endswith("}"))]


def _collection_prefix(path: str) -> str:
    parts = normalize_path_placeholders(path).strip("/").split("/")
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
    normalized_path = normalize_path_placeholders(str(path or ""))
    op = (paths.get(normalized_path) or {}).get(method.lower())
    if isinstance(op, dict):
        return op
    # Runtime paths include the first concrete OpenAPI server base path while
    # ``spec.paths`` does not. Dematerialize it before any fuzzy suffix match;
    # otherwise a server such as ``/api/v1/orders`` makes every request-body
    # schema look absent at runtime.
    server_base = normalize_path_placeholders(_spec_server_base_path(spec)).rstrip("/")
    if server_base and (
        normalized_path == server_base or normalized_path.startswith(server_base + "/")
    ):
        source_path = normalized_path[len(server_base):] or "/"
        op = (paths.get(source_path) or {}).get(method.lower())
        if isinstance(op, dict):
            return op
    suffix = _canonical_suffix(normalized_path)
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


def _schema_value(name: str, schema: dict[str, Any], seed: str, spec: dict[str, Any] | None = None) -> Any:
    if not isinstance(schema, dict):
        return f"qb_auto_{name}_{seed}"
    # Resolve $ref to actual schema definition
    ref = schema.get("$ref")
    if ref and isinstance(ref, str):
        resolved = _resolve_ref(ref, spec if isinstance(spec, dict) else {})
        if isinstance(resolved, dict) and resolved != schema:
            return _schema_value(name, resolved, seed, spec)
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
        return {str(k): _schema_value(str(k), props.get(k) if isinstance(props.get(k), dict) else {"type": "string"}, seed, spec) for k in keys} or {"name": f"qb_auto_object_{seed}"}

    if typ == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}
        min_items = schema.get("minItems", 1)
        return [_schema_value(name + "_item", item_schema, f"{seed}_{i}", spec) for i in range(min(min_items, 3))]

    if typ in {"integer", "number"}:
        # Respect schema constraints: minimum/maximum/exclusive bounds/multipleOf
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if schema.get("exclusiveMinimum") is not None:
            minimum = schema["exclusiveMinimum"] + (1 if typ == "integer" else 0.01)
        if schema.get("exclusiveMaximum") is not None:
            maximum = schema["exclusiveMaximum"] - (1 if typ == "integer" else 0.01)
        multiple = schema.get("multipleOf")
        # Start from minimum if defined, else 1
        base = minimum if minimum is not None else 1
        if isinstance(base, (int, float)):
            if multiple and isinstance(multiple, (int, float)):
                base = ((int(base) if typ == "integer" else base) // multiple) * multiple
            # Clamp to maximum if defined
            if maximum is not None and base > maximum:
                base = maximum - (1 if typ == "integer" else 0.01)
        if typ == "integer":
            return max(0, int(base))
        return max(0.0, float(base))

    if typ == "boolean":
        return True

    # String type — check format attribute first, then name heuristics
    fmt = schema.get("format", "")
    if fmt in ("email",):
        return f"qb-auto-{seed}@qualibug.local"
    if fmt in ("uri", "url"):
        return f"https://qualibug.local/api/test/{seed}"
    if fmt in ("date",):
        return "2026-01-01"
    if fmt in ("date-time",):
        return "2026-01-01T00:00:00Z"
    if fmt in ("uuid",):
        return f"00000000-0000-0000-0000-{seed.zfill(12)[:12]}"
    if fmt in ("byte", "binary"):
        return f"YmFzZTY0X3tiYXNlNjR9"
    # Constraint-based string generation
    min_len = schema.get("minLength", 1)
    max_len = schema.get("maxLength", 255)
    pattern = schema.get("pattern", "")
    if isinstance(min_len, int) and min_len > 0:
        base_val = f"qb_auto_{seed}"
        if len(base_val) < min_len:
            base_val = base_val * ((min_len // len(base_val)) + 1)
        if isinstance(max_len, int) and len(base_val) > max_len:
            base_val = base_val[:max_len]
        return base_val
    # Name-based heuristics (used only when format is not set)
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
            "entity_id": object_id,
            "from_status": terminal,
            "status": terminal,
            "target_status": "active" if str(terminal).lower() != "active" else "inactive",
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
    if path_has_placeholders(candidate):
        score += 5
    return score


def _resource_path_tokens(path: str) -> tuple[str, ...]:
    """Return source path resource tokens without protocol/role scaffolding."""
    ignored = {
        "api", "rest", "rpc", "v1", "v2", "v3", "v4", "admin", "internal",
        "public", "private", "id", "uuid", "key", "code",
    }
    return tuple(
        token
        for token in _path_tokens(path)
        if token not in ignored and not re.fullmatch(r"v\d+", token)
    )


def _same_resource_surface(target_path: str, candidate_path: str) -> bool:
    """Require the same source-declared resource collection.

    Token intersection was too permissive here.  For example, ``/auth/.../users``
    and ``/users/.../balance`` both contain ``users`` and were therefore treated
    as one cleanup surface.  That allowed an unrelated PATCH to be selected as
    the cleanup for an authentication/status probe.  Compare the collection
    portion after removing only documented path scaffolding instead; action
    suffixes such as ``/cancel`` still share the collection with their resource.
    """
    target_collection = _collection_prefix(target_path)
    candidate_collection = _collection_prefix(candidate_path)
    target_tokens = _resource_path_tokens(target_collection)
    candidate_tokens = _resource_path_tokens(candidate_collection)
    if not target_tokens or not candidate_tokens:
        return False
    return target_tokens == candidate_tokens


def _find_create_endpoint(spec: dict[str, Any], target_path: str) -> str:
    best: tuple[int, str] = (0, "")
    target_collection = _collection_prefix(target_path)
    for p, ops in _paths(spec).items():
        if not isinstance(ops, dict) or "post" not in ops:
            continue
        if path_has_placeholders(str(p)):
            continue
        if not _same_resource_surface(target_path, str(p)):
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
        if not path_has_placeholders(str(p)):
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
        if not path_has_placeholders(str(p)):
            continue
        if not _same_resource_surface(target_path, str(p)):
            continue
        score = _score_related_path(target_path, str(p))
        if _canonical_suffix(str(p)) == _canonical_suffix(target_path):
            score += 40
        if score > best[0]:
            best = (score, str(p))
    return best[1] if best[0] >= 15 else ""


def _find_patch_cleanup_endpoint(spec: dict[str, Any], target_path: str) -> str:
    best: tuple[int, str] = (0, "")
    target_collection = normalize_path_placeholders(_collection_prefix(target_path)).rstrip("/")
    target_suffix = _canonical_suffix(target_path)
    for p, ops in _paths(spec).items():
        if not isinstance(ops, dict) or "patch" not in ops:
            continue
        if not path_has_placeholders(str(p)):
            continue
        if not _same_resource_surface(target_path, str(p)):
            continue
        candidate = normalize_path_placeholders(str(p)).rstrip("/")
        score = _score_related_path(target_path, str(p))
        if target_collection and candidate.startswith(target_collection + "/"):
            score += 25
        if _canonical_suffix(str(p)) == target_suffix:
            score += 50
        if score > best[0]:
            best = (score, str(p))
    return best[1] if best[0] >= 25 else ""


def _find_cleanup_endpoint(spec: dict[str, Any], target_path: str) -> tuple[str, str]:
    delete_path = _find_delete_endpoint(spec, target_path)
    if delete_path:
        return "DELETE", delete_path
    best: tuple[int, str, str] = (0, "", "")
    normalized_target = normalize_path_placeholders(target_path).rstrip("/")
    cleanup_action_re = re.compile(
        r"/(?:cancel|close|void|disable|archive|reject|release|rollback|revoke|remove|delete|deactivate|suspend|expire|invalidate|terminate|withdraw|abandon|discard|retire|freeze|reset|clear|purge|取消|删除|关闭|作废|停用|冻结|撤销)$",
        re.I,
    )
    for p, ops in _paths(spec).items():
        if not isinstance(ops, dict) or not path_has_placeholders(str(p)):
            continue
        if not _same_resource_surface(target_path, str(p)):
            continue
        if not cleanup_action_re.search(str(p)):
            continue
        for method in ("post", "patch"):
            if method not in ops:
                continue
            score = _score_related_path(target_path, str(p))
            if normalized_target and normalize_path_placeholders(str(p)).startswith(normalized_target + "/"):
                score += 35
            if score > best[0]:
                best = (score, method.upper(), str(p))
    if best[0] >= 20:
        return best[1], best[2]
    patch_path = _find_patch_cleanup_endpoint(spec, target_path)
    if patch_path:
        return "PATCH", patch_path
    return "", ""




def _fixture_backed_read_probe(probe: dict[str, Any], method: str, path: str) -> bool:
    if str(method or "").upper() not in READ_METHODS:
        return False
    risk = str(probe.get("risk_type") or "")
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    # ownership_scope reads need fixture seeding even on flat-list endpoints
    # (e.g. GET /api/orders) so the identity oracle has data to correlate.
    if risk == "ownership_scope_probe":
        return True
    # All other risks: keep the original gate requiring path placeholders.
    if not path_has_placeholders(str(path or "")):
        return False
    return risk in FIXTURE_BACKED_READ_RISKS or isinstance(plan.get("auth_boundary"), dict)

def _bind_path_params(path: str, generated_id: str) -> dict[str, str]:
    return {name: generated_id for name in infer_path_params(path)}


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
        else:
            merged[key] = value
    return merged


def _placeholder_body_value(name: str, seed: str, generated_id: str) -> str:
    lname = str(name or "").strip().lower()
    if not lname:
        return generated_id
    if "uuid" in lname:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"qualibug:{lname}:{seed}"))
    if any(token in lname for token in ("amount", "price", "total")):
        return "1"
    if any(token in lname for token in ("id", "uuid", "key", "code", "no", "number")):
        return generated_id
    return f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', lname).strip('_') or 'value'}_{seed}"


def _materialize_example_placeholders(value: Any, seed: str, generated_id: str, field_name: str = "") -> Any:
    if isinstance(value, dict):
        return {str(key): _materialize_example_placeholders(child, seed, generated_id, str(key)) for key, child in value.items()}
    if isinstance(value, list):
        return [_materialize_example_placeholders(child, seed, generated_id, field_name) for child in value]
    if not isinstance(value, str):
        return value

    # Keep source placeholders intact. Runtime binding may replace them only
    # after an exact source-declared resolver or a governed fixture dependency
    # has produced the concrete value. A generated UUID or benchmark sample is
    # not evidence for a related resource.
    return value


def _markdown_request_example(api_doc_text: str, method: str, path: str) -> dict[str, Any]:
    lines = str(api_doc_text or "").splitlines()
    if not lines:
        return {}
    target_method = str(method or "").upper()
    target_path = normalize_path_placeholders(path).strip()
    header_re = re.compile(r"^###\s+(GET|POST|PUT|PATCH|DELETE)\s+(\S+)", re.I)
    current_method = ""
    current_path = ""
    in_request_block = False
    in_json = False
    buffer: list[str] = []
    for raw in lines:
        line = str(raw or "")
        header = header_re.match(line.strip())
        if header:
            if in_json and current_method == target_method and current_path == target_path and buffer:
                try:
                    parsed = json.loads("\n".join(buffer))
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
            current_method = str(header.group(1) or "").upper()
            current_path = normalize_path_placeholders(str(header.group(2) or "").strip())
            in_request_block = False
            in_json = False
            buffer = []
            continue
        if current_method != target_method or current_path != target_path:
            continue
        stripped = line.strip()
        if stripped.startswith("响应") or "响应：" in stripped or "响应:" in stripped:
            # Keep scanning; response markers end the request block.
            if stripped.startswith("响应"):
                in_request_block = False
        request_marker = ""
        for prefix in ("请求：", "请求:", "请求", "Request Body:", "Request body:", "request body:"):
            if stripped.startswith(prefix) or f" {prefix}" in f" {stripped}":
                request_marker = prefix
                break
        if request_marker:
            in_request_block = True
            # Compact / enriched docs put JSON on the same line as 请求, sometimes
            # after a short description: ``预占库存。 请求 {"sku":"X"}``.
            inline = stripped
            at = inline.find(request_marker)
            if at >= 0:
                inline = inline[at + len(request_marker):].strip()
            if inline.startswith("{"):
                try:
                    parsed = json.loads(inline)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    pass
            if stripped.startswith(request_marker):
                continue
        if in_request_block and stripped.startswith("```json"):
            in_json = True
            buffer = []
            continue
        if in_request_block and not in_json and stripped.startswith("{"):
            # Bare JSON without code fences — common in doc stubs / test mocks.
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                pass
        if in_json and stripped.startswith("```"):
            try:
                parsed = json.loads("\n".join(buffer))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        if in_json:
            buffer.append(line)
    return {}


def build_source_grounded_request_body(
    api_doc_text: str,
    method: str,
    path: str,
) -> dict[str, Any]:
    """Build a deterministic request body and report its source provenance.

    Exact examples win. When a structured OpenAPI document has no example, a
    non-production test value is materialized from the documented schema. The
    provenance is returned alongside the body so runtime binding policy can
    distinguish an observed/example value from a schema-generated test value.
    """

    example = _markdown_request_example(api_doc_text, method, path)
    if isinstance(example, dict) and example:
        return {"body": example, "provenance": "documented_example"}

    parsed = _parse_structured_api_document(str(api_doc_text or ""))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("paths"), dict):
        return {"body": {}, "provenance": "not_available"}
    spec = _normalize_openapi_spec(parsed)
    operation = _operation(spec, method, path)
    if not operation:
        return {"body": {}, "provenance": "not_available"}

    content = (
        ((operation.get("requestBody") or {}).get("content") or {}).get("application/json")
        or {}
    )
    if not isinstance(content, dict):
        return {"body": {}, "provenance": "not_available"}
    content_example = content.get("example")
    if isinstance(content_example, dict) and content_example:
        return {"body": content_example, "provenance": "documented_example"}
    examples = content.get("examples")
    if isinstance(examples, dict):
        for item in examples.values():
            candidate = item.get("value") if isinstance(item, dict) else None
            if isinstance(candidate, dict) and candidate:
                return {"body": candidate, "provenance": "documented_example"}

    schema = _schema_for_endpoint(spec, method, path)
    if not schema:
        return {"body": {}, "provenance": "not_available"}
    schema_example = schema.get("example") if isinstance(schema, dict) else None
    if isinstance(schema_example, dict) and schema_example:
        return {"body": schema_example, "provenance": "documented_example"}
    seed = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"qualibug-runtime:{str(method or '').upper()}:{normalize_path_placeholders(path)}",
    ).hex[:12]
    generated = _schema_value("request", schema, seed, spec)
    return {
        "body": generated if isinstance(generated, dict) else {},
        "provenance": "documented_schema_generated",
    }


@lru_cache(maxsize=16)
def _parse_structured_api_document(api_doc_text: str) -> dict[str, Any]:
    """Parse immutable API source once per process, keyed by exact content."""

    try:
        parsed = yaml.safe_load(str(api_doc_text or ""))
    except yaml.YAMLError:
        # Markdown / prose API docs are handled by the exact-example parser
        # above and are not necessarily valid YAML documents.
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _request_example_body(api_doc_text: str, method: str, path: str, seed: str, generated_id: str) -> dict[str, Any]:
    example = _markdown_request_example(api_doc_text, method, path)
    if not isinstance(example, dict):
        return {}
    rendered = _materialize_example_placeholders(example, seed, generated_id)
    return rendered if isinstance(rendered, dict) else {}


def _source_fixture_body(
    spec: dict[str, Any],
    api_doc_text: str,
    method: str,
    path: str,
    seed: str,
    generated_id: str,
) -> tuple[dict[str, Any], str, set[str]]:
    """Return a fixture body only when the source declares its shape.

    A disposable write cannot be made safe by adding generic fields such as
    ``name`` or ``price``. The caller needs both the body provenance and the
    source placeholder names so it can fail closed when a documented body
    still depends on an unresolved related resource.
    """

    raw_example = _markdown_request_example(api_doc_text, method, path)
    placeholder_fields: set[str] = set()

    def collect_placeholders(value: Any, field_name: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                collect_placeholders(child, str(key))
            return
        if isinstance(value, list):
            for child in value:
                collect_placeholders(child, field_name)
            return
        if isinstance(value, str):
            placeholder_fields.update(
                str(match.group(1) or field_name).strip()
                for match in re.finditer(r"<([A-Za-z_]\w*)>", value)
                if str(match.group(1) or field_name).strip()
            )

    if isinstance(raw_example, dict) and raw_example:
        collect_placeholders(raw_example)
        return (
            _request_example_body(api_doc_text, method, path, seed, generated_id),
            "documented_example",
            placeholder_fields,
        )

    schema = _schema_for_endpoint(spec, method, path)
    if isinstance(schema, dict) and schema:
        schema_example = schema.get("example")
        if isinstance(schema_example, dict) and schema_example:
            collect_placeholders(schema_example)
            return dict(schema_example), "documented_example", placeholder_fields
        generated = _schema_value("fixture_body", schema, seed, spec)
        if isinstance(generated, dict) and generated:
            return generated, "documented_schema_generated", placeholder_fields

    return {}, "not_available", placeholder_fields


def _resource_identity_value(field: str, seed: str, generated_id: str) -> Any:
    lname = str(field or "").strip().lower()
    if "sku" in lname:
        return f"qb_auto_sku_{seed}"
    if any(token in lname for token in ("code", "no", "number", "key")):
        return f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', lname).strip('_') or 'code'}_{seed}"
    if any(token in lname for token in ("id", "uuid")):
        return generated_id
    return _placeholder_body_value(field, seed, generated_id)


def _resource_identity_defaults(path: str, seed: str, generated_id: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for name in infer_path_params(path):
        field = str(name or "").strip()
        if not field:
            continue
        value = _resource_identity_value(field, seed, generated_id)
        defaults.setdefault(field, value)
        camel = _snake_to_camel(field)
        if camel and camel != field:
            defaults.setdefault(camel, value)
    return defaults


def _make_setup_body(
    spec: dict[str, Any],
    create_path: str,
    seed: str,
    generated_id: str,
    probe: dict[str, Any],
    api_doc_text: str = "",
    *,
    target_path: str = "",
) -> dict[str, Any]:
    schema = _schema_for_endpoint(spec, "POST", create_path)
    body = _schema_value("fixture_body", schema, seed, spec) if schema else {}
    if not isinstance(body, dict):
        body = {"value": body}
    example = _request_example_body(api_doc_text, "POST", create_path, seed, generated_id)
    if example:
        body = _deep_merge_dict(body, example)
    # Layer auto-generated identity/inventory defaults UNDER the API doc example.
    # Use setdefault so the example's domain-specific fields (sku, qty, etc.)
    # survive — the generic IDs should never override what the customer spec says.
    if str(probe.get("risk_type")) == "state_transition_probe":
        body["status"] = "cancelled"
    for field, value in _resource_identity_defaults(target_path or create_path, seed, generated_id).items():
        body.setdefault(field, value)
    return body


def _cleanup_transition_body(spec: dict[str, Any], method: str, cleanup_path: str, seed: str, generated_id: str) -> dict[str, Any]:
    op_schema = _schema_for_endpoint(spec, method, cleanup_path)
    props = op_schema.get("properties") if isinstance(op_schema.get("properties"), dict) else {}
    terminal_values = (
        "DELETED",
        "CANCELLED",
        "CLOSED",
        "OFF_SALE",
        "DISABLED",
        "INACTIVE",
        "ARCHIVED",
        "REMOVED",
        "REJECTED",
        "VOID",
    )
    for field in ("status", "state", "phase", "stage"):
        prop = props.get(field) if isinstance(props, dict) else None
        if not isinstance(prop, dict):
            continue
        enum_values = [str(item) for item in (prop.get("enum") or []) if item not in (None, "")]
        for candidate in terminal_values:
            if candidate in enum_values:
                return {field: candidate}
        if enum_values:
            return {field: enum_values[-1]}
    return {"status": "DELETED"}


def _load_schema_text(input_dir: str | Path | None) -> str:
    if not input_dir:
        return ""
    root = Path(input_dir).resolve()
    if not root.exists():
        return ""
    chunks: list[str] = []
    for path in sorted(root.glob("*.sql")):
        if _contains_blocked_path(path, root):
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:1_000_000])
        except OSError:
            continue
    return "\n\n".join(chunks)


def _snake_to_camel(name: str) -> str:
    parts = [part for part in str(name or "").strip().split("_") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _parse_sql_tables(schema_text: str) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for match in SQL_CREATE_TABLE_RE.finditer(str(schema_text or "")):
        table_name = str(match.group(1) or "").strip().lower()
        body = str(match.group(2) or "")
        if not table_name or not body:
            continue
        columns: dict[str, dict[str, Any]] = {}
        foreign_keys: dict[str, str] = {}
        for raw_line in body.splitlines():
            line = str(raw_line or "").strip().rstrip(",")
            if not line or SQL_SKIP_COLUMN_PREFIX_RE.match(line):
                continue
            column_match = re.match(r"^([A-Za-z_][\w]*)\s+(.+)$", line)
            if not column_match:
                continue
            column_name = str(column_match.group(1) or "").strip().lower()
            definition = str(column_match.group(2) or "").strip()
            if not column_name or not definition:
                continue
            reference_match = SQL_REFERENCE_RE.search(definition)
            reference_table = str(reference_match.group(1) or "").strip().lower() if reference_match else ""
            column_meta = {
                "type": str(definition.split()[0] if definition.split() else "").strip().lower(),
                "definition": definition,
                "not_null": "NOT NULL" in definition.upper(),
                "has_default": "DEFAULT" in definition.upper(),
                "references": reference_table,
            }
            columns[column_name] = column_meta
            if reference_table:
                foreign_keys[column_name] = reference_table
        if columns:
            tables[table_name] = {"columns": columns, "foreign_keys": foreign_keys}
    return tables


def _infer_table_from_path(path: str, tables: dict[str, dict[str, Any]]) -> str:
    table_names = {str(name).lower() for name in tables.keys()}
    for token in reversed(_path_tokens(path)):
        candidate = str(token or "").strip().lower()
        if candidate in table_names:
            return candidate
    return ""


def _body_has_any_field(value: Any, aliases: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if _normalized_name(str(key)) in aliases:
                return True
            if _body_has_any_field(child, aliases):
                return True
    elif isinstance(value, list):
        return any(_body_has_any_field(child, aliases) for child in value[:20])
    return False


def _replace_body_fields(value: Any, aliases: set[str], replacement: Any) -> Any:
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, child in value.items():
            if _normalized_name(str(key)) in aliases and not isinstance(child, (dict, list)):
                rendered[str(key)] = replacement
            else:
                rendered[str(key)] = _replace_body_fields(child, aliases, replacement)
        return rendered
    if isinstance(value, list):
        return [_replace_body_fields(child, aliases, replacement) for child in value]
    return value


def _column_fixture_value(column_name: str, column_meta: dict[str, Any], seed: str) -> Any:
    lname = str(column_name or "").strip().lower()
    ctype = str((column_meta or {}).get("type") or "").strip().lower()
    definition = str((column_meta or {}).get("definition") or "")
    if lname in {"id", "created_at", "updated_at", "deleted_at", "paid_at", "cancelled_at"}:
        return None
    if (column_meta or {}).get("references"):
        return None
    if lname in {"status", "state", "phase", "stage"}:
        enum_match = re.search(r"\bIN\s*\(([^)]+)\)", definition, re.I)
        if enum_match:
            enum_values = [item.strip().strip("'\"") for item in str(enum_match.group(1) or "").split(",")]
            enum_values = [item for item in enum_values if item]
            if enum_values:
                return enum_values[0]
    if "bool" in ctype:
        return True
    if any(token in ctype for token in ("int", "numeric", "decimal", "float", "double", "real")):
        return 1
    if any(token in lname for token in ("phone", "mobile")):
        return "15500000000"
    if "email" in lname:
        return f"qb-auto-{seed}@qualibug.local"
    if any(token in lname for token in ("province", "state", "region")):
        return "qb_auto_region"
    if "city" in lname:
        return "qb_auto_city"
    if any(token in lname for token in ("detail", "address", "street")):
        return f"qb_auto_detail_{seed}"
    if any(token in lname for token in ("receiver", "contact", "consignee", "name")):
        return f"qb_auto_{lname}_{seed}"
    if "uuid" in ctype:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"qualibug:{lname}:{seed}"))
    return _placeholder_body_value(lname, seed, f"qb_auto_{lname}_{seed}")


def _dependency_setup_body(table: str, tables: dict[str, dict[str, Any]], seed: str) -> dict[str, Any]:
    table_meta = tables.get(str(table or "").lower()) if isinstance(tables, dict) else {}
    columns = table_meta.get("columns") if isinstance(table_meta, dict) else {}
    if not isinstance(columns, dict):
        return {}
    body: dict[str, Any] = {}
    for column_name, column_meta in columns.items():
        if not isinstance(column_meta, dict):
            continue
        required = bool(column_meta.get("not_null")) and not bool(column_meta.get("has_default"))
        if not required:
            continue
        value = _column_fixture_value(str(column_name), column_meta, seed)
        if value is None:
            continue
        body[str(column_name)] = value
        camel_name = _snake_to_camel(str(column_name))
        if camel_name and camel_name != column_name:
            body[camel_name] = value
    return body


def _api_prefix_candidates(path: str) -> list[str]:
    normalized = normalize_path_placeholders(path)
    match = re.match(r"^(/api(?:/v\d+)?)\b", normalized)
    prefixes = [str(match.group(1))] if match else []
    if "/api" not in prefixes:
        prefixes.append("/api")
    prefixes.append("")
    unique: list[str] = []
    for prefix in prefixes:
        if prefix not in unique:
            unique.append(prefix)
    return unique


def _resource_suffix_candidates(
    table_name: str,
    tables: dict[str, dict[str, Any]],
    *,
    with_id: bool,
    max_depth: int = 2,
    _visited: set[str] | None = None,
) -> list[str]:
    resource = str(table_name or "").strip().strip("/").lower()
    if not resource:
        return []
    visited = set(_visited or set())
    if resource in visited:
        return []
    visited.add(resource)
    base_suffix = f"/{resource}" + ("/{id}" if with_id else "")
    suffixes: list[str] = [base_suffix]
    if max_depth <= 0:
        return suffixes
    table_meta = tables.get(resource) if isinstance(tables, dict) else {}
    foreign_keys = table_meta.get("foreign_keys") if isinstance(table_meta, dict) else {}
    if not isinstance(foreign_keys, dict):
        return suffixes
    parent_tables = [str(parent or "").strip().lower() for parent in foreign_keys.values() if str(parent or "").strip()]
    for parent in dict.fromkeys(parent_tables):
        for parent_suffix in _resource_suffix_candidates(parent, tables, with_id=False, max_depth=max_depth - 1, _visited=visited):
            nested = f"{parent_suffix}/{resource}" + ("/{id}" if with_id else "")
            if nested not in suffixes:
                suffixes.append(nested)
    return suffixes


def _resource_path_candidates(reference_table: str, base_path: str, tables: dict[str, dict[str, Any]], *, with_id: bool) -> list[str]:
    paths: list[str] = []
    suffixes = _resource_suffix_candidates(reference_table, tables, with_id=with_id)
    ordered_suffixes = sorted(
        suffixes,
        key=lambda item: (-item.count("/"), 0 if item.startswith("/users/") else 1, item),
    )
    for prefix in _api_prefix_candidates(base_path):
        for suffix in ordered_suffixes:
            candidate = f"{prefix}{suffix}" if prefix else suffix
            normalized = normalize_path_placeholders(candidate)
            if normalized not in paths:
                paths.append(normalized)
    return paths


def _plan_fk_dependency_fixtures(
    *,
    input_dir: str | Path | None,
    create_path: str,
    seed: str,
    setup_body: dict[str, Any],
    target_body: dict[str, Any],
    path_params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    schema_text = _load_schema_text(input_dir)
    tables = _parse_sql_tables(schema_text)
    target_table = _infer_table_from_path(create_path, tables)
    if not target_table:
        return setup_body, target_body, [], []
    table_meta = tables.get(target_table) if isinstance(tables, dict) else {}
    foreign_keys = table_meta.get("foreign_keys") if isinstance(table_meta, dict) else {}
    if not isinstance(foreign_keys, dict):
        return setup_body, target_body, [], []
    current_setup = dict(setup_body)
    current_target = dict(target_body)
    setup_requests: list[dict[str, Any]] = []
    cleanup_requests: list[dict[str, Any]] = []
    target_defaults = _dependency_setup_body(target_table, tables, seed)
    for key, value in target_defaults.items():
        current_setup.setdefault(key, value)
    for column_name, reference_table in foreign_keys.items():
        bind_field = str(column_name or "").strip().lower()
        reference = str(reference_table or "").strip().lower()
        if not bind_field or not reference:
            continue
        aliases = {_normalized_name(bind_field)}
        camel_alias = _snake_to_camel(bind_field)
        if camel_alias:
            aliases.add(_normalized_name(camel_alias))
        if not _body_has_any_field(current_setup, aliases) and not _body_has_any_field(current_target, aliases):
            continue
        placeholder = f"qb_auto_ref_{bind_field}_{uuid.uuid4().hex[:8]}"
        path_params[bind_field] = placeholder
        current_setup = _replace_body_fields(current_setup, aliases, placeholder)
        current_target = _replace_body_fields(current_target, aliases, placeholder)
        dependency_body = _dependency_setup_body(reference, tables, seed)
        create_candidates = _resource_path_candidates(reference, create_path, tables, with_id=False)
        delete_candidates = _resource_path_candidates(reference, create_path, tables, with_id=True)
        if create_candidates:
            setup_requests.append(
                {
                    "purpose": f"create_dependency_fixture_{reference}",
                    "method": "POST",
                    "path": create_candidates[0],
                    "path_candidates": create_candidates,
                    "body": dependency_body,
                    "bind_response_id_to": [bind_field],
                }
            )
        if delete_candidates:
            cleanup_requests.append(
                {
                    "purpose": f"cleanup_dependency_fixture_{reference}",
                    "method": "DELETE",
                    "path": delete_candidates[0],
                    "path_candidates": delete_candidates,
                    "path_params": {"id": placeholder},
                }
            )
    return current_setup, current_target, setup_requests, cleanup_requests


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
    api_document_parse_diagnostics: list[dict[str, str]] = []
    source_diagnostics = spec.get("x-qualibug-diagnostics") if isinstance(spec, dict) else {}
    if isinstance(source_diagnostics, dict):
        for item in source_diagnostics.get("api_source_parse_failures") or []:
            if isinstance(item, dict):
                api_document_parse_diagnostics.append({
                    "source": str(item.get("source") or "input_asset"),
                    "code": str(item.get("code") or "API_SOURCE_PARSE_FAILED"),
                    "error_type": str(item.get("error_type") or "unknown"),
                })
    # Load raw API doc text for example extraction.  Check the config first,
    # then fall back to `api.md` on disk so md-only projects (benchmark) work.
    _api_doc_text = str(cfg.get("api_doc_text") or cfg.get("api_spec_text") or "").strip()
    _idr = str(input_dir or cfg.get("input_dir") or cfg.get("project_input_dir") or "").strip()
    if not _api_doc_text and _idr:
        _api_path = Path(_idr) / "api.md"
        if _api_path.exists():
            try:
                _api_doc_text = _api_path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception as exc:
                raise RuntimeError(
                    f"api_markdown_read_failed:{_api_path.name}:{type(exc).__name__}"
                ) from exc
    if _api_doc_text:
        from .universal_api_parser import parse_to_openapi

        try:
            document_spec = parse_to_openapi(_api_doc_text)
        except Exception as exc:
            # Inline API documentation enriches fixture planning, but a parser
            # failure must not terminate all already-grounded candidates.
            # The executor can still use structured sources, Markdown examples,
            # configured bodies, or block only the affected probe if required
            # data remains unavailable.
            document_spec = {}
            api_document_parse_diagnostics.append({
                "source": "inline_api_document",
                "code": "API_DOCUMENT_PARSE_FAILED",
                "error_type": type(exc).__name__,
            })
        if not isinstance(document_spec, dict):
            api_document_parse_diagnostics.append({
                "source": "inline_api_document",
                "code": "API_DOCUMENT_PARSER_RETURNED_NON_OBJECT",
                "error_type": type(document_spec).__name__,
            })
            document_spec = {}
        spec = _merge_openapi_specs(spec, document_spec)
    spec = _normalize_openapi_spec(spec)

    generated_id = f"qb_auto_{re.sub(r'[^a-z0-9_]+', '_', cid.lower()).strip('_')}_{uuid.uuid4().hex[:8]}"
    path_params = _bind_path_params(path, generated_id)
    schema = _schema_for_endpoint(spec, method, path)
    body = _schema_value("request_body", schema, seed, spec) if schema else {}
    if not isinstance(body, dict):
        body = {"value": body}
    example_body = _request_example_body(_api_doc_text, method, path, seed, generated_id)
    if example_body:
        body = _deep_merge_dict(body, example_body)
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
    fixture_setup_body_provenance = "not_available"
    fixture_setup_placeholder_fields: set[str] = set()
    fixture_setup_blocked_reason = ""
    fixture_cleanup_body_provenance = "not_applicable"
    snapshots: dict[str, Any] = {"before": [], "after": [], "note": "no suitable OpenAPI read endpoint discovered"}
    observer_plan: dict[str, Any] = {"planner": "snapshot_observer_planner_v1_phase92q", "observers": [], "coverage": []}

    fixture_backed_read = _fixture_backed_read_probe(probe, method, path)
    if spec and (method in WRITE_METHODS or fixture_backed_read):
        create_path = _materialize_spec_path(spec, _find_create_endpoint(spec, path))
        read_path = normalize_path_placeholders(path) if method in READ_METHODS and path_has_placeholders(path) else _materialize_spec_path(spec, _find_read_endpoint(spec, path))
        cleanup_method, cleanup_path = _find_cleanup_endpoint(spec, path)
        cleanup_path = _materialize_spec_path(spec, cleanup_path)
        if create_path:
            _, fixture_setup_body_provenance, fixture_setup_placeholder_fields = _source_fixture_body(
                spec,
                _api_doc_text,
                "POST",
                create_path,
                seed,
                generated_id,
            )
            if fixture_setup_body_provenance == "not_available":
                fixture_setup_blocked_reason = "CREATE_REQUEST_BODY_NOT_SOURCE_BOUND"
            primary_setup_body = _make_setup_body(
                spec,
                create_path,
                seed,
                generated_id,
                probe,
                _api_doc_text,
                target_path=path,
            )
            primary_setup_body, body, dependency_setup_requests, dependency_cleanup_requests = _plan_fk_dependency_fixtures(
                input_dir=input_dir or cfg.get("input_dir") or cfg.get("project_input_dir"),
                create_path=create_path,
                seed=seed,
                setup_body=primary_setup_body,
                target_body=body,
                path_params=path_params,
            )
            dependency_bindings = {
                str(field).strip()
                for request in dependency_setup_requests
                if isinstance(request, dict)
                for field in (request.get("bind_response_id_to") or [])
                if str(field).strip()
            }
            unresolved_placeholders = fixture_setup_placeholder_fields - dependency_bindings
            if unresolved_placeholders and not fixture_setup_blocked_reason:
                fixture_setup_blocked_reason = (
                    "FIXTURE_REQUEST_BODY_PLACEHOLDER_UNRESOLVED:"
                    + ",".join(sorted(unresolved_placeholders))
                )
            setup_requests.extend(dependency_setup_requests)
            setup_requests.append({
                "purpose": "create_disposable_qb_auto_fixture",
                "method": "POST",
                "path": create_path,
                "body": primary_setup_body,
                "bind_response_id_to": infer_path_params(path) or ["id"],
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
        if setup_requests and cleanup_method in {"PATCH", "PUT"} and cleanup_path:
            _, fixture_cleanup_body_provenance, _ = _source_fixture_body(
                spec,
                _api_doc_text,
                cleanup_method,
                cleanup_path,
                seed,
                generated_id,
            )
            if fixture_cleanup_body_provenance == "not_available" and not fixture_setup_blocked_reason:
                fixture_setup_blocked_reason = "CLEANUP_REQUEST_BODY_NOT_SOURCE_BOUND"
        if setup_requests and cleanup_method and cleanup_path:
            cleanup_request = {"method": cleanup_method, "path": cleanup_path, "path_params": _bind_path_params(cleanup_path, generated_id), "purpose": "cleanup_qb_auto_fixture"}
            if cleanup_method in {"PATCH", "PUT"}:
                cleanup_request["body"] = _cleanup_transition_body(spec, cleanup_method, cleanup_path, seed, generated_id)
            cleanup_requests.append(cleanup_request)
        elif setup_requests:
            # Keep the missing-cleanup obligation explicit.  Never fabricate a
            # cleanup URL: guessed resource paths are unsafe and non-portable.
            cleanup_requests.append({
                "method": "MANUAL",
                "path": "",
                "purpose": "cleanup_qb_auto_fixture_manual_required",
                "source_endpoint": path,
                "reason": "documented_cleanup_endpoint_missing",
                "note": "No automatic cleanup endpoint was found in the supplied API contract.",
            })
        if setup_requests:
            cleanup_requests.extend(dependency_cleanup_requests if 'dependency_cleanup_requests' in locals() else [])

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
            "input_openapi_used": bool(spec.get("paths")) if isinstance(spec, dict) else False,
            "api_document_parse_status": (
                "degraded"
                if api_document_parse_diagnostics
                else ("parsed" if _api_doc_text else "not_provided")
            ),
            "api_document_parse_diagnostics": api_document_parse_diagnostics,
            "schema_used": bool(schema),
            "fixture_setup_body_provenance": fixture_setup_body_provenance,
            "fixture_setup_placeholder_fields": sorted(fixture_setup_placeholder_fields),
            "fixture_setup_blocked_reason": fixture_setup_blocked_reason,
            "fixture_cleanup_body_provenance": fixture_cleanup_body_provenance,
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
