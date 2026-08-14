"""Business Fact Ledger → Chinese Semantic Frame projection adapter (SPEC P0-A).

P0-A contract:
- Every fact in the compiled ``business_fact_ledger`` (v1 or v2 shape) is
  projected into one ``qualibug.chinese-semantic-frame.v1`` frame. Typed fact
  slots map onto typed frame slots with explicit resolution statuses; whatever
  the fact does not carry becomes OMITTED / NOT_MENTIONED / UNKNOWN with a
  reason code — never an empty string, never a guess.
- No Chinese text is re-parsed here: no word lists, no patterns, no regex.
  The raw ownership phrase is preserved on the frame (scope.ownership_relation
  raw) but is NOT part of the semantic signature.
- Actor/entity refs are grounded only by exact match against the asset's
  finalized understanding model registries. Without a registry match a mention
  stays a mention (RESOLVED at source level) and grounding stays PENDING
  (the grounding engine is P0-D).
- Nothing is dropped silently: TERM_ALIAS facts are skipped with a typed
  receipt reason, projection failures set the ledger closure to FAIL, and
  ``silent_drop_allowed`` is always False.
- The projection writes ``asset["chinese_semantic_frame_ledger"]``
  (``qualibug.chinese-semantic-frame-ledger.v1``) and returns the asset.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable

from .chinese_semantic_receipts import build_receipt
from .chinese_semantic_schema import (
    MODALITY_TYPES,
    empty_frame,
    quote_hash,
    semantic_signature,
    validate_semantic_frame,
)

CHINESE_SEMANTIC_FRAME_LEDGER_SCHEMA = "qualibug.chinese-semantic-frame-ledger.v1"

# Frame types whose semantics require an actor slot; an absent actor is OMITTED
# (possibly recoverable from context), never NOT_MENTIONED.
_ACTOR_REQUIRED_FRAME_TYPES = frozenset(
    {
        "PERMISSION_RULE",
        "OWNERSHIP_RULE",
        "SCOPE_RULE",
        "DATA_VISIBILITY_RULE",
        "STATE_GUARD",
    }
)

# Frame types whose semantics require a scope slot.
_SCOPE_REQUIRED_FRAME_TYPES = frozenset(
    {
        "PERMISSION_RULE",
        "OWNERSHIP_RULE",
        "SCOPE_RULE",
        "DATA_VISIBILITY_RULE",
        "POSTCONDITION",
        "COMPENSATION_RULE",
    }
)

# Typed fact types produced by the ledger producers that map 1:1 onto frames.
_FRAME_TYPE_MAPPING = {
    "OBJECT_RELATION": "RELATION_CONSTRAINT",
    "PERMISSION_RULE": "PERMISSION_RULE",
    "STATE_TRANSITION": "STATE_TRANSITION",
    "CARDINALITY_CONSTRAINT": "CARDINALITY_CONSTRAINT",
    "FORMULA_CONSTRAINT": "FORMULA_CONSTRAINT",
    "QUANTITY_CONSTRAINT": "QUANTITY_CONSTRAINT",
    "TIME_WINDOW_CONSTRAINT": "TIME_WINDOW_CONSTRAINT",
    "VALIDATION_RULE": "VALIDATION_RULE",
    "UNIQUENESS_CONSTRAINT": "UNIQUENESS_CONSTRAINT",
    "COMPENSATION_RULE": "COMPENSATION_RULE",
    "PROCESS_ORDERING": "PROCESS_ORDERING",
    "BUSINESS_RULE": "BUSINESS_RULE",
}

_LEGACY_MODALITY_FRAME_TYPES = frozenset({"MAY", "MUST_NOT", "ONLY_IF"})


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return " ".join(_text(value).split()).strip()


def _stable_id(kind: str, *parts: Any) -> str:
    payload = [_text(part) for part in parts]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{kind}:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _frame_type_for_fact(fact: dict[str, Any]) -> str:
    """Map a ledger fact onto a frame type without re-interpreting the text.

    Producer-typed fact types pass through when they are already frame types;
    the compiler's OBJECT_RELATION / DERIVED_VALUE / TERM_ALIAS labels map onto
    frame vocabulary. Facts without a typed fact_type (legacy v1 rows) are
    classified by typed slots only — never by Chinese wording.
    """
    fact_type = _text(fact.get("fact_type"))
    if fact_type:
        if fact_type in _FRAME_TYPE_MAPPING:
            return _FRAME_TYPE_MAPPING[fact_type]
        if fact_type == "DERIVED_VALUE":
            if _list(fact.get("formula_constraints")):
                return "FORMULA_CONSTRAINT"
            if _list(fact.get("quantity_constraints")):
                return "QUANTITY_CONSTRAINT"
            return "BUSINESS_RULE"
        if fact_type == "TERM_ALIAS":
            return "TERM_ALIAS"
        return "BUSINESS_RULE"
    kind = _text(fact.get("kind"))
    if kind == "TERM_ALIAS":
        return "TERM_ALIAS"
    if _list(fact.get("state_effects")) or kind == "STATE_TRANSITION":
        return "STATE_TRANSITION"
    if _list(fact.get("formula_constraints")) and not _dict(fact.get("action")):
        return "FORMULA_CONSTRAINT"
    if _list(fact.get("conservation_linkages")):
        return "CONSERVATION_LINKAGE"
    if _list(fact.get("process_ordering")):
        return "PROCESS_ORDERING"
    subject = _dict(fact.get("subject"))
    scope = _dict(fact.get("scope"))
    if (
        _list(subject.get("actor_refs"))
        or any(_text(value) for value in scope.values())
        or _text(fact.get("modality")) in _LEGACY_MODALITY_FRAME_TYPES
    ):
        return "PERMISSION_RULE"
    if _list(fact.get("quantity_constraints")):
        return "QUANTITY_CONSTRAINT"
    return "BUSINESS_RULE"


def _registry_index(registry: Iterable[dict[str, Any]] | None) -> dict[str, str]:
    """Exact-match index over a registry's identity fields → canonical row id.

    Only exact string equality counts; similarity never grounds a mention.
    """
    index: dict[str, str] = {}
    identity_fields = (
        "id", "actor_id", "object_id", "entity_id",
        "role_key", "role", "name", "canonical_label",
    )
    for row in registry or ():
        if not isinstance(row, dict):
            continue
        canonical_id = _text(
            row.get("id") or row.get("actor_id") or row.get("object_id")
            or row.get("entity_id")
        )
        if not canonical_id:
            continue
        for field in identity_fields:
            value = _text(row.get(field))
            if value:
                index[value] = canonical_id
    return index


def _resolve_refs(refs: Iterable[Any], index: dict[str, str]) -> tuple[list[str], list[str]]:
    resolved: set[str] = set()
    unresolved: list[str] = []
    for ref in refs:
        item = _norm(ref)
        if not item:
            continue
        hit = index.get(item)
        if hit:
            resolved.add(hit)
        else:
            unresolved.append(item)
    return sorted(resolved), sorted(set(unresolved))


def _registries_from_asset(
    asset: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Pull the finalized actor/entity registries from the understanding model.

    The projection runs after the second cognition pass, so these registries
    are final for this build. Missing model → no grounding possible.
    """
    model = _dict(asset.get("enterprise_understanding_model"))
    actors = model.get("actors")
    entities = model.get("business_objects")
    return (
        [row for row in actors if isinstance(row, dict)] if isinstance(actors, list) else None,
        [row for row in entities if isinstance(row, dict)] if isinstance(entities, list) else None,
    )


def _source_span_info(fact: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract (span_fields, closure) from the fact's first source span.

    Accepts both v2 spans (evidence_address + quote + quote_hash) and legacy
    v1 spans (source_id + locator + quote). Never guesses a locator.
    """
    spans = [row for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
    closure = {
        "source_span_count": len(spans),
        "exact_address_span_count": 0,
    }
    if not spans:
        return {}, closure
    span = spans[0]
    address = _dict(span.get("evidence_address")) or span
    locator = _text(address.get("locator") or address.get("source_locator"))
    if not locator:
        locator = _text(span.get("locator"))
    quote = _text(span.get("quote") or fact.get("raw_statement"))
    source_id = _text(
        address.get("source_id")
        or span.get("source_id")
        or fact.get("source_id")
    )
    if locator:
        closure["exact_address_span_count"] = 1
    fields = {
        "source_id": source_id,
        "document_block_id": _text(
            address.get("document_block_id") or span.get("document_block_id")
        ),
        "locator": locator,
        "quote": quote,
        # The frame's quote_hash is always computed from the frame's own quote
        # (frame-internal integrity). A stale declared hash in the source span
        # is surfaced in the projection receipt, never used to drop the fact.
        "quote_hash": quote_hash(quote) if quote else "",
        "declared_quote_hash": _text(span.get("quote_hash")),
        "block_type": _text(address.get("block_type") or span.get("block_type")),
        "legacy_fallback": bool(span.get("legacy_fallback") or address.get("legacy_fallback")),
    }
    return fields, closure


def _closure_status(closure: dict[str, Any], quote: str, locator: str) -> str:
    if quote and locator:
        return "PASS"
    if quote:
        return "PARTIAL"
    return "FAIL"


def project_fact_to_semantic_frame(
    fact: dict[str, Any],
    *,
    actor_registry: Iterable[dict[str, Any]] | None = None,
    entity_registry: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project one ledger fact into one Chinese Semantic Frame.

    Raises ValueError (fail-closed) when the resulting frame is invalid; the
    caller records the failure with a receipt instead of dropping it silently.
    """
    if not isinstance(fact, dict):
        raise ValueError("chinese_semantic_frame_projection_fact_not_object")

    frame_type = _frame_type_for_fact(fact)
    if frame_type == "TERM_ALIAS":
        raise ValueError("chinese_semantic_frame_projection_term_alias_not_a_frame")

    span_fields, closure = _source_span_info(fact)
    quote = span_fields.get("quote", "")
    if not quote:
        raise ValueError("chinese_semantic_frame_projection_fact_without_quote")

    frame = empty_frame()
    frame["frame_id"] = _stable_id(
        "csf",
        fact.get("fact_id") or fact.get("statement_frame_id"),
        fact.get("raw_statement") or quote,
        frame_type,
    )
    frame["frame_type"] = frame_type
    frame["source_span"] = {
        "source_id": span_fields["source_id"],
        "document_block_id": span_fields["document_block_id"],
        "locator": span_fields["locator"],
        "quote": quote,
        "quote_hash": span_fields["quote_hash"] or quote_hash(quote),
    }
    document_context = frame["document_context"]
    if span_fields.get("block_type"):
        document_context["table_context"] = {"block_type": span_fields["block_type"]}
    frame["document_context"] = document_context

    origin = {
        "origin_fact_id": _text(fact.get("fact_id")),
        "origin_statement_frame_id": _text(fact.get("statement_frame_id")),
        "origin_fact_type": _text(fact.get("fact_type") or fact.get("kind")),
        "origin_fact_status": _text(fact.get("status")),
        "origin_derivation": _text(fact.get("derivation")),
        "origin_span_quote_hash": span_fields.get("declared_quote_hash", ""),
    }
    frame["origin"] = origin

    reason_codes: list[str] = []
    spans_for_evidence: list[dict[str, Any]] = [
        {"source_id": span_fields["source_id"], "locator": span_fields["locator"], "quote": quote}
    ]
    if span_fields.get("legacy_fallback"):
        reason_codes.append("LEGACY_FALLBACK_USED")

    # ── actor slot ──
    subject = _dict(fact.get("subject"))
    actor_refs = [row for row in _list(subject.get("actor_refs")) if _text(row)]
    actor_index = _registry_index(actor_registry)
    grounded_actors, _unresolved_actors = _resolve_refs(actor_refs, actor_index)
    actor_slot = frame["actor"]
    actor_slot["mentions"] = list(dict.fromkeys(_norm(row) for row in actor_refs))
    if grounded_actors:
        actor_slot["grounded_actor_refs"] = grounded_actors
        actor_slot["resolution_status"] = "GROUNDED"
    elif actor_refs:
        # Mention is explicit at the source layer; grounding stays PENDING.
        actor_slot["resolution_status"] = "RESOLVED"
    elif frame_type in _ACTOR_REQUIRED_FRAME_TYPES:
        actor_slot["resolution_status"] = "OMITTED"
        reason_codes.append("OMITTED_ACTOR_UNRESOLVED")
    actor_slot["evidence"] = spans_for_evidence
    frame["actor"] = actor_slot

    # ── action slot ──
    action = _dict(fact.get("action"))
    raw_action = _norm(action.get("raw") or action.get("canonical"))
    predicate = _norm(fact.get("predicate"))
    action_slot = frame["action"]
    if raw_action:
        action_slot["mentions"] = [raw_action]
        action_slot["resolution_status"] = "RESOLVED"
    elif predicate:
        action_slot["mentions"] = [predicate]
        action_slot["resolution_status"] = "RESOLVED"
    elif _text(fact.get("modality")) in _LEGACY_MODALITY_FRAME_TYPES:
        action_slot["resolution_status"] = "UNKNOWN"
        reason_codes.append("ACTION_CONCEPT_UNRESOLVED")
    action_slot["evidence"] = spans_for_evidence
    frame["action"] = action_slot

    # ── object slot ──
    object_refs = [
        row
        for row in [
            *_list(subject.get("entity_refs")),
            *_list(_dict(fact.get("object")).get("entity_refs")),
        ]
        if _text(row)
    ]
    entity_index = _registry_index(entity_registry)
    grounded_entities, _unresolved_entities = _resolve_refs(object_refs, entity_index)
    object_slot = frame["object"]
    object_slot["mentions"] = list(dict.fromkeys(_norm(row) for row in object_refs))
    if grounded_entities:
        object_slot["grounded_entity_refs"] = grounded_entities
        object_slot["resolution_status"] = "GROUNDED"
    elif object_refs:
        object_slot["resolution_status"] = "RESOLVED"
    object_slot["evidence"] = spans_for_evidence
    frame["object"] = object_slot

    # ── modality slot ──
    modality_text = _norm(fact.get("modality"))
    modality = frame["modality"]
    if modality_text:
        modality_type = modality_text if modality_text in MODALITY_TYPES else ""
        modality["type"] = modality_type
        modality["resolution_status"] = (
            "RESOLVED" if modality_type else "UNSUPPORTED"
        )
    else:
        modality["resolution_status"] = "NOT_MENTIONED"
    frame["modality"] = modality

    # ── conditions slot ──
    condition_frame = _dict(fact.get("condition_frame"))
    raw_conditions = [
        _norm(row)
        for row in [
            *_list(fact.get("conditions")),
            *_list(condition_frame.get("conditions")),
        ]
        if _norm(row)
    ]
    combinator = _norm(
        fact.get("condition_combinator") or condition_frame.get("combinator")
    ) or "main"
    frame["conditions"] = [
        {
            "condition_id": f"condition:{index}",
            "raw": raw,
            "subject_concept_ref": "",
            "field_concept_ref": "",
            "operator": "",
            "value_concept_ref": "",
            "logic_group": combinator,
            "resolution_status": "RESOLVED",
            "evidence": spans_for_evidence,
        }
        for index, raw in enumerate(dict.fromkeys(raw_conditions), start=1)
    ]

    # ── exceptions slot ──
    raw_exceptions = [
        _norm(row)
        for row in [
            *_list(fact.get("exceptions")),
            *_list(fact.get("exception_scope")),
        ]
        if _norm(row)
    ]
    frame["exceptions"] = [
        {
            "exception_id": f"exception:{index}",
            "logic": "AND",
            "clauses": [{"raw": raw}],
            "resolution_status": "RESOLVED",
            "evidence": spans_for_evidence,
        }
        for index, raw in enumerate(dict.fromkeys(raw_exceptions), start=1)
    ]

    # ── scope slot ──
    scope = _dict(fact.get("scope"))
    tenant = _norm(scope.get("tenant"))
    organization = _norm(scope.get("organization"))
    ownership = _norm(scope.get("ownership"))
    data_scope = _norm(scope.get("data_scope"))
    scope_slot = frame["scope"]
    if ownership:
        scope_slot["scope_type"] = "OWNERSHIP"
        # Raw ownership phrase preserved as evidence; the relation structure
        # (target==current_actor) is concept-layer work (P0-B/C) — never
        # inferred here. The raw text stays out of the semantic signature.
        scope_slot["ownership_relation"] = {"raw": ownership}
        reason_codes.append("OWNERSHIP_RELATION_UNRESOLVED")
    elif tenant:
        scope_slot["scope_type"] = "TENANT"
    elif organization:
        scope_slot["scope_type"] = "ORGANIZATION"
    elif data_scope:
        scope_slot["scope_type"] = "DATA_SCOPE"
    if any(_text(value) for value in (tenant, organization, ownership, data_scope)):
        scope_slot["resolution_status"] = "RESOLVED"
    elif frame_type in _SCOPE_REQUIRED_FRAME_TYPES:
        scope_slot["resolution_status"] = "UNKNOWN"
        reason_codes.append("SCOPE_RELATION_UNRESOLVED")
    frame["scope"] = scope_slot

    # ── state transition slot ──
    effects = [row for row in _list(fact.get("state_effects")) if isinstance(row, dict)]
    transition = frame["state_transition"]
    if effects:
        transition["from_states"] = list(
            dict.fromkeys(_norm(row.get("from_state")) for row in effects if _norm(row.get("from_state")))
        )
        transition["to_states"] = list(
            dict.fromkeys(_norm(row.get("to_state")) for row in effects if _norm(row.get("to_state")))
        )
        transition["resolution_status"] = "RESOLVED"
    frame["state_transition"] = transition

    # ── typed constraint slots ──
    frame["quantity_constraints"] = [
        dict(row) for row in _list(fact.get("quantity_constraints")) if isinstance(row, dict)
    ]
    frame["time_constraints"] = [
        dict(row)
        for row in [
            *_list(fact.get("time_window_constraints")),
            *_list(fact.get("temporal_constraints")),
        ]
        if isinstance(row, dict)
    ]
    frame["formula_constraints"] = [
        dict(row) for row in _list(fact.get("formula_constraints")) if isinstance(row, dict)
    ]
    frame["conservation_linkages"] = [
        dict(row)
        for row in _list(fact.get("conservation_linkages"))
        if isinstance(row, dict)
    ]
    frame["process_ordering"] = [
        dict(row)
        for row in _list(fact.get("process_ordering"))
        if isinstance(row, dict)
    ]
    postconditions = [
        _norm(row) for row in _list(fact.get("postconditions")) if _norm(row)
    ]
    for effect in _list(fact.get("data_effects")):
        if not isinstance(effect, dict):
            continue
        statement = _norm(effect.get("statement"))
        if statement and statement not in postconditions:
            postconditions.append(statement)
    frame["postconditions"] = postconditions
    frame["compensations"] = [
        _norm(row)
        for row in [
            *_list(fact.get("compensation")),
            *_list(fact.get("compensations")),
        ]
        if _norm(row)
    ]

    # ── technical grounding (P0-D engine; P0-A never claims grounding) ──
    grounding = frame["technical_grounding"]
    grounding["actor_refs"] = grounded_actors
    grounding["entity_refs"] = grounded_entities
    grounding["status"] = "PENDING"
    reason_codes.append("TECHNICAL_GROUNDING_PENDING")
    frame["technical_grounding"] = grounding

    # ── resolution ──
    closure["status"] = _closure_status(closure, quote, span_fields["locator"])
    if closure["status"] == "FAIL":
        reason_codes.append("DOCUMENT_STRUCTURE_MISSING")
    frame["resolution"] = {
        "status": "PARTIALLY_RESOLVED" if reason_codes else "RESOLVED",
        "reason_codes": sorted(set(reason_codes)),
        "semantic_signature": "",
        "evidence_closure": closure,
    }
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)

    errors = validate_semantic_frame(frame)
    if errors:
        raise ValueError("chinese_semantic_frame_invalid:" + ",".join(sorted(errors)))
    return frame


def project_business_facts_to_semantic_frames(asset: dict[str, Any]) -> dict[str, Any]:
    """Project the compiled business fact ledger into the frame ledger (in place)."""
    ledger = _dict(asset.get("business_fact_ledger"))
    items = [row for row in _list(ledger.get("items")) if isinstance(row, dict)]
    actor_registry, entity_registry = _registries_from_asset(asset)

    frames: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []
    reason_counts: Counter = Counter()

    for fact in items:
        fact_id = _text(fact.get("fact_id") or fact.get("statement_frame_id"))
        frame_type = _frame_type_for_fact(fact)
        if frame_type == "TERM_ALIAS":
            receipt = build_receipt(
                receipt_kind="FACT_PROJECTION",
                frame_id="",
                status="FAIL",
                reason_codes=["TERM_ALIAS_NOT_A_FRAME"],
                payload={"fact_id": fact_id, "frame_type": frame_type},
            )
            receipts.append(receipt)
            skipped.append((fact_id, "TERM_ALIAS_NOT_A_FRAME"))
            reason_counts["TERM_ALIAS_NOT_A_FRAME"] += 1
            continue
        try:
            frame = project_fact_to_semantic_frame(
                fact,
                actor_registry=actor_registry,
                entity_registry=entity_registry,
            )
        except ValueError as exc:
            message = str(exc)
            code = message.split(":")[0]
            if code not in reason_counts:
                reason_counts[code] = 0
            reason_counts[code] += 1
            failures.append((fact_id, message))
            receipts.append(
                build_receipt(
                    receipt_kind="FACT_PROJECTION",
                    frame_id="",
                    status="FAIL",
                    reason_codes=["DOCUMENT_STRUCTURE_MISSING"]
                    if "fact_without_quote" in message
                    else ["UNSUPPORTED_BLOCK_TYPE"],
                    payload={"fact_id": fact_id, "error": message},
                )
            )
            continue
        frames.append(frame)
        origin = _dict(frame.get("origin"))
        declared_hash = _text(origin.get("origin_span_quote_hash"))
        receipts.append(
            build_receipt(
                receipt_kind="FACT_PROJECTION",
                frame_id=frame["frame_id"],
                status="PASS",
                reason_codes=frame["resolution"]["reason_codes"],
                payload={
                    "fact_id": fact_id,
                    "frame_type": frame_type,
                    "evidence_closure": frame["resolution"]["evidence_closure"]["status"],
                    # A stale declared span hash is surfaced, never silent.
                    "quote_hash_discrepancy": bool(declared_hash)
                    and declared_hash != _text(frame["source_span"].get("quote_hash")),
                },
            )
        )

    closure_status = "FAIL" if failures else ("PARTIAL" if skipped else "PASS")
    asset["chinese_semantic_frame_ledger"] = {
        "schema": CHINESE_SEMANTIC_FRAME_LEDGER_SCHEMA,
        "fact_authority": "original_chinese_source_span",
        "translation_intermediate_forbidden": True,
        "items": frames,
        "receipts": receipts,
        "closure": {
            "status": closure_status,
            "fact_count": len(items),
            "frame_count": len(frames),
            "skipped_count": len(skipped),
            "failed_count": len(failures),
            "reason_code_counts": dict(reason_counts),
            "silent_drop_allowed": False,
        },
    }
    return asset


def frames_from_asset(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the frame ledger items (empty when the ledger is absent)."""
    return [
        row for row in _list(_dict(asset.get("chinese_semantic_frame_ledger")).get("items"))
        if isinstance(row, dict)
    ]
