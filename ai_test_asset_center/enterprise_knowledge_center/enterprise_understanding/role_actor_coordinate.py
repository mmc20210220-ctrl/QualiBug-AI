"""Resolve nested role-name actor coordinates once before authorization projection."""
from __future__ import annotations

import hashlib
import re
from typing import Any


RECEIPT_SCHEMA = "qualibug.role-actor-coordinate-receipt.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _maximal_source_roles(raw: str, actors: list[str]) -> list[str]:
    spans = {
        actor: [
            (match.start(), match.end())
            for match in re.finditer(re.escape(actor), raw)
        ]
        for actor in actors
    }
    selected: list[str] = []
    for actor in actors:
        actor_spans = spans.get(actor) or []
        if not actor_spans:
            selected.append(actor)
            continue
        fully_nested = all(
            any(
                other != actor
                and len(other) > len(actor)
                and left >= other_left
                and right <= other_right
                for other in actors
                for other_left, other_right in spans.get(other, [])
            )
            for left, right in actor_spans
        )
        if not fully_nested:
            selected.append(actor)
    return selected


def disambiguate_role_actor_coordinates(asset: dict[str, Any]) -> dict[str, Any]:
    """Drop only role mentions fully nested inside longer source role mentions."""
    ledger = _dict(asset.get("business_fact_ledger"))
    receipts = [
        dict(row)
        for row in _list(asset.get("role_actor_coordinate_receipts"))
        if isinstance(row, dict)
    ]
    receipt_by_id = {
        _text(row.get("receipt_id")): row
        for row in receipts
        if _text(row.get("receipt_id"))
    }
    for fact in _list(ledger.get("items")):
        if not isinstance(fact, dict) or _text(fact.get("status")).upper() != "ACCEPTED":
            continue
        subject = _dict(fact.get("subject"))
        actors = list(dict.fromkeys(
            _text(value)
            for value in _list(subject.get("actor_refs"))
            if _text(value) and _text(value) != "系统"
        ))
        raw = _text(fact.get("raw_statement"))
        if len(actors) < 2 or not raw:
            continue
        resolved = _maximal_source_roles(raw, actors)
        if resolved == actors:
            continue
        subject["actor_ref_candidates"] = actors
        subject["actor_refs"] = resolved
        fact["subject"] = subject
        receipt = {
            "schema_version": RECEIPT_SCHEMA,
            "fact_id": _text(fact.get("fact_id")),
            "candidate_actor_refs": actors,
            "resolved_actor_refs": resolved,
            "resolution": "LONGEST_NON_NESTED_SOURCE_ROLE_OCCURRENCE",
            "automatic_similarity_inference_allowed": False,
        }
        receipt["receipt_id"] = _stable_id(
            "role_actor_coordinate",
            receipt["fact_id"],
            *actors,
            *resolved,
        )
        receipt_by_id[receipt["receipt_id"]] = receipt
    asset["role_actor_coordinate_receipts"] = sorted(
        receipt_by_id.values(), key=lambda row: _text(row.get("receipt_id"))
    )
    governance = _dict(asset.get("governance"))
    governance["nested_role_actor_coordinate_uses_source_positions"] = True
    governance["role_name_substring_authorization_inference_allowed"] = False
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "disambiguate_role_actor_coordinates"]
