"""Compatibility projection from object recognition to identity resolution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._object_role_evidence import comparison_key, object_slot_mentions
from .schema import (
    as_dict,
    as_list,
    clone_asset_for_understanding_projection,
    dedupe_evidence,
    stable_id,
    text,
    unique_text,
)


def _materialize_recognized_business_objects(
    asset: dict[str, Any], recognition: dict[str, Any]
) -> list[dict[str, Any]]:
    """Project accepted type decisions into the existing identity input shape.

    Recognition owns the type decision.  Identity resolution already consumes
    ``business_objects`` plus declared aliases, so this adapter only materializes
    that accepted decision; it does not infer another object or another alias.
    """

    accepted = set(as_list(recognition.get("accepted_comparison_keys")))
    identity_eligible = set(
        as_list(recognition.get("identity_resolution_eligible_comparison_keys"))
    )
    if "identity_resolution_eligible_comparison_keys" not in recognition:
        identity_eligible = set(accepted)
    candidates = {
        text(row.get("comparison_key")): dict(row)
        for row in as_list(recognition.get("candidates"))
        if isinstance(row, dict)
        and text(row.get("comparison_key")) in identity_eligible
    }
    graph: dict[str, set[str]] = {key: set() for key in identity_eligible}
    alias_edges = [
        dict(row)
        for row in as_list(recognition.get("accepted_alias_edges"))
        if isinstance(row, dict)
    ]
    for edge in alias_edges:
        left, right = text(edge.get("left")), text(edge.get("right"))
        if left in graph and right in graph:
            graph[left].add(right)
            graph[right].add(left)

    components: list[set[str]] = []
    remaining = set(graph)
    while remaining:
        root = min(remaining)
        component: set[str] = set()
        stack = [root]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(graph.get(current, set()) - component))
        remaining -= component
        components.append(component)

    existing_by_key = {
        comparison_key(row.get("object") or row.get("name")): dict(row)
        for row in as_list(asset.get("business_objects"))
        if isinstance(row, dict)
        and comparison_key(row.get("object") or row.get("name")) in identity_eligible
    }
    rows: list[dict[str, Any]] = []
    for component in sorted(components, key=lambda value: sorted(value)):
        component_candidates = [
            candidates[key] for key in sorted(component) if key in candidates
        ]
        labels = unique_text(
            label
            for candidate in component_candidates
            for label in as_list(candidate.get("labels"))
        )
        base_candidates = [existing_by_key[key] for key in component if key in existing_by_key]
        base = deepcopy(
            sorted(
                base_candidates,
                key=lambda row: text(row.get("object_id") or row.get("object") or row.get("name")),
            )[0]
        ) if base_candidates else {}

        canonical = text(base.get("object") or base.get("name"))
        if not canonical:
            declared_left_labels = [
                text(edge.get("left_label"))
                for edge in alias_edges
                if text(edge.get("left")) in component
                and text(edge.get("right")) in component
                and text(edge.get("left_label"))
            ]
            canonical = (
                sorted(declared_left_labels)[0]
                if declared_left_labels
                else (labels[0] if labels else "")
            )
        if not canonical:
            continue

        evidence = dedupe_evidence(
            [
                *as_list(base.get("evidence")),
                *[
                    row
                    for candidate in component_candidates
                    for row in as_list(candidate.get("evidence"))
                    if isinstance(row, dict)
                ],
                *[
                    row
                    for edge in alias_edges
                    if text(edge.get("left")) in component
                    and text(edge.get("right")) in component
                    for row in as_list(edge.get("evidence"))
                    if isinstance(row, dict)
                ],
            ]
        )
        aliases = unique_text(
            [
                *as_list(base.get("aliases")),
                *[label for label in labels if comparison_key(label) != comparison_key(canonical)],
            ]
        )
        projected = {
            **base,
            "object": canonical,
            "name": canonical,
            "object_id": text(base.get("object_id"))
            or stable_id(
                "recognized_business_object_projection",
                recognition.get("recognition_id"),
                sorted(component),
            ),
            "aliases": aliases,
            "evidence": evidence,
            "source_id": text(base.get("source_id"))
            or text(evidence[0].get("source_id") if evidence else "")
            or "business_object_recognition",
            "source_locator": text(base.get("source_locator"))
            or text(evidence[0].get("source_locator") if evidence else "")
            or text(base.get("object_id"))
            or canonical,
            "source_excerpt": text(base.get("source_excerpt"))
            or text(evidence[0].get("quote") if evidence else ""),
            "derivation": "business_object_recognition_projection",
            "recognition_candidate_refs": [
                candidate.get("candidate_id") for candidate in component_candidates
            ],
            "recognition_comparison_keys": sorted(component),
        }
        rows.append(projected)
    return rows


def project_asset_for_recognized_objects(
    asset: dict[str, Any], recognition: dict[str, Any]
) -> dict[str, Any]:
    """Filter object mentions while retaining technical assets for binding."""
    projected = clone_asset_for_understanding_projection(asset)
    accepted = set(as_list(recognition.get("accepted_comparison_keys")))
    identity_eligible = set(
        as_list(recognition.get("identity_resolution_eligible_comparison_keys"))
    )
    if "identity_resolution_eligible_comparison_keys" not in recognition:
        identity_eligible = set(accepted)
    accepted_alias_facts = set(as_list(recognition.get("accepted_alias_fact_ids")))

    def allowed(value: Any) -> bool:
        return comparison_key(value) in identity_eligible

    projected["business_object_recognition"] = deepcopy(recognition)
    projected["business_objects"] = _materialize_recognized_business_objects(
        projected, recognition
    )

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
                original = object_slot_mentions(fact, side)
                accepted_mentions = [value for value in original if allowed(value)]
                rejected_mentions = [value for value in original if not allowed(value)]
                slot["raw_entity_mentions"] = unique_text(
                    [*as_list(slot.get("entity_mentions")), *original]
                )
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
    benchmark = dict(as_dict(recognition.get("benchmark")))
    model["business_object_benchmark"] = benchmark
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
    measured = (
        text(benchmark.get("status")) == "MEASURED"
        and bool(benchmark.get("quality_claim_allowed"))
    )
    metrics.update(
        {
            "business_object_candidate_count": int(recognition_metrics.get("candidate_count") or 0),
            "recognized_business_object_label_count": int(
                recognition_metrics.get("accepted_label_count") or 0
            ),
            "business_object_type_conflict_count": int(
                recognition_metrics.get("type_conflict_count") or 0
            ),
            "business_object_recognition_is_measured_recall": measured,
            "business_object_measurement_status": benchmark.get("status") or "NOT_MEASURED",
        }
    )
    if measured:
        for key in (
            "object_type_precision",
            "object_type_recall",
            "object_type_f1",
            "object_overpromotion_rate",
            "object_miss_rate",
            "object_error_unknown_coverage_rate",
            "silent_object_error_count",
            "type_confusion_distribution",
        ):
            if key in recognition_metrics:
                metrics[f"business_{key}"] = recognition_metrics[key]
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
