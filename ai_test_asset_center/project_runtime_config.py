"""Strict real-project configuration assembled over inward primitives."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .project_runtime_primitives import (
    PROJECT_ROOT,
    load_json_artifact,
    project_config_paths,
    safe_project_id,
)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _merge_connector_runtime_defaults(
    project_id: str,
    config: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    project = safe_project_id(project_id)
    saved = load_json_artifact(
        root
        / "platform_workspace"
        / project
        / "enterprise_pilot_runtime"
        / "connector_registry.json",
        {},
    )
    if not isinstance(saved, dict):
        raise TypeError("connector_registry_must_be_object")
    registry = {
        "project_id": project,
        "connectors": [
            dict(item)
            for item in saved.get("connectors", [])
            if isinstance(item, dict)
        ] if isinstance(saved.get("connectors"), list) else [],
        "test_profile": dict(saved.get("test_profile"))
        if isinstance(saved.get("test_profile"), dict)
        else {},
    }
    profile = (
        registry.get("test_profile")
        if isinstance(registry, dict)
        and isinstance(registry.get("test_profile"), dict)
        else {}
    )
    connectors = (
        registry.get("connectors")
        if isinstance(registry, dict)
        and isinstance(registry.get("connectors"), list)
        else []
    )
    primary_connector = next(
        (
            item
            for item in connectors
            if isinstance(item, dict) and item.get("enabled") is True
        ),
        None,
    )
    merged = dict(config)
    merged["base_url"] = _first_text(
        merged.get("base_url"),
        profile.get("api_base_url"),
        primary_connector.get("endpoint_ref")
        if isinstance(primary_connector, dict)
        else "",
    )
    merged["ui_base_url"] = _first_text(
        merged.get("ui_base_url"),
        profile.get("ui_base_url"),
    )
    for field in ("frontend_urls", "test_credentials", "database"):
        if not isinstance(merged.get(field), dict) and isinstance(
            profile.get(field),
            dict,
        ):
            merged[field] = dict(profile[field])
    merged["environment_ref"] = _first_text(
        merged.get("environment_ref"),
        profile.get("environment_ref"),
    )
    merged["deployment_scope_id"] = _first_text(
        merged.get("deployment_scope_id"),
        profile.get("scope_id"),
    )
    return merged


def load_real_project_config(
    project_id: str = "real_project_demo",
    root: Path | None = None,
) -> dict[str, Any]:
    workspace_root = Path(root or PROJECT_ROOT)
    paths = project_config_paths(project_id, workspace_root)
    raw = load_json_artifact(
        paths["input_dir"] / "real_project_config.json",
        {},
    )
    if not isinstance(raw, dict):
        raise TypeError("real_project_config_must_be_object")
    config = _merge_connector_runtime_defaults(project_id, raw, workspace_root)
    config.setdefault("project_id", safe_project_id(project_id))
    config.setdefault("project_name", config["project_id"])
    config.setdefault("base_url", "")
    config.setdefault("openapi_source", "json")
    config.setdefault("openapi_url", "")
    config.setdefault("discovery_mode", "safe")
    config.setdefault("auth_type", "password_login")
    # An absent declaration stays empty: fabricating "/auth/login" here made
    # consumers read an invented contract as if the operator had declared one
    # (and contradicted the credentials layer's own generic default). Login
    # path discovery belongs to each consumer's candidate-probe safety net.
    config.setdefault("login_api", "")
    config.setdefault("safe_mode", False)
    config.setdefault("allow_destructive_tests", False)
    config.setdefault("request_timeout_seconds", 10)
    config.setdefault("max_probe_count", 100)
    config.setdefault("deployment_mode", "private_deployment")
    config.setdefault("learning_sync_mode", "local_only")
    config.setdefault("deployment_scope_id", "")
    config.setdefault("environment_class", "sandbox")
    return config
