"""Read-only inventory for historical authorization artifacts across projects.

The inventory never rewrites scan results, attempt ledgers, Gate receipts, findings,
or canonical registries. It audits one immutable byte snapshot per artifact, invokes
the existing quarantine and migration authorities, and emits only content-addressed
remediation metadata.

Occurrences are deduplicated by attempt-ledger fingerprint plus finding ID, so copies
of one run in scan_result.json and v12_report.json cannot inflate rerun counts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .historical_authorization_artifact_migration import (
    HistoricalAuthorizationArtifactMigrationError,
    migrate_historical_authorization_scan_result,
    validate_historical_authorization_artifact_migration_receipt,
)
from .historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
    build_historical_authorization_quarantine_projection,
    validate_historical_authorization_quarantine_projection,
)
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)
from .private_pilot_json_io import _write_json_object_atomic


INVENTORY_SCHEMA = "qualibug.historical-authorization-inventory.v1"
DEFAULT_REPORT_RELATIVE_PATH = (
    Path("platform_outputs") / "historical_authorization_inventory.json"
)
_ARTIFACT_NAMES = (
    "scan_result.json",
    "v12_report.json",
    "intelligence_report.json",
)
_PROJECT_STATUSES = (
    "CLEAR",
    "QUARANTINED",
    "REBUILD_BLOCKED",
    "UNVERIFIABLE",
    "CONTRADICTION",
)
_ARTIFACT_STATUSES = _PROJECT_STATUSES + ("INVALID_ARTIFACT",)
_MIGRATION_STATUSES = {
    "NOT_AVAILABLE",
    "NOT_REQUIRED",
    "FAILED",
    "MIGRATED",
    "REBUILD_BLOCKED",
    "MISSING",
}
_ARTIFACT_FIELDS = {
    "artifact_path",
    "artifact_sha256",
    "artifact_size_bytes",
    "artifact_mtime_utc",
    "authority_scope_id",
    "run_id",
    "campaign_id",
    "attempt_ledger_fingerprint",
    "status",
    "reason",
    "quarantine_count",
    "quarantined_finding_ids",
    "rerun_required_count",
    "manual_recompile_required_count",
    "rerun_queue",
    "migration_status",
    "registry_rebuild_reason",
    "rebuilt_registry_fingerprint",
    "rebuilt_delivery_occurrence_count",
    "source_evidence_rewritten",
}
_PROJECT_FIELDS = {
    "project_id",
    "status",
    "artifact_count",
    "authority_scope_count",
    "quarantine_occurrence_count",
    "quarantined_finding_ids",
    "rerun_required_count",
    "manual_recompile_required_count",
    "registry_rebuilt_scope_count",
    "registry_rebuild_blocked_scope_count",
    "unverifiable_artifact_count",
    "contradiction_artifact_count",
    "invalid_artifact_count",
    "rerun_queue",
    "artifacts",
}
_REPORT_FIELDS = {
    "schema_version",
    "generated_at_utc",
    "root",
    "source_roots",
    "requested_projects",
    "missing_projects",
    "status",
    "project_count",
    "artifact_count",
    "authority_scope_count",
    "projects_with_quarantine_count",
    "projects_with_rebuild_blocked_count",
    "projects_unverifiable_count",
    "projects_with_contradiction_count",
    "quarantine_occurrence_count",
    "rerun_required_count",
    "manual_recompile_required_count",
    "project_status_counts",
    "artifact_status_counts",
    "source_artifacts_modified",
    "projects",
    "inventory_fingerprint",
}


class HistoricalAuthorizationInventoryError(ValueError):
    """The inventory report is malformed or internally inconsistent."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mtime_utc_ns(value: int) -> str:
    return datetime.fromtimestamp(
        value / 1_000_000_000,
        timezone.utc,
    ).isoformat().replace("+00:00", "Z")


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _payload_authorities(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    row = _dict(payload)
    nested = _dict(row.get("v12"))
    return (
        _dict(row.get("mainline_run") or nested.get("mainline_run")),
        _dict(
            row.get("obligation_attempt_ledger")
            or nested.get("obligation_attempt_ledger")
        ),
        _dict(
            row.get("canonical_defect_registry")
            or nested.get("canonical_defect_registry")
        ),
    )


def _artifact_snapshot(
    path: Path,
    *,
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Read and parse exactly the bytes whose SHA is placed in the report."""
    fallback_id = _fingerprint(str(path.resolve()))
    metadata = {
        "artifact_path": _relative_path(path, root),
        "artifact_sha256": fallback_id,
        "artifact_size_bytes": 0,
        "artifact_mtime_utc": "",
    }
    try:
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        return {}, metadata, f"ARTIFACT_READ_FAILED:{type(exc).__name__}:{exc}"
    sha256 = hashlib.sha256(raw).hexdigest()
    metadata = {
        "artifact_path": _relative_path(path, root),
        "artifact_sha256": sha256,
        "artifact_size_bytes": len(raw),
        "artifact_mtime_utc": _mtime_utc_ns(after.st_mtime_ns),
    }
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
    ):
        return {}, metadata, "ARTIFACT_CHANGED_DURING_READ"
    try:
        if path.name == "scan_result.json":
            # 分片 store 自动组装（索引文件本身仍是有效 JSON；旧单文件等同 json.loads）
            from .scan_result_store import load_scan_result

            payload = load_scan_result(path, keys=None)
        else:
            payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, metadata, f"INVALID_JSON_ARTIFACT:{type(exc).__name__}:{exc}"
    if not isinstance(payload, dict):
        return {}, metadata, "JSON_ARTIFACT_MUST_BE_OBJECT"
    return payload, metadata, ""


def discover_historical_authorization_artifacts(
    root: str | Path,
    *,
    project_ids: Iterable[str] | None = None,
) -> dict[str, list[Path]]:
    """Discover project artifacts without following symlinks outside the project."""
    resolved_root = Path(root).expanduser().resolve()
    requested = {_text(value) for value in (project_ids or []) if _text(value)}
    discovered: dict[str, list[Path]] = {}
    for source_root_name in ("platform_outputs", "platform_workspace"):
        source_root = resolved_root / source_root_name
        if not source_root.is_dir():
            continue
        for project_dir in sorted(source_root.iterdir(), key=lambda value: value.name):
            if (
                not project_dir.is_dir()
                or project_dir.is_symlink()
                or project_dir.name.startswith(".")
                or (requested and project_dir.name not in requested)
            ):
                continue
            project_root = project_dir.resolve()
            paths: set[Path] = set()
            for artifact_name in _ARTIFACT_NAMES:
                for candidate in project_dir.rglob(artifact_name):
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                    resolved = candidate.resolve()
                    try:
                        resolved.relative_to(project_root)
                    except ValueError:
                        continue
                    paths.add(resolved)
            if paths:
                discovered.setdefault(project_dir.name, []).extend(paths)
    return {
        project_id: sorted(set(paths), key=lambda value: value.as_posix())
        for project_id, paths in sorted(discovered.items())
    }


def _artifact_row(
    metadata: dict[str, Any],
    *,
    status: str,
    reason: str = "",
    authority_scope_id: str = "",
    run_id: str = "",
    campaign_id: str = "",
    ledger_fingerprint: str = "",
    quarantine: dict[str, Any] | None = None,
    migration_status: str = "NOT_AVAILABLE",
    rebuild_reason: str = "",
    rebuilt_registry_fingerprint: str = "",
    rebuilt_occurrence_count: int = 0,
) -> dict[str, Any]:
    quarantine_row = _dict(quarantine)
    return {
        **metadata,
        "authority_scope_id": authority_scope_id
        or "artifact:"
        + _text(metadata.get("artifact_sha256")),
        "run_id": _text(run_id),
        "campaign_id": _text(campaign_id),
        "attempt_ledger_fingerprint": _text(ledger_fingerprint),
        "status": status,
        "reason": _text(reason),
        "quarantine_count": int(quarantine_row.get("quarantine_count") or 0),
        "quarantined_finding_ids": list(
            quarantine_row.get("quarantined_finding_ids") or []
        ),
        "rerun_required_count": int(
            quarantine_row.get("rerun_required_count") or 0
        ),
        "manual_recompile_required_count": int(
            quarantine_row.get("manual_recompile_required_count") or 0
        ),
        "rerun_queue": list(quarantine_row.get("rerun_queue") or []),
        "migration_status": migration_status,
        "registry_rebuild_reason": _text(rebuild_reason),
        "rebuilt_registry_fingerprint": _text(rebuilt_registry_fingerprint),
        "rebuilt_delivery_occurrence_count": int(rebuilt_occurrence_count),
        "source_evidence_rewritten": False,
    }


def _authority_identity_problem(
    mainline: dict[str, Any],
    ledger: dict[str, Any],
) -> str:
    for field in ("run_id", "campaign_id"):
        left = _text(mainline.get(field))
        right = _text(ledger.get(field))
        if not left or not right or left != right:
            return f"AUTHORITY_IDENTITY_MISMATCH:{field}:{left}!={right}"
    mainline_fingerprint = _text(mainline.get("contract_fingerprint"))
    ledger_fingerprint = _text(ledger.get("mainline_contract_fingerprint"))
    if ledger_fingerprint and ledger_fingerprint != mainline_fingerprint:
        return (
            "AUTHORITY_IDENTITY_MISMATCH:mainline_contract_fingerprint:"
            f"{mainline_fingerprint}!={ledger_fingerprint}"
        )
    return ""


def audit_historical_authorization_artifact(
    path: str | Path,
    *,
    root: str | Path,
) -> dict[str, Any]:
    """Audit one immutable artifact snapshot without modifying its source path."""
    resolved_root = Path(root).expanduser().resolve()
    artifact_path = Path(path).expanduser().resolve()
    payload, metadata, snapshot_problem = _artifact_snapshot(
        artifact_path,
        root=resolved_root,
    )
    if snapshot_problem:
        return _artifact_row(
            metadata,
            status="INVALID_ARTIFACT",
            reason=snapshot_problem,
        )

    mainline, ledger, registry = _payload_authorities(payload)
    if not mainline:
        return _artifact_row(
            metadata,
            status="UNVERIFIABLE",
            reason="MAINLINE_RUN_MISSING",
        )
    if not ledger:
        return _artifact_row(
            metadata,
            status="UNVERIFIABLE",
            reason="ATTEMPT_LEDGER_MISSING",
        )
    try:
        validated_mainline = validate_mainline_run_contract(mainline)
    except MainlineContractError as exc:
        return _artifact_row(
            metadata,
            status="UNVERIFIABLE",
            reason=f"MAINLINE_RUN_INVALID:{exc}",
        )
    try:
        validated_ledger = validate_obligation_attempt_ledger(ledger)
    except ObligationAttemptLedgerError as exc:
        return _artifact_row(
            metadata,
            status="UNVERIFIABLE",
            reason=f"ATTEMPT_LEDGER_INVALID:{exc}",
        )

    ledger_fingerprint = _text(validated_ledger.get("ledger_fingerprint"))
    scope_id = (
        "ledger:" + ledger_fingerprint
        if ledger_fingerprint
        else "artifact:" + _text(metadata.get("artifact_sha256"))
    )
    identity_problem = _authority_identity_problem(
        validated_mainline,
        validated_ledger,
    )
    common = {
        "authority_scope_id": scope_id,
        "run_id": _text(validated_mainline.get("run_id")),
        "campaign_id": _text(validated_mainline.get("campaign_id")),
        "ledger_fingerprint": ledger_fingerprint,
    }
    if identity_problem:
        return _artifact_row(
            metadata,
            status="CONTRADICTION",
            reason=identity_problem,
            **common,
        )

    try:
        quarantine = build_historical_authorization_quarantine_projection(
            validated_ledger,
            superseded_registry_fingerprint=_text(
                registry.get("registry_fingerprint")
            ),
        )
        quarantine = validate_historical_authorization_quarantine_projection(
            quarantine
        )
    except HistoricalAuthorizationQuarantineError as exc:
        return _artifact_row(
            metadata,
            status="CONTRADICTION",
            reason=f"HISTORICAL_AUTHORIZATION_CONTRADICTION:{exc}",
            **common,
        )

    if int(quarantine.get("quarantine_count") or 0) == 0:
        return _artifact_row(
            metadata,
            status="CLEAR",
            migration_status="NOT_REQUIRED",
            **common,
        )

    try:
        migrated = migrate_historical_authorization_scan_result(payload)
    except HistoricalAuthorizationArtifactMigrationError as exc:
        reason = _text(exc)
        registry_rebuild_failure = reason.startswith(
            "historical_authorization_registry_rebuild_failed"
        )
        return _artifact_row(
            metadata,
            status=(
                "REBUILD_BLOCKED"
                if registry_rebuild_failure
                else "CONTRADICTION"
            ),
            reason=reason,
            quarantine=quarantine,
            migration_status="FAILED",
            rebuild_reason=reason,
            **common,
        )
    try:
        migration = validate_historical_authorization_artifact_migration_receipt(
            _dict(migrated.get("historical_authorization_artifact_migration"))
        )
    except HistoricalAuthorizationArtifactMigrationError as exc:
        reason = _text(exc)
        return _artifact_row(
            metadata,
            status="CONTRADICTION",
            reason=reason,
            quarantine=quarantine,
            migration_status="FAILED",
            rebuild_reason=reason,
            **common,
        )

    migration_status = _text(migration.get("status")).upper()
    if migration_status not in {"MIGRATED", "REBUILD_BLOCKED"}:
        return _artifact_row(
            metadata,
            status="CONTRADICTION",
            reason=f"MIGRATION_STATUS_INVALID:{migration_status}",
            quarantine=quarantine,
            migration_status=migration_status or "MISSING",
            **common,
        )
    return _artifact_row(
        metadata,
        status=(
            "REBUILD_BLOCKED"
            if migration_status == "REBUILD_BLOCKED"
            else "QUARANTINED"
        ),
        reason=_text(migration.get("rebuild_reason")),
        quarantine=quarantine,
        migration_status=migration_status,
        rebuild_reason=_text(migration.get("rebuild_reason")),
        rebuilt_registry_fingerprint=_text(
            migration.get("rebuilt_registry_fingerprint")
        ),
        rebuilt_occurrence_count=int(
            migration.get("rebuilt_delivery_occurrence_count") or 0
        ),
        **common,
    )


def _project_status(artifacts: list[dict[str, Any]]) -> str:
    statuses = {_text(value.get("status")).upper() for value in artifacts}
    if statuses.intersection({"CONTRADICTION", "INVALID_ARTIFACT"}):
        return "CONTRADICTION"
    if "REBUILD_BLOCKED" in statuses:
        return "REBUILD_BLOCKED"
    if "QUARANTINED" in statuses:
        return "QUARANTINED"
    if "UNVERIFIABLE" in statuses:
        return "UNVERIFIABLE"
    return "CLEAR"


def _project_inventory(
    project_id: str,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(artifacts, key=lambda value: value["artifact_path"])
    occurrences: set[tuple[str, str]] = set()
    reruns: dict[tuple[str, str, str], dict[str, Any]] = {}
    for artifact in ordered:
        scope_id = _text(artifact.get("authority_scope_id"))
        for finding_id in _list(artifact.get("quarantined_finding_ids")):
            finding = _text(finding_id)
            if finding:
                occurrences.add((scope_id, finding))
        for raw in _list(artifact.get("rerun_queue")):
            row = _dict(raw)
            finding = _text(row.get("finding_id"))
            action = _text(row.get("action"))
            if not finding or not action:
                continue
            reruns.setdefault(
                (scope_id, finding, action),
                {
                    "authority_scope_id": scope_id,
                    "run_id": _text(artifact.get("run_id")),
                    "campaign_id": _text(artifact.get("campaign_id")),
                    "finding_id": finding,
                    "obligation_id": _text(row.get("obligation_id")),
                    "experiment_id": _text(row.get("experiment_id")),
                    "action": action,
                    "requirements": list(row.get("requirements") or []),
                    "quarantine_receipt_id": _text(
                        row.get("quarantine_receipt_id")
                    ),
                },
            )
    rerun_queue = sorted(
        reruns.values(),
        key=lambda value: (
            value["authority_scope_id"],
            value["finding_id"],
            value["action"],
        ),
    )
    statuses = Counter(_text(value.get("status")).upper() for value in ordered)
    rebuilt_scopes = {
        _text(value.get("authority_scope_id"))
        for value in ordered
        if _text(value.get("migration_status")).upper() == "MIGRATED"
    }
    blocked_scopes = {
        _text(value.get("authority_scope_id"))
        for value in ordered
        if _text(value.get("migration_status")).upper()
        in {"REBUILD_BLOCKED", "FAILED"}
    }
    return {
        "project_id": project_id,
        "status": _project_status(ordered),
        "artifact_count": len(ordered),
        "authority_scope_count": len(
            {_text(value.get("authority_scope_id")) for value in ordered}
        ),
        "quarantine_occurrence_count": len(occurrences),
        "quarantined_finding_ids": sorted(
            {finding_id for _, finding_id in occurrences}
        ),
        "rerun_required_count": sum(
            value["action"] == "RERUN_REQUIRED" for value in rerun_queue
        ),
        "manual_recompile_required_count": sum(
            value["action"] == "MANUAL_RECOMPILE_REQUIRED"
            for value in rerun_queue
        ),
        "registry_rebuilt_scope_count": len(rebuilt_scopes),
        "registry_rebuild_blocked_scope_count": len(blocked_scopes),
        "unverifiable_artifact_count": statuses["UNVERIFIABLE"],
        "contradiction_artifact_count": statuses["CONTRADICTION"],
        "invalid_artifact_count": statuses["INVALID_ARTIFACT"],
        "rerun_queue": rerun_queue,
        "artifacts": ordered,
    }


def _report_summary(
    projects: list[dict[str, Any]],
    *,
    missing_projects: list[str],
) -> dict[str, Any]:
    del missing_projects  # Missing filters are navigation diagnostics, not evidence.
    artifacts = [
        artifact for project in projects for artifact in project["artifacts"]
    ]
    project_statuses = {value["status"] for value in projects}
    status = (
        "CONTRADICTION"
        if "CONTRADICTION" in project_statuses
        else "ACTION_REQUIRED"
        if project_statuses - {"CLEAR"}
        else "CLEAR"
    )
    return {
        "status": status,
        "project_count": len(projects),
        "artifact_count": len(artifacts),
        "authority_scope_count": sum(
            value["authority_scope_count"] for value in projects
        ),
        "projects_with_quarantine_count": sum(
            value["quarantine_occurrence_count"] > 0 for value in projects
        ),
        "projects_with_rebuild_blocked_count": sum(
            value["registry_rebuild_blocked_scope_count"] > 0
            for value in projects
        ),
        "projects_unverifiable_count": sum(
            value["unverifiable_artifact_count"] > 0 for value in projects
        ),
        "projects_with_contradiction_count": sum(
            value["status"] == "CONTRADICTION" for value in projects
        ),
        "quarantine_occurrence_count": sum(
            value["quarantine_occurrence_count"] for value in projects
        ),
        "rerun_required_count": sum(
            value["rerun_required_count"] for value in projects
        ),
        "manual_recompile_required_count": sum(
            value["manual_recompile_required_count"] for value in projects
        ),
        "project_status_counts": {
            status_name: sum(
                value["status"] == status_name for value in projects
            )
            for status_name in _PROJECT_STATUSES
        },
        "artifact_status_counts": {
            status_name: sum(
                value["status"] == status_name for value in artifacts
            )
            for status_name in _ARTIFACT_STATUSES
        },
    }


def build_historical_authorization_inventory(
    root: str | Path,
    *,
    project_ids: Iterable[str] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build one read-only, project-level historical authorization inventory."""
    resolved_root = Path(root).expanduser().resolve()
    requested = sorted({_text(value) for value in (project_ids or []) if _text(value)})
    discovered = discover_historical_authorization_artifacts(
        resolved_root,
        project_ids=requested,
    )
    projects = [
        _project_inventory(
            project_id,
            [
                audit_historical_authorization_artifact(
                    path,
                    root=resolved_root,
                )
                for path in paths
            ],
        )
        for project_id, paths in discovered.items()
    ]
    projects.sort(key=lambda value: value["project_id"])
    missing = sorted(set(requested) - set(discovered))
    payload: dict[str, Any] = {
        "schema_version": INVENTORY_SCHEMA,
        "generated_at_utc": _text(generated_at_utc) or _utc_now(),
        "root": str(resolved_root),
        "source_roots": ["platform_outputs", "platform_workspace"],
        "requested_projects": requested,
        "missing_projects": missing,
        **_report_summary(projects, missing_projects=missing),
        "source_artifacts_modified": False,
        "projects": projects,
    }
    payload["inventory_fingerprint"] = _fingerprint(payload)
    return validate_historical_authorization_inventory(payload)


def _validate_artifact_row(row: dict[str, Any]) -> None:
    if set(row) != _ARTIFACT_FIELDS:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_fields_invalid"
        )
    status = _text(row.get("status")).upper()
    migration_status = _text(row.get("migration_status")).upper()
    if status not in _ARTIFACT_STATUSES or migration_status not in _MIGRATION_STATUSES:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_status_invalid"
        )
    if row.get("source_evidence_rewritten") is not False:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_mutation_forbidden"
        )
    if (
        not _text(row.get("artifact_path"))
        or len(_text(row.get("artifact_sha256"))) != 64
        or not _text(row.get("authority_scope_id"))
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_identity_invalid"
        )
    finding_ids = row.get("quarantined_finding_ids")
    if (
        not isinstance(finding_ids, list)
        or finding_ids
        != sorted(set(_text(value) for value in finding_ids if _text(value)))
        or int(row.get("quarantine_count") or 0) != len(finding_ids)
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_quarantine_invalid"
        )
    queue = [_dict(value) for value in _list(row.get("rerun_queue"))]
    if (
        int(row.get("rerun_required_count") or 0)
        != sum(_text(value.get("action")) == "RERUN_REQUIRED" for value in queue)
        or int(row.get("manual_recompile_required_count") or 0)
        != sum(
            _text(value.get("action")) == "MANUAL_RECOMPILE_REQUIRED"
            for value in queue
        )
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_rerun_count_invalid"
        )
    if status == "CLEAR" and (
        int(row.get("quarantine_count") or 0) != 0
        or migration_status != "NOT_REQUIRED"
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_clear_semantics_invalid"
        )
    if status == "QUARANTINED" and migration_status != "MIGRATED":
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_migration_semantics_invalid"
        )
    if status == "REBUILD_BLOCKED" and migration_status not in {
        "REBUILD_BLOCKED",
        "FAILED",
    }:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_artifact_rebuild_semantics_invalid"
        )


def validate_historical_authorization_inventory(
    report: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(report)
    if set(row) != _REPORT_FIELDS:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_fields_invalid"
        )
    if row.get("schema_version") != INVENTORY_SCHEMA:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_schema_invalid"
        )
    if row.get("source_roots") != ["platform_outputs", "platform_workspace"]:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_source_roots_invalid"
        )
    if (
        row.get("status") not in {"CLEAR", "ACTION_REQUIRED", "CONTRADICTION"}
        or not _text(row.get("generated_at_utc"))
        or not _text(row.get("root"))
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_identity_invalid"
        )
    if row.get("source_artifacts_modified") is not False:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_source_mutation_forbidden"
        )
    requested = row.get("requested_projects")
    missing = row.get("missing_projects")
    if (
        not isinstance(requested, list)
        or requested != sorted(set(_text(value) for value in requested if _text(value)))
        or not isinstance(missing, list)
        or missing != sorted(set(_text(value) for value in missing if _text(value)))
        or not set(missing).issubset(set(requested))
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_requested_projects_invalid"
        )

    projects = [_dict(value) for value in _list(row.get("projects"))]
    project_ids = [_text(value.get("project_id")) for value in projects]
    if (
        any(set(value) != _PROJECT_FIELDS for value in projects)
        or project_ids != sorted(set(project_ids))
        or any(value.get("status") not in _PROJECT_STATUSES for value in projects)
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_projects_invalid"
        )
    canonical_projects: list[dict[str, Any]] = []
    for project in projects:
        artifacts = [_dict(value) for value in _list(project.get("artifacts"))]
        for artifact in artifacts:
            _validate_artifact_row(artifact)
        expected_project = _project_inventory(project["project_id"], artifacts)
        if project != expected_project:
            raise HistoricalAuthorizationInventoryError(
                f"historical_authorization_inventory_project_summary_invalid:"
                f"{project['project_id']}"
            )
        canonical_projects.append(expected_project)

    expected_summary = _report_summary(
        canonical_projects,
        missing_projects=missing,
    )
    for key, value in expected_summary.items():
        if row.get(key) != value:
            raise HistoricalAuthorizationInventoryError(
                f"historical_authorization_inventory_summary_invalid:{key}"
            )
    observed = _text(row.get("inventory_fingerprint"))
    expected = _fingerprint(
        {key: value for key, value in row.items() if key != "inventory_fingerprint"}
    )
    if not observed or observed != expected:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_fingerprint_invalid"
        )
    return dict(row)


def resolve_inventory_report_path(
    output: str | Path | None,
    *,
    root: str | Path,
) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    if output is None or _text(output) in {"", "default"}:
        return resolved_root / DEFAULT_REPORT_RELATIVE_PATH
    path = Path(output).expanduser()
    return path.resolve() if path.is_absolute() else (resolved_root / path).resolve()


def write_historical_authorization_inventory(
    report: dict[str, Any],
    *,
    output: str | Path | None = None,
    root: str | Path,
) -> Path:
    validated = validate_historical_authorization_inventory(report)
    destination = resolve_inventory_report_path(output, root=root)
    _write_json_object_atomic(destination, validated)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit stored QualiBug projects for historical authorization findings "
            "that require quarantine or rerun. Source artifacts are never modified."
        )
    )
    parser.add_argument("--root", default=".", help="QualiBug root directory.")
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Audit only this project ID. Repeat for multiple projects.",
    )
    parser.add_argument(
        "--output",
        default="default",
        help=(
            "Inventory JSON path. Default: "
            "platform_outputs/historical_authorization_inventory.json under --root."
        ),
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print the report without writing the inventory JSON file.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty JSON.",
    )
    args = parser.parse_args(argv)
    try:
        report = build_historical_authorization_inventory(
            args.root,
            project_ids=args.project,
        )
        if not args.stdout_only:
            write_historical_authorization_inventory(
                report,
                output=args.output,
                root=args.root,
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}:{exc}",
                    "source_artifacts_modified": False,
                },
                ensure_ascii=False,
                indent=None if args.compact else 2,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_REPORT_RELATIVE_PATH",
    "HistoricalAuthorizationInventoryError",
    "INVENTORY_SCHEMA",
    "audit_historical_authorization_artifact",
    "build_historical_authorization_inventory",
    "discover_historical_authorization_artifacts",
    "main",
    "resolve_inventory_report_path",
    "validate_historical_authorization_inventory",
    "write_historical_authorization_inventory",
]
