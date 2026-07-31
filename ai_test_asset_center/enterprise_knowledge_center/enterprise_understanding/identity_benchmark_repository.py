"""Durable project-scoped inputs for enterprise identity measurement.

This module owns storage only. It does not resolve identities, evaluate quality or
rebuild the knowledge asset. Composition loads the persisted inputs before the
single understanding authority runs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .._common import ROOT, _load_json, _safe_project_id, _write_json
from .._utils import _now, _paths
from .identity_benchmark import GROUND_TRUTH_SCHEMA, QUALITY_POLICY_SCHEMA
from .schema import as_dict, as_list, text

AUDIT_SCHEMA = "qualibug.enterprise-identity-benchmark-audit.v1"
GROUND_TRUTH_FILENAME = "enterprise_identity_ground_truth.json"
QUALITY_POLICY_FILENAME = "enterprise_identity_quality_policy.json"
AUDIT_FILENAME = "enterprise_identity_benchmark_audit.json"


def _workspace(project_id: str, root: Path | None = None) -> tuple[str, Path]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    workspace = Path(_paths(project, resolved_root)["workspace"])
    return project, workspace


def identity_benchmark_paths(
    project_id: str, root: Path | None = None
) -> dict[str, Path]:
    _project, workspace = _workspace(project_id, root)
    return {
        "ground_truth": workspace / GROUND_TRUTH_FILENAME,
        "quality_policy": workspace / QUALITY_POLICY_FILENAME,
        "audit": workspace / AUDIT_FILENAME,
    }


def _load(path: Path) -> dict[str, Any]:
    loaded = _load_json(path, {})
    return dict(loaded) if isinstance(loaded, dict) else {}


def load_identity_ground_truth(
    project_id: str, root: Path | None = None
) -> dict[str, Any]:
    return _load(identity_benchmark_paths(project_id, root)["ground_truth"])


def load_identity_quality_policy(
    project_id: str, root: Path | None = None
) -> dict[str, Any]:
    return _load(identity_benchmark_paths(project_id, root)["quality_policy"])


def load_identity_benchmark_audit(
    project_id: str, root: Path | None = None
) -> dict[str, Any]:
    project, _workspace_path = _workspace(project_id, root)
    loaded = _load(identity_benchmark_paths(project, root)["audit"])
    events = [dict(row) for row in as_list(loaded.get("events")) if isinstance(row, dict)]
    return {
        "schema": AUDIT_SCHEMA,
        "project_id": project,
        "updated_at_utc": text(loaded.get("updated_at_utc")),
        "events": events,
    }


def save_identity_ground_truth(
    project_id: str,
    payload: dict[str, Any],
    root: Path | None = None,
) -> Path:
    if text(payload.get("schema")) != GROUND_TRUTH_SCHEMA:
        raise ValueError("identity_ground_truth_schema_invalid")
    path = identity_benchmark_paths(project_id, root)["ground_truth"]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, dict(payload))
    return path


def save_identity_quality_policy(
    project_id: str,
    payload: dict[str, Any],
    root: Path | None = None,
) -> Path:
    if text(payload.get("schema")) != QUALITY_POLICY_SCHEMA:
        raise ValueError("identity_quality_policy_schema_invalid")
    path = identity_benchmark_paths(project_id, root)["quality_policy"]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, dict(payload))
    return path


def snapshot_identity_benchmark_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def restore_identity_benchmark_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".rollback.tmp")
    temporary.write_bytes(snapshot)
    temporary.replace(path)


def payload_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def append_identity_benchmark_audit(
    project_id: str,
    event: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    project, _workspace_path = _workspace(project_id, root)
    path = identity_benchmark_paths(project, root)["audit"]
    ledger = load_identity_benchmark_audit(project, root)
    row = {
        "event_id": hashlib.sha256(
            json.dumps(
                {
                    "project_id": project,
                    "at_utc": _now(),
                    "event": event,
                    "prior_count": len(as_list(ledger.get("events"))),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:24],
        "at_utc": _now(),
        **dict(event),
    }
    ledger["events"] = [*as_list(ledger.get("events")), row]
    ledger["updated_at_utc"] = row["at_utc"]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, ledger)
    return row


def apply_identity_benchmark_repository(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Inject durable benchmark inputs before enterprise understanding runs."""
    ground_truth = load_identity_ground_truth(project_id, root)
    quality_policy = load_identity_quality_policy(project_id, root)
    if ground_truth:
        asset["enterprise_identity_ground_truth"] = ground_truth
    else:
        asset.pop("enterprise_identity_ground_truth", None)
    if quality_policy:
        asset["enterprise_identity_quality_policy"] = quality_policy
    else:
        asset.pop("enterprise_identity_quality_policy", None)
    asset["enterprise_identity_benchmark_repository_receipt"] = {
        "schema": "qualibug.enterprise-identity-benchmark-repository-receipt.v1",
        "ground_truth_loaded": bool(ground_truth),
        "quality_policy_loaded": bool(quality_policy),
        "ground_truth_fingerprint": payload_fingerprint(ground_truth) if ground_truth else "",
        "quality_policy_fingerprint": payload_fingerprint(quality_policy) if quality_policy else "",
        "storage_scope": "PROJECT_WORKSPACE",
    }
    return asset


__all__ = [
    "AUDIT_SCHEMA",
    "append_identity_benchmark_audit",
    "apply_identity_benchmark_repository",
    "identity_benchmark_paths",
    "load_identity_benchmark_audit",
    "load_identity_ground_truth",
    "load_identity_quality_policy",
    "payload_fingerprint",
    "restore_identity_benchmark_file",
    "save_identity_ground_truth",
    "save_identity_quality_policy",
    "snapshot_identity_benchmark_file",
]
