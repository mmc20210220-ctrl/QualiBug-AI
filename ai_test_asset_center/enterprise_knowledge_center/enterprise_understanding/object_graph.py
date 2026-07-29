"""Source-backed business object relation graph.

Relation verbs are language vocabulary, not industry rules. A relation is emitted
only when both endpoint objects are explicit and evidence is traceable. Token
similarity and document order never create a formal relation.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .schema import (
    RELATION_SCHEMA,
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

_RELATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GENERATES", re.compile(r"生成|产生|产出|创建出|形成")),
    ("CONSUMES", re.compile(r"消耗|扣减|占用|领用|使用")),
    ("DEPENDS_ON", re.compile(r"依赖|取决于|基于|根据")),
    ("BELONGS_TO", re.compile(r"属于|归属|隶属")),
    ("REFERENCES", re.compile(r"关联|引用|对应|绑定")),
    ("AFFECTS", re.compile(r"影响|联动|同步更新|同步变更")),
    ("COMPENSATES", re.compile(r"补偿|冲销|红冲|回滚|恢复")),
    ("CREATES", re.compile(r"创建|新建|新增")),
)

_RELATION_ALIASES = {
    "foreign_key": "REFERENCES",
    "field_to_table": "BELONGS_TO",
    "rule_to_entity": "REFERENCES",
    "state_sequence": "TRANSITIONS_TO",
    "role_to_resource": "REFERENCES",
    "business_dependency": "DEPENDS_ON",
    "generates": "GENERATES",
    "creates": "CREATES",
    "consumes": "CONSUMES",
    "depends_on": "DEPENDS_ON",
    "belongs_to": "BELONGS_TO",
    "references": "REFERENCES",
    "affects": "AFFECTS",
    "compensates": "COMPENSATES",
    "transitions_to": "TRANSITIONS_TO",
}


def _accepted_fact(fact: dict[str, Any]) -> bool:
    return text(fact.get("status")) == "ACCEPTED" and text(fact.get("kind")) in {
        "RULE",
        "STATE_TRANSITION",
    }


def _fact_entities(fact: dict[str, Any], object_names: Iterable[str]) -> list[str]:
    subject = as_dict(fact.get("subject"))
    object_part = as_dict(fact.get("object"))
    explicit = unique_text(
        [
            *as_list(subject.get("entity_refs")),
            *as_list(object_part.get("entity_refs")),
        ]
    )
    statement = text(fact.get("raw_statement"))
    known_mentions = [name for name in object_names if name and name in statement]
    return sorted(set([*explicit, *known_mentions]), key=lambda name: (statement.find(name), -len(name), name))


def _relation_type(statement: str) -> tuple[str, str]:
    matches: list[tuple[int, str, str]] = []
    for relation_type, pattern in _RELATION_PATTERNS:
        match = pattern.search(statement)
        if match:
            matches.append((match.start(), relation_type, match.group(0)))
    if not matches:
        return "", ""
    _, relation_type, raw = min(matches, key=lambda row: row[0])
    return relation_type, raw


def _ordered_endpoints(statement: str, entities: list[str], relation_raw: str) -> tuple[str, str]:
    if len(entities) < 2:
        return "", ""
    relation_at = statement.find(relation_raw) if relation_raw else -1
    before = [name for name in entities if statement.find(name) >= 0 and statement.find(name) < relation_at]
    after = [name for name in entities if relation_at >= 0 and statement.find(name, relation_at + len(relation_raw)) >= 0]
    if before and after:
        source = max(before, key=lambda name: statement.find(name))
        target = min(after, key=lambda name: statement.find(name, relation_at + len(relation_raw)))
        if source != target:
            return source, target
    return entities[0], entities[1]


def _normalize_existing_endpoint(value: Any, object_names: set[str]) -> str:
    raw = text(value)
    if not raw:
        return ""
    if raw in object_names:
        return raw
    terminal = raw.split(":")[-1]
    if terminal in object_names:
        return terminal
    exact = [name for name in object_names if name and (name == terminal or name == raw)]
    return exact[0] if len(exact) == 1 else ""


def build_object_graph(
    asset: dict[str, Any],
    facts: Iterable[dict[str, Any]],
    object_names: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build formal relations and unresolved-relation unknowns."""
    known = {text(name) for name in object_names if text(name)}
    relations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []

    for fact in facts:
        if not isinstance(fact, dict) or not _accepted_fact(fact):
            continue
        statement = text(fact.get("raw_statement"))
        relation_type, relation_raw = _relation_type(statement)
        if not relation_type:
            continue
        entities = _fact_entities(fact, known)
        # State-transition wording often contains vocabulary like “新建/生成”, but a
        # single-object lifecycle sentence is not an object-relation claim.
        if text(fact.get("kind")) == "STATE_TRANSITION" and len(entities) < 2:
            continue
        source, target = _ordered_endpoints(statement, entities, relation_raw)
        evidence = evidence_from_fact(fact)
        if not source or not target:
            unknowns.append(
                new_unknown(
                    "OBJECT_RELATION_ENDPOINT_UNRESOLVED",
                    f"语句“{statement}”表达了“{relation_raw}”关系，但关系两端业务对象无法唯一确定。",
                    related_objects=entities,
                    evidence=evidence,
                    severity="P1",
                    blocks_formal_understanding=bool(fact.get("critical")),
                    reason_code="OBJECT_RELATION_ENDPOINT_UNRESOLVED",
                    details={"relation_type": relation_type, "relation_raw": relation_raw, "fact_id": fact.get("fact_id")},
                )
            )
            continue
        relation_id = stable_id("business_relation", source, relation_type, target, fact.get("fact_id"))
        relations.append(
            {
                "schema": RELATION_SCHEMA,
                "relation_id": relation_id,
                "source_object_ref": source,
                "relation_type": relation_type,
                "target_object_ref": target,
                "conditions": unique_text(as_list(fact.get("conditions"))),
                "exceptions": unique_text(as_list(fact.get("exceptions"))),
                "raw_relation": relation_raw,
                "fact_refs": unique_text([fact.get("fact_id")]),
                "evidence": evidence,
                "status": "ACCEPTED",
                "derivation": "explicit_chinese_relation_statement",
            }
        )

    for index, row in enumerate(as_list(asset.get("entity_relations"))):
        if not isinstance(row, dict):
            continue
        status = text(row.get("status") or "accepted").lower()
        derivation = text(row.get("derivation")).lower().replace("-", "_")
        if status in {"candidate", "proposed", "unknown", "unsupported", "rejected"}:
            continue
        if derivation == "token_overlap" or text(row.get("evidence_gate")) == "token_overlap_only_requires_explicit_source_relation":
            continue
        source = _normalize_existing_endpoint(row.get("from") or row.get("source"), known)
        target = _normalize_existing_endpoint(row.get("to") or row.get("target"), known)
        if not source or not target or source == target:
            continue
        raw_relation = text(row.get("relation") or row.get("relation_type"))
        relation_type = _RELATION_ALIASES.get(raw_relation.lower(), raw_relation.upper() or "REFERENCES")
        evidence_rows: list[dict[str, Any]] = []
        evidence = row.get("evidence")
        if isinstance(evidence, dict):
            evidence_rows.append(
                source_evidence(
                    source_id=evidence.get("source_id") or row.get("source_id"),
                    source_locator=evidence.get("source_locator"),
                    quote=evidence.get("quote"),
                    quote_hash=evidence.get("quote_hash"),
                    asset_ref=row.get("edge_id") or row.get("relation_id") or f"entity_relations[{index}]",
                    derivation=derivation or "existing_source_backed_relation",
                )
            )
        if not evidence_rows:
            evidence_rows.append(
                source_evidence(
                    source_id=row.get("source_id"),
                    asset_ref=row.get("edge_id") or row.get("relation_id") or f"entity_relations[{index}]",
                    derivation=derivation or "existing_source_backed_relation",
                )
            )
        relations.append(
            {
                "schema": RELATION_SCHEMA,
                "relation_id": stable_id("business_relation", source, relation_type, target, row.get("edge_id") or index),
                "source_object_ref": source,
                "relation_type": relation_type,
                "target_object_ref": target,
                "conditions": [],
                "exceptions": [],
                "raw_relation": raw_relation,
                "fact_refs": [],
                "evidence": dedupe_evidence(evidence_rows),
                "status": "ACCEPTED",
                "derivation": derivation or "existing_source_backed_relation",
            }
        )

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for relation in relations:
        key = (
            text(relation.get("source_object_ref")),
            text(relation.get("relation_type")),
            text(relation.get("target_object_ref")),
        )
        if not all(key):
            continue
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = relation
            continue
        existing["conditions"] = unique_text([*as_list(existing.get("conditions")), *as_list(relation.get("conditions"))])
        existing["exceptions"] = unique_text([*as_list(existing.get("exceptions")), *as_list(relation.get("exceptions"))])
        existing["fact_refs"] = unique_text([*as_list(existing.get("fact_refs")), *as_list(relation.get("fact_refs"))])
        existing["evidence"] = dedupe_evidence([*as_list(existing.get("evidence")), *as_list(relation.get("evidence"))])

    return sorted(deduped.values(), key=lambda row: (text(row.get("source_object_ref")), text(row.get("relation_type")), text(row.get("target_object_ref")))), unknowns


__all__ = ["build_object_graph"]
