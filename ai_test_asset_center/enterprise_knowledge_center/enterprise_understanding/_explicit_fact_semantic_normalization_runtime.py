from __future__ import annotations

from ._explicit_fact_semantic_normalization_pairing import *

def normalize_explicit_business_fact_semantics(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    normalized_ids: list[str] = []
    skipped_formal_typed = 0
    field_counts: dict[str, int] = {}

    for fact in facts:
        statement = _text(fact.get("raw_statement"))
        current_type = _text(fact.get("fact_type")).upper()
        if not statement or _text(fact.get("kind")) == "TERM_ALIAS":
            continue
        if current_type in _FORMAL_TYPED_COORDINATES:
            skipped_formal_typed += 1
            continue
        changed: list[str] = []

        subject = dict(_dict(fact.get("subject")))
        existing_actors = [
            _text(row) for row in _list(subject.get("actor_refs")) if _text(row)
        ]
        actor_vocabulary = _source_backed_vocabulary(
            statement,
            existing_actors,
            _ROLE_HEADS,
        )
        exception_scopes = set(_text(row) for row in _list(fact.get("exception_scope")))
        only_actor = _only_actor_role(statement)
        explicit_actors = [
            row
            for row in _explicit_refs(statement, actor_vocabulary)
            if row not in exception_scopes
        ]
        actors = [only_actor] if only_actor else (explicit_actors or existing_actors)
        if actors != _list(subject.get("actor_refs")):
            subject["actor_refs"] = actors
            changed.append("actor_refs")

        action_coordinate = _governed_action(statement, fact)
        action = {
            "canonical": _text(action_coordinate.get("canonical")),
            "raw": _text(action_coordinate.get("raw")),
        }
        action = {key: value for key, value in action.items() if value}
        if action and action != _dict(fact.get("action")):
            fact["action"] = action
            fact["predicate"] = action["canonical"]
            changed.append("action")

        existing_entities = [
            _text(row) for row in _list(subject.get("entity_refs")) if _text(row)
        ]
        governed_entities = _governed_entities(
            statement,
            action_coordinate,
            existing_entities,
        )
        entities = governed_entities or existing_entities
        if governed_entities and entities != _list(subject.get("entity_refs")):
            subject["entity_refs"] = entities
            changed.append("entity_refs")
        fact["subject"] = subject

        object_part = dict(_dict(fact.get("object")))
        if governed_entities and entities != _list(object_part.get("entity_refs")):
            object_part["entity_refs"] = entities
            changed.append("object_refs")
        fact["object"] = object_part

        modality, polarity = _modality(statement)
        if modality != _text(fact.get("modality")):
            fact["modality"] = modality
            changed.append("modality")
        if polarity != _text(fact.get("polarity")):
            fact["polarity"] = polarity
            changed.append("polarity")

        conditions, combinator = _condition_coordinates(
            statement,
            fact,
            action=action_coordinate,
            entities=entities,
        )
        before_frame = (
            list(_list(fact.get("conditions"))),
            _text(fact.get("condition_combinator")),
            dict(_dict(fact.get("condition_frame"))),
        )
        _normalize_condition_frame(fact, conditions, combinator)
        after_frame = (
            list(_list(fact.get("conditions"))),
            _text(fact.get("condition_combinator")),
            dict(_dict(fact.get("condition_frame"))),
        )
        if before_frame != after_frame:
            changed.append("condition_frame")

        state_effect = _normalized_state_effect(statement)
        if state_effect is not None and _list(fact.get("state_effects")) != [state_effect]:
            fact["state_effects"] = [state_effect]
            changed.append("state_effects")

        time_window = _normalized_time_window(statement)
        if (
            time_window is not None
            and _list(fact.get("time_window_constraints")) != [time_window]
        ):
            fact["time_window_constraints"] = [time_window]
            changed.append("time_window_constraints")

        postconditions = _normalized_postconditions(
            statement,
            _list(fact.get("postconditions")),
        )
        if postconditions != _list(fact.get("postconditions")):
            fact["postconditions"] = postconditions
            changed.append("postconditions")

        fact_type = _normalized_fact_type(fact)
        if fact_type != current_type:
            fact["fact_type"] = fact_type
            changed.append("fact_type")

        if _normalize_primary_claim(
            fact,
            action=action_coordinate,
            actors=actors,
            entities=entities,
        ):
            changed.append("primary_operation_claim")

        if not changed:
            continue
        fact["explicit_semantic_normalization"] = {
            "status": "PASS",
            "normalized_fields": sorted(set(changed)),
            "source_backed": True,
            "governed_operation_binding": True,
            "new_fact_discovered": False,
            "automatic_winner_used": False,
        }
        fact["semantic_signature"] = _semantic_signature(fact)
        fact_id = _text(fact.get("fact_id"))
        if fact_id:
            normalized_ids.append(fact_id)
        for field in set(changed):
            field_counts[field] = field_counts.get(field, 0) + 1

    paired_groups, paired_ids = _pair_split_if_else_frames(facts)
    normalized_ids = _ordered_unique([*normalized_ids, *paired_ids])
    if paired_groups:
        field_counts["if_then_else_frame"] = field_counts.get("if_then_else_frame", 0) + (
            paired_groups * 2
        )

    ledger["items"] = facts
    asset["business_fact_ledger"] = ledger
    asset["explicit_fact_semantic_normalization_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "normalized_fact_count": len(normalized_ids),
        "normalized_fact_ids": normalized_ids,
        "formal_typed_fact_count_left_on_compiler_coordinates": skipped_formal_typed,
        "paired_if_then_else_group_count": paired_groups,
        "paired_if_then_else_fact_count": paired_groups * 2,
        "normalized_field_counts": dict(sorted(field_counts.items())),
        "existing_ledger_reused": True,
        "existing_source_backed_identity_vocabulary_reused": True,
        "grammar_fragment_identity_reuse_allowed": False,
        "governed_operation_binding": True,
        "qualified_object_conditions_compiled": True,
        "split_if_else_pairing_requires_one_unique_pair_per_locator": True,
        "split_if_else_pairing_prefers_exact_structure_locator": True,
        "negative_operation_is_not_negative_modality": True,
        "new_fact_discovery_allowed": False,
        "source_statement_rewrite_allowed": False,
        "conflicting_source_value_selection_allowed": False,
        "overlapping_identity_coordinate_emission_allowed": False,
        "formal_typed_coordinate_reinterpretation_allowed": False,
        "automatic_winner_used": False,
    }
    governance = dict(_dict(asset.get("governance")))
    governance.update(
        {
            "explicit_fact_coordinates_normalized_at_compiler_boundary": True,
            "explicit_fact_identity_is_bound_to_governed_operation": True,
            "explicit_fact_normalization_reuses_source_backed_identity_vocabulary": True,
            "explicit_fact_normalization_rejects_grammar_fragment_identity": True,
            "explicit_fact_qualified_object_conditions_are_source_backed": True,
            "explicit_fact_split_if_else_pairing_is_locator_scoped": True,
            "explicit_fact_split_if_else_pairing_prefers_structure_locator": True,
            "explicit_fact_negative_operation_is_separate_from_modality": True,
            "explicit_fact_normalization_discovers_new_facts": False,
            "explicit_fact_normalization_selects_conflicting_values": False,
            "explicit_fact_identity_mentions_are_longest_non_overlapping": True,
            "formal_typed_fact_coordinates_remain_compiler_owned": True,
        }
    )
    asset["governance"] = governance
    return asset

__all__ = sorted(name for name in globals() if not name.startswith('__'))
