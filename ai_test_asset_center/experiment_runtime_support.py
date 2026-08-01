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
import os
import re
import time
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
from .sandbox_write_executor import _http_request


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


def _jwt_expired(token: str, *, skew_seconds: int = 30) -> bool:
    """Whether a JWT's own ``exp`` claim has passed.

    Signature is not checked and must not be -- the target owns the secret. Only
    the expiry claim is read, and a token whose claim cannot be parsed is treated
    as usable so a non-JWT bearer (opaque token, API key) is not discarded.

    A stored token is a snapshot, and a snapshot goes stale. Returning one blind
    made the executor send a dead credential; the target answered 401 and the
    oracle recorded that as "the endpoint rejected this actor", which is a
    fabricated authorization defect rather than a finding about the target.
    """
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return False
    import base64

    try:
        segment = parts[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
    except Exception:
        return False
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)):
        return False
    return bool(time.time() + skew_seconds >= float(exp))


def _credential_config_path(root: Path, project: str) -> Path | None:
    """Return the project credential config from its existing SSOT location."""
    for candidate in (
        Path(root) / "platform_workspace" / str(project) / "multi_service_config.json",
        Path(root) / "platform_inputs" / str(project) / "multi_service_config.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _configured_credential_manager(root: Path, project: str) -> Any | None:
    """Load the configured enterprise credential manager, or no manager.

    The credential file is control-plane input, not a knowledge source. Keep its
    schema and decryption authority in ``EnterpriseCredentialManager`` instead
    of re-parsing service accounts in the execution path. Invalid JSON and an
    unavailable decryption key are surfaced to the caller so a configured
    credential cannot silently turn into an anonymous run.
    """
    config_path = _credential_config_path(root, project)
    if config_path is None:
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("runtime_credential_config_invalid") from exc
    if not isinstance(config, dict):
        raise RuntimeError("runtime_credential_config_root_invalid")
    from .enterprise_credential_manager import EnterpriseCredentialManager

    manager = EnterpriseCredentialManager(str(project), Path(root))
    manager.load_from_dict(config)
    return manager


def configured_runtime_accounts(root: Path, project: str) -> list[dict[str, str]]:
    """Project runtime actors from the existing service credential authority.

    A role-to-account coordinate is executable only when the operator declared
    the role and login identity in the credential manager. No localized display
    label is translated here, and no account is invented from a role name.
    """
    manager = _configured_credential_manager(root, project)
    if manager is None:
        return []
    rows: list[dict[str, str]] = []
    credentials = sorted(
        manager.store.all(),
        key=lambda item: (
            _text(getattr(item, "service", "")).lower(),
            _text(getattr(item, "role", "")).lower(),
            _text(getattr(item, "username", "")).lower(),
        ),
    )
    for credential in credentials:
        role = _text(getattr(credential, "role", ""))
        service = _text(getattr(credential, "service", ""))
        username = _text(getattr(credential, "username", ""))
        if not role:
            continue
        row = {
            "role": role,
            "service": service,
            "status": "active",
        }
        if username:
            row.update({
                "account_ref": username,
                "secret_ref": f"secret_ref:test_accounts:{username}",
            })
        elif getattr(credential, "bearer_token", "") or getattr(credential, "api_key", ""):
            # A pre-authenticated service credential has no login identity. Keep
            # it role-scoped; never fabricate an account_ref for it.
            row["secret_ref"] = f"secret_ref:service:{service}:{role}"
        else:
            continue
        rows.append(row)
    return rows


def _configured_credential_tokens(
    root: Path,
    project: str,
    *,
    base_url: str = "",
) -> dict[str, str]:
    """Acquire tokens through the existing enterprise credential manager."""
    manager = _configured_credential_manager(root, project)
    if manager is None:
        return {}
    results = manager.login_all_services(timeout=8)
    failed = [
        f"{service}/{role}"
        for service, role_results in results.items()
        for role, ok in role_results.items()
        if not ok
    ]
    if failed:
        _LOGGER.warning(
            "configured_actor_login_incomplete project=%s credential_count=%s "
            "failed=%s",
            project,
            sum(len(value) for value in results.values()),
            ",".join(sorted(failed)[:12]),
        )
    tokens: dict[str, str] = {}
    role_tokens: dict[str, list[str]] = {}
    for credential in manager.store.all():
        service = _text(getattr(credential, "service", ""))
        role = _text(getattr(credential, "role", ""))
        if not service or not role:
            continue
        token = _text(manager.get_token(service, role, auto_refresh=False))
        if not token:
            continue
        username = _text(getattr(credential, "username", ""))
        if username:
            tokens[username] = token
            tokens[f"secret_ref:test_accounts:{username}"] = token
        else:
            tokens[f"secret_ref:service:{service}:{role}"] = token
        role_tokens.setdefault(role, []).append(token)
    for role, values in role_tokens.items():
        # Role aliases are safe only when the configured authority has one
        # active credential for that role. Account-qualified actors always use
        # their exact secret_ref above.
        unique_values = list(dict.fromkeys(values))
        if len(unique_values) != 1:
            continue
        token = unique_values[0]
        tokens.setdefault(role, token)
        tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
        tokens.setdefault(f"secret_ref:context:{role}", token)
        tokens.setdefault(f"secret_ref:actor:{role}", token)
    return tokens


def load_actor_tokens(root: Path, project: str, *, base_url: str = "") -> dict[str, str]:
    """Map role / secret_ref → bearer token from declared test accounts only.

    P0-4/P0-7 enhanced: falls back to parsing TEST_ACCOUNTS.md from the
    project input directory and performing login when tokens are absent.
    Priority: test_accounts.json > TEST_ACCOUNTS.md (with login) > empty.

    Stored tokens that have expired are dropped so the MD-login fallback runs,
    rather than being handed to the executor as if they were live. ``base_url`` is
    threaded from the caller's approved target because relying on the
    QUALIBUG_TARGET_BASE_URL environment variable made that fallback dead under
    the HTTP scan entrypoint, which never sets it.
    """
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            payload = {}
        tokens: dict[str, str] = {}
        expired_roles: list[str] = []
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
            if _jwt_expired(token):
                # Recorded, not silently skipped: a stale snapshot is the difference
                # between "no credential" and "a credential the target will reject".
                expired_roles.append(role)
                continue
            status = _text(row.get("status") or row.get("account_status") or row.get("state") or "active").upper()
            if account_ref:
                tokens[account_ref] = token
                tokens[f"secret_ref:test_accounts:{account_ref}"] = token
            if status not in {"DISABLED", "LOCKED"}:
                tokens.setdefault(role, token)
                tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
                tokens.setdefault(f"secret_ref:context:{role}", token)
                tokens.setdefault(f"secret_ref:actor:{role}", token)
        if tokens:
            return tokens
        if expired_roles:
            _LOGGER.warning(
                "declared_actor_tokens_expired project=%s count=%s roles=%s "
                "action=reload_test_accounts",
                project,
                len(expired_roles),
                ",".join(sorted(set(expired_roles))[:6]),
            )

    # ── P0-4: Fallback to TEST_ACCOUNTS.md with login ──
    md_accounts = _parse_test_accounts_md(root, project)
    base_url = _text(base_url) or _text(os.environ.get("QUALIBUG_TARGET_BASE_URL") or "")
    login_path = _text(os.environ.get("QUALIBUG_LOGIN_PATH") or "/api/auth/login")
    tokens = {}
    if base_url:
        for acct in md_accounts:
            role = _text(acct.get("role"))
            email = _text(acct.get("email"))
            password = _text(acct.get("password"))
            if not role or not email or not password:
                continue
            try:
                resp = _http_request(
                    "POST",
                    base_url.rstrip("/") + login_path,
                    body={"email": email, "password": password},
                    timeout=8.0,
                )
                status = int(resp.get("status") or 0)
                body = resp.get("body")
                token = ""
                if isinstance(body, dict):
                    token = _text(body.get("token") or body.get("access_token") or body.get("jwt") or _dict(body.get("data")).get("token"))
                if status == 200 and token:
                    account_ref = email
                    tokens[account_ref] = token
                    tokens[f"secret_ref:test_accounts:{account_ref}"] = token
                    # Keep the local-part alias for legacy callers, but the
                    # exact account coordinate is always the full declared email.
                    if "@" in email:
                        tokens.setdefault(email.split("@", 1)[0], token)
                    tokens.setdefault(role, token)
                    tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
                    tokens.setdefault(f"secret_ref:context:{role}", token)
                else:
                    _LOGGER.warning(
                        "actor_login_rejected project=%s role=%s status=%s "
                        "token_present=%s",
                        project,
                        role,
                        status,
                        bool(token),
                    )
            except Exception as exc:
                _LOGGER.warning(
                    "actor_login_transport_failed project=%s role=%s error=%s",
                    project,
                    role,
                    type(exc).__name__,
                )
                continue
    if not tokens:
        tokens.update(_configured_credential_tokens(root, project, base_url=base_url))
    return tokens


def _parse_test_accounts_text(text: str) -> list[dict[str, str]]:
    """Parse one source-declared Markdown account table."""
    accounts: list[dict[str, str]] = []
    lines = text.splitlines()
    header_cols: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if not cells:
            continue
        lower_cells = [c.lower() for c in cells]
        if any(h in lower_cells for h in ("角色", "role", "邮箱", "email")):
            header_cols = lower_cells
            continue
        if all(set(c) <= set("-| ") for c in cells):
            continue
        if not header_cols:
            continue
        row: dict[str, str] = {}
        for i, col in enumerate(header_cols):
            val = cells[i] if i < len(cells) else ""
            if col in ("角色", "role"):
                row["role"] = val
            elif col in ("邮箱", "email"):
                row["email"] = val
            elif col in ("密码", "password"):
                row["password"] = val
            elif col in ("说明", "description", "note"):
                row["note"] = val
        if row.get("email"):
            accounts.append(row)
    return accounts


def _parse_test_accounts_md(root: Path, project: str) -> list[dict[str, str]]:
    """Parse account tables from the registered source corpus first."""
    registered_documents: list[str] = []
    try:
        from .enterprise_source_registry import (
            SourceRegistryError,
            list_source_assets,
            load_source_content,
        )

        for asset in list_source_assets(str(project), root=Path(root)):
            if _text(asset.get("source_type")).lower() != "test_data":
                continue
            source_hash = _text(asset.get("latest_source_hash"))
            if not source_hash:
                raise RuntimeError("test_account_source_hash_missing")
            try:
                registered_documents.append(
                    load_source_content(str(project), source_hash, root=Path(root))
                )
            except SourceRegistryError as exc:
                raise RuntimeError("test_account_source_unreadable") from exc
    except ImportError:
        raise
    if registered_documents:
        accounts: list[dict[str, str]] = []
        for document in registered_documents:
            accounts.extend(_parse_test_accounts_text(document))
        return accounts

    search_dirs = [
        Path(root) / "projects" / str(project) / "input",
        Path(root) / "platform_inputs" / str(project),
        Path(root) / "platform_workspace" / str(project) / "input",
    ]
    md_path: Path | None = None
    for d in search_dirs:
        for fname in ("TEST_ACCOUNTS.md", "test_accounts.md"):
            candidate = d / fname
            if candidate.exists():
                md_path = candidate
                break
        if md_path:
            break
    if not md_path:
        return []
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _parse_test_accounts_text(text)


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
    """Pre-scan environment validation.

    Checks:
    1. base_url reachable
    2. Gateway routes for planned paths (deduplicated, capped)
    3. Auth configuration present
    4. Actor tokens loadable

    Returns a preflight_receipt dict with per-check results and a set of
    obligation IDs that should be marked ENVIRONMENT_BLOCKED.
    """
    checks: list[dict[str, Any]] = []
    blocked_obligation_ids: set[str] = set()
    all_passed = True

    # ── Check 1: base_url reachable ──
    base_url_ok = False
    if not base_url:
        checks.append({"check": "base_url_reachable", "status": "FAILED", "detail": "no base_url provided"})
        all_passed = False
    else:
        try:
            resp = _http_request("GET", base_url.rstrip("/") + "/", timeout=8.0)
            status = int(resp.get("status") or 0)
            # Any HTTP response means the server is reachable
            if status > 0:
                base_url_ok = True
                checks.append({"check": "base_url_reachable", "status": "PASSED", "http_status": status})
            else:
                checks.append({"check": "base_url_reachable", "status": "FAILED", "detail": "no HTTP response"})
                all_passed = False
        except Exception as exc:
            checks.append({"check": "base_url_reachable", "status": "FAILED", "detail": str(exc)[:200]})
            all_passed = False

    # ── Check 2: Gateway route sampling ──
    planned_paths: set[str] = set()
    for item in _list(obligation_plan.get("selected")):
        if not isinstance(item, dict):
            continue
        op_key = _text(item.get("operation_key"))
        # operation_key format: "METHOD /path" or "op:ref"
        if " " in op_key:
            path_part = op_key.split(" ", 1)[1]
            if path_part.startswith("/"):
                planned_paths.add(path_part)
    route_results: list[dict[str, Any]] = []
    routes_ok = 0
    routes_failed = 0
    if base_url_ok and planned_paths:
        sample_paths = sorted(planned_paths)[:max_route_checks]
        for path in sample_paths:
            try:
                resp = _http_request("HEAD", base_url.rstrip("/") + path, timeout=6.0)
                status = int(resp.get("status") or 0)
                if status == 404:
                    routes_failed += 1
                    route_results.append({"path": path, "status": status, "route": "NOT_FOUND"})
                else:
                    routes_ok += 1
                    route_results.append({"path": path, "status": status, "route": "OK"})
            except Exception as exc:
                routes_failed += 1
                error_type = type(exc).__name__
                route_results.append(
                    {
                        "path": path,
                        "status": 0,
                        "route": "ERROR",
                        "error_type": error_type,
                        "detail": str(exc)[:200],
                    }
                )
                _LOGGER.warning(
                    "environment_preflight_route_failed path=%s error=%s",
                    path,
                    error_type,
                )
    route_ratio = routes_ok / max(1, routes_ok + routes_failed)
    checks.append({
        "check": "gateway_routes",
        "status": "PASSED" if route_ratio >= 0.5 else "DEGRADED" if routes_ok > 0 else "FAILED",
        "routes_ok": routes_ok,
        "routes_failed": routes_failed,
        "sample_size": len(route_results),
    })
    if routes_ok == 0 and routes_failed > 0:
        all_passed = False

    # ── Check 3: Auth configuration ──
    auth_config_present = False
    auth_source = ""
    # Check test_accounts.json
    accounts_path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if accounts_path.exists():
        auth_config_present = True
        auth_source = "test_accounts.json"
    # Check project input directory for TEST_ACCOUNTS.md
    input_dir = Path(root) / "projects" / str(project) / "input"
    if not auth_config_present and input_dir.exists():
        for fname in ("TEST_ACCOUNTS.md", "test_accounts.md", "accounts.md"):
            if (input_dir / fname).exists():
                auth_config_present = True
                auth_source = fname
                break
    # Check runtime_contract for explicit auth
    rc = _dict(runtime_contract)
    if not auth_config_present and _text(rc.get("auth_token") or rc.get("bearer_token")):
        auth_config_present = True
        auth_source = "runtime_contract"
    checks.append({
        "check": "auth_configuration",
        "status": "PASSED" if auth_config_present else "WARNING",
        "source": auth_source,
    })

    # ── Check 4: Actor tokens loadable ──
    # base_url is in scope here and must be passed: without it the MD-login
    # fallback cannot run, so this check reports "no tokens" for a project whose
    # accounts are perfectly usable.
    actor_tokens = load_actor_tokens(root, project, base_url=base_url)
    tokens_ok = len(actor_tokens) > 0
    checks.append({
        "check": "actor_tokens",
        "status": "PASSED" if tokens_ok else "WARNING",
        "token_count": len(actor_tokens),
        "roles": sorted(set(
            k for k in actor_tokens if not k.startswith("secret_ref:")
        ))[:20],
    })

    # ── Determine blocked obligations ──
    # If base_url is not reachable, ALL planned obligations are blocked
    if not base_url_ok:
        for item in _list(obligation_plan.get("selected")):
            if isinstance(item, dict):
                oid = _text(item.get("obligation_id"))
                if oid:
                    blocked_obligation_ids.add(oid)

    return {
        "schema_version": "qualibug.environment-preflight.v1",
        "all_passed": all_passed,
        "checks": checks,
        "base_url": base_url,
        "base_url_reachable": base_url_ok,
        "route_sample_results": route_results,
        "auth_config_present": auth_config_present,
        "auth_source": auth_source,
        "actor_token_count": len(actor_tokens),
        "environment_blocked_obligation_ids": sorted(blocked_obligation_ids),
        "environment_blocked_count": len(blocked_obligation_ids),
    }
