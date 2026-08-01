"""Prepare PRD-only narrative declarations for the existing object gate."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._object_narrative_declarations import source_narrative_object_declarations
from ._object_role_evidence import comparison_key, object_slot_mentions
from .schema import as_dict, as_list, evidence_from_fact, stable_id, text, unique_text


def _filter_slot(slot: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    copied = deepcopy(slot)
    mentions = unique_text([
        *as_list(slot.get("entity_refs")),
        *as_list(slot.get("entity_mentions")),
    ])
    allowed = [value for value in mentions if comparison_key(value) in labels]
    rejected = [value for value in mentions if comparison_key(value) not in labels]
    copied["raw_entity_mentions"] = unique_text([
        *as_list(slot.get("raw_entity_mentions")),
        *mentions,
    ])
    copied["entity_refs"] = allowed
    copied["entity_mentions"] = allowed
    copied["business_object_rejected_mentions"] = unique_text([
        *as_list(slot.get("business_object_rejected_mentions")),
        *rejected,
    ])
    return copied


def prepare_narrative_declared_asset(
    asset: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project conservative narrative declarations without changing identity authority."""

    declarations = source_narrative_object_declarations(asset)
    if not declarations:
        return asset, {"declared_labels": {}, "surface_parents": {}}

    prepared = deepcopy(asset)
    labels: dict[str, str] = {}
    surfaces: dict[str, dict[str, Any]] = {}
    declared_objects: list[dict[str, Any]] = []
    for declaration in declarations:
        values = unique_text(as_list(declaration.get("labels")))
        if not values:
            continue
        canonical = text(declaration.get("canonical_label")) or values[0]
        evidence = [
            dict(row)
            for row in as_list(declaration.get("evidence"))
            if isinstance(row, dict)
        ]
        for value in values:
            key = comparison_key(value)
            if key:
                labels[key] = value
        pending_parents = unique_text(
            as_list(declaration.get("identity_pending_parent_labels"))
        )
        if pending_parents:
            surfaces[comparison_key(canonical)] = {
                "label": canonical,
                "parents": [
                    comparison_key(value)
                    for value in pending_parents
                    if comparison_key(value)
                ],
                "evidence": evidence,
            }
        declared_objects.append({
            "object_id": text(declaration.get("declaration_id"))
            or stable_id("source_narrative_object", canonical, values),
            "object": canonical,
            "name": canonical,
            "aliases": [
                value
                for value in values
                if comparison_key(value) != comparison_key(canonical)
            ],
            "evidence": evidence,
            "source_id": text(evidence[0].get("source_id") if evidence else ""),
            "source_locator": text(
                evidence[0].get("source_locator") if evidence else ""
            ) or text(declaration.get("declaration_id")),
            "source_excerpt": text(evidence[0].get("quote") if evidence else ""),
            "source": "source_object_declaration",
            "derivation": text(declaration.get("authority")),
            "source_declaration_authorities": unique_text([
                declaration.get("authority"),
                *as_list(declaration.get("authorities")),
            ]),
            "surface_suffix_discovery_allowed": False,
            "surface_prefix_discovery_allowed": False,
        })

    existing = [
        dict(row)
        for row in as_list(prepared.get("business_objects"))
        if isinstance(row, dict)
    ]
    retained = [
        row
        for row in existing
        if any(
            comparison_key(value) in labels
            for value in [
                row.get("object") or row.get("name"),
                *as_list(row.get("aliases")),
            ]
            if comparison_key(value)
        )
    ]
    prepared["business_objects"] = [*retained, *declared_objects]

    rejected: list[dict[str, Any]] = []
    ledger = dict(as_dict(prepared.get("business_fact_ledger")))
    items: list[dict[str, Any]] = []
    for raw in as_list(ledger.get("items")):
        if not isinstance(raw, dict):
            continue
        fact = deepcopy(raw)
        if text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}:
            evidence = evidence_from_fact(fact)
            for side in ("subject", "object"):
                for mention in object_slot_mentions(fact, side):
                    key = comparison_key(mention)
                    if key and key not in labels:
                        rejected.append({
                            "fact_id": text(fact.get("fact_id")),
                            "label": text(mention),
                            "comparison_key": key,
                            "reason_code": (
                                "UNDECLARED_OBJECT_SLOT_WHEN_NARRATIVE_DECLARATIONS_EXIST"
                            ),
                            "evidence": evidence,
                            "roles": [f"BUSINESS_FACT_{side.upper()}"],
                        })
                fact[side] = _filter_slot(as_dict(fact.get(side)), labels)
        items.append(fact)
    if ledger:
        ledger["items"] = items
        prepared["business_fact_ledger"] = ledger

    return prepared, {
        "declared_labels": labels,
        "declaration_surface_modes": {
            key: {"suffix": False, "prefix": False} for key in labels
        },
        "surface_parents": {
            key: list(row["parents"]) for key, row in surfaces.items()
        },
        "rejected_fact_mentions": rejected,
    }


__all__ = ["prepare_narrative_declared_asset"]
