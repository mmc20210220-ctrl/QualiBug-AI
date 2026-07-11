from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException

from core.engine import Engine

app = FastAPI(title="QualiBug API")
engine = Engine()


def _expected_api_token() -> str:
    return os.environ.get("QUALIBUG_API_TOKEN", "").strip()


def _access_policy_raw() -> str:
    return os.environ.get("QUALIBUG_ACCESS_POLICY_JSON", "").strip()


def _jwt_identity_policy_raw() -> str:
    return os.environ.get("QUALIBUG_JWT_ACCESS_POLICY_JSON", "").strip()


def _workspace_root() -> Path:
    return Path(os.environ.get("QUALIBUG_WORKSPACE_ROOT", Path.cwd())).resolve()


def _extract_bearer_token(authorization: str | None) -> str:
    token = (authorization or "").strip()
    return token.split(" ", 1)[1].strip() if token.lower().startswith("bearer ") else token


def require_api_token(authorization: str | None = Header(None)) -> str:
    """Compatibility authentication for deployments without a policy."""
    expected = _expected_api_token()
    token = _extract_bearer_token(authorization)
    if not expected:
        raise HTTPException(status_code=503, detail="api token is not configured")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="authentication required")
    return token


def require_access(authorization: str | None, permission: str, *, project_id: str = "") -> Any | None:
    """Authorize by deployment policy; roles supplied by clients never grant access.

    The service accepts either an opaque-token policy or a verified JWT subject
    mapped through an identity policy. When neither policy is configured, the
    existing single-token deployment mode remains available for compatibility.
    """
    raw_token_policy = _access_policy_raw()
    raw_identity_policy = _jwt_identity_policy_raw()
    if not raw_token_policy and not raw_identity_policy:
        require_api_token(authorization)
        return None
    try:
        from ai_test_asset_center.enterprise_access_policy import (
            AccessPolicyError,
            authenticate_jwt_identity,
            authenticate_token,
            parse_identity_policy,
            parse_token_policy,
            require_authorized,
        )
        token_policy = parse_token_policy(raw_token_policy) if raw_token_policy else {}
        identity_policy = parse_identity_policy(raw_identity_policy) if raw_identity_policy else {}
    except AccessPolicyError as exc:
        raise HTTPException(status_code=503, detail=f"access policy is invalid: {str(exc)}") from exc
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    principal = authenticate_token(token, token_policy) if token_policy else None
    if principal is None and identity_policy:
        principal = authenticate_jwt_identity(token, identity_policy)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        return require_authorized(principal, permission=permission, project_id=project_id)
    except AccessPolicyError as exc:
        raise HTTPException(status_code=403, detail="permission denied") from exc


def _component(status: str, *, reason: str = "", checked_at: str = "") -> dict[str, Any]:
    return {"status": status, "reason": reason, "checked_at": checked_at}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(body: dict[str, Any], key: str, limit: int = 1_000_000) -> str:
    return str(body.get(key) or "").strip()[:limit]


def _actor(principal: Any | None) -> dict[str, str]:
    """Derive audit actor from authenticated identity, not request body fields."""
    principal_id = str(getattr(principal, "principal_id", "") or "").strip()
    if principal_id:
        return {"name": principal_id[:120], "role": ""}
    return {"name": "api_token_operator", "role": ""}


def _require_allowed_target(base_url: str) -> None:
    target = str(base_url or "").strip()
    if not target:
        return
    parsed = urlparse(target)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="target base_url must be an absolute http or https URL")
    allowed = {item.strip().rstrip("/").lower() for item in os.environ.get("QUALIBUG_ALLOWED_TARGET_ORIGINS", "").split(",") if item.strip()}
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    if not allowed:
        raise HTTPException(status_code=403, detail="target allowlist is not configured")
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="target origin is not in the configured allowlist")


@app.get("/run")
def run(q: str, authorization: str = Header(None)) -> dict[str, Any]:
    require_access(authorization, "engine.run")
    return engine.run(q, _extract_bearer_token(authorization))


@app.get("/replay")
def replay(q: str, authorization: str = Header(None)) -> dict[str, Any]:
    require_access(authorization, "engine.replay")
    return engine.replay(q, _extract_bearer_token(authorization))


@app.get("/metrics")
def metrics(authorization: str = Header(None)) -> dict[str, Any]:
    require_access(authorization, "engine.metrics.read")
    return engine.metrics


@app.get("/graph")
def graph(authorization: str = Header(None)) -> dict[str, Any]:
    require_access(authorization, "engine.graph.read")
    tenant = engine.auth.verify(_extract_bearer_token(authorization))
    return engine.graph.get(tenant, {})


@app.get("/logs")
def logs(authorization: str = Header(None)) -> list[dict[str, Any]]:
    require_access(authorization, "engine.logs.read")
    return engine.logs


@app.get("/v1/access/me")
def access_me(authorization: str = Header(None)) -> dict[str, Any]:
    principal = require_access(authorization, "identity.read")
    if principal is None:
        return {"mode": "legacy_token", "principal_id": "", "tenant_id": "", "permissions": [], "project_ids": []}
    return {
        "mode": "policy",
        "principal_id": str(principal.principal_id),
        "tenant_id": str(principal.tenant_id),
        "permissions": sorted(str(item) for item in principal.permissions),
        "project_ids": sorted(str(item) for item in principal.project_ids),
    }


@app.post("/v1/source-assets/register")
def register_source_asset_endpoint(body: dict[str, Any], authorization: str = Header(None)) -> dict[str, Any]:
    project_id = _text(body, "project_id", 160)
    principal = require_access(authorization, "source.register", project_id=project_id)
    try:
        from ai_test_asset_center.enterprise_source_registry import register_source_asset
        source_id = _text(body, "source_id", 160)
        source_type = _text(body, "source_type", 80)
        content = body.get("content")
        if not project_id or not source_id or not source_type or content in (None, ""):
            raise HTTPException(status_code=422, detail="project_id, source_id, source_type and content are required")
        return register_source_asset(
            project_id,
            source_id,
            content,
            source_type=source_type,
            root=_workspace_root(),
            actor=_actor(principal),
            origin=_text(body, "origin", 80) or "api_upload",
            filename=_text(body, "filename", 240),
            external_ref=_text(body, "external_ref", 500),
            metadata=_as_dict(body.get("metadata")),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"source registration failed: {type(exc).__name__}") from exc


@app.get("/v1/source-assets/{project_id}")
def list_source_assets_endpoint(project_id: str, authorization: str = Header(None)) -> dict[str, Any]:
    require_access(authorization, "source.read", project_id=project_id)
    try:
        from ai_test_asset_center.enterprise_source_registry import list_source_assets
        return {"project_id": project_id, "assets": list_source_assets(project_id, root=_workspace_root())}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"source asset lookup failed: {type(exc).__name__}") from exc


@app.post("/v1/test-data-receipts")
def issue_test_data_receipt_endpoint(body: dict[str, Any], authorization: str = Header(None)) -> dict[str, Any]:
    project_id = _text(body, "project_id", 160)
    principal = require_access(authorization, "test_data.receipt.issue", project_id=project_id)
    try:
        from ai_test_asset_center.enterprise_test_data_receipts import issue_test_data_receipt
        required = ("project_id", "kind", "campaign_id", "scope_id", "environment_ref")
        if any(not _text(body, key, 160) for key in required):
            raise HTTPException(status_code=422, detail="project_id, kind, campaign_id, scope_id and environment_ref are required")
        return issue_test_data_receipt(
            project_id,
            root=_workspace_root(),
            kind=_text(body, "kind", 40),
            campaign_id=_text(body, "campaign_id", 160),
            scope_id=_text(body, "scope_id", 160),
            environment_ref=_text(body, "environment_ref", 160),
            actor=_actor(principal),
            data_scope_ref=_text(body, "data_scope_ref", 240),
            fixture_ref=_text(body, "fixture_ref", 240),
            provenance_ref=_text(body, "provenance_ref", 240),
            operation_ref=_text(body, "operation_ref", 240),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"test-data receipt failed: {type(exc).__name__}") from exc


@app.post("/v1/execution-approvals")
def issue_execution_approval_endpoint(body: dict[str, Any], authorization: str = Header(None)) -> dict[str, Any]:
    project_id = _text(body, "project_id", 160)
    principal = require_access(authorization, "execution.approval.issue", project_id=project_id)
    try:
        from ai_test_asset_center.execution_approvals import issue_execution_approval
        required = ("project_id", "campaign_id", "scope_id", "environment_ref", "source_hash", "target_base_url", "expires_at_utc")
        if any(not _text(body, key, 500) for key in required):
            raise HTTPException(status_code=422, detail="project_id, campaign_id, scope_id, environment_ref, source_hash, target_base_url and expires_at_utc are required")
        _require_allowed_target(_text(body, "target_base_url", 500))
        return issue_execution_approval(
            project_id,
            root=_workspace_root(),
            campaign_id=_text(body, "campaign_id", 160),
            scope_id=_text(body, "scope_id", 160),
            environment_ref=_text(body, "environment_ref", 160),
            source_hash=_text(body, "source_hash", 128),
            target_base_url=_text(body, "target_base_url", 500),
            execution_mode=_text(body, "execution_mode", 80) or "safe_read_only",
            expires_at_utc=_text(body, "expires_at_utc", 80),
            actor=_actor(principal),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"execution approval failed: {type(exc).__name__}") from exc


@app.post("/v1/scans")
def run_enterprise_scan(body: dict[str, Any], authorization: str = Header(None)) -> dict[str, Any]:
    project_id = _text(body, "project_id", 160)
    if not project_id:
        raise HTTPException(status_code=422, detail="project_id is required")
    base_url = _text(body, "base_url", 500)
    principal = require_access(authorization, "campaign.execute" if base_url else "campaign.plan", project_id=project_id)
    _require_allowed_target(base_url)
    context = _as_dict(body.get("campaign_context"))
    for key in ("scope_id", "environment_ref", "environment_type", "source_manifest", "test_data_contract", "execution_approval_id", "execution_mode", "release_policy"):
        if key in body and key not in context:
            context[key] = body[key]
    context.setdefault("authenticated_principal", _actor(principal))
    try:
        from ai_test_asset_center.__main__ import scan
        result = scan(
            project=project_id,
            root=_workspace_root(),
            prd_text=_text(body, "prd_text"),
            api_doc_text=_text(body, "api_doc_text"),
            base_url=base_url,
            ci_gate=body.get("ci_gate") is True,
            multi_layer=body.get("multi_layer") is not False,
            save_report=body.get("save_report") is not False,
            campaign_context=context,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"enterprise scan failed: {type(exc).__name__}") from exc


@app.get("/v1/evidence-bundles/{project_id}/{bundle_id}/verify")
def verify_evidence_bundle_endpoint(project_id: str, bundle_id: str, authorization: str = Header(None)) -> dict[str, Any]:
    require_access(authorization, "evidence.verify", project_id=project_id)
    try:
        from ai_test_asset_center.evidence_artifact_store import verify_evidence_bundle
        return verify_evidence_bundle(project_id, bundle_id, root=_workspace_root())
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"evidence bundle verification failed: {type(exc).__name__}") from exc


@app.get("/health")
def health() -> dict[str, Any]:
    """Expose verified status, not optimistic configuration status."""
    static_token_configured = bool(_expected_api_token())
    token_policy_configured = bool(_access_policy_raw())
    jwt_identity_policy_configured = bool(_jwt_identity_policy_raw())
    policy_configured = token_policy_configured or jwt_identity_policy_configured
    auth_configured = static_token_configured or policy_configured
    allowlist_configured = bool(os.environ.get("QUALIBUG_ALLOWED_TARGET_ORIGINS", "").strip())
    return {
        "status": "healthy",
        "version": engine.version,
        "components": {
            "api": _component("healthy"),
            "authentication": _component("configured_unverified" if auth_configured else "not_configured", reason="token_or_identity_policy_presence_checked_only"),
            "access_policy": _component("configured_unverified" if policy_configured else "not_configured", reason="token_or_jwt_identity_policy_presence_checked_only"),
            "execution_engine": _component("configured_unverified", reason="runtime execution requires source, campaign, target and approval contracts"),
            "target_allowlist": _component("configured_unverified" if allowlist_configured else "not_configured", reason="configuration_presence_checked_only"),
            "llm": _component("not_configured", reason="no_provider_health_check_registered"),
            "external_services": _component("not_configured", reason="no_external_service_health_check_registered"),
        },
        "auth_configured": auth_configured,
        "access_policy_configured": policy_configured,
        "jwt_identity_policy_configured": jwt_identity_policy_configured,
        "target_allowlist_configured": allowlist_configured,
        "execution_source": "memory_simulation",
    }
