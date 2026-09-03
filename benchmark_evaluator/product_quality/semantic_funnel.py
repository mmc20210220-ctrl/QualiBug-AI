from __future__ import annotations

"""Read-only semantic funnel diagnostics for product-quality audits.

The module observes already-produced enterprise understanding and semantic-extraction
receipts. It never changes product inference and never reads human review anchors
during product capture.
"""

from typing import Any, Iterable


SEMANTIC_CAPTURE_SCHEMA = "qualibug.product-quality-semantic-capture.v1"
SEMANTIC_FUNNEL_SCHEMA = "qualibug.product-quality-semantic-funnel.v1"
SEMANTIC_RUNTIME_CAPTURE_SCHEMA = "qualibug.product-quality-semantic-runtime-capture.v1"
SEMANTIC_FUNNEL_QUALITY_CLAIM = (
    "EXACT_SOURCE_ANCHOR_STAGE_TRACE_NOT_RECALL_SCORE_OR_AUTOMATIC_QUALITY_VERDICT"
)
SEMANTIC_RUNTIME_QUALITY_CLAIM = (
    "CANONICAL_SEMANTIC_EXTRACTION_RECEIPT_SNAPSHOT_NOT_A_SECOND_MODE_RESOLVER"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [
            dict(row)
            for row in _list(value.get("items"))
            if isinstance(row, dict)
        ]
    return []


def _unique_text(values: Iterable[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _evidence_quotes_from(value: Any) -> list[str]:
    """Collect source/evidence text only, never titles or generated summaries."""

    quotes: list[str] = []

    def visit(node: Any, *, evidence_context: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, evidence_context=evidence_context)
            return
        if not isinstance(node, dict):
            return
        for key, child in node.items():
            key_text = _text(key).lower()
            child_is_evidence = evidence_context or key_text in {
                "evidence",
                "source_evidence",
                "source_span",
                "source_spans",
                "evidence_span",
                "evidence_spans",
            }
            if child_is_evidence and key_text in {
                "quote",
                "verbatim_quote",
                "text",
            }:
                text_value = _text(child)
                if text_value:
                    quotes.append(text_value)
                continue
            if isinstance(child, (dict, list)):
                visit(child, evidence_context=child_is_evidence)

    visit(value)
    return _unique_text(quotes)


def _source_ids_from(value: Any) -> list[str]:
    source_ids: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        for key, child in node.items():
            if _text(key).lower() == "source_id" and _text(child):
                source_ids.append(_text(child))
            elif isinstance(child, (dict, list)):
                visit(child)

    visit(value)
    return _unique_text(source_ids)


def _fact_object_refs(row: dict[str, Any]) -> list[str]:
    subject = _dict(row.get("subject"))
    obj = _dict(row.get("object"))
    return _unique_text(
        [
            *_list(row.get("object_refs")),
            *_list(subject.get("entity_refs")),
            *_list(subject.get("resolved_entity_refs")),
            *_list(obj.get("entity_refs")),
            *_list(obj.get("resolved_entity_refs")),
        ]
    )


def _fact_action(row: dict[str, Any]) -> str:
    action = _dict(row.get("action"))
    return _text(
        action.get("canonical")
        or action.get("operation_ref")
        or action.get("raw")
        or row.get("operation_ref")
    )


def _behavior_object_refs(row: dict[str, Any]) -> list[str]:
    subject = _dict(row.get("subject"))
    obj = _dict(row.get("object"))
    return _unique_text(
        [
            *_list(row.get("object_refs")),
            *_list(subject.get("object_refs")),
            *_list(subject.get("entity_refs")),
            *_list(obj.get("entity_refs")),
            *_list(obj.get("resolved_entity_refs")),
        ]
    )


def _behavior_operation_ref(row: dict[str, Any]) -> str:
    action = _dict(row.get("action"))
    operation = row.get("operation")
    operation_value = ""
    if isinstance(operation, dict):
        operation_value = _text(
            operation.get("operation_ref")
            or operation.get("canonical")
            or operation.get("raw")
        )
    else:
        operation_value = _text(operation)
    return _text(
        row.get("operation_ref")
        or operation_value
        or action.get("operation_ref")
        or action.get("canonical")
        or action.get("raw")
    )


def _behavior_source_fact_ids(row: dict[str, Any]) -> list[str]:
    origin = _dict(row.get("origin"))
    ids = [
        *_list(row.get("source_fact_ids")),
        *_list(row.get("fact_refs")),
        *_list(row.get("origin_fact_ids")),
        row.get("source_fact_id"),
        row.get("fact_ref"),
        origin.get("origin_fact_id"),
    ]
    for evidence in _list(row.get("evidence")):
        if isinstance(evidence, dict):
            ids.extend([evidence.get("fact_id"), evidence.get("source_fact_id")])
    return _unique_text(ids)


def _business_facts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    model = _dict(asset.get("enterprise_understanding_model"))
    for candidate in (
        asset.get("business_fact_ledger"),
        model.get("business_fact_ledger"),
    ):
        rows = _rows(candidate)
        if rows:
            return rows
    return []


def _business_behaviors(asset: dict[str, Any]) -> list[dict[str, Any]]:
    model = _dict(asset.get("enterprise_understanding_model"))
    for candidate in (
        model.get("business_behaviors"),
        asset.get("business_behaviors"),
    ):
        rows = _rows(candidate)
        if rows:
            return rows
    return []


def _lifecycle_transitions(asset: dict[str, Any]) -> list[dict[str, Any]]:
    model = _dict(asset.get("enterprise_understanding_model"))
    transitions: list[dict[str, Any]] = []
    for lifecycle in _rows(model.get("lifecycles")):
        object_ref = _text(lifecycle.get("object_ref"))
        for transition in _rows(lifecycle.get("transitions")):
            row = dict(transition)
            row["_object_ref"] = object_ref
            transitions.append(row)
    return transitions


def _semantic_runtime_capture(asset: dict[str, Any]) -> dict[str, Any]:
    """Copy/summarize canonical extraction receipts without re-resolving their mode."""

    availability = dict(_dict(asset.get("semantic_extraction_availability")))
    receipts = _rows(asset.get("semantic_extraction_receipts"))
    mode_receipts = [
        {
            "receipt_id": _text(row.get("receipt_id")),
            "schema_version": _text(row.get("schema_version")),
            "requested_mode": _text(row.get("requested_mode")),
            "effective_mode": _text(row.get("effective_mode")),
            "provider_status": _text(row.get("provider_status")),
            "fallback_mode": _text(row.get("fallback_mode")),
            "fallback_reason": _text(row.get("fallback_reason")),
            "governance_policy_applied": bool(row.get("governance_policy_applied")),
        }
        for row in receipts
        if _text(row.get("schema_version"))
        == "qualibug.semantic-rule-extraction-mode.v1"
    ]
    source_receipts = [
        {
            "receipt_id": _text(row.get("receipt_id")),
            "source_id": _text(row.get("source_id")),
            "triggered": row.get("triggered") is True,
            "status": _text(row.get("status")),
            "source_char_count": int(row.get("source_char_count") or 0),
            "chunks_total": int(row.get("chunks_total") or 0),
            "chunks_attempted": int(row.get("chunks_attempted") or 0),
            "chunks_completed": int(row.get("chunks_completed") or 0),
            "unprocessed_ranges": [
                dict(value)
                for value in _list(row.get("unprocessed_ranges"))
                if isinstance(value, dict)
            ],
            "candidates_raw_count": int(row.get("candidates_raw_count") or 0),
            "candidates_validated_count": int(
                row.get("candidates_validated_count") or 0
            ),
            "rejected_count": int(row.get("rejected_count") or 0),
            "rule_funnel": dict(_dict(row.get("rule_funnel"))),
        }
        for row in receipts
        if _text(row.get("schema_version"))
        == "qualibug.semantic-extraction-receipt.v1"
    ]

    ledger_rows = _rows(asset.get("rule_candidate_ledger"))
    ledger_summaries = [
        {
            "source_id": _text(row.get("source_id")),
            "schema_version": _text(row.get("schema_version")),
            "entry_count": int(row.get("entry_count") or 0),
            "regex_entry_count": int(row.get("regex_entry_count") or 0),
            "llm_entry_count": int(row.get("llm_entry_count") or 0),
            "merged_count": int(row.get("merged_count") or 0),
            "conflicted_count": int(row.get("conflicted_count") or 0),
        }
        for row in ledger_rows
    ]
    promotion_receipts = [
        {
            "source_id": _text(row.get("source_id")),
            "schema_version": _text(row.get("schema_version")),
            "promoted_count": int(row.get("promoted_count") or 0),
            "promoted_rule_ids": _unique_text(_list(row.get("promoted_rule_ids"))),
            "skipped_counts": dict(_dict(row.get("skipped_counts"))),
            "all_promoted_have_evidence": row.get("all_promoted_have_evidence") is True,
            "conflicts_silently_resolved": int(
                row.get("conflicts_silently_resolved") or 0
            ),
        }
        for row in _rows(asset.get("rule_promotion_receipts"))
    ]
    promotion_gates = dict(_dict(asset.get("rule_promotion_gates")))

    return {
        "schema": SEMANTIC_RUNTIME_CAPTURE_SCHEMA,
        "quality_claim": SEMANTIC_RUNTIME_QUALITY_CLAIM,
        "canonical_receipts_only": True,
        "mode_re_resolved_by_audit": False,
        "availability": availability,
        "mode_receipt_count": len(mode_receipts),
        "mode_receipts": mode_receipts,
        "effective_modes": _unique_text(
            row.get("effective_mode") for row in mode_receipts
        ),
        "provider_statuses": _unique_text(
            row.get("provider_status") for row in mode_receipts
        ),
        "source_receipt_count": len(source_receipts),
        "source_receipts": source_receipts,
        "rule_candidate_ledger_count": len(ledger_summaries),
        "rule_candidate_ledgers": ledger_summaries,
        "promotion_receipt_count": len(promotion_receipts),
        "promotion_receipts": promotion_receipts,
        "promotion_gates": promotion_gates,
    }


def build_semantic_capture(asset: dict[str, Any]) -> dict[str, Any]:
    """Snapshot semantic outputs before any external review truth is loaded."""

    facts: list[dict[str, Any]] = []
    for row in _business_facts(asset):
        fact_id = _text(row.get("fact_id") or row.get("id"))
        if not fact_id:
            continue
        facts.append(
            {
                "fact_id": fact_id,
                "kind": _text(row.get("kind")),
                "status": _text(row.get("status")),
                "action": _fact_action(row),
                "object_refs": _fact_object_refs(row),
                "source_ids": _source_ids_from(row),
                "evidence_quotes": _evidence_quotes_from(row),
            }
        )

    behaviors: list[dict[str, Any]] = []
    for row in _business_behaviors(asset):
        behavior_id = _text(row.get("behavior_id") or row.get("id"))
        if not behavior_id:
            continue
        operation_ref = _behavior_operation_ref(row)
        object_refs = _behavior_object_refs(row)
        behaviors.append(
            {
                "behavior_id": behavior_id,
                "status": _text(row.get("status")).upper(),
                "reason_code": _text(row.get("reason_code")),
                "candidate_only": row.get("candidate_only") is True,
                "formal_business_rule": row.get("formal_business_rule") is True,
                "operation_ref": operation_ref,
                "object_refs": object_refs,
                "source_fact_ids": _behavior_source_fact_ids(row),
                "source_ids": _source_ids_from(row),
                "evidence_quotes": _evidence_quotes_from(row),
                "grounded_for_test_intelligence": bool(operation_ref and object_refs),
            }
        )

    lifecycle_transitions: list[dict[str, Any]] = []
    for row in _lifecycle_transitions(asset):
        transition_id = _text(row.get("transition_id"))
        if not transition_id:
            continue
        object_ref = _text(row.get("_object_ref"))
        from_state = _text(row.get("from_state"))
        to_state = _text(row.get("to_state"))
        transition_kind = _text(row.get("transition_kind")).upper()
        completeness = _text(row.get("completeness")).upper()
        lifecycle_transitions.append(
            {
                "transition_id": transition_id,
                "object_ref": object_ref,
                "operation_ref": _text(row.get("operation_ref") or row.get("event")),
                "from_state": from_state,
                "to_state": to_state,
                "transition_kind": transition_kind,
                "completeness": completeness,
                "fact_refs": _unique_text(_list(row.get("fact_refs"))),
                "source_ids": _source_ids_from(row),
                "evidence_quotes": _evidence_quotes_from(row),
                "eligible_for_test_intelligence": bool(
                    object_ref
                    and from_state
                    and to_state
                    and completeness == "COMPLETE"
                    and transition_kind in {"ALLOWED", "FORBIDDEN"}
                ),
            }
        )

    return {
        "schema": SEMANTIC_CAPTURE_SCHEMA,
        "quality_claim": SEMANTIC_FUNNEL_QUALITY_CLAIM,
        "review_truth_loaded": False,
        "fact_count": len(facts),
        "behavior_count": len(behaviors),
        "lifecycle_transition_count": len(lifecycle_transitions),
        "semantic_extraction_runtime": _semantic_runtime_capture(asset),
        "facts": facts,
        "behaviors": behaviors,
        "lifecycle_transitions": lifecycle_transitions,
    }


def _quote_matches(quote: str, evidence_quotes: list[Any]) -> bool:
    return bool(
        quote
        and any(quote in _text(candidate) for candidate in evidence_quotes if _text(candidate))
    )


def semantic_funnel_for_anchor(
    anchor: dict[str, Any],
    semantic_capture: dict[str, Any],
    candidate_output_ids: dict[str, list[str]],
) -> dict[str, Any]:
    """Trace one external anchor through current semantics without auto-scoring it."""

    quote = _text(anchor.get("exact_quote"))
    expected_surfaces = {
        _text(value) for value in _list(anchor.get("expected_surfaces")) if _text(value)
    }
    facts = _rows(semantic_capture.get("facts"))
    behaviors = _rows(semantic_capture.get("behaviors"))
    transitions = _rows(semantic_capture.get("lifecycle_transitions"))

    matched_facts = [
        row
        for row in facts
        if _quote_matches(quote, _list(row.get("evidence_quotes")))
    ]
    fact_ids = {
        _text(row.get("fact_id")) for row in matched_facts if _text(row.get("fact_id"))
    }

    matched_behaviors = [
        row
        for row in behaviors
        if _quote_matches(quote, _list(row.get("evidence_quotes")))
        or bool(
            fact_ids
            & {
                _text(value)
                for value in _list(row.get("source_fact_ids"))
                if _text(value)
            }
        )
    ]
    grounded_behaviors = [
        row for row in matched_behaviors if row.get("grounded_for_test_intelligence") is True
    ]
    formal_behaviors = [
        row
        for row in grounded_behaviors
        if _text(row.get("status")).upper() == "CONFIRMED"
        and row.get("formal_business_rule") is True
        and row.get("candidate_only") is not True
    ]

    matched_transitions = [
        row
        for row in transitions
        if _quote_matches(quote, _list(row.get("evidence_quotes")))
        or bool(
            fact_ids
            & {
                _text(value)
                for value in _list(row.get("fact_refs"))
                if _text(value)
            }
        )
    ]
    eligible_transitions = [
        row
        for row in matched_transitions
        if row.get("eligible_for_test_intelligence") is True
    ]

    obligation_ids = [
        _text(value)
        for value in _list(candidate_output_ids.get("test_obligation_ids"))
        if _text(value)
    ]
    design_ids = [
        _text(value)
        for value in _list(candidate_output_ids.get("test_design_ids"))
        if _text(value)
    ]

    test_surface_expected = bool({"test_obligation", "test_design"} & expected_surfaces)
    semantic_units_present = bool(matched_behaviors or matched_transitions)
    grounded_units_present = bool(grounded_behaviors or eligible_transitions)
    formal_units_present = bool(formal_behaviors or eligible_transitions)
    final_product_output_present = bool(obligation_ids or design_ids)

    if not test_surface_expected:
        first_break_stage = "NOT_APPLICABLE_REQUIREMENT_ONLY"
    elif final_product_output_present and not formal_units_present:
        first_break_stage = "INTERMEDIATE_TRACE_GAP_WITH_PRODUCT_OUTPUT"
    elif not matched_facts and not semantic_units_present:
        first_break_stage = "FACT_EXTRACTION"
    elif not semantic_units_present:
        first_break_stage = "SEMANTIC_UNIT_PROJECTION"
    elif not grounded_units_present:
        first_break_stage = "SEMANTIC_GROUNDING"
    elif not formal_units_present:
        first_break_stage = "FORMAL_SEMANTIC_CONFIRMATION"
    elif "test_obligation" in expected_surfaces and not obligation_ids:
        first_break_stage = "TEST_OBLIGATION_PROJECTION"
    elif "test_design" in expected_surfaces and not design_ids:
        first_break_stage = "TEST_DESIGN_PROJECTION"
    else:
        first_break_stage = "END_TO_END_REACHED"

    return {
        "schema": SEMANTIC_FUNNEL_SCHEMA,
        "quality_claim": SEMANTIC_FUNNEL_QUALITY_CLAIM,
        "automatic_quality_verdict": False,
        "human_review_required": True,
        "first_break_stage": first_break_stage,
        "matched_fact_ids": sorted(fact_ids),
        "matched_behavior_ids": sorted(
            _text(row.get("behavior_id"))
            for row in matched_behaviors
            if _text(row.get("behavior_id"))
        ),
        "grounded_behavior_ids": sorted(
            _text(row.get("behavior_id"))
            for row in grounded_behaviors
            if _text(row.get("behavior_id"))
        ),
        "formal_behavior_ids": sorted(
            _text(row.get("behavior_id"))
            for row in formal_behaviors
            if _text(row.get("behavior_id"))
        ),
        "matched_lifecycle_transition_ids": sorted(
            _text(row.get("transition_id"))
            for row in matched_transitions
            if _text(row.get("transition_id"))
        ),
        "eligible_lifecycle_transition_ids": sorted(
            _text(row.get("transition_id"))
            for row in eligible_transitions
            if _text(row.get("transition_id"))
        ),
        "test_obligation_ids": sorted(obligation_ids),
        "test_design_ids": sorted(design_ids),
        "matched_facts": matched_facts,
        "matched_behaviors": matched_behaviors,
        "matched_lifecycle_transitions": matched_transitions,
    }
