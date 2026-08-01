"""Source-backed object declarations for narrative-only requirements.

This module does not perform open-ended noun extraction.  It reuses two existing
product authorities that survive when API/database material is absent:

* source-grounded permission semantic frames; and
* exact relation chains observed in document structure blocks.

The result is only object-*type* authority.  Lexically overlapping labels remain
identity-pending unless the existing identity authority later supplies an alias.
"""
from __future__ import annotations

import re
from typing import Any

from ._object_role_evidence import accepted_facts, comparison_key, negative_role_index
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

_ARROW = re.compile(r"\s*(?:→|->|⇒|=>|⟶|──>|—>)\s*")
_CLAUSE = re.compile(r"[；;。\n]+")
_TRIM = " \t\r\n-—–:：,，。；;、()（）[]【】<>《》\"'`"
_ENUM = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_LABEL = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fffA-Za-z][\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_. -]{0,39}$")
_PRD_TYPES = frozenset({"prd", "requirement", "requirements", "product_requirement"})


def _source_types(asset: dict[str, Any]) -> dict[str, str]:
    inventory = [
        row
        for row in (
            as_list(asset.get("source_inventory"))
            or as_list(asset.get("canonical_source_inventory"))
        )
        if isinstance(row, dict)
    ]
    return {
        text(row.get("source_id")): text(
            row.get("source_type") or row.get("type") or row.get("kind")
        ).casefold()
        for row in inventory
        if text(row.get("source_id"))
    }


def _source_backed_evidence(
    *, source_id: Any, locator: Any, quote: Any, derivation: str, asset_ref: Any = ""
) -> list[dict[str, Any]]:
    sid, loc, raw = text(source_id), text(locator), text(quote)
    if not sid or sid == "industry_inference" or not loc or not raw:
        return []
    row = {
        "source_id": sid,
        "source_locator": loc,
        "quote": raw,
        "derivation": derivation,
    }
    if text(asset_ref):
        row["asset_ref"] = text(asset_ref)
    return [row]


def _actor_labels(asset: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for row in as_list(asset.get("roles")):
        if not isinstance(row, dict):
            continue
        for value in (row.get("role"), row.get("name"), row.get("display_name")):
            key = comparison_key(value)
            if key:
                labels.add(key)
        evidence = row.get("evidence")
        if isinstance(evidence, str):
            key = comparison_key(evidence)
            if key:
                labels.add(key)
        for item in as_list(evidence):
            if not isinstance(item, dict):
                continue
            for value in (
                item.get("matched_term"),
                item.get("quote"),
                item.get("label"),
            ):
                key = comparison_key(value)
                if key:
                    labels.add(key)
    return labels


def _paragraph_nodes(asset: dict[str, Any]) -> list[dict[str, Any]]:
    root = asset.get("document_semantic_trees")
    trees = as_list(as_dict(root).get("items")) or as_list(root)
    rows: list[dict[str, Any]] = []
    for tree in trees:
        if not isinstance(tree, dict):
            continue
        source_id = text(tree.get("source_id"))
        for node in as_list(tree.get("nodes")):
            if not isinstance(node, dict) or bool(node.get("semantic_heading")):
                continue
            if text(node.get("span_kind") or node.get("block_type")).upper() not in {
                "PARAGRAPH",
                "LIST_ITEM",
                "TEXT",
                "",
            }:
                continue
            copied = dict(node)
            copied.setdefault("source_id", source_id)
            rows.append(copied)
    return rows


def _clean_endpoint(value: Any) -> str:
    label = text(value).strip(_TRIM)
    label = re.sub(r"^(?:\d+(?:\.\d+)*[.)、：:]?\s*)", "", label).strip(_TRIM)
    return label


def _state_context(node: dict[str, Any]) -> bool:
    context = " ".join(
        [
            *[text(value) for value in as_list(node.get("path_titles"))],
            text(node.get("title")),
        ]
    ).casefold()
    return any(marker in context for marker in ("状态机", "状态流转", "state machine", "lifecycle state"))


def _relation_chain_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    source_types = _source_types(asset)
    negative = negative_role_index(asset, accepted_facts(asset))
    rows: list[dict[str, Any]] = []
    for node in _paragraph_nodes(asset):
        source_id = text(node.get("source_id") or as_dict(node.get("evidence")).get("source_id"))
        source_type = source_types.get(source_id, "")
        if source_type and source_type not in _PRD_TYPES:
            continue
        if _state_context(node):
            continue
        raw = text(node.get("title") or node.get("text"))
        evidence_row = as_dict(node.get("evidence"))
        locator = text(evidence_row.get("source_locator") or node.get("source_locator"))
        for clause in _CLAUSE.split(raw):
            if not _ARROW.search(clause):
                continue
            endpoints = [_clean_endpoint(value) for value in _ARROW.split(clause)]
            if len(endpoints) < 3 or any(not value for value in endpoints):
                continue
            if any(not _SAFE_LABEL.fullmatch(value) for value in endpoints):
                continue
            # Enum/state chains are not object relations.
            if all(_ENUM.fullmatch(value) for value in endpoints):
                continue
            endpoint_keys = [comparison_key(value) for value in endpoints]
            if any(
                bool(negative.get(key, set()) & {"ACTOR", "ACTION", "STATE"})
                for key in endpoint_keys
            ):
                continue
            evidence = _source_backed_evidence(
                source_id=source_id,
                locator=locator,
                quote=clause.strip(),
                derivation="source_narrative_relation_chain",
                asset_ref=node.get("node_id") or node.get("document_block_id"),
            )
            if not evidence:
                continue
            for position, label in enumerate(endpoints):
                rows.append(
                    {
                        "declaration_id": stable_id(
                            "source_narrative_relation_endpoint",
                            source_id,
                            locator,
                            clause,
                            position,
                            label,
                        ),
                        "canonical_label": label,
                        "labels": [label],
                        "evidence": evidence,
                        "authority": "SOURCE_NARRATIVE_RELATION_ENDPOINT",
                        "surface_suffix_discovery_allowed": False,
                        "surface_prefix_discovery_allowed": False,
                        "narrative_source_id": source_id,
                        "narrative_role": "RELATION_ENDPOINT",
                    }
                )
    return rows


def _permission_subject_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    source_types = _source_types(asset)
    actor_keys = _actor_labels(asset)
    rows: list[dict[str, Any]] = []
    for rule in as_list(asset.get("rule_library")):
        if not isinstance(rule, dict) or text(rule.get("rule_type")).casefold() != "permission":
            continue
        frame = as_dict(rule.get("semantic_frame"))
        if not bool(frame.get("source_grounded")):
            continue
        source_id = text(rule.get("source_id"))
        source_type = text(rule.get("source_type")).casefold() or source_types.get(source_id, "")
        if source_type and source_type not in _PRD_TYPES:
            continue
        subject = _clean_endpoint(frame.get("subject"))
        statement = text(rule.get("statement"))
        key = comparison_key(subject)
        if (
            not subject
            or not statement
            or key in actor_keys
            or not _SAFE_LABEL.fullmatch(subject)
            or comparison_key(subject) not in comparison_key(statement)
        ):
            continue
        evidence = _source_backed_evidence(
            source_id=source_id,
            locator=rule.get("source_locator") or rule.get("rule_id"),
            quote=statement,
            derivation="source_grounded_permission_subject",
            asset_ref=rule.get("rule_id"),
        )
        if not evidence:
            continue
        rows.append(
            {
                "declaration_id": stable_id(
                    "source_grounded_permission_object", source_id, statement, subject
                ),
                "canonical_label": subject,
                "labels": [subject],
                "evidence": evidence,
                "authority": "SOURCE_GROUNDED_PERMISSION_SUBJECT",
                "surface_suffix_discovery_allowed": False,
                "surface_prefix_discovery_allowed": False,
                "narrative_source_id": source_id,
                "narrative_role": "PERMISSION_RESOURCE",
            }
        )
    return rows


def source_narrative_object_declarations(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Return conservative declarations available without API/database material."""

    rows = [*_permission_subject_rows(asset), *_relation_chain_rows(asset)]
    permission_rows = [
        row for row in rows if text(row.get("narrative_role")) == "PERMISSION_RESOURCE"
    ]
    for row in rows:
        if text(row.get("narrative_role")) != "RELATION_ENDPOINT":
            continue
        label = text(row.get("canonical_label"))
        key = comparison_key(label)
        parents = unique_text(
            parent.get("canonical_label")
            for parent in permission_rows
            if text(parent.get("narrative_source_id")) == text(row.get("narrative_source_id"))
            and comparison_key(parent.get("canonical_label")) != key
            and comparison_key(parent.get("canonical_label")).endswith(key)
        )
        if parents:
            row["identity_pending_parent_labels"] = parents
    # Exact de-duplication only; semantic union remains outside this module.
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (comparison_key(row.get("canonical_label")), text(row.get("authority")))
        if not key[0]:
            continue
        prior = by_key.get(key)
        if prior is None:
            prior = dict(row)
            prior["evidence"] = dedupe_evidence(as_list(row.get("evidence")))
            by_key[key] = prior
            continue
        prior["evidence"] = dedupe_evidence(
            [*as_list(prior.get("evidence")), *as_list(row.get("evidence"))]
        )
        prior["identity_pending_parent_labels"] = unique_text(
            [
                *as_list(prior.get("identity_pending_parent_labels")),
                *as_list(row.get("identity_pending_parent_labels")),
            ]
        )
    return list(by_key.values())


__all__ = ["source_narrative_object_declarations"]
