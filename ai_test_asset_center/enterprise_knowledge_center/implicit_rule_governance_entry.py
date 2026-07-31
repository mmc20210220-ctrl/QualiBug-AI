"""Canonical command and source ingress for implicit-rule governance.

Decision commands are ephemeral inputs; the append-only decision ledger is persistence.
This adapter removes exact command replays already present in the ledger, preserves
same-ID/different-content commands so the ledger can report tampering, and ensures
``runtime_evidence_refs`` can resolve only per-rule evidence IDs rather than a batch
receipt ID.

Source lifecycle also needs one membership authority.  Enterprise assets may expose the
same source through registry, inventory and presentation views.  The highest existing
view determines active membership; lower views may fill missing immutable hash/version
fields but may never re-add a source removed from that authority.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .implicit_rule_authority_decisions import _decision_id
from .implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection as _enrich,
)

_SOURCE_FIELDS = (
    "enterprise_source_registry",
    "source_registry",
    "source_inventory",
    "sources",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _decision_content(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": _decision_id(row),
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
        _decision_id(row): _decision_content(row)
        for row in _list(ledger.get("items"))
        if isinstance(row, dict) and _decision_id(row)
    }
    result: list[dict[str, Any]] = []
    for raw in _list(asset.get("implicit_rule_authority_decisions")):
        if not isinstance(raw, dict):
            continue
        decision_id = _decision_id(raw)
        prior = persisted.get(decision_id)
        if prior is not None and _canonical(prior) == _canonical(_decision_content(raw)):
            continue
        result.append(dict(raw))
    return result


def _iter_source_rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                yield dict(row)
        return
    if not isinstance(value, dict):
        return
    assets = value.get("assets")
    if isinstance(assets, dict):
        for source_id, raw in assets.items():
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("source_id", source_id)
            yield row
        return
    if isinstance(assets, list):
        for row in assets:
            if isinstance(row, dict):
                yield dict(row)
        return
    for source_id, raw in value.items():
        if not isinstance(raw, dict):
            continue
        if not any(
            key in raw
            for key in (
                "source_id",
                "asset_id",
                "latest_source_hash",
                "source_hash",
                "content_hash",
                "latest_version_id",
                "source_version_id",
                "version_id",
                "versions",
            )
        ):
            continue
        row = dict(raw)
        row.setdefault("source_id", source_id)
        yield row


def _source_id(row: dict[str, Any]) -> str:
    return _text(row.get("source_id") or row.get("asset_id") or row.get("id"))


def _source_hash(row: dict[str, Any]) -> str:
    manifest = _dict(row.get("manifest"))
    return _text(
        row.get("latest_source_hash")
        or row.get("source_hash")
        or row.get("content_hash")
        or row.get("text_hash")
        or row.get("hash")
        or manifest.get("source_hash")
    ).removeprefix("sha256:")


def _source_version_id(row: dict[str, Any]) -> str:
    manifest = _dict(row.get("manifest"))
    return _text(
        row.get("latest_version_id")
        or row.get("source_version_id")
        or row.get("version_id")
        or manifest.get("source_version_id")
        or manifest.get("version_id")
    )


def _canonical_source_rows(asset: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    views = {
        field: [row for row in _iter_source_rows(asset.get(field))]
        for field in _SOURCE_FIELDS
        if field in asset and isinstance(asset.get(field), (list, dict))
    }
    authority_field = next((field for field in _SOURCE_FIELDS if field in views), "")
    if not authority_field:
        return [], ""

    authority_rows = views[authority_field]
    supplemental_by_id: dict[str, list[dict[str, Any]]] = {}
    for field in _SOURCE_FIELDS:
        for row in views.get(field, []):
            source_id = _source_id(row)
            if source_id:
                supplemental_by_id.setdefault(source_id, []).append(row)

    canonical_rows: list[dict[str, Any]] = []
    for authority_row in authority_rows:
        source_id = _source_id(authority_row)
        if not source_id:
            continue
        row = dict(authority_row)
        if not _source_hash(row):
            supplement = next(
                (
                    candidate
                    for candidate in supplemental_by_id.get(source_id, [])
                    if _source_hash(candidate)
                ),
                {},
            )
            if supplement:
                row["source_hash"] = _source_hash(supplement)
        if not _source_version_id(row):
            supplement = next(
                (
                    candidate
                    for candidate in supplemental_by_id.get(source_id, [])
                    if _source_version_id(candidate)
                ),
                {},
            )
            if supplement:
                row["source_version_id"] = _source_version_id(supplement)
        row["source_id"] = source_id
        canonical_rows.append(row)
    return canonical_rows, authority_field


def _runtime_receipt_without_batch_identity(value: Any) -> dict[str, Any]:
    root = dict(_dict(value))
    if root:
        root.pop("receipt_id", None)
    return root


def enrich_asset_with_governed_implicit_rule_projection(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Normalize command/source/evidence ingress, then delegate to governance."""

    submitted_commands = [
        dict(row)
        for row in _list(asset.get("implicit_rule_authority_decisions"))
        if isinstance(row, dict)
    ]
    filtered_commands = _filter_replayed_commands(asset)
    runtime_receipts = {
        field: dict(_dict(asset.get(field)))
        for field in (
            "implicit_rule_runtime_evolution",
            "latest_implicit_rule_runtime_evolution",
        )
        if _dict(asset.get(field))
    }
    original_source_views = {
        field: asset.get(field)
        for field in _SOURCE_FIELDS
        if field in asset
    }
    canonical_sources, source_authority_field = _canonical_source_rows(asset)

    working = dict(asset)
    working["implicit_rule_authority_decisions"] = filtered_commands
    for field, receipt in runtime_receipts.items():
        working[field] = _runtime_receipt_without_batch_identity(receipt)
    if source_authority_field:
        # Lifecycle reads all four legacy/current views. Give every view the same
        # authority membership so a presentation view cannot re-add a deactivated ID.
        working["enterprise_source_registry"] = {
            "assets": {
                _source_id(row): dict(row)
                for row in canonical_sources
                if _source_id(row)
            }
        }
        working["source_registry"] = {
            "assets": {
                _source_id(row): dict(row)
                for row in canonical_sources
                if _source_id(row)
            }
        }
        working["source_inventory"] = [dict(row) for row in canonical_sources]
        working["sources"] = [dict(row) for row in canonical_sources]

    result = _enrich(working)
    # Governance sees normalized views; persistence keeps each original product view.
    for field in _SOURCE_FIELDS:
        if field in original_source_views:
            result[field] = original_source_views[field]
        else:
            result.pop(field, None)
    # Governance validation sees only per-rule evidence IDs, while persistence keeps the
    # full immutable batch receipt and its content-addressed receipt_id.
    for field, receipt in runtime_receipts.items():
        result[field] = receipt

    result["implicit_rule_governance_ingress_receipt"] = {
        "schema_version": "qualibug.implicit-rule-governance-ingress.v1",
        "submitted_decision_count": len(submitted_commands),
        "new_or_conflicting_decision_count": len(filtered_commands),
        "exact_replayed_decisions_ignored": (
            len(submitted_commands) - len(filtered_commands)
        ),
        "runtime_evidence_ref_requires_per_rule_evidence_id": True,
        "batch_receipt_id_is_not_runtime_evidence_id": True,
        "runtime_batch_receipt_preserved": bool(runtime_receipts),
        "derived_decision_identity_used_for_replay_detection": True,
        "source_membership_authority_field": source_authority_field,
        "canonical_active_source_count": len(canonical_sources),
        "lower_source_views_may_readd_deactivated_sources": False,
        "lower_source_views_may_fill_missing_version_identity": True,
        "original_source_views_preserved": True,
    }
    return result


__all__ = ["enrich_asset_with_governed_implicit_rule_projection"]
