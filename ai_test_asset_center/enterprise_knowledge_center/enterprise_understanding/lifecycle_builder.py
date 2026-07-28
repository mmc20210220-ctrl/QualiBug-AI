"""Evidence-backed lifecycle construction for enterprise business objects."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any, Iterable

from .schema import (
    LIFECYCLE_SCHEMA,
    as_dict,
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    new_unknown,
    source_evidence,
    stable_id,
    text,
    unique_text,
)

_TRANSITION_TEXT_RE = re.compile(
    r"^\s*(?P<from>[^>→\-]{1,48}?)\s*(?:->|→|=>|至|到)\s*(?P<to>[^>→]{1,48}?)\s*$"
)


def _accepted_fact(fact: dict[str, Any]) -> bool:
    return text(fact.get("status")) == "ACCEPTED" and text(fact.get("kind")) == "STATE_TRANSITION"


def _fact_objects(fact: dict[str, Any], known_objects: set[str]) -> list[str]:
    subject = as_dict(fact.get("subject"))
    object_part = as_dict(fact.get("object"))
    explicit = unique_text(
        [
            *as_list(subject.get("entity_refs")),
            *as_list(object_part.get("entity_refs")),
        ]
    )
    statement = text(fact.get("raw_statement"))
    mentions = [name for name in known_objects if name and name in statement]
    return sorted(set([*explicit, *mentions]), key=lambda name: (statement.find(name), -len(name), name))


def _resolve_machine_object(row: dict[str, Any], known_objects: set[str]) -> str:
    candidates = unique_text(
        [
            row.get("object"),
            row.get("entity"),
            row.get("subject"),
            row.get("business_object"),
            row.get("name"),
        ]
    )
    exact = [candidate for candidate in candidates if candidate in known_objects]
    if len(exact) == 1:
        return exact[0]
    joined = " ".join(candidates)
    mentions = [name for name in known_objects if name and name in joined]
    return mentions[0] if len(mentions) == 1 else ""


def _transition_from_existing(
    transition: Any,
    *,
    object_ref: str,
    source_id: str,
    asset_ref: str,
    index: int,
) -> dict[str, Any] | None:
    if isinstance(transition, str):
        match = _TRANSITION_TEXT_RE.match(transition)
        if not match:
            return None
        from_state = text(match.group("from"))
        to_state = text(match.group("to"))
        event = ""
        operation_ref = ""
        forbidden = False
        raw = transition
    elif isinstance(transition, dict):
        from_state = text(transition.get("from_state") or transition.get("from") or transition.get("source"))
        to_state = text(transition.get("to_state") or transition.get("to") or transition.get("target"))
        event = text(transition.get("event") or transition.get("trigger"))
        operation_ref = text(transition.get("operation") or transition.get("action"))
        forbidden = bool(
            transition.get("forbidden")
            or text(transition.get("kind")).upper() == "FORBIDDEN"
            or text(transition.get("status")).lower() in {"forbidden", "disallowed", "invalid"}
            or transition.get("allowed") is False
        )
        raw = text(transition.get("raw") or transition.get("statement"))
    else:
        return None
    if not from_state and not to_state:
        return None
    evidence = [
        source_evidence(
            source_id=source_id,
            asset_ref=f"{asset_ref}:transition:{index}",
            quote=raw,
            derivation="existing_source_backed_state_machine",
        )
    ]
    return {
        "transition_id": stable_id("lifecycle_transition", object_ref, from_state, event, operation_ref, to_state, forbidden, asset_ref, index),
        "object_ref": object_ref,
        "from_state": from_state,
        "event": event,
        "operation_ref": operation_ref,
        "to_state": to_state,
        "conditions": [],
        "exceptions": [],
        "transition_kind": "FORBIDDEN" if forbidden else "ALLOWED",
        "fact_refs": [],
        "evidence": dedupe_evidence(evidence),
        "completeness": "COMPLETE" if from_state and to_state else "INCOMPLETE",
    }


def _weak_components(states: set[str], transitions: list[dict[str, Any]]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for transition in transitions:
        if text(transition.get("transition_kind")) == "FORBIDDEN":
            continue
        source = text(transition.get("from_state"))
        target = text(transition.get("to_state"))
        if source and target:
            graph[source].add(target)
            graph[target].add(source)
    remaining = set(states)
    components: list[list[str]] = []
    while remaining:
        start = min(remaining)
        queue: deque[str] = deque([start])
        component: set[str] = set()
        while queue:
            state = queue.popleft()
            if state in component:
                continue
            component.add(state)
            for neighbor in graph.get(state, set()):
                if neighbor not in component:
                    queue.append(neighbor)
        remaining -= component
        components.append(sorted(component))
    return components


def build_lifecycles(
    asset: dict[str, Any],
    facts: Iterable[dict[str, Any]],
    object_names: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build one lifecycle per explicit object and expose incomplete semantics."""
    known = {text(name) for name in object_names if text(name)}
    transitions_by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    states_by_object: dict[str, set[str]] = defaultdict(set)
    evidence_by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unknowns: list[dict[str, Any]] = []

    for fact in facts:
        if not isinstance(fact, dict) or not _accepted_fact(fact):
            continue
        objects = _fact_objects(fact, known)
        evidence = evidence_from_fact(fact)
        if len(objects) != 1:
            unknowns.append(
                new_unknown(
                    "LIFECYCLE_OBJECT_UNRESOLVED",
                    f"状态语句“{text(fact.get('raw_statement'))}”无法唯一绑定到一个业务对象。",
                    related_objects=objects,
                    evidence=evidence,
                    severity="P0" if fact.get("critical") else "P1",
                    blocks_formal_understanding=True,
                    reason_code="LIFECYCLE_OBJECT_UNRESOLVED",
                    details={"fact_id": fact.get("fact_id")},
                )
            )
            continue
        object_ref = objects[0]
        action = as_dict(fact.get("action"))
        operation_ref = text(action.get("canonical") or action.get("raw"))
        modality = text(fact.get("modality"))
        for effect_index, effect_raw in enumerate(as_list(fact.get("state_effects"))):
            effect = as_dict(effect_raw)
            from_state = text(effect.get("from_state"))
            to_state = text(effect.get("to_state"))
            transition_kind = "FORBIDDEN" if modality == "MUST_NOT" else "ALLOWED"
            transition = {
                "transition_id": stable_id("lifecycle_transition", object_ref, from_state, operation_ref, to_state, transition_kind, fact.get("fact_id"), effect_index),
                "object_ref": object_ref,
                "from_state": from_state,
                "event": text(as_dict(fact.get("trigger")).get("raw")),
                "operation_ref": operation_ref,
                "to_state": to_state,
                "conditions": unique_text(as_list(fact.get("conditions"))),
                "exceptions": unique_text(as_list(fact.get("exceptions"))),
                "transition_kind": transition_kind,
                "fact_refs": unique_text([fact.get("fact_id")]),
                "evidence": evidence,
                "completeness": "COMPLETE" if from_state and to_state else "INCOMPLETE",
            }
            transitions_by_object[object_ref].append(transition)
            evidence_by_object[object_ref].extend(evidence)
            if from_state:
                states_by_object[object_ref].add(from_state)
            if to_state:
                states_by_object[object_ref].add(to_state)
            if not from_state:
                unknowns.append(
                    new_unknown(
                        "LIFECYCLE_FROM_STATE_UNKNOWN",
                        f"{object_ref}执行“{operation_ref or '状态变化'}”进入“{to_state}”前的起始状态未定义。",
                        related_objects=[object_ref],
                        related_operations=[operation_ref],
                        evidence=evidence,
                        severity="P1",
                        blocks_formal_understanding=False,
                        reason_code="LIFECYCLE_FROM_STATE_UNKNOWN",
                        details={"fact_id": fact.get("fact_id"), "to_state": to_state},
                    )
                )
            if not to_state:
                unknowns.append(
                    new_unknown(
                        "LIFECYCLE_TO_STATE_UNKNOWN",
                        f"{object_ref}执行“{operation_ref or '状态变化'}”后的目标状态未定义。",
                        related_objects=[object_ref],
                        related_operations=[operation_ref],
                        evidence=evidence,
                        severity="P0",
                        blocks_formal_understanding=True,
                        reason_code="LIFECYCLE_TO_STATE_UNKNOWN",
                        details={"fact_id": fact.get("fact_id"), "from_state": from_state},
                    )
                )

    for machine_index, machine in enumerate(as_list(asset.get("state_machines"))):
        if not isinstance(machine, dict):
            continue
        object_ref = _resolve_machine_object(machine, known)
        source_id = text(machine.get("source_id"))
        asset_ref = text(machine.get("state_machine_id")) or f"state_machines[{machine_index}]"
        machine_evidence = [
            source_evidence(
                source_id=source_id,
                asset_ref=asset_ref,
                quote=machine.get("statement") or machine.get("source_excerpt"),
                derivation="existing_source_backed_state_machine",
            )
        ]
        if not object_ref:
            unknowns.append(
                new_unknown(
                    "STATE_MACHINE_OBJECT_UNRESOLVED",
                    f"状态机“{text(machine.get('name') or asset_ref)}”无法唯一绑定到业务对象。",
                    evidence=machine_evidence,
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="STATE_MACHINE_OBJECT_UNRESOLVED",
                    details={"state_machine_id": asset_ref},
                )
            )
            continue
        evidence_by_object[object_ref].extend(machine_evidence)
        states_by_object[object_ref].update(unique_text(as_list(machine.get("states"))))
        raw_transitions = [
            *as_list(machine.get("transitions")),
            *as_list(machine.get("allowed_transitions")),
            *[
                {**as_dict(row), "forbidden": True}
                for row in as_list(machine.get("forbidden_transitions"))
                if isinstance(row, dict)
            ],
        ]
        for transition_index, raw in enumerate(raw_transitions):
            transition = _transition_from_existing(
                raw,
                object_ref=object_ref,
                source_id=source_id,
                asset_ref=asset_ref,
                index=transition_index,
            )
            if not transition:
                continue
            transitions_by_object[object_ref].append(transition)
            if transition.get("from_state"):
                states_by_object[object_ref].add(text(transition.get("from_state")))
            if transition.get("to_state"):
                states_by_object[object_ref].add(text(transition.get("to_state")))

    lifecycles: list[dict[str, Any]] = []
    for object_ref in sorted(set([*transitions_by_object, *states_by_object])):
        transitions = transitions_by_object.get(object_ref, [])
        deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for transition in transitions:
            key = (
                text(transition.get("from_state")),
                text(transition.get("operation_ref") or transition.get("event")),
                text(transition.get("to_state")),
                text(transition.get("transition_kind")),
            )
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = transition
            else:
                existing["conditions"] = unique_text([*as_list(existing.get("conditions")), *as_list(transition.get("conditions"))])
                existing["exceptions"] = unique_text([*as_list(existing.get("exceptions")), *as_list(transition.get("exceptions"))])
                existing["fact_refs"] = unique_text([*as_list(existing.get("fact_refs")), *as_list(transition.get("fact_refs"))])
                existing["evidence"] = dedupe_evidence([*as_list(existing.get("evidence")), *as_list(transition.get("evidence"))])
        transition_rows = sorted(deduped.values(), key=lambda row: (text(row.get("from_state")), text(row.get("to_state")), text(row.get("operation_ref"))))
        states = set(states_by_object.get(object_ref, set()))
        target_map: dict[tuple[str, str], set[str]] = defaultdict(set)
        for transition in transition_rows:
            if text(transition.get("transition_kind")) == "FORBIDDEN":
                continue
            key = (text(transition.get("from_state")), text(transition.get("operation_ref") or transition.get("event")))
            target = text(transition.get("to_state"))
            if key[0] and key[1] and target:
                target_map[key].add(target)
        for (from_state, operation_ref), targets in target_map.items():
            if len(targets) > 1:
                related_evidence = [row for transition in transition_rows if text(transition.get("from_state")) == from_state and text(transition.get("operation_ref") or transition.get("event")) == operation_ref for row in as_list(transition.get("evidence"))]
                unknowns.append(
                    new_unknown(
                        "LIFECYCLE_TARGET_CONTRADICTION",
                        f"{object_ref}在状态“{from_state}”执行“{operation_ref}”被资料声明为多个目标状态：{'、'.join(sorted(targets))}。",
                        related_objects=[object_ref],
                        related_operations=[operation_ref],
                        evidence=related_evidence,
                        severity="P0",
                        blocks_formal_understanding=True,
                        reason_code="LIFECYCLE_TARGET_CONTRADICTION",
                        details={"from_state": from_state, "operation_ref": operation_ref, "target_states": sorted(targets)},
                    )
                )
        components = _weak_components(states, transition_rows)
        nontrivial_components = [component for component in components if component]
        if len(nontrivial_components) > 1 and len(transition_rows) > 1:
            unknowns.append(
                new_unknown(
                    "LIFECYCLE_DISCONNECTED",
                    f"{object_ref}生命周期包含彼此未连接的状态片段，资料尚未说明它们如何衔接。",
                    related_objects=[object_ref],
                    evidence=evidence_by_object.get(object_ref, []),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code="LIFECYCLE_DISCONNECTED",
                    details={"components": nontrivial_components},
                )
            )
        evidence = dedupe_evidence(
            [
                *evidence_by_object.get(object_ref, []),
                *[row for transition in transition_rows for row in as_list(transition.get("evidence"))],
            ]
        )
        complete_count = sum(1 for row in transition_rows if text(row.get("completeness")) == "COMPLETE")
        lifecycles.append(
            {
                "schema": LIFECYCLE_SCHEMA,
                "lifecycle_id": stable_id("business_lifecycle", object_ref),
                "object_ref": object_ref,
                "states": sorted(states),
                "transitions": transition_rows,
                "allowed_transition_count": sum(1 for row in transition_rows if text(row.get("transition_kind")) == "ALLOWED"),
                "forbidden_transition_count": sum(1 for row in transition_rows if text(row.get("transition_kind")) == "FORBIDDEN"),
                "complete_transition_count": complete_count,
                "incomplete_transition_count": len(transition_rows) - complete_count,
                "graph_components": nontrivial_components,
                "evidence": evidence,
                "status": "PARTIAL" if len(transition_rows) != complete_count or len(nontrivial_components) > 1 else "UNDERSTOOD",
            }
        )

    return lifecycles, unknowns


__all__ = ["build_lifecycles"]
