"""Source-declared identity edges; no fuzzy or industry-name authority."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .identity_evidence_policy import classify_identity_fact
from .identity_types import (
    HARD_IDENTITY_CLASSES,
    IDENTITY_EDGE_SCHEMA,
    asset_evidence,
    identity_scope,
)
from .schema import as_dict, as_list, dedupe_evidence, evidence_from_fact, stable_id, text


def identity_evidence_class(fact: dict[str, Any]) -> str:
    return classify_identity_fact(fact)


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
        if (
            text(fact.get("status")) == "ACCEPTED"
            and identity_evidence_class(fact) in HARD_IDENTITY_CLASSES
        ):
            values[text(fact.get("alias"))].add(text(fact.get("canonical_term")))
    return {alias for alias, canonicals in values.items() if alias and len(canonicals) > 1}


def _identity_edge(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    scope: dict[str, str],
    accepted: bool,
    authority: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
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
        "authority": authority,
        "status": "ACCEPTED" if accepted else "CANDIDATE_ONLY",
        "independent_evidence_family": "EXACT_LABEL_SAME_SCOPE",
        "scope": scope,
        "evidence": dedupe_evidence(
            [*as_list(left.get("evidence")), *as_list(right.get("evidence"))]
        ),
        "automatic_union_allowed": accepted,
        "reason_code": reason_code,
    }


def _source_declared_identity_anchor_labels(
    facts: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    conflicting_aliases: set[str],
) -> set[str]:
    """Return labels whose cross-source equality is backed by source authority.

    Exact text alone is never enough. A label becomes an anchor only when an
    accepted hard identity fact names it, or one unique business-object asset
    declares it. Conflicting alias labels remain fail-closed even when their text
    matches across sources.
    """
    anchors: set[str] = set()
    for fact in facts:
        if (
            not isinstance(fact, dict)
            or text(fact.get("kind")) != "TERM_ALIAS"
            or text(fact.get("status")) != "ACCEPTED"
            or identity_evidence_class(fact) not in HARD_IDENTITY_CLASSES
        ):
            continue
        for label in (text(fact.get("canonical_term")), text(fact.get("alias"))):
            if label and label not in conflicting_aliases:
                anchors.add(label)

    declared_occurrences: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for mention in mentions:
        if text(mention.get("source_kind")) not in {
            "BUSINESS_OBJECT_ASSET",
            "BUSINESS_OBJECT_ALIAS",
        }:
            continue
        label = text(mention.get("raw_label"))
        if label:
            declared_occurrences[label].add(
                (text(mention.get("source_id")), text(mention.get("source_locator")))
            )
    anchors.update(
        label
        for label, occurrences in declared_occurrences.items()
        if len(occurrences) == 1 and label not in conflicting_aliases
    )
    return anchors


def _exact_occurrence_edges(
    mentions: list[dict[str, Any]],
    identity_anchor_labels: set[str],
) -> list[dict[str, Any]]:
    """Close one source occurrence before comparing it with other sources.

    Subject/object projections from one fact are the same occurrence. Sorting all
    mentions globally can interleave another source between them, so closure must
    first happen inside ``(source_id, source_locator)`` and only then across sources.
    """
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        if text(mention.get("mention_type")) != "BUSINESS_OBJECT":
            continue
        scope = as_dict(mention.get("scope"))
        grouped[
            (
                text(mention.get("raw_label")),
                text(scope.get("system")),
                text(scope.get("module")),
                text(scope.get("version")),
            )
        ].append(mention)

    edges: list[dict[str, Any]] = []
    for key, group in grouped.items():
        scope = {"system": key[1], "module": key[2], "version": key[3]}
        occurrences: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for mention in group:
            occurrences[
                (text(mention.get("source_id")), text(mention.get("source_locator")))
            ].append(mention)

        representatives: list[dict[str, Any]] = []
        for occurrence in sorted(occurrences):
            ordered = sorted(
                occurrences[occurrence], key=lambda row: text(row.get("mention_id"))
            )
            representatives.append(ordered[0])
            for left, right in zip(ordered, ordered[1:]):
                edges.append(
                    _identity_edge(
                        left,
                        right,
                        scope=scope,
                        accepted=True,
                        authority="SAME_SOURCE_OCCURRENCE",
                        reason_code="EXACT_LABEL_SAME_OCCURRENCE",
                    )
                )

        scope_declared = any(key[1:])
        identity_anchor_declared = key[0] in identity_anchor_labels
        ordered_representatives = sorted(
            representatives, key=lambda row: text(row.get("mention_id"))
        )
        for left, right in zip(ordered_representatives, ordered_representatives[1:]):
            edges.append(
                _identity_edge(
                    left,
                    right,
                    scope=scope,
                    accepted=scope_declared or identity_anchor_declared,
                    authority=(
                        "EXPLICIT_SCOPE"
                        if scope_declared
                        else "SOURCE_DECLARED_IDENTITY_ANCHOR"
                        if identity_anchor_declared
                        else "CANDIDATE_ONLY_SCOPE_MISSING"
                    ),
                    reason_code=(
                        "EXACT_LABEL_SCOPE_PROVEN"
                        if scope_declared
                        else "EXACT_LABEL_IDENTITY_ANCHOR_PROVEN"
                        if identity_anchor_declared
                        else "EXACT_LABEL_SCOPE_MISSING"
                    ),
                )
            )
    return edges


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

    # A source-declared business-object asset names its display aliases directly
    # (``name`` + ``aliases``). That declared alias is identity evidence that the
    # canonical label and the alias denote one entity; identity resolution must not
    # split them back into separate entities merely because their surface text
    # differs. This is the same source-backed authority as a hard TERM_ALIAS fact.
    canonical_mentions: dict[tuple[str, str], dict[str, Any]] = {}
    for mention in mentions:
        if text(mention.get("source_kind")) == "BUSINESS_OBJECT_ASSET":
            key = (text(mention.get("source_id")), text(mention.get("source_locator")))
            canonical_mentions.setdefault(key, mention)
    alias_edges: dict[str, dict[str, Any]] = {}
    for mention in mentions:
        if text(mention.get("source_kind")) != "BUSINESS_OBJECT_ALIAS":
            continue
        key = (text(mention.get("source_id")), text(mention.get("source_locator")))
        canonical = canonical_mentions.get(key)
        if canonical is None:
            continue
        edge_id = stable_id(
            "identity_edge",
            canonical.get("mention_id"),
            mention.get("mention_id"),
            "SAME_AS",
            "SOURCE_DECLARED_BUSINESS_OBJECT_ALIAS",
        )
        alias_edges[edge_id] = {
            "schema": IDENTITY_EDGE_SCHEMA,
            "edge_id": edge_id,
            "left_mention_id": canonical.get("mention_id"),
            "right_mention_id": mention.get("mention_id"),
            "relation": "SAME_AS",
            "evidence_class": "EXPLICIT_ALIAS",
            "authority": "SOURCE_DECLARED_BUSINESS_OBJECT_ALIAS",
            "status": "ACCEPTED",
            "independent_evidence_family": "BUSINESS_OBJECT_ASSET_ALIAS",
            "scope": as_dict(mention.get("scope")),
            "evidence": dedupe_evidence(
                [*as_list(canonical.get("evidence")), *as_list(mention.get("evidence"))]
            ),
            "automatic_union_allowed": True,
        }
    edges.extend(alias_edges.values())

    conflicting_aliases = _conflicting_aliases(facts)
    identity_anchor_labels = _source_declared_identity_anchor_labels(
        facts, mentions, conflicting_aliases
    )
    edges.extend(_exact_occurrence_edges(mentions, identity_anchor_labels))

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
