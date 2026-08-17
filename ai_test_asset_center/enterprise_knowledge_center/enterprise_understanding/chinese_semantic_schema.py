"""Chinese Semantic Frame — the single source of truth for Chinese business semantics.

SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1 (P0-A: SSOT & Schema).

Contract (P0-A):
- ``qualibug.chinese-semantic-frame.v1`` is the ONLY intermediate representation
  that may carry Chinese business semantics. Downstream modules must consume
  frames (directly or through the Fact Ledger / Behavior IR adapters) instead
  of re-parsing raw Chinese text; the legacy parsers remain the authoritative
  producers until P0-E but are no longer the final arbiter of meaning.
- Slot statuses follow SPEC §6; empty strings never mean UNKNOWN, confidence is
  never correctness, keyword hits are never GROUNDED.
- Reason codes follow SPEC §16; the forbidden terminal codes
  (parse_failed / unknown_error / no_match) are rejected by validation.
- The semantic signature is computed over typed slots only (never over quote /
  evidence), so equivalent Chinese paraphrases that normalize to the same
  concept refs produce the same signature. ``semantic_structure_payload``
  drops concept refs entirely — it is the naming-invariant structural core
  (SPEC §18.3).
- Validation is fail-closed: an invalid frame reports errors and must never be
  silently accepted by a consumer.

No industry word lists, role tables, action patterns or benchmark vocabulary
live here; P0-A adds no Chinese vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

CHINESE_SEMANTIC_FRAME_SCHEMA = "qualibug.chinese-semantic-frame.v1"

# ── SPEC §6: uniform slot status vocabulary ──
# Empty values must never be expressed as UNKNOWN, high confidence must never
# upgrade a slot to RESOLVED, and a keyword hit must never upgrade a slot to
# GROUNDED. These rules are enforced by the adapters, not by the status set.
SLOT_STATUSES = frozenset(
    {
        "RESOLVED",          # 原文层语义已明确
        "CONCEPT_RESOLVED",  # 已归一为业务概念，尚未绑定技术实现
        "GROUNDED",          # 已绑定到真实技术对象
        "OMITTED",           # 中文省略了该槽位，可能由上下文恢复
        "NOT_MENTIONED",     # 规则本身不涉及该槽位
        "AMBIGUOUS",         # 存在多个合理解释
        "UNKNOWN",           # 当前资料不足以确认
        "CONFLICTING",       # 多个来源给出不兼容结论
        "NOT_APPLICABLE",    # 该类型规则不需要此槽位
        "UNSUPPORTED",       # 当前系统尚不支持该语言结构
    }
)

# ── SPEC §9: first-phase frame types (open list — later phases may extend) ──
FRAME_TYPES = frozenset(
    {
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
        "CONSERVATION_LINKAGE",
        "DATA_VISIBILITY_RULE",
    }
)

# ── Modality vocabulary (typed, mapped from typed fact slots, never guessed) ──
MODALITY_TYPES = frozenset(
    {
        "MUST",
        "MUST_NOT",
        "MAY",
        "ONLY_IF",
        "ASSERTS",
        "INVARIANT",
    }
)

# ── SPEC §16: reason codes (minimum set; typed additions allowed) ──
REASON_CODES = frozenset(
    {
        "DOCUMENT_STRUCTURE_MISSING",
        "UNSUPPORTED_BLOCK_TYPE",
        "CLAUSE_SEGMENTATION_AMBIGUOUS",
        "OMITTED_ACTOR_UNRESOLVED",
        "COREFERENCE_UNRESOLVED",
        "NEGATION_SCOPE_AMBIGUOUS",
        "CONDITION_SCOPE_AMBIGUOUS",
        "EXCEPTION_SCOPE_UNRESOLVED",
        "ACTION_CONCEPT_UNRESOLVED",
        "OBJECT_CONCEPT_UNRESOLVED",
        "ROLE_CONCEPT_UNRESOLVED",
        "OWNERSHIP_RELATION_UNRESOLVED",
        "SCOPE_RELATION_UNRESOLVED",
        "STATE_CONCEPT_UNRESOLVED",
        "MULTIPLE_OPERATION_CANDIDATES",
        "MULTIPLE_FIELD_CANDIDATES",
        "MULTIPLE_ACTOR_CANDIDATES",
        "MULTIPLE_STATE_VALUE_CANDIDATES",
        "SOURCE_CONFLICT",
        "GROUNDING_EVIDENCE_INSUFFICIENT",
        "LEGACY_FALLBACK_USED",
        # Typed additions beyond the SPEC minimum set (SPEC §16 "至少包括").
        "TECHNICAL_GROUNDING_PENDING",  # grounding engine is P0-D; P0-A never claims grounding
        "TERM_ALIAS_NOT_A_FRAME",       # term definitions are not business constraints
        "INVARIANT_PROJECTION_DEFERRED",  # structured-condition invariants land with grounding
    }
)

# ── SPEC §16: forbidden terminal reason codes ──
FORBIDDEN_TERMINAL_REASON_CODES = frozenset(
    {"parse_failed", "unknown_error", "no_match"}
)

# ── Frame-level resolution statuses (SPEC §5 resolution.status) ──
RESOLUTION_STATUSES = frozenset(
    {"RESOLVED", "PARTIALLY_RESOLVED", "UNKNOWN", "CONFLICTING"}
)

# ── Technical grounding statuses (P0-D engine states; P0-A only PENDING) ──
GROUNDING_STATUSES = frozenset(
    {"PENDING", "GROUNDED", "PARTIAL", "CONFLICTING", "UNKNOWN", "NOT_APPLICABLE"}
)

# ── Evidence closure statuses (SPEC §5 resolution.evidence_closure) ──
EVIDENCE_CLOSURE_STATUSES = frozenset({"PASS", "PARTIAL", "FAIL"})

_SIGNATURE_PREFIX = "csf-signature:"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def quote_hash(quote: str) -> str:
    return hashlib.sha256(_text(quote).encode("utf-8")).hexdigest()


def _norm_refs(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _norm(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return sorted(result)


def empty_frame(
    *,
    frame_id: str = "",
    quote: str = "",
    source_id: str = "",
) -> dict[str, Any]:
    """Return a full skeleton frame with SPEC §5 shape and explicit slot statuses.

    Slot statuses are explicit from birth — an empty slot is NOT_MENTIONED or
    NOT_APPLICABLE, never an empty string standing for UNKNOWN.
    """
    quote_text = _text(quote)
    return {
        "schema_version": CHINESE_SEMANTIC_FRAME_SCHEMA,
        "frame_id": _text(frame_id),
        "source_span": {
            "source_id": _text(source_id),
            "document_block_id": "",
            "locator": "",
            "quote": quote_text,
            "quote_hash": quote_hash(quote_text),
        },
        "document_context": {
            "document_type": "",
            "section_path": [],
            "heading": "",
            "list_parent": "",
            "table_context": {},
            "previous_frame_refs": [],
            "next_frame_refs": [],
        },
        "frame_type": "",
        "actor": {
            "mentions": [],
            "concept_refs": [],
            "grounded_actor_refs": [],
            "resolution_status": "NOT_MENTIONED",
            "evidence": [],
        },
        "action": {
            "mentions": [],
            "canonical_concept_refs": [],
            "grounded_operation_refs": [],
            "resolution_status": "NOT_MENTIONED",
            "evidence": [],
        },
        "object": {
            "mentions": [],
            "concept_refs": [],
            "grounded_entity_refs": [],
            "resolution_status": "NOT_MENTIONED",
            "evidence": [],
        },
        "modality": {
            "type": "",
            "raw_marker": "",
            "scope_refs": [],
            "resolution_status": "NOT_MENTIONED",
        },
        "conditions": [],
        "exceptions": [],
        "scope": {
            "scope_type": "",
            "ownership_relation": {},
            "organization_relation": {},
            "tenant_relation": {},
            "resolution_status": "NOT_MENTIONED",
        },
        "state_transition": {
            "from_states": [],
            "to_states": [],
            "resolution_status": "NOT_APPLICABLE",
        },
        "quantity_constraints": [],
        "time_constraints": [],
        "formula_constraints": [],
        "conservation_linkages": [],
        "process_ordering": [],
        "postconditions": [],
        "compensations": [],
        "technical_grounding": {
            "operation_refs": [],
            "entity_refs": [],
            "field_refs": [],
            "actor_refs": [],
            "state_value_refs": [],
            "status": "PENDING",
        },
        "resolution": {
            "status": "PARTIALLY_RESOLVED",
            "reason_codes": [],
            "semantic_signature": "",
            "evidence_closure": {
                "status": "PARTIAL",
                "source_span_count": 0,
                "exact_address_span_count": 0,
            },
        },
    }


# ── Semantic signature ──
# The signature is the dedup/merge authority (like
# structured_fact_compiler._semantic_signature) but computed over frame slots:
# typed structure + normalized concept refs. Quote, raw constraint wording,
# provenance/evidence, frame_id and resolution metadata never participate.
# Two frames that express the same business semantics (after concept
# normalization) collide on one signature.


def _condition_signature(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_concept_ref": _text(row.get("subject_concept_ref")),
        "field_concept_ref": _text(row.get("field_concept_ref")),
        "operator": _text(row.get("operator")),
        "value_concept_ref": _text(row.get("value_concept_ref")),
        "logic_group": _text(row.get("logic_group")),
    }


def _exception_signature(row: dict[str, Any]) -> dict[str, Any]:
    clauses = []
    for clause in _list(_dict(row).get("clauses")):
        if not isinstance(clause, dict):
            continue
        clauses.append(
            {
                "field_concept_ref": _text(clause.get("field_concept_ref")),
                "operator": _text(clause.get("operator")),
                "value_concept_ref": _text(clause.get("value_concept_ref")),
                "actor_concept_ref": _text(clause.get("actor_concept_ref")),
                "action_concept_ref": _text(clause.get("action_concept_ref")),
                "result": _text(clause.get("result")),
            }
        )
    return {
        "logic": _text(row.get("logic")),
        "clauses": sorted(clauses, key=_canonical_json),
    }


_CONSTRAINT_PROVENANCE_KEYS = frozenset(
    {
        "confidence",
        "constraint_id",
        "derivation",
        "document_block_id",
        "evidence",
        "locator",
        "origin",
        "quote",
        "quote_hash",
        "raw",
        "resolution_status",
        "source_backed",
        "source_id",
        "source_refs",
        "source_span",
    }
)


def _constraint_semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _text(key): _constraint_semantic_value(item)
            for key, item in sorted(value.items())
            if _text(key) not in _CONSTRAINT_PROVENANCE_KEYS
        }
    if isinstance(value, list):
        return [_constraint_semantic_value(item) for item in value]
    return value


def _constraint_signatures(rows: Iterable[Any]) -> list[str]:
    result = []
    for row in rows:
        if isinstance(row, dict):
            result.append(_canonical_json(_constraint_semantic_value(row)))
    return sorted(set(result))


def semantic_signature_payload(frame: dict[str, Any]) -> dict[str, Any]:
    actor = _dict(frame.get("actor"))
    action = _dict(frame.get("action"))
    obj = _dict(frame.get("object"))
    scope = _dict(frame.get("scope"))
    modality = _dict(frame.get("modality"))
    transition = _dict(frame.get("state_transition"))
    return {
        "frame_type": _text(frame.get("frame_type")),
        "modality_type": _text(modality.get("type")),
        "actor_concept_refs": _norm_refs(_list(actor.get("concept_refs"))),
        "action_concept_refs": _norm_refs(_list(action.get("canonical_concept_refs"))),
        "object_concept_refs": _norm_refs(_list(obj.get("concept_refs"))),
        "ownership_relation": {
            # Structured relation kinds only — raw surface text ("只能使用自己的")
            # must never leak into the semantic signature; it is not a typed slot.
            _text(key): sorted(_norm(v) for v in value) if isinstance(value, list) else _norm(value)
            for key, value in sorted(_dict(scope.get("ownership_relation")).items())
            if _text(key) != "raw" and _norm(value)
        },
        "scope_type": _text(scope.get("scope_type")),
        "conditions": sorted(
            (_condition_signature(row) for row in _list(frame.get("conditions")) if isinstance(row, dict)),
            key=_canonical_json,
        ),
        "exceptions": sorted(
            (_exception_signature(row) for row in _list(frame.get("exceptions")) if isinstance(row, dict)),
            key=_canonical_json,
        ),
        "state_transition": {
            "from_states": _norm_refs(_list(transition.get("from_states"))),
            "to_states": _norm_refs(_list(transition.get("to_states"))),
        },
        "quantity_constraints": _constraint_signatures(_list(frame.get("quantity_constraints"))),
        "time_constraints": _constraint_signatures(_list(frame.get("time_constraints"))),
        "formula_constraints": _constraint_signatures(_list(frame.get("formula_constraints"))),
        "conservation_linkages": _constraint_signatures(_list(frame.get("conservation_linkages"))),
        "process_ordering": _constraint_signatures(_list(frame.get("process_ordering"))),
        "postconditions": _norm_refs(_list(frame.get("postconditions"))),
        "compensations": _norm_refs(_list(frame.get("compensations"))),
    }


def semantic_signature(frame: dict[str, Any]) -> str:
    payload = semantic_signature_payload(frame)
    encoded = _canonical_json(payload)
    return _SIGNATURE_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def semantic_structure_payload(frame: dict[str, Any]) -> dict[str, Any]:
    """Naming-invariant structural core (SPEC §18.3).

    Drops every concept ref and state value, keeping only the structural kinds:
    frame type, modality, ownership relation *kind*, scope type, condition and
    exception *structure*, and which constraint families are present. Renaming
    business objects / roles / industries must not change this payload.
    """
    scope = _dict(frame.get("scope"))
    conditions = []
    for row in _list(frame.get("conditions")):
        if not isinstance(row, dict):
            continue
        conditions.append(
            {
                "operator": _text(row.get("operator")),
                "logic_group": _text(row.get("logic_group")),
            }
        )
    exceptions = []
    for row in _list(frame.get("exceptions")):
        if not isinstance(row, dict):
            continue
        exceptions.append(
            {
                "logic": _text(row.get("logic")),
                "clause_count": len(
                    [c for c in _list(row.get("clauses")) if isinstance(c, dict)]
                ),
            }
        )
    constraint_keys = {
        "quantity": "quantity_constraints",
        "time": "time_constraints",
        "formula": "formula_constraints",
        "postcondition": "postconditions",
        "compensation": "compensations",
    }
    # Ownership relation structure: the typed kind values (e.g. "OWN",
    # "current_actor") — the raw surface phrase never participates.
    ownership_relation = _dict(scope.get("ownership_relation"))
    ownership_kinds = sorted(
        _norm(value)
        for key, value in ownership_relation.items()
        if _text(key) != "raw" and _norm(value)
    )
    return {
        "frame_type": _text(frame.get("frame_type")),
        "modality_type": _text(_dict(frame.get("modality")).get("type")),
        "ownership_relation_kinds": ownership_kinds,
        "scope_type": _text(scope.get("scope_type")),
        "condition_structure": sorted(conditions, key=_canonical_json),
        "exception_structure": sorted(exceptions, key=_canonical_json),
        "has_state_transition": bool(
            _list(_dict(frame.get("state_transition")).get("from_states"))
            or _list(_dict(frame.get("state_transition")).get("to_states"))
        ),
        "constraint_families": sorted(
            family
            for family, key in constraint_keys.items()
            if _list(frame.get(key))
        ),
    }


# ── Fail-closed validation ──


def validate_semantic_frame(frame: dict[str, Any]) -> list[str]:
    """Return structural violations; an empty list means the frame is valid.

    Never repairs the frame and never downgrades an error to a warning.
    """
    errors: list[str] = []
    if not isinstance(frame, dict):
        return ["frame_not_object"]
    if _text(frame.get("schema_version")) != CHINESE_SEMANTIC_FRAME_SCHEMA:
        errors.append("schema_version_mismatch")
    if not _text(frame.get("frame_id")):
        errors.append("frame_id_missing")
    if _text(frame.get("frame_type")) not in FRAME_TYPES:
        errors.append("frame_type_invalid")
    source_span = _dict(frame.get("source_span"))
    if not _text(source_span.get("source_id")):
        errors.append("source_span_source_id_missing")
    quote = _text(source_span.get("quote"))
    if not quote:
        errors.append("source_span_quote_missing")
    if quote and _text(source_span.get("quote_hash")) != quote_hash(quote):
        errors.append("source_span_quote_hash_mismatch")

    modality = _dict(frame.get("modality"))
    if modality:
        if _text(modality.get("type")) and _text(modality.get("type")) not in MODALITY_TYPES:
            errors.append(f"modality_type_invalid:{_text(modality.get('type'))}")
        if _status_of(modality) not in SLOT_STATUSES:
            errors.append(f"modality_status_invalid:{_status_of(modality)}")

    for slot_name in ("actor", "action", "object", "scope", "state_transition"):
        slot = _dict(frame.get(slot_name))
        if _status_of(slot) not in SLOT_STATUSES:
            errors.append(f"{slot_name}_status_invalid:{_status_of(slot)}")

    conditions = _list(frame.get("conditions"))
    for index, row in enumerate(conditions):
        if not isinstance(row, dict):
            errors.append(f"condition_not_object:{index}")
            continue
        if not _text(row.get("condition_id")):
            errors.append(f"condition_id_missing:{index}")
        if _status_of(row) not in SLOT_STATUSES:
            errors.append(f"condition_status_invalid:{index}:{_status_of(row)}")

    exceptions = _list(frame.get("exceptions"))
    for index, row in enumerate(exceptions):
        if not isinstance(row, dict):
            errors.append(f"exception_not_object:{index}")
            continue
        if not _text(row.get("exception_id")):
            errors.append(f"exception_id_missing:{index}")
        if _status_of(row) not in SLOT_STATUSES:
            errors.append(f"exception_status_invalid:{index}:{_status_of(row)}")

    grounding = _dict(frame.get("technical_grounding"))
    if _text(grounding.get("status")) not in GROUNDING_STATUSES:
        errors.append(f"grounding_status_invalid:{_text(grounding.get('status'))}")

    resolution = _dict(frame.get("resolution"))
    if _text(resolution.get("status")) not in RESOLUTION_STATUSES:
        errors.append(f"resolution_status_invalid:{_text(resolution.get('status'))}")
    reason_codes = _list(resolution.get("reason_codes"))
    for code in reason_codes:
        code_text = _text(code)
        if code_text not in REASON_CODES:
            errors.append(f"reason_code_invalid:{code_text}")
        if code_text in FORBIDDEN_TERMINAL_REASON_CODES:
            errors.append(f"forbidden_terminal_reason_code:{code_text}")
    signature = _text(resolution.get("semantic_signature"))
    if not signature:
        errors.append("semantic_signature_missing")
    elif not signature.startswith(_SIGNATURE_PREFIX):
        errors.append("semantic_signature_prefix_invalid")
    elif signature != semantic_signature(frame):
        errors.append("semantic_signature_mismatch")
    closure = _dict(resolution.get("evidence_closure"))
    if _text(closure.get("status")) not in EVIDENCE_CLOSURE_STATUSES:
        errors.append(f"evidence_closure_status_invalid:{_text(closure.get('status'))}")
    return errors


def _status_of(slot: dict[str, Any]) -> str:
    return _text(slot.get("resolution_status"))
