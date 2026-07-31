"""Source-declared identity edges; no fuzzy or industry-name authority."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .identity_types import (
    HARD_IDENTITY_CLASSES,
    IDENTITY_EDGE_SCHEMA,
    asset_evidence,
    identity_scope,
)
from .schema import as_dict, as_list, dedupe_evidence, evidence_from_fact, stable_id, text

_FORMULA = re.compile(r"[=＝+＋×*÷/<>]|(?:加|减|乘|除).*(?:金额|数量|单价)")


def identity_evidence_class(fact: dict[str, Any]) -> str:
    explicit = text(fact.get("identity_evidence_class"))
    if explicit:
        return explicit
    statement = text(fact.get("raw_statement"))
    alias = text(fact.get("alias"))
    if _FORMULA.search(alias) or _FORMULA.search(statement):
        return "DEFINITION"
    if re.search(r"更名为|改称|原名|旧称|新名称", statement):
        return "RENAMING"
    if re.search(r"以下简称|简称为|简称|缩写|[（(][^()（）]{1,32}[）)]|aka|a\.k\.a", statement, re.I):
        return "EXPLICIT_ABBREVIATION"
    if re.search(r"又称|也称|又名|也叫|又叫|亦称|等同于|同义于|also known as|also called", statement, re.I):
        return "EXPLICIT_ALIAS"
    if re.search(r"是指|指的是|定义为|定义是|即为|即是", statement):
        return "DEFINITION"
    return "POSSIBLE_EQUIVALENCE"


def _pick(
    mentions: list[dict[str, Any]], label: str, source_id: str, role: str
) -> dict[str, Any] | None:
    candidates = [row for row in mentions if text(row.get("raw_label")) == label]
    exact = [
        row
        for row in candidates
        if text(row.get("source_id")) == source_id and text(row.get("role")) == role
    ]
    same_source = [row for row in candidates if text(row.get("source_id")) == source_id]
    chosen = exact or same_source or candidates
    return sorted(chosen, key=lambda row: text(row.get("mention_id")))[0] if chosen else None


def _conflicting_aliases(facts: list[dict[str, Any]]) -> set[str]:
    values: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        if not isinstance(fact, dict) or text(fact.get("kind")) != "TERM_ALIAS":
            continue
        if text(fact.get("status")) == "ACCEPTED" and identity_evidence_class(fact) in HARD_IDENTITY_CLASSES:
            values[text(fact.get("alias"))].add(text(fact.get("canonical_term")))
    return {alias for alias, canonicals in values.items() if alias and len(canonicals) > 1}


def build_identity_edges(
    asset: dict[str, Any],
    facts: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    technical_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    business_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        target = (
            technical_by_label
            if text(mention.get("mention_type")) == "TECHNICAL_ARTIFACT"
            else business_by_label
        )
        target[text(mention.get("raw_label"))].append(mention)

    for fact in facts:
        if (
            not isinstance(fact, dict)
            or text(fact.get("kind")) != "TERM_ALIAS"
            or text(fact.get("status")) != "ACCEPTED"
        ):
            continue
        canonical, alias = text(fact.get("canonical_term")), text(fact.get("alias"))
        spans = as_list(fact.get("source_spans"))
        span = as_dict(spans[0]) if spans else {}
        source_id = text(span.get("source_id") or fact.get("source_id") or "fact")
        left = _pick(mentions, canonical, source_id, "canonical")
        right = _pick(mentions, alias, source_id, "alias")
        if not left or not right or not canonical or not alias or canonical == alias:
            continue
        relation = "SAME_AS"
        evidence_class = identity_evidence_class(fact)
        technical = technical_by_label.get(canonical, []) + technical_by_label.get(alias, [])
        if technical:
            technical_mention = sorted(
                technical, key=lambda row: text(row.get("mention_id"))
            )[0]
            business_mention = (
                right if text(technical_mention.get("raw_label")) == canonical else left
            )
            left, right, relation = business_mention, technical_mention, "IMPLEMENTS_ENTITY"
        accepted = relation != "SAME_AS" or evidence_class in HARD_IDENTITY_CLASSES
        edges.append(
            {
                "schema": IDENTITY_EDGE_SCHEMA,
                "edge_id": stable_id(
                    "identity_edge",
                    left.get("mention_id"),
                    right.get("mention_id"),
                    relation,
                    evidence_class,
                ),
                "left_mention_id": left.get("mention_id"),
                "right_mention_id": right.get("mention_id"),
                "relation": relation,
                "evidence_class": evidence_class,
                "authority": "SOURCE_DECLARED",
                "status": "ACCEPTED" if accepted else "CANDIDATE_ONLY",
                "independent_evidence_family": "TERM_ALIAS",
                "scope": identity_scope(fact),
                "evidence": evidence_from_fact(fact),
                "automatic_union_allowed": relation == "SAME_AS" and accepted,
            }
        )

    exact: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        if text(mention.get("mention_type")) != "BUSINESS_OBJECT":
            continue
        scope = as_dict(mention.get("scope"))
        exact[
            (
                text(mention.get("raw_label")),
                text(scope.get("system")),
                text(scope.get("module")),
                text(scope.get("version")),
            )
        ].append(mention)
    for key, group in exact.items():
        ordered = sorted(group, key=lambda row: text(row.get("mention_id")))
        for left, right in zip(ordered, ordered[1:]):
            edges.append(
                {
                    "schema": IDENTITY_EDGE_SCHEMA,
                    "edge_id": stable_id(
                        "identity_edge",
                        left.get("mention_id"),
                        right.get("mention_id"),
                        "EXACT_LABEL_SAME_SCOPE",
                    ),
                    "left_mention_id": left.get("mention_id"),
                    "right_mention_id": right.get("mention_id"),
                    "relation": "SAME_AS",
                    "evidence_class": "EXACT_LABEL_SAME_SCOPE",
                    "authority": "SOURCE_OCCURRENCE_IDENTITY",
                    "status": "ACCEPTED",
                    "independent_evidence_family": "EXACT_LABEL_SAME_SCOPE",
                    "scope": {"system": key[1], "module": key[2], "version": key[3]},
                    "evidence": dedupe_evidence(
                        [*as_list(left.get("evidence")), *as_list(right.get("evidence"))]
                    ),
                    "automatic_union_allowed": True,
                }
            )

    for index, raw in enumerate(as_list(asset.get("data_tables"))):
        if not isinstance(raw, dict):
            continue
        business_label = text(
            raw.get("business_object")
            or raw.get("business_entity")
            or raw.get("object_ref")
            or raw.get("entity_ref")
        )
        technical_label = text(raw.get("name") or raw.get("table"))
        business = sorted(
            business_by_label.get(business_label, []),
            key=lambda row: text(row.get("mention_id")),
        )
        technical = sorted(
            technical_by_label.get(technical_label, []),
            key=lambda row: text(row.get("mention_id")),
        )
        if not business_label or not technical_label or not business or not technical:
            continue
        left, right = business[0], technical[0]
        edges.append(
            {
                "schema": IDENTITY_EDGE_SCHEMA,
                "edge_id": stable_id(
                    "identity_edge",
                    left.get("mention_id"),
                    right.get("mention_id"),
                    "IMPLEMENTS_ENTITY",
                ),
                "left_mention_id": left.get("mention_id"),
                "right_mention_id": right.get("mention_id"),
                "relation": "IMPLEMENTS_ENTITY",
                "evidence_class": "EXPLICIT_IMPLEMENTATION_MAPPING",
                "authority": "SOURCE_DECLARED_ASSET",
                "status": "ACCEPTED",
                "independent_evidence_family": "DATABASE_TABLE",
                "scope": identity_scope(raw),
                "evidence": asset_evidence(
                    raw,
                    text(raw.get("table_id")) or f"data_tables[{index}]",
                    "explicit_business_technical_binding",
                ),
                "automatic_union_allowed": False,
            }
        )

    conflicting_aliases = _conflicting_aliases(facts)
    if conflicting_aliases:
        by_id = {text(row.get("mention_id")): row for row in mentions}
        for edge in edges:
            if (
                text(edge.get("independent_evidence_family")) != "TERM_ALIAS"
                or text(edge.get("relation")) != "SAME_AS"
            ):
                continue
            endpoints = [
                as_dict(by_id.get(text(edge.get("left_mention_id")))),
                as_dict(by_id.get(text(edge.get("right_mention_id")))),
            ]
            aliases = {
                text(row.get("raw_label"))
                for row in endpoints
                if text(row.get("role")) == "alias"
            }
            if aliases & conflicting_aliases:
                edge["status"] = "CONFLICTED"
                edge["automatic_union_allowed"] = False
                edge["conflict_reason"] = "TERM_ALIAS_IDENTITY_CONFLICT"
    return list({text(row.get("edge_id")): row for row in edges}.values())
