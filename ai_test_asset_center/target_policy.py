"""Single source of truth for target authorization and environment safety.

The decision is deliberately data-only.  Callers must supply an explicitly
declared environment type and the approved target URL; host names such as
``localhost`` never imply that a target is non-production.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

TARGET_POLICY_SCHEMA = "qualibug.target-policy-decision.v1"

NON_PRODUCTION_ENVIRONMENTS = frozenset(
    {
        "local",
        "dev",
        "development",
        "test",
        "qa",
        "sit",
        "uat",
        "staging",
        "stage",
        "sandbox",
        "pre-release",
        "pre_release",
        "prerelease",
        "preprod",
        "pre_prod",
        "preview",
        "system_test",
        "systest",
    }
)
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production", "live", "prd", "release"})
WRITE_EXECUTION_MODE = "approved_sandbox_write"
READ_ONLY_EXECUTION_MODE = "safe_read_only"


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_environment_type(value: Any) -> str:
    token = _text(value).lower().replace(" ", "_")
    aliases = {
        "pre-release": "pre_release",
        "prerelease": "pre_release",
        "pre-prod": "preprod",
        "pre_prod": "preprod",
        "system-test": "system_test",
    }
    return aliases.get(token, token)


def is_production_environment(value: Any) -> bool:
    token = normalize_environment_type(value)
    if not token:
        return False
    if token in PRODUCTION_ENVIRONMENTS:
        return True
    if token in NON_PRODUCTION_ENVIRONMENTS:
        return False
    return any(part in token for part in PRODUCTION_ENVIRONMENTS)


def is_nonproduction_environment(value: Any) -> bool:
    token = normalize_environment_type(value)
    return bool(token) and token in NON_PRODUCTION_ENVIRONMENTS and not is_production_environment(token)


def normalize_base_url(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 80 if scheme == "http" else 443
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    path = (parsed.path or "").rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def build_target_policy_decision(
    *,
    requested_base_url: Any,
    approved_base_url: Any,
    environment_type: Any,
    environment_ref: Any,
    execution_mode: Any,
    runtime_status: Any = "approved",
) -> dict[str, Any]:
    requested = normalize_base_url(requested_base_url)
    approved = normalize_base_url(approved_base_url)
    env_type = normalize_environment_type(environment_type)
    env_ref = _text(environment_ref)
    mode = _text(execution_mode) or READ_ONLY_EXECUTION_MODE
    runtime = _text(runtime_status).lower() or "blocked"

    blocking: list[str] = []
    if not requested:
        blocking.append("TARGET_URL_MISSING_OR_INVALID")
    if requested and not approved:
        blocking.append("TARGET_NOT_APPROVED")
    if requested and approved and requested != approved:
        blocking.append("TARGET_URL_APPROVAL_MISMATCH")
    if not env_ref:
        blocking.append("ENVIRONMENT_REFERENCE_MISSING")
    if not env_type:
        blocking.append("UNKNOWN_ENVIRONMENT")
    if runtime != "approved":
        blocking.append("RUNTIME_CONTRACT_NOT_APPROVED")

    exact_match = bool(requested and approved and requested == approved)
    read_allowed = bool(exact_match and env_ref and runtime == "approved")
    write_allowed = bool(
        read_allowed
        and mode == WRITE_EXECUTION_MODE
        and is_nonproduction_environment(env_type)
    )
    if mode == READ_ONLY_EXECUTION_MODE:
        blocking.append("READ_ONLY_MODE")
    elif mode != WRITE_EXECUTION_MODE:
        blocking.append("EXECUTION_MODE_UNSUPPORTED")
    if is_production_environment(env_type):
        blocking.append("PRODUCTION_WRITE_BLOCKED")
    elif env_type and not is_nonproduction_environment(env_type):
        blocking.append("UNKNOWN_ENVIRONMENT")

    blocking = sorted(set(blocking))
    hard_read_blocks = {
        "TARGET_URL_MISSING_OR_INVALID",
        "TARGET_NOT_APPROVED",
        "TARGET_URL_APPROVAL_MISMATCH",
        "ENVIRONMENT_REFERENCE_MISSING",
        "RUNTIME_CONTRACT_NOT_APPROVED",
    }
    status = "approved" if read_allowed and not hard_read_blocks.intersection(blocking) else "blocked"
    canonical = {
        "requested_base_url": requested,
        "approved_base_url": approved,
        "environment_type": env_type,
        "environment_ref": env_ref,
        "execution_mode": mode,
        "runtime_status": runtime,
        "read_allowed": read_allowed,
        "write_allowed": write_allowed,
        "blocking_codes": blocking,
    }
    decision_id = "tpd_" + hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema_version": TARGET_POLICY_SCHEMA,
        "decision_id": decision_id,
        "status": status,
        **canonical,
        "exact_target_match": exact_match,
        "authorization_basis": "explicit_environment_and_exact_target",
    }


def primary_write_block(decision: dict[str, Any]) -> str:
    """Return a stable fail-closed reason for the governed write executor."""
    codes = set(decision.get("blocking_codes") or [])
    priority = (
        "PRODUCTION_WRITE_BLOCKED",
        "UNKNOWN_ENVIRONMENT",
        "TARGET_URL_APPROVAL_MISMATCH",
        "TARGET_NOT_APPROVED",
        "TARGET_URL_MISSING_OR_INVALID",
        "ENVIRONMENT_REFERENCE_MISSING",
        "RUNTIME_CONTRACT_NOT_APPROVED",
        "READ_ONLY_MODE",
        "EXECUTION_MODE_UNSUPPORTED",
    )
    for code in priority:
        if code in codes:
            return code
    return "TARGET_POLICY_BLOCKED"
