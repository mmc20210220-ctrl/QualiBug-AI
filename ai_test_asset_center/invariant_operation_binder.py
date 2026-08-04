from __future__ import annotations

"""Bind source-extracted invariants to the operations they constrain.

Business rules arrive as prose ("已取消订单不能支付、发货、确认收货"); operations arrive
from an API specification. Nothing joined them, so ``invariant.operation_refs`` was
populated only when the *rule* already carried an operation reference -- which a
requirements document never does. Measured on a live 11-service target: 107 of 111
invariants had ``operation_refs == []``, and 174 of 435 obligations reached
``MISSING_PRIMARY_OPERATION``. The product read the rules and could not reach them.

This module is the missing join. It is vocabulary-driven, not domain-hardcoded: the
noun and verb bridges come from ``policies/semantic_lexicon.json`` and the entity and
state names come from the IR the target's own documents produced, so the same code
works on a system whose nouns this file has never seen.

Binding discipline, because a wrong binding is a false-defect generator:

* Two independent signals are always required. One entity token in common is not a
  binding; neither is one verb.
* Every binding carries the tokens that produced it, so a reader can disagree with
  it. A binding with no stated basis would be a guess wearing a receipt.
* An invariant that cannot be bound stays unbound, with a reason. Guessing to raise
  a coverage number is the failure mode this whole codebase exists to prevent.
* Umbrella sentences ("系统应保证权限隔离、状态一致性、金额准确性") are refused outright.
  They are summaries, not invariants over a single operation, and binding one to
  every write it mentions would attach an unfalsifiable assertion to real traffic.
"""

import json
import re
from pathlib import Path
from typing import Any

BINDER_SCHEMA = "qualibug.invariant-operation-binding.v1"

# An invariant naming this many distinct entities with no action verb is a summary
# sentence rather than a constraint on one operation.
_UMBRELLA_ENTITY_THRESHOLD = 3
# An invariant binding to more operations than this is almost certainly matching on
# something too generic to be a constraint; the overflow is reported, not dropped.
_MAX_BINDINGS_PER_INVARIANT = 8

_PATH_NOISE = frozenset({
    "api", "v1", "v2", "v3", "rest", "svc", "service", "services",
    "internal", "public", "admin", "web", "app",
})
_METHOD_ACTIONS = {
    "GET": "read", "HEAD": "read", "OPTIONS": "read",
    "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete",
}
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ID_SUFFIX = re.compile(r"(?:_?id|_?ref|_?no|_?code)$", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_STATE_LIKE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _load_lexicon() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _lexicon_map(lexicon: dict[str, Any], name: str) -> dict[str, list[str]]:
    """Return ``{as-written token: [canonical tokens]}`` for one lexicon section."""
    raw = lexicon.get(name)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if key.startswith("_") or key == "comment" or not isinstance(value, list):
            continue
        canonical = [_text(item).lower() for item in value if _text(item)]
        if canonical:
            out[_text(key)] = canonical
    return out


def _normalise_entity(token: str) -> str:
    """Singularise and strip id-ish suffixes so ``orderId`` and ``orders`` agree."""
    base = _ID_SUFFIX.sub("", _text(token).lower()).strip("_-")
    if len(base) > 3 and base.endswith("ies"):
        return base[:-3] + "y"
    if len(base) > 3 and base.endswith("ses"):
        return base[:-2]
    if len(base) > 3 and base.endswith("s") and not base.endswith("ss"):
        return base[:-1]
    return base


def _field_entity_tokens(field: str) -> set[str]:
    """Entity nouns implied by a request/response field name.

    ``orderId`` on a payment operation is what links "已取消订单不能支付" to
    ``POST /api/payments/pay``: the rule's entity is the order, the operation's is the
    payment, and only the field declares the relationship between them.
    """
    raw = _text(field)
    if not raw:
        return set()
    parts: set[str] = set()
    for chunk in re.split(r"[.\[\]_\-/]+", raw):
        if not chunk:
            continue
        for piece in _CAMEL_SPLIT.split(chunk):
            token = _normalise_entity(piece)
            if len(token) >= 3:
                parts.add(token)
    return parts


def operation_tokens(operation: dict[str, Any]) -> dict[str, set[str]]:
    """Entity / action / field-entity tokens an operation exposes.

    Path-derived entities are the operation's own subject; field-derived entities are
    the entities it *references*. They are kept apart because a match on the subject
    is stronger evidence than a match on a referenced foreign key.
    """
    op = _dict(operation)
    method = _text(op.get("method")).upper()
    path = _text(op.get("path"))

    entities: set[str] = set()
    tail_tokens: list[str] = []
    for segment in path.split("/"):
        segment = segment.strip()
        if not segment or segment.startswith((":", "{")) or segment.startswith("<"):
            continue
        lowered = segment.lower()
        if lowered in _PATH_NOISE:
            continue
        normalised = _normalise_entity(lowered)
        if normalised:
            entities.add(normalised)
            tail_tokens.append(normalised)

    field_entities: set[str] = set()
    for field in _list(op.get("parameters")) + _list(op.get("field_dictionary")):
        field_entities |= _field_entity_tokens(field)
    field_entities -= entities

    return {
        "entities": entities,
        "field_entities": field_entities,
        "path_tail": {tail_tokens[-1]} if tail_tokens else set(),
        "method": {method} if method else set(),
        "is_write": {"write"} if method in _WRITE_METHODS else set(),
    }


def operation_actions(
    operation: dict[str, Any],
    verb_lexicon: dict[str, list[str]],
) -> set[str]:
    """Canonical actions an operation performs.

    A verb in the final path segment beats the HTTP method: ``POST /orders/:id/cancel``
    is a cancel, not a create, and treating it as a create would bind every
    "must not be cancelled" rule to the wrong endpoint.
    """
    op = _dict(operation)
    method = _text(op.get("method")).upper()
    path = _text(op.get("path"))
    segments = [s for s in path.split("/") if s and not s.startswith((":", "{", "<"))]

    # The canonical action vocabulary, usable here as exact path-segment matches.
    # The lexicon deliberately omits short English keys such as "pay" and "ship"
    # because its other consumers match by substring, where "ship" would hit
    # "relationship". A path segment is compared for equality, so those same short
    # tokens are both safe and necessary -- without them POST /api/payments/pay
    # resolves to "create" and every payment rule binds to the wrong endpoint.
    canonical_actions = {token for values in verb_lexicon.values() for token in values}

    actions: set[str] = set()
    for segment in reversed(segments):
        lowered = segment.lower()
        if lowered in _PATH_NOISE:
            continue
        # Step 1: try the segment exactly as it appears in the path. A
        # tail like ``cancel`` or ``ship`` matches an action here.
        if lowered in canonical_actions:
            actions.add(lowered)
        for written, canonical in verb_lexicon.items():
            if written.lower() == lowered:
                actions.update(canonical)
        if actions:
            break
        # Step 2: try the singularised form. ``/api/orders`` and
        # ``/api/refunds`` only carry their action after singularising;
        # without this fallback they fall through to the method default
        # ``create`` and bind the wrong obligation (every payment rule
        # to a refund endpoint, every cancellation rule to an order
        # create, etc.). The singulariser is the same one used for
        # entity tokens, so the contract stays single-sourced -- the
        # risk of over-binding is bounded by canonical_actions and the
        # verb lexicon, both already required for a binding.
        singular = _normalise_entity(lowered)
        if singular and singular != lowered:
            if singular in canonical_actions:
                actions.add(singular)
            for written, canonical in verb_lexicon.items():
                if written.lower() == singular:
                    actions.update(canonical)
        if actions:
            break

    if actions:
        return actions
    default = _METHOD_ACTIONS.get(method)
    return {default} if default else set()


def operation_action_profile(
    operation: dict[str, Any],
    verb_lexicon: dict[str, list[str]],
) -> dict[str, set[str]]:
    """Split an operation's actions into path-declared and method-defaulted.

    The distinction is load-bearing. "create" derived from POST is not evidence of
    what an operation does -- every POST is a create. Letting it satisfy the weaker
    binding rule made "并发下单不得超卖" bind to POST /api/refunds, because the rule
    says create, the method says create, and refunds happen to carry an orderId. That
    is a false defect waiting to be reported against the wrong endpoint.
    """
    op = _dict(operation)
    method = _text(op.get("method")).upper()
    path_actions = operation_actions(op, verb_lexicon)
    method_default = _METHOD_ACTIONS.get(method)
    if method_default and path_actions == {method_default}:
        return {"specific": set(), "all": path_actions}
    return {"specific": path_actions, "all": path_actions}


def invariant_tokens(
    invariant: dict[str, Any],
    *,
    entity_lexicon: dict[str, list[str]],
    verb_lexicon: dict[str, list[str]],
    known_entities: set[str],
    known_states: set[str],
) -> dict[str, set[str]]:
    """Entity / action / state tokens a prose invariant refers to.

    Matching is substring-based because the source language may not delimit words,
    which is also why the caller must supply real vocabulary: a two-character token
    would match almost anything, so only lexicon-declared and IR-declared names are
    ever looked for.
    """
    inv = _dict(invariant)
    statement = " ".join([
        _text(inv.get("description")),
        _text(_dict(inv.get("expression")).get("raw")),
    ]).strip()
    lowered = statement.lower()

    entities: set[str] = set()
    for written, canonical in entity_lexicon.items():
        if written and written.lower() in lowered:
            entities.update(_normalise_entity(token) for token in canonical)
    for name in known_entities:
        if len(name) >= 3 and name in lowered:
            entities.add(_normalise_entity(name))
    entities.discard("")

    actions: set[str] = set()
    for written, canonical in verb_lexicon.items():
        if written and written.lower() in lowered:
            actions.update(canonical)

    states: set[str] = set(_STATE_LIKE.findall(statement))
    for name in known_states:
        if name and name.lower() in lowered:
            states.add(name.upper())

    return {
        "entities": entities,
        "actions": actions,
        "states": states,
        "statement": statement,
    }


def _known_vocabulary(behavior_ir: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Entity and state names the target's own documents produced."""
    entities: set[str] = set()
    for node in _list(behavior_ir.get("entities")):
        for key in ("name", "entity", "id"):
            value = _normalise_entity(_text(_dict(node).get(key)))
            if value and not value.startswith("bir_"):
                entities.add(value)
    states: set[str] = set()
    for node in _list(behavior_ir.get("states")):
        for key in ("name", "state", "value"):
            value = _text(_dict(node).get(key))
            if value and not value.startswith("bir_"):
                states.add(value.upper())
    return entities, states


def bind_invariants_to_operations(
    behavior_ir: dict[str, Any],
    *,
    lexicon: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Join prose invariants to the operations they constrain.

    Returns ``{schema_version, bindings, unbound, counts}``. ``bindings`` entries carry
    ``basis`` and ``matched_tokens`` so every join is auditable; ``unbound`` entries
    carry a ``reason_code`` so an invariant the binder declined is visible rather than
    absent. Nothing here mutates the IR -- ``apply_invariant_operation_bindings``
    does that, so a caller can inspect the join before accepting it.
    """
    ir = _dict(behavior_ir)
    lex = lexicon if isinstance(lexicon, dict) else _load_lexicon()
    entity_lexicon = _lexicon_map(lex, "entity_token_lexicon")
    verb_lexicon = _lexicon_map(lex, "verb_action_lexicon")
    known_entities, known_states = _known_vocabulary(ir)

    operations = [op for op in _list(ir.get("operations")) if isinstance(op, dict)]
    op_profiles: list[dict[str, Any]] = []
    for op in operations:
        tokens = operation_tokens(op)
        op_profiles.append({
            "id": _text(op.get("id")),
            "operation_id": _text(op.get("operation_id")),
            "method": _text(op.get("method")).upper(),
            "path": _text(op.get("path")),
            "entities": tokens["entities"],
            "field_entities": tokens["field_entities"],
            "is_write": bool(tokens["is_write"]),
            "actions": operation_actions(op, verb_lexicon),
            "specific_actions": operation_action_profile(op, verb_lexicon)["specific"],
        })

    bindings: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []

    for invariant in _list(ir.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        invariant_id = _text(invariant.get("id"))
        if not invariant_id:
            continue
        if _list(invariant.get("operation_refs")):
            continue  # already bound at source; never second-guess a declared ref

        tokens = invariant_tokens(
            invariant,
            entity_lexicon=entity_lexicon,
            verb_lexicon=verb_lexicon,
            known_entities=known_entities,
            known_states=known_states,
        )
        statement = tokens["statement"]
        if not statement:
            unbound.append({
                "invariant_ref": invariant_id,
                "reason_code": "INVARIANT_STATEMENT_EMPTY",
            })
            continue

        if len(tokens["entities"]) >= _UMBRELLA_ENTITY_THRESHOLD and not tokens["actions"]:
            unbound.append({
                "invariant_ref": invariant_id,
                "reason_code": "INVARIANT_STATEMENT_NOT_OPERATION_SPECIFIC",
                "detail": f"names {len(tokens['entities'])} entities and no action",
                "statement": statement[:200],
            })
            continue

        matches: list[dict[str, Any]] = []
        for profile in op_profiles:
            entity_hit = tokens["entities"] & profile["entities"]
            field_hit = tokens["entities"] & profile["field_entities"]
            action_hit = tokens["actions"] & profile["actions"]

            if entity_hit and action_hit:
                basis, confidence = "entity_and_action", 0.9
            elif (tokens["actions"] & profile["specific_actions"]) and field_hit:
                # The rule's entity reaches this operation only through a declared
                # field -- the orderId on a payment. Weaker than a subject match,
                # strong enough to be a real join, but ONLY when the action is a verb
                # the path actually declares. A method-defaulted "create" would match
                # every POST that references the entity.
                action_hit = tokens["actions"] & profile["specific_actions"]
                basis, confidence = "action_and_referenced_entity", 0.75
            elif entity_hit and tokens["states"] and profile["is_write"]:
                basis, confidence = "entity_and_state_write", 0.6
            else:
                continue

            matches.append({
                "operation_ref": profile["id"],
                "operation_id": profile["operation_id"],
                "method": profile["method"],
                "path": profile["path"],
                "basis": basis,
                "confidence": confidence,
                "matched_tokens": {
                    "entities": sorted(entity_hit),
                    "referenced_entities": sorted(field_hit),
                    "actions": sorted(action_hit),
                    "states": sorted(tokens["states"])[:6],
                },
            })

        if not matches:
            unbound.append({
                "invariant_ref": invariant_id,
                "reason_code": "INVARIANT_NO_MATCHING_OPERATION",
                "detail": (
                    f"entities={sorted(tokens['entities'])[:6]} "
                    f"actions={sorted(tokens['actions'])[:6]}"
                ),
                "statement": statement[:200],
            })
            continue

        matches.sort(key=lambda m: (-m["confidence"], m["path"]))
        overflow = max(0, len(matches) - _MAX_BINDINGS_PER_INVARIANT)
        kept = matches[:_MAX_BINDINGS_PER_INVARIANT]
        entry = {
            "invariant_ref": invariant_id,
            "statement": statement[:200],
            "operations": kept,
            "operation_refs": [m["operation_ref"] for m in kept if m["operation_ref"]],
        }
        if overflow:
            # Never a silent cap: a truncated binding set reads as "this is all of
            # them" and would hide that the match was too generic to trust.
            entry["truncated_match_count"] = overflow
            entry["truncation_note"] = (
                f"{overflow} further operations matched and were dropped at the "
                f"{_MAX_BINDINGS_PER_INVARIANT}-binding cap; a match this broad is "
                "usually a sign the statement is not operation-specific"
            )
        bindings.append(entry)

    return {
        "schema_version": BINDER_SCHEMA,
        "bindings": bindings,
        "unbound": unbound,
        "counts": {
            "invariant_total": len([i for i in _list(ir.get("invariants")) if isinstance(i, dict)]),
            "operation_total": len(op_profiles),
            "bound": len(bindings),
            "unbound": len(unbound),
            "binding_refs": sum(len(b["operation_refs"]) for b in bindings),
            "truncated": sum(1 for b in bindings if b.get("truncated_match_count")),
        },
    }


def apply_invariant_operation_bindings(
    behavior_ir: dict[str, Any],
    binding_result: dict[str, Any] | None = None,
    *,
    min_confidence: float = 0.6,
) -> dict[str, Any]:
    """Write resolved bindings into the IR's invariants and return a receipt.

    Mutates ``behavior_ir`` in place, setting ``operation_refs`` and recording
    ``operation_binding_basis`` beside it so a downstream reader can tell a derived
    binding from one the source declared. Invariants that stay unbound keep their
    existing coverage gap -- the binder narrows the gap set, it does not erase it.
    """
    ir = _dict(behavior_ir)
    result = binding_result if isinstance(binding_result, dict) else bind_invariants_to_operations(ir)
    by_ref = {
        _text(entry.get("invariant_ref")): entry
        for entry in _list(result.get("bindings"))
        if isinstance(entry, dict)
    }

    applied = 0
    refs_written = 0
    for invariant in _list(ir.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        entry = by_ref.get(_text(invariant.get("id")))
        if not entry or _list(invariant.get("operation_refs")):
            continue
        accepted = [
            match for match in _list(entry.get("operations"))
            if isinstance(match, dict) and float(match.get("confidence") or 0) >= min_confidence
        ]
        refs = [_text(m.get("operation_ref")) for m in accepted if _text(m.get("operation_ref"))]
        if not refs:
            continue
        invariant["operation_refs"] = refs
        invariant["operation_binding_basis"] = {
            "schema_version": BINDER_SCHEMA,
            "derivation": "derived_by_invariant_operation_binder",
            "min_confidence": min_confidence,
            "matches": [
                {
                    "operation_ref": _text(m.get("operation_ref")),
                    "path": _text(m.get("path")),
                    "method": _text(m.get("method")),
                    "basis": _text(m.get("basis")),
                    "confidence": float(m.get("confidence") or 0),
                    "matched_tokens": _dict(m.get("matched_tokens")),
                }
                for m in accepted
            ],
        }
        applied += 1
        refs_written += len(refs)

    # The SOURCE_INVARIANT_OPERATION_UNBOUND gaps were written while the IR was being
    # built, before this join ran. Leaving them would have the result claim an
    # invariant is unreachable at the same time as carrying its operation_refs -- a
    # gap list that over-reports is as misleading as one that under-reports, and the
    # release gate counts these.
    bound_ids = {
        _text(invariant.get("id"))
        for invariant in _list(ir.get("invariants"))
        if isinstance(invariant, dict)
        and _dict(invariant.get("operation_binding_basis")).get("derivation")
        == "derived_by_invariant_operation_binder"
    }
    pruned = 0
    if bound_ids and isinstance(ir.get("coverage_gaps"), list):
        remaining = []
        for gap in ir["coverage_gaps"]:
            record = _dict(gap)
            if (
                _text(record.get("reason_code")) == "SOURCE_INVARIANT_OPERATION_UNBOUND"
                and _text(record.get("invariant_ref")) in bound_ids
            ):
                pruned += 1
                continue
            remaining.append(gap)
        ir["coverage_gaps"] = remaining

    return {
        "schema_version": BINDER_SCHEMA,
        "invariants_bound": applied,
        "operation_refs_written": refs_written,
        "min_confidence": min_confidence,
        "still_unbound": len(_list(result.get("unbound"))),
        "stale_unbound_gaps_pruned": pruned,
        "counts": _dict(result.get("counts")),
    }
