"""Runtime support helpers for experiment execution.

Path placeholders, actor tokens, preflight gates, binding resolution, and
single HTTP step transport. Extracted from experiment_executor so
execute_one_experiment / execute_selected_experiments stay the orchestration
surface.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .behavior_ir_core import _infer_operation_effect
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
from .runtime_onboarding_preflight import (
    run_environment_preflight as _run_environment_preflight,
)
from .sandbox_write_executor import _http_request
from .experiment_runtime_credentials import (
    _configured_credential_manager,
    _configured_credential_tokens,
    _credential_config_path,
    _jwt_expired,
    _login_declared_account,
    _parse_test_accounts_md,
    _parse_test_accounts_text,
    _persist_refreshed_account_tokens,
    _register_actor_token_aliases,
    _token_from_login_response,
    configured_runtime_accounts,
    load_actor_tokens,
)


_LOGGER = logging.getLogger(__name__)


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


_SECRET_REF_TEST_ACCOUNTS_RE = re.compile(
    r"^secret_ref:test_accounts:([^:\s]+)$"
)


def _resolve_body_credential_refs(
    value: Any,
    *,
    root: Any,
    project: str,
) -> Any:
    """Resolve the product's own credential references in request bodies.

    An account-state precondition treatment (仅 ACTIVE 用户可登录) marks the
    rejection arm's password with ``secret_ref:test_accounts:<email>`` — the
    same secret-reference convention the runtime actor catalog uses. The
    governed executor resolves the reference to the declared credential
    before transport, so the probe exercises the real non-ACTIVE account
    instead of a guessed password. Unresolvable references stay verbatim (the
    target then rejects them like any invalid credential); they are never
    replaced with fabricated values.
    """
    if isinstance(value, dict):
        return {
            key: _resolve_body_credential_refs(child, root=root, project=project)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_body_credential_refs(child, root=root, project=project)
            for child in value
        ]
    if not isinstance(value, str):
        return value
    match = _SECRET_REF_TEST_ACCOUNTS_RE.match(value.strip())
    if not match:
        return value
    account_ref = _text(match.group(1))
    try:
        from .experiment_runtime_credentials import _parse_test_accounts_md

        rows: list[dict[str, Any]] = []
        catalog_path = (
            Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
        )
        if catalog_path.exists():
            try:
                payload = json.loads(catalog_path.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                rows = list(
                    payload.get("accounts")
                    or payload.get("actors")
                    or payload.get("users")
                    or []
                )
                if not rows:
                    rows = [
                        {
                            **(child if isinstance(child, dict) else {}),
                            "account_ref": key,
                        }
                        for key, child in payload.items()
                        if isinstance(child, dict)
                        and key not in {"schema", "schema_version", "meta"}
                    ]
            elif isinstance(payload, list):
                rows = [row for row in payload if isinstance(row, dict)]
        if not rows:
            rows = [dict(row) for row in _parse_test_accounts_md(root, project)]
    except Exception:
        return value
    for row in rows:
        if _text(row.get("account_ref") or row.get("email")) != account_ref:
            continue
        credential = _text(
            row.get("password") or row.get("pass") or row.get("credential")
        )
        if credential:
            return credential
        break
    return value


def _missing_required_body_fields(
    request_body: Any,
    operation: dict[str, Any],
) -> list[str]:
    """Return required request-body field names absent/empty in ``request_body``.

    Reads the operation's declared request schema (OpenAPI-derived). This is the
    fail-fast guard for the funnel's worst loss segment: when the materialized
    body omits a field the target requires (e.g. ``sku``), the target returns a
    5xx instead of QualiBug surfacing the missing contract up front.

    Returns ``[]`` whenever no required fields are declared, so requests against
    a target whose contract is unknown are never blocked on a guessed contract.
    """

    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        # OpenAPI requestBody shape: content -> application/json -> schema.
        media = _dict(content.get("application/json"))
        schema = _dict(media.get("schema")) or schema
    required = [str(f) for f in _list(schema.get("required")) if _text(f)]
    if not required:
        return []
    body = request_body if isinstance(request_body, dict) else {}
    missing: list[str] = []
    for field in required:
        value = body.get(field)
        if value is None or value == "" or value == [] or value == {}:
            missing.append(field)
    return missing


# A foreign-key value that is one of these literals (case-insensitive) cannot
# reference a real entity: it is a placeholder/sentinel or a fabricated default
# that the target would reject with 500/404. Used by the FK guard.
_FK_FABRICATED_WORDS = frozenset({
    "null", "none", "undefined", "nil", "na", "n/a",
    "fake", "dummy", "unknown", "placeholder", "todo", "test", "xxx",
})
# Numeric fabricated defaults (as int or bare string) for FK ids.
_FK_FABRICATED_NUMBERS = frozenset({"0", "1"})
# Matches a placeholder token that survived materialization, e.g. "<user_id>" or
# "{order_id}" left embedded inside a scalar body value.
_FK_EMBEDDED_PLACEHOLDER_RE = re.compile(r"[<{][A-Za-z_][A-Za-z0-9_]*[>}]")


def _foreign_key_field_names(operation: dict[str, Any]) -> list[str]:
    """Return body field names declared as foreign keys in the operation schema.

    Only fields explicitly marked ``x-foreign-key: true`` in the request schema
    are returned, so non-reference fields are never blocked. When no foreign
    keys are declared (e.g. an OpenAPI target without the extension) the FK
    guard is a safe no-op — precision over recall: a target whose contract
    omits FK metadata is never blocked on a guess.
    """

    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        schema = _dict(media.get("schema")) or schema
    props = _dict(schema.get("properties"))
    return [
        str(name)
        for name, prop in props.items()
        if _dict(prop).get("x-foreign-key") is True
    ]


def _foreign_key_violations(
    request_body: Any,
    operation: dict[str, Any],
) -> list[str]:
    """Return foreign-key body fields whose value cannot reference a real entity.

    This is the fail-fast guard for the funnel's foreign-key loss segment
    (§8.4): a bound ``user_id`` / ``order_id`` / ``coupon_code`` that resolves to
    a placeholder, sentinel, or fabricated default (e.g. ``1``) will 500/404 at
    the target. We block such payloads up front with a visible reason instead of
    spending a request on a guaranteed failure — "avoid sending requests with
    fabricated IDs" (§8.5.3).

    Only contract-declared FK fields are inspected (see ``_foreign_key_field_names``),
    so absent optional FKs are left to the target and required FKs are already
    covered by the required-field guard. A full existence probe (GET the
    referenced row) is intentionally out of scope: it would inject network
    round-trips and could mask the binding-graph root cause.
    """

    fks = _foreign_key_field_names(operation)
    if not fks:
        return []
    body = request_body if isinstance(request_body, dict) else {}
    violations: list[str] = []
    for field in fks:
        if field not in body:
            continue
        value = body.get(field)
        if value is None or value == "" or value == [] or value == {}:
            violations.append(field)
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if _FK_EMBEDDED_PLACEHOLDER_RE.search(value):
                violations.append(field)
                continue
            if stripped.lower() in _FK_FABRICATED_WORDS or stripped in _FK_FABRICATED_NUMBERS:
                violations.append(field)
                continue
        if isinstance(value, (int, float)) and value in (0, 1):
            violations.append(field)
            continue
    return violations


def _unauthorized_actor_role(
    operation: Any,
    actor: Any,
) -> str | None:
    """Return the actor role that fails the operation's required-role check.

    Mirrors the foreign-key guard's precision rule: only a contract-declared
    role restriction blocks. Returns a normalized role label (e.g. ``"buyer"``
    or ``"missing_role"``) when the actor is NOT permitted on the operation,
    or ``None`` when the guard is a no-op (operation declares no required roles)
    or the actor's role is in the permitted set.

    This is the fail-fast guard for the funnel's 403 loss segment (§8.4/§8.5.4):
    54 envelope-observed 403s (e.g. ``PATCH /api/users/admin/.../balance``,
    ``POST /api/products/admin``) came from an actor whose role is not permitted
    on the target. We block before transport instead of letting the target
    return 403, which the funnel would misread as a discovery finding.
    """

    op = _dict(operation)
    act = _dict(actor)
    required = [
        str(r).lower()
        for r in _list(op.get("required_roles") or op.get("allowed_roles"))
    ]
    if not required:
        return None
    actor_role = _text(act.get("role")).lower()
    if not actor_role or actor_role not in required:
        return actor_role or "missing_role"
    return None


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




def preflight_experiment_executable(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail). Fail closed — never COMPILED-at-runtime.

    Strict mode: no actor substitution, no path guessing, no best-effort
    degradation. Missing actors/operations/bindings/observers are BLOCKED.
    """
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
            elif secret not in actor_tokens and role not in actor_tokens:
                return False, "BLOCKED_MISSING_ACTOR", f"token_unresolved:{actor_ref}"
        if not op_ref or op_ref not in ops:
            return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
        op = ops[op_ref]
        path = _text(step.get("path") or op.get("path") or op.get("raw_path"))
        # ── Placeholder interception: BLOCK if path has unresolved placeholders ──
        # Generating qb_test_* placeholder IDs guarantees 404/400 failures and
        # wastes compute. Block the experiment until real fixture data exists.
        if path_has_placeholders(path):
            _params = infer_path_params(path)
            _bp = _list(exp.get("binding_plan"))
            _pre_rb = _dict(exp.get("_pre_resolved_bindings"))
            _needs_resolve = []
            for _p in _params:
                # resolver_operations is the plan-side field the compilers emit
                # and the materializer consumes; resolver_operation_ref only
                # appears on a receipt after resolution has already run.
                _resolved = any(
                    b.get("target") == _p and (
                        b.get("status") == "bound"
                        or _list(b.get("resolver_operations"))
                        # fixture_create_only plans are runtime_resolvable with
                        # a source-declared create+cleanup and empty resolvers.
                        or (
                            _text(b.get("status")) == "runtime_resolvable"
                            and bool(_dict(b.get("fixture_setup")))
                        )
                    )
                    for b in _bp if isinstance(b, dict)
                )
                # Also accept batch-level pre-resolved bindings
                if not _resolved and _pre_rb.get(_p) not in (None, ""):
                    _resolved = True
                if not _resolved:
                    _needs_resolve.append(_p)
            if _needs_resolve:
                return False, "BLOCKED_MISSING_BINDING", f"unresolved_path_placeholders:{';'.join(_needs_resolve[:6])}"
        # ── Detect pre-compiled placeholder IDs (qb_test_*) in path ──
        if "qb_test_" in path or "QB-TEST-" in path:
            return False, "BLOCKED_MISSING_BINDING", f"placeholder_id_in_path:{path[:80]}"
        method = _text(op.get("method") or "GET").upper()
        if not path.startswith("/"):
            if path and not path.startswith("http"):
                path = "/" + path
                op["path"] = path
            elif not path:
                # Try declared alternative path fields from source
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
                    # No source-declared path available — block immediately.
                    return False, "BLOCKED_MISSING_OPERATION", f"source_declared_path_missing:{op_ref}"
            else:
                # http:// URL - use as-is
                pass
        # Check whether every placeholder has an exact source-observed binding.
        _bp = _list(exp.get("binding_plan"))
        _pre_rb = _dict(exp.get("_pre_resolved_bindings"))
        _has_materialized_bindings = False
        if path_has_placeholders(path):
            _params = infer_path_params(path)
            _has_materialized_bindings = _params and all(
                _pre_rb.get(p) not in (None, "")
                for p in _params
            )
        if path_has_placeholders(path) and not _has_materialized_bindings and not _runtime_binding_contract_ready(
            path,
            binding_plan=_bp,
            fixture_dag=dag,
            operations=ops,
        ):
            # ── Placeholder interception: BLOCK instead of generating fake IDs ──
            _unresolved = infer_path_params(path)
            return False, "BLOCKED_MISSING_BINDING", f"unresolvable_placeholders_last_resort:{';'.join(_unresolved[:6])}"
        if not method:
            return False, "BLOCKED_MISSING_OPERATION", f"missing_method:{op_ref}"
        # V1.6.1: honor compile-time readback resolvers on effect observers.
        # Runtime previously re-checked IR-only declared_effect_observers and ignored
        # resolver_operations/readback_contract_id already attached at compile, turning
        # COMPILED field-oracle experiments into BLOCKED_MISSING_OBSERVER:write_observer.
        _exp_has_compiled_effect_resolvers = any(
            isinstance(obs, dict)
            and _text(obs.get("observer_id"))
            in {
                "entity_state",
                "before_state",
                "after_state",
                "final_state",
                "business_effect",
            }
            and (
                bool(_list(obs.get("resolver_operations")))
                or bool(_text(obs.get("readback_contract_id")))
            )
            for obs in _list(exp.get("observers"))
        )
        if (
            _infer_operation_effect(op, method) == "write"
            and not _declared_observation_path(path, ops)
            and not _declared_effect_observer_available(op, ops)
            and not _exp_has_compiled_effect_resolvers
        ):
            # Response-only experiments (authorization, validation) assert on
            # HTTP status codes. Their write is expected to be rejected; no state
            # change occurs, so effect observation is unnecessary. Only block when
            # the experiment actually has effect observers that need evidence.
            _EFFECT_OBS_IDS = {
                "entity_state", "before_state", "after_state",
                "final_state", "business_effect",
            }
            _has_effect_observers = any(
                isinstance(obs, dict)
                and _text(obs.get("observer_id")) in _EFFECT_OBS_IDS
                for obs in _list(exp.get("observers"))
            )
            if _has_effect_observers:
                # A write response reports that the request was accepted, not that
                # the business effect happened. Degrading to it would make the
                # response its own proof.
                return False, "BLOCKED_MISSING_OBSERVER", f"write_observer:{op_ref}"
        # Collection POST create with only response-bound identity GET:
        # the identity GET requires the write response ID, so it cannot serve
        # as a pre-write observer. Block unless an independent pre-write
        # observer exists (collection GET, unique-key query, or DB adapter).
        if (
            _infer_operation_effect(op, method) == "write"
            and path.startswith("/")
            and not path_has_placeholders(path)
            and _has_response_bound_create_observers(op, ops)
        ):
            _has_pre_write_observer = _declared_observation_path(path, ops) and not path_has_placeholders(
                _declared_observation_path(path, ops)
            )
            if not _has_pre_write_observer:
                # Check experiment-level observers for a non-response-bound read
                _exp_observers = _list(exp.get("observers"))
                _has_independent_pre_write = any(
                    isinstance(obs, dict)
                    and _text(obs.get("surface")) != "business_effect"
                    and _text(obs.get("observer_id")) != "http_response"
                    and not _text(obs.get("identity_source")).startswith("write_response")
                    for obs in _exp_observers
                    if _text(obs.get("surface")) in {"http_api", "database", "event"}
                )
                if not _has_independent_pre_write:
                    return False, "BLOCKED_MISSING_OBSERVER", "response_bound_after_without_pre_write_observer"
    if not _list(exp.get("observers")):
        return False, "BLOCKED_MISSING_OBSERVER", "none"
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    risk_family = _text(assertion.get("kind") or assertion.get("type"))
    if risk_family == "owner_tenant_visibility":
        risk_family = "authorization"
    # Adapter set recorded at compile time, not a hardcoded {"http_api"}.
    #
    # Hardcoding it here meant an experiment compiled with a wider adapter set -- the
    # entire point of being able to register a database, queue, view or timing observer
    # -- would compile and then be rejected at runtime with
    # BLOCKED_UNSUPPORTED_ADAPTER. This keeps the drift check (the observers must still
    # be within what compilation approved) without pinning the value.
    #
    # Legacy experiments compiled before compiled_adapters existed fall back to the
    # http_api baseline, which is exactly the set they were gated against.
    _compiled_adapters = {
        _text(item) for item in _list(exp.get("compiled_adapters")) if _text(item)
    } or {"http_api"}
    observer_reason, observer_detail = validate_observer_declarations(
        [row for row in _list(exp.get("observers")) if isinstance(row, dict)],
        risk_family=risk_family,
        available_adapters=_compiled_adapters,
        require_authorization_comparison=not _is_permitted_operation_invocation(exp),
    )
    if observer_reason:
        return False, observer_reason, observer_detail
    safety = _dict(exp.get("safety_contract"))
    is_write = bool(safety.get("governed_write"))
    if is_write and not _list(exp.get("cleanup_plan")):
        # Allow writes where cleanup is explicitly declared not required
        if not safety.get("cleanup_not_required"):
            # Never invent cleanup at preflight. Compilers must bind a
            # source-declared compensator (or snapshot restore for in-place
            # PUT/PATCH) before a write reaches transport.
            return False, "BLOCKED_NON_REVERSIBLE_WRITE", "cleanup_compensation_unresolved"
    cleanup_preflight_error = _cleanup_body_preflight_error(exp)
    if cleanup_preflight_error:
        return False, "BLOCKED_NON_REVERSIBLE_WRITE", cleanup_preflight_error
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
    """Return a source-declared effect observer through the shared graph.

    Prefer identity-bound (entity-scoped) observers that share write-path
    placeholders and can be fully materialized from known bindings. Collection
    GETs remain a fallback only when no entity observer can be bound — never
    preferred when an entity GET is available; otherwise identity-write state
    changes stay invisible and falsely look unchanged.
    """
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
    write_placeholders = set(infer_path_params(normalize_path_placeholders(path)))
    entity_bound: list[str] = []
    collection_bound: list[str] = []
    for observer in observers:
        template = _text(observer.get("path"))
        materialized = template
        for name, value in binding_values.items():
            if value in (None, ""):
                continue
            materialized = materialized.replace(
                "{" + name + "}",
                quote(str(value), safe=""),
            )
        if not (
            materialized.startswith("/")
            and not path_has_placeholders(materialized)
        ):
            continue
        obs_placeholders = set(infer_path_params(template))
        if obs_placeholders and (
            not write_placeholders or (obs_placeholders & write_placeholders)
        ):
            entity_bound.append(materialized)
        elif obs_placeholders:
            entity_bound.append(materialized)
        else:
            collection_bound.append(materialized)
    if entity_bound:
        return entity_bound[0]
    if collection_bound:
        return collection_bound[0]
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


def _has_response_bound_create_observers(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> bool:
    """True when effect proof is an identity GET under this collection create."""

    from .real_id_resolver import (
        collection_path,
        normalize_path_placeholders,
        path_has_placeholders,
    )

    target = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    if (
        _infer_operation_effect(
            operation,
            _text(operation.get("method")).upper(),
        ) != "write"
        or not target.startswith("/")
        or path_has_placeholders(target)
    ):
        return False
    observers = declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    for observer in observers:
        path = normalize_path_placeholders(_text(observer.get("path")))
        if (
            path_has_placeholders(path)
            and normalize_path_placeholders(collection_path(path)) == target
        ):
            return True
    return False


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
    """Extract source-observed entity rows without assuming a domain schema.

    Reference reads must never bind the harness's own synthetic records
    (qb-auto-*@qualibug.local — test artifacts with no business data), and
    the remaining candidates are ordered by observable business-data richness
    so the materializer picks a subject that actually carries the resource.
    Mirrors the reference-extraction rules of ``real_id_resolver_base``.
    """
    if isinstance(value, list):
        rows = [row for row in value if isinstance(row, dict)]
    elif isinstance(value, dict):
        rows = []
        for key in ("data", "result", "items", "records", "results", "list", "rows", "content"):
            child = value.get(key)
            if isinstance(child, list):
                rows.extend(row for row in child if isinstance(row, dict))
            elif isinstance(child, dict):
                rows.append(child)
        if not rows:
            rows = [value]
    else:
        rows = []
    try:
        from .real_id_resolver_base import (
            _business_data_richness,
            _is_harness_disposable_record,
        )
        real_rows = [
            row for row in rows if not _is_harness_disposable_record(row)
        ]
        if real_rows:
            real_rows.sort(key=_business_data_richness, reverse=True)
            return real_rows
    except Exception:
        pass
    return rows


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
    # State-scoped target paths (``@state=cancelled@/api/orders/{id}/cancel``)
    # select an entity in the required state from the collection response
    # before structural binding — the compiled binding carries the required
    # state and the batch pre-resolution may not have supplied a value.
    if isinstance(body, list) and target_path.startswith("@state="):
        from .runtime_binding_materializer_base import (
            _STATE_TARGET_PATH_RE,
            _state_selected_entity,
        )

        _state_match = _STATE_TARGET_PATH_RE.match(target_path)
        if _state_match:
            _required = _state_match.group(1).lower()
            target_path = _state_match.group(2)
            _selected = _state_selected_entity(body, _required)
            if not _selected:
                # No entity in the required state: binding the first row would
                # fail the state precondition later; return empty so the
                # materializer falls back to fixture setup or blocks visibly.
                return {}
            body = _selected
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


def project_observed_body(
    rows: Any,
    projection_fields: list[Any],
    reference_mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project source-observed entity field values into a write body template.

    When a write operation declares a request schema but no request example,
    the compiler marks its binding with ``body_projection_fields`` and the
    resolver reads the same collection with the writer's credentials. The
    body is assembled from ONE observed row — the row covering the most
    projection fields, so the values stay mutually coherent — and only
    fields the source schema declares are copied, keyed by their schema
    names. Every value comes from the environment's own observed data;
    nothing is synthesized.

    ``reference_mapping`` maps body fields onto the row fields of a
    REFERENCED entity: ``{"orderId": {"source_field": "id"}}`` fills the
    body's foreign-key field from the referenced row's identity field,
    which normalized-key equality could never match (``orderid`` vs
    ``id``). Scalar schema fields still bind by normalized key equality.

    Returns {} when no row carries any projection field (the caller treats
    the binding as unresolved and fails closed). Missing required fields are
    caught downstream by the pre-transport required-field gate, which keeps
    the gap visible instead of inventing values.
    """
    fields = [str(field).strip() for field in _list(projection_fields) if str(field or "").strip()]
    if not fields:
        return {}
    mapping = _dict(reference_mapping) if isinstance(reference_mapping, dict) else {}
    candidates = [row for row in _list(rows) if isinstance(row, dict)]
    best: dict[str, Any] = {}
    for row in candidates:
        projected: dict[str, Any] = {}
        for field in fields:
            ref = mapping.get(field)
            source_field = ""
            if isinstance(ref, dict):
                source_field = _text(ref.get("source_field"))
            if source_field:
                source_key = re.sub(r"[^a-z0-9]+", "", source_field.lower())
                for row_key, row_value in row.items():
                    if re.sub(r"[^a-z0-9]+", "", str(row_key).lower()) != source_key:
                        continue
                    if row_value in (None, ""):
                        continue
                    projected[field] = row_value
                    break
                continue
            field_key = re.sub(r"[^a-z0-9]+", "", field.lower())
            if not field_key:
                continue
            for row_key, row_value in row.items():
                if re.sub(r"[^a-z0-9]+", "", str(row_key).lower()) != field_key:
                    continue
                if row_value in (None, ""):
                    continue
                projected[field] = row_value
                break
        if len(projected) > len(best):
            best = projected
    return best


def consensus_identity_value(body: Any, target: str) -> tuple[str, str]:
    """Resolve an owner identity only when every observed entity row agrees.

    A caller-scoped owned-entity read is trustworthy as an identity source
    only when all observed rows carry the same owner-field value: the read
    executes with the owner's own credentials, so agreement means the single
    observed value is the caller's own identity. Disagreement means the
    collection mixes owners (leak or shared scope) and any picked value
    could be another actor's identity — cross-contamination — so the caller
    must fail closed instead of binding.

    Returns ``(value, status)`` with status in ``{"consensus", "absent",
    "conflicted"}``. Rows lacking the field entirely abstain rather than
    conflict; only concrete disagreeing values contaminate.
    """
    from .real_id_resolver import param_field_candidates

    rows = _runtime_entity_candidates(body)
    # Only the target's own declared field names — the generic identity
    # fallbacks in param_field_candidates (id/sku/code/...) name the ENTITY's
    # identity, not the OWNER's, and binding them would pollute the arm
    # identity with an unrelated resource id.
    target_key = re.sub(r"[^a-z0-9]+", "", str(target or "").lower())
    field_names = [
        name
        for name in param_field_candidates(target)
        if re.sub(r"[^a-z0-9]+", "", name.lower()) == target_key
    ]
    if not field_names:
        return "", "absent"
    observed: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field_name in field_names:
            value = row.get(field_name)
            if (
                value not in (None, "", [], {})
                and not isinstance(value, (dict, list))
            ):
                observed.append(str(value))
                break
    distinct = list(dict.fromkeys(observed))
    if not distinct:
        return "", "absent"
    if len(distinct) > 1:
        return "", "conflicted"
    return distinct[0], "consensus"


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


# ── P0-3: Environment Preflight ──

def run_environment_preflight(
    *,
    root: Path,
    project: str,
    base_url: str,
    obligation_plan: dict[str, Any],
    behavior_ir: dict[str, Any],
    runtime_contract: dict[str, Any] | None = None,
    max_route_checks: int = 20,
) -> dict[str, Any]:
    """Compatibility facade for the canonical onboarding preflight helper."""
    return _run_environment_preflight(
        root=root,
        project=project,
        base_url=base_url,
        obligation_plan=obligation_plan,
        behavior_ir=behavior_ir,
        runtime_contract=runtime_contract,
        max_route_checks=max_route_checks,
        http_request=_http_request,
        actor_token_loader=load_actor_tokens,
    )
