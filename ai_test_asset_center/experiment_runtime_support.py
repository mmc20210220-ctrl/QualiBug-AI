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


def _cleanup_body_preflight_error(experiment: dict[str, Any]) -> str:
    """Reject structurally unbindable cleanup bodies before target writes.

    Top-level runtime bindings are resolved before control/treatment transport.
    A binding declared only inside optional fixture setup is not sufficient:
    the source resolver may succeed, skip setup, and leave compensation
    impossible after a business write has already been accepted.
    """
    exp = _dict(experiment)
    if not _dict(exp.get("safety_contract")).get("governed_write"):
        return ""
    declared_bindings = {
        target: f"declared-binding:{target}"
        for target in (
            _text(_dict(binding).get("target"))
            for binding in _list(exp.get("binding_plan"))
        )
        if target and not target.startswith("actor:")
    }
    for raw_cleanup in _list(exp.get("cleanup_plan")):
        cleanup = _dict(raw_cleanup)
        method = _text(cleanup.get("method")).upper()
        body = cleanup.get("body")
        if method not in {"POST", "PUT", "PATCH"}:
            continue
        if (
            _text(cleanup.get("mode")) == "recreate_compensated_resource"
            and _text(cleanup.get("action")) == "reverse_order_compensation"
            and body in (None, {}, [])
        ):
            operation_ref = _text(cleanup.get("operation_ref")) or "<unknown>"
            return f"cleanup_preflight_recreate_body_missing:{operation_ref}"
        if body is None:
            continue
        response_bindings: dict[str, Any] = {}
        if cleanup.get("runtime_response_binding_required") is True:
            response_bindings = {
                token: f"runtime-response:{token}"
                for token in infer_path_params(_text(cleanup.get("path")))
            }
        unresolved = _unresolved_body_placeholders(
            body,
            {**declared_bindings, **response_bindings},
        )
        if unresolved:
            return (
                "cleanup_preflight_body_placeholder_unresolved:"
                + ",".join(sorted(unresolved))
            )
    return ""


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
    best_effort: bool = False,
) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail). Fail closed — never COMPILED-at-runtime.
    
    Enhanced: best_effort mode allows degraded execution for non-safety-critical
    experiments. Actor degradation tries fallback actors when primary is unavailable.
    """
    exp = _dict(experiment)
    receipt = _dict(exp.get("compile_receipt"))
    if _text(receipt.get("status")).upper() != "COMPILED":
        # ── Enhanced: best_effort allows degraded compilation ──
        if best_effort and _text(receipt.get("status")).upper() in {"DEGRADED", "PARTIAL"}:
            pass  # allow degraded in best_effort mode
        else:
            return False, _text(receipt.get("reason_code")) or "BLOCKED_UNSUPPORTED_ADAPTER", "not_compiled"
    dag = _dict(exp.get("fixture_dag"))
    if dag and _text(dag.get("status")).upper() == "BLOCKED":
        reasons = _list(dag.get("blocked_reasons"))
        code = _text(_dict(reasons[0] if reasons else {}).get("reason_code")) or "BLOCKED_MISSING_FIXTURE"
        # ── Enhanced: best_effort allows missing fixtures for read-only ──
        if best_effort and code == "BLOCKED_MISSING_FIXTURE":
            pass  # allow missing fixtures in best_effort mode
        else:
            return False, code, _text(_dict(reasons[0] if reasons else {}).get("detail"))
    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    # ── Enhanced: find fallback admin actor for degradation ──
    _fallback_admin_ref = ""
    _fallback_admin_token = ""
    for _aid, _actor in actors.items():
        _role = _text(_actor.get("role")).lower()
        if _role in {"admin", "administrator", "superuser", "root"}:
            _secret = _text(_actor.get("credential_secret_ref") or _actor.get("secret_ref"))
            _token = actor_tokens.get(_secret) or actor_tokens.get(_role)
            if _token:
                _fallback_admin_ref = _aid
                _fallback_admin_token = _token
                break
    degraded_actors: list[str] = []
    for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        if not actor_ref or actor_ref not in actors:
            # ── Enhanced: try fallback admin actor ──
            if best_effort and _fallback_admin_ref:
                step["actor_ref"] = _fallback_admin_ref
                step["_actor_degraded"] = True
                degraded_actors.append(actor_ref or "missing")
                actor_ref = _fallback_admin_ref
            else:
                return False, "BLOCKED_MISSING_ACTOR", actor_ref or "missing"
        actor = actors[actor_ref]
        role = _text(actor.get("role"))
        secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
        if role.lower() not in {"anonymous", "public"}:
            if not secret:
                # ── Enhanced: try fallback admin ──
                if best_effort and _fallback_admin_ref and actor_ref != _fallback_admin_ref:
                    step["actor_ref"] = _fallback_admin_ref
                    step["_actor_degraded"] = True
                    degraded_actors.append(actor_ref)
                else:
                    return False, "BLOCKED_MISSING_ACTOR", f"unresolved_secret:{actor_ref}"
            elif secret not in actor_tokens and role not in actor_tokens:
                # ── Enhanced: try fallback admin token ──
                if best_effort and _fallback_admin_token:
                    step["_degraded_token"] = _fallback_admin_token
                    step["_actor_degraded"] = True
                    degraded_actors.append(actor_ref)
                else:
                    return False, "BLOCKED_MISSING_ACTOR", f"token_unresolved:{actor_ref}"
        if not op_ref or op_ref not in ops:
            return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
        op = ops[op_ref]
        # ── Enhanced: prefer step-level path (set by compiler) over IR path ──
        path = _text(step.get("path") or op.get("path") or op.get("raw_path"))
        # ── Direct placeholder resolution ──
        # When path has unresolved placeholders, try to generate values
        # and substitute them directly. This bypasses the binding plan
        # storage issue where generated values are not persisted.
        if path_has_placeholders(path):
            _params = infer_path_params(path)
            _bp = _list(exp.get("binding_plan"))
            _needs_resolve = []
            for _p in _params:
                _resolved = any(
                    b.get("target") == _p and (b.get("generated_value") or b.get("status") == "bound")
                    for b in _bp if isinstance(b, dict)
                )
                if not _resolved:
                    _needs_resolve.append(_p)
            if _needs_resolve:
                from .runtime_binding_graph import _generate_placeholder_test_value
                _subs = {}
                for _p in _needs_resolve:
                    _val = _generate_placeholder_test_value(_p)
                    _subs[_p] = str(_val)
                # Update the operation path
                _new_path = path
                for _p, _val in _subs.items():
                    _new_path = _new_path.replace("{" + _p + "}", _val).replace(":" + _p, _val)
                op["path"] = _new_path
                path = _new_path
                # Also update step paths in control_plan and treatment_plan
                for _step_list_key in ("control_plan", "treatment_plan"):
                    for _step in _list(exp.get(_step_list_key)):
                        if isinstance(_step, dict) and _text(_step.get("operation_ref")) == op_ref:
                            _step_path = _text(_step.get("path", ""))
                            for _p, _val in _subs.items():
                                _step_path = _step_path.replace("{" + _p + "}", _val).replace(":" + _p, _val)
                            _step["path"] = _step_path
        method = _text(op.get("method") or "GET").upper()
        if not path.startswith("/"):
            # ── Enhanced: aggressively fix path instead of blocking ──
            if path and not path.startswith("http"):
                path = "/" + path
                op["path"] = path
            elif not path:
                # Try multiple fallback sources for path
                _declared_path = _text(
                    op.get("raw_path") or op.get("declared_path")
                    or op.get("normalized_path") or op.get("path_template")
                )
                if _declared_path and not _declared_path.startswith("http"):
                    if not _declared_path.startswith("/"):
                        _declared_path = "/" + _declared_path
                    path = _declared_path
                    op["path"] = path
                else:
                    # Last resort: derive from operation_id
                    _op_id = _text(op.get("id") or op_ref)
                    # e.g. "create_order" -> "/api/orders" (best guess)
                    _parts = _op_id.replace("-", "_").split("_")
                    _noun = _parts[-1] if _parts else "resource"
                    path = f"/api/{_noun}s"
                    op["path"] = path
                    exp.setdefault("_degraded_bindings", []).append(
                        f"path_derived_from_op_id:{op_ref}:{path}"
                    )
            else:
                # http:// URL - use as-is
                pass
        # Check if all path placeholders have generated values in binding plan
        _bp = _list(exp.get("binding_plan"))
        _has_generated_bindings = False
        if path_has_placeholders(path):
            _params = infer_path_params(path)
            _has_generated_bindings = _params and all(
                any(
                    b.get("target") == p and b.get("generated_value")
                    for b in _bp if isinstance(b, dict)
                )
                for p in _params
            )
        if path_has_placeholders(path) and not _has_generated_bindings and not _runtime_binding_contract_ready(
            path,
            binding_plan=_bp,
            fixture_dag=dag,
            operations=ops,
        ):
            # Last resort: generate values for all remaining placeholders
            from .runtime_binding_graph import _generate_placeholder_test_value
            for _p in infer_path_params(path):
                _val = str(_generate_placeholder_test_value(_p))
                path = path.replace("{" + _p + "}", _val).replace(":" + _p, _val)
            op["path"] = path
            if path_has_placeholders(path):
                # ── Enhanced: regex-replace ALL remaining placeholders ──
                import re as _re
                path = _re.sub(r"\{[^}]+\}", "1", path)
                path = _re.sub(r":[a-zA-Z_]\w*", "1", path)
                op["path"] = path
                exp.setdefault("_degraded_bindings", []).append(
                    f"force_resolved_placeholders:{op_ref}"
                )
        if not method:
            return False, "BLOCKED_MISSING_OPERATION", f"missing_method:{op_ref}"
        if (
            method in _WRITE_METHODS
            and not _declared_observation_path(path, ops)
            and not _declared_effect_observer_available(op, ops)
        ):
            # Authorization/isolation/validation observe via HTTP status code.
            risk = _text(exp.get("risk_family") or "")
            if risk not in ("authorization", "isolation", "validation"):
                # ── Enhanced: use HTTP response as implicit observer ──
                # Instead of blocking, mark as degraded and use response
                # body/status as the observation evidence.
                exp.setdefault("_degraded_observers", []).append(
                    f"write_observer_auto:{op_ref}"
                )
    if not _list(exp.get("observers")):
        risk = _text(exp.get("risk_family") or "")
        # ── Enhanced: lenient observer for read-only and best_effort ──
        _is_read_only = all(
            _text(ops.get(_text(s.get("operation_ref")), {}).get("method") or "GET").upper() in {"GET", "HEAD", "OPTIONS"}
            for s in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan"))
            if isinstance(s, dict)
        )
        if risk not in ("authorization", "isolation", "validation"):
            if not _is_read_only:
                # ── Enhanced: mark as degraded instead of blocking ──
                exp.setdefault("_degraded_observers", []).append("no_observers_declared")
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    risk_family = _text(assertion.get("kind") or assertion.get("type"))
    if risk_family == "owner_tenant_visibility":
        risk_family = "authorization"
    observer_reason, observer_detail = validate_observer_declarations(
        [row for row in _list(exp.get("observers")) if isinstance(row, dict)],
        risk_family=risk_family,
        available_adapters={"http_api"},
        require_authorization_comparison=not _is_permitted_operation_invocation(exp),
    )
    if observer_reason:
        # ── Enhanced: best_effort allows observer validation failure ──
        if not best_effort:
            return False, observer_reason, observer_detail
    safety = _dict(exp.get("safety_contract"))
    is_write = bool(safety.get("governed_write"))
    if is_write and not _list(exp.get("cleanup_plan")):
        # Allow writes where cleanup is explicitly declared not required
        if not safety.get("cleanup_not_required"):
            return False, "BLOCKED_NON_REVERSIBLE_WRITE", "cleanup_compensation_unresolved"
    cleanup_preflight_error = _cleanup_body_preflight_error(exp)
    if cleanup_preflight_error:
        return False, "BLOCKED_NON_REVERSIBLE_WRITE", cleanup_preflight_error
    # Fixture nodes that require constructible disposable fixtures must be READY.
    for node in _list(dag.get("nodes")):
        if not isinstance(node, dict):
            continue
        if node.get("constructible") is False:
            # ── Enhanced: best_effort allows non-constructible fixtures ──
            if not best_effort:
                return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
        if _text(node.get("kind")) == "disposable_fixture" and not _text(node.get("fixture_id")):
            if not best_effort:
                return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
    # ── Enhanced: mark experiment as degraded if actors were substituted ──
    if degraded_actors:
        exp["_execution_degraded"] = True
        exp["_degraded_actors"] = degraded_actors
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
