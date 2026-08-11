"""Prepare explicit source declarations for the existing object recognition gate."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._object_role_evidence import comparison_key, object_slot_mentions
from ._object_source_declarations import source_object_declarations
from ._object_source_surfaces import collect_source_attested_object_surfaces
from .schema import (
    as_dict,
    as_list,
    clone_asset_for_understanding_projection,
    evidence_from_fact,
    stable_id,
    text,
    unique_text,
)


def _direct_surface_parents(
    fact: dict[str, Any], label: Any, declarations: dict[str, str]
) -> list[str]:
    """Return parents only for direct, behavior-bound object surfaces.

    The existing candidate collector already owns conservative source suffix
    discovery.  This bridge only covers two source structures it cannot express:
    a shared namespace prefix (``库存`` for two declared inventory entities) and
    a direct qualified object name (``退货记录`` for declared ``退货``).
    Parser fragments and atomic projections are never eligible.
    """

    key = comparison_key(label)
    if not key or key in declarations or text(fact.get("parent_fact_ref")):
        return []
    action = as_dict(fact.get("action"))
    if not text(action.get("canonical") or action.get("raw")):
        return []
    primary_object = any(
        text(claim.get("claim_type")) == "PRIMARY_OPERATION"
        and key in {comparison_key(value) for value in as_list(claim.get("object_refs"))}
        for claim in as_list(fact.get("claims"))
        if isinstance(claim, dict)
    )
    if not primary_object:
        return []

    extensions = [
        parent for parent in declarations
        if key.startswith(parent) and key != parent and len(parent) >= 2
    ]
    if extensions:
        return sorted(set(extensions))
    shared_prefix = [
        parent for parent in declarations
        if parent.startswith(key) and key != parent and len(key) >= 2
    ]
    return sorted(set(shared_prefix)) if len(shared_prefix) >= 2 else []


def _filter_slot(
    fact: dict[str, Any], slot: dict[str, Any], declarations: dict[str, str]
) -> dict[str, Any]:
    copied = deepcopy(slot)
    mentions = unique_text([
        *as_list(slot.get("entity_refs")),
        *as_list(slot.get("entity_mentions")),
    ])
    allowed: list[str] = []
    rejected: list[str] = []
    for mention in mentions:
        key = comparison_key(mention)
        parents = _direct_surface_parents(fact, mention, declarations)
        if key in declarations or parents:
            allowed.append(mention)
        else:
            rejected.append(mention)
    copied["raw_entity_mentions"] = unique_text([
        *as_list(slot.get("raw_entity_mentions")),
        *mentions,
    ])
    copied["entity_refs"] = unique_text(allowed)
    copied["entity_mentions"] = unique_text(allowed)
    copied["business_object_rejected_mentions"] = unique_text([
        *as_list(slot.get("business_object_rejected_mentions")),
        *rejected,
    ])
    return copied


def prepare_source_declared_asset(asset: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    declarations = source_object_declarations(asset)
    if not declarations:
        return asset, {"declared_keys": [], "surface_parents": {}}

    prepared = clone_asset_for_understanding_projection(asset)
    # The parser emits ``entity_inventory_table`` for both explicit entity
    # inventories and weak database table directories.  Source structure has
    # already selected the declarations above, so raw tables return to
    # technical-only evidence before the existing candidate collector runs.
    for table in as_list(prepared.get("data_tables")):
        if not isinstance(table, dict):
            continue
        if text(table.get("derivation")) == "entity_inventory_table":
            table["derivation"] = "source_data_table"
        table["derivations"] = [
            value
            for value in as_list(table.get("derivations"))
            if text(value) != "entity_inventory_table"
        ]
    existing_business_objects = [
        dict(row) for row in as_list(prepared.get("business_objects"))
        if isinstance(row, dict)
    ]
    business_objects: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    declaration_surface_modes: dict[str, dict[str, bool]] = {}
    for declaration in declarations:
        values = unique_text(as_list(declaration.get("labels")))
        if not values:
            continue
        canonical = text(declaration.get("canonical_label")) or values[0]
        for value in values:
            key = comparison_key(value)
            labels[key] = value
            declaration_surface_modes[key] = {
                "suffix": bool(declaration.get("surface_suffix_discovery_allowed", True)),
                "prefix": bool(declaration.get("surface_prefix_discovery_allowed", False)),
            }
        evidence = [dict(row) for row in as_list(declaration.get("evidence")) if isinstance(row, dict)]
        business_objects.append({
            "object_id": text(declaration.get("declaration_id")) or stable_id("source_declared_object", canonical, values),
            "object": canonical,
            "name": canonical,
            "aliases": [value for value in values if comparison_key(value) != comparison_key(canonical)],
            "evidence": evidence,
            "source_id": text(evidence[0].get("source_id") if evidence else ""),
            "source_locator": text(evidence[0].get("source_locator") if evidence else "") or text(declaration.get("declaration_id")),
            "source_excerpt": text(evidence[0].get("quote") if evidence else ""),
            "source": "source_object_declaration",
            "derivation": text(declaration.get("authority")),
            "source_declaration_authorities": unique_text(
                [declaration.get("authority"), *as_list(declaration.get("authorities"))]
            ),
            "surface_suffix_discovery_allowed": bool(
                declaration.get("surface_suffix_discovery_allowed")
            ),
            "surface_prefix_discovery_allowed": bool(
                declaration.get("surface_prefix_discovery_allowed")
            ),
        })
    retained_existing = [
        row for row in existing_business_objects
        if any(
            comparison_key(value) in labels
            for value in [
                row.get("object") or row.get("name"),
                *as_list(row.get("aliases")),
            ]
            if comparison_key(value)
        )
    ]
    business_objects = [*retained_existing, *business_objects]
    prepared["business_objects"] = business_objects

    surfaces: dict[str, dict[str, Any]] = {}
    for surface in collect_source_attested_object_surfaces(prepared, declarations):
        key = comparison_key(surface.get("label"))
        if not key:
            continue
        surfaces[key] = {
            "label": text(surface.get("label")),
            "parents": set(as_list(surface.get("parents"))),
            "evidence": list(as_list(surface.get("evidence"))),
        }
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
                    parents = _direct_surface_parents(fact, mention, labels)
                    if parents:
                        row = surfaces.setdefault(key, {"label": text(mention), "parents": set(), "evidence": []})
                        row["parents"].update(parents)
                        row["evidence"].extend(evidence)
                    elif key not in labels:
                        rejected.append({
                            "fact_id": text(fact.get("fact_id")),
                            "label": text(mention),
                            "comparison_key": key,
                            "reason_code": "UNDECLARED_OBJECT_SLOT_WHEN_SOURCE_DECLARATIONS_EXIST",
                            "evidence": evidence,
                            "roles": [f"BUSINESS_FACT_{side.upper()}"],
                        })
                fact[side] = _filter_slot(fact, as_dict(fact.get(side)), labels)
        items.append(fact)
    for key, row in sorted(surfaces.items()):
        evidence = [
            dict(value) for value in row["evidence"] if isinstance(value, dict)
        ]
        business_objects.append({
            "object_id": stable_id(
                "source_object_surface", key, sorted(row["parents"])
            ),
            "object": row["label"],
            "name": row["label"],
            "aliases": [],
            "evidence": evidence,
            "source_id": text(evidence[0].get("source_id") if evidence else ""),
            "source_locator": text(
                evidence[0].get("source_locator") if evidence else ""
            ) or key,
            "source_excerpt": text(evidence[0].get("quote") if evidence else ""),
            "source": "source_object_surface",
            "derivation": "direct_source_object_slot_surface",
            "surface_suffix_discovery_allowed": False,
            "surface_prefix_discovery_allowed": False,
        })
    prepared["business_objects"] = business_objects
    if ledger:
        ledger["items"] = items
        prepared["business_fact_ledger"] = ledger
    return prepared, {
        "declared_labels": labels,
        "declaration_surface_modes": declaration_surface_modes,
        "surface_parents": {key: sorted(row["parents"]) for key, row in surfaces.items()},
        "rejected_fact_mentions": rejected,
    }


def finalize_source_declared_recognition(recognition: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
    declared_labels = {text(key): text(value) for key, value in as_dict(authority.get("declared_labels")).items()}
    parents_by_surface = {
        text(key): [text(value) for value in as_list(values) if text(value)]
        for key, values in as_dict(authority.get("surface_parents")).items()
    }
    declaration_modes = {
        text(key): as_dict(value)
        for key, value in as_dict(authority.get("declaration_surface_modes")).items()
    }
    rejected = [dict(row) for row in as_list(authority.get("rejected_fact_mentions")) if isinstance(row, dict)]
    if not parents_by_surface and not rejected and not declaration_modes:
        return recognition
    result = deepcopy(recognition)
    eligible = set(as_list(result.get("identity_resolution_eligible_comparison_keys")))
    accepted = set(as_list(result.get("accepted_comparison_keys")))
    unauthorized_labels: set[str] = set()
    review_count = 0
    for candidate in as_list(result.get("candidates")):
        if not isinstance(candidate, dict) or not candidate.get("source_surface_origin"):
            continue
        key = text(candidate.get("comparison_key"))
        parent_keys = [text(value) for value in as_list(candidate.get("surface_parent_keys"))]
        source_allows = any(
            bool(declaration_modes.get(parent, {}).get("suffix"))
            or bool(declaration_modes.get(parent, {}).get("prefix"))
            for parent in parent_keys
        )
        surface_evidence = [
            row
            for row in as_list(candidate.get("evidence"))
            if isinstance(row, dict) and text(row.get("quote"))
        ]
        slash_only = bool(surface_evidence) and all(
            "/" in text(row.get("quote")) or "／" in text(row.get("quote"))
            for row in surface_evidence
        )
        if key in parents_by_surface or (source_allows and not slash_only):
            continue
        candidate.update({
            "status": "PENDING_SOURCE_SURFACE_NOT_AUTHORIZED",
            "reason_code": "SOURCE_SURFACE_NOT_AUTHORIZED_BY_DECLARATION_GATE",
            "identity_resolution_eligible": False,
            "requires_identity_review": False,
            "automatic_identity_union_allowed": False,
        })
        accepted.discard(key)
        eligible.discard(key)
        unauthorized_labels.update(text(value) for value in as_list(candidate.get("labels")))
    result["accepted_comparison_keys"] = sorted(accepted)
    result["accepted_labels"] = [
        value for value in as_list(result.get("accepted_labels"))
        if text(value) not in unauthorized_labels
    ]
    for candidate in as_list(result.get("candidates")):
        if not isinstance(candidate, dict):
            continue
        key = text(candidate.get("comparison_key"))
        parents = parents_by_surface.get(key)
        if not parents or not text(candidate.get("status")).startswith("ACCEPTED"):
            continue
        candidate.update({
            "status": "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING",
            "reason_code": "SOURCE_ATTESTED_OBJECT_SURFACE_IDENTITY_UNRESOLVED",
            "source_surface_origin": True,
            "surface_parent_keys": parents,
            "surface_parent_labels": [declared_labels.get(parent, parent) for parent in parents],
            "identity_resolution_eligible": False,
            "requires_identity_review": True,
            "automatic_identity_union_allowed": False,
        })
        eligible.discard(key)
        review_count += 1
    result["identity_resolution_eligible_comparison_keys"] = sorted(eligible)
    existing_rejected = [dict(row) for row in as_list(result.get("rejected_fact_mentions")) if isinstance(row, dict)]
    by_key = {
        (text(row.get("fact_id")), text(row.get("comparison_key")), text(row.get("reason_code"))): row
        for row in [*existing_rejected, *rejected]
    }
    result["rejected_fact_mentions"] = list(by_key.values())
    gate = as_dict(result.get("gate"))
    metrics = dict(as_dict(gate.get("metrics")))
    metrics["surface_form_identity_review_count"] = int(metrics.get("surface_form_identity_review_count") or 0) + review_count
    metrics["identity_resolution_eligible_candidate_count"] = len(eligible)
    metrics["rejected_fact_mention_count"] = len(result["rejected_fact_mentions"])
    gate["metrics"] = metrics
    if review_count and not text(gate.get("required_operator_action")):
        gate["required_operator_action"] = "confirm source surface identities through TERM_ALIAS or identity review"
    result["gate"] = gate
    result["recognition_id"] = stable_id(
        "business_object_recognition",
        result.get("recognition_id"),
        [(row.get("candidate_id"), row.get("status")) for row in as_list(result.get("candidates")) if isinstance(row, dict)],
    )
    return result


__all__ = ["finalize_source_declared_recognition", "prepare_source_declared_asset"]
