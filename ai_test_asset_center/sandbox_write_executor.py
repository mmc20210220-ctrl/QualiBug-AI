"""Sandbox write-probe executor — before/after snapshots, cleanup, audit.

Enabled by default only for an approved, explicitly declared non-production
environment with a test-account token. Production and unknown environments are
fail-closed. Failures are recorded honestly (never silent, never fake cleanup).

Design: every non-authentication write emits its own before/after observation,
goes through the normal executor once — this module only adds GET(before),
cleanup outcome, and audit receipt. Multi-write execution fails closed when the
normal executor cannot expose the required per-write hook.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .enterprise_project_config import match_production_data_exclusion
from .real_id_resolver import collection_path as documented_collection_path
from .real_id_resolver import normalize_path_placeholders, path_has_placeholders

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
    # Identity and classification are separate facts. A URL belongs in
    # ``environment_ref`` and must never be interpreted as the safety class.
    for key in ("environment_kind", "environment_type", "target_environment", "environment_class", "environment"):
        value = _text(rc.get(key)).lower()
        if value:
            return value
    # Legacy configurations sometimes stored a literal class token (for
    # example ``qa``) in environment_ref. Preserve only that narrow case; an
    # opaque identity or URL remains unknown and therefore fail-closed.
    legacy_ref = _text(rc.get("environment_ref")).lower()
    if is_production_environment(legacy_ref) or is_test_or_sandbox_environment(legacy_ref):
        return legacy_ref
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


def _extract_resource_id(body: Any, path: str = "") -> str:
    """Extract a created-resource identity without domain-specific field names."""
    if not isinstance(body, dict):
        return ""
    candidates = [body]
    for envelope in ("data", "result", "item", "resource"):
        nested = body.get(envelope)
        if isinstance(nested, dict):
            candidates.append(nested)

    segment = _collection_path(path).rstrip("/").rsplit("/", 1)[-1]
    normalized_segment = re.sub(r"[^a-z0-9]", "", segment.lower())
    stems = {normalized_segment}
    if normalized_segment.endswith("ies") and len(normalized_segment) > 3:
        stems.add(normalized_segment[:-3] + "y")
    if normalized_segment.endswith("s") and len(normalized_segment) > 1:
        stems.add(normalized_segment[:-1])
    preferred_keys = {f"{stem}id" for stem in stems if stem}

    for source in candidates:
        normalized_keys = {
            re.sub(r"[^a-z0-9]", "", str(key).lower()): key
            for key in source
        }
        for normalized in ("id", "uuid", "pk", *sorted(preferred_keys)):
            original = normalized_keys.get(normalized)
            if original is None:
                continue
            value = source.get(original)
            if value not in (None, "", [], {}):
                return str(value)
    # When exactly one scalar ``*Id`` field exists, it is the only
    # source-grounded created identity available. Multiple candidates are
    # ambiguous and must not be guessed.
    suffix_matches: list[Any] = []
    for source in candidates:
        for key, value in source.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized.endswith("id") and value not in (None, "", [], {}) and not isinstance(value, (dict, list)):
                suffix_matches.append(value)
    unique = list(dict.fromkeys(str(value) for value in suffix_matches))
    if len(unique) == 1:
        return unique[0]
    return ""


def _append_audit(root: Path, project: str, record: dict[str, Any]) -> Path:
    audit_dir = root / "platform_workspace" / project / "defect_discovery"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "sandbox_write_audit.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def execute_governed_control_write(
    *,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    operation_phase: str,
    actor_identity: str,
    actor_token: str,
    method: str,
    path: str,
    body: Any,
    observation_path: str,
) -> dict[str, Any]:
    """Execute one explicitly declared non-production control write.

    Evaluation fixture reset endpoints use this path so setup and cleanup are
    independently governed and auditable around the product scan. The function
    never guesses a route and never returns success without a real 2xx receipt.
    """

    method = _text(method).upper()
    path = normalize_path_placeholders(_text(path)).split("?", 1)[0]
    observation_path = normalize_path_placeholders(_text(observation_path)).split("?", 1)[0]
    phase = _text(operation_phase)
    if method not in _WRITE_METHODS:
        raise ValueError("governed control write method must be POST, PUT, PATCH, or DELETE")
    if not path.startswith("/") or path_has_placeholders(path):
        raise ValueError("governed control write path must be one concrete declared path")
    if not observation_path.startswith("/") or path_has_placeholders(observation_path):
        raise ValueError("governed control observation path must be one concrete declared path")
    if not phase:
        raise ValueError("governed control write requires operation_phase")

    allowed, reason = sandbox_write_allowed(
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        actor_token=actor_token,
        actor_identity=actor_identity,
    )
    base = _text(base_url).rstrip("/")
    before = _http_request("GET", base + observation_path, token=actor_token) if allowed else {}
    write = _http_request(method, base + path, token=actor_token, body=body) if allowed else {}
    after = _http_request("GET", base + observation_path, token=actor_token) if allowed else {}
    accepted = allowed and 200 <= int(write.get("status") or 0) < 300
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor_role": actor_identity,
        "method": method,
        "path": path,
        "before_ref": f"control_before:{observation_path}:{before.get('status', 0)}",
        "after_ref": f"control_after:{observation_path}:{after.get('status', 0)}",
        "cleanup_status": "completed" if accepted and "cleanup" in phase.lower() else "not_applicable",
        "cleanup_reason": "" if accepted else reason if not allowed else "control_write_not_accepted",
        "operation_phase": phase,
        "operation_accepted": accepted,
        "campaign_id": campaign_id,
        "slice_id": "evaluation_fixture_control",
        "environment_kind": resolve_environment_kind(root, project, runtime_contract),
        "approved_base_url": _text(runtime_contract.get("approved_base_url")),
        "http_status": int(write.get("status") or 0),
    }
    audit_path = _append_audit(root, project, record)
    return {
        "status": "executed" if accepted else "blocked" if not allowed else "failed",
        "reason": "accepted" if accepted else record["cleanup_reason"],
        "accepted": accepted,
        "method": method,
        "path": path,
        "before": before,
        "write": write,
        "after": after,
        "before_ref": record["before_ref"],
        "after_ref": record["after_ref"],
        "audit_path": str(audit_path),
        "audit_record": record,
        "production_http_requests": 0,
    }


def _is_authentication_step(step: Any) -> bool:
    action = _text(getattr(step, "action", "") or "").strip().lower()
    if action == "login" or action.startswith("login_") or action.endswith("_login"):
        return True
    path = _text(getattr(step, "api_path", "") or "").strip().lower()
    return path.endswith("/login") or "/auth/login" in path


def _is_fixture_bootstrap_step(step: Any) -> bool:
    """Identity bootstrap POSTs are fixtures, not the scenario's primary write."""
    action = _text(getattr(step, "action", "") or "").strip().lower()
    return action.startswith("bootstrap_create_")


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
        if _is_authentication_step(step) or _is_fixture_bootstrap_step(step):
            continue
        method = _text(getattr(step, "api_method", "") or "").upper()
        path = _text(getattr(step, "api_path", "") or "")
        if method in _WRITE_METHODS and path.startswith("/"):
            return method, path, getattr(step, "body_template", None)
    return None


def _scenario_write_steps(scenario: Any) -> list[tuple[str, str, Any]]:
    """Return every source-declared non-authentication write in execution order."""
    writes: list[tuple[str, str, Any]] = []
    for step in getattr(scenario, "steps", []) or []:
        if _is_authentication_step(step):
            continue
        method = _text(getattr(step, "api_method", "") or "").upper()
        path = _text(getattr(step, "api_path", "") or "")
        if method in _WRITE_METHODS and path.startswith("/"):
            writes.append((method, path, getattr(step, "body_template", None)))
    return writes


def _execute_fn_accepts_write_observer(execute_fn: Callable[..., dict[str, Any]]) -> bool:
    try:
        parameters = inspect.signature(execute_fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.name == "write_observer" for parameter in parameters)


def _documented_observation_path(
    scenario: Any,
    write_path: str,
    documented_routes: list[dict[str, Any]] | None,
) -> str:
    """Choose a source-declared GET observer; never invent a read endpoint."""
    normalized_write = normalize_path_placeholders(write_path).split("?", 1)[0]
    write_collection = documented_collection_path(normalized_write) or normalized_write
    candidates: list[tuple[int, int, str]] = []

    # Explicit scenario resolver/observer steps are strongest because they were
    # generated from the same source-bound behavior slice.
    for index, step in enumerate(getattr(scenario, "steps", []) or []):
        method = _text(getattr(step, "api_method", "") or "").upper()
        path = normalize_path_placeholders(_text(getattr(step, "api_path", "") or "")).split("?", 1)[0]
        if method in _WRITE_METHODS and not _is_authentication_step(step) and not _is_fixture_bootstrap_step(step):
            break
        if method not in {"GET", "HEAD"} or not path.startswith("/"):
            continue
        if path_has_placeholders(path):
            path = documented_collection_path(path)
        if not path or path_has_placeholders(path):
            continue
        relation_rank = 0 if path == write_collection else (1 if write_collection.startswith(path.rstrip("/") + "/") else 2)
        candidates.append((relation_rank, index, path))

    for index, route in enumerate(documented_routes or []):
        if not isinstance(route, dict) or _text(route.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        path = normalize_path_placeholders(_text(route.get("path"))).split("?", 1)[0]
        if not path.startswith("/"):
            continue
        route_collection = documented_collection_path(path) or path
        if path_has_placeholders(path):
            # A documented detail route such as ``GET /items/{id}`` does not
            # prove that ``GET /items`` exists.  Before a create we do not yet
            # have the server identity, so fail observably instead of inventing
            # a collection endpoint from the detail template.
            continue
        if path == write_collection:
            relation_rank = 0
        elif route_collection == write_collection:
            relation_rank = 1
        elif write_collection.startswith(path.rstrip("/") + "/"):
            relation_rank = 2
        else:
            continue
        # Route-catalog candidates follow explicit scenario steps at equal rank.
        candidates.append((relation_rank, 10_000 + index, path))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][2]


def execute_governed_fixture_lifecycle(
    *,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    slice_id: str,
    actor_identity: str,
    actor_token: str,
    observation_path: str,
    setup_execute_fn: Callable[[], list[dict[str, Any]]],
    cleanup_execute_fn: Callable[[], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute a disposable fixture lifecycle behind the write safety gate.

    The fixture planner owns request rendering and response-id binding.  This
    wrapper owns the non-production decision, before/after observations,
    cleanup outcome, campaign/slice/target identity and durable audit.  Both
    callbacks are invoked only after the shared write gate succeeds.
    """
    allowed, reason = sandbox_write_allowed(
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        actor_token=actor_token,
        actor_identity=actor_identity,
    )
    if not allowed:
        return {
            "status": "blocked",
            "reason": reason,
            "setup_receipts": [],
            "cleanup_receipts": [],
            "audit_path": "",
        }

    base = base_url.rstrip("/")
    documented_observer = normalize_path_placeholders(observation_path).split("?", 1)[0]
    if not documented_observer.startswith("/") or path_has_placeholders(documented_observer):
        documented_observer = ""

    def observe() -> dict[str, Any]:
        if not documented_observer:
            return {
                "status": 0,
                "body": {},
                "headers": {},
                "error": "documented_observer_missing",
            }
        return _http_request("GET", base + documented_observer, token=actor_token)

    before = observe()
    setup_receipts = list(setup_execute_fn() or [])
    after_setup = observe()
    setup_ok = _fixture_receipts_accepted(setup_receipts)
    setup_attempted = any(
        isinstance(receipt, dict) and receipt.get("status") == "executed"
        for receipt in setup_receipts
    )
    setup_accepted_any = any(
        isinstance(receipt, dict) and receipt.get("accepted") is True
        for receipt in setup_receipts
    )
    setup_statuses = [
        int(_as_dict(receipt.get("response")).get("status_code") or 0)
        for receipt in setup_receipts
        if isinstance(receipt, dict) and receipt.get("status") == "executed"
    ]
    unchanged_observer = (
        200 <= int(before.get("status") or 0) < 300
        and 200 <= int(after_setup.get("status") or 0) < 300
        and before.get("body") == after_setup.get("body")
    )
    rejected_without_observed_mutation = (
        setup_attempted
        and not setup_accepted_any
        and bool(setup_statuses)
        and all(400 <= status < 500 for status in setup_statuses)
        and unchanged_observer
    )
    # A partially accepted setup can already have created state.  Always run
    # the source-planned cleanup after any executed setup attempt so a later
    # failed fixture request cannot strand disposable data. A fully rejected
    # 4xx setup may skip cleanup only when a documented observer proves state
    # remained unchanged.
    cleanup_needed = setup_attempted and not rejected_without_observed_mutation
    cleanup_receipts = list(cleanup_execute_fn() or []) if cleanup_needed else []
    after_cleanup = observe()
    cleanup_ok = setup_ok and _fixture_receipts_accepted(cleanup_receipts)
    cleanup_status = "completed" if cleanup_ok else (
        "not_required" if rejected_without_observed_mutation else (
            "failed" if setup_attempted else "not_applicable"
        )
    )
    cleanup_reason = "" if cleanup_ok else (
        "setup_rejected_observer_unchanged" if rejected_without_observed_mutation else (
            "fixture_cleanup_not_accepted" if setup_attempted else "fixture_setup_not_accepted"
        )
    )

    observer_ref = documented_observer or "documented_observer_missing"
    before_ref = f"sandbox_before:{observer_ref}:{before.get('status')}"
    after_setup_ref = f"sandbox_after_setup:{observer_ref}:{after_setup.get('status')}"
    after_cleanup_ref = f"sandbox_after_cleanup:{observer_ref}:{after_cleanup.get('status')}"
    audit_records: list[dict[str, Any]] = []

    for phase, receipts, phase_before_ref, phase_after_ref in (
        ("fixture_setup", setup_receipts, before_ref, after_setup_ref),
        ("fixture_cleanup", cleanup_receipts, after_setup_ref, after_cleanup_ref),
    ):
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            accepted = receipt.get("accepted") is True
            audit_records.append(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "actor_role": actor_identity,
                    "method": _text(receipt.get("method")).upper(),
                    "path": _text(receipt.get("path")),
                    "before_ref": phase_before_ref,
                    "after_ref": phase_after_ref,
                    "cleanup_status": cleanup_status if phase == "fixture_setup" else (
                        "completed" if accepted else "failed"
                    ),
                    "cleanup_reason": cleanup_reason if phase == "fixture_setup" else (
                        "" if accepted else "fixture_cleanup_operation_not_accepted"
                    ),
                    "operation_phase": phase,
                    "operation_accepted": accepted,
                    "campaign_id": campaign_id,
                    "slice_id": slice_id,
                    "environment_kind": resolve_environment_kind(root, project, runtime_contract),
                    "approved_base_url": _text(runtime_contract.get("approved_base_url")),
                }
            )

    audit_path = ""
    for record in audit_records:
        audit_path = str(_append_audit(root, project, record))
    return {
        "status": "completed" if cleanup_ok else "cleanup_incomplete",
        "reason": "fixture_lifecycle_completed" if cleanup_ok else cleanup_reason,
        "setup_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "before": before,
        "after_setup": after_setup,
        "after_cleanup": after_cleanup,
        "cleanup": {"status": cleanup_status, "reason": cleanup_reason},
        "audit_path": audit_path,
        "audit_records": audit_records,
    }


def _fixture_receipts_accepted(receipts: list[dict[str, Any]]) -> bool:
    """Require every planned fixture write to have an accepted HTTP result."""
    if not receipts:
        return False
    return all(
        isinstance(receipt, dict)
        and receipt.get("status") == "executed"
        and receipt.get("accepted") is True
        for receipt in receipts
    )


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


def _documented_delete_cleanup_path(
    created_path: str,
    resource_id: str,
    documented_routes: list[dict[str, Any]] | None,
) -> str:
    """Resolve a DELETE cleanup route from source facts, or return empty."""
    if not resource_id:
        return ""
    normalized_created = normalize_path_placeholders(created_path).split("?", 1)[0]
    created_collection = documented_collection_path(normalized_created) or normalized_created
    candidates: list[str] = []
    for route in documented_routes or []:
        if not isinstance(route, dict) or _text(route.get("method")).upper() != "DELETE":
            continue
        route_path = normalize_path_placeholders(_text(route.get("path"))).split("?", 1)[0]
        if not route_path.startswith("/"):
            continue
        route_collection = documented_collection_path(route_path) or route_path
        if route_collection != created_collection:
            continue
        placeholders = re.findall(r"\{[A-Za-z_]\w*\}", route_path)
        if len(placeholders) != 1:
            continue
        candidates.append(route_path.replace(placeholders[0], str(resource_id)))
    return sorted(set(candidates), key=lambda item: (item.count("/"), item))[0] if candidates else ""


def _is_action_style_write_path(path: str) -> bool:
    """True when the write targets an existing resource action, not a create.

    Industry-generic: collection POST ``/api/orders`` creates; nested action
    ``/api/orders/{id}/cancel`` mutates an existing entity and does not create a
    new deletable resource identity even if the response echoes ``id``.
    """
    normalized = normalize_path_placeholders(_text(path)).split("?", 1)[0]
    parts = [p for p in normalized.strip("/").split("/") if p]
    if len(parts) < 3:
        return False
    # Identity segment then trailing action verb(s).
    for index, part in enumerate(parts):
        if part.startswith("{") and part.endswith("}") and index < len(parts) - 1:
            return True
        # Concrete id already bound: UUID / numeric / opaque token mid-path.
        if index < len(parts) - 1 and (
            re.fullmatch(r"[0-9a-fA-F-]{8,}", part)
            or re.fullmatch(r"\d+", part)
        ):
            return True
    return False


def _cleanup_after_write(
    *,
    method: str,
    path: str,
    base_url: str,
    token: str,
    before_body: Any,
    write_body: Any,
    documented_routes: list[dict[str, Any]] | None = None,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Honest cleanup — never report completed on missing cleanup evidence."""
    base = base_url.rstrip("/")
    retry_count = max(0, min(int(retry_count or 0), 3))

    def _cleanup_request(method_name: str, url: str, *, body: Any = None) -> tuple[dict[str, Any], int]:
        receipt: dict[str, Any] = {}
        for attempt in range(retry_count + 1):
            receipt = _http_request(method_name, url, token=token, body=body)
            status_code = int(receipt.get("status") or 0)
            if 200 <= status_code < 300 or (method_name == "DELETE" and status_code == 404):
                return receipt, attempt + 1
        return receipt, retry_count + 1

    if method == "POST":
        if _is_action_style_write_path(path):
            return {
                "status": "not_reversible",
                "strategy": "action_post_on_existing_resource",
                "receipt_ref": path,
                "warning": "action_style_write_no_created_resource",
            }
        resource_id = _extract_resource_id(write_body, path)
        if not resource_id:
            # Action-style POST (validate/pay/reserve) with no created identity.
            return {
                "status": "not_reversible",
                "strategy": "action_post_no_created_resource",
                "receipt_ref": path,
                "warning": "created_resource_id_missing",
            }
        delete_path = _documented_delete_cleanup_path(path, resource_id, documented_routes)
        if not delete_path:
            # Create succeeded but source docs declare no DELETE cleanup route.
            return {
                "status": "not_reversible",
                "strategy": "delete_created_resource",
                "receipt_ref": path,
                "warning": "documented_cleanup_route_missing",
            }
        receipt, attempts = _cleanup_request("DELETE", base + delete_path)
        status = int(receipt.get("status") or 0)
        ok = 200 <= status < 300 or status in {204, 404}
        result = {
            "status": "completed" if ok else "failed",
            "strategy": "delete_created_resource",
            "receipt_ref": delete_path,
            "receipt": {"status": status},
            "attempts": attempts,
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
        receipt, attempts = _cleanup_request(method, base + path, body=before_body)
        status = int(receipt.get("status") or 0)
        ok = 200 <= status < 300
        result = {
            "status": "completed" if ok else "failed",
            "strategy": "restore_before_snapshot",
            "receipt_ref": path,
            "receipt": {"status": status},
            "attempts": attempts,
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


def _execute_with_per_write_governance(
    scenario: Any,
    base_url: str,
    *,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    safety_boundary: dict[str, Any] | None,
    observer_token: str,
    documented_routes: list[dict[str, Any]] | None,
    execute_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Observe, compensate, and audit every write in one source scenario."""
    base = base_url.rstrip("/")
    events: list[dict[str, Any]] = []

    def write_observer(phase: str, payload: dict[str, Any]) -> int | None:
        if phase == "before":
            method = _text(payload.get("method")).upper()
            path = _text(payload.get("path"))
            if method not in _WRITE_METHODS or not path.startswith("/"):
                raise RuntimeError(f"invalid_governed_write_event:{method}:{path}")
            if safety_boundary:
                exclusion = match_production_data_exclusion(safety_boundary, path, "")
                if exclusion:
                    raise RuntimeError(f"governed_write_blocked:{exclusion}:{method}:{path}")
            token = _text(observer_token or payload.get("token"))
            observe_path = _documented_observation_path(scenario, path, documented_routes)
            before = (
                _http_request("GET", base + observe_path, token=token)
                if observe_path
                else {
                    "status": 0,
                    "body": {},
                    "headers": {},
                    "error": "documented_observer_missing",
                }
            )
            event_id = len(events)
            events.append(
                {
                    "event_id": event_id,
                    "action": _text(payload.get("action")),
                    "method": method,
                    "path": path,
                    "request_body": payload.get("body"),
                    "token": token,
                    "observe_path": observe_path,
                    "before": before,
                    "after_hook_received": False,
                }
            )
            return event_id
        if phase == "after":
            event_id = payload.get("event_id")
            if not isinstance(event_id, int) or event_id < 0 or event_id >= len(events):
                raise RuntimeError(f"invalid_governed_write_event_id:{event_id}")
            event = events[event_id]
            if event["after_hook_received"]:
                raise RuntimeError(f"duplicate_governed_write_after_event:{event_id}")
            event["after_hook_received"] = True
            event["status"] = int(payload.get("status") or 0)
            event["response_body"] = payload.get("response_body")
            event["path"] = _text(payload.get("path")) or event["path"]
            return None
        raise RuntimeError(f"unsupported_governed_write_phase:{phase}")

    execution_exception = ""
    try:
        trace = execute_fn(
            scenario,
            base_url,
            safety_boundary=safety_boundary,
            write_observer=write_observer,
        )
    except Exception as exc:
        execution_exception = f"{type(exc).__name__}:{exc}"
        trace = {
            "scenario_id": _text(getattr(scenario, "id", "") or "?"),
            "steps": [],
            "errors": [f"governed_multi_write_execution_failed:{execution_exception}"],
        }
    from .policy_wiring import get_policy_value

    retry_count = int(get_policy_value("execution", "cleanup_retry_count", 1) or 0)
    audit_records: list[dict[str, Any]] = []
    governed_writes: list[dict[str, Any]] = []
    audit_path = ""
    for event in reversed(events):
        observe_path = _text(event.get("observe_path"))
        after = (
            _http_request("GET", base + observe_path, token=_text(event.get("token")))
            if observe_path
            else {
                "status": 0,
                "body": {},
                "headers": {},
                "error": "documented_observer_missing",
            }
        )
        event["after"] = after
        status = int(event.get("status") or 0)
        observer_proves_unchanged = (
            200 <= int(_as_dict(event.get("before")).get("status") or 0) < 300
            and 200 <= int(after.get("status") or 0) < 300
            and _as_dict(event.get("before")).get("body") == after.get("body")
        )
        if not event.get("after_hook_received"):
            cleanup = {
                "status": "failed",
                "strategy": "per_write_hook_integrity",
                "receipt_ref": event["path"],
                "error": "write_after_hook_missing",
            }
        elif not 200 <= status < 300 and observer_proves_unchanged:
            cleanup = {
                "status": "not_required",
                "strategy": "rejected_write_observer_unchanged",
                "receipt_ref": event["path"],
            }
        else:
            cleanup = _cleanup_after_write(
                method=event["method"],
                path=event["path"],
                base_url=base_url,
                token=_text(event.get("token")),
                before_body=_as_dict(event.get("before")).get("body"),
                write_body=event.get("response_body"),
                documented_routes=documented_routes,
                retry_count=retry_count,
            )
        observer_ref = observe_path or "documented_observer_missing"
        before_ref = f"sandbox_before:{observer_ref}:{_as_dict(event.get('before')).get('status')}"
        after_ref = f"sandbox_after:{observer_ref}:{after.get('status')}"
        audit_record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actor_role": _text(getattr(scenario, "actor_role", "") or trace.get("actor_role") or ""),
            "method": event["method"],
            "path": event["path"],
            "before_ref": before_ref,
            "after_ref": after_ref,
            "cleanup_status": cleanup.get("status"),
            "cleanup_receipt_ref": cleanup.get("receipt_ref") or "",
            "operation_accepted": 200 <= status < 300,
            "campaign_id": campaign_id,
            "slice_id": _text(getattr(scenario, "behavior_slice_id", "") or ""),
            "environment_kind": resolve_environment_kind(root, project, runtime_contract),
            "approved_base_url": _text(runtime_contract.get("approved_base_url")),
        }
        audit_path = str(_append_audit(root, project, audit_record))
        audit_records.append(audit_record)
        governed_writes.append(
            {
                "event_id": event["event_id"],
                "action": event["action"],
                "method": event["method"],
                "path": event["path"],
                "status": status,
                "before": {"status": _as_dict(event.get("before")).get("status"), "ref": before_ref},
                "after": {"status": after.get("status"), "ref": after_ref},
                "cleanup": cleanup,
            }
        )
    governed_writes.sort(key=lambda item: int(item["event_id"]))
    audit_records.reverse()
    cleanup_statuses = [_text(_as_dict(item.get("cleanup")).get("status")).lower() for item in governed_writes]
    cleanup_complete = bool(governed_writes) and all(
        status in {"completed", "verified", "not_required"} for status in cleanup_statuses
    )
    lifecycle_complete = not execution_exception and cleanup_complete
    aggregate_cleanup_status = (
        "completed"
        if cleanup_complete
        else ("not_reversible" if "not_reversible" in cleanup_statuses else "failed")
    )
    primary = governed_writes[0] if governed_writes else {}
    trace["sandbox_write"] = {
        "status": "completed" if lifecycle_complete else "cleanup_incomplete",
        "reason": (
            "per_write_execution_failed"
            if execution_exception
            else ("per_write_lifecycle_completed" if lifecycle_complete else "per_write_cleanup_incomplete")
        ),
        "before": primary.get("before") or {},
        "after": primary.get("after") or {},
        "cleanup": {
            "status": aggregate_cleanup_status,
            "receipt_ref": audit_path if lifecycle_complete else "",
            "write_count": len(governed_writes),
        },
        "writes": governed_writes,
        "audit_records": audit_records,
        "audit_path": audit_path,
        "governed_write_receipt_count": len(audit_records),
        "execution_exception": execution_exception,
    }
    if not governed_writes:
        trace["sandbox_write"].update(
            {
                "status": "not_executed",
                "reason": "no_write_request_reached_transport",
                "cleanup": {"status": "not_applicable", "receipt_ref": "", "write_count": 0},
            }
        )
    return trace


def execute_with_sandbox_write(
    scenario: Any,
    base_url: str,
    *,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    campaign_id: str = "",
    safety_boundary: dict[str, Any] | None = None,
    observer_token: str = "",
    documented_routes: list[dict[str, Any]] | None = None,
    execute_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Wrap ``execute_fn`` with before/after/cleanup when sandbox write is allowed.

    Read-only scenarios continue through the normal executor. A write scenario
    that fails any gate is returned as blocked without firing the request.
    """
    write_steps = _scenario_write_steps(scenario)
    primary_write = _first_write_step(scenario)
    write_meta = primary_write or (write_steps[0] if write_steps else None)
    if write_meta is None:
        return execute_fn(scenario, base_url, safety_boundary=safety_boundary)
    token = _text(getattr(scenario, "actor_token", "") or "")
    observation_token = token or _text(observer_token)
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
        actor_token=observation_token,
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

    requires_per_write_governance = len(write_steps) > 1 or primary_write is None or any(
        _is_fixture_bootstrap_step(step)
        for step in (getattr(scenario, "steps", []) or [])
        if _text(getattr(step, "api_method", "") or "").upper() in _WRITE_METHODS
    )
    if requires_per_write_governance:
        if not _execute_fn_accepts_write_observer(execute_fn):
            return _blocked_write_trace(
                scenario,
                "multi_write_executor_missing_per_write_governance_hook",
                write_meta,
            )
        return _execute_with_per_write_governance(
            scenario,
            base_url,
            root=root,
            project=project,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            safety_boundary=safety_boundary,
            observer_token=observation_token,
            documented_routes=documented_routes,
            execute_fn=execute_fn,
        )

    base = base_url.rstrip("/")
    observe_path = _documented_observation_path(scenario, path, documented_routes)
    before = (
        _http_request("GET", base + observe_path, token=observation_token)
        if observe_path
        else {"status": 0, "body": {}, "headers": {}, "error": "documented_observer_missing"}
    )
    trace = execute_fn(scenario, base_url, safety_boundary=safety_boundary)
    after = (
        _http_request("GET", base + observe_path, token=observation_token)
        if observe_path
        else {"status": 0, "body": {}, "headers": {}, "error": "documented_observer_missing"}
    )

    write_body: Any = {}
    executed_path = path
    for step in trace.get("steps") or []:
        if not isinstance(step, dict) or _text(step.get("method")).upper() != method:
            continue
        step_action = _text(step.get("action")).lower()
        step_path = _text(step.get("path"))
        if step_action == "login" or step_action.startswith("login_") or step_path.lower().endswith("/login") or "/auth/login" in step_path.lower():
            continue
        if step_action.startswith("bootstrap_create_"):
            continue
        if step_path.startswith("/") and not path_has_placeholders(normalize_path_placeholders(step_path)):
            executed_path = step_path
        write_body = _as_dict(step.get("response")).get("body")
        break

    from .policy_wiring import get_policy_value

    cleanup = _cleanup_after_write(
        method=method,
        path=executed_path,
        base_url=base_url,
        token=observation_token,
        before_body=before.get("body"),
        write_body=write_body,
        documented_routes=documented_routes,
        retry_count=int(get_policy_value("execution", "cleanup_retry_count", 1) or 0),
    )
    observer_ref = observe_path or "documented_observer_missing"
    before_ref = f"sandbox_before:{observer_ref}:{before.get('status')}"
    after_ref = f"sandbox_after:{observer_ref}:{after.get('status')}"
    evidence = {
        "before_snapshot_ref": before_ref,
        "after_snapshot_ref": after_ref,
        "cleanup": {
            "status": cleanup.get("status"),
            "receipt_ref": cleanup.get("receipt_ref") or "",
        },
        "method": method,
        "path": executed_path,
    }
    audit_record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor_role": _text(getattr(scenario, "actor_role", "") or trace.get("actor_role") or ""),
        "method": method,
        "path": executed_path,
        "before_ref": before_ref,
        "after_ref": after_ref,
        "cleanup_status": cleanup.get("status"),
        "campaign_id": campaign_id,
        "slice_id": _text(getattr(scenario, "behavior_slice_id", "") or ""),
        "environment_kind": resolve_environment_kind(root, project, runtime_contract),
        "approved_base_url": _text(runtime_contract.get("approved_base_url")),
    }
    audit_path = _append_audit(root, project, audit_record)

    cleanup_status = _text(cleanup.get("status")).lower()
    # Only a completed/verified compensation is a clean terminal state.
    # ``not_required`` is reserved for a rejected request whose source observer
    # proves no mutation; this legacy single-write path cannot prove that here.
    write_status = (
        "completed"
        if cleanup_status in {"completed", "verified"}
        else "cleanup_incomplete"
    )
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
