from __future__ import annotations

import hashlib
import json
import os
import time
import calendar
from pathlib import Path
from typing import Any

from .policy_registry import get_policy_registry
from .policy_wiring import get_policy_dict
from .project_runtime_config import load_real_project_config
from .project_runtime_primitives import (
    PROJECT_ROOT as ROOT,
    safe_project_id as _safe_project_id,
)

ALLOWED_DEPLOYMENT_MODES = {"private_deployment", "public_saas", "dedicated_cloud"}
ALLOWED_SYNC_MODES = {
    "local_only",
    "import_only",
    "sanitized_export_import",
    "sanitized_api_sync",
    "customer_hub_sync",
}
APPROVER_IDENTITY_SOURCES = {
    "local_config",
    "tenant_rbac",
    "sso_claims",
    "customer_hub",
    "api_token",
    "admin_override",
}
APPROVER_ROLES = {
    "qa_lead",
    "security_owner",
    "project_owner",
    "tenant_admin",
    "testops_admin",
    "admin",
}
KEY_TO_ENV = {
    "project_id": "QUALIBUG_PROJECT_ID",
    "deployment_mode": "QUALIBUG_DEPLOYMENT_MODE",
    "learning_sync_mode": "QUALIBUG_LEARNING_SYNC_MODE",
    "deployment_scope_id": "QUALIBUG_DEPLOYMENT_SCOPE_ID",
    "environment_class": "QUALIBUG_ENVIRONMENT_CLASS",
}
DRIFT_HIGH_FIELDS = {"deployment_mode", "deployment_scope_id", "environment_class"}
DRIFT_MEDIUM_FIELDS = {"learning_sync_mode"}
DRIFT_LOW_FIELDS = {"policy_version"}
ALLOWED_UNLOCK_LEVELS = {"limited", "normal"}
PROD_ENVIRONMENT_CLASSES = {"prod", "prod_like", "production", "production_like"}


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _normalize_scope_id(value: Any, default: str) -> str:
    raw = str(value or "").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")
    return safe or default


def _normalize_role_name(value: Any, default: str = "qa_lead") -> str:
    text = str(value or "").strip().lower()
    if text.startswith("industry_role:"):
        text = text.split(":", 1)[1].strip().lower()
    return text or default


def _normalize_identity_source(value: Any, default: str = "") -> str:
    text = str(value or "").strip().lower()
    return text if text in APPROVER_IDENTITY_SOURCES else default


def _normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        text = str(value or "").strip()
        items = text.split(",") if text else []
    clean: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            clean.append(text)
    return clean


def _normalize_approver_context(
    approver_context: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(approver_context or {})
    project_bindings = [
        _safe_project_id(item)
        for item in _normalize_text_list(
            raw.get("project_bindings")
            or raw.get("project_ids")
            or raw.get("projects")
            or raw.get("project_binding")
        )
        if _safe_project_id(item)
    ]
    deployment_scope_bindings = [
        _normalize_scope_id(item, "")
        for item in _normalize_text_list(
            raw.get("deployment_scope_bindings")
            or raw.get("scope_bindings")
            or raw.get("deployment_scope_id")
            or raw.get("scope_id")
            or raw.get("scope_binding")
        )
        if _normalize_scope_id(item, "")
    ]
    environment_bindings = [
        _normalize_scope_id(item, "").lower()
        for item in _normalize_text_list(
            raw.get("environment_bindings")
            or raw.get("environment_classes")
            or raw.get("environment_class")
            or raw.get("environment_binding")
        )
        if _normalize_scope_id(item, "")
    ]
    identity_source = _normalize_identity_source(
        raw.get("identity_source") or raw.get("source"),
        "",
    )
    actor_id = str(
        raw.get("actor_id")
        or raw.get("subject")
        or raw.get("user_id")
        or raw.get("principal_id")
        or ""
    ).strip()[:120]
    return {
        "actor_id": actor_id,
        "identity_source": identity_source,
        "project_bindings": sorted(set(project_bindings)),
        "deployment_scope_bindings": sorted(set(deployment_scope_bindings)),
        "environment_bindings": sorted(set(environment_bindings)),
        "provided": bool(
            actor_id
            or identity_source
            or project_bindings
            or deployment_scope_bindings
            or environment_bindings
        ),
    }


def _read_env_values() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, env_name in KEY_TO_ENV.items():
        raw = os.environ.get(env_name)
        if raw is not None and str(raw).strip() != "":
            values[key] = raw
    return values


def resolve_deployment_config(
    project_id: str | None = None,
    root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve deployment config from overrides > env > project config > policy > defaults."""
    root = root or ROOT
    overrides = dict(overrides or {})
    requested_project = _safe_project_id(project_id or overrides.get("project_id") or os.environ.get("QUALIBUG_PROJECT_ID"))

    active_record = get_policy_registry().get_active()
    policy_values = dict(get_policy_dict("execution") or {})
    project_cfg = load_real_project_config(requested_project, root)
    env_values = _read_env_values()

    defaults = {
        "project_id": requested_project or "real_project_demo",
        "deployment_mode": "private_deployment",
        "learning_sync_mode": "local_only",
        "deployment_scope_id": "",
        "environment_class": "sandbox",
    }
    precedence = [defaults, policy_values, project_cfg, env_values, overrides]

    resolved: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key in defaults:
        value = None
        source = "default"
        for layer_name, layer in zip(
            ["default", "policy", "project_config", "env", "override"],
            precedence,
        ):
            if key in layer and layer.get(key) not in (None, ""):
                value = layer.get(key)
                source = layer_name
        resolved[key] = value
        sources[key] = source

    resolved["project_id"] = _safe_project_id(resolved.get("project_id") or requested_project or "real_project_demo")
    resolved["deployment_mode"] = _normalize_choice(
        resolved.get("deployment_mode"),
        ALLOWED_DEPLOYMENT_MODES,
        "private_deployment",
    )
    resolved["learning_sync_mode"] = _normalize_choice(
        resolved.get("learning_sync_mode"),
        ALLOWED_SYNC_MODES,
        "local_only",
    )
    resolved["environment_class"] = _normalize_scope_id(resolved.get("environment_class"), "sandbox").lower()
    default_scope = "public_default" if resolved["deployment_mode"] == "public_saas" else "private_default"
    resolved["deployment_scope_id"] = _normalize_scope_id(resolved.get("deployment_scope_id"), default_scope)
    resolved["policy_version"] = str(active_record.policy_version if active_record else "v1.0.0-baseline")
    resolved["_sources"] = sources
    return resolved


def build_deployment_config_snapshot(resolved: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(resolved or {})
    sources = dict(resolved.get("_sources", {}) or resolved.get("sources", {}) or {})
    return {
        "project_id": str(resolved.get("project_id") or "real_project_demo"),
        "deployment_mode": str(resolved.get("deployment_mode") or "private_deployment"),
        "learning_sync_mode": str(resolved.get("learning_sync_mode") or "local_only"),
        "deployment_scope_id": str(resolved.get("deployment_scope_id") or ""),
        "environment_class": str(resolved.get("environment_class") or "sandbox"),
        "policy_version": str(resolved.get("policy_version") or "v1.0.0-baseline"),
        "sources": sources,
    }


def _snapshot_fingerprint(snapshot: dict[str, Any] | None) -> str:
    payload = build_deployment_config_snapshot(snapshot)
    stable = json.dumps(
        {
            "project_id": payload.get("project_id"),
            "deployment_mode": payload.get("deployment_mode"),
            "learning_sync_mode": payload.get("learning_sync_mode"),
            "deployment_scope_id": payload.get("deployment_scope_id"),
            "environment_class": payload.get("environment_class"),
            "policy_version": payload.get("policy_version"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_utc(value: str | None) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return 0.0


def deployment_config_snapshot_path(project_id: str, root: Path | None = None) -> Path:
    root = root or ROOT
    project = _safe_project_id(project_id or "real_project_demo")
    return root / "platform_workspace" / project / "runtime_learning" / "deployment_config_snapshot.json"


def deployment_drift_approval_path(project_id: str, root: Path | None = None) -> Path:
    root = root or ROOT
    project = _safe_project_id(project_id or "real_project_demo")
    return root / "platform_workspace" / project / "runtime_learning" / "deployment_drift_approval.json"


def load_deployment_config_snapshot(project_id: str, root: Path | None = None) -> dict[str, Any] | None:
    path = deployment_config_snapshot_path(project_id, root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def persist_deployment_config_snapshot(snapshot: dict[str, Any], root: Path | None = None) -> Path:
    path = deployment_config_snapshot_path(str((snapshot or {}).get("project_id") or "real_project_demo"), root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(snapshot or {}), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def required_deployment_drift_roles(
    current_snapshot: dict[str, Any] | None,
    drift_summary: dict[str, Any] | None,
) -> list[str]:
    snapshot = build_deployment_config_snapshot(current_snapshot)
    drift_summary = dict(drift_summary or {})
    severity = str(drift_summary.get("severity") or "low").lower()
    environment_class = str(snapshot.get("environment_class") or "sandbox").lower()
    deployment_mode = str(snapshot.get("deployment_mode") or "private_deployment").lower()

    if severity == "high" or environment_class in {"prod", "prod_like", "production", "production_like"} or deployment_mode == "public_saas":
        return ["security_owner", "testops_admin", "admin"]
    if severity == "medium" or deployment_mode == "dedicated_cloud":
        return ["qa_lead", "security_owner", "project_owner", "testops_admin", "admin"]
    return ["qa_lead", "project_owner", "tenant_admin", "security_owner", "testops_admin", "admin"]


def required_deployment_drift_context(
    current_snapshot: dict[str, Any] | None,
    drift_summary: dict[str, Any] | None,
    *,
    approver_role: str,
) -> dict[str, Any]:
    snapshot = build_deployment_config_snapshot(current_snapshot)
    drift_summary = dict(drift_summary or {})
    role_name = _normalize_role_name(approver_role, "qa_lead")
    severity = str(drift_summary.get("severity") or "low").lower()
    environment_class = str(snapshot.get("environment_class") or "sandbox").lower()
    deployment_mode = str(snapshot.get("deployment_mode") or "private_deployment").lower()
    high_risk_context = (
        severity == "high"
        or environment_class in PROD_ENVIRONMENT_CLASSES
        or deployment_mode == "public_saas"
    )

    require_project_binding = role_name in {"qa_lead", "project_owner", "tenant_admin"}
    require_scope_binding = role_name == "tenant_admin"
    require_environment_binding = role_name in {"qa_lead", "project_owner", "tenant_admin"}
    trusted_identity_sources = [
        "admin_override",
        "customer_hub",
        "sso_claims",
        "tenant_rbac",
    ]
    if not high_risk_context:
        trusted_identity_sources.extend(["local_config", "api_token"])

    return {
        "approver_role": role_name,
        "requires_context": require_project_binding or require_scope_binding or require_environment_binding,
        "require_project_binding": require_project_binding,
        "require_scope_binding": require_scope_binding,
        "require_environment_binding": require_environment_binding,
        "expected_project_id": str(snapshot.get("project_id") or ""),
        "expected_deployment_scope_id": str(snapshot.get("deployment_scope_id") or ""),
        "expected_environment_class": environment_class,
        "trusted_identity_sources": trusted_identity_sources,
        "high_risk_context": high_risk_context,
    }


def validate_deployment_drift_approval(
    current_snapshot: dict[str, Any] | None,
    drift_summary: dict[str, Any] | None,
    *,
    approver: str,
    approver_role: str,
    approver_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = build_deployment_config_snapshot(current_snapshot)
    role_name = _normalize_role_name(approver_role, "qa_lead")
    required_roles = required_deployment_drift_roles(current_snapshot, drift_summary)
    role_allowed = role_name in required_roles
    normalized_context = _normalize_approver_context(approver_context)
    requirements = required_deployment_drift_context(
        snapshot,
        drift_summary,
        approver_role=role_name,
    )
    expected_project_id = str(requirements.get("expected_project_id") or "")
    expected_scope_id = str(requirements.get("expected_deployment_scope_id") or "")
    expected_environment = str(requirements.get("expected_environment_class") or "")
    project_matches = (
        not bool(requirements.get("require_project_binding"))
        or expected_project_id in set(normalized_context.get("project_bindings") or [])
    )
    scope_matches = (
        not bool(requirements.get("require_scope_binding"))
        or expected_scope_id in set(normalized_context.get("deployment_scope_bindings") or [])
    )
    environment_matches = (
        not bool(requirements.get("require_environment_binding"))
        or expected_environment in set(normalized_context.get("environment_bindings") or [])
    )
    identity_source = str(normalized_context.get("identity_source") or "")
    trusted_identity_sources = set(requirements.get("trusted_identity_sources") or [])
    identity_source_allowed = not identity_source or identity_source in trusted_identity_sources
    context_reasons: list[str] = []
    context_allowed = True
    if requirements.get("requires_context") and not normalized_context.get("provided"):
        context_allowed = False
        context_reasons.append("当前审批角色需要绑定项目/租户/环境上下文")
    if not project_matches:
        context_allowed = False
        context_reasons.append(f"审批上下文未绑定当前项目: {expected_project_id}")
    if not scope_matches:
        context_allowed = False
        context_reasons.append(f"审批上下文未绑定当前部署作用域: {expected_scope_id}")
    if not environment_matches:
        context_allowed = False
        context_reasons.append(f"审批上下文未绑定当前环境级别: {expected_environment}")
    if not identity_source_allowed:
        context_allowed = False
        context_reasons.append(
            "当前审批身份来源不在可信列表: " + ", ".join(sorted(trusted_identity_sources))
        )
    overall_allowed = role_allowed and context_allowed
    return {
        "approver": str(approver or "admin")[:120],
        "approver_role": role_name,
        "required_roles": required_roles,
        "role_allowed": role_allowed,
        "approval_requirements": requirements,
        "approver_context": normalized_context,
        "context_allowed": context_allowed,
        "overall_allowed": overall_allowed,
        "project_matches": project_matches,
        "scope_matches": scope_matches,
        "environment_matches": environment_matches,
        "identity_source_allowed": identity_source_allowed,
        "context_reasons": context_reasons,
        "reason": (
            "审批角色与上下文均满足当前部署漂移要求"
            if overall_allowed
            else (
                f"当前漂移要求角色之一: {', '.join(required_roles)}"
                if not role_allowed
                else "; ".join(context_reasons)
            )
        ),
    }


def approve_deployment_config_drift(
    snapshot: dict[str, Any] | None,
    *,
    approver: str = "admin",
    approver_role: str = "admin",
    approver_context: dict[str, Any] | None = None,
    unlock_level: str = "limited",
    ttl_hours: int = 24,
    comment: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    payload = build_deployment_config_snapshot(snapshot)
    unlock = str(unlock_level or "limited").strip().lower()
    if unlock not in ALLOWED_UNLOCK_LEVELS:
        unlock = "limited"
    approved_at = _now_utc()
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + max(1, int(ttl_hours or 1)) * 3600))
    record = {
        "project_id": payload.get("project_id"),
        "snapshot_fingerprint": _snapshot_fingerprint(payload),
        "approver": str(approver or "admin")[:120],
        "approver_role": _normalize_role_name(approver_role, "admin"),
        "approver_context": _normalize_approver_context(approver_context),
        "approved_at_utc": approved_at,
        "expires_at_utc": expires_at,
        "unlock_level": unlock,
        "comment": str(comment or "")[:500],
        "snapshot": payload,
    }
    path = deployment_drift_approval_path(str(payload.get("project_id") or "real_project_demo"), root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def load_deployment_drift_approval(project_id: str, root: Path | None = None) -> dict[str, Any] | None:
    path = deployment_drift_approval_path(project_id, root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def detect_deployment_config_drift(
    current_snapshot: dict[str, Any] | None,
    previous_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    current_snapshot = build_deployment_config_snapshot(current_snapshot)
    if not previous_snapshot:
        return {
            "status": "first_seen",
            "severity": "none",
            "changed_fields": [],
            "value_changes": {},
            "source_changes": {},
            "requires_attention": False,
        }

    previous = build_deployment_config_snapshot(previous_snapshot)
    changed_fields: list[str] = []
    value_changes: dict[str, dict[str, Any]] = {}
    for field in ("deployment_mode", "learning_sync_mode", "deployment_scope_id", "environment_class", "policy_version"):
        old_value = previous.get(field)
        new_value = current_snapshot.get(field)
        if old_value != new_value:
            changed_fields.append(field)
            value_changes[field] = {"old": old_value, "new": new_value}

    source_changes: dict[str, dict[str, Any]] = {}
    all_source_fields = set(dict(previous.get("sources", {}) or {})) | set(dict(current_snapshot.get("sources", {}) or {}))
    for field in sorted(all_source_fields):
        old_source = dict(previous.get("sources", {}) or {}).get(field)
        new_source = dict(current_snapshot.get("sources", {}) or {}).get(field)
        if old_source != new_source:
            source_changes[field] = {"old": old_source, "new": new_source}

    if not changed_fields and not source_changes:
        return {
            "status": "stable",
            "severity": "none",
            "changed_fields": [],
            "value_changes": {},
            "source_changes": {},
            "requires_attention": False,
        }

    severity = "low"
    if any(field in DRIFT_HIGH_FIELDS for field in changed_fields):
        severity = "high"
    elif any(field in DRIFT_MEDIUM_FIELDS for field in changed_fields):
        severity = "medium"
    elif any(field in DRIFT_LOW_FIELDS for field in changed_fields) or source_changes:
        severity = "low"

    return {
        "status": "drifted",
        "severity": severity,
        "changed_fields": changed_fields,
        "value_changes": value_changes,
        "source_changes": source_changes,
        "requires_attention": severity in {"high", "medium"},
    }


def evaluate_deployment_drift_unlock(
    current_snapshot: dict[str, Any] | None,
    drift_summary: dict[str, Any] | None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    snapshot = build_deployment_config_snapshot(current_snapshot)
    drift_summary = dict(drift_summary or {})
    drift_status = str(drift_summary.get("status") or "")
    if drift_status == "first_seen":
        return {
            "status": "bootstrapping",
            "effective_unlock_level": "normal",
            "requested_unlock_level": "normal",
            "requires_approval": False,
            "matched": True,
            "expired": False,
            "snapshot_fingerprint": _snapshot_fingerprint(snapshot),
            "approval": {},
        }
    if drift_status != "drifted":
        return {
            "status": "not_required",
            "effective_unlock_level": "normal",
            "requested_unlock_level": "normal",
            "requires_approval": False,
            "matched": True,
            "expired": False,
            "snapshot_fingerprint": _snapshot_fingerprint(snapshot),
            "approval": {},
        }

    approval = load_deployment_drift_approval(str(snapshot.get("project_id") or "real_project_demo"), root) or {}
    fingerprint = _snapshot_fingerprint(snapshot)
    expires_at = _parse_utc(str(approval.get("expires_at_utc") or ""))
    now_ts = time.time()
    expired = bool(expires_at and expires_at < now_ts)
    matched = str(approval.get("snapshot_fingerprint") or "") == fingerprint
    requested_unlock_level = str(approval.get("unlock_level") or "limited").strip().lower() or "limited"
    if requested_unlock_level not in ALLOWED_UNLOCK_LEVELS:
        requested_unlock_level = "limited"
    validation = validate_deployment_drift_approval(
        snapshot,
        drift_summary,
        approver=str(approval.get("approver") or "admin"),
        approver_role=str(approval.get("approver_role") or "qa_lead"),
        approver_context=dict(approval.get("approver_context") or {}),
    )

    if not approval or not matched:
        return {
            "status": "unapproved",
            "effective_unlock_level": "restricted",
            "requested_unlock_level": requested_unlock_level,
            "requires_approval": True,
            "matched": matched,
            "expired": False,
            "snapshot_fingerprint": fingerprint,
            "approval": approval,
            "approval_validation": validation,
        }
    if expired:
        return {
            "status": "expired",
            "effective_unlock_level": "restricted",
            "requested_unlock_level": requested_unlock_level,
            "requires_approval": True,
            "matched": True,
            "expired": True,
            "snapshot_fingerprint": fingerprint,
            "approval": approval,
            "approval_validation": validation,
        }
    if not bool(validation.get("role_allowed")):
        return {
            "status": "role_rejected",
            "effective_unlock_level": "restricted",
            "requested_unlock_level": requested_unlock_level,
            "requires_approval": True,
            "matched": True,
            "expired": False,
            "snapshot_fingerprint": fingerprint,
            "approval": approval,
            "approval_validation": validation,
        }
    if not bool(validation.get("context_allowed")):
        return {
            "status": "context_rejected",
            "effective_unlock_level": "restricted",
            "requested_unlock_level": requested_unlock_level,
            "requires_approval": True,
            "matched": True,
            "expired": False,
            "snapshot_fingerprint": fingerprint,
            "approval": approval,
            "approval_validation": validation,
        }

    severity = str(drift_summary.get("severity") or "low").lower()
    effective_unlock_level = requested_unlock_level
    approval_status = "approved_normal"
    if severity == "high":
        effective_unlock_level = "limited"
        approval_status = "approved_limited"
    elif requested_unlock_level == "limited":
        approval_status = "approved_limited"

    return {
        "status": approval_status,
        "effective_unlock_level": effective_unlock_level,
        "requested_unlock_level": requested_unlock_level,
        "requires_approval": False,
        "matched": True,
        "expired": False,
        "snapshot_fingerprint": fingerprint,
        "approval": approval,
        "approval_validation": validation,
    }
