"""Core probe decision, fixture binding, execution, verification, findings."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from ._common import _append_query, _auth_boundary_plan, _configured_query_params, _find_sensitive_keys, _fixture_backed_read_probe, _fixture_control_headers, _has_business_data, _has_unresolved_placeholder, _headers_from_config, _is_auth_boundary_risk, _join_url, _negative_headers, _read_fixture_setup_approval, _redact, _render_path, _render_query, _safe_payload_summary, _url_host  # noqa: F401

logger = logging.getLogger(__name__)


def _http_request(*args: Any, **kwargs: Any) -> Any:
    """Issue probe transport through the package facade, resolved per call.

    The facade attribute is the transport seam callers and tests intercept.
    Binding the implementation into this module at import time would keep the
    facade in place while silently routing every probe past it.
    """
    from . import _common

    facade = sys.modules.get(__package__)
    impl = getattr(facade, "_http_request", None)
    if impl is None or impl is _http_request:
        impl = _common._http_request
    return impl(*args, **kwargs)

from ._common import *  # noqa: F401,F403
from ._common import _approval_enabled, _get_mapping_value, _production_guard_allows  # noqa: F401
from ._evidence_delivery import *  # noqa: F401,F403

def _probe_has_strict_document_grounding(probe: dict[str, Any]) -> bool:
    refs = probe.get("source_refs") if isinstance(probe.get("source_refs"), list) else []
    kinds = {str(r.get("kind") or "") for r in refs if isinstance(r, dict)}
    basis = probe.get("grounding_basis") if isinstance(probe.get("grounding_basis"), dict) else {}
    has_endpoint = "endpoint_contract" in kinds or int(basis.get("endpoint_contract_refs") or 0) >= 1
    has_support = bool(kinds - {"endpoint_contract", ""}) or int(basis.get("supporting_requirement_refs") or 0) >= 1
    return bool(has_endpoint and has_support)




def _auto_fixture_enabled(config: dict[str, Any]) -> bool:
    # Product default: QualiBug creates test data.  Customers should not fill
    # order IDs/request bodies except as an advanced override.
    cfg = config.get("auto_fixture") or config.get("auto_fixtures") or config.get("auto_test_data") or {}
    if isinstance(cfg, dict) and "enabled" in cfg:
        return bool(cfg.get("enabled"))
    return bool(config.get("qualibug_auto_create_test_data", True))


def _auto_fixture_bundle(config: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    cid = str(probe.get("candidate_id") or "")
    cache = config.setdefault("_auto_fixture_runtime", {})
    if cid in cache and isinstance(cache[cid], dict):
        return cache[cid]
    from ..auto_test_data_factory import build_auto_fixture_for_probe

    bundle = build_auto_fixture_for_probe(
        probe,
        input_dir=config.get("input_dir") or config.get("project_input_dir"),
        config=config,
    )
    if not isinstance(bundle, dict):
        raise TypeError(f"auto_fixture_bundle_must_be_object:{cid or 'unknown_candidate'}")
    cache[cid] = bundle
    return bundle


def _configured_path_params(config: dict[str, Any], probe: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    candidate_id = str(probe.get("candidate_id") or "")
    path_param_cfg = dict(config.get("path_params") or {})
    merged = dict(path_param_cfg.get("*") or {})
    if _auto_fixture_enabled(config) and (method in WRITE_METHODS or bool(config.get("auto_read_path_params")) or _fixture_backed_read_probe(probe, method, path)):
        bundle = _auto_fixture_bundle(config, probe)
        if isinstance(bundle.get("path_params"), dict):
            merged.update(bundle.get("path_params") or {})
    merged.update(path_param_cfg.get(candidate_id) or {})
    merged.update(path_param_cfg.get(f"{method} {path}") or {})
    return merged


def _configured_body(config: dict[str, Any], candidate_id: str, method: str, path: str, probe: dict[str, Any] | None = None) -> tuple[Any, str]:
    bodies = config.get("request_bodies") or config.get("bodies") or {}
    if isinstance(bodies, dict):
        body = _get_mapping_value(bodies, candidate_id, method, path)
        if isinstance(body, dict) and set(body.keys()) == {"json"}:
            body = body.get("json")
        if body not in (None, {}, [], ""):
            return body, "customer_or_advanced_override_configured"
    elif bodies:
        return None, "request_bodies_not_object"

    # Coupon probes injected by pre-scan enrichment carry a concrete DB-sampled
    # body (real coupon code + sku + qty).  Use it verbatim so the validate
    # endpoint receives actual data the customer DB already contains.
    if probe is not None and isinstance(probe.get("_coupon_body"), dict):
        return dict(probe["_coupon_body"]), "db_sampled_coupon_case"

    if probe is not None and _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        receipt = bundle.get("receipt") if isinstance(bundle.get("receipt"), dict) else {}
        source_block = str(receipt.get("fixture_setup_blocked_reason") or "").strip()
        if source_block:
            return None, f"auto_fixture_source_contract_blocked:{source_block}"
        body = bundle.get("request_body") if isinstance(bundle, dict) else None
        if body not in (None, {}, [], ""):
            return body, "auto_fixture_generated_by_qualibug"
        return None, str((bundle or {}).get("error") or "auto_fixture_body_generation_failed")
    return None, "write_probe_body_not_document_configured"






def _configured_replay_count(config: dict[str, Any], probe: dict[str, Any]) -> int:
    cid = str(probe.get("candidate_id") or "")
    ep = probe.get("endpoint") or {}
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    replay_cfg = config.get("replay") or {}
    value = _get_mapping_value(replay_cfg, cid, method, path) if isinstance(replay_cfg, dict) else None
    if isinstance(value, dict):
        value = value.get("count")
    try:
        n = int(value or 2)
    except Exception:
        n = 2
    return max(1, min(n, 5))


def _headers_for_probe(probe: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    headers = _headers_from_config(config)
    probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    auth_boundary = _auth_boundary_plan(probe)
    if _is_auth_boundary_risk(probe):
        actor = str(auth_boundary.get("actor") or "anonymous").lower()
        profile = str(auth_boundary.get("credential_profile") or actor or "").strip()
        resolved = config.get("_resolved_account_headers") if isinstance(config.get("_resolved_account_headers"), dict) else {}
        if actor == "anonymous" or profile in {"", "no_credentials", "anonymous"}:
            headers = _negative_headers(headers, list(probe_plan.get("negative_headers") or AUTH_HEADER_NAMES))
        elif isinstance(resolved.get(profile), dict) and resolved.get(profile):
            headers = dict(resolved.get(profile) or {})
        elif isinstance(resolved.get(actor), dict) and resolved.get(actor):
            headers = dict(resolved.get(actor) or {})
        elif "tenant" in actor or "tenant" in profile:
            headers["X-Tenant-Id"] = f"qb_auto_forbidden_{profile or actor}"
    # Per-candidate headers can add safe idempotency keys etc.  Values are supplied by the customer config, not invented.
    ep = probe.get("endpoint") or {}
    cid = str(probe.get("candidate_id") or "")
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    if _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        if isinstance(bundle.get("headers"), dict):
            headers.update({str(k): str(v) for k, v in (bundle.get("headers") or {}).items()})
    per = config.get("headers") or {}
    if isinstance(per, dict):
        value = _get_mapping_value(per, cid, method, path)
        if isinstance(value, dict):
            headers.update({str(k): str(v) for k, v in value.items()})
    auth_boundary = _auth_boundary_plan(probe)
    if _is_auth_boundary_risk(probe):
        actor = str(auth_boundary.get("actor") or "anonymous").lower()
        profile = str(auth_boundary.get("credential_profile") or actor or "").strip()
        if actor == "anonymous" or profile in {"", "no_credentials", "anonymous"}:
            headers = _negative_headers(headers, list(probe_plan.get("negative_headers") or AUTH_HEADER_NAMES))
    return headers


def _decide_probe(probe: dict[str, Any], *, base_url: str, config: dict[str, Any], options: dict[str, Any]) -> ProbeDecision:
    ep = probe.get("endpoint") or {}
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    risk_type = str(probe.get("risk_type") or "unknown")
    execution_policy = str(probe.get("execution_policy") or "")
    candidate_id = str(probe.get("candidate_id") or "")
    merged_path_params = _configured_path_params(config, probe, method, path)
    rendered, missing = _render_path(path, merged_path_params)
    query_string = _render_query(_configured_query_params(config, probe, method, path), merged_path_params)
    request_path = _append_query(rendered, query_string)

    headers = _headers_for_probe(probe, config)
    body = None
    body_reason = "not_needed"
    if method in WRITE_METHODS:
        body, body_reason = _configured_body(config, candidate_id, method, path, probe)
    req = {
        "method": method,
        "url": _join_url(base_url, request_path),
        "path": request_path,
        "query": _redact(_configured_query_params(config, probe, method, path)),
        "headers": _redact(headers),
        "body": _redact(body),
    }
    # Hard safety boundary (主链 1): never probe data the customer marked
    # off-limits (e.g. production PII, settlement tables). This is a pure
    # blocking guard — it can only ever block a probe, never enable one.
    _excl_target = _join_url(base_url, request_path) if base_url else request_path
    _excl_hit = match_production_data_exclusion(config, _excl_target, risk_type)
    if _excl_hit:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", _excl_hit, req)
    read_fixture_setup_required = False
    if method in READ_METHODS and _auto_fixture_enabled(config) and _fixture_backed_read_probe(probe, method, path):
        bundle = _auto_fixture_bundle(config, probe)
        read_fixture_setup_required = bool(isinstance(bundle, dict) and bundle.get("setup_requests"))
        if read_fixture_setup_required:
            req["runtime_fixture_setup_required"] = True
            req["runtime_fixture_setup_reason"] = "fixture_backed_read_probe"

    if os.environ.get("QUALIBUG_STRICT_PROBE_GROUNDING", "1") != "0" and not _probe_has_strict_document_grounding(probe):
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "ungrounded_probe_missing_endpoint_or_requirement_source_refs", req)
    if missing:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"missing_path_params:{','.join(missing)}", req)
    # Probe config templates deliberately contain <FILL:...> placeholders.
    # Never execute read or write probes until the customer replaces them with
    # disposable-sandbox values.  Dry-run reports can still render placeholders.
    if base_url and _has_unresolved_placeholder({"path": rendered, "query": _configured_query_params(config, probe, method, path), "headers": headers, "body": body}):
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "probe_config_contains_unresolved_placeholders", req)
    if method in WRITE_METHODS:
        approval_id = str(options.get("approval_id") or "")
        allow_write = bool(options.get("allow_write_sandbox") or config.get("allow_write_probes") or ((config.get("test_environment") or {}).get("allow_write_probes") if isinstance(config.get("test_environment"), dict) else False))
        if not allow_write:
            # safe_read_only POSTs (validate / search / query) are semantically
            # read-only — they do not modify customer data, so write-sandbox
            # approval is not required. Let them through the write gate.
            if execution_policy == "safe_read_only":
                pass
            else:
                return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "write_probe_requires_test_environment_execution_enabled", req)
        if execution_policy != "disposable_sandbox_required":
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"write_probe_policy_not_disposable_sandbox_required:{execution_policy}", req)
        if not base_url:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "write_probe_base_url_required", req)
        ok, reason, _sandbox = _approval_enabled(config, base_url, approval_id)
        if not ok:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", reason, req)
        if body is None:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", body_reason, req)
        if risk_type == "idempotency_replay_probe":
            raw_headers = _headers_for_probe(probe, config)
            has_key = any(k.lower() == "idempotency-key" for k in raw_headers) or any(str(k).lower() in {"idempotency_key", "idempotencykey", "business_key", "request_id", "external_event_id"} for k in (body.keys() if isinstance(body, dict) else []))
            if not has_key:
                return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "idempotency_probe_requires_configured_idempotency_key_or_business_key", req)
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "execute_write_sandbox", "eligible_disposable_sandbox_write_probe", req)
    if not base_url:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "dry_run_only", "base_url_not_configured", req)
    if method not in READ_METHODS:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"unsupported_method:{method}", req)
    if execution_policy != "read_only_safe":
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"policy_not_read_only_safe:{execution_policy}", req)
    if not options.get("execute_readonly"):
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "dry_run_only", "execute_readonly_not_enabled", req)
    if read_fixture_setup_required:
        ok, setup_reason = _read_fixture_setup_approval(config, base_url, options)
        if not ok:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", setup_reason, req)
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "execute_readonly", f"eligible_read_only_probe_with_fixture_setup:{setup_reason}", req)
    return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "execute_readonly", "eligible_read_only_probe", req)


def _server_error_finding(code: int, *, probe: dict[str, Any], summary: Any, sensitive_keys: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Universal server-error oracle.

    A correct HTTP API must answer any request — including malformed, negative or
    cross-boundary probes — with a 4xx client error, never a 5xx. A 5xx therefore
    means the request reached unguarded server code: an unhandled exception, a
    missing input validation, or a crash. That is a real defect on its own, so we
    surface it instead of burying it as "inconclusive". This is fully generic — it
    encodes only the HTTP contract, no per-project or per-industry assumptions.
    """
    method = str((probe.get("endpoint") or {}).get("method") or "").upper()
    path = str((probe.get("endpoint") or {}).get("path") or "")
    result = {
        "verdict": "validated_candidate",
        "reason": (
            f"server returned HTTP {code} for {method} {path} — unhandled server error / missing "
            f"input validation (a correct API must answer with a 4xx client error, never 5xx)"
        ),
        "confidence": 0.8,
        "payload_summary": summary,
        "sensitive_keys": sensitive_keys,
        "server_error_defect": True,
        "defect_class": "server_error_5xx",
    }
    if extra:
        result.update(extra)
    return result


def _verify_observation(probe: dict[str, Any], response: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    risk_type = str(probe.get("risk_type") or "")
    status = response.get("status_code")
    payload = response.get("payload")
    sensitive_keys = _find_sensitive_keys(payload)
    summary = _safe_payload_summary(payload)

    if status is None:
        return {"verdict": "inconclusive", "reason": response.get("error") or "network_error", "confidence": 0.0, "payload_summary": summary, "sensitive_keys": sensitive_keys}
    try:
        _obs_code = int(status)
    except Exception:
        _obs_code = 0
    if _obs_code >= 500:
        return _server_error_finding(_obs_code, probe=probe, summary=summary, sensitive_keys=sensitive_keys)

    # ── Universal identity-based data-isolation oracle ──
    # For ownership_scope / auth_boundary READS at 2xx: extract the primary
    # actor from the probe, decode their JWT identity, and scan the response
    # payload for identity values.  Actor identity in the payload means the
    # caller sees their own id/email in someone else's data → cross-owner leak.
    # Generic: identity from standard JWT claims; match is any leaf string.
    actors = [str(a).strip().lower() for a in (probe.get("actors") or []) if str(a).strip()]
    primary_actor = actors[0] if actors else ""
    if (config
        and primary_actor
        and primary_actor != "anonymous"
        and risk_type in {"ownership_scope_probe", "auth_boundary_probe"}
        and 200 <= _obs_code < 300
        and isinstance(payload, dict)):
        resolved = config.get("_resolved_account_headers") if isinstance(config.get("_resolved_account_headers"), dict) else {}
        actor_headers = resolved.get(primary_actor) or {}
        if not actor_headers:
            for alias in (primary_actor, "buyer", "merchant", "admin"):
                if resolved.get(alias):
                    actor_headers = resolved[alias]
                    break
        identity_values: set[str] = set()
        for token_key in ("Authorization", "authorization"):
            token_val = str(actor_headers.get(token_key) or actor_headers.get(token_key.lower()) or "")
            if token_val.startswith("Bearer "):
                try:
                    import base64 as _b64
                    parts = token_val[7:].split(".")
                    if len(parts) >= 2:
                        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
                        claims = json.loads(_b64.b64decode(padded).decode("utf-8", errors="replace"))
                        for field in ("sub", "id", "user_id", "email", "role"):
                            val = str(claims.get(field) or "").strip()
                            if val:
                                identity_values.add(val)
                except Exception:
                    pass
        if identity_values:
            def _leaf_strings(obj: Any) -> list[str]:
                leaves: list[str] = []
                if isinstance(obj, dict):
                    for v in obj.values():
                        leaves.extend(_leaf_strings(v))
                elif isinstance(obj, list):
                    for v in obj[:50]:
                        leaves.extend(_leaf_strings(v))
                elif isinstance(obj, str):
                    leaves.append(obj)
                return leaves
            payload_strings = _leaf_strings(payload)
            identity_matches = [s for s in payload_strings if s in identity_values]
            if identity_matches:
                return {
                    "verdict": "validated_candidate",
                    "reason": (
                        f"{primary_actor} ownership_scope read returned 2xx with actor identity "
                        f"({sorted(identity_values)[:2]}) in payload; cross-owner data-isolation likely violated"
                    ),
                    "confidence": 0.78,
                    "payload_summary": summary,
                    "sensitive_keys": sensitive_keys,
                    "defect_class": "cross_owner_data_isolation",
                    "matched_identities": sorted(identity_matches)[:10],
                }
    # ── end identity oracle ──

    if _read_negative_probe_was_accepted(probe, payload) and 200 <= int(status) < 300:
        return {
            "verdict": "validated_candidate",
            "reason": f"read-only negative scope probe returned HTTP {status} with accepted cross-boundary business data",
            "confidence": 0.9,
            "payload_summary": summary,
            "sensitive_keys": sensitive_keys,
        }
    if _is_auth_boundary_risk(probe, risk_type):
        auth = _auth_boundary_plan(probe)
        actor = str(auth.get("actor") or "anonymous")
        expected = _expected_negative_statuses(probe)
        if int(status) in expected:
            return {"verdict": "falsified_or_protected", "reason": f"{actor} access boundary rejected with HTTP {status}", "confidence": 0.82, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        if 200 <= int(status) < 300 and _has_business_data(payload):
            return {"verdict": "validated_candidate", "reason": f"{actor} access boundary returned HTTP {status} with non-empty business data", "confidence": 0.91, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        if 200 <= int(status) < 300:
            return {"verdict": "needs_more_evidence", "reason": f"{actor} HTTP {status} but no non-empty business payload", "confidence": 0.45, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        return {"verdict": "inconclusive", "reason": f"unexpected HTTP {status} for {actor} access boundary", "confidence": 0.35, "payload_summary": summary, "sensitive_keys": sensitive_keys}

    if risk_type == "audit_privacy_probe":
        if 200 <= int(status) < 300 and sensitive_keys:
            return {"verdict": "validated_candidate", "reason": "read-only response contains sensitive-looking fields that require role/desensitization review", "confidence": 0.72, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        if 200 <= int(status) < 300:
            return {"verdict": "observed_no_finding", "reason": "read-only response observed without obvious sensitive keys", "confidence": 0.55, "payload_summary": summary, "sensitive_keys": sensitive_keys}
    return {"verdict": "observed_no_finding", "reason": f"read-only observation HTTP {status}; no runtime oracle matched", "confidence": 0.4, "payload_summary": summary, "sensitive_keys": sensitive_keys}


def _read_negative_probe_was_accepted(probe: dict[str, Any], payload: Any) -> bool:
    risk_type = str(probe.get("risk_type") or "")
    if risk_type not in {"ownership_scope_probe", "auth_boundary_probe", "anonymous_auth_boundary_probe", "cross_tenant_auth_boundary_probe", "role_downgrade_auth_boundary_probe"}:
        return False
    method = str(probe.get("method") or ((probe.get("endpoint") or {}).get("method") if isinstance(probe.get("endpoint"), dict) else "") or "").upper()
    if method and method not in READ_METHODS:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("expected_should_have_rejected") is True and str(payload.get("actual_behavior") or "") in {"accepted_or_returned_business_data", "accepted_despite_negative_probe"}:
        return _has_business_data(payload)
    resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
    if str(resource.get("status") or "") == "accepted_despite_negative_probe":
        return _has_business_data(payload)
    if payload.get("observed_bug_id") and _has_business_data(payload):
        return True
    return False


def _negative_probe_payload_was_accepted(probe: dict[str, Any], payload: Any) -> bool:
    risk_type = str(probe.get("risk_type") or "")
    if risk_type not in {
        "ownership_scope_probe",
        "auth_boundary_probe",
        "anonymous_auth_boundary_probe",
        "cross_tenant_auth_boundary_probe",
        "role_downgrade_auth_boundary_probe",
        "state_transition_probe",
        "async_external_event_probe",
        "conservation_probe",
        "idempotency_replay_probe",
    }:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("expected_should_have_rejected") is True and str(payload.get("actual_behavior") or "") in {"accepted_or_returned_business_data", "accepted_despite_negative_probe"}:
        return _has_business_data(payload)
    resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
    return str(resource.get("status") or "") == "accepted_despite_negative_probe" and _has_business_data(payload)


def _expected_negative_statuses(probe: dict[str, Any]) -> set[int]:
    probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    values = probe_plan.get("expected_status") or probe_plan.get("expected_statuses") or []
    statuses: set[int] = set()
    for v in values:
        try:
            statuses.add(int(v))
        except Exception:
            pass
    if statuses:
        return statuses
    risk_type = str(probe.get("risk_type") or "")
    if _is_auth_boundary_risk(probe, risk_type):
        return EXPECTED_AUTH_FAILURES
    if risk_type == "ownership_scope_probe":
        return {403, 404}
    if risk_type in {"state_transition_probe", "idempotency_replay_probe", "async_external_event_probe"}:
        return {409, 422}
    return DEFAULT_NEGATIVE_WRITE_FAILURES


def _extract_id_like(value: Any) -> str:
    if isinstance(value, dict):
        # Generic identity key patterns (industry-neutral)
        for key in ("id", "uuid", "code", "key", "ref", "number"):
            if key in value and value[key] not in (None, ""):
                return str(value[key])
        # Check for *_id pattern keys
        for key, val in value.items():
            if key.endswith("_id") and val not in (None, ""):
                return str(val)
        for v in value.values():
            hit = _extract_id_like(v)
            if hit:
                return hit
    if isinstance(value, list) and value:
        return _extract_id_like(value[0])
    return ""


def _normalized_id_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _scalar_id_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value)


def _find_value_for_normalized_keys(value: Any, keys: set[str]) -> str:
    if not keys:
        return ""
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_id_key(key) in keys:
                hit = _scalar_id_value(item)
                if hit:
                    return hit
        for item in value.values():
            hit = _find_value_for_normalized_keys(item, keys)
            if hit:
                return hit
    elif isinstance(value, list):
        for item in value[:10]:
            hit = _find_value_for_normalized_keys(item, keys)
            if hit:
                return hit
    return ""


def _resource_aliases_for_bind_field(field: str) -> set[str]:
    raw = str(field or "").strip().strip("{}")
    if not raw:
        return set()
    # Convert common camelCase path params to token form before suffix removal.
    tokenized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", tokenized) if p]
    if len(parts) >= 2 and parts[-1].lower() in {"id", "uuid", "code"}:
        base = "_".join(parts[:-1])
    else:
        normalized = _normalized_id_key(raw)
        base = re.sub(r"(?:id|uuid|code)$", "", normalized)
    aliases = {_normalized_id_key(base)} if base else set()
    aliases |= {_normalized_id_key(base + "s") for base in list(aliases) if base}
    return {a for a in aliases if a}


def _find_id_under_resource_alias(value: Any, aliases: set[str]) -> str:
    if not aliases:
        return ""
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_id_key(key) in aliases:
                hit = _extract_id_like(item)
                if hit:
                    return hit
        for item in value.values():
            hit = _find_id_under_resource_alias(item, aliases)
            if hit:
                return hit
    elif isinstance(value, list):
        for item in value[:10]:
            hit = _find_id_under_resource_alias(item, aliases)
            if hit:
                return hit
    return ""


def _extract_id_for_bind_fields(value: Any, bind_fields: list[str]) -> str:
    """Extract the server id that matches the path param being rebound.

    Generic response parsing often sees many ``id`` fields (customer.id, user.id,
    resource.id).  When runtime binding knows the target path param (for example
    ``entity_id``), prefer exact response keys such as ``entity_id``/``entityId`` or
    nested resource objects such as ``entity.id`` before falling back to the first
    generic id.
    """
    fields = [str(f or "").strip().strip("{}") for f in bind_fields if str(f or "").strip()]
    exact_keys = {_normalized_id_key(f) for f in fields if _normalized_id_key(f)}
    hit = _find_value_for_normalized_keys(value, exact_keys)
    if hit:
        return hit
    for field in fields:
        hit = _find_id_under_resource_alias(value, _resource_aliases_for_bind_field(field))
        if hit:
            return hit
    return _extract_id_like(value)


def _payload_contains_scalar(value: Any, needle: str) -> bool:
    target = str(needle or "").strip()
    if not target:
        return False
    if isinstance(value, dict):
        return any(_payload_contains_scalar(v, target) for v in value.values())
    if isinstance(value, list):
        return any(_payload_contains_scalar(v, target) for v in value[:50])
    if value in (None, "", [], {}):
        return False
    text = str(value)
    return text == target or target in text


def _runtime_bound_fixture_ids(config: dict[str, Any], probe: dict[str, Any]) -> list[str]:
    bundle = _auto_fixture_bundle(config, probe) if _auto_fixture_enabled(config) else {}
    if not isinstance(bundle, dict):
        return []
    ids: list[str] = []
    receipt = bundle.get("receipt") if isinstance(bundle.get("receipt"), dict) else {}
    for value in [receipt.get("runtime_bound_fixture_id") if isinstance(receipt, dict) else None]:
        text = str(value or "").strip()
        if text and not text.startswith("qb_auto") and text not in ids:
            ids.append(text)
    bindings = bundle.get("runtime_bindings") if isinstance(bundle.get("runtime_bindings"), list) else []
    for item in bindings:
        if not isinstance(item, dict) or item.get("source") not in {"setup_response", "flow_step_response"}:
            continue
        text = str(item.get("response_id") or "").strip()
        if text and not text.startswith("qb_auto") and text not in ids:
            ids.append(text)
    return ids[:12]


def _anchor_auth_boundary_fixture_evidence(
    probe: dict[str, Any],
    verification: dict[str, Any],
    response: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Require fixture-backed auth findings to prove the protected object leaked.

    A 200 response with some business-looking payload is not enough for a
    fixture-backed anonymous/cross-tenant/role-downgrade probe: list endpoints or
    public metadata can also return business-shaped data.  Once setup has bound
    a server-side fixture id, keep the candidate validated only when the negative
    actor response contains that exact runtime id; otherwise downgrade to
    needs-more-evidence instead of shipping a likely false positive.
    """
    if not _is_auth_boundary_risk(probe) or verification.get("verdict") != "validated_candidate":
        return verification
    bound_ids = _runtime_bound_fixture_ids(config, probe)
    if not bound_ids:
        return verification
    payload = response.get("payload")
    leaked = [rid for rid in bound_ids if _payload_contains_scalar(payload, rid)]
    anchored = dict(verification)
    anchored["fixture_evidence_anchor_ids"] = bound_ids
    if leaked:
        anchored["leaked_fixture_ids"] = leaked[:5]
        anchored["evidence_anchor"] = "negative_actor_response_contains_runtime_bound_fixture_id"
        anchored["confidence"] = max(float(anchored.get("confidence") or 0.0), 0.94)
        anchored["reason"] = f"{anchored.get('reason')}; response contains runtime-bound fixture id(s) {leaked[:3]}"
        return anchored
    anchored["verdict"] = "needs_more_evidence"
    anchored["confidence"] = min(float(anchored.get("confidence") or 0.0), 0.59)
    anchored["reason"] = (
        f"{verification.get('reason')}; response did not contain the runtime-bound fixture id(s) "
        f"{bound_ids[:3]}, so the observed business payload may be public or unrelated"
    )
    return anchored


def _find_negative_resource_values(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (int, float)) and v < 0 and NEGATIVE_NUMBER_KEY_RE.search(str(k)):
                hits.append({"path": path, "value": v})
            hits.extend(_find_negative_resource_values(v, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value[:20]):
            hits.extend(_find_negative_resource_values(item, f"{prefix}[{idx}]" if prefix else f"[{idx}]"))
    return hits[:30]


def _replace_fixture_runtime_value(value: Any, old_id: str, new_id: str) -> Any:
    if not old_id or not new_id or old_id == new_id:
        return value
    if isinstance(value, dict):
        return {k: _replace_fixture_runtime_value(v, old_id, new_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_fixture_runtime_value(v, old_id, new_id) for v in value]
    if isinstance(value, str):
        return value.replace(old_id, new_id)
    return value


def _bind_auto_fixture_response_id(config: dict[str, Any], probe: dict[str, Any], item: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    bind_to = item.get("bind_response_id_to") or []
    if isinstance(bind_to, str):
        bind_to = [bind_to]
    bind_fields = [str(x) for x in bind_to if str(x or "").strip()]
    if not bind_fields:
        return {}
    response_id = _extract_id_for_bind_fields(response.get("payload"), bind_fields)
    if not response_id:
        return {"bound": False, "reason": "setup_response_missing_bindable_id", "bind_response_id_to": bind_fields}
    bundle = _auto_fixture_bundle(config, probe)
    old_id = str(((bundle.get("receipt") or {}).get("primary_fixture_id") if isinstance(bundle.get("receipt"), dict) else "") or "")
    path_params = bundle.setdefault("path_params", {}) if isinstance(bundle, dict) else {}
    previous_values: dict[str, str] = {}
    if isinstance(path_params, dict):
        for field in bind_fields:
            previous_values[field] = str(path_params.get(field) or "")
            path_params[field] = response_id
    primary_fields = {
        re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
        for name in infer_path_params(str(((probe.get("endpoint") or {}).get("path") if isinstance(probe.get("endpoint"), dict) else "") or ""))
    }
    replace_from = [v for v in previous_values.values() if v]
    if old_id and any(re.sub(r"[^a-z0-9]+", "", field.lower()) in primary_fields for field in bind_fields):
        replace_from.append(old_id)
    for previous in dict.fromkeys([v for v in replace_from if v and v != response_id]):
        for key in ("request_body", "setup_requests", "snapshots", "cleanup_requests"):
            if key in bundle:
                bundle[key] = _replace_fixture_runtime_value(bundle.get(key), previous, response_id)
    runtime_bindings = bundle.setdefault("runtime_bindings", [])
    binding = {
        "bound": True,
        "source": "setup_response",
        "response_id": response_id,
        "previous_fixture_id": old_id,
        "previous_values": previous_values,
        "path_params": bind_fields,
    }
    if isinstance(runtime_bindings, list):
        runtime_bindings.append(binding)
    receipt = bundle.setdefault("receipt", {})
    if isinstance(receipt, dict):
        receipt["runtime_bound_fixture_id"] = response_id
        receipt["runtime_bound_path_params"] = bind_fields
    return binding


FLOW_BINDABLE_PATH_PARAM_RE = re.compile(
    r"^(?:id|uuid|code)$|^.+[_-]?(?:id|uuid|code)$",
    re.I,
)


def _flow_path_placeholders(steps: list[dict[str, Any]], start_index: int, fallback_path: str) -> list[str]:
    names: list[str] = []
    for path in [fallback_path] + [str(s.get("path") or "") for s in steps[start_index + 1 :] if isinstance(s, dict)]:
        for name in re.findall(r"\{([^{}]+)\}", str(path or "")):
            if name not in names:
                names.append(name)
    return names


def _explicit_flow_bind_fields(step: dict[str, Any]) -> list[str]:
    raw = (
        step.get("bind_response_id_to")
        or step.get("bind_response_id_to_path_params")
        or step.get("response_id_param")
        or step.get("response_id_path_param")
    )
    if isinstance(raw, str):
        raw = [raw]
    return [str(x) for x in (raw or []) if str(x or "").strip()]


def _generated_fixture_value(value: Any) -> bool:
    text = str(value or "")
    return not text or text.startswith("qb_auto") or bool(UNRESOLVED_PLACEHOLDER_RE.search(text))


def _infer_flow_bind_fields(
    *,
    step: dict[str, Any],
    steps: list[dict[str, Any]],
    step_index: int,
    decision_path: str,
    path_params: dict[str, Any],
) -> tuple[list[str], str]:
    explicit = _explicit_flow_bind_fields(step)
    if explicit:
        return explicit, "explicit_step_bind_response_id_to"
    future = _flow_path_placeholders(steps, step_index, decision_path)
    candidates = [name for name in future if FLOW_BINDABLE_PATH_PARAM_RE.search(name)]
    candidates = [name for name in candidates if _generated_fixture_value(path_params.get(name))]
    if len(candidates) == 1:
        return candidates, "inferred_single_future_generated_id_path_param"
    return [], "no_unambiguous_flow_response_id_path_param"


def _bind_flow_response_id_to_runtime(
    config: dict[str, Any],
    probe: dict[str, Any],
    path_params: dict[str, Any],
    bind_fields: list[str],
    response_id: str,
    reason: str,
) -> dict[str, Any]:
    fields = [str(f) for f in bind_fields if str(f or "").strip()]
    if not fields or not response_id:
        return {}
    old_values: dict[str, str] = {}
    for field in fields:
        old_values[field] = str(path_params.get(field) or "")
        path_params[field] = response_id
    bundle = _auto_fixture_bundle(config, probe)
    if isinstance(bundle, dict):
        bundle_params = bundle.setdefault("path_params", {})
        if isinstance(bundle_params, dict):
            for field in fields:
                old_values.setdefault(field, str(bundle_params.get(field) or ""))
                bundle_params[field] = response_id
        for old_id in [v for v in old_values.values() if v and v != response_id]:
            for key in ("request_body", "setup_requests", "snapshots", "cleanup_requests"):
                if key in bundle:
                    bundle[key] = _replace_fixture_runtime_value(bundle.get(key), old_id, response_id)
        runtime_bindings = bundle.setdefault("runtime_bindings", [])
        if isinstance(runtime_bindings, list):
            runtime_bindings.append({
                "bound": True,
                "source": "flow_step_response",
                "response_id": response_id,
                "previous_values": old_values,
                "path_params": fields,
                "reason": reason,
            })
    return {
        "bound": True,
        "source": "flow_step_response",
        "response_id": response_id,
        "previous_values": old_values,
        "path_params": fields,
        "reason": reason,
    }


def _effective_runtime_request(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, body: Any) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    path_params = _configured_path_params(config, probe, decision.method, decision.path)
    rendered, missing = _render_path(decision.path, path_params)
    query_params = _configured_query_params(config, probe, decision.method, decision.path)
    query_string = _render_query(query_params, path_params)
    request_path = _append_query(rendered, query_string)
    headers = _headers_for_probe(probe, config)
    request = {
        "method": decision.method,
        "url": _join_url(base_url, request_path),
        "path": request_path,
        "query": _redact(query_params),
        "headers": _redact(headers),
        "body": _redact(body),
        "path_params_bound_at_execution": _redact(path_params),
    }
    return request, headers, missing


def _primary_write_verification_response(probe: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    for response in responses:
        code = response.get("status_code")
        if isinstance(code, int) and 200 <= code < 300 and _negative_probe_payload_was_accepted(probe, response.get("payload")):
            return response
    return responses[0] if responses else {}


def _verify_write_observation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any]:
    risk_type = str(probe.get("risk_type") or "")
    expected = _expected_negative_statuses(probe)
    first = _primary_write_verification_response(probe, responses)
    status = first.get("status_code")
    payload = first.get("payload")
    summary = _safe_payload_summary(payload)
    sensitive_keys = _find_sensitive_keys(payload)
    negative_values = _find_negative_resource_values(payload)
    invariant_eval: dict[str, Any] = {}  # retired stub always returned {}
    db_snapshot = snapshots.get("db") if isinstance(snapshots.get("db"), dict) else {}
    db_diffs = [item for item in (db_snapshot.get("diffs") or []) if isinstance(item, dict)]
    db_anomalies = [item for item in db_diffs if item.get("added_rows") or item.get("removed_rows") or item.get("modified_rows")]
    db_evidence = None
    if db_anomalies:
        first_diff = db_anomalies[0]
        db_evidence = {
            "before_db_snapshot": ((db_snapshot.get("before_snapshots") or [{}])[0] if isinstance(db_snapshot.get("before_snapshots"), list) else {}),
            "after_db_snapshot": ((db_snapshot.get("after_snapshots") or [{}])[0] if isinstance(db_snapshot.get("after_snapshots"), list) else {}),
            "db_assertion": str(first_diff.get("detail") or "数据库前后快照存在差异"),
            "business_operation": f"{str((probe.get('endpoint') or {}).get('method') or '').upper()} {str((probe.get('endpoint') or {}).get('path') or '')}".strip(),
            "table": str(first_diff.get("table") or ""),
        }

    if invariant_eval.get("verdict") == "failed":
        failed_results = [r for r in (invariant_eval.get("results") or []) if isinstance(r, dict) and r.get("verdict") == "failed"]
        failed_fields: list[str] = []
        for item in failed_results:
            failed_fields.extend([str(x) for x in (item.get("failed_fields") or [])])
        return {
            "verdict": "validated_candidate",
            "reason": f"before/after business invariant failed: {invariant_eval.get('reason')}",
            "confidence": max(0.87, float(invariant_eval.get("confidence") or 0.0)),
            "payload_summary": summary,
            "sensitive_keys": sensitive_keys,
            "business_invariant_evaluation": invariant_eval,
            "db_evidence": db_evidence,
            "failed_fields": list(dict.fromkeys(failed_fields))[:30],
        }

    if status is None:
        return {"verdict": "inconclusive", "reason": first.get("error") or "network_error", "confidence": 0.0, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
    try:
        code = int(status)
    except Exception:
        code = 0

    if code in expected:
        return {"verdict": "falsified_or_protected", "reason": f"negative sandbox write was rejected with expected HTTP {code}", "confidence": 0.82, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}

    if code >= 500:
        return _server_error_finding(code, probe=probe, summary=summary, sensitive_keys=sensitive_keys, extra={"business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence})

    # Universal resource-conservation oracle (all write risk types, not just
    # conservation_probe): if an accepted write (2xx) echoes a resource value that
    # is impossibly negative — quantity/stock/amount/price/balance/points below
    # zero — a correct system should have rejected the input. This is a domain-
    # agnostic integrity invariant (counts and monies never go negative), driven by
    # the observed response, so it generalizes to any transactional project.
    if 200 <= code < 300 and negative_values:
        return {
            "verdict": "validated_candidate",
            "reason": (
                f"write accepted with HTTP {code} but the response exposed negative resource value(s) "
                f"{[str(h.get('path')) + '=' + str(h.get('value')) for h in negative_values[:3]]}; a correct "
                f"system must reject inputs that drive quantity/stock/amount/balance below zero"
            ),
            "confidence": 0.85,
            "payload_summary": summary,
            "sensitive_keys": sensitive_keys,
            "negative_values": negative_values,
            "business_invariant_evaluation": invariant_eval,
            "db_evidence": db_evidence,
        }

    if 200 <= code < 300 and _negative_probe_payload_was_accepted(probe, payload):
        method = str(first.get("method") or "").upper()
        fallback_note = " via safe GET fallback" if method == "GET" and str(first.get("fallback_from_method") or "").upper() == "POST" else ""
        return {
            "verdict": "validated_candidate",
            "reason": f"negative runtime probe returned HTTP {code}{fallback_note} and business payload shows the rejected operation was accepted",
            "confidence": 0.9,
            "payload_summary": summary,
            "sensitive_keys": sensitive_keys,
            "business_invariant_evaluation": invariant_eval,
            "db_evidence": db_evidence,
        }

    if _is_auth_boundary_risk(probe, risk_type) or risk_type in {"ownership_scope_probe", "state_transition_probe", "async_external_event_probe"}:
        if 200 <= code < 300:
            return {"verdict": "validated_candidate", "reason": f"negative sandbox write was accepted with HTTP {code}; expected one of {sorted(expected)}", "confidence": 0.86, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
        return {"verdict": "inconclusive", "reason": f"negative sandbox write returned unexpected HTTP {code}", "confidence": 0.38, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}

    if risk_type == "idempotency_replay_probe":
        ok_responses = [r for r in responses if isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300]
        ids = [_extract_id_like(r.get("payload")) for r in ok_responses]
        ids = [x for x in ids if x]
        if len(ok_responses) >= 2 and len(set(ids)) >= 2:
            return {"verdict": "validated_candidate", "reason": f"replayed sandbox write produced multiple distinct resource identifiers: {ids[:3]}", "confidence": 0.88, "payload_summary": summary, "sensitive_keys": sensitive_keys, "replay_ids": ids[:5], "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
        if len(ok_responses) >= 2 and ids and len(set(ids)) == 1:
            return {"verdict": "observed_no_finding", "reason": "replayed sandbox write returned the same resource identifier; no duplicate side effect observed by response oracle", "confidence": 0.62, "payload_summary": summary, "sensitive_keys": sensitive_keys, "replay_ids": ids[:5], "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
        if len(ok_responses) >= 2:
            return {"verdict": "needs_more_evidence", "reason": "replayed write was accepted, but response lacks stable resource identifiers or side-effect oracle", "confidence": 0.5, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
        if code in expected:
            return {"verdict": "falsified_or_protected", "reason": f"replay rejected with HTTP {code}", "confidence": 0.76, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}

    if risk_type == "conservation_probe":
        if negative_values:
            return {"verdict": "validated_candidate", "reason": "sandbox write response exposed negative resource-like values", "confidence": 0.83, "payload_summary": summary, "sensitive_keys": sensitive_keys, "negative_values": negative_values, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
        if 200 <= code < 300:
            snap_count = len(snapshots.get("before") or []) + len(snapshots.get("after") or [])
            reason = "write accepted; conservation requires configured before/after DB or API snapshots for confirmation"
            if snap_count:
                reason = "write accepted and snapshots captured; manual/advanced reconciliation is required before confirmation"
            if invariant_eval.get("verdict") == "passed" and snap_count:
                return {"verdict": "observed_no_finding", "reason": "write accepted, but derived before/after conservation/resource invariants passed on observed snapshots", "confidence": 0.62, "payload_summary": summary, "sensitive_keys": sensitive_keys, "snapshot_count": snap_count, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
            return {"verdict": "needs_more_evidence", "reason": reason, "confidence": 0.48, "payload_summary": summary, "sensitive_keys": sensitive_keys, "snapshot_count": snap_count, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}

    if 200 <= code < 300:
        if invariant_eval.get("verdict") == "passed":
            return {"verdict": "observed_no_finding", "reason": f"sandbox write returned HTTP {code}; derived before/after invariants passed", "confidence": 0.58, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
        return {"verdict": "observed_no_finding", "reason": f"sandbox write returned HTTP {code}; no runtime oracle matched", "confidence": 0.4, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}
    return {"verdict": "inconclusive", "reason": f"sandbox write returned HTTP {code}; no runtime oracle matched", "confidence": 0.35, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval, "db_evidence": db_evidence}


def _snapshot_requests(config: dict[str, Any], probe: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    cid = str(probe.get("candidate_id") or "")
    ep = probe.get("endpoint") or {}
    key = f"{str(ep.get('method') or '').upper()} {str(ep.get('path') or '')}"
    snapshots = config.get("snapshots") or {}
    reqs: list[dict[str, Any]] = []
    if isinstance(snapshots, dict):
        selected = snapshots.get(cid) or snapshots.get(key) or snapshots.get("*") or {}
        if isinstance(selected, dict):
            raw = selected.get(phase) or []
            if isinstance(raw, dict):
                raw = [raw]
            reqs.extend([r for r in raw if isinstance(r, dict)])
    # Commercial default: when customer did not configure snapshots, use the
    # OpenAPI-derived snapshot reads planned by the auto data factory.
    if not reqs and _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        auto_snaps = bundle.get("snapshots") if isinstance(bundle, dict) else {}
        raw = ((auto_snaps or {}).get(phase) if isinstance(auto_snaps, dict) else []) or []
        if isinstance(raw, dict):
            raw = [raw]
        reqs.extend([r for r in raw if isinstance(r, dict)])
    return reqs[:5]


def _execute_snapshots(config: dict[str, Any], base_url: str, probe: dict[str, Any], phase: str, timeout: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _snapshot_requests(config, probe, phase):
        method = str(item.get("method") or "GET").upper()
        if method not in READ_METHODS:
            out.append({"skipped": True, "reason": f"snapshot_method_not_read_only:{method}"})
            continue
        path_params = _fixture_item_path_params(config, probe, item)
        path, missing = _render_path(str(item.get("path") or ""), path_params)
        if missing:
            out.append({"skipped": True, "reason": f"snapshot_missing_path_params:{','.join(missing)}", "path": item.get("path"), "observer_kind": item.get("observer_kind")})
            continue
        query_string = _render_query(item.get("query"), path_params)
        request_path = path + (("?" + query_string) if query_string else "")
        headers = _headers_from_config(config)
        response = _http_request(method, _join_url(base_url, request_path), headers, timeout=timeout)
        out.append({
            "method": method,
            "path": request_path,
            "observer_kind": item.get("observer_kind"),
            "evidence_goal": item.get("evidence_goal"),
            "source": item.get("source"),
            "response": {"status_code": response.get("status_code"), "error": response.get("error"), "payload": _redact(response.get("payload")), "duration_ms": response.get("duration_ms")},
        })
    return out


def _markdown_schema_tables(text: str) -> set[str]:
    """Extract candidate database table names from a schema doc that is Markdown
    rather than raw ``CREATE TABLE`` DDL — e.g. a "主要表" listing or ``table.column``
    references. Generic: relies only on identifier shape, no per-project names.
    """
    names: set[str] = set()
    t = str(text or "")
    for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"`]?([a-z_][a-z0-9_]*)", t, re.I):
        names.add(m.group(1).lower())
    _stop = {"table", "field", "column", "index", "note", "notes", "desc", "http", "json", "api", "url", "the", "and"}
    for line in t.splitlines():
        mm = re.match(r"\s*\|\s*([A-Za-z_][A-Za-z0-9_]{2,40})\s*\|", line)
        if mm:
            tok = mm.group(1).lower()
            if tok not in _stop:
                names.add(tok)
    for m in re.finditer(r"\b([a-z_][a-z0-9_]{2,40})\.[a-z_][a-z0-9_]+\b", t):
        tok = m.group(1).lower()
        if tok not in _stop and not tok.endswith("_md"):
            names.add(tok)
    return {n for n in names if len(n) >= 3 and not n.isdigit()}


def _db_snapshot_tables(config: dict[str, Any], probe: dict[str, Any]) -> list[str]:
    tables: list[str] = []
    try:
        from ..auto_test_data_factory import _infer_table_from_path, _parse_sql_tables
    except Exception:
        return tables

    input_dir = config.get("input_dir") or config.get("project_input_dir")
    schema_text = ""
    parsed_tables: dict[str, dict[str, Any]] = {}
    if input_dir:
        schema_path = Path(str(input_dir)) / "schema.sql"
        if schema_path.exists():
            try:
                schema_text = schema_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                schema_text = ""
    if schema_text:
        parsed_tables = _parse_sql_tables(schema_text)
    if not parsed_tables and input_dir:
        # Fallback: many real customers document their schema as Markdown (table
        # listings / field references) instead of shipping CREATE TABLE DDL. Derive
        # table names from any schema-like doc so DB before/after evidence is still
        # captured — this closes the "证据链可复现" gap on md-only projects.
        md_names: set[str] = set()
        for p in sorted(Path(str(input_dir)).glob("*.md")):
            try:
                md_names |= _markdown_schema_tables(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
        parsed_tables = {name: {} for name in md_names}
    if not parsed_tables:
        return tables

    endpoint = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    endpoint_path = normalize_path_placeholders(str(endpoint.get("path") or ""))
    candidate = _infer_table_from_path(endpoint_path, parsed_tables)
    if candidate:
        tables.append(candidate)

    for ref in probe.get("source_refs") or []:
        if not isinstance(ref, dict):
            continue
        text = " ".join(str(ref.get(key) or "") for key in ("section", "quote", "file"))
        lowered = text.lower()
        for table_name in parsed_tables.keys():
            if table_name.lower() in lowered and table_name not in tables:
                tables.append(table_name)
    return tables[:5]


def _execute_db_snapshot(config: dict[str, Any], probe: dict[str, Any], phase: str) -> dict[str, Any]:
    tables = _db_snapshot_tables(config, probe)
    if not tables:
        return {"status": "skipped", "reason": "no_db_tables_inferred", "tables": []}
    try:
        from .db_snapshot_verifier import DBSnapshotVerifier
    except Exception as exc:
        return {"status": "skipped", "reason": f"db_snapshot_verifier_unavailable:{type(exc).__name__}", "tables": tables}

    verifier = DBSnapshotVerifier()
    if not verifier.configured:
        return {"status": "skipped", "reason": "db_snapshot_not_configured", "tables": tables}
    try:
        if phase == "before":
            verifier.snapshot_before(tables)
            return {
                "status": "captured",
                "phase": phase,
                "tables": tables,
                "before_snapshots": [snap.to_dict() for snap in verifier._before.values()],
                "_verifier": verifier,
            }
        previous = config.get("_qualibug_db_snapshot_before")
        if not isinstance(previous, dict):
            return {"status": "skipped", "reason": "db_snapshot_before_missing", "tables": tables}
        before_verifier = previous.get("verifier")
        if before_verifier is None:
            return {"status": "skipped", "reason": "db_snapshot_before_verifier_missing", "tables": tables}
        before_verifier.snapshot_after(tables)
        result = before_verifier.verify()
        return {
            "status": "captured",
            "phase": phase,
            "tables": tables,
            "db_type": result.db_type,
            "tables_checked": result.tables_checked,
            "before_snapshots": result.before_snapshots,
            "after_snapshots": result.after_snapshots,
            "diffs": result.diffs,
            "findings": result.findings,
            "duration_ms": result.duration_ms,
        }
    except Exception as exc:
        return {"status": "failed", "reason": f"db_snapshot_failed:{type(exc).__name__}", "tables": tables}


def _auto_fixture_requests(config: dict[str, Any], probe: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if not _auto_fixture_enabled(config):
        return []
    bundle = _auto_fixture_bundle(config, probe)
    if str(bundle.get("error") or "").strip():
        raise RuntimeError(
            f"auto_fixture_generation_failed:{probe.get('candidate_id') or 'unknown_candidate'}:{bundle.get('error')}"
        )
    receipt = bundle.get("receipt") if isinstance(bundle.get("receipt"), dict) else {}
    source_block = str(receipt.get("fixture_setup_blocked_reason") or "").strip()
    if source_block and key in {"setup_requests", "cleanup_requests"}:
        return [{
            "status": "blocked",
            "reason": f"auto_fixture_source_contract_blocked:{source_block}",
            "source_contract_reason": source_block,
        }]
    raw = bundle.get(key) if isinstance(bundle, dict) else []
    return [r for r in (raw if isinstance(raw, list) else []) if isinstance(r, dict)][:5]


def _runtime_bound_fixture_path_params(config: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    """Return path params that were rebound from observed fixture/flow responses."""
    if not _auto_fixture_enabled(config):
        return {}
    bundle = _auto_fixture_bundle(config, probe)
    if not isinstance(bundle, dict):
        return {}
    params = bundle.get("path_params") if isinstance(bundle.get("path_params"), dict) else {}
    fields: list[str] = []
    receipt = bundle.get("receipt") if isinstance(bundle.get("receipt"), dict) else {}
    for field in receipt.get("runtime_bound_path_params") or []:
        if str(field) not in fields:
            fields.append(str(field))
    for binding in bundle.get("runtime_bindings") or []:
        if not isinstance(binding, dict) or not binding.get("bound"):
            continue
        for field in binding.get("path_params") or []:
            if str(field) not in fields:
                fields.append(str(field))
    return {field: params[field] for field in fields if isinstance(params, dict) and field in params}


def _fixture_item_path_params(config: dict[str, Any], probe: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Merge path params for setup/snapshot/cleanup with runtime ids winning safely.

    Generated fixture items can contain placeholders such as ``qb_auto_order_1``.
    Once an earlier setup step returns the real server id, later fixture setup,
    snapshot and cleanup requests must use that observed id.  Customer-supplied
    concrete params still win unless their value is one of QualiBug's generated
    placeholders.
    """
    path_params = dict((config.get("path_params") or {}).get("*") or {})
    if isinstance(item.get("path_params"), dict):
        path_params.update(item.get("path_params") or {})
    if _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        bundle_params = bundle.get("path_params") if isinstance(bundle, dict) and isinstance(bundle.get("path_params"), dict) else {}
        for key, value in (bundle_params or {}).items():
            path_params.setdefault(str(key), value)
        for key, value in _runtime_bound_fixture_path_params(config, probe).items():
            if key not in path_params or _generated_fixture_value(path_params.get(key)):
                path_params[key] = value
    return path_params


def _render_fixture_runtime_value(value: Any, path_params: dict[str, Any], original_params: dict[str, Any] | None = None) -> Any:
    """Render observed runtime ids into fixture bodies/queries.

    Fixture bodies generated before execution often carry either ``{entity_id}``
    placeholders or QualiBug-generated ids such as ``qb_auto_entity_1``.  After
    earlier setup steps return real server ids, later fixture bodies must use the
    same observed ids as the path/query; otherwise the child object can be
    created under the right URL but still reference the stale parent id in JSON.
    """
    originals = original_params if isinstance(original_params, dict) else {}
    if isinstance(value, dict):
        return {k: _render_fixture_runtime_value(v, path_params, originals) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_fixture_runtime_value(v, path_params, originals) for v in value]
    if isinstance(value, str):
        rendered = value
        for key, replacement in (path_params or {}).items():
            rendered = rendered.replace("{" + str(key) + "}", str(replacement))
        for key, original in originals.items():
            replacement = path_params.get(key)
            if replacement is None or replacement == original:
                continue
            if _generated_fixture_value(original):
                rendered = rendered.replace(str(original), str(replacement))
        return rendered
    return value


def _fixture_request_body(config: dict[str, Any], probe: dict[str, Any], item: dict[str, Any], path_params: dict[str, Any]) -> Any:
    body = item.get("body")
    if body is None:
        return None
    originals = item.get("path_params") if isinstance(item.get("path_params"), dict) else {}
    return _render_fixture_runtime_value(body, path_params, originals)


def _runtime_body_binding_summary(original: Any, rendered: Any) -> dict[str, Any]:
    if original == rendered:
        return {"bound": False}
    return {"bound": True, "original": _safe_payload_summary(original), "rendered": _safe_payload_summary(rendered)}


def _runtime_binding_original_values(config: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    """Return pre-runtime placeholder values observed before response id binding.

    Multi-step flow steps can carry their own request bodies.  Those bodies may
    contain either ``{entity_id}`` placeholders or the original generated
    ``qb_auto_*`` IDs.  After a previous step binds the real server id, render
    step bodies against the current path params and replace any remembered
    generated values from earlier runtime bindings.
    """
    if not _auto_fixture_enabled(config):
        return {}
    bundle = _auto_fixture_bundle(config, probe)
    if not isinstance(bundle, dict):
        return {}
    originals: dict[str, Any] = {}
    for binding in bundle.get("runtime_bindings") or []:
        if not isinstance(binding, dict) or not binding.get("bound"):
            continue
        previous = binding.get("previous_values") if isinstance(binding.get("previous_values"), dict) else {}
        for key, value in previous.items():
            if value not in (None, "", [], {}):
                originals[str(key)] = value
    return originals


def _flow_step_body_template(step: dict[str, Any], fallback_body: Any) -> tuple[Any, str]:
    if "body" in step:
        return step.get("body"), "step.body"
    if "request_body" in step:
        return step.get("request_body"), "step.request_body"
    if "json" in step:
        return step.get("json"), "step.json"
    return fallback_body, "shared_probe_body"


def _render_flow_step_body(config: dict[str, Any], probe: dict[str, Any], step: dict[str, Any], fallback_body: Any, path_params: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    template, source = _flow_step_body_template(step, fallback_body)
    rendered = _render_fixture_runtime_value(template, path_params, _runtime_binding_original_values(config, probe))
    summary = _runtime_body_binding_summary(template, rendered)
    summary["source"] = source
    return rendered, summary


def _render_runtime_target_body(
    config: dict[str, Any],
    probe: dict[str, Any],
    method: str,
    path: str,
    body: Any,
) -> tuple[Any, dict[str, Any]]:
    """Render observed fixture ids into the final target request body.

    Setup, snapshot, cleanup and flow-step bodies already bind runtime ids.  The
    one remaining gap was the main write probe body when it came from
    ``request_bodies`` or another advanced override: the URL could target the
    server-created resource while JSON still carried ``{entity_id}`` or
    ``qb_auto_*`` placeholders.  Render the target body against the same
    runtime path params immediately before execution and record a receipt so the
    report shows whether body binding actually happened.
    """
    path_params = _configured_path_params(config, probe, method, path)
    rendered = _render_fixture_runtime_value(body, path_params, _runtime_binding_original_values(config, probe))
    summary = _runtime_body_binding_summary(body, rendered)
    summary["source"] = "runtime_target_request_body"
    return rendered, summary


def _execute_auto_fixture_requests(config: dict[str, Any], base_url: str, probe: dict[str, Any], key: str, timeout: float) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    allowed_routes_value = config.get("fixture_allowed_routes")
    allowed_routes: set[tuple[str, str]] | None = None
    if allowed_routes_value is not None:
        if not isinstance(allowed_routes_value, list):
            raise TypeError("fixture_allowed_routes_must_be_list")
        allowed_routes = {
            (
                str(route.get("method") or "").upper(),
                normalize_path_placeholders(str(route.get("path") or "")).split("?", 1)[0],
            )
            for route in allowed_routes_value
            if isinstance(route, dict)
            and str(route.get("method") or "").strip()
            and str(route.get("path") or "").strip().startswith("/")
        }
    initial_items = _auto_fixture_requests(config, probe, key)
    for index in range(len(initial_items)):
        current_items = _auto_fixture_requests(config, probe, key)
        if index >= len(current_items):
            break
        item = current_items[index]
        if str(item.get("status") or "").lower() == "blocked":
            receipts.append(dict(item))
            continue
        method = str(item.get("method") or "POST").upper()
        if method not in WRITE_METHODS:
            receipts.append({"status": "skipped", "reason": f"auto_fixture_method_not_write:{method}", "path": item.get("path")})
            continue
        path_params = _fixture_item_path_params(config, probe, item)
        query_string = _render_query(item.get("query"), path_params)
        headers = _fixture_control_headers(config)
        if isinstance(item.get("headers"), dict):
            headers.update({str(k): str(v) for k, v in (item.get("headers") or {}).items()})
        request_body = _fixture_request_body(config, probe, item, path_params)
        path_candidates = [str(item.get("path") or "").strip()]
        if isinstance(item.get("path_candidates"), list):
            path_candidates.extend(str(candidate or "").strip() for candidate in item.get("path_candidates") if str(candidate or "").strip())
        if allowed_routes is not None:
            path_candidates = [
                candidate
                for candidate in path_candidates
                if (
                    method,
                    normalize_path_placeholders(candidate).split("?", 1)[0],
                ) in allowed_routes
            ]
            if not path_candidates:
                receipts.append(
                    {
                        "status": "blocked",
                        "reason": "auto_fixture_path_not_source_documented",
                        "method": method,
                        "path": item.get("path"),
                    }
                )
                continue
        tried_paths: list[str] = []
        response: dict[str, Any] = {}
        request_path = ""
        accepted = False
        binding: dict[str, Any] = {}
        missing: list[str] = []
        for candidate_path in dict.fromkeys(path_candidates):
            path, missing = _render_path(candidate_path, path_params)
            if missing:
                continue
            request_path = path + (("?" + query_string) if query_string else "")
            tried_paths.append(request_path)
            response = _http_request(method, _join_url(base_url, request_path), headers, body=request_body, timeout=timeout)
            code = response.get("status_code")
            accepted = bool(isinstance(code, int) and 200 <= int(code) < 300)
            if accepted:
                binding = _bind_auto_fixture_response_id(config, probe, item, response)
                break
            if code not in {404, 405, 501}:
                break
        if not request_path and missing:
            receipts.append({"status": "blocked", "reason": f"auto_fixture_missing_path_params:{','.join(missing)}", "path": item.get("path")})
            continue
        code = response.get("status_code")
        receipts.append({
            "status": "executed",
            "purpose": item.get("purpose") or key,
            "accepted": accepted,
            "method": method,
            "path": request_path,
            "path_candidates_tried": tried_paths,
            "used_fixture_control_headers": True,
            "path_params_bound_at_execution": _redact(path_params),
            "body_runtime_binding": _runtime_body_binding_summary(item.get("body"), request_body),
            "runtime_binding": binding,
            "response": {"status_code": code, "error": response.get("error"), "payload": _redact(response.get("payload")), "duration_ms": response.get("duration_ms")},
        })
    return receipts


def _execute_parallel_write_attempts(method: str, url: str, headers: dict[str, str], body: Any, *, attempts: int, timeout: float) -> list[dict[str, Any]]:
    attempts = max(2, min(int(attempts or 2), 8))
    barrier = threading.Barrier(attempts)
    lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=max(1.0, min(timeout, 5.0)))
        except Exception:
            pass
        started = time.time()
        response = _http_request(method, url, headers, body=body, timeout=timeout)
        response["attempt"] = idx + 1
        response["parallel"] = True
        response["started_at_ms"] = int(started * 1000)
        with lock:
            results.append(response)

    threads = [threading.Thread(target=worker, args=(idx,), daemon=True) for idx in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 1.0)
    return sorted(results, key=lambda r: int(r.get("attempt") or 0))


def _verify_concurrency_observation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any] | None:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    if plan.get("strategy") != "concurrency_race_runtime_probe":
        return None
    invariant_eval: dict[str, Any] = {}  # retired stub always returned {}
    if invariant_eval.get("verdict") == "failed":
        return {
            "verdict": "validated_candidate",
            "reason": f"parallel race probe broke before/after invariant: {invariant_eval.get('reason')}",
            "confidence": max(0.9, float(invariant_eval.get("confidence") or 0.0)),
            "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
            "sensitive_keys": [],
            "business_invariant_evaluation": invariant_eval,
        }
    ok = [r for r in responses if isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300]
    ids = [_extract_id_like(r.get("payload")) for r in ok]
    ids = [x for x in ids if x]
    race_family = str(plan.get("race_family") or "")
    if race_family in {"idempotency_race", "stock_oversell_race", "terminal_transition_race", "approval_double_decision_race", "generic_duplicate_write_race"} and len(ok) >= 2:
        if len(set(ids)) >= 2:
            return {
                "verdict": "validated_candidate",
                "reason": f"parallel race probe accepted multiple attempts and produced distinct side-effect identifiers: {ids[:4]}",
                "confidence": 0.9,
                "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
                "sensitive_keys": [],
                "replay_ids": ids[:8],
                "business_invariant_evaluation": invariant_eval,
            }
        return {
            "verdict": "needs_more_evidence",
            "reason": f"parallel race probe accepted {len(ok)} attempts; response did not expose enough side-effect identifiers, before/after observers must decide",
            "confidence": 0.58,
            "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
            "sensitive_keys": [],
            "business_invariant_evaluation": invariant_eval,
        }
    if len(ok) <= 1:
        return {
            "verdict": "falsified_or_protected",
            "reason": "parallel race probe did not accept multiple writes; no duplicate side effect observed by HTTP oracle",
            "confidence": 0.72,
            "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
            "sensitive_keys": [],
            "business_invariant_evaluation": invariant_eval,
        }
    return None


def _runtime_query_surface_fallback_response(
    *,
    probe: dict[str, Any],
    method: str,
    original_path: str,
    first_response: dict[str, Any],
    headers: dict[str, str],
    base_url: str,
    timeout: float,
) -> dict[str, Any]:
    original_method = str(method or "").upper()
    if original_method not in READ_METHODS or not _unknown_runtime_surface(first_response):
        return first_response
    for contract_method in _query_surface_contract_methods(probe, original_path):
        if contract_method == original_method:
            continue
        response = _http_request(contract_method, _join_url(base_url, original_path), headers, timeout=timeout)
        response.update({
            "method": contract_method,
            "path": original_path,
            "fallback_from_method": original_method,
            "fallback_reason": "query_surface_contract_method_after_unknown_runtime_surface",
        })
        if not _unknown_runtime_surface(response):
            return response
    if original_method == "GET" and re.match(r"^/api/v\d+/.+/(?:list|search)(?:\?|$)", str(original_path or ""), re.I):
        response = _http_request("POST", _join_url(base_url, original_path), headers, timeout=timeout)
        response.update({
            "method": "POST",
            "path": original_path,
            "fallback_from_method": original_method,
            "fallback_reason": "query_surface_post_contract_probe_after_unknown_get_surface",
        })
        if not _unknown_runtime_surface(response):
            return response
    for path in _query_surface_get_fallback_paths(probe, original_path):
        if path == original_path:
            continue
        response = _http_request(original_method, _join_url(base_url, path), headers, timeout=timeout)
        response.update({
            "method": original_method,
            "path": path,
            "fallback_from_path": original_path,
            "fallback_reason": "query_surface_unknown_runtime_surface",
        })
        if not _unknown_runtime_surface(response):
            return response
    return first_response


def _execute_read_probe(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    setup_required = bool((decision.request or {}).get("runtime_fixture_setup_required"))
    setup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "setup_requests", timeout) if setup_required else []
    setup_blocked = any(r.get("status") == "blocked" for r in setup_receipts)
    effective_request, headers, missing = _effective_runtime_request(probe, decision, config, base_url, None)
    if setup_blocked:
        response = {"status_code": None, "error": "auto_fixture_setup_blocked", "payload": None, "duration_ms": 0}
        verification = {"verdict": "inconclusive", "reason": "auto_fixture_setup_blocked", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    elif missing:
        response = {"status_code": None, "error": f"runtime_missing_path_params_after_read_fixture_setup:{','.join(missing)}", "payload": None, "duration_ms": 0}
        verification = {"verdict": "inconclusive", "reason": response["error"], "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    else:
        response = _http_request(decision.method, str(effective_request.get("url") or ""), headers, timeout=timeout)
        response = _runtime_query_surface_fallback_response(
            probe=probe,
            method=decision.method,
            original_path=decision.path,
            first_response=response,
            headers=headers,
            base_url=base_url,
            timeout=timeout,
        )
        verification = _verify_observation(probe, response, config=config)
        verification = _anchor_auth_boundary_fixture_evidence(probe, verification, response, config)
    cleanup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "cleanup_requests", timeout) if setup_required else []
    return {
        "candidate_id": decision.candidate_id,
        "risk_type": decision.risk_type,
        "method": decision.method,
        "path": decision.path,
        "request": effective_request if not setup_blocked else (decision.request | {"setup_blocked": True}),
        "fixture_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "response": {
            "method": response.get("method") or decision.method,
            "path": response.get("path") or decision.path,
            "fallback_from_method": response.get("fallback_from_method"),
            "fallback_from_path": response.get("fallback_from_path"),
            "fallback_reason": response.get("fallback_reason"),
            "status_code": response.get("status_code"),
            "error": response.get("error"),
            "payload": _redact(response.get("payload")),
            "duration_ms": response.get("duration_ms"),
        },
        "verification": verification,
        "source_refs": probe.get("source_refs") or [],
        "grounding_basis": probe.get("grounding_basis") or {},
    }


def _execute_flow_probe(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    scenario = plan.get("flow_scenario") if isinstance(plan.get("flow_scenario"), dict) else {}
    steps = [s for s in (scenario.get("steps") or []) if isinstance(s, dict)]
    setup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "setup_requests", timeout)
    setup_blocked = any(r.get("status") == "blocked" for r in setup_receipts)
    snapshots = {
        "before": [] if setup_blocked else _execute_snapshots(config, base_url, probe, "before", timeout),
        "after": [],
        "db": {},
    }
    if not setup_blocked:
        db_before = _execute_db_snapshot(config, probe, "before")
        snapshots["db"] = {"before": db_before}
        if db_before.get("status") == "captured" and db_before.get("_verifier") is not None:
            config["_qualibug_db_snapshot_before"] = {"verifier": db_before.get("_verifier"), "tables": db_before.get("tables") or []}
    responses: list[dict[str, Any]] = []
    shared_body, _reason = _configured_body(config, decision.candidate_id, decision.method, decision.path, probe)
    shared_params = _configured_path_params(config, probe, decision.method, decision.path)
    headers = _headers_for_probe(probe, config)
    if not setup_blocked:
        for idx, step in enumerate(steps[:8]):
            method = str(step.get("method") or decision.method).upper()
            raw_path = str(step.get("path") or decision.path)
            path, missing = _render_path(raw_path, shared_params)
            if missing:
                responses.append({"attempt": idx + 1, "step": idx + 1, "status_code": None, "error": f"flow_step_missing_path_params:{','.join(missing)}", "payload": {}})
                continue
            query_string = _render_query(step.get("query"), shared_params)
            request_path = _append_query(path, query_string)
            step_body, step_body_binding = _render_flow_step_body(config, probe, step, shared_body, shared_params)
            response = _http_request(method, _join_url(base_url, request_path), headers, body=step_body, timeout=timeout)
            runtime_binding: dict[str, Any] = {}
            if isinstance(response.get("status_code"), int) and 200 <= int(response.get("status_code")) < 300:
                bind_fields, bind_reason = _infer_flow_bind_fields(
                    step=step,
                    steps=steps[:8],
                    step_index=idx,
                    decision_path=decision.path,
                    path_params=shared_params,
                )
                response_id = _extract_id_for_bind_fields(response.get("payload"), bind_fields)
                runtime_binding = _bind_flow_response_id_to_runtime(
                    config,
                    probe,
                    shared_params,
                    bind_fields,
                    response_id,
                    bind_reason,
                )
                if runtime_binding:
                    shared_body = _replace_fixture_runtime_value(shared_body, str((runtime_binding.get("previous_values") or {}).get(bind_fields[0]) or ""), response_id)
            responses.append(response | {"attempt": idx + 1, "step": idx + 1, "flow_action": step.get("action"), "flow_path": request_path, "request_body_runtime_binding": step_body_binding, "runtime_binding": runtime_binding})
        snapshots["after"] = _execute_snapshots(config, base_url, probe, "after", timeout)
        db_after = _execute_db_snapshot(config, probe, "after")
        if isinstance(snapshots.get("db"), dict):
            snapshots["db"]["after"] = db_after
    cleanup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "cleanup_requests", timeout)
    verification = _verify_flow_observation(probe, responses, snapshots) if not setup_blocked else {"verdict": "inconclusive", "reason": "auto_fixture_setup_blocked", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    return {
        "candidate_id": decision.candidate_id,
        "risk_type": decision.risk_type,
        "method": decision.method,
        "path": decision.path,
        "request": decision.request,
        "fixture_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "responses": [
            {"attempt": r.get("attempt"), "step": r.get("step"), "flow_action": r.get("flow_action"), "flow_path": r.get("flow_path"), "request_body_runtime_binding": r.get("request_body_runtime_binding") or {}, "runtime_binding": r.get("runtime_binding") or {}, "status_code": r.get("status_code"), "error": r.get("error"), "payload": _redact(r.get("payload")), "duration_ms": r.get("duration_ms")}
            for r in responses
        ],
        "snapshots": snapshots,
        "verification": verification,
        "source_refs": probe.get("source_refs") or [],
        "grounding_basis": probe.get("grounding_basis") or {},
    }


def _verify_flow_observation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    scenario = plan.get("flow_scenario") if isinstance(plan.get("flow_scenario"), dict) else {}
    invariant_eval: dict[str, Any] = {}  # retired stub always returned {}
    db_evidence = _db_evidence_from_snapshots(probe, snapshots)
    before_after_snapshot = _flow_before_after_snapshot(responses)
    payload_summary = _safe_payload_summary([r.get("payload") for r in responses])
    common = {
        "payload_summary": payload_summary,
        "sensitive_keys": [],
        "business_invariant_evaluation": invariant_eval,
        "db_evidence": db_evidence,
        "before_after_snapshot": before_after_snapshot,
    }
    if invariant_eval.get("verdict") == "failed":
        failed_results = [r for r in (invariant_eval.get("results") or []) if isinstance(r, dict) and r.get("verdict") == "failed"]
        failed_fields: list[str] = []
        for item in failed_results:
            failed_fields.extend([str(x) for x in (item.get("failed_fields") or [])])
        return {
            "verdict": "validated_candidate",
            "reason": f"multi-step flow broke before/after invariant: {invariant_eval.get('reason')}",
            "confidence": max(0.89, float(invariant_eval.get("confidence") or 0.0)),
            "failed_fields": list(dict.fromkeys(failed_fields))[:30],
            **common,
        }
    strategy = str(scenario.get("strategy") or plan.get("strategy") or "")
    ok = [r for r in responses if isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300]
    if ok and db_evidence:
        return {
            "verdict": "validated_candidate",
            "reason": "multi-step flow was accepted and DB snapshots captured a real side-effect delta",
            "confidence": 0.9,
            **common,
        }
    if strategy == "illegal_order_inversion_flow" and ok:
        return {
            "verdict": "validated_candidate",
            "reason": f"illegal multi-step order inversion accepted {len(ok)} step(s); expected rejection/no side effect",
            "confidence": 0.87,
            **common,
        }
    if responses and not ok:
        return {
            "verdict": "falsified_or_protected",
            "reason": "multi-step negative flow rejected all write steps",
            "confidence": 0.74,
            **common,
        }
    return {
        "verdict": "needs_more_evidence",
        "reason": "multi-step flow executed but runtime oracle needs observer deltas for confirmation",
        "confidence": 0.52,
        **common,
    }


def _db_evidence_from_snapshots(probe: dict[str, Any], snapshots: dict[str, Any]) -> dict[str, Any] | None:
    db_snapshot = snapshots.get("db") if isinstance(snapshots.get("db"), dict) else {}
    db_diffs = [item for item in (db_snapshot.get("diffs") or []) if isinstance(item, dict)]
    db_anomalies = [item for item in db_diffs if item.get("added_rows") or item.get("removed_rows") or item.get("modified_rows")]
    if not db_anomalies:
        return None
    first_diff = db_anomalies[0]
    endpoint = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    return {
        "before_db_snapshot": ((db_snapshot.get("before_snapshots") or [{}])[0] if isinstance(db_snapshot.get("before_snapshots"), list) else {}),
        "after_db_snapshot": ((db_snapshot.get("after_snapshots") or [{}])[0] if isinstance(db_snapshot.get("after_snapshots"), list) else {}),
        "db_assertion": str(first_diff.get("detail") or "数据库前后快照存在差异"),
        "business_operation": f"{str(endpoint.get('method') or '').upper()} {str(endpoint.get('path') or '')}".strip(),
        "table": str(first_diff.get("table") or ""),
    }


def _flow_before_after_snapshot(responses: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_steps = [item for item in responses if isinstance(item, dict) and (item.get("flow_path") or item.get("path"))]
    if not runtime_steps:
        return {}

    def _snapshot(step: dict[str, Any]) -> dict[str, Any]:
        status_code = step.get("status_code")
        try:
            status = int(status_code)
        except Exception:
            status = 0
        return {
            "action": str(step.get("flow_action") or step.get("step") or ""),
            "method": str(step.get("method") or "").upper(),
            "path": str(step.get("flow_path") or step.get("path") or ""),
            "status_code": status,
            "body": _redact(step.get("payload")),
        }

    return {
        "before": _snapshot(runtime_steps[0]),
        "after": _snapshot(runtime_steps[-1]),
    }


def _query_surface_path(path: str) -> bool:
    low = str(path or "").lower().split("?", 1)[0].rstrip("/")
    return low == "/list" or low == "/search" or low.endswith("/list") or low.endswith("/search")


def _unknown_runtime_surface(response: dict[str, Any]) -> bool:
    payload = response.get("payload")
    return int(response.get("status_code") or 0) == 404 and isinstance(payload, dict) and payload.get("error") == "unknown_runtime_surface"


def _query_surface_get_fallback_paths(probe: dict[str, Any], original_path: str) -> list[str]:
    if not _query_surface_path(original_path):
        return []
    suffix = str(original_path or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
    hits: list[str] = []
    pattern = re.compile(r"(/api/v\d+/[^\s`|，,。；;]+)")
    for ref in probe.get("source_refs") or []:
        if not isinstance(ref, dict):
            continue
        text = " ".join(str(ref.get(k) or "") for k in ("section", "quote"))
        for match in pattern.findall(text):
            candidate = match.strip().rstrip("`")
            candidate_base = candidate.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
            if candidate_base == suffix:
                hits.append(candidate)
    if not hits:
        hits.append(original_path)
    return list(dict.fromkeys(hits))[:3]


def _query_surface_contract_methods(probe: dict[str, Any], original_path: str) -> list[str]:
    if not _query_surface_path(original_path):
        return []
    suffix = str(original_path or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
    methods: list[str] = []
    pattern = re.compile(r"\b(GET|HEAD|POST)\s+(/[^\s`|，,。；;]+)", re.I)
    for ref in probe.get("source_refs") or []:
        if not isinstance(ref, dict) or str(ref.get("kind") or "") != "endpoint_contract":
            continue
        text = " ".join(str(ref.get(k) or "") for k in ("section", "quote"))
        for method, path in pattern.findall(text):
            candidate_base = str(path or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
            if candidate_base == suffix:
                methods.append(method.upper())
    return [m for m in dict.fromkeys(methods) if m in {"GET", "HEAD", "POST"}]


def _query_surface_input_paths(config: dict[str, Any], probe: dict[str, Any], original_path: str) -> list[str]:
    if not _query_surface_path(original_path):
        return []
    input_dir = config.get("input_dir") or config.get("project_input_dir")
    if not input_dir:
        return []
    input_path = Path(str(input_dir))
    api_md = input_path / "API.md"
    if not api_md.exists():
        api_md = input_path / "api.md"
    if not api_md.exists():
        return []
    cache = config.setdefault("_query_surface_input_path_cache", {})
    cache_key = str(api_md.resolve())
    text = cache.get(cache_key)
    if text is None:
        text = api_md.read_text(encoding="utf-8", errors="replace")
        cache[cache_key] = text
    suffix = str(original_path or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
    capability_code = str(((probe.get("endpoint") or {}).get("capability_code") if isinstance(probe.get("endpoint"), dict) else "") or "").upper()
    capability_number = capability_code[1:].lstrip("0") if re.match(r"^C\d+$", capability_code) else ""
    matches: list[tuple[int, str]] = []
    for section in re.finditer(r"^###\s*(?:\d+\.\s*)?(/[^\n]+)\n(?P<body>.*?)(?=^###\s|\Z)", str(text or ""), re.M | re.S):
        path = section.group(1).strip()
        base = path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
        if base != suffix or not path.startswith("/api/"):
            continue
        hay = f"{section.group(0)} {path}".upper()
        has_capability = bool(
            capability_code and (
                capability_code in hay
                or (capability_number and re.search(rf"###\s*0*{re.escape(capability_number)}\b", hay))
            )
        )
        if capability_code and not has_capability:
            continue
        matches.append((0 if has_capability else 1, path))
    return list(dict.fromkeys(path for _, path in sorted(matches)))[:5]


def _append_query_surface_get_fallbacks(
    *,
    probe: dict[str, Any],
    config: dict[str, Any],
    original_method: str,
    original_path: str,
    first_response: dict[str, Any],
    responses: list[dict[str, Any]],
    headers: dict[str, str],
    body: Any,
    base_url: str,
    timeout: float,
) -> None:
    if str(original_method or "").upper() != "POST" or not _unknown_runtime_surface(first_response):
        return
    for path in _query_surface_input_paths(config, probe, original_path):
        if path == original_path:
            continue
        response = _http_request("POST", _join_url(base_url, path), headers, body=body, timeout=timeout)
        responses.append(response | {
            "attempt": len(responses) + 1,
            "method": "POST",
            "path": path,
            "fallback_from_method": "POST",
            "fallback_from_path": original_path,
            "fallback_reason": "query_surface_input_path_after_unknown_runtime_surface",
        })
        if not _unknown_runtime_surface(response):
            return
    for path in _query_surface_get_fallback_paths(probe, original_path):
        response = _http_request("GET", _join_url(base_url, path), headers, timeout=timeout)
        responses.append(response | {
            "attempt": len(responses) + 1,
            "method": "GET",
            "path": path,
            "fallback_from_method": "POST",
            "fallback_from_path": original_path,
            "fallback_reason": "post_query_surface_unknown_runtime_surface",
        })
        if not _unknown_runtime_surface(response):
            break


def _execute_write_probe(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    setup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "setup_requests", timeout)
    setup_blocked = any(r.get("status") == "blocked" for r in setup_receipts)
    body, _reason = _configured_body(config, decision.candidate_id, decision.method, decision.path, probe)
    body, target_body_binding = _render_runtime_target_body(config, probe, decision.method, decision.path, body)
    effective_request, headers, missing = _effective_runtime_request(probe, decision, config, base_url, body)
    effective_request["body_runtime_binding"] = target_body_binding
    snapshots = {
        "before": [] if setup_blocked or missing else _execute_snapshots(config, base_url, probe, "before", timeout),
        "after": [],
        "db": {},
    }
    if not setup_blocked and not missing:
        db_before = _execute_db_snapshot(config, probe, "before")
        snapshots["db"] = {"before": db_before}
        if db_before.get("status") == "captured" and db_before.get("_verifier") is not None:
            config["_qualibug_db_snapshot_before"] = {"verifier": db_before.get("_verifier"), "tables": db_before.get("tables") or []}
    replay_count = _configured_replay_count(config, probe) if decision.risk_type in {"idempotency_replay_probe", "async_external_event_probe"} else 1
    responses: list[dict[str, Any]] = []
    if missing:
        responses.append({"attempt": 1, "status_code": None, "error": f"runtime_missing_path_params_after_setup:{','.join(missing)}", "payload": {}})
    elif not setup_blocked:
        probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
        concurrency = probe_plan.get("concurrency") if isinstance(probe_plan.get("concurrency"), dict) else {}
        if probe_plan.get("strategy") == "concurrency_race_runtime_probe" and concurrency:
            responses.extend(_execute_parallel_write_attempts(
                decision.method,
                str(effective_request.get("url") or ""),
                headers,
                body,
                attempts=int(concurrency.get("parallel_attempts") or 2),
                timeout=timeout,
            ))
        else:
            for idx in range(replay_count):
                response = _http_request(decision.method, str(effective_request.get("url") or ""), headers, body=body, timeout=timeout)
                responses.append(response | {"attempt": idx + 1})
            if responses:
                _append_query_surface_get_fallbacks(
                    probe=probe,
                    config=config,
                    original_method=decision.method,
                    original_path=decision.path,
                    first_response=responses[0],
                    responses=responses,
                    headers=headers,
                    body=body,
                    base_url=base_url,
                    timeout=timeout,
                )
        snapshots["after"] = _execute_snapshots(config, base_url, probe, "after", timeout)
        db_after = _execute_db_snapshot(config, probe, "after")
        if isinstance(snapshots.get("db"), dict):
            snapshots["db"]["after"] = db_after
    cleanup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "cleanup_requests", timeout)
    if missing:
        verification = {"verdict": "inconclusive", "reason": f"runtime_missing_path_params_after_setup:{','.join(missing)}", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    elif not setup_blocked:
        concurrency_verification = _verify_concurrency_observation(probe, responses, snapshots)
        verification = concurrency_verification or _verify_write_observation(probe, responses, snapshots)
    else:
        verification = {"verdict": "inconclusive", "reason": "auto_fixture_setup_blocked", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    return {
        "candidate_id": decision.candidate_id,
        "risk_type": decision.risk_type,
        "method": decision.method,
        "path": decision.path,
        "request": effective_request if not setup_blocked else (decision.request | {"setup_blocked": True}),
        "fixture_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "responses": [
            {"attempt": r.get("attempt"), "parallel": r.get("parallel"), "method": r.get("method") or decision.method, "path": r.get("path") or decision.path, "fallback_from_method": r.get("fallback_from_method"), "fallback_from_path": r.get("fallback_from_path"), "fallback_reason": r.get("fallback_reason"), "status_code": r.get("status_code"), "error": r.get("error"), "payload": _redact(r.get("payload")), "duration_ms": r.get("duration_ms")}
            for r in responses
        ],
        "snapshots": snapshots,
        "verification": verification,
        "source_refs": probe.get("source_refs") or [],
        "grounding_basis": probe.get("grounding_basis") or {},
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        f"# Grounded Probe Execution Report — {report.get('project_id') or ''}",
        "",
        "## Guardrail",
        "",
        f"- strict_no_peek: `{report.get('strict_no_peek')}`",
        "- source: `grounded_probe_plan.json` generated from `projects/<project>/input`",
        "- read-only probes: require explicit `--execute-readonly`",
        "- write probes: require explicit disposable sandbox approval, env flag, cleanup strategy and configured request bodies",
        "- runtime findings require observed HTTP evidence",
        "- Phase92P: before/after business invariants are auto-judged from observed snapshots",
        "- Phase92Q: OpenAPI-derived multi-observer snapshots expand runtime evidence",
        "- Phase92R: observer responses are semantically joined into a before/after business object graph",
        "- Phase92S: cross-observer conservation reconciliation compares state deltas with ledger/history deltas",
        "- Phase92T: validated findings include customer-ready evidence packages with strength score, invariant and delta summaries",
        "- Phase92U: customer impact triage assigns severity, priority, owner and escalation reason",
        "- Phase92V: report-level customer delivery index groups validated findings by priority, severity, risk family and evidence coverage",
        "- Phase92W: each finding links back to generated report, repro script and regression assets",
        "- Phase92X: P0/P1 findings include fix-verification checklists, rerun plans, close criteria and lifecycle status",
        "- Phase92Y: stable lifecycle signatures prevent false close/reopen when candidate ids or path parameters change",
        "- Phase92Z: P0/P1 findings generate developer remediation verification artifacts",
        "- Phase93A: runtime onboarding preflight reports target, auth, fixture, cleanup and observer readiness before probes run",
        "- Phase93B: per-probe runtime capability matrix explains ready, degraded and blocked execution lanes",
        "- Phase93C: onboarding remediation kit gives customers exact safe setup actions and config patch templates",
        "- Phase93D: runtime execution runbook sequences preflight, read-only, write-sandbox and fix-verification runs",
        "- Phase93E: runtime evidence readiness SLA gate quantifies commercial evidence coverage",
        "- Phase95B: runtime evidence scoreboard reports actual execution, setup, binding, snapshot and cleanup rates",
        "- Phase93F: SLA-gated execution policy separates must-run, degraded, blocked and supplemental probes",
        "- Phase93G: SLA gap prioritizer generates the smallest next onboarding delta patch",
        "- Phase93H: onboarding patch safety validator blocks production targets, raw secrets and unsafe cleanup gaps",
        "- Phase93I: write-sandbox approval packet packages customer approval requirements",
        "- Phase93J: commercial handoff bundle indexes all runtime onboarding, SLA, approval and remediation artifacts",
        "- Phase93K: commercial handoff acceptance gate validates bundle signoff readiness and blockers",
        "- Phase93L: commercial handoff secret audit blocks raw credentials in customer-facing artifacts",
        "- Phase93M: commercial handoff archive manifest and immutable run receipt hash delivery artifacts for rerun auditability",
        "- Phase93N: immutable handoff receipt comparison explains whether reruns use the same input and delivery archive lineage",
        "- Phase93O: commercial rerun audit gate decides whether a rerun may close previous findings under the same lineage",
        "",
        "## Summary",
        "",
        f"- probes total: {summary.get('probe_count')}",
        f"- original probes: {summary.get('original_probe_count')}; Phase94 bug-discovery probes added: {summary.get('phase94_added_probe_count')} (P0: {summary.get('phase94_added_p0_probe_count')})",
        f"- Phase94 multistep flow scenarios: {summary.get('phase94_multistep_flow_scenario_count')}",
        f"- executed read-only: {summary.get('executed_readonly_count')}",
        f"- executed write sandbox: {summary.get('executed_write_sandbox_count')}",
        f"- blocked: {summary.get('blocked_count')}",
        f"- dry-run only: {summary.get('dry_run_count')}",
        f"- validated candidates: {summary.get('validated_candidate_count')}",
        f"- protected/falsified: {summary.get('protected_count')}",
        f"- needs more evidence: {summary.get('needs_more_evidence_count')}",
        f"- probe outcomes: `{json.dumps((summary.get('probe_outcome_counts') or {}), ensure_ascii=False)}`",
        f"- customer delivery index: `{(report.get('customer_delivery_index') or {}).get('engine')}`",
        f"- by priority: `{json.dumps(((report.get('customer_delivery_index') or {}).get('by_priority') or {}), ensure_ascii=False)}`",
        f"- auto fixture setup requests: {summary.get('auto_fixture_setup_request_count')}",
        f"- auto fixture cleanup requests: {summary.get('auto_fixture_cleanup_request_count')}",
        f"- auto snapshot requests: {summary.get('auto_snapshot_request_count')}",
        f"- fix verification required: {summary.get('fix_verification_required_count')}",
        f"- closed by rerun: {summary.get('closed_by_rerun_count')}",
        f"- stable lifecycle matches: {summary.get('stable_lifecycle_match_count')}",
        f"- remediation work items: {summary.get('remediation_work_item_count')}",
        f"- onboarding preflight: `{((report.get('onboarding_preflight') or {}).get('status'))}`; P0/P1 ready: `{((report.get('onboarding_preflight') or {}).get('ready_for_p0_p1_runtime_validation'))}`",
        f"- runtime capability lanes: `{json.dumps(((report.get('runtime_capability_matrix') or {}).get('by_preflight_lane') or {}), ensure_ascii=False)}`",
        f"- onboarding remediation actions: `{((report.get('onboarding_remediation_kit') or {}).get('action_count'))}`",
        f"- runtime runbook status: `{((report.get('runtime_execution_runbook') or {}).get('status'))}`",
        f"- runtime evidence SLA gate: `{((report.get('runtime_evidence_readiness_sla_gate') or {}).get('status'))}` / score `{((report.get('runtime_evidence_readiness_sla_gate') or {}).get('commercial_readiness_score'))}`",
        f"- runtime evidence scoreboard integrity: `{summary.get('runtime_execution_integrity_score')}`; binding success `{summary.get('runtime_scoreboard_binding_success_rate')}%`",
        f"- runtime SLA policy: `{((report.get('runtime_sla_execution_policy') or {}).get('status'))}`",
        f"- write sandbox approval: `{((report.get('write_sandbox_approval_packet') or {}).get('status'))}`",
        f"- commercial handoff bundle: `{((report.get('commercial_handoff_bundle') or {}).get('status'))}`",
        f"- commercial handoff acceptance: `{((report.get('commercial_handoff_acceptance_gate') or {}).get('status'))}`",
        f"- commercial handoff secret audit: `{((report.get('commercial_handoff_secret_audit') or {}).get('status'))}`",
        f"- handoff archive manifest: `{((report.get('handoff_archive_manifest') or {}).get('status'))}` / lineage `{((report.get('immutable_run_receipt') or {}).get('run_lineage_id'))}`",
        f"- handoff receipt comparison: `{((report.get('handoff_receipt_comparison') or {}).get('status'))}` / changes `{((report.get('handoff_receipt_comparison') or {}).get('change_count'))}`",
        f"- handoff rerun audit gate: `{((report.get('handoff_rerun_audit_gate') or {}).get('status'))}` / closure allowed `{((report.get('handoff_rerun_audit_gate') or {}).get('closure_verification_allowed'))}`",
        "",
        "## Probe Outcomes",
        "",
        "| Candidate | Outcome | Role | Endpoint | HTTP | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for outcome in (report.get("probe_outcomes") or [])[:80]:
        lines.append(
            f"| `{outcome.get('candidate_id')}` | `{outcome.get('outcome')}` | `{outcome.get('role')}` | "
            f"`{outcome.get('method')} {outcome.get('path')}` | `{','.join(str(x) for x in (outcome.get('http_statuses') or []))}` | "
            f"{str(outcome.get('reason') or '')[:180]} |"
        )
    if len(report.get("probe_outcomes") or []) > 80:
        lines.append(f"| ... | ... | ... | ... | ... | first 80 of {len(report.get('probe_outcomes') or [])} shown |")
    lines.extend([
        "",
        "## Runtime findings",
        "",
    ])
    findings = report.get("findings") or []
    if not findings:
        lines.append("No runtime-validated candidates were produced in this run.")
        lines.append("")
    for f in findings[:80]:
        evidence = f.get("evidence") or {}
        lines.extend([
            f"### {f.get('finding_id')} — {f.get('title')}",
            "",
            f"- status: `{f.get('status')}` / confidence: `{f.get('confidence')}`",
            f"- risk_type: `{f.get('risk_type')}`",
            f"- endpoint: `{f.get('method')} {f.get('path')}`",
            f"- reason: {f.get('reason')}",
            f"- evidence: HTTP `{evidence.get('status_code')}`; payload `{json.dumps((evidence.get('payload_summary') or {}), ensure_ascii=False)}`",
            f"- evidence strength: `{f.get('evidence_grade')}` / `{f.get('evidence_strength_score')}`",
            f"- triage: `{f.get('priority')}` / `{f.get('severity')}` — {f.get('customer_impact_summary')}",
            f"- violated invariants: {', '.join(str(x.get('kind')) for x in (f.get('violated_invariants') or [])[:6]) or 'n/a'}",
            f"- delta summary: `{json.dumps((f.get('delta_summary') or {}), ensure_ascii=False)[:900]}`",
            f"- source refs: {'; '.join((str(r.get('file')) + ' / ' + str(r.get('section')) + ' / ' + str(r.get('kind'))) for r in (f.get('source_refs') or [])[:4])}",
            f"- fix verification lifecycle: `{((f.get('fix_verification') or {}).get('lifecycle_status'))}`; close criteria: `{json.dumps(((f.get('fix_verification') or {}).get('fix_close_criteria') or [])[:3], ensure_ascii=False)}`",
            "",
        ])
    lines.extend(["## Decisions", ""])
    for d in (report.get("decisions") or [])[:160]:
        lines.append(f"- `{d.get('candidate_id')}` `{d.get('method')} {d.get('path')}` → **{d.get('decision')}** ({d.get('reason')})")
    return "\n".join(lines)


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _render_repro_ps1(report: dict[str, Any]) -> str:
    lines = [
        "# Auto-generated by QualiBug grounded probe executor.",
        "# Review target/base URL before running. Commands are generated from input-only probe plans.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for decision in report.get("decisions") or []:
        req = decision.get("request") or {}
        headers = req.get("headers") or {}
        if headers:
            header_items = "; ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in headers.items())
            header_line = f"$headers = @{{ {header_items} }}"
            header_arg = " -Headers $headers"
        else:
            header_line = ""
            header_arg = ""
        if decision.get("method") in READ_METHODS:
            lines.append(f"# {decision.get('candidate_id')} {decision.get('risk_type')} — {decision.get('reason')}")
            if header_line:
                lines.append(header_line)
            lines.append(f"Invoke-WebRequest -Method {decision.get('method')} -Uri {_powershell_quote(req.get('url') or '')}{header_arg}")
            lines.append("")
        elif decision.get("decision") == "execute_write_sandbox":
            lines.append(f"# SANDBOX ONLY: {decision.get('candidate_id')} {decision.get('risk_type')} — {decision.get('reason')}")
            lines.append("# The following write command is commented out intentionally. Run only against the approved disposable sandbox.")
            if header_line:
                lines.append("# " + header_line)
            body = json.dumps(req.get("body") or {}, ensure_ascii=False).replace("'", "''")
            lines.append(f"# Invoke-WebRequest -Method {decision.get('method')} -Uri {_powershell_quote(req.get('url') or '')}{header_arg} -ContentType 'application/json' -Body '{body}'")
            lines.append("")
    return "\n".join(lines)


def _render_pytest(report: dict[str, Any]) -> str:
    read_cases: list[dict[str, Any]] = []
    write_cases: list[dict[str, Any]] = []
    for decision in report.get("decisions") or []:
        if str(decision.get("risk_type") or "") in AUTH_BOUNDARY_RISKS and decision.get("method") in READ_METHODS and (decision.get("request") or {}).get("url"):
            read_cases.append({"id": decision.get("candidate_id"), "method": decision.get("method"), "url": (decision.get("request") or {}).get("url"), "headers": (decision.get("request") or {}).get("headers") or {}})
        if decision.get("method") in WRITE_METHODS and decision.get("decision") == "execute_write_sandbox" and (decision.get("request") or {}).get("url"):
            write_cases.append({"id": decision.get("candidate_id"), "method": decision.get("method"), "url": (decision.get("request") or {}).get("url"), "headers": (decision.get("request") or {}).get("headers") or {}, "body": (decision.get("request") or {}).get("body") or {}})
    return "\n".join([
        "# Auto-generated QualiBug regression asset.",
        "# Read-only tests assert documented anonymous access boundaries.",
        "# Write tests are skipped by default and require a disposable sandbox.",
        "import os",
        "import pytest",
        "import requests",
        "",
        f"READ_CASES = {json.dumps(read_cases, ensure_ascii=False, indent=2)}",
        f"WRITE_CASES = {json.dumps(write_cases, ensure_ascii=False, indent=2)}",
        "",
        "@pytest.mark.parametrize('case', READ_CASES, ids=lambda c: c.get('id'))",
        "def test_anonymous_read_boundaries(case):",
        "    resp = requests.request(case['method'], case['url'], headers=case.get('headers') or {}, timeout=10)",
        "    assert resp.status_code in (401, 403, 404), resp.text[:500]",
        "",
        "@pytest.mark.skipif(os.environ.get('QUALIBUG_RUN_SANDBOX_WRITE_REGRESSION') != '1', reason='requires disposable sandbox')",
        "@pytest.mark.parametrize('case', WRITE_CASES, ids=lambda c: c.get('id'))",
        "def test_documented_negative_write_boundaries(case):",
        "    resp = requests.request(case['method'], case['url'], headers=case.get('headers') or {}, json=case.get('body') or {}, timeout=10)",
        "    assert resp.status_code in (400, 401, 403, 404, 409, 422), resp.text[:500]",
        "",
    ])


def _finding_from_observation(obs: dict[str, Any], finding_no: int, source: str) -> dict[str, Any]:
    verification = obs.get("verification") or {}
    evidence_status = None
    if obs.get("response"):
        evidence_status = (obs.get("response") or {}).get("status_code")
    elif obs.get("responses"):
        statuses = [r.get("status_code") for r in (obs.get("responses") or [])]
        evidence_status = statuses[0] if statuses else None
    evidence_package = package_runtime_finding_evidence(obs, source=source)
    finding = {
        "finding_id": f"GPF-{finding_no:04d}",
        "candidate_id": obs.get("candidate_id"),
        "title": f"{obs.get('risk_type')} validated for {obs.get('method')} {obs.get('path')}",
        "status": "validated_candidate",
        "risk_type": obs.get("risk_type"),
        "method": obs.get("method"),
        "path": obs.get("path"),
        "confidence": verification.get("confidence"),
        "evidence_strength_score": evidence_package.get("evidence_strength_score"),
        "evidence_grade": evidence_package.get("evidence_grade"),
        "reason": verification.get("reason"),
        "evidence": {"status_code": evidence_status, "payload_summary": verification.get("payload_summary"), "sensitive_keys": verification.get("sensitive_keys"), "replay_ids": verification.get("replay_ids"), "negative_values": verification.get("negative_values"), "business_invariant_evaluation": verification.get("business_invariant_evaluation"), "db_evidence": verification.get("db_evidence")},
        "evidence_package": evidence_package,
        "db_evidence": verification.get("db_evidence") or {},
        "violated_invariants": evidence_package.get("violated_invariants") or [],
        "delta_summary": evidence_package.get("delta_summary") or {},
        "source_refs": obs.get("source_refs") or [],
        "grounding_basis": obs.get("grounding_basis") or {},
        "source": source,
    }
    triage = triage_runtime_finding(finding)
    finding["customer_triage"] = triage
    finding["severity"] = triage.get("severity")
    finding["priority"] = triage.get("priority")
    finding["customer_impact_summary"] = triage.get("customer_impact_summary")
    return finding


def _observation_status_codes(obs: dict[str, Any]) -> list[int]:
    codes: list[int] = []
    if isinstance(obs.get("response"), dict):
        code = (obs.get("response") or {}).get("status_code")
        if isinstance(code, int):
            codes.append(code)
    for response in obs.get("responses") or []:
        if not isinstance(response, dict):
            continue
        code = response.get("status_code")
        if isinstance(code, int):
            codes.append(code)
    return codes


def _probe_outcome_role(verdict: str) -> str:
    if verdict == "validated_candidate":
        return "customer_finding"
    if verdict == "falsified_or_protected":
        return "protected_baseline"
    if verdict == "needs_more_evidence":
        return "evidence_gap"
    if verdict == "observed_no_finding":
        return "no_finding_observed"
    if verdict == "blocked_before_execution":
        return "blocked_before_execution"
    if verdict == "dry_run_only":
        return "dry_run_only"
    return "runtime_observation"


def _build_probe_outcomes(
    decisions: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    write_observations: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build one compact, customer-auditable outcome row for every probe."""
    obs_by_candidate: dict[str, dict[str, Any]] = {}
    for obs in observations + write_observations:
        candidate_id = str(obs.get("candidate_id") or "")
        if candidate_id:
            obs_by_candidate[candidate_id] = obs

    finding_by_candidate = {
        str(finding.get("candidate_id") or ""): finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("candidate_id")
    }

    outcomes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for decision in decisions:
        candidate_id = str(decision.get("candidate_id") or "")
        seen.add(candidate_id)
        obs = obs_by_candidate.get(candidate_id)
        if obs:
            verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
            verdict = str(verification.get("verdict") or "needs_more_evidence")
            finding = finding_by_candidate.get(candidate_id) or {}
            outcomes.append({
                "candidate_id": candidate_id,
                "decision": decision.get("decision"),
                "outcome": verdict,
                "status": verdict,
                "role": _probe_outcome_role(verdict),
                "finding_id": finding.get("finding_id") or "",
                "risk_type": obs.get("risk_type") or decision.get("risk_type"),
                "method": obs.get("method") or decision.get("method"),
                "path": obs.get("path") or decision.get("path"),
                "http_statuses": _observation_status_codes(obs),
                "reason": verification.get("reason") or decision.get("reason") or "",
                "customer_countable_bug": verdict == "validated_candidate",
                "customer_ready": bool(finding.get("customer_ready")),
            })
            continue

        decision_name = str(decision.get("decision") or "not_executed")
        if decision_name == "blocked":
            outcome = "blocked_before_execution"
        elif decision_name == "dry_run_only":
            outcome = "dry_run_only"
        else:
            outcome = "not_observed"
        outcomes.append({
            "candidate_id": candidate_id,
            "decision": decision.get("decision"),
            "outcome": outcome,
            "status": outcome,
            "role": _probe_outcome_role(outcome),
            "finding_id": "",
            "risk_type": decision.get("risk_type"),
            "method": decision.get("method"),
            "path": decision.get("path"),
            "http_statuses": [],
            "reason": decision.get("reason") or "",
            "customer_countable_bug": False,
            "customer_ready": False,
        })

    for candidate_id, obs in obs_by_candidate.items():
        if candidate_id in seen:
            continue
        verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
        verdict = str(verification.get("verdict") or "needs_more_evidence")
        finding = finding_by_candidate.get(candidate_id) or {}
        outcomes.append({
            "candidate_id": candidate_id,
            "decision": "observed_without_decision",
            "outcome": verdict,
            "status": verdict,
            "role": _probe_outcome_role(verdict),
            "finding_id": finding.get("finding_id") or "",
            "risk_type": obs.get("risk_type"),
            "method": obs.get("method"),
            "path": obs.get("path"),
            "http_statuses": _observation_status_codes(obs),
            "reason": verification.get("reason") or "",
            "customer_countable_bug": verdict == "validated_candidate",
            "customer_ready": bool(finding.get("customer_ready")),
        })
    return outcomes


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _count_status(receipts: list[dict[str, Any]], *, status: str | None = None, accepted: bool | None = None) -> int:
    total = 0
    for receipt in receipts:
        if status is not None and receipt.get("status") != status:
            continue
        if accepted is not None and bool(receipt.get("accepted")) is not accepted:
            continue
        total += 1
    return total


def _collect_runtime_binding_events(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for obs in observations:
        cid = str(obs.get("candidate_id") or "")
        req = obs.get("request") if isinstance(obs.get("request"), dict) else {}
        body_binding = req.get("body_runtime_binding")
        if _meaningful_runtime_binding(body_binding):
            events.append({
                "candidate_id": cid,
                "source": body_binding.get("source") or "request_body",
                "bound": bool(body_binding.get("bound")),
                "kind": "target_request_body",
            })
        for bucket_name in ("fixture_receipts", "cleanup_receipts"):
            for receipt in obs.get(bucket_name) or []:
                if not isinstance(receipt, dict):
                    continue
                binding = receipt.get("runtime_binding")
                if _meaningful_runtime_binding(binding):
                    events.append({
                        "candidate_id": cid,
                        "source": binding.get("source") or bucket_name,
                        "bound": bool(binding.get("bound")),
                        "kind": bucket_name,
                        "path": receipt.get("path"),
                    })
                body_binding = receipt.get("body_runtime_binding")
                if _meaningful_runtime_binding(body_binding):
                    events.append({
                        "candidate_id": cid,
                        "source": body_binding.get("source") or f"{bucket_name}_body",
                        "bound": bool(body_binding.get("bound")),
                        "kind": f"{bucket_name}_body",
                        "path": receipt.get("path"),
                    })
        for response in obs.get("responses") or []:
            if not isinstance(response, dict):
                continue
            binding = response.get("runtime_binding")
            if _meaningful_runtime_binding(binding):
                events.append({
                    "candidate_id": cid,
                    "source": binding.get("source") or "flow_response",
                    "bound": bool(binding.get("bound")),
                    "kind": "flow_response",
                    "step": response.get("step"),
                })
            body_binding = response.get("request_body_runtime_binding")
            if _meaningful_runtime_binding(body_binding):
                events.append({
                    "candidate_id": cid,
                    "source": body_binding.get("source") or "flow_step_body",
                    "bound": bool(body_binding.get("bound")),
                    "kind": "flow_step_body",
                    "step": response.get("step"),
                })
    return events


def _execution_failure_reasons(decisions: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for decision in decisions:
        if decision.get("decision") == "blocked":
            reason = str(decision.get("reason") or "blocked")
            reasons[reason] = reasons.get(reason, 0) + 1
    for obs in observations:
        verification = obs.get("verification") if isinstance(obs.get("verification"), dict) else {}
        verdict = str(verification.get("verdict") or "")
        if verdict in {"inconclusive", "needs_more_evidence"}:
            reason = str(verification.get("reason") or verdict)
            reasons[reason] = reasons.get(reason, 0) + 1
        response = obs.get("response") if isinstance(obs.get("response"), dict) else {}
        if response.get("error"):
            reason = str(response.get("error"))
            reasons[reason] = reasons.get(reason, 0) + 1
        for response in obs.get("responses") or []:
            if isinstance(response, dict) and response.get("error"):
                reason = str(response.get("error"))
                reasons[reason] = reasons.get(reason, 0) + 1
    return dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:20])


def _meaningful_runtime_binding(binding: Any) -> bool:
    if not isinstance(binding, dict) or not binding:
        return False
    if binding.get("bound") is True:
        return True
    return any(
        key in binding
        for key in (
            "bound",
            "source",
            "reason",
            "original",
            "rendered",
            "path_params",
            "response_id",
            "previous_values",
        )
    )


def _snapshot_status_code(snapshot: dict[str, Any]) -> int | None:
    if isinstance(snapshot.get("status_code"), int):
        return int(snapshot.get("status_code"))
    response = snapshot.get("response") if isinstance(snapshot.get("response"), dict) else {}
    if isinstance(response.get("status_code"), int):
        return int(response.get("status_code"))
    return None


