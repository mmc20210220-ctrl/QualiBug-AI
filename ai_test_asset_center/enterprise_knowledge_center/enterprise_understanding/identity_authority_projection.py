"""Project existing operator authority decisions into identity-resolution receipts.

The durable authority ledger remains the single operator decision system. This
module does not create another ledger or decision API.
"""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, stable_id, text, unique_text


def _is_identity_conflict(conflict: dict[str, Any]) -> bool:
    kind = text(conflict.get("kind")).upper()
    reason = text(conflict.get("reason_code")).upper()
    if "TERM_ALIAS" in kind or "IDENTITY" in kind:
        return True
    if "TERM_ALIAS" in reason or "IDENTITY" in reason:
        return True
    return any(
        text(as_dict(row).get("kind")).upper() == "TERM_ALIAS"
        for row in [*as_list(conflict.get("facts")), *as_list(conflict.get("evidence"))]
        if isinstance(row, dict)
    )


def project_identity_authority_receipt(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    conflicts = [
        row
        for row in as_list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict) and _is_identity_conflict(row)
    ]
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for conflict in conflicts:
        authority = as_dict(conflict.get("authority_decision"))
        item = {
            "conflict_id": conflict.get("conflict_id"),
            "status": conflict.get("status"),
            "decision_id": authority.get("decision_id"),
            "action": authority.get("action"),
            "selected_fact_id": authority.get("selected_fact_id"),
            "selected_source_ref": authority.get("selected_source_ref"),
            "participant_fact_ids": unique_text(
                as_dict(row).get("fact_id")
                for row in [
                    *as_list(conflict.get("facts")),
                    *as_list(conflict.get("evidence")),
                ]
                if isinstance(row, dict)
            ),
        }
        if text(conflict.get("status")).upper() in {
            "RESOLVED",
            "SUPERSEDED",
            "DISMISSED",
        }:
            resolved.append(item)
        else:
            unresolved.append(item)

    receipt = {
        "schema": "qualibug.enterprise-identity-authority-projection-receipt.v1",
        "receipt_id": stable_id(
            "identity_authority_receipt",
            [row.get("conflict_id") for row in resolved],
            [row.get("conflict_id") for row in unresolved],
        ),
        "authority_ledger_schema": "qualibug.operator-authority-decision-ledger.v1",
        "new_identity_decision_ledger_created": False,
        "identity_conflict_count": len(conflicts),
        "resolved_identity_conflict_count": len(resolved),
        "unresolved_identity_conflict_count": len(unresolved),
        "applied_decisions": resolved,
        "unresolved_decisions": unresolved,
        "participant_set_drift_fails_closed": True,
        "automatic_authority_pick_allowed": False,
    }
    result["authority_decision_projection"] = receipt
    asset["enterprise_identity_authority_projection_receipt"] = receipt
    asset["enterprise_identity_resolution"] = result
    return result
