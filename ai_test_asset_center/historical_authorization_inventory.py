"""Read-only inventory for historical authorization artifacts across projects.

The inventory does not rewrite scan results, attempt ledgers, Gate receipts,
findings, or canonical registries. It discovers existing project artifacts, invokes
the existing historical authorization quarantine and migration authorities, and emits
only a content-addressed remediation report.

Counts are deduplicated by immutable attempt-ledger fingerprint plus finding ID so
copies of one run in scan_result.json and v12_report.json cannot inflate the number
of authorization occurrences that require rerun.
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
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic


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
_ARTIFACT_STATUSES = (
    "CLEAR",
    "QUARANTINED",
    "REBUILD_BLOCKED",
    "UNVERIFIABLE",
    "CONTRADICTION",
    "INVALID_ARTIFACT",
)
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


def _mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        timezone.utc,
    ).isoformat().replace("+00:00", "Z")


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload_authorities(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    row = _dict(payload)
    nested = _dict(row.get("v12"))
    mainline = _dict(row.get("mainline_run") or nested.get("mainline_run"))
    ledger = _dict(
        row.get("obligation_attempt_ledger")
        or nested.get("obligation_attempt_ledger")
    )
    registry = _dict(
        row.get("canonical_defect_registry")
        or nested.get("canonical_defect_registry")
    )
    return mainline, ledger, registry


def discover_historical_authorization_artifacts(
    root: str | Path,
    *,
    project_ids: Iterable[str] | None = None,
) -> dict[str, list[Path]]:
    """Discover direct project artifacts without following symlinks outside root."""
    resolved_root = Path(root).expanduser().resolve()
    requested = {
        _text(value) for value in (project_ids or []) if _text(value)
    }
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
            project_paths: set[Path] = set()
            project_root = project_dir.resolve()
            for artifact_name in _ARTIFACT_NAMES:
                for candidate in project_dir.rglob(artifact_name):
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                    resolved = candidate.resolve()
                    try:
                        resolved.relative_to(project_root)
                    except ValueError:
                        continue
                    project_paths.add(resolved)
            if project_paths:
                discovered.setdefault(project_dir.name, []).extend(
                    sorted(project_paths, key=lambda value: value.as_posix())
                )
    return {
        project_id: sorted(set(paths), key=lambda value: value.as_posix())
        for project_id, paths in sorted(discovered.items())
    }


def _empty_artifact_row(
    *,
    path: Path,
    root: Path,
    artifact_sha256: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "artifact_path": _relative_path(path, root),
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": path.stat().st_size,
        "artifact_mtime_utc": _mtime_utc(path),
        "authority_scope_id": "artifact:" + artifact_sha256,
        "run_id": "",
        "campaign_id": "",
        "attempt_ledger_fingerprint": "",
        "status": status,
        "reason": reason,
        "quarantine_count": 0,
        "quarantined_finding_ids": [],
        "rerun_required_count": 0,
        "manual_recompile_required_count": 0,
        "rerun_queue": [],
        "migration_status": "NOT_AVAILABLE",
        "registry_rebuild_reason": "",
        "rebuilt_registry_fingerprint": "",
        "rebuilt_delivery_occurrence_count": 0,
        "source_evidence_rewritten": False,
    }


def audit_historical_authorization_artifact(
    path: str | Path,
    *,
    root: str | Path,
) -> dict[str, Any]:
    """Audit one artifact and return metadata only; source bytes remain untouched."""
    resolved_root = Path(root).expanduser().resolve()
    artifact_path = Path(path).expanduser().resolve()
    sha256 = _artifact_sha256(artifact_path)
    try:
        payload = _read_json_object(artifact_path)
    except (OSError, ValueError) as exc:
        return _empty_artifact_row(
            path=artifact_path,
            root=resolved_root,
            artifact_sha256=sha256,
            status="INVALID_ARTIFACT",
            reason=f"{type(exc).__name__}:{exc}",
        )

    mainline, ledger, registry = _payload_authorities(payload)
    if not mainline:
        return _empty_artifact_row(
            path=artifact_path,
            root=resolved_root,
            artifact_sha256=sha256,
            status="UNVERIFIABLE",
            reason="MAINLINE_RUN_MISSING",
        )
    if not ledger:
        return _empty_artifact_row(
            path=artifact_path,
            root=resolved_root,
            artifact_sha256=sha256,
            status="UNVERIFIABLE",
            reason="ATTEMPT_LEDGER_MISSING",
        )
    try:
        validated_mainline = validate_mainline_run_contract(mainline)
    except MainlineContractError as exc:
        return _empty_artifact_row(
            path=artifact_path,
            root=resolved_root,
            artifact_sha256=sha256,
            status="UNVERIFIABLE",
            reason=f"MAINLINE_RUN_INVALID:{exc}",
        )
    try:
        validated_ledger = validate_obligation_attempt_ledger(ledger)
    except ObligationAttemptLedgerError as exc:
        return _empty_artifact_row(
            path=artifact_path,
            root=resolved_root,
            artifact_sha256=sha256,
            status="UNVERIFIABLE",
            reason=f"ATTEMPT_LEDGER_INVALID:{exc}",
        )

    ledger_fingerprint = _text(validated_ledger.get("ledger_fingerprint"))
    authority_scope_id = (
        "ledger:" + ledger_fingerprint
        if ledger_fingerprint
        else "artifact:" + sha256
    )
    base = {
        "artifact_path": _relative_path(artifact_path, resolved_root),
        "artifact_sha256": sha256,
        "artifact_size_bytes": artifact_path.stat().st_size,
        "artifact_mtime_utc": _mtime_utc(artifact_path),
        "authority_scope_id": authority_scope_id,
        "run_id": _text(validated_mainline.get("run_id")),
        "campaign_id": _text(validated_mainline.get("campaign_id")),
        "attempt_ledger_fingerprint": ledger_fingerprint,
    }
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
        return {
            **base,
            "status": "CONTRADICTION",
            "reason": f"HISTORICAL_AUTHORIZATION_CONTRADICTION:{exc}",
            "quarantine_count": 0,
            "quarantined_finding_ids": [],
            "rerun_required_count": 0,
            "manual_recompile_required_count": 0,
            "rerun_queue": [],
            "migration_status": "NOT_AVAILABLE",
            "registry_rebuild_reason": "",
            "rebuilt_registry_fingerprint": "",
            "rebuilt_delivery_occurrence_count": 0,
            "source_evidence_rewritten": False,
        }

    quarantine_count = int(quarantine.get("quarantine_count") or 0)
    if quarantine_count == 0:
        return {
            **base,
            "status": "CLEAR",
            "reason": "",
            "quarantine_count": 0,
            "quarantined_finding_ids": [],
            "rerun_required_count": 0,
            "manual_recompile_required_count": 0,
            "rerun_queue": [],
            "migration_status": "NOT_REQUIRED",
            "registry_rebuild_reason": "",
            "rebuilt_registry_fingerprint": "",
            "rebuilt_delivery_occurrence_count": 0,
            "source_evidence_rewritten": False,
        }

    try:
        migrated = migrate_historical_authorization_scan_result(payload)
        migration = validate_historical_authorization_artifact_migration_receipt(
            _dict(migrated.get("historical_authorization_artifact_migration"))
        )
    except HistoricalAuthorizationArtifactMigrationError as exc:
        reason = _text(exc)
        contradiction = (
            "historical_authorization_contradiction" in reason
            or "historical_authorization_formal_scope_invalid" in reason
        )
        return {
            **base,
            "status": "CONTRADICTION" if contradiction else "REBUILD_BLOCKED",
            "reason": reason,
            "quarantine_count": quarantine_count,
            "quarantined_finding_ids": list(
                quarantine.get("quarantined_finding_ids") or []
            ),
            "rerun_required_count": int(
                quarantine.get("rerun_required_count") or 0
            ),
            "manual_recompile_required_count": int(
                quarantine.get("manual_recompile_required_count") or 0
            ),
            "rerun_queue": list(quarantine.get("rerun_queue") or []),
            "migration_status": "FAILED",
            "registry_rebuild_reason": reason,
            "rebuilt_registry_fingerprint": "",
            "rebuilt_delivery_occurrence_count": 0,
            "source_evidence_rewritten": False,
        }

    migration_status = _text(migration.get("status")).upper()
    return {
        **base,
        "status": (
            "REBUILD_BLOCKED"
            if migration_status == "REBUILD_BLOCKED"
            else "QUARANTINED"
        ),
        "reason": _text(migration.get("rebuild_reason")),
        "quarantine_count": quarantine_count,
        "quarantined_finding_ids": list(
            quarantine.get("quarantined_finding_ids") or []
        ),
        "rerun_required_count": int(
            quarantine.get("rerun_required_count") or 0
        ),
        "manual_recompile_required_count": int(
            quarantine.get("manual_recompile_required_count") or 0
        ),
        "rerun_queue": list(quarantine.get("rerun_queue") or []),
        "migration_status": migration_status,
        "registry_rebuild_reason": _text(migration.get("rebuild_reason")),
        "rebuilt_registry_fingerprint": _text(
            migration.get("rebuilt_registry_fingerprint")
        ),
        "rebuilt_delivery_occurrence_count": int(
            migration.get("rebuilt_delivery_occurrence_count") or 0
        ),
        "source_evidence_rewritten": bool(
            migration.get("source_evidence_rewritten")
        ),
    }


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
    quarantine_occurrences: dict[tuple[str, str], dict[str, Any]] = {}
    reruns: dict[tuple[str, str, str], dict[str, Any]] = {}
    for artifact in ordered:
        scope_id = _text(artifact.get("authority_scope_id"))
        for finding_id in _list(artifact.get("quarantined_finding_ids")):
            finding = _text(finding_id)
            if finding:
                quarantine_occurrences.setdefault(
                    (scope_id, finding),
                    {
                        "authority_scope_id": scope_id,
                        "finding_id": finding,
                    },
                )
        for raw in _list(artifact.get("rerun_queue")):
            row = _dict(raw)
            finding = _text(row.get("finding_id"))
            action = _text(row.get("action"))
            receipt_id = _text(row.get("quarantine_receipt_id"))
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
                    "quarantine_receipt_id": receipt_id,
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
    status_counter = Counter(_text(value.get("status")).upper() for value in ordered)
    rebuilt_scopes = {
        _text(value.get("authority_scope_id"))
        for value in ordered
        if _text(value.get("migration_status")).upper() == "MIGRATED"
    }
    blocked_scopes = {
        _text(value.get("authority_scope_id"))
        for value in ordered
        if _text(value.get("migration_status")).upper() in {
            "REBUILD_BLOCKED",
            "FAILED",
        }
    }
    return {
        "project_id": project_id,
        "status": _project_status(ordered),
        "artifact_count": len(ordered),
        "authority_scope_count": len(
            {_text(value.get("authority_scope_id")) for value in ordered}
        ),
        "quarantine_occurrence_count": len(quarantine_occurrences),
        "quarantined_finding_ids": sorted(
            {value["finding_id"] for value in quarantine_occurrences.values()}
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
        "unverifiable_artifact_count": status_counter["UNVERIFIABLE"],
        "contradiction_artifact_count": status_counter["CONTRADICTION"],
        "invalid_artifact_count": status_counter["INVALID_ARTIFACT"],
        "rerun_queue": rerun_queue,
        "artifacts": ordered,
    }


def build_historical_authorization_inventory(
    root: str | Path,
    *,
    project_ids: Iterable[str] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build one read-only, project-level historical authorization inventory."""
    resolved_root = Path(root).expanduser().resolve()
    requested_projects = sorted(
        {_text(value) for value in (project_ids or []) if _text(value)}
    )
    discovered = discover_historical_authorization_artifacts(
        resolved_root,
        project_ids=requested_projects,
    )
    projects: list[dict[str, Any]] = []
    for project_id, paths in discovered.items():
        projects.append(
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
        )
    projects.sort(key=lambda value: value["project_id"])
    missing_projects = sorted(set(requested_projects) - set(discovered))
    project_status_counts = {
        status: sum(value["status"] == status for value in projects)
        for status in _PROJECT_STATUSES
    }
    artifact_rows = [
        artifact
        for project in projects
        for artifact in project["artifacts"]
    ]
    artifact_status_counts = {
        status: sum(value["status"] == status for value in artifact_rows)
        for status in _ARTIFACT_STATUSES
    }
    project_statuses = {value["status"] for value in projects}
    overall_status = (
        "CONTRADICTION"
        if "CONTRADICTION" in project_statuses
        else "ACTION_REQUIRED"
        if project_statuses - {"CLEAR"}
        else "CLEAR"
    )
    payload: dict[str, Any] = {
        "schema_version": INVENTORY_SCHEMA,
        "generated_at_utc": _text(generated_at_utc) or _utc_now(),
        "root": str(resolved_root),
        "source_roots": ["platform_outputs", "platform_workspace"],
        "requested_projects": requested_projects,
        "missing_projects": missing_projects,
        "status": overall_status,
        "project_count": len(projects),
        "artifact_count": len(artifact_rows),
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
        "project_status_counts": project_status_counts,
        "artifact_status_counts": artifact_status_counts,
        "source_artifacts_modified": False,
        "projects": projects,
    }
    payload["inventory_fingerprint"] = _fingerprint(payload)
    return validate_historical_authorization_inventory(payload)


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
    if row.get("status") not in {"CLEAR", "ACTION_REQUIRED", "CONTRADICTION"}:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_status_invalid"
        )
    if row.get("source_artifacts_modified") is not False:
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_source_mutation_forbidden"
        )
    projects = _list(row.get("projects"))
    if any(set(_dict(value)) != _PROJECT_FIELDS for value in projects):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_project_fields_invalid"
        )
    project_ids = [_text(_dict(value).get("project_id")) for value in projects]
    if project_ids != sorted(set(project_ids)):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_projects_not_canonical"
        )
    artifacts: list[dict[str, Any]] = []
    for project in projects:
        artifact_rows = [_dict(value) for value in _list(project.get("artifacts"))]
        if any(set(value) != _ARTIFACT_FIELDS for value in artifact_rows):
            raise HistoricalAuthorizationInventoryError(
                "historical_authorization_inventory_artifact_fields_invalid"
            )
        if int(project.get("artifact_count") or 0) != len(artifact_rows):
            raise HistoricalAuthorizationInventoryError(
                "historical_authorization_inventory_project_artifact_count_invalid"
            )
        if any(value.get("source_evidence_rewritten") is not False for value in artifact_rows):
            raise HistoricalAuthorizationInventoryError(
                "historical_authorization_inventory_artifact_mutation_forbidden"
            )
        artifacts.extend(artifact_rows)
    if (
        int(row.get("project_count") or 0) != len(projects)
        or int(row.get("artifact_count") or 0) != len(artifacts)
    ):
        raise HistoricalAuthorizationInventoryError(
            "historical_authorization_inventory_count_invalid"
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
    parser.add_argument(
        "--root",
        default=".",
        help="QualiBug repository/private-pilot root.",
    )
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
