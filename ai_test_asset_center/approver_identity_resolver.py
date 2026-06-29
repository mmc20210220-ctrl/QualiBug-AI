from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_IDENTITY_SOURCES = {
    "local_config",
    "tenant_rbac",
    "sso_claims",
    "customer_hub",
    "api_token",
    "admin_override",
}
IDENTITY_SOURCE_PRIORITY = {
    "admin_override": 60,
    "customer_hub": 50,
    "sso_claims": 40,
    "tenant_rbac": 30,
    "api_token": 20,
    "local_config": 10,
    "": 0,
}


def _safe_project_id(value: Any) -> str:
    raw = str(value or "real_project_demo").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")
    return safe or "real_project_demo"


def _safe_scope_id(value: Any, default: str = "") -> str:
    raw = str(value or "").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")
    return safe or default


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


def _normalize_role_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("industry_role:"):
        text = text.split(":", 1)[1].strip().lower()
    return text


def _normalize_identity_source(value: Any, default: str = "") -> str:
    text = str(value or "").strip().lower()
    return text if text in ALLOWED_IDENTITY_SOURCES else default


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def approver_identity_paths(project_id: str, root: Path | None = None) -> dict[str, Path]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    input_dir = root / "platform_inputs" / project
    return {
        "input_dir": input_dir,
        "registry": input_dir / "approver_identity_registry.json",
        "project_members": input_dir / "approver_project_members.json",
        "tenant_rbac": input_dir / "approver_tenant_rbac.json",
        "sso_claims": input_dir / "approver_sso_claims.json",
    }


def save_approver_identity_registry(
    project_id: str,
    registry: dict[str, Any],
    root: Path | None = None,
) -> Path:
    paths = approver_identity_paths(project_id, root)
    return _write_json(paths["registry"], dict(registry or {}))


def load_approver_identity_registry(
    project_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    paths = approver_identity_paths(project_id, root)
    registry = _read_json(paths["registry"], {})
    if not isinstance(registry, dict):
        registry = {}

    project_members = _read_json(paths["project_members"], [])
    tenant_rbac = _read_json(paths["tenant_rbac"], [])
    sso_claims = _read_json(paths["sso_claims"], [])

    merged = dict(registry)
    if "project_members" not in merged and project_members:
        merged["project_members"] = project_members
    if "tenant_rbac" not in merged and tenant_rbac:
        merged["tenant_rbac"] = tenant_rbac
    if "sso_claims" not in merged and sso_claims:
        merged["sso_claims"] = sso_claims
    return merged


def _iter_registry_items(payload: Any, fallback_key: str = "") -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return [item for item in payload.get("items", []) if isinstance(item, dict)]
        if fallback_key and isinstance(payload.get(fallback_key), list):
            return [item for item in payload.get(fallback_key, []) if isinstance(item, dict)]
    return []


def _entry_actor_ids(entry: dict[str, Any]) -> set[str]:
    values = set()
    for key in ("actor_id", "user_id", "subject", "principal_id", "approver", "email"):
        text = str(entry.get(key) or "").strip()
        if text:
            values.add(text)
    for item in _normalize_text_list(entry.get("actor_ids") or entry.get("users") or entry.get("subjects")):
        values.add(item)
    return values


def _entry_roles(entry: dict[str, Any]) -> set[str]:
    values = set()
    for key in ("role", "approver_role"):
        role = _normalize_role_name(entry.get(key))
        if role:
            values.add(role)
    for item in _normalize_text_list(entry.get("roles")):
        role = _normalize_role_name(item)
        if role:
            values.add(role)
    return values


def _entry_projects(entry: dict[str, Any]) -> set[str]:
    values = set()
    for item in _normalize_text_list(
        entry.get("project_bindings")
        or entry.get("project_ids")
        or entry.get("projects")
        or entry.get("project_id")
    ):
        safe = _safe_project_id(item)
        if safe:
            values.add(safe)
    return values


def _entry_scopes(entry: dict[str, Any]) -> set[str]:
    values = set()
    for item in _normalize_text_list(
        entry.get("deployment_scope_bindings")
        or entry.get("scope_bindings")
        or entry.get("deployment_scope_ids")
        or entry.get("scope_ids")
        or entry.get("deployment_scope_id")
        or entry.get("scope_id")
        or entry.get("tenant_ids")
    ):
        safe = _safe_scope_id(item, "")
        if safe:
            values.add(safe)
    return values


def _entry_environments(entry: dict[str, Any]) -> set[str]:
    values = set()
    for item in _normalize_text_list(
        entry.get("environment_bindings")
        or entry.get("environment_classes")
        or entry.get("environment_class")
        or entry.get("env_scope")
    ):
        safe = _safe_scope_id(item, "").lower()
        if safe:
            values.add(safe)
    return values


def _merge_context(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("project_bindings", "deployment_scope_bindings", "environment_bindings", "matched_roles", "resolution_sources"):
        merged[key] = sorted(set(list(merged.get(key, []) or []) + list(extra.get(key, []) or [])))
    base_source = str(merged.get("identity_source") or "")
    extra_source = str(extra.get("identity_source") or "")
    if IDENTITY_SOURCE_PRIORITY.get(extra_source, 0) >= IDENTITY_SOURCE_PRIORITY.get(base_source, 0):
        merged["identity_source"] = extra_source or base_source
    else:
        merged["identity_source"] = base_source
    merged["actor_id"] = str(merged.get("actor_id") or extra.get("actor_id") or "")
    merged["provided"] = bool(
        merged.get("actor_id")
        or merged.get("identity_source")
        or merged.get("project_bindings")
        or merged.get("deployment_scope_bindings")
        or merged.get("environment_bindings")
    )
    return merged


def _match_identity_entries(
    items: list[dict[str, Any]],
    *,
    approver: str,
    approver_role: str,
    expected_project_id: str,
    expected_scope_id: str,
    expected_environment: str,
    default_identity_source: str,
) -> dict[str, Any]:
    role_name = _normalize_role_name(approver_role)
    merged: dict[str, Any] = {
        "actor_id": str(approver or "").strip(),
        "identity_source": "",
        "project_bindings": [],
        "deployment_scope_bindings": [],
        "environment_bindings": [],
        "matched_roles": [],
        "resolution_sources": [],
        "provided": False,
    }
    for entry in items:
        actor_ids = _entry_actor_ids(entry)
        if approver and actor_ids and approver not in actor_ids:
            continue
        entry_roles = _entry_roles(entry)
        if role_name and entry_roles and role_name not in entry_roles:
            continue

        projects = _entry_projects(entry)
        scopes = _entry_scopes(entry)
        environments = _entry_environments(entry)
        if expected_project_id and projects and expected_project_id not in projects:
            continue
        if expected_scope_id and scopes and expected_scope_id not in scopes:
            continue
        if expected_environment and environments and expected_environment not in environments:
            continue

        merged = _merge_context(
            merged,
            {
                "actor_id": str(approver or "").strip(),
                "identity_source": _normalize_identity_source(entry.get("identity_source"), default_identity_source),
                "project_bindings": list(projects),
                "deployment_scope_bindings": list(scopes),
                "environment_bindings": list(environments),
                "matched_roles": list(entry_roles),
                "resolution_sources": [default_identity_source],
            },
        )
    return merged


def _load_env_approver_context() -> dict[str, Any]:
    payload = os.environ.get("QUALIBUG_APPROVER_CONTEXT_JSON") or os.environ.get("QUALIBUG_APPROVER_SSO_CLAIMS_JSON") or ""
    if not payload.strip():
        return {}
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_approver_context(
    project_id: str,
    *,
    approver: str,
    approver_role: str,
    current_snapshot: dict[str, Any] | None = None,
    root: Path | None = None,
    explicit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = dict(current_snapshot or {})
    expected_project_id = _safe_project_id(snapshot.get("project_id") or project_id)
    expected_scope_id = _safe_scope_id(snapshot.get("deployment_scope_id"), "")
    expected_environment = _safe_scope_id(snapshot.get("environment_class"), "").lower()
    registry = load_approver_identity_registry(expected_project_id, root)

    resolved: dict[str, Any] = {
        "actor_id": str(approver or "").strip()[:120],
        "identity_source": "",
        "project_bindings": [],
        "deployment_scope_bindings": [],
        "environment_bindings": [],
        "matched_roles": [],
        "resolution_sources": [],
        "provided": False,
    }

    env_context = _load_env_approver_context()
    if env_context:
        resolved = _merge_context(
            resolved,
            {
                "actor_id": str(
                    env_context.get("actor_id")
                    or env_context.get("subject")
                    or approver
                    or ""
                ).strip()[:120],
                "identity_source": _normalize_identity_source(
                    env_context.get("identity_source") or env_context.get("source"),
                    "sso_claims",
                ),
                "project_bindings": [_safe_project_id(item) for item in _normalize_text_list(env_context.get("project_bindings") or env_context.get("project_ids"))],
                "deployment_scope_bindings": [_safe_scope_id(item, "") for item in _normalize_text_list(env_context.get("deployment_scope_bindings") or env_context.get("tenant_ids")) if _safe_scope_id(item, "")],
                "environment_bindings": [_safe_scope_id(item, "").lower() for item in _normalize_text_list(env_context.get("environment_bindings") or env_context.get("environment_classes") or env_context.get("env_scope")) if _safe_scope_id(item, "")],
                "matched_roles": [_normalize_role_name(item) for item in _normalize_text_list(env_context.get("roles") or env_context.get("role")) if _normalize_role_name(item)],
                "resolution_sources": ["env_claims_json"],
            },
        )

    resolved = _merge_context(
        resolved,
        _match_identity_entries(
            _iter_registry_items(registry.get("project_members"), "project_members"),
            approver=approver,
            approver_role=approver_role,
            expected_project_id=expected_project_id,
            expected_scope_id="",
            expected_environment=expected_environment,
            default_identity_source="local_config",
        ),
    )
    resolved = _merge_context(
        resolved,
        _match_identity_entries(
            _iter_registry_items(registry.get("tenant_rbac"), "tenant_rbac"),
            approver=approver,
            approver_role=approver_role,
            expected_project_id=expected_project_id,
            expected_scope_id=expected_scope_id,
            expected_environment=expected_environment,
            default_identity_source="tenant_rbac",
        ),
    )
    resolved = _merge_context(
        resolved,
        _match_identity_entries(
            _iter_registry_items(registry.get("sso_claims"), "sso_claims"),
            approver=approver,
            approver_role=approver_role,
            expected_project_id=expected_project_id,
            expected_scope_id=expected_scope_id,
            expected_environment=expected_environment,
            default_identity_source="sso_claims",
        ),
    )

    if explicit_context:
        resolved = _merge_context(
            resolved,
            {
                "actor_id": str(
                    explicit_context.get("actor_id")
                    or explicit_context.get("subject")
                    or approver
                    or ""
                ).strip()[:120],
                "identity_source": _normalize_identity_source(
                    explicit_context.get("identity_source") or explicit_context.get("source"),
                    resolved.get("identity_source") or "",
                ),
                "project_bindings": [_safe_project_id(item) for item in _normalize_text_list(explicit_context.get("project_bindings") or explicit_context.get("project_ids"))],
                "deployment_scope_bindings": [_safe_scope_id(item, "") for item in _normalize_text_list(explicit_context.get("deployment_scope_bindings") or explicit_context.get("scope_bindings")) if _safe_scope_id(item, "")],
                "environment_bindings": [_safe_scope_id(item, "").lower() for item in _normalize_text_list(explicit_context.get("environment_bindings") or explicit_context.get("environment_classes")) if _safe_scope_id(item, "")],
                "matched_roles": [_normalize_role_name(item) for item in _normalize_text_list(explicit_context.get("roles") or explicit_context.get("role")) if _normalize_role_name(item)],
                "resolution_sources": ["explicit_context"],
            },
        )

    resolved["project_bindings"] = [item for item in resolved.get("project_bindings", []) if item]
    resolved["deployment_scope_bindings"] = [item for item in resolved.get("deployment_scope_bindings", []) if item]
    resolved["environment_bindings"] = [item for item in resolved.get("environment_bindings", []) if item]
    resolved["matched_roles"] = [item for item in resolved.get("matched_roles", []) if item]
    resolved["resolution_sources"] = [item for item in resolved.get("resolution_sources", []) if item]
    resolved["provided"] = bool(
        resolved.get("actor_id")
        or resolved.get("identity_source")
        or resolved.get("project_bindings")
        or resolved.get("deployment_scope_bindings")
        or resolved.get("environment_bindings")
    )
    return resolved
