"""Chinese Semantic Frame → Behavior IR projection adapter (SPEC P0-A).

P0-A contract:
- Only GROUNDED frame slots may produce contributions. UNKNOWN / OMITTED /
  NOT_MENTIONED slots never emit relations — they are recorded as skip reasons
  in the projection receipt (``qualibug.behavior-ir-semantic-frame-projection.v1``
  content is carried inside the typed receipt).
- Supported contribution kinds in P0-A: ``owns`` (structured ownership relation
  with grounded actor + entity), ``permits`` / ``denies`` (PERMISSION_RULE with
  grounded actor + operation). State-transition and invariant contributions are
  deliberately deferred until the grounding engine (P0-D) resolves state and
  condition slots; the skip is receipted, never silent.
- Endpoints must resolve against the Behavior IR node index; a contribution
  whose endpoint names nothing is skipped with GROUNDING_EVIDENCE_INSUFFICIENT
  (a dangling relation would look present while being inert).
- ``apply_semantic_frames_to_behavior_ir`` merges contributions into an IR
  model by deterministic node id, so repeated application never duplicates and
  legacy relations are never overwritten. Provenance rides in ``source_refs``
  (kind=chinese_semantic_frame, frame_id, quote) — relation field sets and the
  behavior-ir.v2 schema are untouched.
- In P0-A production the frames are ungrounded (TECHNICAL_GROUNDING_PENDING),
  so the projection adds nothing; the capability is proven by synthetic
  grounded frames in tests and becomes active when grounding lands.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable

from .chinese_semantic_ledger_adapter import frames_from_asset
from .chinese_semantic_receipts import build_receipt
from .chinese_semantic_schema import (
    validate_semantic_frame,
)

CHINESE_SEMANTIC_BEHAVIOR_IR_PROJECTION_SCHEMA = (
    "qualibug.behavior-ir-semantic-frame-projection.v1"
)

_CONTRIBUTION_KINDS = frozenset({"owns", "permits", "denies"})

_PERMISSION_MODALITY_TYPES = frozenset({"MAY", "MUST", "ONLY_IF"})

_OWNERSHIP_FRAME_TYPES = frozenset(
    {"OWNERSHIP_RULE", "PERMISSION_RULE", "SCOPE_RULE", "DATA_VISIBILITY_RULE"}
)

# ref kinds the endpoint resolver understands
_REF_KINDS = frozenset({"actor", "entity", "operation"})


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _default_relation_builder(contribution: dict[str, Any]) -> dict[str, Any]:
    """Standalone builder: deterministic content-addressed relation node id.

    Behavior IR core passes its own builder so ids match the legacy
    ``_stable_id("rel", ...)`` scheme and dedup covers legacy relations.
    """
    payload = {
        "relation_type": _text(contribution.get("relation_type")),
        "from_ref": _text(contribution.get("from_ref")),
        "to_ref": _text(contribution.get("to_ref")),
        "operation_ref": _text(contribution.get("operation_ref")),
        "actor_ref": _text(contribution.get("actor_ref")),
        "scope": _text(contribution.get("scope")),
    }
    node_id = "csf_rel:" + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "id": node_id,
        **payload,
        "source_refs": [dict(row) for row in _list(contribution.get("source_refs"))],
        "confidence": 0.8,
        "derivation": "schema-derived",
        "status": "accepted",
    }


def _first_grounded(slot: dict[str, Any], field: str) -> str:
    values = [
        _text(row) for row in _list(slot.get(field)) if _text(row)
    ]
    return values[0] if values else ""


def _ref_resolves(
    ref: str,
    kind: str,
    ref_resolver: Callable[[str, str], bool] | None,
) -> bool:
    if not ref:
        return False
    if ref_resolver is None:
        return True
    if kind not in _REF_KINDS:
        return False
    return bool(ref_resolver(kind, ref))


def project_semantic_frames_to_behavior_ir(
    frames: Iterable[dict[str, Any]],
    *,
    ref_resolver: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Project grounded frame slots into typed relation contributions.

    Raises ValueError on an invalid frame (fail-closed — a frame that failed
    schema validation must never be half-consumed).
    """
    contributions: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    frames_considered = 0
    frames_with_contributions = 0

    def _count_reason(code: str) -> None:
        reason_counts[code] = reason_counts.get(code, 0) + 1

    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("chinese_semantic_frame_projection_frame_not_object")
        errors = validate_semantic_frame(frame)
        if errors:
            raise ValueError(
                "chinese_semantic_frame_invalid:" + ",".join(sorted(errors))
            )
        frames_considered += 1
        frame_id = _text(frame.get("frame_id"))
        frame_type = _text(frame.get("frame_type"))
        actor = _dict(frame.get("actor"))
        action = _dict(frame.get("action"))
        obj = _dict(frame.get("object"))
        scope = _dict(frame.get("scope"))
        modality = _dict(frame.get("modality"))

        source_refs = [
            {
                "kind": "chinese_semantic_frame",
                "frame_id": frame_id,
                "quote": _text(_dict(frame.get("source_span")).get("quote")),
            }
        ]
        actor_ref = _first_grounded(actor, "grounded_actor_refs")
        entity_ref = _first_grounded(obj, "grounded_entity_refs")
        operation_ref = _first_grounded(action, "grounded_operation_refs")

        emitted = False

        # ── owns: structured ownership relation (kind key other than raw) ──
        ownership_relation = _dict(scope.get("ownership_relation"))
        structured_ownership = [
            key for key in ownership_relation if _text(key) != "raw"
        ]
        if (
            frame_type in _OWNERSHIP_FRAME_TYPES
            and structured_ownership
        ):
            if not actor_ref or not entity_ref:
                _count_reason("OWNERSHIP_RELATION_UNRESOLVED")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "reason_code": "OWNERSHIP_RELATION_UNRESOLVED",
                    }
                )
            elif not _ref_resolves(actor_ref, "actor", ref_resolver) or not _ref_resolves(
                entity_ref, "entity", ref_resolver
            ):
                _count_reason("GROUNDING_EVIDENCE_INSUFFICIENT")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
                    }
                )
            else:
                contributions.append(
                    {
                        "contribution_kind": "RELATION",
                        "relation_type": "owns",
                        "from_ref": actor_ref,
                        "to_ref": entity_ref,
                        "actor_ref": actor_ref,
                        "operation_ref": "",
                        "scope": _text(scope.get("scope_type")),
                        "frame_id": frame_id,
                        "source_refs": source_refs,
                    }
                )
                emitted = True

        # ── permits / denies: PERMISSION_RULE with grounded actor + operation ──
        modality_type = _text(modality.get("type"))
        if frame_type == "PERMISSION_RULE" and modality_type:
            relation_type = ""
            if modality_type == "MUST_NOT":
                relation_type = "denies"
            elif modality_type in _PERMISSION_MODALITY_TYPES:
                relation_type = "permits"
            if relation_type:
                if not actor_ref:
                    _count_reason("OMITTED_ACTOR_UNRESOLVED")
                    skips.append(
                        {
                            "frame_id": frame_id,
                            "reason_code": "OMITTED_ACTOR_UNRESOLVED",
                        }
                    )
                elif not operation_ref:
                    _count_reason("ACTION_CONCEPT_UNRESOLVED")
                    skips.append(
                        {
                            "frame_id": frame_id,
                            "reason_code": "ACTION_CONCEPT_UNRESOLVED",
                        }
                    )
                elif not _ref_resolves(actor_ref, "actor", ref_resolver) or not _ref_resolves(
                    operation_ref, "operation", ref_resolver
                ):
                    _count_reason("GROUNDING_EVIDENCE_INSUFFICIENT")
                    skips.append(
                        {
                            "frame_id": frame_id,
                            "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
                        }
                    )
                else:
                    contributions.append(
                        {
                            "contribution_kind": "RELATION",
                            "relation_type": relation_type,
                            "from_ref": actor_ref,
                            "to_ref": operation_ref,
                            "actor_ref": actor_ref,
                            "operation_ref": operation_ref,
                            "scope": _text(scope.get("scope_type")),
                            "frame_id": frame_id,
                            "source_refs": source_refs,
                        }
                    )
                    emitted = True

        # ── deferred families (P0-D): state transitions and structured
        # invariants need grounded state/condition slots before they can emit
        # relations; the deferral is receipted, never silent.
        if (
            frame_type == "STATE_TRANSITION"
            and (
                _list(_dict(frame.get("state_transition")).get("from_states"))
                or _list(_dict(frame.get("state_transition")).get("to_states"))
            )
        ):
            _count_reason("INVARIANT_PROJECTION_DEFERRED")
            skips.append(
                {
                    "frame_id": frame_id,
                    "reason_code": "INVARIANT_PROJECTION_DEFERRED",
                }
            )
            continue

        if not emitted:
            # Ungrounded frames are the P0-A norm; record the skip, never a guess.
            _count_reason("TECHNICAL_GROUNDING_PENDING")
            skips.append(
                {
                    "frame_id": frame_id,
                    "reason_code": "TECHNICAL_GROUNDING_PENDING",
                }
            )
        else:
            frames_with_contributions += 1

    receipt = build_receipt(
        receipt_kind="BEHAVIOR_IR_PROJECTION",
        status="PARTIAL" if skips else "PASS",
        reason_codes=sorted(reason_counts),
        payload={
            "frames_considered": frames_considered,
            "frames_with_contributions": frames_with_contributions,
            "contribution_count": len(contributions),
            "skip_count": len(skips),
            "reason_code_counts": dict(sorted(reason_counts.items())),
        },
    )
    return {
        "schema": CHINESE_SEMANTIC_BEHAVIOR_IR_PROJECTION_SCHEMA,
        "status": "PARTIAL" if skips else "PASS",
        "contributions": contributions,
        "skips": skips,
        "receipt": receipt,
    }


def apply_semantic_frames_to_behavior_ir(
    model: dict[str, Any],
    frames: Iterable[dict[str, Any]],
    *,
    relation_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ref_resolver: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Merge frame contributions into a Behavior IR model (dedup by node id).

    Existing relation ids are never overwritten; only genuinely new grounded
    relations are added. Returns the projection receipt and stores it on the
    model as ``semantic_frame_projection_receipt``.
    """
    result = project_semantic_frames_to_behavior_ir(frames, ref_resolver=ref_resolver)
    relations = [row for row in _list(model.get("relations")) if isinstance(row, dict)]
    existing_ids = {_text(row.get("id")) for row in relations if _text(row.get("id"))}
    builder = relation_builder or _default_relation_builder
    added = 0
    merged = 0
    for contribution in result["contributions"]:
        node = builder(contribution)
        node_id = _text(node.get("id"))
        if not node_id or node_id in existing_ids:
            merged += 1
            continue
        relations.append(node)
        existing_ids.add(node_id)
        added += 1
    model["relations"] = relations
    receipt = dict(result["receipt"])
    receipt["payload"] = dict(receipt["payload"])
    receipt["payload"]["added_count"] = added
    receipt["payload"]["deduped_count"] = merged
    model["semantic_frame_projection_receipt"] = receipt
    return receipt
