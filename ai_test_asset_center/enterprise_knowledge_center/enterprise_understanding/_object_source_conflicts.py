"""Detect source-backed business-object declaration authority conflicts.

This module does not choose a source.  It projects conflicting source declarations
into the repository's existing SELECT_FACT / LEAVE_UNRESOLVED authority domain so
operator decisions, participant drift checks, and audit receipts remain single-source.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .._chinese_business_conflicts import (
    TECHNICAL_CONFLICT_SCHEMA,
    make_authority_eligible_conflict,
)
from ._object_role_evidence import comparison_key
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

OBJECT_DECLARATION_ALIAS_CONFLICT = "BUSINESS_OBJECT_DECLARATION_ALIAS_CONFLICT"


def object_declaration_fact_id(row: dict[str, Any]) -> str:
    """Return the stable authority participant id for one source declaration."""

    evidence = [
        item for item in as_list(row.get("evidence")) if isinstance(item, dict)
    ]
    first = evidence[0] if evidence else {}
    return stable_id(
        "business_object_source_declaration_fact",
        comparison_key(row.get("canonical_label")),
        text(first.get("source_id")),
        text(first.get("source_locator")),
        sorted(comparison_key(value) for value in as_list(row.get("labels"))),
        text(row.get("authority")),
    )


def _participant(row: dict[str, Any], alias: str) -> dict[str, Any]:
    evidence = dedupe_evidence(
        item for item in as_list(row.get("evidence")) if isinstance(item, dict)
    )
    first = evidence[0] if evidence else {}
    canonical = text(row.get("canonical_label"))
    statement = text(first.get("quote")) or f"{canonical} aliases {alias}"
    source_id = text(first.get("source_id"))
    source_locator = text(first.get("source_locator"))
    fact_id = object_declaration_fact_id(row)
    return {
        "fact_id": fact_id,
        "kind": "BUSINESS_OBJECT_SOURCE_DECLARATION",
        "status": "ACCEPTED",
        "source_id": source_id,
        "source_locator": source_locator,
        "raw_statement": statement,
        "statement": statement,
        "entity": alias,
        "object_declaration": {
            "declaration_id": text(row.get("declaration_id")),
            "canonical_label": canonical,
            "labels": unique_text(as_list(row.get("labels"))),
            "authority": text(row.get("authority")),
        },
        "formal_promotion_allowed": False,
        "source_spans": [
            {
                "source_id": source_id,
                "locator": source_locator,
                "quote": text(first.get("quote")),
                "quote_hash": text(first.get("quote_hash")),
                "document_version": text(
                    first.get("document_version")
                    or row.get("document_version")
                ),
                "derivation": "business_object_source_declaration_conflict",
            }
        ],
    }


def detect_business_object_source_conflicts(
    declaration_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect one exact alias being declared for different canonical objects.

    Conflict eligibility requires independent source ids and at least two distinct
    canonical labels.  Same-source parser duplication and cross-source agreement
    are deduplicated rather than escalated.
    """

    by_alias: dict[str, list[dict[str, Any]]] = {}
    alias_labels: dict[str, str] = {}
    for raw in declaration_rows:
        if not isinstance(raw, dict):
            continue
        canonical = text(raw.get("canonical_label"))
        canonical_key = comparison_key(canonical)
        evidence = [
            row for row in as_list(raw.get("evidence")) if isinstance(row, dict)
        ]
        evidence_by_source: dict[str, list[dict[str, Any]]] = {}
        for evidence_row in evidence:
            source_id = text(evidence_row.get("source_id"))
            if source_id:
                evidence_by_source.setdefault(source_id, []).append(evidence_row)
        if not canonical_key or not evidence_by_source:
            continue
        for source_id, source_evidence in evidence_by_source.items():
            source_row = dict(raw)
            source_row["evidence"] = source_evidence
            source_row["declaration_id"] = stable_id(
                "source_object_declaration_participant",
                raw.get("declaration_id"),
                canonical_key,
                source_id,
            )
            for label in as_list(raw.get("labels")):
                label_key = comparison_key(label)
                if not label_key or label_key == canonical_key:
                    continue
                by_alias.setdefault(label_key, []).append(source_row)
                alias_labels.setdefault(label_key, text(label))

    conflicts: list[dict[str, Any]] = []
    for alias_key, rows in sorted(by_alias.items()):
        canonical_keys = {
            comparison_key(row.get("canonical_label")) for row in rows
        }
        source_ids = {
            text(as_dict(as_list(row.get("evidence"))[0]).get("source_id"))
            for row in rows
            if as_list(row.get("evidence"))
        }
        if len(canonical_keys) < 2 or len({value for value in source_ids if value}) < 2:
            continue
        alias = alias_labels.get(alias_key, alias_key)
        participants = list(
            {
                object_declaration_fact_id(row): _participant(row, alias)
                for row in rows
            }.values()
        )
        if len(participants) < 2:
            continue
        conflict = make_authority_eligible_conflict(
            OBJECT_DECLARATION_ALIAS_CONFLICT,
            participants,
            (
                f"business-object label '{alias}' is declared for multiple canonical "
                "objects across independent sources"
            ),
            schema=TECHNICAL_CONFLICT_SCHEMA,
            entity=alias,
        )
        conflict.update(
            {
                "conflict_type": "business_object_declaration_alias_conflict",
                "conflict_domain": "BUSINESS_OBJECT_RECOGNITION",
                "blocks_formal_understanding": True,
                "automatic_winner_selected": False,
                "object_declaration_participants": [
                    {
                        "fact_id": row["fact_id"],
                        "source_id": row["source_id"],
                        "source_locator": row["source_locator"],
                        **as_dict(row.get("object_declaration")),
                    }
                    for row in participants
                ],
            }
        )
        conflicts.append(conflict)
    return conflicts


def business_object_source_conflicts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Return projected object-declaration conflicts, resolved or unresolved."""

    return [
        dict(row)
        for row in as_list(asset.get("cross_document_conflicts"))
        if isinstance(row, dict)
        and text(row.get("kind")) == OBJECT_DECLARATION_ALIAS_CONFLICT
    ]


def project_business_object_source_conflicts(asset: dict[str, Any]) -> dict[str, Any]:
    """Project object declaration conflicts into the existing authority ledger domain."""

    from ._object_source_declarations import source_object_declarations

    result = asset
    detected = detect_business_object_source_conflicts(
        source_object_declarations(result)
    )
    prior = [
        dict(row)
        for row in as_list(result.get("cross_document_conflicts"))
        if isinstance(row, dict)
        and text(row.get("kind")) != OBJECT_DECLARATION_ALIAS_CONFLICT
    ]
    result["cross_document_conflicts"] = [*prior, *deepcopy(detected)]
    receipt = {
        "schema": "qualibug.business-object-source-conflict-projection.v1",
        "conflict_count": len(detected),
        "conflict_ids": [row.get("conflict_id") for row in detected],
        "uses_existing_operator_authority_ledger": True,
        "parallel_authority_ledger_created": False,
        "automatic_winner_selected": False,
        "disallowed_authority_signals": [
            "recency",
            "filename",
            "document_order",
            "model_confidence",
            "industry_default",
        ],
    }
    result["business_object_source_conflict_projection"] = receipt
    return result


__all__ = [
    "OBJECT_DECLARATION_ALIAS_CONFLICT",
    "business_object_source_conflicts",
    "detect_business_object_source_conflicts",
    "object_declaration_fact_id",
    "project_business_object_source_conflicts",
]
