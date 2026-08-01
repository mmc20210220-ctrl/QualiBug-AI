"""Bind exact source-declared database semantics to existing business identities.

This module is a projection inside the single enterprise Identity Authority. It never
creates or merges business entities: a complete structured database label must already
resolve to exactly one current entity, and the technical table is then attached as a typed
binding with source evidence.
"""
from __future__ import annotations

from typing import Any

from .identity_types import (
    IDENTITY_BINDING_SCHEMA,
    IDENTITY_EDGE_SCHEMA,
    asset_evidence,
    identity_scope,
)
from .identity_unknown_reconciliation import (
    reconcile_resolved_technical_identity_unknowns,
)
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

_DATA_TABLE_SEMANTIC_FIELDS = (
    "business_label",
    "logical_name",
    "description",
    "comment",
    "summary",
)


def _exact_table_semantic_declarations(
    raw: dict[str, Any],
    *,
    artifact_ref: str,
    lookup: dict[str, str],
    known_entity_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Resolve exact structured table semantics without lexical inference."""
    declarations: dict[str, dict[str, Any]] = {}
    source_id = text(raw.get("source_id"))
    if not source_id or source_id == "asset":
        return declarations
    technical_label = text(raw.get("name") or raw.get("table"))
    for field in _DATA_TABLE_SEMANTIC_FIELDS:
        label = text(raw.get(field))
        if not label or label == technical_label:
            continue
        entity_id = lookup.get(label)
        if not entity_id or entity_id not in known_entity_ids:
            continue
        current = declarations.setdefault(
            entity_id,
            {
                "relation": "IMPLEMENTS_ENTITY",
                "authorities": [],
                "semantic_labels": [],
                "semantic_fields": [],
                "evidence": [],
            },
        )
        current["authorities"] = unique_text(
            [
                *as_list(current.get("authorities")),
                "SOURCE_DECLARED_TECHNICAL_SEMANTIC_LABEL",
            ]
        )
        current["semantic_labels"] = unique_text(
            [*as_list(current.get("semantic_labels")), label]
        )
        current["semantic_fields"] = unique_text(
            [*as_list(current.get("semantic_fields")), field]
        )
        current["evidence"] = dedupe_evidence(
            [
                *as_list(current.get("evidence")),
                {
                    "source_id": source_id,
                    "source_locator": text(raw.get("source_locator"))
                    or artifact_ref,
                    "quote": label,
                    "asset_ref": artifact_ref,
                    "derivation": "exact_source_declared_business_label",
                    "semantic_field": field,
                    "exact_complete_value_match": True,
                },
            ]
        )
    return declarations


def _reconcile_aggregate_technical_unknowns(
    unknowns: list[dict[str, Any]], bound_artifacts: set[str]
) -> list[dict[str, Any]]:
    """Compatibility wrapper around the single Unknown reconciliation authority."""
    return reconcile_resolved_technical_identity_unknowns(
        unknowns, bound_artifacts
    )


def project_exact_table_semantic_bindings(
    asset: dict[str, Any],
    result: dict[str, Any],
    *,
    lookup: dict[str, str],
    mentions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    bound_artifacts: set[str],
    unknowns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    known_entity_ids = {
        text(row.get("entity_id"))
        for row in as_list(result.get("clusters"))
        if isinstance(row, dict) and text(row.get("entity_id"))
    }
    mention_by_artifact = {
        text(row.get("artifact_ref")): row
        for row in mentions
        if isinstance(row, dict)
        and text(row.get("mention_type")) == "TECHNICAL_ARTIFACT"
        and text(row.get("artifact_type")) == "DATABASE_TABLE"
        and text(row.get("artifact_ref"))
    }
    for index, raw in enumerate(as_list(asset.get("data_tables"))):
        if not isinstance(raw, dict):
            continue
        artifact_ref = text(raw.get("table_id")) or f"data_tables[{index}]"
        if artifact_ref in bound_artifacts:
            continue
        declarations = _exact_table_semantic_declarations(
            raw,
            artifact_ref=artifact_ref,
            lookup=lookup,
            known_entity_ids=known_entity_ids,
        )
        if len(declarations) != 1:
            continue
        entity_id, authority_row = next(iter(declarations.items()))
        evidence = dedupe_evidence(
            [
                *asset_evidence(
                    raw, artifact_ref, "source_backed_database_table"
                ),
                *as_list(authority_row.get("evidence")),
            ]
        )
        binding_id = stable_id(
            "identity_binding",
            entity_id,
            "DATABASE_TABLE",
            artifact_ref,
            "IMPLEMENTS_ENTITY",
        )
        bindings.append(
            {
                "schema": IDENTITY_BINDING_SCHEMA,
                "binding_id": binding_id,
                "entity_id": entity_id,
                "artifact_type": "DATABASE_TABLE",
                "artifact_ref": artifact_ref,
                "artifact_label": text(raw.get("name") or raw.get("table"))
                or artifact_ref,
                "relation": "IMPLEMENTS_ENTITY",
                "status": "RESOLVED",
                "identity_field_bindings": [],
                "identity_authorities": list(
                    as_list(authority_row.get("authorities"))
                ),
                "source_semantic_labels": list(
                    as_list(authority_row.get("semantic_labels"))
                ),
                "source_semantic_fields": list(
                    as_list(authority_row.get("semantic_fields"))
                ),
                "evidence": evidence,
            }
        )
        mention = as_dict(mention_by_artifact.get(artifact_ref))
        if mention:
            edges.append(
                {
                    "schema": IDENTITY_EDGE_SCHEMA,
                    "edge_id": stable_id(
                        "identity_edge",
                        entity_id,
                        mention.get("mention_id"),
                        "IMPLEMENTS_ENTITY",
                        "EXACT_SOURCE_DECLARED_BUSINESS_LABEL",
                    ),
                    "entity_id": entity_id,
                    "right_mention_id": mention.get("mention_id"),
                    "relation": "IMPLEMENTS_ENTITY",
                    "evidence_class": "EXACT_SOURCE_DECLARED_BUSINESS_LABEL",
                    "authority": "SOURCE_DECLARED_TECHNICAL_SEMANTIC_LABEL",
                    "status": "ACCEPTED",
                    "scope": identity_scope(raw),
                    "source_semantic_labels": list(
                        as_list(authority_row.get("semantic_labels"))
                    ),
                    "source_semantic_fields": list(
                        as_list(authority_row.get("semantic_fields"))
                    ),
                    "evidence": evidence,
                    "automatic_union_allowed": False,
                }
            )
        bound_artifacts.add(artifact_ref)
    return reconcile_resolved_technical_identity_unknowns(
        unknowns, bound_artifacts
    )


__all__ = ["project_exact_table_semantic_bindings"]
