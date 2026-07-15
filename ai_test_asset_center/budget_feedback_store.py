from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .deployment_config_resolver import resolve_deployment_config
from .real_project_onboarding import ROOT, _safe_project_id


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_scope_id(value: str | None, default: str) -> str:
    raw = (value or default).strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")
    return safe or default


def _paths(project_id: str, deployment_scope_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    deployment = _safe_scope_id(deployment_scope_id, "default_deployment")
    return {
        "project_store": root / "platform_workspace" / project / "runtime_learning" / "budget_feedback_profiles.sqlite3",
        "deployment_store": root / "platform_workspace" / "_deployment_scopes" / deployment / "runtime_learning" / "budget_feedback_profiles.sqlite3",
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_feedback_profiles (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            deployment_mode TEXT NOT NULL,
            sync_mode TEXT NOT NULL,
            environment_class TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            profile_json TEXT NOT NULL,
            source_mode TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, environment_class)
        )
        """
    )
    return connection


def sanitize_budget_feedback_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = dict(summary or {})
    clean_by_tier: dict[str, dict[str, Any]] = {}
    for tier_name, bucket in dict(summary.get("by_tier", {}) or {}).items():
        tier = str(tier_name or "").upper()[:16]
        data = dict(bucket or {})
        clean_by_tier[tier] = {
            "reviewed_count": int(data.get("reviewed_count", 0) or 0),
            "confirmed_count": int(data.get("confirmed_count", 0) or 0),
            "falsified_count": int(data.get("falsified_count", 0) or 0),
            "hit_rate": float(data.get("hit_rate", 0.0) or 0.0),
        }
    return {
        "reviewed_count": int(summary.get("reviewed_count", 0) or 0),
        "confirmed_count": int(summary.get("confirmed_count", 0) or 0),
        "falsified_count": int(summary.get("falsified_count", 0) or 0),
        "hit_rate": float(summary.get("hit_rate", 0.0) or 0.0),
        "by_tier": clean_by_tier,
    }


def resolve_budget_learning_context(
    project_id: str | None = None,
    root: Path | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    deployment_config = resolve_deployment_config(project_id=project_id, root=root, overrides=policy_overrides)
    project = _safe_project_id(deployment_config.get("project_id") or "real_project_demo")
    deployment_mode = str(deployment_config.get("deployment_mode") or "private_deployment")
    sync_mode = str(deployment_config.get("learning_sync_mode") or "local_only")
    deployment_scope_id = _safe_scope_id(deployment_config.get("deployment_scope_id"), "default_deployment")
    environment_class = _safe_scope_id(deployment_config.get("environment_class"), "sandbox")
    policy_version = str(deployment_config.get("policy_version") or "v1.0.0-baseline")
    paths = _paths(project, deployment_scope_id, root)
    return {
        "project_id": project,
        "deployment_mode": deployment_mode,
        "learning_sync_mode": sync_mode,
        "deployment_scope_id": deployment_scope_id,
        "environment_class": environment_class,
        "policy_version": policy_version,
        "project_store_path": paths["project_store"],
        "deployment_store_path": paths["deployment_store"],
        "allow_project_write": True,
        "allow_deployment_read": sync_mode in {"import_only", "sanitized_export_import", "sanitized_api_sync", "customer_hub_sync"},
        "allow_deployment_write": sync_mode in {"sanitized_export_import", "sanitized_api_sync", "customer_hub_sync"},
        "external_sync_allowed": sync_mode in {"sanitized_export_import", "sanitized_api_sync", "customer_hub_sync"},
        "resolved_from": dict(deployment_config.get("_sources", {}) or {}),
    }


def _save_profile(path: Path, scope_type: str, scope_id: str, context: dict[str, Any], summary: dict[str, Any], source_mode: str) -> None:
    clean = sanitize_budget_feedback_summary(summary)
    with closing(_connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO budget_feedback_profiles(
                scope_type, scope_id, deployment_mode, sync_mode, environment_class,
                policy_version, sample_count, profile_json, source_mode, updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scope_type, scope_id, environment_class) DO UPDATE SET
                deployment_mode = excluded.deployment_mode,
                sync_mode = excluded.sync_mode,
                policy_version = excluded.policy_version,
                sample_count = excluded.sample_count,
                profile_json = excluded.profile_json,
                source_mode = excluded.source_mode,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                scope_type,
                scope_id,
                str(context.get("deployment_mode", "private_deployment")),
                str(context.get("learning_sync_mode", "local_only")),
                str(context.get("environment_class", "sandbox")),
                str(context.get("policy_version", "")),
                int(clean.get("reviewed_count", 0) or 0),
                json.dumps(clean, ensure_ascii=False, sort_keys=True),
                str(source_mode or "runtime_feedback")[:64],
                _now(),
            ),
        )
        connection.commit()


def persist_budget_feedback_profile(context: dict[str, Any], summary: dict[str, Any], source_mode: str = "runtime_feedback") -> dict[str, Any]:
    clean = sanitize_budget_feedback_summary(summary)
    writes: list[str] = []
    project_store = Path(context["project_store_path"])
    deployment_store = Path(context["deployment_store_path"])
    if context.get("allow_project_write", True):
        _save_profile(project_store, "project", str(context["project_id"]), context, clean, source_mode)
        writes.append("project")
    if context.get("allow_deployment_write", False):
        _save_profile(deployment_store, "deployment", str(context["deployment_scope_id"]), context, clean, source_mode)
        writes.append("deployment")
    return {"written_scopes": writes, "summary": clean}


def _load_profile(path: Path, scope_type: str, scope_id: str, environment_class: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with closing(_connect(path)) as connection:
        row = connection.execute(
            """
            SELECT profile_json, sample_count, updated_at_utc, source_mode, policy_version
            FROM budget_feedback_profiles
            WHERE scope_type = ? AND scope_id = ? AND environment_class = ?
            """,
            (scope_type, scope_id, environment_class),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["profile_json"] or "{}")
    except Exception:
        payload = {}
    payload["_sample_count"] = int(row["sample_count"] or 0)
    payload["_updated_at_utc"] = str(row["updated_at_utc"] or "")
    payload["_source_mode"] = str(row["source_mode"] or "")
    payload["_policy_version"] = str(row["policy_version"] or "")
    return payload


def merge_budget_feedback_profiles(project_summary: dict[str, Any] | None, deployment_summary: dict[str, Any] | None) -> dict[str, Any]:
    project_summary = sanitize_budget_feedback_summary(project_summary)
    deployment_summary = sanitize_budget_feedback_summary(deployment_summary)
    if not deployment_summary.get("reviewed_count", 0):
        return project_summary
    if not project_summary.get("reviewed_count", 0):
        return deployment_summary
    merged = {
        "reviewed_count": int(project_summary.get("reviewed_count", 0) or 0),
        "confirmed_count": int(project_summary.get("confirmed_count", 0) or 0),
        "falsified_count": int(project_summary.get("falsified_count", 0) or 0),
        "hit_rate": float(project_summary.get("hit_rate", 0.0) or 0.0),
        "by_tier": dict(project_summary.get("by_tier", {}) or {}),
    }
    deployment_weight = 0.35
    merged["hit_rate"] = (merged["hit_rate"] * (1.0 - deployment_weight)) + (
        float(deployment_summary.get("hit_rate", 0.0) or 0.0) * deployment_weight
    )
    for tier_name, dep_bucket in dict(deployment_summary.get("by_tier", {}) or {}).items():
        tier = str(tier_name or "").upper()[:16]
        local_bucket = dict(merged["by_tier"].get(tier, {}) or {})
        local_hit = float(local_bucket.get("hit_rate", 0.0) or 0.0)
        dep_hit = float(dep_bucket.get("hit_rate", 0.0) or 0.0)
        merged["by_tier"][tier] = {
            "reviewed_count": int(local_bucket.get("reviewed_count", 0) or 0),
            "confirmed_count": int(local_bucket.get("confirmed_count", 0) or 0),
            "falsified_count": int(local_bucket.get("falsified_count", 0) or 0),
            "hit_rate": (local_hit * (1.0 - deployment_weight)) + (dep_hit * deployment_weight),
        }
    return merged


def load_budget_feedback_profile(context: dict[str, Any]) -> dict[str, Any]:
    project_summary = _load_profile(
        Path(context["project_store_path"]),
        "project",
        str(context["project_id"]),
        str(context["environment_class"]),
    )
    deployment_summary = None
    if context.get("allow_deployment_read", False):
        deployment_summary = _load_profile(
            Path(context["deployment_store_path"]),
            "deployment",
            str(context["deployment_scope_id"]),
            str(context["environment_class"]),
        )
    return merge_budget_feedback_profiles(project_summary, deployment_summary)
