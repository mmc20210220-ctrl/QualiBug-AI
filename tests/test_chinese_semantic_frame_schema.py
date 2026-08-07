"""P0-A: Chinese Semantic Frame schema — statuses, reason codes, signature.

Covers SPEC §6 (slot status vocabulary), §16 (reason codes / forbidden
terminals), §18.2 (paraphrase invariance at typed-slot level) and §18.3
(naming invariance of the structural core).
"""

from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_schema import (
    CHINESE_SEMANTIC_FRAME_SCHEMA,
    FORBIDDEN_TERMINAL_REASON_CODES,
    FRAME_TYPES,
    MODALITY_TYPES,
    REASON_CODES,
    SLOT_STATUSES,
    empty_frame,
    semantic_signature,
    semantic_structure_payload,
    validate_semantic_frame,
)


def _valid_frame(*, frame_id: str = "csf:test", quote: str = "普通用户只能使用自己的用户ID。") -> dict:
    frame = empty_frame(frame_id=frame_id, quote=quote, source_id="source:test")
    frame["frame_type"] = "PERMISSION_RULE"
    frame["actor"] = {
        "mentions": ["普通用户"],
        "concept_refs": ["concept:regular_user"],
        "grounded_actor_refs": [],
        "resolution_status": "CONCEPT_RESOLVED",
        "evidence": [],
    }
    frame["action"] = {
        "mentions": ["使用"],
        "canonical_concept_refs": ["concept:use"],
        "grounded_operation_refs": [],
        "resolution_status": "CONCEPT_RESOLVED",
        "evidence": [],
    }
    frame["object"] = {
        "mentions": ["用户ID"],
        "concept_refs": ["concept:user_id"],
        "grounded_entity_refs": [],
        "resolution_status": "CONCEPT_RESOLVED",
        "evidence": [],
    }
    frame["modality"] = {
        "type": "ONLY_IF",
        "raw_marker": "只能",
        "scope_refs": [],
        "resolution_status": "RESOLVED",
    }
    frame["scope"] = {
        "scope_type": "OWNERSHIP",
        "ownership_relation": {"kind": "OWN", "target": "current_actor"},
        "organization_relation": {},
        "tenant_relation": {},
        "resolution_status": "RESOLVED",
    }
    frame["conditions"] = [
        {
            "condition_id": "condition:1",
            "raw": "订单已支付",
            "subject_concept_ref": "concept:order",
            "field_concept_ref": "concept:order_status",
            "operator": "EQUALS",
            "value_concept_ref": "state:paid",
            "logic_group": "main",
            "resolution_status": "CONCEPT_RESOLVED",
            "evidence": [],
        }
    ]
    frame["technical_grounding"]["status"] = "GROUNDED"
    frame["resolution"]["reason_codes"] = []
    frame["resolution"]["status"] = "RESOLVED"
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    return frame


def test_schema_constant_is_the_ssot() -> None:
    assert CHINESE_SEMANTIC_FRAME_SCHEMA == "qualibug.chinese-semantic-frame.v1"
    # SPEC §6 status vocabulary is exactly the closed set.
    assert SLOT_STATUSES == frozenset(
        {
            "RESOLVED",
            "CONCEPT_RESOLVED",
            "GROUNDED",
            "OMITTED",
            "NOT_MENTIONED",
            "AMBIGUOUS",
            "UNKNOWN",
            "CONFLICTING",
            "NOT_APPLICABLE",
            "UNSUPPORTED",
        }
    )
    # SPEC §16 minimum reason code set is present.
    for code in (
        "DOCUMENT_STRUCTURE_MISSING",
        "OMITTED_ACTOR_UNRESOLVED",
        "COREFERENCE_UNRESOLVED",
        "NEGATION_SCOPE_AMBIGUOUS",
        "EXCEPTION_SCOPE_UNRESOLVED",
        "SOURCE_CONFLICT",
        "GROUNDING_EVIDENCE_INSUFFICIENT",
        "LEGACY_FALLBACK_USED",
    ):
        assert code in REASON_CODES
    # Forbidden terminal codes are never legal reason codes.
    assert FORBIDDEN_TERMINAL_REASON_CODES == frozenset(
        {"parse_failed", "unknown_error", "no_match"}
    )
    assert not (FORBIDDEN_TERMINAL_REASON_CODES & REASON_CODES)


def test_spec_9_frame_types_are_present() -> None:
    for frame_type in (
        "PERMISSION_RULE",
        "OWNERSHIP_RULE",
        "SCOPE_RULE",
        "VALIDATION_RULE",
        "STATE_TRANSITION",
        "STATE_GUARD",
        "QUANTITY_CONSTRAINT",
        "FORMULA_CONSTRAINT",
        "TIME_WINDOW_CONSTRAINT",
        "UNIQUENESS_CONSTRAINT",
        "CARDINALITY_CONSTRAINT",
        "RELATION_CONSTRAINT",
        "POSTCONDITION",
        "COMPENSATION_RULE",
        "PROCESS_ORDERING",
        "DATA_VISIBILITY_RULE",
    ):
        assert frame_type in FRAME_TYPES


def test_valid_frame_passes_validation() -> None:
    assert validate_semantic_frame(_valid_frame()) == []


def test_validate_rejects_unknown_slot_status() -> None:
    frame = _valid_frame()
    frame["actor"]["resolution_status"] = "PARSED"
    errors = validate_semantic_frame(frame)
    assert "actor_status_invalid:PARSED" in errors


def test_validate_rejects_unknown_reason_code_and_forbidden_terminal() -> None:
    frame = _valid_frame()
    frame["resolution"]["reason_codes"] = ["no_match"]
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    errors = validate_semantic_frame(frame)
    assert "forbidden_terminal_reason_code:no_match" in errors

    frame = _valid_frame()
    frame["resolution"]["reason_codes"] = ["fancy_new_code"]
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    errors = validate_semantic_frame(frame)
    assert "reason_code_invalid:fancy_new_code" in errors


def test_validate_rejects_tampered_signature() -> None:
    frame = _valid_frame()
    frame["actor"]["concept_refs"] = ["concept:admin"]
    errors = validate_semantic_frame(frame)
    assert "semantic_signature_mismatch" in errors


def test_validate_rejects_missing_quote_and_frame_id() -> None:
    frame = _valid_frame()
    frame["source_span"]["quote"] = ""
    frame["source_span"]["quote_hash"] = ""
    errors = validate_semantic_frame(frame)
    assert "source_span_quote_missing" in errors

    frame = _valid_frame()
    frame["frame_id"] = ""
    errors = validate_semantic_frame(frame)
    assert "frame_id_missing" in errors


def test_validate_rejects_bad_modality_type() -> None:
    frame = _valid_frame()
    frame["modality"]["type"] = "PERHAPS"
    frame["modality"]["resolution_status"] = "RESOLVED"
    errors = validate_semantic_frame(frame)
    assert "modality_type_invalid:PERHAPS" in errors


def test_signature_is_stable_under_quote_and_evidence_rewrites() -> None:
    # SPEC §18.2: the signature lives on typed slots, never on the quote,
    # mentions or evidence — two paraphrases that normalize to the same slots
    # must collide on one signature.
    first = _valid_frame(frame_id="csf:a", quote="普通用户只能使用自己的用户ID。")
    second = _valid_frame(frame_id="csf:b", quote="目标用户必须与当前登录用户一致。")
    second["actor"]["mentions"] = ["目标用户"]
    second["action"]["mentions"] = ["必须一致"]
    second["object"]["mentions"] = ["登录用户"]
    second["source_span"]["locator"] = "prd.docx#paragraph=99"
    second["resolution"]["semantic_signature"] = semantic_signature(second)
    assert validate_semantic_frame(second) == []
    assert semantic_signature(first) == semantic_signature(second)


def test_signature_ignores_raw_ownership_surface_text() -> None:
    # The raw ownership phrase is evidence, not a typed slot; different
    # phrasings of the same ownership relation keep one signature.
    first = _valid_frame()
    second = _valid_frame(frame_id="csf:c")
    second["scope"]["ownership_relation"] = {"kind": "OWN", "target": "current_actor", "raw": "仅本人名下"}
    second["resolution"]["semantic_signature"] = semantic_signature(second)
    assert semantic_signature(first) == semantic_signature(second)


def test_signature_differs_when_typed_slots_differ() -> None:
    first = _valid_frame()
    second = _valid_frame(frame_id="csf:d")
    second["modality"]["type"] = "MUST_NOT"
    second["modality"]["resolution_status"] = "RESOLVED"
    second["resolution"]["semantic_signature"] = semantic_signature(second)
    assert semantic_signature(first) != semantic_signature(second)


def test_structure_payload_is_naming_invariant() -> None:
    # SPEC §18.3: renaming business objects / roles / industries keeps the
    # structural core identical even though concept refs change.
    original = _valid_frame()
    renamed = _valid_frame(frame_id="csf:e")
    renamed["actor"]["concept_refs"] = ["concept:subject"]
    renamed["action"]["canonical_concept_refs"] = ["concept:submit"]
    renamed["object"]["concept_refs"] = ["concept:record_key"]
    renamed["conditions"][0]["subject_concept_ref"] = "concept:ticket"
    renamed["conditions"][0]["field_concept_ref"] = "concept:ticket_status"
    renamed["conditions"][0]["value_concept_ref"] = "state:submitted"
    renamed["resolution"]["semantic_signature"] = semantic_signature(renamed)

    assert semantic_signature(original) != semantic_signature(renamed)
    assert semantic_structure_payload(original) == semantic_structure_payload(renamed)
    structure = semantic_structure_payload(renamed)
    assert structure["frame_type"] == "PERMISSION_RULE"
    assert structure["modality_type"] == "ONLY_IF"
    assert "OWN" in structure["ownership_relation_kinds"]
    assert structure["scope_type"] == "OWNERSHIP"
    assert structure["condition_structure"] == [
        {"operator": "EQUALS", "logic_group": "main"}
    ]


def test_empty_frame_slots_are_explicit_not_implicit() -> None:
    frame = empty_frame(frame_id="csf:empty", quote="x", source_id="s")
    # Empty slots carry explicit statuses — never empty strings standing for
    # UNKNOWN (SPEC §6 forbidden mapping).
    assert frame["actor"]["resolution_status"] == "NOT_MENTIONED"
    assert frame["scope"]["resolution_status"] == "NOT_MENTIONED"
    assert frame["state_transition"]["resolution_status"] == "NOT_APPLICABLE"
    assert frame["technical_grounding"]["status"] == "PENDING"


def test_modality_vocabulary_is_closed() -> None:
    assert MODALITY_TYPES == frozenset(
        {"MUST", "MUST_NOT", "MAY", "ONLY_IF", "ASSERTS", "INVARIANT"}
    )
