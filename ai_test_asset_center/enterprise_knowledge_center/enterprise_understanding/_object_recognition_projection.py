"""Compatibility projection from object recognition to identity resolution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._object_role_evidence import comparison_key
from .identity_types import fact_mentions
from .schema import as_dict, as_list, stable_id, text, unique_text


def project_asset_for_recognized_objects(
    asset: dict[str, Any], recognition: dict[str, Any]
) -> dict[str, Any]:
    """Filter object mentions while retaining technical assets for binding."""
    projected = deepcopy(asset)
    accepted = set(as_list(recognition.get("accepted_comparison_keys")))
    accepted_alias_facts = set(as_list(recognition.get("accepted_alias_fact_ids")))

    def allowed(value: Any) -> bool:
        return comparison_key(value) in accepted

    projected["business_object_recognition"] = deepcopy(recognition)
    projected["business_objects"] = [
        deepcopy(row)
        for row in as_list(projected.get("business_objects"))
        if isinstance(row, dict) and allowed(row.get("object") or row.get("name"))
    ]

    ledger = dict(as_dict(projected.get("business_fact_ledger")))
    facts: list[dict[str, Any]] = []
    for raw in as_list(ledger.get("items")):
        if not isinstance(raw, dict):
            continue
        fact = deepcopy(raw)
        kind = text(fact.get("kind"))
        if kind in {"RULE", "STATE_TRANSITION"}:
            for side in ("subject", "object"):
                slot = dict(as_dict(fact.get(side)))
                original = fact_mentions(fact, side)
                accepted_mentions = [value for value in original if allowed(value)]
                rejected_mentions = [value for value in original if not allowed(value)]
                slot["raw_entity_mentions"] = original
                slot["entity_mentions"] = unique_text(accepted_mentions)
                slot["entity_refs"] = unique_text(accepted_mentions)
                slot["business_object_rejected_mentions"] = unique_text(rejected_mentions)
                slot["resolution_evidence"] = [
                    deepcopy(row)
                    for row in as_list(slot.get("resolution_evidence"))
                    if isinstance(row, dict)
                    and (allowed(row.get("mention")) or allowed(row.get("resolved_ref")))
                ]
                fact[side] = slot
            facts.append(fact)
        elif kind == "TERM_ALIAS":
            if (
                allowed(fact.get("canonical_term"))
                and allowed(fact.get("alias"))
                and text(fact.get("fact_id")) in accepted_alias_facts
            ):
                facts.append(fact)
        else:
            facts.append(fact)
    ledger["items"] = facts
    projected["business_fact_ledger"] = ledger
    return projected


def publish_recognition_and_identity(
    asset: dict[str, Any],
    recognized_asset: dict[str, Any],
    resolution: dict[str, Any],
) -> None:
    """Publish resolved refs without replacing original source mentions."""
    asset["business_object_recognition"] = deepcopy(
        recognized_asset.get("business_object_recognition")
    )
    for key in (
        "enterprise_identity_registry",
        "enterprise_identity_resolution",
        "enterprise_identity_gate",
    ):
        if key in recognized_asset:
            asset[key] = deepcopy(recognized_asset[key])

    original_facts = {
        text(row.get("fact_id")): row
        for row in as_list(as_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict) and text(row.get("fact_id"))
    }
    resolved_facts = {
        text(row.get("fact_id")): row
        for row in as_list(
            as_dict(recognized_asset.get("business_fact_ledger")).get("items")
        )
        if isinstance(row, dict) and text(row.get("fact_id"))
    }
    for fact_id, original in original_facts.items():
        resolved = resolved_facts.get(fact_id)
        if resolved is None:
            continue
        original["identity_resolution_refs"] = unique_text(
            as_list(resolved.get("identity_resolution_refs"))
        )
        for side in ("subject", "object"):
            resolved_slot = as_dict(resolved.get(side))
            original_slot = dict(as_dict(original.get(side)))
            original_slot["resolved_entity_refs"] = unique_text(
                as_list(resolved_slot.get("resolved_entity_refs"))
            )
            original_slot["business_object_rejected_mentions"] = unique_text(
                as_list(resolved_slot.get("business_object_rejected_mentions"))
            )
            original[side] = original_slot
    asset["enterprise_identity_resolution"] = deepcopy(resolution)


def apply_recognition_to_model(
    model: dict[str, Any], recognition: dict[str, Any]
) -> dict[str, Any]:
    model["business_object_recognition"] = deepcopy(recognition)
    recognition_gate = dict(as_dict(recognition.get("gate")))
    model["business_object_recognition_gate"] = recognition_gate
    model["unknowns"] = [
        *as_list(model.get("unknowns")),
        *as_list(recognition.get("unknowns")),
    ]

    gate = dict(as_dict(model.get("gate")))
    gate["business_object_recognition_gate"] = recognition_gate
    if not bool(recognition_gate.get("entry_allowed", True)):
        gate.update(
            {
                "status": recognition_gate.get("status"),
                "entry_allowed": False,
                "required_operator_action": recognition_gate.get("required_operator_action"),
                "critical_unknowns": [
                    *as_list(gate.get("critical_unknowns")),
                    *as_list(recognition_gate.get("critical_conflicts")),
                ],
            }
        )
    model["gate"] = gate

    metrics = dict(as_dict(model.get("metrics")))
    recognition_metrics = as_dict(recognition_gate.get("metrics"))
    metrics.update(
        {
            "business_object_candidate_count": int(recognition_metrics.get("candidate_count") or 0),
            "recognized_business_object_label_count": int(
                recognition_metrics.get("accepted_label_count") or 0
            ),
            "business_object_type_conflict_count": int(
                recognition_metrics.get("type_conflict_count") or 0
            ),
            "business_object_recognition_is_measured_recall": False,
        }
    )
    model["metrics"] = metrics
    model["model_id"] = stable_id(
        "enterprise_understanding", model.get("model_id"), recognition.get("recognition_id")
    )
    return model


__all__ = [
    "apply_recognition_to_model",
    "project_asset_for_recognized_objects",
    "publish_recognition_and_identity",
]
