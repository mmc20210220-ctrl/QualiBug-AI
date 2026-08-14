"""Behavior-only binding for source surfaces after identity resolution.

The binding is deliberately excluded from identity clustering. It preserves raw
source mentions and never creates an alias edge or entity union.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._object_role_evidence import comparison_key, object_slot_mentions
from .schema import (
    as_dict,
    as_list,
    clone_asset_for_understanding_projection,
    text,
    unique_text,
)

def _behavior_surface_bindings(
    recognition: dict[str, Any], identity_eligible: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    eligible = identity_eligible or set(
        as_list(recognition.get("identity_resolution_eligible_comparison_keys"))
    )
    bindings: dict[str, dict[str, Any]] = {}
    for candidate in as_list(recognition.get("candidates")):
        if not isinstance(candidate, dict):
            continue
        status = text(candidate.get("status"))
        if (
            status == "ACCEPTED"
            and bool(candidate.get("explicit_object_authority"))
            and bool(candidate.get("identity_resolution_eligible"))
        ):
            for label in unique_text(as_list(candidate.get("labels"))):
                key = comparison_key(label)
                if not key or (eligible and key not in eligible):
                    continue
                bindings[key] = {
                    "surface_label": label,
                    "parent_label": label,
                    "parent_comparison_key": key,
                    "candidate_id": text(candidate.get("candidate_id")),
                    "authority": "EXPLICIT_SOURCE_OBJECT_DECLARATION",
                    "scope": "BEHAVIOR_BINDING_ONLY",
                    "identity_union_performed": False,
                    "automatic_alias_edge_created": False,
                }
            continue
        if status != "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING":
            continue
        parent_labels = unique_text(as_list(candidate.get("surface_parent_labels")))
        if len(parent_labels) != 1:
            continue
        parent_label = parent_labels[0]
        parent_key = comparison_key(parent_label)
        if eligible and parent_key not in eligible:
            continue
        for label in unique_text(as_list(candidate.get("labels"))):
            key = comparison_key(label)
            if not key or key in eligible:
                continue
            bindings[key] = {
                "surface_label": label,
                "parent_label": parent_label,
                "parent_comparison_key": parent_key,
                "candidate_id": text(candidate.get("candidate_id")),
                "authority": "UNIQUE_SOURCE_DECLARATION_SURFACE",
                "scope": "BEHAVIOR_BINDING_ONLY",
                "identity_union_performed": False,
                "automatic_alias_edge_created": False,
            }
    return bindings

def prepare_identity_safe_behavior_surfaces(
    asset: dict[str, Any], recognition: dict[str, Any]
) -> dict[str, Any]:
    """Remove behavior mentions from identity input while retaining audit traces."""
    projected = clone_asset_for_understanding_projection(asset)
    bindings = _behavior_surface_bindings(recognition)
    # Only genuine short surfaces (surface label differs from its parent) are
    # behavior mentions to strip from identity input. Self-bindings emitted for
    # ACCEPTED explicit objects reuse the object's own label and must not erase
    # that identity coordinate.
    binding_keys = {
        key
        for key, binding in bindings.items()
        if comparison_key(binding.get("surface_label"))
        != comparison_key(binding.get("parent_label"))
    }
    ledger = dict(as_dict(projected.get("business_fact_ledger")))
    facts: list[dict[str, Any]] = []
    for raw in as_list(ledger.get("items")):
        if not isinstance(raw, dict):
            continue
        fact = deepcopy(raw)
        if text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}:
            for side in ("subject", "object"):
                slot = dict(as_dict(fact.get(side)))
                mentions = object_slot_mentions(fact, side)
                removed = [value for value in mentions if comparison_key(value) in binding_keys]
                kept = [value for value in mentions if comparison_key(value) not in binding_keys]
                slot["raw_entity_mentions"] = unique_text([
                    *as_list(slot.get("raw_entity_mentions")),
                    *mentions,
                ])
                slot["entity_mentions"] = unique_text(kept)
                slot["entity_refs"] = unique_text(kept)
                slot["business_object_rejected_mentions"] = unique_text([
                    *as_list(slot.get("business_object_rejected_mentions")),
                    *removed,
                ])
                slot["resolution_evidence"] = [
                    deepcopy(row)
                    for row in as_list(slot.get("resolution_evidence"))
                    if isinstance(row, dict)
                    and comparison_key(row.get("mention") or row.get("resolved_ref"))
                    not in binding_keys
                ]
                fact[side] = slot
        facts.append(fact)
    ledger["items"] = facts
    projected["business_fact_ledger"] = ledger
    projected["business_object_behavior_surface_bindings"] = list(bindings.values())
    return projected

def project_behavior_surface_bindings(
    asset: dict[str, Any], recognition: dict[str, Any]
) -> dict[str, Any]:
    """Bind unique source surfaces to behaviors after identity resolution only.

    The projection never creates an alias edge or changes an identity cluster. It
    converts a source-local object surface into its one declared parent only for
    operation/lifecycle construction, while preserving the raw source mention.
    """
    projected = clone_asset_for_understanding_projection(asset)
    bindings = _behavior_surface_bindings(recognition)
    label_to_entity = dict(
        as_dict(
            as_dict(projected.get("enterprise_identity_resolution")).get(
                "label_to_entity"
            )
        )
    )

    def binding_for(value: Any) -> dict[str, Any] | None:
        return bindings.get(comparison_key(value))

    applied: list[dict[str, Any]] = []
    ledger = dict(as_dict(projected.get("business_fact_ledger")))
    facts: list[dict[str, Any]] = []
    for raw in as_list(ledger.get("items")):
        if not isinstance(raw, dict):
            continue
        fact = deepcopy(raw)
        if text(fact.get("kind")) in {"RULE", "STATE_TRANSITION"}:
            for side in ("subject", "object"):
                slot = dict(as_dict(fact.get(side)))
                statement_mentions = (
                    [
                        text(binding.get("surface_label"))
                        for binding in bindings.values()
                        if text(binding.get("surface_label"))
                        and text(binding.get("surface_label"))
                        in text(fact.get("raw_statement"))
                    ]
                    if side == "object"
                    else []
                )
                raw_mentions = unique_text(
                    [
                        *as_list(slot.get("raw_entity_mentions")),
                        *as_list(slot.get("business_object_rejected_mentions")),
                        *statement_mentions,
                    ]
                )
                accepted = unique_text(
                    [
                        *as_list(slot.get("entity_mentions")),
                        *as_list(slot.get("entity_refs")),
                    ]
                )
                evidence: list[dict[str, Any]] = []
                bound_surface_keys: set[str] = set()
                for mention in raw_mentions:
                    binding = binding_for(mention)
                    if binding is None:
                        continue
                    parent = text(binding.get("parent_label"))
                    accepted.append(parent)
                    bound_surface_keys.add(comparison_key(mention))
                    row = {
                        **deepcopy(binding),
                        "mention": text(mention),
                        "resolved_ref": parent,
                        "fact_id": text(fact.get("fact_id")),
                        "slot": side,
                    }
                    evidence.append(row)
                    applied.append(row)
                if not evidence:
                    fact[side] = slot
                    continue
                slot["entity_mentions"] = unique_text(accepted)
                slot["entity_refs"] = unique_text(accepted)
                slot["resolved_entity_refs"] = unique_text(
                    [
                        *as_list(slot.get("resolved_entity_refs")),
                        *(label_to_entity.get(label) for label in accepted),
                    ]
                )
                slot["identity_pending_mentions"] = unique_text(
                    [
                        *as_list(slot.get("identity_pending_mentions")),
                        *(
                            mention
                            for mention in raw_mentions
                            if comparison_key(mention) in bound_surface_keys
                        ),
                    ]
                )
                slot["business_object_rejected_mentions"] = unique_text(
                    mention
                    for mention in as_list(slot.get("business_object_rejected_mentions"))
                    if comparison_key(mention) not in bound_surface_keys
                )
                slot["behavior_binding_evidence"] = [
                    *[
                        deepcopy(row)
                        for row in as_list(slot.get("behavior_binding_evidence"))
                        if isinstance(row, dict)
                    ],
                    *evidence,
                ]
                fact[side] = slot
            fact["identity_resolution_refs"] = unique_text(
                [
                    *as_list(fact.get("identity_resolution_refs")),
                    *as_list(as_dict(fact.get("subject")).get("resolved_entity_refs")),
                    *as_list(as_dict(fact.get("object")).get("resolved_entity_refs")),
                ]
            )
        facts.append(fact)
    ledger["items"] = facts
    projected["business_fact_ledger"] = ledger

    source_machine_bindings = {
        text(row.get("source_id")): dict(row)
        for row in as_list(recognition.get("state_machine_source_bindings"))
        if isinstance(row, dict) and text(row.get("source_id"))
    }
    machines: list[dict[str, Any]] = []
    for raw in as_list(projected.get("state_machines")):
        if not isinstance(raw, dict):
            continue
        machine = deepcopy(raw)
        object_label = text(machine.get("object"))
        binding = binding_for(object_label) or source_machine_bindings.get(
            text(machine.get("source_id"))
        )
        if binding is not None:
            machine["raw_object"] = object_label
            machine["object"] = text(binding.get("parent_label"))
            machine["object_binding_authority"] = text(binding.get("authority"))
            machine["object_binding_scope"] = text(binding.get("scope"))
            machine["identity_union_performed"] = False
            machine["automatic_alias_edge_created"] = False
            applied.append(
                {
                    **deepcopy(binding),
                    "state_machine_id": text(machine.get("state_machine_id")),
                    "mention": object_label,
                    "resolved_ref": machine["object"],
                }
            )
        machines.append(machine)
    if machines:
        projected["state_machines"] = machines

    projected["business_object_behavior_binding_receipt"] = {
        "schema": "qualibug.business-object-behavior-surface-binding.v1",
        "binding_count": len(applied),
        "surface_binding_count": len(bindings),
        "bindings": applied,
        "scope": "BEHAVIOR_BINDING_ONLY",
        "runs_after_identity_resolution": True,
        "identity_union_performed": False,
        "automatic_alias_edge_created": False,
    }
    return projected

__all__ = [
    "prepare_identity_safe_behavior_surfaces",
    "project_behavior_surface_bindings",
]
