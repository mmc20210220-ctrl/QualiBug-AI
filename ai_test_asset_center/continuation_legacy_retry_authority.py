"""Compatibility bridge for legacy generic retry resume authority.

The retired continuation consumer persisted retry rows with only
``block_reason=RETRY_ELIGIBLE``. The exact continuation engine intentionally
uses concrete retry reason codes and therefore does not recognize that generic
legacy token. Dropping it would silently lose persisted retry identities across
a code upgrade.

The bridge converts the generic token to an engine-recognized eligibility token
for the duration of one continuation attempt, while recording the affected IDs.
If an identity remains pending without acquiring a newer concrete reason, the
legacy generic reason is restored before persistence. No historical failure
reason is invented.
"""
from __future__ import annotations

from typing import Any


_LEGACY_RETRY_REASON = "RETRY_ELIGIBLE"
_ENGINE_ELIGIBILITY_TOKEN = "UNRECEIPTED_SELECTED"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def prepare_legacy_retry_authority(
    obligation_plan: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Temporarily adapt legacy generic retry rows for the exact engine."""
    plan = dict(obligation_plan) if isinstance(obligation_plan, dict) else {}
    rows: list[dict[str, Any]] = []
    legacy_ids: set[str] = set()

    for raw in _list(plan.get("blocked_retry_pool")):
        if not isinstance(raw, dict):
            # Structural integrity validation owns malformed-row handling.
            rows.append(raw)
            continue
        row = dict(raw)
        oid = _text(row.get("obligation_id"))
        reason = _text(row.get("block_reason") or row.get("reason_code")).upper()
        if oid and reason == _LEGACY_RETRY_REASON:
            legacy_ids.add(oid)
            row["legacy_block_reason"] = _LEGACY_RETRY_REASON
            row["block_reason"] = _ENGINE_ELIGIBILITY_TOKEN
            row.pop("reason_code", None)
        rows.append(row)

    if legacy_ids:
        plan["blocked_retry_pool"] = rows
        plan["legacy_retry_authority_receipt"] = {
            "schema_version": "qualibug.legacy-retry-authority.v1",
            "status": "ADAPTED_FOR_EXACT_ENGINE",
            "legacy_retry_count": len(legacy_ids),
            "legacy_retry_obligation_ids": sorted(legacy_ids),
            "source_reason": _LEGACY_RETRY_REASON,
            "temporary_engine_token": _ENGINE_ELIGIBILITY_TOKEN,
        }
    return plan, legacy_ids


def restore_legacy_retry_authority(
    obligation_plan: dict[str, Any],
    legacy_ids: set[str],
) -> dict[str, Any]:
    """Restore generic legacy reason only when no newer reason was learned."""
    plan = dict(obligation_plan) if isinstance(obligation_plan, dict) else {}
    if not legacy_ids:
        return plan

    rows: list[dict[str, Any]] = []
    restored_ids: list[str] = []
    for raw in _list(plan.get("blocked_retry_pool")):
        if not isinstance(raw, dict):
            rows.append(raw)
            continue
        row = dict(raw)
        oid = _text(row.get("obligation_id"))
        reason = _text(row.get("block_reason") or row.get("reason_code")).upper()
        if oid in legacy_ids and reason == _ENGINE_ELIGIBILITY_TOKEN:
            row["block_reason"] = _LEGACY_RETRY_REASON
            row.pop("reason_code", None)
            row.pop("legacy_block_reason", None)
            restored_ids.append(oid)
        rows.append(row)

    plan["blocked_retry_pool"] = rows
    receipt = dict(
        plan.get("legacy_retry_authority_receipt")
        if isinstance(plan.get("legacy_retry_authority_receipt"), dict)
        else {}
    )
    receipt.update({
        "schema_version": "qualibug.legacy-retry-authority.v1",
        "status": "RESTORED" if restored_ids else "CONSUMED_OR_RECLASSIFIED",
        "legacy_retry_count": len(legacy_ids),
        "restored_legacy_retry_count": len(restored_ids),
        "restored_legacy_retry_obligation_ids": sorted(restored_ids),
    })
    plan["legacy_retry_authority_receipt"] = receipt
    return plan


__all__ = [
    "prepare_legacy_retry_authority",
    "restore_legacy_retry_authority",
]
