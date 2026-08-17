"""Chinese Semantic Frame → Behavior IR projection adapter.

P0-A contract:
- Only GROUNDED frame slots may produce contributions. UNKNOWN / OMITTED /
  NOT_MENTIONED slots never emit relations — they are recorded as skip reasons
  in the projection receipt (``qualibug.behavior-ir-semantic-frame-projection.v1``
  content is carried inside the typed receipt).
- Supported relation contributions are ``owns`` (structured ownership relation
  with grounded actor + entity) and ``permits`` / ``denies`` (PERMISSION_RULE
  with grounded actor + operation). Source-backed fixed-duration time windows
  additionally become temporal invariants only when exactly one real operation
  is grounded. Calendar-sensitive windows stay visibly unresolved.
- Endpoints must resolve against the Behavior IR node index; a contribution
  whose endpoint names nothing is skipped with GROUNDING_EVIDENCE_INSUFFICIENT
  (a dangling relation would look present while being inert).
- ``apply_semantic_frames_to_behavior_ir`` merges contributions into an IR
  model by deterministic node id, so repeated application never duplicates and
  legacy relations are never overwritten. Provenance rides in ``source_refs``
  (kind=chinese_semantic_frame, frame_id, quote) — relation field sets and the
  behavior-ir.v2 schema are untouched.
- Ungrounded frames add nothing and remain visibly
  TECHNICAL_GROUNDING_PENDING; grounded contributions use the same production
  path covered by the integration tests.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable

from .chinese_semantic_receipts import build_receipt
from .chinese_semantic_schema import (
    validate_semantic_frame,
)

CHINESE_SEMANTIC_BEHAVIOR_IR_PROJECTION_SCHEMA = (
    "qualibug.behavior-ir-semantic-frame-projection.v1"
)

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


def _default_invariant_builder(contribution: dict[str, Any]) -> dict[str, Any]:
    """Standalone deterministic temporal invariant builder."""
    expression = _dict(contribution.get("expression"))
    operation_refs = sorted(
        {_text(ref) for ref in _list(contribution.get("operation_refs")) if _text(ref)}
    )
    identity = {
        "operation_refs": operation_refs,
        "operator": _text(expression.get("operator")),
        "anchor": _text(expression.get("anchor")),
        "duration": _text(expression.get("duration")),
        "window_ms": expression.get("window_ms"),
    }
    node_id = "csf_inv:" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "id": node_id,
        "description": _text(contribution.get("description")),
        "expression": dict(expression),
        "operation_refs": operation_refs,
        "frame_family_evidence": dict(
            _dict(contribution.get("frame_family_evidence"))
        ),
        "source_refs": [dict(row) for row in _list(contribution.get("source_refs"))],
        "confidence": 0.8,
        "derivation": "schema-derived",
        "status": "accepted",
    }


def _constraint_source_refs(
    frame: dict[str, Any],
    constraint: dict[str, Any],
    *,
    constraint_index: int,
) -> list[dict[str, Any]]:
    """Project exact constraint evidence without inventing source coordinates."""
    frame_id = _text(frame.get("frame_id"))
    refs: list[dict[str, Any]] = []
    for evidence in _list(constraint.get("evidence")):
        if not isinstance(evidence, dict):
            continue
        refs.append(
            {
                "kind": "chinese_semantic_time_constraint",
                "frame_id": frame_id,
                "constraint_index": constraint_index,
                "quote": _text(constraint.get("raw")),
                **{
                    key: evidence[key]
                    for key in (
                        "source_id",
                        "locator",
                        "document_block_id",
                        "block_type",
                        "origin",
                    )
                    if evidence.get(key) not in (None, "")
                },
            }
        )
    if not refs:
        source_span = _dict(frame.get("source_span"))
        refs.append(
            {
                "kind": "chinese_semantic_time_constraint",
                "frame_id": frame_id,
                "constraint_index": constraint_index,
                "quote": _text(constraint.get("raw")),
                **{
                    key: source_span[key]
                    for key in ("source_id", "locator", "document_block_id")
                    if source_span.get(key) not in (None, "")
                },
                **(
                    {"origin": _text(constraint.get("origin"))}
                    if _text(constraint.get("origin"))
                    else {}
                ),
            }
        )
    return refs


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


def _temporal_constraint_identity(value: dict[str, Any]) -> tuple[Any, ...] | None:
    """Return the exact typed identity shared by frame and process evidence."""
    row = _dict(value)
    window_ms = row.get("window_ms")
    raw = _text(row.get("raw"))
    anchor = _text(row.get("anchor"))
    duration = _text(row.get("duration"))
    if (
        row.get("source_backed") is not True
        or isinstance(window_ms, bool)
        or not isinstance(window_ms, (int, float))
        or int(window_ms) != window_ms
        or int(window_ms) <= 0
        or not raw
        or not anchor
        or not duration
    ):
        return None
    return raw, anchor, duration, int(window_ms)


def _source_process_wait_binding(
    constraint: dict[str, Any],
    *,
    operation_ref: str,
    process_graphs: Iterable[dict[str, Any]],
    ref_resolver: Callable[[str, str], bool] | None,
) -> tuple[dict[str, Any], str]:
    """Bind one temporal clause to one exact observer-backed process wait.

    Raw-language similarity is deliberately forbidden.  A candidate must
    carry the same typed time-window identity, name the constrained operation
    as its target node, and expose a source-declared observer, predicate and
    bounded polling policy.  Zero or multiple candidates remain visible.
    """
    identity = _temporal_constraint_identity(constraint)
    if identity is None:
        return {}, "TEMPORAL_PROCESS_WAIT_UNRESOLVED"
    complete: list[dict[str, Any]] = []
    incomplete_match = False
    for graph_value in process_graphs:
        graph = _dict(graph_value)
        if _text(graph.get("status")) != "COMPILED":
            continue
        graph_ref = _text(
            graph.get("execution_graph_id") or graph.get("process_id")
        )
        if not graph_ref or not (
            _list(graph.get("source_refs"))
            or _list(graph.get("evidence"))
        ):
            continue
        nodes = {
            _text(row.get("node_id")): row
            for row in _list(graph.get("nodes"))
            if isinstance(row, dict) and _text(row.get("node_id"))
        }
        for wait_value in _list(graph.get("wait_contracts")):
            wait = _dict(wait_value)
            if (
                _text(wait.get("wait_kind")) != "TIMED_WAIT"
                or _text(wait.get("status")) != "BOUND"
                or wait.get("source_backed") is not True
            ):
                continue
            if not any(
                _temporal_constraint_identity(_dict(window)) == identity
                for window in _list(wait.get("time_window_constraints"))
                if isinstance(window, dict)
            ):
                continue
            source_node_id = _text(wait.get("source_node_id"))
            target_node_id = _text(wait.get("target_node_id"))
            source_node = _dict(nodes.get(source_node_id))
            target_node = _dict(nodes.get(target_node_id))
            if _text(target_node.get("operation_ref")) != operation_ref:
                continue
            anchor_operation_ref = _text(source_node.get("operation_ref"))
            observer_operation_ref = _text(
                wait.get("observer_operation_ref")
                or wait.get("read_operation_ref")
            )
            predicate = _dict(
                wait.get("predicate") or wait.get("terminal_predicate")
            )
            async_policy = _dict(
                wait.get("async_policy") or wait.get("poll_policy")
            )
            if not (
                anchor_operation_ref
                and source_node_id != target_node_id
                and anchor_operation_ref != operation_ref
                and observer_operation_ref
                and predicate
                and async_policy.get("enabled") is True
                and async_policy.get("expected_max_delay_ms") == identity[3]
                and _ref_resolves(
                    anchor_operation_ref, "operation", ref_resolver
                )
                and _ref_resolves(
                    observer_operation_ref, "operation", ref_resolver
                )
                and _text(wait.get("wait_id") or wait.get("contract_id"))
            ):
                incomplete_match = True
                continue
            complete.append(
                {
                    "anchor_operation_ref": anchor_operation_ref,
                    "completion_operation_ref": operation_ref,
                    "completion_observer": observer_operation_ref,
                    "process_graph_ref": graph_ref,
                    "wait_contract_ref": _text(
                        wait.get("wait_id") or wait.get("contract_id")
                    ),
                    "anchor_grounding_status": "BOUND",
                    "completion_grounding_status": "BOUND",
                }
            )
    unique = {
        _canonical_json(row): row for row in complete
    }
    if len(unique) == 1:
        return next(iter(unique.values())), ""
    if len(unique) > 1:
        return {}, "TEMPORAL_PROCESS_WAIT_AMBIGUOUS"
    if incomplete_match:
        return {}, "TEMPORAL_COMPLETION_OBSERVER_UNRESOLVED"
    return {}, "TEMPORAL_PROCESS_WAIT_UNRESOLVED"


def project_semantic_frames_to_behavior_ir(
    frames: Iterable[dict[str, Any]],
    *,
    ref_resolver: Callable[[str, str], bool] | None = None,
    process_graphs: Iterable[dict[str, Any]] = (),
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

    source_process_graphs = [
        row for row in process_graphs if isinstance(row, dict)
    ]

    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("chinese_semantic_frame_projection_frame_not_object")
        errors = validate_semantic_frame(frame)
        if errors:
            raise ValueError(
                "chinese_semantic_frame_invalid:" + ",".join(sorted(errors))
            )
        frames_considered += 1
        frame_skip_start = len(skips)
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
        # Permission-row scope alignment (P0-D): legacy permission/ownership
        # relations carry the permission row's scope in their node id, so the
        # frame channel emits the same scope to dedup against them instead of
        # creating parallel duplicates with identical endpoints.
        relation_scope = _text(
            _dict(frame.get("technical_grounding")).get("permission_scope")
        )
        actor_ref = _first_grounded(actor, "grounded_actor_refs")
        entity_ref = _first_grounded(obj, "grounded_entity_refs")
        grounded_operation_refs = list(
            dict.fromkeys(
                _text(ref)
                for ref in _list(action.get("grounded_operation_refs"))
                if _text(ref)
            )
        )
        operation_ref = grounded_operation_refs[0] if grounded_operation_refs else ""

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
                        "scope": relation_scope,
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
                elif len(grounded_operation_refs) > 1:
                    _count_reason("MULTIPLE_OPERATION_CANDIDATES")
                    skips.append(
                        {
                            "frame_id": frame_id,
                            "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
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
                            "scope": relation_scope,
                            "frame_id": frame_id,
                            "source_refs": source_refs,
                        }
                    )
                    emitted = True

        # ── temporal invariants: explicit fixed-duration window + one exact
        # grounded operation. Calendar-sensitive units require source-declared
        # timezone/calendar semantics and therefore remain a visible skip.
        for constraint_index, constraint_value in enumerate(
            _list(frame.get("time_constraints"))
        ):
            if not isinstance(constraint_value, dict):
                continue
            constraint = dict(constraint_value)
            if constraint.get("source_backed") is not True:
                _count_reason("GROUNDING_EVIDENCE_INSUFFICIENT")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "constraint_index": constraint_index,
                        "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
                    }
                )
                continue
            if len(grounded_operation_refs) > 1:
                _count_reason("MULTIPLE_OPERATION_CANDIDATES")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "constraint_index": constraint_index,
                        "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
                    }
                )
                continue
            if not operation_ref:
                _count_reason("ACTION_CONCEPT_UNRESOLVED")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "constraint_index": constraint_index,
                        "reason_code": "ACTION_CONCEPT_UNRESOLVED",
                    }
                )
                continue
            if not _ref_resolves(operation_ref, "operation", ref_resolver):
                _count_reason("GROUNDING_EVIDENCE_INSUFFICIENT")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "constraint_index": constraint_index,
                        "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
                    }
                )
                continue
            window_ms = constraint.get("window_ms")
            if (
                isinstance(window_ms, bool)
                or not isinstance(window_ms, (int, float))
                or window_ms <= 0
                or int(window_ms) != window_ms
            ):
                reason_code = _text(
                    constraint.get("window_resolution_reason")
                    or "TEMPORAL_WINDOW_UNCOMPILED"
                )
                _count_reason(reason_code)
                skips.append(
                    {
                        "frame_id": frame_id,
                        "constraint_index": constraint_index,
                        "reason_code": reason_code,
                    }
                )
                continue
            relation = _text(constraint.get("relation")).upper()
            if relation != "WITHIN":
                _count_reason("TEMPORAL_RELATION_UNSUPPORTED")
                skips.append(
                    {
                        "frame_id": frame_id,
                        "constraint_index": constraint_index,
                        "reason_code": "TEMPORAL_RELATION_UNSUPPORTED",
                    }
                )
                continue
            expression = {
                "kind": "temporal",
                "operator": "within",
                "window_ms": int(window_ms),
                "anchor": _text(constraint.get("anchor")),
                "duration": _text(constraint.get("duration")),
                "raw": _text(constraint.get("raw")),
                # The constrained frame action is grounded, but the source
                # anchor (e.g. "提交后") is still language-level evidence. A
                # protocol must not reinterpret this as eventual consistency
                # until the anchor operation and completion observation bind.
                "temporal_semantics": "action_deadline",
                "anchor_grounding_status": "UNRESOLVED",
            }
            if source_process_graphs:
                binding, binding_reason = _source_process_wait_binding(
                    constraint,
                    operation_ref=operation_ref,
                    process_graphs=source_process_graphs,
                    ref_resolver=ref_resolver,
                )
                if binding:
                    expression.update(binding)
                else:
                    _count_reason(binding_reason)
                    skips.append(
                        {
                            "frame_id": frame_id,
                            "constraint_index": constraint_index,
                            "reason_code": binding_reason,
                        }
                    )
            contributions.append(
                {
                    "contribution_kind": "INVARIANT",
                    "description": _text(constraint.get("raw")),
                    "expression": expression,
                    "operation_refs": [operation_ref],
                    "frame_family_evidence": {
                        "grounded": True,
                        "frame_type": "TIME_WINDOW_CONSTRAINT",
                        "parent_frame_type": frame_type,
                        "frame_id": frame_id,
                    },
                    "frame_id": frame_id,
                    "source_refs": [
                        *source_refs,
                        *_constraint_source_refs(
                            frame,
                            constraint,
                            constraint_index=constraint_index,
                        ),
                    ],
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

        if emitted:
            frames_with_contributions += 1
        elif len(skips) == frame_skip_start:
            # Ungrounded frames are the P0-A norm; record the skip, never a guess.
            _count_reason("TECHNICAL_GROUNDING_PENDING")
            skips.append(
                {
                    "frame_id": frame_id,
                    "reason_code": "TECHNICAL_GROUNDING_PENDING",
                }
            )

    receipt = build_receipt(
        receipt_kind="BEHAVIOR_IR_PROJECTION",
        status="PARTIAL" if skips else "PASS",
        reason_codes=sorted(reason_counts),
        payload={
            "frames_considered": frames_considered,
            "frames_with_contributions": frames_with_contributions,
            "contribution_count": len(contributions),
            "relation_contribution_count": sum(
                row.get("contribution_kind") == "RELATION" for row in contributions
            ),
            "invariant_contribution_count": sum(
                row.get("contribution_kind") == "INVARIANT" for row in contributions
            ),
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
    invariant_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ref_resolver: Callable[[str, str], bool] | None = None,
    process_graphs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Merge frame contributions into a Behavior IR model (dedup by node id).

    Existing relation ids are never overwritten; only genuinely new grounded
    relations are added. Returns the projection receipt and stores it on the
    model as ``semantic_frame_projection_receipt``.
    """
    result = project_semantic_frames_to_behavior_ir(
        frames,
        ref_resolver=ref_resolver,
        process_graphs=process_graphs,
    )
    relations = [row for row in _list(model.get("relations")) if isinstance(row, dict)]
    invariants = [row for row in _list(model.get("invariants")) if isinstance(row, dict)]
    relation_ids = {_text(row.get("id")) for row in relations if _text(row.get("id"))}
    invariant_by_id = {
        _text(row.get("id")): row for row in invariants if _text(row.get("id"))
    }
    relation_node_builder = relation_builder or _default_relation_builder
    invariant_node_builder = invariant_builder or _default_invariant_builder
    added = 0
    merged = 0
    relation_added = 0
    invariant_added = 0
    for contribution in result["contributions"]:
        contribution_kind = _text(contribution.get("contribution_kind"))
        if contribution_kind == "RELATION":
            node = relation_node_builder(contribution)
            node_id = _text(node.get("id"))
            if not node_id or node_id in relation_ids:
                merged += 1
                continue
            relations.append(node)
            relation_ids.add(node_id)
            relation_added += 1
            added += 1
            continue
        if contribution_kind != "INVARIANT":
            raise ValueError(
                f"chinese_semantic_frame_contribution_kind_invalid:{contribution_kind}"
            )
        node = invariant_node_builder(contribution)
        node_id = _text(node.get("id"))
        if not node_id:
            merged += 1
            continue
        existing = invariant_by_id.get(node_id)
        if existing is not None:
            existing_refs = {
                _canonical_json(ref)
                for ref in _list(existing.get("source_refs"))
                if isinstance(ref, dict)
            }
            for source_ref in _list(node.get("source_refs")):
                if not isinstance(source_ref, dict):
                    continue
                identity = _canonical_json(source_ref)
                if identity not in existing_refs:
                    existing.setdefault("source_refs", []).append(dict(source_ref))
                    existing_refs.add(identity)
            merged += 1
            continue
        invariants.append(node)
        invariant_by_id[node_id] = node
        invariant_added += 1
        added += 1
    model["relations"] = relations
    model["invariants"] = invariants
    receipt = dict(result["receipt"])
    receipt["payload"] = dict(receipt["payload"])
    receipt["payload"]["added_count"] = added
    receipt["payload"]["deduped_count"] = merged
    receipt["payload"]["relation_added_count"] = relation_added
    receipt["payload"]["invariant_added_count"] = invariant_added
    model["semantic_frame_projection_receipt"] = receipt
    return receipt
