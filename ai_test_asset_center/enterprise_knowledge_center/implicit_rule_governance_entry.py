"""Canonical command ingress for implicit-rule governance.

Decision commands are ephemeral inputs; the append-only decision ledger is persistence.
This adapter removes exact command replays already present in the ledger, preserves
same-ID/different-content commands so the ledger can report tampering, and ensures
``runtime_evidence_refs`` can resolve only per-rule evidence IDs rather than a batch
receipt ID.  It delegates all lifecycle and decision semantics to the existing
``implicit_rule_governance`` authority.
"""
from __future__ import annotations

import json
from typing import Any

from .implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection as _enrich,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _decision_content(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": _text(row.get("decision_id")),
        "rule_ref": _text(row.get("rule_ref") or row.get("rule_id")),
        "decision_type": _text(row.get("decision_type")).upper(),
        "counterexample_classification": _text(
            row.get("counterexample_classification")
        ).upper(),
        "authority_type": _text(row.get("authority_type")).lower(),
        "decided_by": dict(_dict(row.get("decided_by"))),
        "reason": _text(row.get("reason")),
        "evidence_refs": sorted(
            {_text(value) for value in _list(row.get("evidence_refs")) if _text(value)}
        ),
        "runtime_evidence_refs": sorted(
            {
                _text(value)
                for value in _list(row.get("runtime_evidence_refs"))
                if _text(value)
            }
        ),
        "replacement_rule_ref": _text(row.get("replacement_rule_ref")),
    }


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _filter_replayed_commands(asset: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = _dict(asset.get("implicit_rule_authority_decision_ledger"))
    persisted = {
        _text(row.get("decision_id")): _decision_content(row)
        for row in _list(ledger.get("items"))
        if isinstance(row, dict) and _text(row.get("decision_id"))
    }
    result: list[dict[str, Any]] = []
    for raw in _list(asset.get("implicit_rule_authority_decisions")):
        if not isinstance(raw, dict):
            continue
        decision_id = _text(raw.get("decision_id"))
        prior = persisted.get(decision_id)
        if prior is not None and _canonical(prior) == _canonical(_decision_content(raw)):
            continue
        result.append(dict(raw))
    return result


def _runtime_receipt_without_batch_identity(value: Any) -> dict[str, Any]:
    root = dict(_dict(value))
    if root:
        root.pop("receipt_id", None)
    return root


def enrich_asset_with_governed_implicit_rule_projection(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Normalize command/evidence ingress, then delegate to governance authority."""

    working = dict(asset)
    working["implicit_rule_authority_decisions"] = _filter_replayed_commands(asset)
    for field in (
        "implicit_rule_runtime_evolution",
        "latest_implicit_rule_runtime_evolution",
    ):
        if field in working:
            working[field] = _runtime_receipt_without_batch_identity(working.get(field))
    result = _enrich(working)
    result["implicit_rule_governance_ingress_receipt"] = {
        "schema_version": "qualibug.implicit-rule-governance-ingress.v1",
        "submitted_decision_count": len(
            _list(asset.get("implicit_rule_authority_decisions"))
        ),
        "new_or_conflicting_decision_count": len(
            _list(working.get("implicit_rule_authority_decisions"))
        ),
        "exact_replayed_decisions_ignored": (
            len(_list(asset.get("implicit_rule_authority_decisions")))
            - len(_list(working.get("implicit_rule_authority_decisions")))
        ),
        "runtime_evidence_ref_requires_per_rule_evidence_id": True,
        "batch_receipt_id_is_not_runtime_evidence_id": True,
    }
    return result


__all__ = ["enrich_asset_with_governed_implicit_rule_projection"]
