"""Runtime support helpers for experiment execution.

Path placeholders, actor tokens, preflight gates, binding resolution, and
single HTTP step transport. Extracted from experiment_executor so
execute_one_experiment / execute_selected_experiments stay the orchestration
surface.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .observer_contracts_base import validate_observer_declarations
from .real_id_resolver import (
    bind_entity_fields,
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    runtime_binding_contract_ready as _runtime_binding_contract_ready,
    runtime_setup_value_from_response as _runtime_setup_value_from_response,
)
from .runtime_binding_graph import declared_effect_observers
from .sandbox_write_executor import _http_request


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_BODY_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PERMITTED_OPERATION_INVOCATION = "permitted_operation_invocation"


def _is_permitted_operation_invocation(experiment: dict[str, Any]) -> bool:
    """True when compile already treated this experiment as permit-only.

    Permit-only reversible writes observe via ``http_response`` and must not be
    re-blocked at preflight solely for lacking an independent effect-read GET.
    """

    for assertion in _list(experiment.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        if _text(assertion.get("template")) == _PERMITTED_OPERATION_INVOCATION:
            return True
        if (
            _text(_dict(assertion.get("property")).get("template"))
            == _PERMITTED_OPERATION_INVOCATION
        ):
            return True
    for step in _list(experiment.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        if _text(step.get("intent")) == _PERMITTED_OPERATION_INVOCATION:
            return True
        if _text(step.get("property_template")) == _PERMITTED_OPERATION_INVOCATION:
            return True
    return False


def _unresolved_path_placeholders(path: str) -> list[str]:
    """Return path tokens that are still present after runtime materialization."""

    normalized = normalize_path_placeholders(path)
    if not path_has_placeholders(normalized):
        return []
    return list(dict.fromkeys(infer_path_params(normalized)))


def _unresolved_body_placeholders(
    value: Any,
    bindings: dict[str, Any],
) -> list[str]:
    """Return source body tokens that remain unbound before a write."""

    unresolved: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, str):
            return
        match = _BODY_PLACEHOLDER_RE.match(node)
        if not match:
            return
        token = _text(match.group(1))
        if token and bindings.get(token) not in (None, "", [], {}):
            return
        if token and token not in unresolved:
            unresolved.append(token)

    walk(value)
    return unresolved


def _select_fixture_actor(
    fixture_setup: dict[str, Any],
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    """Select a declared fixture actor aligned with the experiment control.

    A fixture create operation may list several permitted actors.  Selecting
    the first actor is not semantically safe: the created resource can then be
    invisible to the control/treatment actors that the experiment is meant to
    compare.  Prefer the control actor, then treatment, but only when the
    source-declared fixture actor list contains that identity.  Fall back to
    the first executable declared actor when neither plan actor is allowed.
    """
    declared_refs = [
        _text(actor_ref)
        for actor_ref in _list(fixture_setup.get("actor_refs"))
        if _text(actor_ref)
    ]
    preferred_refs = [
        _text(_dict(step).get("actor_ref"))
        for step in [*control_plan, *treatment_plan]
        if isinstance(step, dict) and _text(_dict(step).get("actor_ref"))
    ]
    ordered_refs = list(dict.fromkeys([
        *[ref for ref in preferred_refs if ref in declared_refs],
        *declared_refs,
    ]))
    for actor_ref in ordered_refs:
        actor = actors.get(actor_ref) or {}
        token = _resolve_token(actor, tokens)
        if _text(actor.get("role")).lower() in {"anonymous", "public"} or token:
            return actor_ref, actor, token
    return "", {}, ""


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _observation_state(value: Any) -> dict[str, Any]:
    row = _dict(value)
    return {
        "status": int(row.get("status") or row.get("status_code") or 0),
        "body": row.get("body"),
    }


def _governance_audit_receipt_id(governed: dict[str, Any]) -> str:
    row = _dict(governed)
    audit_record = _dict(row.get("audit_record"))
    audit_path = _text(row.get("audit_path"))
    if not audit_record and not audit_path:
        return ""
    material = {
        "audit_record": audit_record,
        "audit_path": audit_path,
        "before_ref": _text(row.get("before_ref")),
        "after_ref": _text(row.get("after_ref")),
        "accepted": row.get("accepted") is True,
    }
    return "audit_" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:24]



def _body_contains_scalar(value: Any, expected: Any) -> bool:
    if isinstance(value, dict):
        return any(_body_contains_scalar(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_body_contains_scalar(child, expected) for child in value)
    return value == expected


def _index_by_id(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and _text(node.get("id")):
            out[_text(node.get("id"))] = node
    return out


def _documented_routes(operations: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for operation in operations.values():
        if not isinstance(operation, dict):
            continue
        method = _text(operation.get("method")).upper()
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if method and path.startswith("/"):
            routes.append({"method": method, "path": path})
    return routes


def _inverse_delta_cleanup_body(
    request_body: Any,
    *,
    delta_field: str = "",
) -> tuple[dict[str, Any], str]:
    if not isinstance(request_body, dict):
        return {}, "request_body_missing"
    target_key = _text(delta_field)
    matches = [
        (key, value)
        for key, value in request_body.items()
        if (
            (_text(key) == target_key if target_key else "".join(ch for ch in str(key).lower() if ch.isalnum()) == "delta")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    ]
    if len(matches) != 1:
        return {}, "delta_field_not_unique"
    key, value = matches[0]
    cleanup_body = dict(request_body)
    cleanup_body[key] = -value
    return cleanup_body, f"inverse_delta:{key}"


def load_actor_tokens(root: Path, project: str) -> dict[str, str]:
    """Map role / secret_ref → bearer token from declared test accounts only."""
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    tokens: dict[str, str] = {}
    rows: list[Any] = []
    if isinstance(payload, dict):
        rows = list(payload.get("accounts") or payload.get("actors") or payload.get("users") or [])
        if not rows:
            rows = [
                {**(value if isinstance(value, dict) else {}), "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict) and key not in {"schema", "schema_version", "meta"}
            ]
    elif isinstance(payload, list):
        rows = payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = _text(row.get("role") or row.get("name") or row.get("id"))
        account_ref = _text(row.get("account_ref") or row.get("name") or row.get("id") or row.get("email"))
        token = _text(row.get("token") or row.get("access_token") or row.get("jwt"))
        if not role or not token:
            continue
        status = _text(row.get("status") or row.get("account_status") or row.get("state") or "active").upper()
        if account_ref:
            tokens[account_ref] = token
            tokens[f"secret_ref:test_accounts:{account_ref}"] = token
        if status not in {"DISABLED", "LOCKED"}:
            tokens.setdefault(role, token)
            tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
            tokens.setdefault(f"secret_ref:context:{role}", token)
    return tokens


def preflight_experiment_executable(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail). Fail closed — never COMPILED-at-runtime."""
    exp = _dict(experiment)
    receipt = _dict(exp.get("compile_receipt"))
    if _text(receipt.get("status")).upper() != "COMPILED":
        return False, _text(receipt.get("reason_code")) or "BLOCKED_UNSUPPORTED_ADAPTER", "not_compiled"
    dag = _dict(exp.get("fixture_dag"))
    if dag and _text(dag.get("status")).upper() == "BLOCKED":
        reasons = _list(dag.get("blocked_reasons"))
        code = _text(_dict(reasons[0] if reasons else {}).get("reason_code")) or "BLOCKED_MISSING_FIXTURE"
        return False, code, _text(_dict(reasons[0] if reasons else {}).get("detail"))
    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        if not actor_ref or actor_ref not in actors:
            return False, "BLOCKED_MISSING_ACTOR", actor_ref or "missing"
        actor = actors[actor_ref]
        role = _text(actor.get("role"))
        secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
        if role.lower() not in {"anonymous", "public"}:
            if not secret:
                return False, "BLOCKED_MISSING_ACTOR", f"unresolved_secret:{actor_ref}"
            if secret not in actor_tokens and role not in actor_tokens:
                return False, "BLOCKED_MISSING_ACTOR", f"token_unresolved:{actor_ref}"
        if not op_ref or op_ref not in ops:
            return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
        op = ops[op_ref]
        path = _text(op.get("path") or op.get("raw_path"))
        method = _text(op.get("method") or "GET").upper()
        if not path.startswith("/"):
            return False, "BLOCKED_MISSING_BINDING", f"unresolved_path:{op_ref}:{path}"
        if path_has_placeholders(path) and not _runtime_binding_contract_ready(
            path,
            binding_plan=_list(exp.get("binding_plan")),
            fixture_dag=dag,
            operations=ops,
        ):
            return False, "BLOCKED_MISSING_BINDING", f"unresolved_path:{op_ref}:{path}"
        if not method:
            return False, "BLOCKED_MISSING_OPERATION", f"missing_method:{op_ref}"
        # Align with experiment compile: permit-only reversible writes may rely
        # on http_response alone. Non-permit writes still require a declared
        # effect-read observer path (fail closed).
        if (
            method in _WRITE_METHODS
            and not _is_permitted_operation_invocation(exp)
            and not _declared_observation_path(path, ops)
            and not _declared_effect_observer_available(op, ops)
        ):
            return False, "BLOCKED_MISSING_OBSERVER", f"write_observer:{op_ref}"
    if not _list(exp.get("observers")):
        return False, "BLOCKED_MISSING_OBSERVER", "none"
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    risk_family = _text(assertion.get("kind") or assertion.get("type"))
    if risk_family == "owner_tenant_visibility":
        risk_family = "authorization"
    observer_reason, observer_detail = validate_observer_declarations(
        [row for row in _list(exp.get("observers")) if isinstance(row, dict)],
        risk_family=risk_family,
        available_adapters={"http_api"},
    )
    if observer_reason:
        return False, observer_reason, observer_detail
    safety = _dict(exp.get("safety_contract"))
    is_write = bool(safety.get("governed_write"))
    if is_write and not _list(exp.get("cleanup_plan")):
        return False, "BLOCKED_NON_REVERSIBLE_WRITE", "cleanup_compensation_unresolved"
    # Fixture nodes that require constructible disposable fixtures must be READY.
    for node in _list(dag.get("nodes")):
        if not isinstance(node, dict):
            continue
        if node.get("constructible") is False:
            return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
        if _text(node.get("kind")) == "disposable_fixture" and not _text(node.get("fixture_id")):
            return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
    return True, "", ""


def _resolve_token(actor: dict[str, Any], tokens: dict[str, str]) -> str:
    role = _text(actor.get("role"))
    secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if role.lower() in {"anonymous", "public"}:
        return ""
    return tokens.get(secret) or tokens.get(role) or ""


def _request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    request_schema = _dict(_dict(operation).get("request_schema"))
    content = _dict(request_schema.get("content"))
    for media in content.values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return dict(example)
        examples = _dict(media.get("examples"))
        for row in examples.values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return dict(value)
    return {}


def _scalar_body_bindings(value: Any) -> dict[str, Any]:
    bindings: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (str, int, float, bool)) and child not in ("", None):
                    bindings.setdefault(_text(key), child)
                else:
                    walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return bindings


def _operation_for_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized_path = normalize_path_placeholders(path)
    for operation in operations.values():
        if not isinstance(operation, dict):
            continue
        candidate = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if candidate == normalized_path:
            return operation
    return {"path": path}


def _declared_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
    *,
    runtime_bindings: dict[str, Any] | None = None,
    request_body: Any = None,
) -> str:
    """Return a source-declared effect observer through the shared graph."""
    operation = _operation_for_observation_path(path, operations)
    observers = declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    binding_values = {
        **_scalar_body_bindings(_request_example(operation)),
        **_scalar_body_bindings(request_body),
        **(runtime_bindings or {}),
    }
    for observer in observers:
        materialized = _text(observer.get("path"))
        for name, value in binding_values.items():
            materialized = materialized.replace("{" + name + "}", quote(str(value), safe=""))
        if materialized.startswith("/") and not path_has_placeholders(materialized):
            return materialized
    return ""


def _declared_effect_observer_available(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> bool:
    return bool(
        declared_effect_observers(
            operation,
            behavior_ir={"operations": list(operations.values())},
            max_candidates=5,
        )
    )


def _response_bound_observation_path(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    write_body: Any,
) -> dict[str, str]:
    if not isinstance(write_body, (dict, list)):
        return {}
    observers = declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    for observer in observers:
        path = normalize_path_placeholders(_text(observer.get("path")))
        if not path.startswith("/") or not path_has_placeholders(path):
            continue
        values: dict[str, Any] = {}
        for name in infer_path_params(path):
            value = _runtime_setup_value_from_response(write_body, name)
            if value in (None, "", [], {}):
                values = {}
                break
            values[name] = value
        if not values:
            continue
        materialized = path
        for name, value in values.items():
            materialized = materialized.replace(
                "{" + name + "}",
                quote(str(value), safe=""),
            )
        if materialized.startswith("/") and not path_has_placeholders(materialized):
            return {
                "operation_ref": _text(observer.get("operation_ref")),
                "method": _text(observer.get("method")).upper() or "GET",
                "path": materialized,
                "path_template": path,
            }
    return {}


def _runtime_entity_candidates(value: Any) -> list[dict[str, Any]]:
    """Extract source-observed entity rows without assuming a domain schema."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("data", "result", "items", "records", "results", "list", "rows", "content"):
        child = value.get(key)
        if isinstance(child, list):
            rows.extend(row for row in child if isinstance(row, dict))
        elif isinstance(child, dict):
            rows.append(child)
    return rows or [value]


def _select_runtime_binding(
    body: Any,
    target_path: str,
    *,
    preferred_body: Any = None,
) -> dict[str, str]:
    """Choose an observed entity that can actually receive the planned write.

    A collection resolver must not blindly bind the first row when the source
    operation declares a state/value transition.  Prefer the first observed
    entity whose declared mutation fields differ from the planned request;
    otherwise preserve the canonical structural resolver result.
    """
    default = bind_entity_fields(body, target_path)
    desired = _scalar_body_bindings(preferred_body)
    if not default or not desired:
        return default
    target_params = infer_path_params(target_path) or ["id"]
    target_param = target_params[0]
    default_value = _text(default.get(target_param) or default.get("id"))
    if not default_value:
        return default
    for entity in _runtime_entity_candidates(body):
        identity = _text(
            entity.get(target_param)
            or entity.get("id")
            or entity.get("uuid")
            or entity.get("key")
        )
        if not identity:
            continue
        if any(
            field in entity
            and entity.get(field) != desired_value
            for field, desired_value in desired.items()
        ):
            selected = bind_entity_fields(entity, target_path)
            if selected.get(target_param) or selected.get("id"):
                return selected
    return default


def _run_http_step(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str,
    body: Any = None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    resp = _http_request(method, url, token=token, body=body)
    return {
        "method": method,
        "path": path,
        "status_code": int(resp.get("status") or 0),
        "body": resp.get("body"),
        "headers": resp.get("headers") or {},
        "duration_ms": resp.get("duration_ms"),
        "error": resp.get("error") or "",
        "raw": resp,
    }


_POOL_FIELD_HINTS = frozenset({
    "available",
    "remaining",
    "free",
    "balance",
    "quota",
    "stock",
})
_POOL_EXCLUDE_HINTS = frozenset({
    "locked",
    "held",
    "reserved",
    "safety",
    "min",
    "max",
    "delta",
})


def _normalized_field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")


def _positive_int_body_fields(body: dict[str, Any]) -> list[tuple[str, int]]:
    fields: list[tuple[str, int]] = []
    for key, value in body.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if int(value) != value or int(value) <= 0:
            continue
        fields.append((_text(key), int(value)))
    return fields


def _observed_numeric_fields(body: Any) -> dict[str, int]:
    values: dict[str, int] = {}
    for row in _runtime_entity_candidates(body):
        if not isinstance(row, dict):
            continue
        for key, child in row.items():
            if isinstance(child, bool) or not isinstance(child, (int, float)):
                continue
            if int(child) != child or int(child) < 0:
                continue
            normalized = _normalized_field_name(key)
            if (
                not normalized
                or normalized in {"id", "uuid", "key"}
                or normalized.endswith("_id")
            ):
                continue
            values[_text(key)] = int(child)
    return values


def _select_observed_capacity(
    observed: dict[str, int],
    body_key: str,
) -> tuple[str, int] | None:
    """Bind one observed pool field to a request quantity field, or None.

    Uses shared field tokens (e.g. qty↔available_qty) and prefers free-pool
    hints while excluding locked/held/reserved pools. Ambiguity fails closed.
    """

    body_token = _normalized_field_name(body_key)
    if not body_token or not observed:
        return None
    matched: list[tuple[str, int, bool]] = []
    for key, value in observed.items():
        tokens = {
            part
            for part in _normalized_field_name(key).split("_")
            if part
        }
        if body_token not in tokens and _normalized_field_name(key) != body_token:
            continue
        if tokens.intersection(_POOL_EXCLUDE_HINTS):
            continue
        matched.append((key, value, bool(tokens.intersection(_POOL_FIELD_HINTS))))
    preferred = [row for row in matched if row[2]]
    chosen = preferred or matched
    if len(chosen) != 1:
        return None
    return chosen[0][0], chosen[0][1]


def stress_concurrency_quantity_bodies(
    *,
    steps: list[dict[str, Any]],
    operations: dict[str, dict[str, Any]],
    runtime_bindings: dict[str, Any],
    base_url: str,
    actor_token: str,
) -> dict[str, Any]:
    """Scale shared positive quantity fields so concurrent writes can contend.

    Only mutates when every barrier participant shares one identical positive
    integer body field and a unique observed free-pool capacity field aligns by
    name. Never invents fields or scales ambiguous shapes (e.g. adjust deltas).
    """

    receipt: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "concurrency_quantity_stress_not_applicable",
    }
    if len(steps) < 2 or not _text(base_url):
        return receipt
    materialized_bodies: list[dict[str, Any]] = []
    operation_refs: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            return receipt
        op_ref = _text(step.get("operation_ref"))
        op = operations.get(op_ref) or {}
        operation_refs.add(op_ref)
        body = step.get("body") if "body" in step else op.get("request_example")
        body = _materialize_body_template(body, runtime_bindings)
        if not isinstance(body, dict) or not body:
            return receipt
        materialized_bodies.append(dict(body))
    if len(operation_refs) != 1:
        receipt["reason_code"] = "concurrency_quantity_stress_mixed_operations"
        return receipt
    quantity_fields = _positive_int_body_fields(materialized_bodies[0])
    if len(quantity_fields) != 1:
        receipt["reason_code"] = "concurrency_quantity_stress_quantity_field_not_unique"
        return receipt
    field_name, original_qty = quantity_fields[0]
    for body in materialized_bodies[1:]:
        other = _positive_int_body_fields(body)
        if other != quantity_fields:
            receipt["reason_code"] = "concurrency_quantity_stress_body_mismatch"
            return receipt
    op_ref = next(iter(operation_refs))
    op = operations.get(op_ref) or {}
    path_template = _text(op.get("path") or op.get("raw_path"))
    observation_path = _declared_observation_path(
        path_template,
        operations,
        runtime_bindings=runtime_bindings,
        request_body=materialized_bodies[0],
    )
    if not observation_path:
        receipt["reason_code"] = "concurrency_quantity_stress_observer_missing"
        return receipt
    observed = _run_http_step(
        base_url=base_url,
        method="GET",
        path=observation_path,
        token=actor_token,
    )
    if not (200 <= int(observed.get("status_code") or 0) < 300):
        receipt["reason_code"] = "concurrency_quantity_stress_observer_failed"
        receipt["observation_status_code"] = observed.get("status_code")
        return receipt
    capacity_match = _select_observed_capacity(
        _observed_numeric_fields(observed.get("body")),
        field_name,
    )
    if capacity_match is None:
        receipt["reason_code"] = "concurrency_quantity_stress_capacity_unresolved"
        return receipt
    capacity_field, capacity = capacity_match
    if capacity < 1:
        receipt["reason_code"] = "concurrency_quantity_stress_capacity_non_positive"
        return receipt
    participant_count = len(steps)
    stressed_qty = max(original_qty, (capacity // participant_count) + 1)
    if stressed_qty == original_qty:
        receipt["status"] = "UNCHANGED"
        receipt["reason_code"] = "concurrency_quantity_stress_already_contending"
        receipt.update({
            "field": field_name,
            "capacity_field": capacity_field,
            "capacity": capacity,
            "original_qty": original_qty,
            "stressed_qty": stressed_qty,
            "observation_path": observation_path,
        })
        return receipt
    for step, body in zip(steps, materialized_bodies):
        updated = dict(body)
        updated[field_name] = stressed_qty
        step["body"] = updated
    receipt.update({
        "status": "APPLIED",
        "reason_code": "concurrency_quantity_stress_applied",
        "field": field_name,
        "capacity_field": capacity_field,
        "capacity": capacity,
        "original_qty": original_qty,
        "stressed_qty": stressed_qty,
        "participant_count": participant_count,
        "observation_path": observation_path,
    })
    return receipt
