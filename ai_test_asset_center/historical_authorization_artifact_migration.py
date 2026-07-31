"""Derived migration view for stored historical authorization artifacts.

The source attempt ledger, Gate receipts, findings, and replay evidence are never
rewritten.  When the quarantine authority identifies unverifiable historical
authorization occurrences, this adapter rebuilds only the current canonical registry
from the non-quarantined formal scope and seals a migration receipt.  The old registry
is represented only by its fingerprint, so obsolete customer-visible claims remain
auditable without being republished.
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


def _migration_receipt(
    *,
    quarantine: dict[str, Any],
    superseded_registry_fingerprint: str,
    rebuilt_registry_fingerprint: str,
    rebuilt_occurrence_count: int,
) -> dict[str, Any]:
    payload = {
        "schema_version": MIGRATION_RECEIPT_SCHEMA,
        "status": (
            "MIGRATED"
            if int(quarantine.get("quarantine_count") or 0) > 0
            else "CLEAR"
        ),
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
    if row.get("status") not in {"CLEAR", "MIGRATED"}:
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
    if row.get("status") == "MIGRATED" and not ids:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_ids_missing"
        )
    if row.get("status") == "CLEAR" and ids:
        raise HistoricalAuthorizationArtifactMigrationError(
            "historical_authorization_migration_clear_with_ids"
        )
    for field in (
        "run_id",
        "campaign_id",
        "attempt_ledger_fingerprint",
        "quarantine_projection_fingerprint",
        "rebuilt_registry_fingerprint",
    ):
        if not _text(row.get(field)):
            raise HistoricalAuthorizationArtifactMigrationError(
                f"historical_authorization_migration_identity_missing:{field}"
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

    # Lazy imports avoid a cycle: canonical_defect_registry -> formal_delivery_scope
    # -> historical_authorization_quarantine.
    from .canonical_defect_registry import build_canonical_defect_registry
    from .discovery_mainline_contract import validate_mainline_run_contract

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
    result["historical_authorization_quarantine"] = quarantine
    result["historical_authorization_artifact_migration"] = migration
    result["canonical_defect_registry"] = rebuilt_registry
    if isinstance(result.get("v12"), dict):
        nested = dict(result["v12"])
        nested["historical_authorization_quarantine"] = quarantine
        nested["historical_authorization_artifact_migration"] = migration
        nested["canonical_defect_registry"] = rebuilt_registry
        result["v12"] = nested
    return result


__all__ = [
    "HistoricalAuthorizationArtifactMigrationError",
    "MIGRATION_RECEIPT_SCHEMA",
    "migrate_historical_authorization_scan_result",
    "validate_historical_authorization_artifact_migration_receipt",
]
