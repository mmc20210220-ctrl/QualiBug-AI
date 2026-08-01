"""Source-backed state-guard coordinates for the Chinese comprehension facade.

The historical extractor remains the only source parser. This module closes one
missing grammar after atomic facts exist but before structured compilation and
semantic-signature deduplication. It never creates a fact, changes a fact id, resolves
an object identity, chooses an endpoint, or selects a conflict winner.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

RECEIPT_SCHEMA = "qualibug.chinese-state-guard-coordinate-closure.v1"

_STATE_GUARD_RE = re.compile(
    r"^(?:只有|仅)"
    r"(?P<states>[^，,；;。的]{1,48}?)状态的"
    r"(?P<object>[^，,；;。]{1,32}?)"
    r"(?:才)?(?:可以|允许|可|能)"
    r"(?:被)?(?P<action>[^，,；;。]{1,48})$",
    re.I,
)
_STATE_OR_RE = re.compile(r"\s*(?:或者|或|/|｜|\|)\s*")
_ACTION_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (canonical, re.compile(pattern, re.I))
    for canonical, pattern in (
        ("分配", r"^(?:被)?(?:分配|指派|派单)$"),
        ("开始处理", r"^(?:开始处理|开始办理|启动处理)$"),
        ("重开", r"^(?:被)?(?:重开|重新打开|重新开启)$"),
        ("解决", r"^(?:被)?(?:解决|处理完成|办结)$"),
        ("关闭", r"^(?:被)?关闭$"),
        ("升级", r"^(?:被)?(?:升级|上报)$"),
        ("评论", r"^(?:添加评论|发表评论|评论|留言)$"),
    )
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ordered_unique(values: Iterable[Any]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            rows.append(item)
    return rows


def _source_entities(fact: dict[str, Any]) -> list[str]:
    subject = _dict(fact.get("subject"))
    object_part = _dict(fact.get("object"))
    return _ordered_unique(
        [
            *_list(subject.get("entity_mentions")),
            *_list(subject.get("entity_refs")),
            *_list(subject.get("resolved_entity_refs")),
            *_list(object_part.get("entity_mentions")),
            *_list(object_part.get("entity_refs")),
            *_list(object_part.get("resolved_entity_refs")),
        ]
    )


def _canonical_action(value: Any) -> str:
    raw = _text(value)
    for canonical, pattern in _ACTION_ALIASES:
        if pattern.fullmatch(raw):
            return canonical
    return ""


def _states(value: Any) -> list[str]:
    return _ordered_unique(
        row.strip()
        for row in _STATE_OR_RE.split(_text(value))
        if row.strip()
    )


def close_state_guard_coordinates(facts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fill literal state/action coordinates on existing atomic facts in place."""
    normalized_ids: list[str] = []
    existing_coordinate_count = 0
    non_guard_count = 0
    identity_reuse_failures: list[str] = []
    coordinate_conflicts: list[str] = []

    for fact in facts:
        if not isinstance(fact, dict) or _text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            continue
        statement = _text(fact.get("normalized_statement") or fact.get("raw_statement"))
        match = _STATE_GUARD_RE.fullmatch(statement)
        if match is None:
            non_guard_count += 1
            continue
        object_name = _text(match.group("object"))
        if object_name not in _source_entities(fact):
            identity_reuse_failures.append(_text(fact.get("fact_id")))
            continue
        canonical_action = _canonical_action(match.group("action"))
        state_values = _states(match.group("states"))
        if not canonical_action or not state_values:
            non_guard_count += 1
            continue

        existing_action = _dict(fact.get("action"))
        existing_canonical = _text(existing_action.get("canonical") or existing_action.get("raw"))
        existing_conditions = _ordered_unique(_list(fact.get("conditions")))
        expected_conditions = [f"{object_name}.status={state}" for state in state_values]
        if existing_canonical and existing_canonical != canonical_action:
            coordinate_conflicts.append(_text(fact.get("fact_id")))
            continue
        if existing_conditions and existing_conditions != expected_conditions:
            coordinate_conflicts.append(_text(fact.get("fact_id")))
            continue
        if existing_canonical and existing_conditions:
            existing_coordinate_count += 1
            continue

        fact["action"] = {
            "canonical": canonical_action,
            "raw": _text(match.group("action")),
        }
        fact["conditions"] = expected_conditions
        combinator = "OR" if len(expected_conditions) > 1 else "SINGLE_CONDITION"
        fact["condition_combinator"] = combinator
        fact["condition_frame"] = {
            "kind": "ANY" if combinator == "OR" else "LEAF",
            "combinator": combinator,
            "conditions": list(expected_conditions),
            "exception_scopes": [],
            "overlays": [],
            "source_backed": True,
        }
        fact["trigger"] = {"raw": _text(match.group("states")) + "状态"}
        fact["state_guard_coordinate_closure"] = {
            "schema": RECEIPT_SCHEMA,
            "status": "PASS",
            "source_backed": True,
            "object_identity_reused": object_name,
            "new_fact_created": False,
            "fact_id_changed": False,
            "endpoint_operation_inference_used": False,
        }
        fact_id = _text(fact.get("fact_id"))
        if fact_id:
            normalized_ids.append(fact_id)

    return {
        "schema": RECEIPT_SCHEMA,
        "status": "BLOCKED_COORDINATE_CONFLICT" if coordinate_conflicts else "PASS",
        "normalized_fact_count": len(normalized_ids),
        "normalized_fact_ids": normalized_ids,
        "existing_coordinate_count": existing_coordinate_count,
        "non_state_guard_count": non_guard_count,
        "object_identity_reuse_failure_ids": identity_reuse_failures,
        "coordinate_conflict_fact_ids": coordinate_conflicts,
        "existing_atomic_facts_reused": True,
        "runs_before_structured_fact_deduplication": True,
        "new_fact_creation_allowed": False,
        "fact_id_rewrite_allowed": False,
        "endpoint_operation_inference_allowed": False,
        "automatic_conflict_winner_allowed": False,
    }


def synchronize_rule_library_from_facts(asset: dict[str, Any], facts: Iterable[dict[str, Any]]) -> None:
    """Refresh the existing compatibility rule projection from the same fact objects."""
    by_id = {
        _text(fact.get("fact_id")): fact
        for fact in facts
        if isinstance(fact, dict) and _text(fact.get("fact_id"))
    }
    for rule in _list(asset.get("rule_library")):
        if not isinstance(rule, dict):
            continue
        contract = _dict(rule.get("semantic_contract"))
        fact = by_id.get(_text(contract.get("fact_id")))
        if fact is None:
            continue
        action = _dict(fact.get("action"))
        rule["semantic_contract"] = fact
        rule["action"] = _text(action.get("canonical") or action.get("raw"))
        rule["conditions"] = list(_list(fact.get("conditions")))
        rule["condition_combinator"] = _text(fact.get("condition_combinator"))
        rule["condition_frame"] = dict(_dict(fact.get("condition_frame")))


__all__ = [
    "RECEIPT_SCHEMA",
    "close_state_guard_coordinates",
    "synchronize_rule_library_from_facts",
]
