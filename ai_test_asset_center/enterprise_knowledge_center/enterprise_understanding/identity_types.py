"""Schemas and source mention collection for enterprise identity resolution."""
from __future__ import annotations

import re
from typing import Any, Iterable

from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    source_evidence,
    stable_id,
    text,
    unique_text,
)

IDENTITY_MENTION_SCHEMA = "qualibug.enterprise-identity-mention.v1"
IDENTITY_EDGE_SCHEMA = "qualibug.enterprise-identity-evidence-edge.v1"
IDENTITY_CLUSTER_SCHEMA = "qualibug.enterprise-identity-cluster.v1"
IDENTITY_BINDING_SCHEMA = "qualibug.enterprise-identity-binding.v1"
IDENTITY_REGISTRY_SCHEMA = "qualibug.enterprise-identity-registry.v1"
IDENTITY_GATE_SCHEMA = "qualibug.enterprise-identity-resolution-gate.v1"
IDENTITY_RESULT_SCHEMA = "qualibug.enterprise-identity-resolution.v1"
HARD_IDENTITY_CLASSES = frozenset({"EXPLICIT_ALIAS", "EXPLICIT_ABBREVIATION", "RENAMING"})


def identity_scope(row: dict[str, Any]) -> dict[str, str]:
    raw = as_dict(row.get("scope"))
    return {
        "system": text(raw.get("system") or row.get("system") or row.get("system_id")),
        "module": text(raw.get("module") or row.get("module") or row.get("module_id")),
        "version": text(raw.get("version") or row.get("version") or row.get("version_id")),
    }


def asset_evidence(row: dict[str, Any], ref: str, derivation: str) -> list[dict[str, Any]]:
    raw_rows = [value for value in as_list(row.get("evidence")) if isinstance(value, dict)]
    if raw_rows:
        return dedupe_evidence(
            source_evidence(
                source_id=value.get("source_id") or row.get("source_id") or "asset",
                source_locator=value.get("source_locator") or value.get("locator") or ref,
                quote=value.get("quote") or value.get("verbatim_quote") or row.get("statement"),
                quote_hash=value.get("quote_hash"),
                asset_ref=ref,
                derivation=derivation,
            )
            for value in raw_rows
        )
    return [
        source_evidence(
            source_id=row.get("source_id") or "asset",
            source_locator=row.get("source_locator") or ref,
            quote=row.get("statement") or row.get("source_excerpt") or row.get("description"),
            asset_ref=ref,
            derivation=derivation,
        )
    ]


def fact_mentions(fact: dict[str, Any], side: str) -> list[str]:
    slot = as_dict(fact.get(side))
    values = [*as_list(slot.get("entity_mentions")), *as_list(slot.get("entity_refs"))]
    for resolution in as_list(slot.get("resolution_evidence")):
        if isinstance(resolution, dict):
            values.extend([resolution.get("mention"), resolution.get("resolved_ref")])
    return unique_text(values)


def annotate_fact_identity_mentions(asset: dict[str, Any]) -> dict[str, Any]:
    for fact in as_list(as_dict(asset.get("business_fact_ledger")).get("items")):
        if not isinstance(fact, dict) or text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            continue
        for side in ("subject", "object"):
            slot = dict(as_dict(fact.get(side)))
            slot["entity_mentions"] = fact_mentions(fact, side)
            slot.setdefault("resolved_entity_refs", [])
            fact[side] = slot
    return asset


def _mention(
    label: Any,
    *,
    mention_type: str,
    source_kind: str,
    source_id: Any,
    locator: Any,
    evidence: Iterable[dict[str, Any]],
    scope: dict[str, str] | None = None,
    role: str = "",
    artifact_type: str = "",
    artifact_ref: str = "",
) -> dict[str, Any] | None:
    label = text(label)
    if not label:
        return None
    source_id = text(source_id) or "asset"
    locator = text(locator) or artifact_ref or source_kind
    return {
        "schema": IDENTITY_MENTION_SCHEMA,
        "mention_id": stable_id(
            "identity_mention", source_id, locator, mention_type, artifact_type, role, label
        ),
        "raw_label": label,
        "comparison_keys": [re.sub(r"\s+", "", label).casefold()],
        "mention_type": mention_type,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_locator": locator,
        "scope": dict(scope or {}),
        "role": role,
        "artifact_type": artifact_type,
        "artifact_ref": artifact_ref,
        "evidence": dedupe_evidence(evidence),
    }


def collect_identity_mentions(
    asset: dict[str, Any], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []

    def add(value: dict[str, Any] | None) -> None:
        if value:
            mentions.append(value)

    for index, raw in enumerate(as_list(asset.get("business_objects"))):
        if not isinstance(raw, dict):
            continue
        ref = text(raw.get("object_id")) or f"business_objects[{index}]"
        evidence = asset_evidence(raw, ref, "existing_business_object")
        add(
            _mention(
                raw.get("object") or raw.get("name"),
                mention_type="BUSINESS_OBJECT",
                source_kind="BUSINESS_OBJECT_ASSET",
                source_id=raw.get("source_id"),
                locator=raw.get("source_locator") or ref,
                evidence=evidence,
                scope=identity_scope(raw),
            )
        )
        for alias in as_list(raw.get("aliases")):
            add(
                _mention(
                    alias,
                    mention_type="BUSINESS_OBJECT",
                    source_kind="BUSINESS_OBJECT_ALIAS",
                    source_id=raw.get("source_id"),
                    locator=raw.get("source_locator") or ref,
                    evidence=evidence,
                    scope=identity_scope(raw),
                    role="alias",
                )
            )

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        spans = as_list(fact.get("source_spans"))
        span = as_dict(spans[0]) if spans else {}
        source_id = span.get("source_id") or fact.get("source_id") or "fact"
        locator = span.get("locator") or span.get("source_locator") or fact.get("fact_id")
        evidence = evidence_from_fact(fact)
        kind = text(fact.get("kind"))
        if kind in {"RULE", "STATE_TRANSITION"}:
            for side in ("subject", "object"):
                for label in fact_mentions(fact, side):
                    add(
                        _mention(
                            label,
                            mention_type="BUSINESS_OBJECT",
                            source_kind="BUSINESS_FACT",
                            source_id=source_id,
                            locator=locator,
                            evidence=evidence,
                            scope=identity_scope(fact),
                            role=side,
                        )
                    )
        elif kind == "TERM_ALIAS":
            for role, label in (
                ("canonical", fact.get("canonical_term")),
                ("alias", fact.get("alias")),
            ):
                add(
                    _mention(
                        label,
                        mention_type="BUSINESS_OBJECT",
                        source_kind="TERM_ALIAS",
                        source_id=source_id,
                        locator=locator,
                        evidence=evidence,
                        scope=identity_scope(fact),
                        role=role,
                    )
                )

    for index, raw in enumerate(as_list(asset.get("data_tables"))):
        if not isinstance(raw, dict):
            continue
        ref = text(raw.get("table_id")) or f"data_tables[{index}]"
        add(
            _mention(
                raw.get("name") or raw.get("table"),
                mention_type="TECHNICAL_ARTIFACT",
                source_kind="DATA_TABLE",
                source_id=raw.get("source_id"),
                locator=raw.get("source_locator") or ref,
                evidence=asset_evidence(raw, ref, "source_backed_data_table"),
                scope=identity_scope(raw),
                artifact_type="DATABASE_TABLE",
                artifact_ref=ref,
            )
        )
    return list({text(row.get("mention_id")): row for row in mentions}.values())
