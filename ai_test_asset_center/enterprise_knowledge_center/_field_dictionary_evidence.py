"""Truthful normalized evidence for structured field declarations.

Field dictionary coordinates are parser projections, not verbatim source quotes.
This module installs a narrow compatibility layer over the existing parser and
cross-document conflict detector so normalized declarations retain both sides
without being mislabeled as exact source text.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Callable

_NORMALIZED_KIND = "NORMALIZED_STRUCTURED_DECLARATION"
_NORMALIZED_DERIVATION = "normalized_field_dictionary_projection"
_REQUIRED_RE = re.compile(r"(?:^|;\s*)required=(?:true|false)(?:;|$)", re.I)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _projection(row: dict[str, Any], *, include_required: bool) -> str:
    bits: list[str] = []
    table = _text(row.get("table"))
    field = _text(row.get("field"))
    field_type = _text(row.get("type"))
    if table and table != "default":
        bits.append(f"table={table}")
    if field:
        bits.append(f"field={field}")
    if include_required:
        bits.append(f"required={'true' if row.get('required') is True else 'false'}")
    if field_type:
        bits.append(f"type={field_type}")
    return "; ".join(bits)[:320]


def normalize_field_dictionary_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Move generated field coordinates into the normalized evidence contract."""
    if not isinstance(row, dict):
        return row
    if _text(row.get("evidence_kind")) == "EXACT_SOURCE_QUOTE":
        return row

    generated_hint = _text(row.get("source_excerpt") or row.get("quote"))
    if not generated_hint:
        return row
    include_required = bool(_REQUIRED_RE.search(generated_hint))
    normalized = _projection(row, include_required=include_required)
    if not normalized or generated_hint != normalized:
        return row

    row["normalized_evidence"] = normalized
    row["evidence_kind"] = _NORMALIZED_KIND
    row["evidence_derivation"] = _NORMALIZED_DERIVATION
    row.pop("quote", None)
    row.pop("source_excerpt", None)
    return row


def _technical_declaration_fact(
    *,
    kind: str,
    source_id: str,
    entity: str,
    statement: str,
    locator: str = "",
    details: dict[str, Any] | None = None,
    quote: str = "",
    normalized_evidence: str = "",
    evidence_kind: str = "",
    evidence_derivation: str = "",
) -> dict[str, Any]:
    source = _text(source_id)
    if not source:
        raise ValueError("technical_declaration_source_id_required")
    material = f"{kind}|{source_id}|{entity}|{statement}"
    fact_id = "techfact:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    evidence_quote = _text(quote)[:500]
    evidence_locator = _text(locator)
    normalized = _text(normalized_evidence)[:500]
    kind_value = _text(evidence_kind)
    derivation_value = _text(evidence_derivation)
    span = {
        "source_id": source,
        "locator": evidence_locator,
        "quote": evidence_quote,
        "quote_hash": (
            hashlib.sha256(evidence_quote.encode("utf-8")).hexdigest()
            if evidence_quote
            else ""
        ),
        "derivation": "structured_source_declaration_projection",
    }
    if normalized:
        span["normalized_evidence"] = normalized
    if kind_value:
        span["evidence_kind"] = kind_value
    if derivation_value:
        span["evidence_derivation"] = derivation_value
    return {
        "fact_id": fact_id,
        "kind": kind,
        "status": "ACCEPTED",
        "source_id": source,
        "source_locator": evidence_locator,
        "raw_statement": statement,
        "statement": statement,
        "entity": entity,
        "technical_declaration": dict(details or {}),
        "formal_promotion_allowed": False,
        "source_spans": [span],
    }


def _field_required_conflicts(
    field_dictionary: list[dict[str, Any]],
    make_conflict: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    field_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in field_dictionary:
        if not isinstance(field, dict):
            continue
        table = _text(field.get("table")).lower()
        field_name = _text(field.get("field")).lower()
        if table and field_name:
            field_by_name[f"{table}:{field_name}"].append(field)

    conflicts: list[dict[str, Any]] = []
    for key, entries in field_by_name.items():
        required_true = [row for row in entries if row.get("required") is True]
        required_false = [row for row in entries if row.get("required") is False]
        if not required_true or not required_false:
            continue

        participants: list[dict[str, Any]] = []
        for declaration, required in [
            *((row, True) for row in required_true),
            *((row, False) for row in required_false),
        ]:
            source_id = _text(declaration.get("source_id"))
            if not source_id:
                continue
            participants.append(
                _technical_declaration_fact(
                    kind="TECHNICAL_FIELD_DECLARATION",
                    source_id=source_id,
                    entity=key,
                    statement=(
                        f"Field '{key}' declared required={str(required).lower()}"
                    ),
                    locator=_text(
                        declaration.get("source_locator")
                        or declaration.get("locator")
                        or declaration.get("field_id")
                    ),
                    details={
                        "required": required,
                        "table": declaration.get("table"),
                        "field": declaration.get("field"),
                    },
                    quote=_text(declaration.get("quote")),
                    normalized_evidence=_text(
                        declaration.get("normalized_evidence")
                    ),
                    evidence_kind=_text(declaration.get("evidence_kind")),
                    evidence_derivation=_text(
                        declaration.get("evidence_derivation")
                    ),
                )
            )

        participants = list(
            {
                _text(row.get("fact_id")): row
                for row in participants
                if _text(row.get("fact_id"))
            }.values()
        )
        source_ids = {
            _text(row.get("source_id"))
            for row in participants
            if _text(row.get("source_id"))
        }
        required_values = {
            bool((row.get("technical_declaration") or {}).get("required"))
            for row in participants
        }
        if len(source_ids) < 2 or required_values != {True, False}:
            continue

        conflict = make_conflict(
            "FIELD_REQUIRED_MISMATCH",
            participants,
            (
                f"Field '{key}' is declared required in one source but "
                "nullable/optional in another"
            ),
            entity=key,
        )
        participant_by_fact_id = {
            _text(row.get("fact_id")): row
            for row in participants
            if _text(row.get("fact_id"))
        }
        for collection_name in ("facts", "evidence"):
            for row in conflict.get(collection_name) or []:
                if not isinstance(row, dict):
                    continue
                participant = participant_by_fact_id.get(_text(row.get("fact_id")))
                if not participant:
                    continue
                span = (participant.get("source_spans") or [{}])[0]
                if not isinstance(span, dict):
                    continue
                for evidence_field in (
                    "normalized_evidence",
                    "evidence_kind",
                    "evidence_derivation",
                ):
                    value = span.get(evidence_field)
                    if value not in (None, ""):
                        row[evidence_field] = value
                row["quote"] = _text(span.get("quote"))
        conflict["conflict_type"] = "field_required_mismatch"
        conflict["source_a"] = sorted(source_ids)[0]
        conflict["source_b"] = sorted(source_ids)[1]
        conflict["detail"] = conflict["reason"]
        conflicts.append(conflict)
    return conflicts


def install_field_dictionary_evidence_contract() -> Callable[..., list[dict[str, Any]]]:
    """Install the normalized field evidence contract exactly once."""
    from . import _api, _parsing
    from . import _chinese_business_conflicts as conflicts_module

    current_parser = _parsing._field_dictionary_entries
    if not getattr(current_parser, "_qualibug_normalized_field_evidence", False):
        original_parser = current_parser

        def field_dictionary_entries(
            text: str,
            payload: Any,
            source_id: str,
        ) -> list[dict[str, Any]]:
            return [
                normalize_field_dictionary_entry(row)
                for row in original_parser(text, payload, source_id)
            ]

        field_dictionary_entries._qualibug_normalized_field_evidence = True  # type: ignore[attr-defined]
        field_dictionary_entries._qualibug_original = original_parser  # type: ignore[attr-defined]
        _parsing._field_dictionary_entries = field_dictionary_entries

    current_detector = _api._detect_cross_document_conflicts
    if not getattr(current_detector, "_qualibug_normalized_field_evidence", False):
        original_detector = current_detector

        def detect_cross_document_conflicts(
            field_dictionary: list[dict[str, Any]],
            rules: list[dict[str, Any]],
            interfaces: list[dict[str, Any]],
            permissions: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            field_conflicts = _field_required_conflicts(
                field_dictionary,
                conflicts_module.make_authority_eligible_conflict,
            )
            return [
                *field_conflicts,
                *original_detector([], rules, interfaces, permissions),
            ]

        detect_cross_document_conflicts._qualibug_normalized_field_evidence = True  # type: ignore[attr-defined]
        detect_cross_document_conflicts._qualibug_original = original_detector  # type: ignore[attr-defined]
        _api._detect_cross_document_conflicts = detect_cross_document_conflicts

    _api._technical_declaration_fact = _technical_declaration_fact
    return _api._detect_cross_document_conflicts


__all__ = [
    "install_field_dictionary_evidence_contract",
    "normalize_field_dictionary_entry",
]
