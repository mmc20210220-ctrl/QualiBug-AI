"""Legacy V12 scenario HTTP execution.

Moved out of ``v12_pipeline`` so the compatibility wrapper stays a thin
mainline facade. Symbols remain importable from ``v12_pipeline``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .disposable_identity_materializer import (
    disposable_identity_nonce,
    materialize_disposable_identity_fields,
)
from .enterprise_project_config import (
    _load_execution_safety_boundary,
    match_production_data_exclusion,
)
from .real_id_resolver import (
    alternate_collection_paths,
    bind_entity_fields,
    bind_path_params_from_documented_body,
    collection_path,
    infer_path_params,
    normalize_path_placeholders,
    param_field_candidates,
    path_has_placeholders,
)

logger = logging.getLogger(__name__)

_SENSITIVE = {
    "authorization",
    "token",
    "password",
    "secret",
    "cookie",
    "api_key",
    "apikey",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _record_v12_har(
    method: str,
    url: str,
    status: int,
    body: Any,
    actor: str = "",
    elapsed_ms: float = 0.0,
) -> None:
    from ai_test_asset_center.v12_pipeline import _record_v12_har as _har

    _har(method, url, status, body, actor=actor, elapsed_ms=elapsed_ms)


def _execute_scenario(
    scenario: Any,
    base_url: str,
    max_retries: int = 2,
    safety_boundary: dict[str, Any] | None = None,
    write_observer: Any = None,
) -> dict[str, Any]:
    # Retrying an entire write scenario can duplicate already-accepted writes.
    # Read-only scenarios retain bounded retry behavior; write scenarios execute
    # once and surface the original failure.
    has_write = any(
        str(getattr(step, "api_method", "") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and str(getattr(step, "action", "") or "").strip().lower() != "login"
        for step in (getattr(scenario, "steps", []) or [])
    )
    attempt_limit = 0 if has_write else max(0, int(max_retries or 0))
    for attempt in range(attempt_limit + 1):
        try:
            return __execute_scenario_once(
                scenario,
                base_url,
                safety_boundary=safety_boundary,
                write_observer=write_observer,
            )
        except Exception as exc:
            if attempt < attempt_limit:
                time.sleep(0.5 * (attempt + 1))
                continue
            return {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": [f"failed_after_retries:{exc}"], "duration_ms": 0}
    return {"scenario_id": "?", "steps": [], "errors": ["unreachable"], "duration_ms": 0}


def _resolve_get_candidates(path: str) -> list[str]:
    """Resolve-step GET URLs: bare collection first, then paginated variants."""
    base = str(path or "").split("?", 1)[0]
    if not base:
        return []
    from .real_id_resolver import _PAGINATION_SUFFIXES

    candidates = [base] if "?" not in str(path or "") else [str(path)]
    if "?" not in base:
        candidates.extend(base + suffix for suffix in _PAGINATION_SUFFIXES)
    return list(dict.fromkeys(item for item in candidates if item.startswith("/")))


def _read_observer_action(action_name: str) -> bool:
    action = str(action_name or "").strip().lower()
    return (
        action.startswith("observe_")
        or action.startswith("verify_")
        or "observer" in action
        or "observation" in action
    )


def _entity_candidates_from_response(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [dict(item) for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    for field in ("records", "data", "items", "results", "list", "rows"):
        value = body.get(field)
        if isinstance(value, list):
            rows = [dict(item) for item in value if isinstance(item, dict)]
            if rows:
                return rows
        if isinstance(value, dict):
            nested = _entity_candidates_from_response(value)
            if nested:
                return nested
    for value in body.values():
        nested = _entity_candidates_from_response(value)
        if nested:
            return nested
    return []


def _identity_binding_keys(path_template: str, bindings: dict[str, Any]) -> list[str]:
    params = infer_path_params(path_template)
    if params:
        return params
    priority = ["id", "uuid", "code", "sku", "orderId", "order_id"]
    discovered = [
        str(key)
        for key, value in bindings.items()
        if value not in (None, "", [], {})
        and (
            str(key).lower() in {"id", "uuid", "code", "sku"}
            or str(key).lower().endswith("id")
            or str(key).lower().endswith("_id")
        )
    ]
    return list(dict.fromkeys([*priority, *discovered]))


def _binding_value_for_key(key: str, bindings: dict[str, Any]) -> Any:
    for alias in param_field_candidates(key):
        value = bindings.get(alias)
        if value not in (None, "", [], {}):
            return value
    value = bindings.get(key)
    if value not in (None, "", [], {}):
        return value
    return None


def _entity_matches_runtime_binding(entity: dict[str, Any], key: str, expected: Any) -> bool:
    if expected in (None, "", [], {}):
        return False
    expected_text = str(expected)
    for field in param_field_candidates(key):
        value = entity.get(field)
        if value not in (None, "", [], {}) and str(value) == expected_text:
            return True
    return False


def _project_bound_observer_entity(
    body: Any,
    *,
    path_template: str,
    bindings: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return the bound entity from a collection response, preserving trace metadata."""

    candidates = _entity_candidates_from_response(body)
    if not candidates:
        return None, {}
    keys = _identity_binding_keys(path_template, bindings)
    matched_keys: list[str] = []
    for key in keys:
        expected = _binding_value_for_key(key, bindings)
        if expected in (None, "", [], {}):
            continue
        for index, entity in enumerate(candidates):
            if _entity_matches_runtime_binding(entity, key, expected):
                matched_keys.append(key)
                return dict(entity), {
                    "projection": "bound_entity_from_collection",
                    "matched_key": key,
                    "matched_value": str(expected),
                    "candidate_index": index,
                    "candidate_count": len(candidates),
                }
    return None, {
        "projection": "bound_entity_from_collection_not_found",
        "candidate_count": len(candidates),
        "binding_keys": keys,
    }


def _declared_get_hints(scenario: Any) -> set[str]:
    hints = getattr(scenario, "runtime_hints", {}) or {}
    if not isinstance(hints, dict):
        return set()
    values = hints.get("declared_get_paths") or hints.get("declared_read_paths") or []
    if not isinstance(values, list):
        return set()
    return {
        normalize_path_placeholders(str(item or "")).split("?", 1)[0]
        for item in values
        if str(item or "").startswith("/")
    }


def _observer_collection_fallback_paths(path_template: str, concrete_path: str, scenario: Any) -> list[str]:
    normalized_template = normalize_path_placeholders(path_template).split("?", 1)[0]
    if not path_has_placeholders(normalized_template):
        return []
    primary = collection_path(normalized_template)
    candidates = [primary] if primary else []
    candidates.extend(alternate_collection_paths(normalized_template))
    declared = _declared_get_hints(scenario)
    concrete = str(concrete_path or "").split("?", 1)[0].rstrip("/")
    filtered: list[str] = []
    for candidate in candidates:
        path = normalize_path_placeholders(str(candidate or "")).split("?", 1)[0]
        if not path.startswith("/") or path_has_placeholders(path):
            continue
        if path.rstrip("/") == concrete:
            continue
        if declared and path not in declared:
            continue
        filtered.append(path)
    return list(dict.fromkeys(filtered))


def _encoded_request_url(base_url: str, path: str) -> str:
    """Percent-encode non-ASCII route/query text without changing separators."""

    raw_path, separator, raw_query = str(path or "").partition("?")
    encoded_path = urllib.parse.quote(raw_path, safe="/%:@-._~!$&'()*+,;=")
    encoded_query = urllib.parse.quote(raw_query, safe="=&;%:@/?-._~!$'()*+,") if separator else ""
    suffix = f"?{encoded_query}" if separator else ""
    return base_url.rstrip("/") + encoded_path + suffix


def _body_has_unbound_placeholders(body: Any) -> list[str]:
    text = json.dumps(body, ensure_ascii=False) if body else ""
    return list(dict.fromkeys(re.findall(r"\{([A-Za-z_]\w*)\}", text)))


def _coerce_runtime_amount(value: Any, original: Any) -> Any:
    text = str(value).strip()
    if not text:
        return original
    try:
        number = float(text)
    except (TypeError, ValueError):
        return value
    if isinstance(original, int) and not isinstance(original, bool) and number.is_integer():
        return int(number)
    if isinstance(original, float):
        return float(number)
    if number.is_integer():
        return int(number)
    return number


def _runtime_amount_binding(bindings: dict[str, Any]) -> Any:
    for key in (
        "payable_amount",
        "payableAmount",
        "amount_due",
        "amountDue",
        "due_amount",
        "dueAmount",
        "total_amount",
        "totalAmount",
        "amount",
    ):
        value = bindings.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _is_money_control_step(scenario: Any, action_name: str) -> bool:
    category = str(getattr(scenario, "category", "") or "").strip().lower()
    if "money" in category or "financial" in category or "conservation" in category:
        return True
    semantic_text = " ".join([
        str(action_name or ""),
        str(getattr(scenario, "entity", "") or ""),
        " ".join(str(item or "") for item in (getattr(scenario, "oracle_rules", []) or [])),
    ]).lower()
    semantic_tokens = set(re.findall(r"[a-z][a-z0-9_]*", semantic_text))
    financial_tokens = {
        "amount",
        "billing",
        "capture",
        "charge",
        "checkout",
        "invoice",
        "money",
        "pay",
        "payment",
        "payout",
        "refund",
        "remittance",
        "settle",
        "settlement",
        "transaction",
    }
    if semantic_tokens.intersection(financial_tokens):
        return True
    action = str(action_name or "").lower()
    return any(
        token in action
        for token in (
            "money",
            "payment",
            "refund",
            "pay",
            "charge",
            "capture",
            "settle",
            "checkout",
        )
    )


def _bind_runtime_amount_controls(body: Any, bindings: dict[str, Any], scenario: Any, action_name: str) -> Any:
    if not isinstance(body, dict) or not _is_money_control_step(scenario, action_name):
        return body
    amount = _runtime_amount_binding(bindings)
    if amount in (None, "", [], {}):
        return body
    out: dict[str, Any] = {}
    for key, value in body.items():
        key_l = str(key).strip().lower()
        if isinstance(value, dict):
            out[key] = _bind_runtime_amount_controls(value, bindings, scenario, action_name)
        elif isinstance(value, list):
            out[key] = [
                _bind_runtime_amount_controls(item, bindings, scenario, action_name)
                if isinstance(item, dict) else item
                for item in value
            ]
        elif key_l == "amount" or key_l.endswith("amount") or key_l.endswith("_amount"):
            out[key] = _coerce_runtime_amount(amount, value)
        else:
            out[key] = value
    return out


def _disposable_fixture_nonce(scenario: Any, step: Any, action_name: str) -> str:
    return disposable_identity_nonce(
        getattr(scenario, "id", "") or "",
        getattr(step, "order", "") or "",
        action_name,
    )


def _materialize_disposable_identity_fixture_body(value: Any, nonce: str, prefix: str = "") -> tuple[Any, list[str]]:
    """Replace identity-create fixture literals/placeholders with one-run values.

    This is intentionally scoped to ``bootstrap_create_*`` runtime steps. Login
    bodies and business mutation bodies keep their documented values; only the
    disposable fixture create request gets unique identity fields so repeated
    probes do not reuse demo accounts or collide with previous non-production
    writes.
    """
    return materialize_disposable_identity_fields(value, nonce, prefix=prefix)


def _governed_write_block_reason(value: Any) -> str:
    """Normalize sandbox write-governance blocks raised via write_observer."""

    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text
    if normalized.lower().startswith("runtimeerror:"):
        normalized = normalized.split(":", 1)[1].strip()
    for prefix in (
        "write_cleanup_operation_not_declared",
        "identity_mutation_requires_disposable_fixture",
        "protected_runtime_identity_mutation_blocked",
        "governed_write_blocked:",
        "multi_write_executor_missing_per_write_governance_hook",
        "invalid_governed_write_event:",
        "DELETE_SAFETY_GUARD",
    ):
        if normalized == prefix or normalized.startswith(prefix):
            return normalized.split("\n", 1)[0][:240]
    return ""


def _append_blocked_write_step(
    trace: dict[str, Any],
    *,
    action_name: str,
    method: str,
    path: str,
    body: Any,
    reason: str,
) -> None:
    trace.setdefault("errors", []).append(reason)
    trace["steps"].append({
        "action": action_name,
        "method": method,
        "path": path,
        "status": 0,
        "request": {"body": _redact(body)} if body else {},
        "response": {"status_code": 0, "headers": {}, "body": {"error": reason}},
        "expected_status": 0,
        "skipped_reason": reason,
        "execution_blocked": True,
    })


def __execute_scenario_once(
    scenario: Any,
    base_url: str,
    safety_boundary: dict[str, Any] | None = None,
    write_observer: Any = None,
) -> dict[str, Any]:
    trace: dict[str, Any] = {"scenario_id": getattr(scenario, "id", "?"), "steps": [], "errors": []}
    # Runtime bindings are produced only by explicit, source-derived scenario
    # steps (login/resolver/create). Never perform hidden seed reads or writes
    # before authentication: those bypass the scenario evidence chain and make
    # the executor industry-specific.
    bindings: dict[str, Any] = {}
    resolved_fixture_binding_names: set[str] = set()
    actor_tokens: dict[str, str] = {}

    # ── DB evidence: snapshot the data layer before/after write scenarios ──
    # Config-driven (QUALIBUG_DB_DSN); no per-project table hardcoding. The diff
    # itself reveals which table changed, giving idempotency/consistency/state
    # oracles a real data-layer proof instead of an HTTP-status-only inference.
    _db_verifier = None
    _db_tables: list[str] = []
    _scenario_has_write = any(
        str(getattr(s, "api_method", "") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        for s in (getattr(scenario, "steps", []) or [])
    )
    if _scenario_has_write and os.environ.get("QUALIBUG_DB_DSN"):
        try:
            from .db_snapshot_verifier import DBSnapshotVerifier

            _db_verifier = DBSnapshotVerifier()
            _db_tables = _db_verifier.list_tables()
            if _db_tables:
                _db_verifier.snapshot_before(_db_tables)
            else:
                _db_verifier = None
        except Exception as _db_exc:
            trace["db_evidence"] = {"status": "unavailable", "reason": f"db_snapshot_setup_failed:{type(_db_exc).__name__}"}
            _db_verifier = None
    for step in getattr(scenario, "steps", []) or []:
        method = str(getattr(step, "api_method", "") or "").upper()
        raw_path = str(getattr(step, "api_path", "") or "")
        path_template = normalize_path_placeholders(raw_path)
        path, body = path_template, getattr(step, "body_template", {}) or {}
        if not method or not path.startswith("/"):
            trace["errors"].append("invalid_source_bound_step")
            continue
        # Normalize placeholders FIRST (:id → {id}) so _replace can match them
        path, body = _replace(path, bindings), _replace(body, bindings)
        if path_has_placeholders(path) and body and method not in {"GET", "HEAD"}:
            from .policy_wiring import get_policy_value

            allowed_binding_sources = {
                str(item).strip()
                for item in (
                    get_policy_value("execution", "runtime_binding_sources", []) or []
                )
                if str(item).strip()
            }
            body_provenance = str(getattr(step, "body_provenance", "") or "").strip()
            required_source = {
                "documented_example": "documented_example",
                "documented_schema_generated": "documented_schema_generated_value",
            }.get(body_provenance, "")
            if required_source and required_source in allowed_binding_sources:
                candidate_bindings, binding_evidence = bind_path_params_from_documented_body(
                    path,
                    body,
                )
                for key, value in candidate_bindings.items():
                    bindings.setdefault(key, value)
                if binding_evidence:
                    trace.setdefault("runtime_binding_events", []).extend([
                        {
                            **item,
                            "source": required_source,
                            "body_provenance": body_provenance,
                        }
                        for item in binding_evidence
                    ])
                    path, body = _replace(path, bindings), _replace(body, bindings)
        if path_has_placeholders(path):
            # Alias fill: sku/orderId/id often share the same bound value under
            # different names depending on which list endpoint returned them.
            path = _fill_path_aliases(path, bindings)
            body = _replace(body, bindings)
        if path_has_placeholders(path):
            missing_bindings = infer_path_params(path)
            reason = f"missing_runtime_path_binding:{','.join(missing_bindings)}" if missing_bindings else "missing_runtime_path_binding"
            trace["errors"].append(reason)
            trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
            trace["precondition_not_met"].append({
                "step": getattr(step, "action", ""),
                "path": path,
                "missing_path_params": missing_bindings,
            })
            trace["steps"].append({
                "action": getattr(step, "action", ""),
                "method": method,
                "path": path,
                "status": 0,
                "request": {"body": _redact(body)} if body else {},
                "response": {"status_code": 0, "headers": {}, "body": {"error": reason}},
                "expected_status": getattr(step, "expected_status", 0),
                "skipped_reason": reason,
            })
            break
        # 主链 5 × 主链 1: honor the customer-defined production-data safety
        # boundary during REAL execution. Reuses match_production_data_exclusion
        # — the single source of truth shared with grounded_probe_executor
        # (主链 1). If a step would touch excluded production data we MUST NOT
        # fire the request: block only, never enable. The step is recorded as
        # blocked so the oracle knows the evidence is intentionally absent (not
        # a fabricated pass).
        if safety_boundary:
            _excl = match_production_data_exclusion(
                safety_boundary, path, str(getattr(step, "risk_type", "") or "")
            )
            if _excl:
                trace.setdefault("production_data_blocked", True)
                trace["production_data_block_reason"] = _excl
                trace["errors"].append(_excl)
                trace["steps"].append({
                    "action": getattr(step, "action", ""),
                    "method": method,
                    "path": path,
                    "status": 0,
                    "request": {"body": _redact(body)} if body else {},
                    "response": {"status_code": 0, "headers": {}, "body": {"error": _excl}},
                    "expected_status": getattr(step, "expected_status", 0),
                    "skipped_reason": _excl,
                    "execution_blocked": True,
                })
                continue
        # ── DELETE safety guard: never allow blanket DELETE ──
        # Every DELETE must target a specific resource (path param like /:id)
        # or carry a query/filter condition.  Table-wide deletes are never
        # allowed — even in test environments.
        if method == "DELETE":
            _has_path_param = bool(re.search(r"/:[A-Za-z_]|/\{[A-Za-z_]", path))
            _has_query_filter = bool(re.search(r"[?&](id|uuid|sku|code|name|limit)=", str(getattr(step, "api_path", "") or "")))
            _has_body_filter = isinstance(body, dict) and any(
                k for k in body if k.lower() in ("id", "ids", "uuid", "sku", "email", "code", "filter", "where")
            )
            if not (_has_path_param or _has_query_filter or _has_body_filter):
                _delete_block_reason = "DELETE_SAFETY_GUARD: 禁止无条件的全表删除操作。DELETE 必须携带路径参数 (/:id) 或查询条件 (?id=) 或请求体过滤。"
                trace["errors"].append(_delete_block_reason)
                trace["steps"].append({
                    "action": getattr(step, "action", ""),
                    "method": method,
                    "path": path,
                    "status": 0,
                    "request": {"body": _redact(body)} if body else {},
                    "response": {"status_code": 0, "headers": {}, "body": {"error": _delete_block_reason}},
                    "expected_status": getattr(step, "expected_status", 0),
                    "skipped_reason": _delete_block_reason,
                    "execution_blocked": True,
                })
                continue
            # Inject safety LIMIT if not present
            if "limit" not in str(getattr(step, "api_path", "") or "").lower() and not _has_path_param:
                # Append ?_limit=1 or &limit=1 as safety net
                sep = "&" if "?" in path else "?"
                path = f"{path}{sep}_qualibug_safe_limit=1"
        raw_action_name = str(getattr(step, "action", "") or "").strip()
        action_name = raw_action_name.lower()
        if action_name.startswith("bootstrap_create_") and method in {"POST", "PUT"}:
            binding_name = raw_action_name[len("bootstrap_create_"):].strip()
            resolved_binding = _binding_value_for_key(binding_name, bindings)
            if (
                binding_name
                and binding_name.lower() in resolved_fixture_binding_names
                and resolved_binding not in (None, "", [], {})
            ):
                binding_value = str(resolved_binding)
                bindings[binding_name] = binding_value
                for alias in param_field_candidates(binding_name):
                    bindings.setdefault(alias, binding_value)
                trace.setdefault("runtime_binding_events", []).append({
                    "source": "existing_runtime_binding",
                    "step": str(getattr(step, "action", "") or ""),
                    "binding": binding_name,
                    "value_fingerprint": hashlib.sha256(
                        binding_value.encode("utf-8")
                    ).hexdigest(),
                })
                continue
        if (
            action_name.startswith("bootstrap_create_")
            and method in {"POST", "PUT"}
            and isinstance(body, dict)
        ):
            nonce = _disposable_fixture_nonce(scenario, step, action_name)
            body, materialized_fields = _materialize_disposable_identity_fixture_body(body, nonce)
            if materialized_fields:
                trace.setdefault("runtime_binding_events", []).append({
                    "source": "disposable_identity_fixture_materialization",
                    "step": str(getattr(step, "action", "") or ""),
                    "fields": materialized_fields,
                })
        unbound_body_fields = _body_has_unbound_placeholders(body) if body and method not in {"GET", "HEAD"} else []
        if unbound_body_fields:
            reason = f"missing_runtime_body_binding:{','.join(unbound_body_fields)}"
            trace["errors"].append(reason)
            trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
            trace["precondition_not_met"].append({
                "step": getattr(step, "action", ""),
                "path": path,
                "missing_body_fields": unbound_body_fields,
            })
            trace["steps"].append({
                "action": getattr(step, "action", ""),
                "method": method,
                "path": path,
                "status": 0,
                "request": {"body": _redact(body)} if body else {},
                "response": {"status_code": 0, "headers": {}, "body": {"error": reason}},
                "expected_status": getattr(step, "expected_status", 0),
                "skipped_reason": reason,
            })
            break
        if method in {"POST", "PUT", "PATCH", "DELETE"} and body:
            body = _bind_runtime_amount_controls(body, bindings, scenario, action_name)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body and method not in {"GET", "HEAD"} else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        token, actor = str(getattr(scenario, "actor_token", "") or ""), str(getattr(step, "actor", "") or "")
        if actor and actor in actor_tokens:
            token = actor_tokens[actor]
        # If the scenario didn't carry a pre-issued actor token, fall back to a
        # token captured earlier in this scenario (e.g. from a login step whose
        # extract_from_response=["token"]).  This lets multi-actor permission /
        # isolation probes authenticate as the role they logged in as.
        if not token and isinstance(bindings.get("token"), str) and bindings.get("token"):
            token = str(bindings["token"])
        if token:
            headers["Authorization"] = f"Bearer {token}"
        write_event_id: Any = None
        if method in {"POST", "PUT", "PATCH", "DELETE"} and action_name != "login" and write_observer is not None:
            try:
                write_event_id = write_observer(
                    "before",
                    {
                        "action": action_name,
                        "method": method,
                        "path": path,
                        "body": body,
                        "token": token,
                    },
                )
            except RuntimeError as exc:
                block_reason = _governed_write_block_reason(exc)
                if not block_reason:
                    raise
                _append_blocked_write_step(
                    trace,
                    action_name=action_name,
                    method=method,
                    path=path,
                    body=body,
                    reason=block_reason,
                )
                break
        started = time.time()
        resolve_attempt_paths = (
            _resolve_get_candidates(path)
            if method == "GET" and action_name.startswith("resolve_")
            else [path]
        )
        status, response_body, response_headers = 0, {}, {}
        url = _encoded_request_url(base_url, path)
        har_recorded = False
        observer_projection: dict[str, Any] = {}
        for attempt_path in resolve_attempt_paths:
            url = _encoded_request_url(base_url, attempt_path)
            try:
                request = urllib.request.Request(url, method=method, data=data, headers=headers)
                with urllib.request.urlopen(request, timeout=10) as response:
                    raw = response.read(300_000).decode("utf-8", errors="replace")
                    status, response_body, response_headers = int(response.status), _json_or_text(raw), dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
                status, response_body, response_headers = int(exc.code), _json_or_text(raw), dict(exc.headers.items()) if exc.headers else {}
            except Exception as exc:
                status, response_body, response_headers = 0, {"error": str(exc)}, {}
                trace["errors"].append(str(exc))
            # A resolver fallback is useful for an explicit HTTP 4xx/5xx
            # response, but a transport/runtime failure is not evidence that a
            # different pagination shape will work. Stop immediately so one
            # unavailable endpoint does not fan out into several opaque calls.
            if method == "GET" and action_name.startswith("resolve_") and status == 0:
                break
            if method != "GET" or not action_name.startswith("resolve_"):
                break
            if 200 <= status < 300:
                # A successful empty collection is an authoritative negative
                # result for this resolver.  Trying every pagination dialect
                # after a valid [] response only creates opaque traffic (and
                # can turn a deterministic missing-binding trace into a
                # transport-error cascade).  Pagination fallbacks remain
                # available for explicit HTTP errors and non-empty envelopes
                # whose requested identity field is absent.
                if isinstance(response_body, list) and not response_body:
                    path = attempt_path
                    break
                trial_bindings = dict(bindings)
                for field in getattr(step, "extract_from_response", []) or []:
                    value = _extract(response_body, str(field))
                    if value not in (None, "", [], {}):
                        trial_bindings[str(field)] = value
                for key, value in bind_entity_fields(response_body, attempt_path).items():
                    trial_bindings.setdefault(key, value)
                expected_fields = [str(f) for f in (getattr(step, "extract_from_response", []) or []) if str(f)]
                if not expected_fields or any(trial_bindings.get(f) not in (None, "", [], {}) for f in expected_fields):
                    path = attempt_path
                    break
            if attempt_path == resolve_attempt_paths[-1]:
                path = attempt_path
                break
        if method == "GET" and _read_observer_action(action_name):
            if status in {404, 405}:
                fallback_paths = _observer_collection_fallback_paths(path_template, path, scenario)
                if fallback_paths:
                    original_event = {
                        "source": "read_observer_collection_fallback",
                        "reason": f"observer_detail_http_{status}",
                        "original_path": path,
                        "original_status": status,
                        "candidate_paths": list(fallback_paths),
                    }
                    _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
                    har_recorded = True
                    trace.setdefault("observer_fallback_events", []).append(dict(original_event))
                    for fallback_path in fallback_paths:
                        fallback_started = time.time()
                        fallback_url = _encoded_request_url(base_url, fallback_path)
                        fallback_status, fallback_body, fallback_headers = 0, {}, {}
                        fallback_error = ""
                        try:
                            request = urllib.request.Request(fallback_url, method=method, headers=headers)
                            with urllib.request.urlopen(request, timeout=10) as response:
                                raw = response.read(300_000).decode("utf-8", errors="replace")
                                fallback_status = int(response.status)
                                fallback_body = _json_or_text(raw)
                                fallback_headers = dict(response.headers.items())
                        except urllib.error.HTTPError as exc:
                            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
                            fallback_status = int(exc.code)
                            fallback_body = _json_or_text(raw)
                            fallback_headers = dict(exc.headers.items()) if exc.headers else {}
                        except Exception as exc:
                            fallback_status = 0
                            fallback_body = {"error": str(exc)}
                            fallback_headers = {}
                            fallback_error = str(exc)
                        _record_v12_har(
                            method,
                            fallback_url,
                            fallback_status,
                            _redact(fallback_body),
                            actor,
                            (time.time() - fallback_started) * 1000,
                        )
                        event = {
                            "source": "read_observer_collection_fallback",
                            "original_path": path,
                            "original_status": status,
                            "fallback_path": fallback_path,
                            "fallback_status": fallback_status,
                        }
                        if fallback_error:
                            event["fallback_error"] = fallback_error
                        selected, projection = _project_bound_observer_entity(
                            fallback_body,
                            path_template=path_template,
                            bindings=bindings,
                        )
                        if projection:
                            event.update(projection)
                        trace.setdefault("observer_fallback_events", []).append(event)
                        if 200 <= fallback_status < 300 and selected is not None:
                            observer_projection = {
                                **event,
                                "original_response": {
                                    "status_code": status,
                                    "body": _redact(response_body),
                                },
                            }
                            path = fallback_path
                            url = fallback_url
                            status = fallback_status
                            response_body = selected
                            response_headers = fallback_headers
                            break
            if 200 <= status < 300 and not observer_projection:
                selected, projection = _project_bound_observer_entity(
                    response_body,
                    path_template=path_template,
                    bindings=bindings,
                )
                if selected is not None:
                    observer_projection = {
                        **projection,
                        "source": "read_observer_collection_projection",
                        "observer_path": path,
                    }
                    trace.setdefault("observer_projection_events", []).append(dict(observer_projection))
                    response_body = selected
        if not har_recorded:
            _record_v12_har(method, url, status, _redact(response_body), actor, (time.time() - started) * 1000)
        if write_event_id is not None and write_observer is not None:
            try:
                write_observer(
                    "after",
                    {
                        "event_id": write_event_id,
                        "action": action_name,
                        "method": method,
                        "path": path,
                        "status": status,
                        "response_body": response_body,
                        "token": token,
                    },
                )
            except RuntimeError as exc:
                block_reason = _governed_write_block_reason(exc)
                if not block_reason:
                    raise
                trace.setdefault("errors", []).append(block_reason)
        for field in getattr(step, "extract_from_response", []) or []:
            value = _extract(response_body, str(field))
            if value not in (None, "", [], {}):
                bindings[str(field)] = value
        # When extract asked for orderId/sku/... but the body only exposed id,
        # mirror the primary identity onto those alias fields so later body
        # placeholders like {orderId} bind without a second round-trip.
        if method == "GET" and 200 <= status < 300:
            primary = bindings.get("id")
            if primary not in (None, ""):
                from .real_id_resolver import param_field_candidates

                for field in getattr(step, "extract_from_response", []) or []:
                    field_name = str(field or "").strip()
                    if not field_name or bindings.get(field_name) not in (None, ""):
                        continue
                    aliases = {c.lower() for c in param_field_candidates(field_name)}
                    if "id" in aliases or field_name.lower() in aliases:
                        bindings[field_name] = str(primary)
        # Resolve steps / list GETs: bind all identity fields needed by later
        # path placeholders (sku, orderId, id, ...), not just the first "id".
        if method == "GET" and 200 <= status < 300 and (
            action_name.startswith("resolve_") or "resolve_entity" in action_name or action_name.startswith("observe_")
        ):
            for key, value in bind_entity_fields(response_body, path).items():
                bindings.setdefault(key, value)
            if action_name.startswith("resolve_body_"):
                binding_name = raw_action_name[len("resolve_body_"):].strip()
                resolved_binding = _binding_value_for_key(binding_name, bindings)
                if binding_name and resolved_binding not in (None, "", [], {}):
                    binding_value = str(resolved_binding)
                    bindings[binding_name] = binding_value
                    for alias in param_field_candidates(binding_name):
                        bindings.setdefault(alias, binding_value)
                    resolved_fixture_binding_names.add(binding_name.lower())
        if action_name == "login" and actor and bindings.get("token"):
            actor_tokens[actor] = str(bindings["token"])
        # ── Auto-extract: POST responses that create resources ──
        # If a POST/PUT step succeeded (2xx) and the response contains an "id"
        # field, bind it for subsequent steps.  This enables multi-step flows
        # like: create a source-derived entity → bind its ID → invoke a related operation.
        if method in ("POST", "PUT") and 200 <= status < 300:
            for key, value in bind_entity_fields(response_body, path).items():
                bindings[key] = value
            if isinstance(response_body, dict):
                for auto_field in ("id", "sku", "order_id", "orderId", "order_no", "code"):
                    auto_val = response_body.get(auto_field)
                    if auto_val not in (None, "", [], {}):
                        bindings[str(auto_field)] = str(auto_val)
                        bindings.setdefault("id", str(auto_val))
            # Bootstrap creates name the target path param in the action
            # (bootstrap_create_orderId). Mirror the created id onto that param
            # and its REST aliases so later steps can bind {orderId}/{id}.
            if action_name.startswith("bootstrap_create_"):
                created_param = action_name[len("bootstrap_create_"):].strip()
                created_value = (
                    _extract(response_body, created_param)
                    or _extract(response_body, "id")
                    or _extract(response_body, "uuid")
                    or bindings.get("id")
                )
                if created_param and created_value not in (None, ""):
                    bindings[created_param] = str(created_value)
                    bindings["id"] = str(created_value)
                    from .real_id_resolver import param_field_candidates

                    for alias in param_field_candidates(created_param):
                        bindings[alias] = str(created_value)
            if isinstance(response_body, dict):
                for amount_field in (
                    "payable_amount",
                    "payableAmount",
                    "amount_due",
                    "amountDue",
                    "due_amount",
                    "dueAmount",
                    "total_amount",
                    "totalAmount",
                    "amount",
                ):
                    amount_val = response_body.get(amount_field)
                    if amount_val not in (None, "", [], {}):
                        bindings[amount_field] = amount_val
            # Also honor extract_from_response aliases when the body only has id.
            for field in getattr(step, "extract_from_response", []) or []:
                field_name = str(field)
                if bindings.get(field_name) not in (None, ""):
                    continue
                if bindings.get("id") not in (None, ""):
                    from .real_id_resolver import param_field_candidates

                    if "id" in {c.lower() for c in param_field_candidates(field_name)} or field_name.lower() in {"id", "uuid"}:
                        bindings[field_name] = str(bindings["id"])
        # Filtered extraction: pick items in a GET list whose attribute
        # matches a where= clause, then extract their ids.  This lets a
        # precondition resolver bind a concrete entity id when the state
        # precondition (e.g. status=PAID) is real at runtime.
        step_where = dict(getattr(step, "extract_where", None) or {})
        if step_where and isinstance(response_body, (list, dict)):
            candidates = response_body if isinstance(response_body, list) else [response_body]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if not all(str(candidate.get(k)) == str(v) for k, v in step_where.items()):
                    continue
                for field in (getattr(step, "extract_from_response", []) or []):
                    value = _extract(candidate, str(field))
                    if value not in (None, "", [], {}):
                        bindings[str(field)] = value
                        break  # first matching entity
                break  # only need one matching entity
            if not bindings.get("id"):
                trace.setdefault("precondition_not_met", list(trace.get("precondition_not_met", [])))
                trace["precondition_not_met"].append(step_where)
        step_record = {
            "action": getattr(step, "action", ""),
            "method": method,
            "path": path,
            "status": status,
            "request": {"body": _redact(body)} if body else {},
            "response": {"status_code": status, "headers": _redact(response_headers), "body": _redact(response_body)},
            "expected_status": getattr(step, "expected_status", 0),
        }
        if observer_projection:
            step_record["observer_projection"] = observer_projection
        trace["steps"].append(step_record)
    # ── DB evidence: snapshot after all steps and diff against the before state ──
    if _db_verifier is not None:
        try:
            _db_verifier.snapshot_after(_db_tables)
            _db_result = _db_verifier.verify()
            _changed = [
                {
                    "table": d.get("table"),
                    "before_count": d.get("before_count"),
                    "after_count": d.get("after_count"),
                    "added": d.get("added_rows"),
                    "removed": d.get("removed_rows"),
                    "modified": d.get("modified_rows"),
                }
                for d in (_db_result.diffs or [])
                if d.get("checksum_changed") or d.get("added_rows") or d.get("removed_rows") or d.get("modified_rows")
            ]
            trace["db_evidence"] = {
                "status": "captured",
                "db_type": _db_result.db_type,
                "tables_checked": _db_result.tables_checked,
                "any_change": bool(_changed),
                "changed_tables": _changed,
                "duration_ms": _db_result.duration_ms,
            }
        except Exception as _db_exc:
            trace["db_evidence"] = {"status": "unavailable", "reason": f"db_snapshot_verify_failed:{type(_db_exc).__name__}"}

    trace["runtime_binding_summary"] = {
        "event_count": len(trace.get("runtime_binding_events") or []),
        "sources": sorted({
            str(item.get("source") or "")
            for item in (trace.get("runtime_binding_events") or [])
            if isinstance(item, dict) and str(item.get("source") or "")
        }),
        "bound_path_params": sorted({
            str(item.get("path_param") or "")
            for item in (trace.get("runtime_binding_events") or [])
            if isinstance(item, dict) and str(item.get("path_param") or "")
        }),
    }
    return trace


def _replace(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, bindings) for item in value]
    if not isinstance(value, str):
        return value
    for key, item in bindings.items():
        value = value.replace("{" + key + "}", str(item))
    if "{" in value and "}" in value:
        from .real_id_resolver import param_field_candidates

        for param in infer_path_params(value):
            token = "{" + param + "}"
            if token not in value:
                continue
            for alias in param_field_candidates(param):
                if alias in bindings and bindings[alias] not in (None, ""):
                    value = value.replace(token, str(bindings[alias]))
                    break
    return value


def _summarize_execution_skip_telemetry(traces: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    """Aggregate skip/precondition reasons across scenario execution traces."""
    reason_counts: dict[str, int] = {}
    path_binding_misses: dict[str, int] = {}
    empty_write_bodies: dict[str, int] = {}
    blocked_samples: list[dict[str, str]] = []
    sandbox_write_status_counts: dict[str, int] = {}
    cleanup_status_counts: dict[str, int] = {}
    cleanup_failure_reasons: dict[str, int] = {}
    observer_status_counts: dict[str, int] = {}
    scenarios_with_http = 0
    scenarios_blocked = 0
    for _, trace in traces:
        if not isinstance(trace, dict):
            continue
        steps = trace.get("steps") or []
        has_http = any(
            int(step.get("status") or _dict(step.get("response")).get("status_code") or 0) > 0
            for step in steps
            if isinstance(step, dict)
        )
        if has_http:
            scenarios_with_http += 1
        else:
            scenarios_blocked += 1
            errors = [str(item) for item in (trace.get("errors") or []) if str(item).strip()]
            skipped_reasons = [
                str(step.get("skipped_reason") or "").strip()
                for step in steps
                if isinstance(step, dict) and str(step.get("skipped_reason") or "").strip()
            ]
            primary_reason = str(
                trace.get("execution_block_reason")
                or (errors[0] if errors else "")
                or (skipped_reasons[0] if skipped_reasons else "")
                or "no_runtime_receipt"
            )
            if len(blocked_samples) < 20:
                blocked_samples.append({
                    "scenario_id": str(
                        getattr(_, "id", "")
                        or getattr(_, "behavior_slice_id", "")
                        or trace.get("scenario_id")
                        or "?"
                    )[:160],
                    "behavior_slice_id": str(getattr(_, "behavior_slice_id", "") or "")[:160],
                    "reason": primary_reason[:240],
                })
        for err in trace.get("errors") or []:
            key = str(err).split(":", 1)[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1
            if key == "missing_runtime_path_binding":
                detail = str(err).split(":", 1)[1] if ":" in str(err) else "unknown"
                path_binding_misses[detail] = path_binding_misses.get(detail, 0) + 1
        for step in steps:
            if not isinstance(step, dict):
                continue
            skipped = str(step.get("skipped_reason") or "").strip()
            if skipped:
                key = skipped.split(":", 1)[0]
                reason_counts[key] = reason_counts.get(key, 0) + 1
            method = str(step.get("method") or "").upper()
            if method in {"POST", "PUT", "PATCH"} and not skipped:
                req_body = _dict(step.get("request")).get("body")
                if not req_body:
                    target = f"{method} {step.get('path') or '?'}"
                    empty_write_bodies[target] = empty_write_bodies.get(target, 0) + 1
        sandbox = trace.get("sandbox_write") if isinstance(trace.get("sandbox_write"), dict) else {}
        if sandbox:
            sandbox_status = str(sandbox.get("status") or "unknown")
            sandbox_write_status_counts[sandbox_status] = sandbox_write_status_counts.get(sandbox_status, 0) + 1
            cleanup = sandbox.get("cleanup") if isinstance(sandbox.get("cleanup"), dict) else {}
            cleanup_status = str(cleanup.get("status") or "unknown")
            cleanup_status_counts[cleanup_status] = cleanup_status_counts.get(cleanup_status, 0) + 1
            cleanup_error = str(cleanup.get("error") or "").strip()
            if cleanup_error:
                cleanup_failure_reasons[cleanup_error] = cleanup_failure_reasons.get(cleanup_error, 0) + 1
            cleanup_warning = str(cleanup.get("warning") or "").strip()
            if cleanup_warning and cleanup_status in {"failed", "not_reversible", "cleanup_incomplete"}:
                cleanup_failure_reasons[cleanup_warning] = cleanup_failure_reasons.get(cleanup_warning, 0) + 1
            for phase in ("before", "after"):
                observation = sandbox.get(phase) if isinstance(sandbox.get(phase), dict) else {}
                ref = str(observation.get("ref") or "")
                status_key = str(observation.get("status") or 0)
                if "documented_observer_missing" in ref:
                    status_key = "documented_observer_missing"
                key = f"{phase}:{status_key}"
                observer_status_counts[key] = observer_status_counts.get(key, 0) + 1
    return {
        "scenarios_with_http": scenarios_with_http,
        "scenarios_blocked": scenarios_blocked,
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        "path_binding_misses": dict(sorted(path_binding_misses.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "empty_write_bodies": dict(sorted(empty_write_bodies.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "blocked_samples": blocked_samples,
        "sandbox_write_status_counts": dict(sorted(sandbox_write_status_counts.items())),
        "cleanup_status_counts": dict(sorted(cleanup_status_counts.items())),
        "cleanup_failure_reasons": dict(sorted(cleanup_failure_reasons.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "observer_status_counts": dict(sorted(observer_status_counts.items())),
    }


def _fill_path_aliases(path: str, bindings: dict[str, Any]) -> str:
    """Fill remaining path placeholders using alias-compatible bindings.

    Example: path needs ``{sku}`` but only ``id`` was bound from a product list
    that used ``sku`` as the primary key — map via shared identity values.
    """
    if not path_has_placeholders(path) or not bindings:
        return path
    from .real_id_resolver import param_field_candidates

    filled = path
    for param in infer_path_params(path):
        token = "{" + param + "}"
        if token not in filled:
            continue
        if param in bindings and bindings[param] not in (None, ""):
            filled = filled.replace(token, str(bindings[param]))
            continue
        for alias in param_field_candidates(param):
            if alias in bindings and bindings[alias] not in (None, ""):
                filled = filled.replace(token, str(bindings[alias]))
                bindings.setdefault(param, str(bindings[alias]))
                break
    return filled


def _extract(value: Any, field: str) -> Any:
    if not field:
        return None
    current = value
    if "." in field:
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    if isinstance(value, dict):
        if field in value:
            return value[field]
        for item in value.values():
            found = _extract(item, field)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract(item, field)
            if found is not None:
                return found
    return None


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw[:5000]


def _count_by(items: list[Any], attr: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items:
        result[str(getattr(item, attr, "unknown"))] += 1
    return dict(result)




def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("<REDACTED>" if str(key).lower().replace("-", "_") in _SENSITIVE else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:25]]
    text = str(value)
    return text[:1000] + "…" if len(text) > 1000 else value

