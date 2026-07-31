"""Derived migration view for stored historical authorization artifacts.

The source attempt ledger, Gate receipts, findings, and replay evidence are never
rewritten. When the quarantine authority identifies unverifiable historical
authorization occurrences, this adapter rebuilds only the current canonical registry
from the non-quarantined formal scope and seals a migration receipt. The old registry
is represented only by its fingerprint, so obsolete customer-visible claims remain
auditable without being republished.

When a stored artifact lacks a still-formal occurrence needed for registry rebuilding,
the migration becomes REBUILD_BLOCKED: the obsolete registry is removed from the
derived view and publication remains disabled. Present-but-contradictory evidence is
still a hard error and is never converted into a compatibility result.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
    build_historical_authorization_quarantine_projection,
    validate_historical_authorization_quarantine_projection,
)


MIGRATION_RECEIPT_SCHEMA = (
    "qualibug.historical-authorization-artifact-migration.v1"
)


class HistoricalAuthorizationArtifactMigrationError(ValueError):
    """A stored artifact cannot be migrated without changing source evidence."""


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


def _finding_id(value: dict[str, Any]) -> str:
    row = _dict(value)
    return _text(row.get("finding_id") or row.get("id") or row.get("bug_id"))


def _migration_receipt(
    *,
    quarantine: dict[str, Any],
    superseded_registry_fingerprint: str,
    rebuilt_registry_fingerprint: str,
    rebuilt_occurrence_count: int,
    rebuild_reason: str = "",
) -> dict[str, Any]:
    quarantined = int(quarantine.get("quarantine_count") or 0) > 0
    status = (
        "REBUILD_BLOCKED"
        if _text(rebuild_reason)
        else "MIGRATED"
        if quarantined
        else "CLEAR"
    )
    payload = {
        "schema_version": MIGRATION_RECEIPT_SCHEMA,
        "status": status,
        "run_id": _text(quarantine.get("run_id")),
        "campaign_id": _text(quarantine.get("campaign_id")),
        "attempt_ledger_fingerprint": _text(
            quarantine.get("attempt_ledger_fingerprint")
        ),
        "quarantine_projection_fingerprint": _text(
            quarantine.get("projection_fingerprint")
        ),
        "superseded_registry_fingerprint": _text(
            superseded_registry_fingerprint
        ),
        "rebuilt_registry_fingerprint": _text(
            rebuilt_registry_fingerprint
        ),
        "rebuild_reason": _text(rebuild_reason),
        "quarantined_finding_ids": list(
            quarantine.get("quarantined_finding_ids") or []
        ),
        "rebuilt_delivery_occurrence_count": int(rebuilt_occurrence_count),
        "source_evidence_rewritten": False,
    }
    fingerprint = _fingerprint(payload)
    return {
        **payload,
        "receipt_id": "auth_artifact_migration_" + fingerprint[:24],
        "receipt_fingerprint": fingerprint,
    }


def validate_historical_authorization_artifact_migration_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(receipt)
    required = {
        "schema_version",
        "status",
        "run_id",
        "campaign_id",
        "attempt_ledger_fingerprint",
        "quarantine_projection_fingerprint",
        "superseded_registry_fingerprint",
        "rebuilt_registry_fingerprint",
        "rebuild_reason",
        "quarantined_finding_ids",
        "rebuilt_delivery_occurrence_count",
        "source_evidence_rewritten",
        "receipt_id",
        "receipt_fingerprint",
    }
    if set(row) != required:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_receipt_fields_invalid"
        )
    if row.get("schema_version") != MIGRATION_RECEIPT_SCHEMA:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_receipt_schema_invalid"
        )
    status = _text(row.get("status")).upper()
    if status not in {"CLEAR", "MIGRATED", "REBUILD_BLOCKED"}:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_receipt_status_invalid"
        )
    if row.get("source_evidence_rewritten") is not False:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_source_rewrite_forbidden"
        )
    ids = row.get("quarantined_finding_ids")
    if (
        not isinstance(ids, list)
        or ids != sorted(set(_text(value) for value in ids if _text(value)))
    ):
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_ids_invalid"
        )
    if status in {"MIGRATED", "REBUILD_BLOCKED"} and not ids:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_ids_missing"
        )
    if status == "CLEAR" and ids:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_clear_with_ids"
        )
    for field in (
        "run_id",
        "campaign_id",
        "attempt_ledger_fingerprint",
        "quarantine_projection_fingerprint",
    ):
        if not _text(row.get(field)):
            raise HistoricalAuthorizationArtifactMigrationError(
                f"historical_authorization_migration_identity_missing:{field}"
            )
    if status == "REBUILD_BLOCKED":
        if (
            not _text(row.get("rebuild_reason"))
            or _text(row.get("rebuilt_registry_fingerprint"))
            or int(row.get("rebuilt_delivery_occurrence_count") or 0) != 0
        ):
            raise HistoricalAuthorizationArtifactMigrationError(
                "historical_authorization_migration_blocked_semantics_invalid"
            )
    elif (
        _text(row.get("rebuild_reason"))
        or not _text(row.get("rebuilt_registry_fingerprint"))
    ):
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_rebuilt_identity_invalid"
        )
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    fingerprint = _fingerprint(unsigned)
    if (
        _text(row.get("receipt_id"))
        != "auth_artifact_migration_" + fingerprint[:24]
        or _text(row.get("receipt_fingerprint")) != fingerprint
    ):
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_receipt_fingerprint_invalid"
        )
    return dict(row)


def _attach_migration_view(
    result: dict[str, Any],
    *,
    quarantine: dict[str, Any],
    migration: dict[str, Any],
    rebuilt_registry: dict[str, Any] | None,
) -> dict[str, Any]:
    output = dict(result)
    output["historical_authorization_quarantine"] = quarantine
    output["historical_authorization_artifact_migration"] = migration
    if rebuilt_registry is None:
        output.pop("canonical_defect_registry", None)
    else:
        output["canonical_defect_registry"] = rebuilt_registry
    if isinstance(output.get("v12"), dict):
        nested = dict(output["v12"])
        nested["historical_authorization_quarantine"] = quarantine
        nested["historical_authorization_artifact_migration"] = migration
        if rebuilt_registry is None:
            nested.pop("canonical_defect_registry", None)
        else:
            nested["canonical_defect_registry"] = rebuilt_registry
        output["v12"] = nested
    return output


def migrate_historical_authorization_scan_result(
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    """Return a migrated view while preserving every source evidence object."""
    source = _dict(scan_result)
    if not source:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_scan_result_missing"
        )
    result = deepcopy(source)
    v12 = _dict(result.get("v12"))
    ledger = _dict(
        result.get("obligation_attempt_ledger")
        or v12.get("obligation_attempt_ledger")
    )
    mainline = _dict(result.get("mainline_run") or v12.get("mainline_run"))
    occurrences = [
        dict(value)
        for value in _list(
            result.get("delivery_occurrences")
            or v12.get("delivery_occurrences")
        )
        if isinstance(value, dict)
    ]
    old_registry = _dict(
        result.get("canonical_defect_registry")
        or v12.get("canonical_defect_registry")
    )
    if not ledger:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_attempt_ledger_missing"
        )
    if not mainline:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_mainline_run_missing"
        )

    old_registry_fingerprint = _text(old_registry.get("registry_fingerprint"))
    try:
        quarantine = build_historical_authorization_quarantine_projection(
            ledger,
            superseded_registry_fingerprint=old_registry_fingerprint,
        )
        quarantine = validate_historical_authorization_quarantine_projection(
            quarantine
        )
    except HistoricalAuthorizationQuarantineError as exc:
        raise HistoricalAuthorizationArtifactMigrationError(
            f"historical_authorization_quarantine_invalid:{exc}"
        ) from exc

    # Lazy imports avoid cycles through canonical_defect_registry/formal_delivery_scope.
    from .canonical_defect_registry import build_canonical_defect_registry
    from .discovery_mainline_contract import validate_mainline_run_contract
    from .formal_delivery_scope import validated_delivery_gate_finding_ids

    try:
        formal_occurrence_ids = set(
            validated_delivery_gate_finding_ids(ledger)
        )
    except Exception as exc:
        raise HistoricalAuthorizationArtifactMigrationError(
            f"historical_authorization_formal_scope_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    materialized_occurrence_ids = {
        _finding_id(value) for value in occurrences if _finding_id(value)
    }
    missing_occurrences = sorted(
        formal_occurrence_ids - materialized_occurrence_ids
    )
    if missing_occurrences:
        migration = _migration_receipt(
            quarantine=quarantine,
            superseded_registry_fingerprint=old_registry_fingerprint,
            rebuilt_registry_fingerprint="",
            rebuilt_occurrence_count=0,
            rebuild_reason=(
                "FORMAL_DELIVERY_OCCURRENCES_MISSING:"
                + ",".join(missing_occurrences)
            ),
        )
        migration = validate_historical_authorization_artifact_migration_receipt(
            migration
        )
        return _attach_migration_view(
            result,
            quarantine=quarantine,
            migration=migration,
            rebuilt_registry=None,
        )

    try:
        validated_mainline = validate_mainline_run_contract(mainline)
        rebuilt_registry = build_canonical_defect_registry(
            mainline_run=validated_mainline,
            deliverable_occurrences=occurrences,
            obligation_attempt_ledger=ledger,
        )
    except Exception as exc:
        raise HistoricalAuthorizationArtifactMigrationError(
            f"historical_authorization_registry_rebuild_failed:{type(exc).__name__}:{exc}"
        ) from exc

    migration = _migration_receipt(
        quarantine=quarantine,
        superseded_registry_fingerprint=old_registry_fingerprint,
        rebuilt_registry_fingerprint=_text(
            rebuilt_registry.get("registry_fingerprint")
        ),
        rebuilt_occurrence_count=int(
            rebuilt_registry.get("delivery_occurrence_count") or 0
        ),
    )
    migration = validate_historical_authorization_artifact_migration_receipt(
        migration
    )
    return _attach_migration_view(
        result,
        quarantine=quarantine,
        migration=migration,
        rebuilt_registry=rebuilt_registry,
    )


__all__ = [
    "HistoricalAuthorizationArtifactMigrationError",
    "MIGRATION_RECEIPT_SCHEMA",
    "migrate_historical_authorization_scan_result",
    "validate_historical_authorization_artifact_migration_receipt",
]
