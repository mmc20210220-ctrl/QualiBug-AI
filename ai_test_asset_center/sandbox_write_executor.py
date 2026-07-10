"""Sandbox write-probe executor — before/after snapshots, cleanup, audit.

Enabled by default only for an approved, explicitly declared non-production
environment with a test-account token. Production and unknown environments are
fail-closed. Failures are recorded honestly (never silent, never fake cleanup).

Design: wrap an already-planned scenario execution so the write itself still
goes through the normal executor once — this module only adds GET(before),
GET(after), cleanup, and audit around that single write.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .enterprise_project_config import match_production_data_exclusion

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Non-production environment kinds where read+write probing is the product's
# intended mode of operation (customer test / pre-release / staging systems).
_TEST_ENV_TOKENS = frozenset({
    "test", "sandbox", "staging", "stage", "system_test", "systest", "qa", "uat",
    "sit", "dev", "development", "local", "preprod", "pre_prod", "pre-prod",
    "prerelease", "pre_release", "pre-release", "preview", "预发布", "预发", "测试",
})
# HARD RED LINE — never write here. This is the product's core safety promise:
# QualiBug operates read+write on customer NON-production environments only and
# never touches production.
_PRODUCTION_ENV_TOKENS = frozenset({
    "prod", "production", "live", "prd", "release", "生产", "线上", "正式",
})


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _production_mode() -> bool:
    """Global production lock (QUALIBUG_PRODUCTION) — disables all writes."""
    return _truthy(os.environ.get("QUALIBUG_PRODUCTION", ""))


def write_probing_disabled() -> bool:
    """Explicit customer kill-switch for strict read-only, even in test envs."""
    if _truthy(os.environ.get("QUALIBUG_DISABLE_SANDBOX_WRITE", "")):
        return True
    # Back-compat: an explicit QUALIBUG_ENABLE_SANDBOX_WRITE=0/false also disables.
    explicit = os.environ.get("QUALIBUG_ENABLE_SANDBOX_WRITE", "")
    if explicit and not _truthy(explicit):
        return True
    return False


def sandbox_write_enabled() -> bool:
    """Master switch — DEFAULT ON.

    The product's positioning is read+write on customer non-production (test /
    pre-release) environments; that is a deliberate competitive advantage, not an
    opt-in expert mode. The only hard red line is production, which is enforced in
    ``sandbox_write_allowed`` (env kind) and per-request via
    ``match_production_data_exclusion``. A customer can still force strict
    read-only with QUALIBUG_DISABLE_SANDBOX_WRITE=1 (or QUALIBUG_ENABLE_SANDBOX_WRITE=0).
    """
    if _production_mode():
        return False
    if write_probing_disabled():
        return False
    return True


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_or_text(raw: str) -> Any:
    text = str(raw or "")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:2000]}


def load_project_environment_kind(root: Path, project: str) -> str:
    """Read declared environment from project config (no hardcoding of values)."""
    candidates = [
        root / "platform_inputs" / project / "multi_service_config.json",
        root / "platform_inputs" / project / "real_project_config.json",
        root / "platform_workspace" / project / "multi_service_config.json",
        root / "platform_workspace" / project / "real_project_config.json",
        root / "platform_inputs" / project / "enterprise_testops_environment.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"failed_to_read_environment_config:{path.name}:{type(exc).__name__}"
            ) from exc
        if not isinstance(data, dict):
            continue
        for key in ("environment", "target_environment", "environment_kind", "env"):
            value = _text(data.get(key)).lower()
            if value:
                return value
        environments = data.get("environments")
        if isinstance(environments, list):
            target = _text(data.get("target_environment")).lower()
            for item in environments:
                if not isinstance(item, dict):
                    continue
                name = _text(item.get("name")).lower()
                etype = _text(item.get("type")).lower()
                if target and name == target:
                    return etype or name
            for item in environments:
                if isinstance(item, dict):
                    etype = _text(item.get("type") or item.get("name")).lower()
                    if etype:
                        return etype
        services = data.get("services")
        if isinstance(services, list):
            for svc in services:
                if isinstance(svc, dict):
                    value = _text(svc.get("environment") or svc.get("target_environment")).lower()
                    if value:
                        return value
    return ""


def is_production_environment(env_kind: str) -> bool:
    """HARD RED LINE detector — is this a production/live environment?"""
    token = _text(env_kind).lower()
    if not token:
        return False
    if token in _PRODUCTION_ENV_TOKENS:
        return True
    # Substring match, but do not let "preprod"/"pre-release" false-positive on
    # the "prod"/"release" substrings — those are non-production tokens.
    if token in _TEST_ENV_TOKENS:
        return False
    return any(part in token for part in _PRODUCTION_ENV_TOKENS)


def is_test_or_sandbox_environment(env_kind: str) -> bool:
    token = _text(env_kind).lower()
    if not token:
        return False
    if is_production_environment(token):
        return False
    if token in _TEST_ENV_TOKENS:
        return True
    return any(part in token for part in _TEST_ENV_TOKENS)


def resolve_environment_kind(
    root: Path,
    project: str,
    runtime_contract: dict[str, Any] | None = None,
) -> str:
    """Best-effort environment kind from project config, then runtime contract."""
    kind = load_project_environment_kind(root, project)
    if kind:
        return kind
    rc = _as_dict(runtime_contract)
    for key in ("environment_kind", "environment_ref", "target_environment", "environment"):
        value = _text(rc.get(key)).lower()
        if value:
            return value
    return ""


def sandbox_write_allowed(
    *,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    actor_token: str,
    actor_identity: str = "",
    scenario: Any = None,
) -> tuple[bool, str]:
    """Gate for read+write probing.

    Default posture: WRITE ALLOWED on customer non-production environments. The
    only hard blocks are (a) production / production-mode, (b) an undeclared
    environment (fail-safe — we will not assume an unknown target is safe to
    write), (c) missing runtime approval / base_url / actor token.
    """
    if _production_mode():
        return False, "production_mode_blocks_write"
    if not sandbox_write_enabled():
        return False, "write_probing_disabled_by_operator"
    if str(_as_dict(runtime_contract).get("status") or "") != "approved":
        return False, "runtime_contract_not_approved"
    if _text(_as_dict(runtime_contract).get("execution_mode")).lower() == "safe_read_only":
        return False, "execution_mode_read_only"
    if not _text(_as_dict(runtime_contract).get("approved_base_url")):
        return False, "approved_base_url_missing"
    env_kind = resolve_environment_kind(root, project, runtime_contract)
    # HARD RED LINE — never write to production.
    if is_production_environment(env_kind):
        return False, f"production_environment_blocked:{env_kind}"
    # Fail-safe: an undeclared environment is NOT assumed to be non-production.
    if not env_kind:
        return False, "environment_kind_undeclared"
    if not is_test_or_sandbox_environment(env_kind):
        return False, f"environment_not_recognized_nonprod:{env_kind}"
    identity = _text(actor_identity)
    if not identity and scenario is not None:
        identity = _scenario_declared_actor_identity(scenario)
    if not _text(actor_token) and not identity:
        return False, "test_actor_identity_missing"
    return True, "approved"


def _http_request(
    method: str,
    url: str,
    *,
    token: str = "",
    body: Any = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None and method.upper() not in {"GET", "HEAD"}:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.time()
    try:
        request = urllib.request.Request(url, method=method.upper(), data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(300_000).decode("utf-8", errors="replace")
            status = int(response.status)
            response_body = _json_or_text(raw)
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
        status = int(exc.code)
        response_body = _json_or_text(raw)
        response_headers = dict(exc.headers.items()) if exc.headers else {}
    except Exception as exc:
        return {
            "method": method.upper(),
            "url": url,
            "status": 0,
            "body": {"error": f"{type(exc).__name__}: {exc}"},
            "headers": {},
            "duration_ms": int((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "method": method.upper(),
        "url": url,
        "status": status,
        "body": response_body,
        "headers": response_headers,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _collection_path(path: str) -> str:
    cleaned = path.split("?")[0].rstrip("/")
    cleaned = re.sub(r"/\{[^}]+\}$", "", cleaned)
    cleaned = re.sub(r"/:[A-Za-z_][A-Za-z0-9_]*$", "", cleaned)
    return cleaned or path


def _extract_resource_id(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("id", "uuid", "resource_id", "entity_id"):
            value = body.get(key)
            if value not in (None, "", [], {}):
                return str(value)
        data = body.get("data")
        if isinstance(data, dict):
            for key in ("id", "uuid"):
                value = data.get(key)
                if value not in (None, "", [], {}):
                    return str(value)
    return ""


def _append_audit(root: Path, project: str, record: dict[str, Any]) -> Path:
    audit_dir = root / "platform_workspace" / project / "defect_discovery"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "sandbox_write_audit.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def _is_authentication_step(step: Any) -> bool:
    action = _text(getattr(step, "action", "") or "").strip().lower()
    if action == "login" or action.startswith("login_") or action.endswith("_login"):
        return True
    path = _text(getattr(step, "api_path", "") or "").strip().lower()
    return path.endswith("/login") or "/auth/login" in path


def _scenario_declared_actor_identity(scenario: Any) -> str:
    for key in ("actor_role", "actor", "actor_id"):
        value = _text(getattr(scenario, key, "") or "")
        if value:
            return value
    actors = getattr(scenario, "actors", None) or []
    if isinstance(actors, list):
        for actor in actors:
            label = _text(actor)
            if label:
                return label
    return ""


def _first_write_step(scenario: Any) -> tuple[str, str, Any] | None:
    for step in getattr(scenario, "steps", []) or []:
        if _is_authentication_step(step):
            continue
        method = _text(getattr(step, "api_method", "") or "").upper()
        path = _text(getattr(step, "api_path", "") or "")
        if method in _WRITE_METHODS and path.startswith("/"):
            return method, path, getattr(step, "body_template", None)
    return None


def _blocked_write_trace(scenario: Any, reason: str, write_meta: tuple[str, str, Any]) -> dict[str, Any]:
    method, path, body = write_meta
    return {
        "scenario_id": getattr(scenario, "id", "?"),
        "steps": [{
            "action": "write_blocked",
            "method": method,
            "path": path,
            "status": 0,
            "request": {"body_present": body not in (None, {}, [], "")},
            "response": {"status_code": 0, "headers": {}, "body": {"error": reason}},
            "skipped_reason": reason,
            "execution_blocked": True,
        }],
        "errors": [reason],
        "sandbox_write": {
            "status": "blocked",
            "reason": reason,
            "cleanup": {"status": "not_applicable", "reason": reason},
        },
    }


def _cleanup_after_write(
    *,
    method: str,
    path: str,
    base_url: str,
    token: str,
    before_body: Any,
    write_body: Any,
) -> dict[str, Any]:
    """Honest cleanup — never report completed on failure."""
    base = base_url.rstrip("/")
    observe_path = _collection_path(path)
    if method == "POST":
        resource_id = _extract_resource_id(write_body)
        if not resource_id:
            return {
                "status": "failed",
                "strategy": "delete_created_resource",
                "receipt_ref": "",
                "error": "created_resource_id_missing",
            }
        delete_path = f"{observe_path.rstrip('/')}/{resource_id}"
        receipt = _http_request("DELETE", base + delete_path, token=token)
        status = int(receipt.get("status") or 0)
        ok = 200 <= status < 300 or status in {204, 404}
        result = {
            "status": "completed" if ok else "failed",
            "strategy": "delete_created_resource",
            "receipt_ref": delete_path,
            "receipt": {"status": status},
        }
        if not ok:
            result["error"] = f"cleanup_delete_http_{status}"
        return result
    if method in {"PUT", "PATCH"}:
        if not isinstance(before_body, dict) or not before_body:
            return {
                "status": "failed",
                "strategy": "restore_before_snapshot",
                "receipt_ref": path,
                "error": "before_snapshot_not_restorable",
            }
        receipt = _http_request(method, base + path, token=token, body=before_body)
        status = int(receipt.get("status") or 0)
        ok = 200 <= status < 300
        result = {
            "status": "completed" if ok else "failed",
            "strategy": "restore_before_snapshot",
            "receipt_ref": path,
            "receipt": {"status": status},
        }
        if not ok:
            result["error"] = f"cleanup_restore_http_{status}"
        return result
    # DELETE — irreversible
    return {
        "status": "not_reversible",
        "strategy": "delete_irreversible",
        "receipt_ref": path,
        "warning": "DELETE_not_reversible",
    }


def execute_with_sandbox_write(
    scenario: Any,
    base_url: str,
    *,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    campaign_id: str = "",
    safety_boundary: dict[str, Any] | None = None,
    execute_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Wrap ``execute_fn`` with before/after/cleanup when sandbox write is allowed.

    Read-only scenarios continue through the normal executor. A write scenario
    that fails any gate is returned as blocked without firing the request.
    """
    write_meta = _first_write_step(scenario)
    if write_meta is None:
        return execute_fn(scenario, base_url, safety_boundary=safety_boundary)
    token = _text(getattr(scenario, "actor_token", "") or "")
    actor_identity = _text(
        getattr(scenario, "actor_role", "")
        or getattr(scenario, "actor", "")
        or getattr(scenario, "actor_id", "")
        or runtime_contract.get("actor_identity")
    )
    allowed, reason = sandbox_write_allowed(
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        actor_token=token,
        actor_identity=actor_identity,
        scenario=scenario,
    )
    if not allowed:
        return _blocked_write_trace(scenario, reason, write_meta)

    method, path, _body = write_meta
    if safety_boundary:
        excl = match_production_data_exclusion(safety_boundary, path, "")
        if excl:
            trace = _blocked_write_trace(scenario, excl, write_meta)
            trace["production_data_blocked"] = True
            trace["production_data_block_reason"] = excl
            return trace

    # Upgrade policy marker for observability / ranking
    scenario.execution_policy = "approved_sandbox_write"

    base = base_url.rstrip("/")
    observe_path = _collection_path(path)
    before = _http_request("GET", base + observe_path, token=token)
    trace = execute_fn(scenario, base_url, safety_boundary=safety_boundary)
    after = _http_request("GET", base + observe_path, token=token)

    write_body: Any = {}
    for step in trace.get("steps") or []:
        if isinstance(step, dict) and _text(step.get("method")).upper() == method:
            write_body = _as_dict(step.get("response")).get("body")
            if write_body:
                break

    cleanup = _cleanup_after_write(
        method=method,
        path=path,
        base_url=base_url,
        token=token,
        before_body=before.get("body"),
        write_body=write_body,
    )
    before_ref = f"sandbox_before:{observe_path}:{before.get('status')}"
    after_ref = f"sandbox_after:{observe_path}:{after.get('status')}"
    evidence = {
        "before_snapshot_ref": before_ref,
        "after_snapshot_ref": after_ref,
        "cleanup": {
            "status": cleanup.get("status"),
            "receipt_ref": cleanup.get("receipt_ref") or "",
        },
        "method": method,
        "path": path,
    }
    audit_record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor_role": _text(getattr(scenario, "actor_role", "") or trace.get("actor_role") or ""),
        "method": method,
        "path": path,
        "before_ref": before_ref,
        "after_ref": after_ref,
        "cleanup_status": cleanup.get("status"),
        "campaign_id": campaign_id,
        "slice_id": _text(getattr(scenario, "behavior_slice_id", "") or ""),
        "environment_kind": resolve_environment_kind(root, project, runtime_contract),
        "approved_base_url": _text(runtime_contract.get("approved_base_url")),
    }
    audit_path = _append_audit(root, project, audit_record)

    cleanup_status = _text(cleanup.get("status"))
    write_status = "completed" if cleanup_status == "completed" else "cleanup_incomplete"
    trace["sandbox_write"] = {
        "status": write_status,
        "before": {"status": before.get("status"), "ref": before_ref},
        "after": {"status": after.get("status"), "ref": after_ref},
        "cleanup": cleanup,
        "evidence": evidence,
        "audit_path": str(audit_path),
    }
    trace.setdefault("evidence", {})
    if isinstance(trace["evidence"], dict):
        trace["evidence"]["before_snapshot_ref"] = before_ref
        trace["evidence"]["after_snapshot_ref"] = after_ref
        trace["evidence"]["cleanup"] = {
            "status": cleanup.get("status"),
            "receipt_ref": cleanup.get("receipt_ref") or "",
        }
    return trace
