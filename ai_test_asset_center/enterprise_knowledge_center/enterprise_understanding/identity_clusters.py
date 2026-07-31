"""Identity closure, stable registry reuse, conflicts and technical bindings."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from .identity_edges import identity_evidence_class
from .identity_types import (
    HARD_IDENTITY_CLASSES,
    IDENTITY_BINDING_SCHEMA,
    IDENTITY_CLUSTER_SCHEMA,
)
from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    new_unknown,
    stable_id,
    text,
    unique_text,
)

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,31}$")


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def _canonical(labels: Iterable[str], votes: Iterable[str]) -> str:
    pool = [text(value) for value in votes if text(value)] or unique_text(labels)
    counts: dict[str, int] = defaultdict(int)
    for value in pool:
        counts[value] += 1
    return (
        sorted(
            counts,
            key=lambda value: (
                -counts[value],
                -len(_CJK.findall(value)),
                bool(_CODE.fullmatch(value)),
                -len(value),
                value,
            ),
        )[0]
        if counts
        else ""
    )


def build_identity_clusters(
    asset: dict[str, Any],
    mentions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    business = {
        text(row.get("mention_id")): row
        for row in mentions
        if text(row.get("mention_type")) == "BUSINESS_OBJECT"
    }
    union = _UnionFind(business)
    for edge in edges:
        left, right = text(edge.get("left_mention_id")), text(edge.get("right_mention_id"))
        if (
            text(edge.get("relation")) == "SAME_AS"
            and bool(edge.get("automatic_union_allowed"))
            and left in business
            and right in business
        ):
            union.union(left, right)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention_id, mention in business.items():
        groups[union.find(mention_id)].append(mention)

    votes: dict[str, list[str]] = defaultdict(list)
    accepted_edge_ids: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        left = text(edge.get("left_mention_id"))
        if (
            left not in business
            or text(edge.get("relation")) != "SAME_AS"
            or text(edge.get("status")) != "ACCEPTED"
        ):
            continue
        root = union.find(left)
        accepted_edge_ids[root].append(text(edge.get("edge_id")))
        for endpoint in (left, text(edge.get("right_mention_id"))):
            row = as_dict(business.get(endpoint))
            if text(row.get("role")) == "canonical":
                votes[root].append(text(row.get("raw_label")))

    previous = [
        row
        for row in as_list(as_dict(asset.get("enterprise_identity_registry")).get("entities"))
        if isinstance(row, dict)
    ]
    clusters: list[dict[str, Any]] = []
    mention_to_entity: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for root, members in sorted(groups.items()):
        labels = unique_text(row.get("raw_label") for row in members)
        matched = [
            row
            for row in previous
            if set(labels)
            & set(
                unique_text(
                    [row.get("canonical_label"), *as_list(row.get("aliases")), *as_list(row.get("labels"))]
                )
            )
        ]
        canonical = _canonical(labels, votes.get(root, []))
        if len(matched) > 1:
            entity_id, status = stable_id("enterprise_entity_conflicted", labels), "CONFLICTED"
            conflicts.append(
                {
                    "kind": "IDENTITY_REGISTRY_CLUSTER_COLLISION",
                    "status": "UNRESOLVED",
                    "labels": labels,
                    "candidate_entity_ids": unique_text(row.get("entity_id") for row in matched),
                    "automatic_resolution_allowed": False,
                    "evidence": dedupe_evidence(
                        evidence for row in members for evidence in as_list(row.get("evidence"))
                    ),
                }
            )
        else:
            entity_id = text(as_dict(matched[0] if matched else {}).get("entity_id")) or stable_id(
                "enterprise_entity",
                members[0].get("source_id"),
                members[0].get("source_locator"),
                canonical,
            )
            status = "RESOLVED"
        cluster = {
            "schema": IDENTITY_CLUSTER_SCHEMA,
            "entity_id": entity_id,
            "entity_type": "BUSINESS_OBJECT",
            "canonical_label": canonical,
            "member_mention_ids": sorted(text(row.get("mention_id")) for row in members),
            "accepted_identity_edge_ids": sorted(set(accepted_edge_ids.get(root, []))),
            "aliases": [label for label in labels if label != canonical],
            "labels": labels,
            "status": status,
            "evidence": dedupe_evidence(
                evidence for row in members for evidence in as_list(row.get("evidence"))
            ),
        }
        clusters.append(cluster)
        for member in members:
            mention_to_entity[text(member.get("mention_id"))] = entity_id
    return clusters, mention_to_entity, conflicts


def build_label_to_entity(
    mentions: list[dict[str, Any]], clusters: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, set[str]]]:
    by_id = {text(row.get("mention_id")): row for row in mentions}
    mapping: dict[str, str] = {}
    collisions: dict[str, set[str]] = defaultdict(set)
    for cluster in clusters:
        entity_id = text(cluster.get("entity_id"))
        for mention_id in as_list(cluster.get("member_mention_ids")):
            label = text(as_dict(by_id.get(text(mention_id))).get("raw_label"))
            if label in mapping and mapping[label] != entity_id:
                collisions[label].update({mapping[label], entity_id})
                mapping.pop(label, None)
            elif label and label not in collisions:
                mapping[label] = entity_id
    return mapping, collisions


def build_alias_conflicts(
    facts: list[dict[str, Any]], label_to_entity: dict[str, str]
) -> list[dict[str, Any]]:
    candidates: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if (
            not isinstance(fact, dict)
            or text(fact.get("kind")) != "TERM_ALIAS"
            or identity_evidence_class(fact) not in HARD_IDENTITY_CLASSES
        ):
            continue
        alias, canonical = text(fact.get("alias")), text(fact.get("canonical_term"))
        if text(fact.get("status")) == "ACCEPTED" and alias and label_to_entity.get(canonical):
            candidates[alias].add(label_to_entity[canonical])
            evidence[alias].extend(evidence_from_fact(fact))
    return [
        {
            "kind": "TERM_ALIAS_IDENTITY_CONFLICT",
            "status": "UNRESOLVED",
            "alias": alias,
            "candidate_entity_ids": sorted(entity_ids),
            "automatic_resolution_allowed": False,
            "evidence": dedupe_evidence(evidence[alias]),
        }
        for alias, entity_ids in sorted(candidates.items())
        if len(entity_ids) > 1
    ]


def build_identity_bindings(
    mentions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    mention_to_entity: dict[str, str],
    clusters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {text(row.get("mention_id")): row for row in mentions}
    bindings: list[dict[str, Any]] = []
    bound: set[str] = set()
    for edge in edges:
        if text(edge.get("relation")) != "IMPLEMENTS_ENTITY" or text(edge.get("status")) != "ACCEPTED":
            continue
        left = as_dict(by_id.get(text(edge.get("left_mention_id"))))
        right = as_dict(by_id.get(text(edge.get("right_mention_id"))))
        if text(left.get("mention_type")) == "TECHNICAL_ARTIFACT":
            technical, business_id = left, text(edge.get("right_mention_id"))
        else:
            technical, business_id = right, text(edge.get("left_mention_id"))
        entity_id = mention_to_entity.get(business_id)
        if not entity_id or text(technical.get("mention_type")) != "TECHNICAL_ARTIFACT":
            continue
        ref = text(technical.get("artifact_ref")) or text(technical.get("mention_id"))
        bound.add(ref)
        bindings.append(
            {
                "schema": IDENTITY_BINDING_SCHEMA,
                "binding_id": stable_id(
                    "identity_binding", entity_id, technical.get("artifact_type"), ref
                ),
                "entity_id": entity_id,
                "artifact_type": technical.get("artifact_type"),
                "artifact_ref": ref,
                "artifact_label": technical.get("raw_label"),
                "relation": "IMPLEMENTS_ENTITY",
                "status": "RESOLVED",
                "identity_field_bindings": [],
                "evidence": dedupe_evidence(
                    [*as_list(edge.get("evidence")), *as_list(technical.get("evidence"))]
                ),
            }
        )
    unbound = [
        row
        for row in mentions
        if text(row.get("mention_type")) == "TECHNICAL_ARTIFACT"
        and text(row.get("artifact_ref")) not in bound
    ]
    unknowns: list[dict[str, Any]] = []
    if unbound and clusters:
        unknowns.append(
            new_unknown(
                "CROSS_SOURCE_IDENTITY_UNRESOLVED",
                f"存在{len(unbound)}个技术资产尚未通过源声明绑定到业务身份；系统不会按名称相似自动合并。",
                related_objects=[row.get("canonical_label") for row in clusters],
                evidence=dedupe_evidence(
                    evidence for row in unbound for evidence in as_list(row.get("evidence"))
                ),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="CROSS_SOURCE_IDENTITY_UNRESOLVED",
                details={
                    "unresolved_artifacts": [
                        {
                            "artifact_type": row.get("artifact_type"),
                            "artifact_ref": row.get("artifact_ref"),
                            "label": row.get("raw_label"),
                        }
                        for row in unbound[:80]
                    ],
                    "automatic_inference_allowed": False,
                },
            )
        )
    return list({text(row.get("binding_id")): row for row in bindings}.values()), unknowns
