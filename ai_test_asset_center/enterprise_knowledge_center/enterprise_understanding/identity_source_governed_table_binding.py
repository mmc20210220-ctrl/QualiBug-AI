"""Project source-governed database-table bindings into Identity Authority."""
from __future__ import annotations

from typing import Any, Iterable

from .identity_unknown_reconciliation import (
    reconcile_resolved_technical_identity_unknowns,
)
from .identity_source_governed_table_evidence import (
    API_AUTHORITY,
    FIELD_AUTHORITY,
    collect_api_candidates,
    collect_field_candidates,
    table_rows,
)
from .identity_types import (
    IDENTITY_BINDING_SCHEMA,
    IDENTITY_EDGE_SCHEMA,
    identity_scope,
)
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

RECEIPT_SCHEMA = "qualibug.enterprise-identity-source-governed-table-binding.v1"


def _merge(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "matched_fields",
        "semantic_labels",
        "semantic_entity_ids",
        "rule_refs",
        "fact_refs",
        "interface_refs",
        "source_ids",
    )
    merged: dict[str, Any] = {key: [] for key in keys}
    merged.update({"authorities": [], "evidence": []})
    for row in rows:
        merged["authorities"] = unique_text(
            [*as_list(merged.get("authorities")), row.get("authority")]
        )
        for key in keys:
            merged[key] = unique_text(
                [*as_list(merged.get(key)), *as_list(row.get(key))]
            )
        merged["evidence"] = dedupe_evidence(
            [*as_list(merged.get("evidence")), *as_list(row.get("evidence"))]
        )
    return merged


def _conflict(
    table_ref: str,
    reason: str,
    entity_ids: list[str],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "conflict_id": stable_id(
            "enterprise_identity_table_binding_conflict",
            table_ref,
            reason,
            entity_ids,
        ),
        "kind": "SOURCE_GOVERNED_TABLE_BINDING_CONFLICT",
        "status": "UNRESOLVED",
        "artifact_ref": table_ref,
        "candidate_entity_ids": entity_ids,
        "authorities": unique_text(row.get("authority") for row in candidates),
        "reason_code": reason,
        "source_ids": unique_text(
            value
            for row in candidates
            for value in as_list(row.get("source_ids"))
        ),
        "semantic_labels": unique_text(
            value
            for row in candidates
            for value in as_list(row.get("semantic_labels"))
        ),
        "matched_fields": unique_text(
            value
            for row in candidates
            for value in as_list(row.get("matched_fields"))
        ),
        "evidence": dedupe_evidence(
            value
            for row in candidates
            for value in as_list(row.get("evidence"))
            if isinstance(value, dict)
        ),
        "blocks_formal_understanding": False,
        "blocks_technical_binding": True,
        "automatic_resolution_allowed": False,
    }


def project_source_governed_table_bindings(
    asset: dict[str, Any],
    result: dict[str, Any],
    *,
    rule_authority: dict[str, dict[str, Any]],
    mentions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    bound_artifacts: set[str],
    unknowns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tables = table_rows(asset)
    field_candidates = collect_field_candidates(
        asset, result, tables, rule_authority
    )
    api_candidates = collect_api_candidates(asset, result, tables, bindings)
    mention_by_artifact = {
        text(row.get("artifact_ref")): row
        for row in mentions
        if isinstance(row, dict)
        and text(row.get("mention_type")) == "TECHNICAL_ARTIFACT"
        and text(row.get("artifact_type")) == "DATABASE_TABLE"
    }
    admitted: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for table_ref, table in sorted(tables.items()):
        if table_ref in bound_artifacts:
            continue
        candidates = [
            *field_candidates.get(table_ref, []),
            *api_candidates.get(table_ref, []),
        ]
        entity_ids = unique_text(row.get("entity_id") for row in candidates)
        semantic_ids = unique_text(
            entity_id
            for row in candidates
            for entity_id in as_list(row.get("semantic_entity_ids"))
        )
        if not entity_ids:
            continue
        if len(entity_ids) != 1:
            conflicts.append(
                _conflict(
                    table_ref,
                    "SOURCE_GOVERNED_TABLE_BINDING_ENTITY_CONFLICT",
                    entity_ids,
                    candidates,
                )
            )
            continue
        if semantic_ids != entity_ids:
            conflicts.append(
                _conflict(
                    table_ref,
                    "SOURCE_GOVERNED_TABLE_SEMANTIC_AMBIGUITY",
                    unique_text([*entity_ids, *semantic_ids]),
                    candidates,
                )
            )
            continue

        entity_id = entity_ids[0]
        merged = _merge(
            row for row in candidates if text(row.get("entity_id")) == entity_id
        )
        evidence = dedupe_evidence(as_list(merged.get("evidence")))
        if not evidence:
            continue
        binding = {
            "schema": IDENTITY_BINDING_SCHEMA,
            "binding_id": stable_id(
                "identity_binding",
                entity_id,
                "DATABASE_TABLE",
                table_ref,
                "IMPLEMENTS_ENTITY",
            ),
            "entity_id": entity_id,
            "artifact_type": "DATABASE_TABLE",
            "artifact_ref": table_ref,
            "artifact_label": text(table.get("name") or table.get("table"))
            or table_ref,
            "relation": "IMPLEMENTS_ENTITY",
            "status": "RESOLVED",
            "identity_field_bindings": [],
            "identity_authorities": list(as_list(merged.get("authorities"))),
            "source_semantic_labels": list(as_list(merged.get("semantic_labels"))),
            "source_field_refs": list(as_list(merged.get("matched_fields"))),
            "source_rule_refs": list(as_list(merged.get("rule_refs"))),
            "source_fact_refs": list(as_list(merged.get("fact_refs"))),
            "source_interface_refs": list(as_list(merged.get("interface_refs"))),
            "source_ids": list(as_list(merged.get("source_ids"))),
            "evidence": evidence,
        }
        bindings.append(binding)
        mention = as_dict(mention_by_artifact.get(table_ref))
        if mention:
            evidence_keys = (
                "source_semantic_labels",
                "source_field_refs",
                "source_rule_refs",
                "source_fact_refs",
                "source_interface_refs",
                "source_ids",
                "evidence",
            )
            edges.append(
                {
                    "schema": IDENTITY_EDGE_SCHEMA,
                    "edge_id": stable_id(
                        "identity_edge",
                        entity_id,
                        mention.get("mention_id"),
                        "IMPLEMENTS_ENTITY",
                        binding["identity_authorities"],
                    ),
                    "entity_id": entity_id,
                    "right_mention_id": mention.get("mention_id"),
                    "relation": "IMPLEMENTS_ENTITY",
                    "evidence_class": "EXACT_SOURCE_GOVERNED_TECHNICAL_BRIDGE",
                    "authority": "+".join(binding["identity_authorities"]),
                    "status": "ACCEPTED",
                    "scope": identity_scope(table),
                    **{key: binding[key] for key in evidence_keys},
                    "automatic_union_allowed": False,
                }
            )
        bound_artifacts.add(table_ref)
        admitted.append(
            {
                "artifact_ref": table_ref,
                "entity_id": entity_id,
                "authorities": binding["identity_authorities"],
                "matched_fields": binding["source_field_refs"],
                "semantic_labels": binding["source_semantic_labels"],
                "rule_refs": binding["source_rule_refs"],
                "interface_refs": binding["source_interface_refs"],
                "source_ids": binding["source_ids"],
            }
        )

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": stable_id(
            "enterprise_identity_source_governed_table_binding",
            admitted,
            conflicts,
        ),
        "admitted_binding_count": len(admitted),
        "field_candidate_table_count": len(field_candidates),
        "api_candidate_table_count": len(api_candidates),
        "conflict_count": len(conflicts),
        "admitted_bindings": admitted,
        "conflicts": conflicts,
        "minimum_exclusive_field_count": 2,
        "field_ownership_requires_two_exclusive_fields": True,
        "api_resource_requires_exact_path_segment": True,
        "table_description_entity_corroboration_required": True,
        "table_description_unique_entity_required": True,
        "cross_source_independence_required": True,
        "token_overlap_authority_used": False,
        "name_similarity_authority_used": False,
        "automatic_entity_union_allowed": False,
    }
    result["source_governed_table_binding"] = receipt
    asset["enterprise_identity_source_governed_table_binding"] = receipt
    return reconcile_resolved_technical_identity_unknowns(unknowns, bound_artifacts)


def augment_source_governed_table_bindings(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Consume sealed technical output and reconcile table Unknowns only."""
    from .identity_technical_projection import _rule_entity_authority

    mentions = [
        dict(row)
        for row in as_list(result.get("mentions"))
        if isinstance(row, dict)
    ]
    edges = [
        dict(row) for row in as_list(result.get("edges")) if isinstance(row, dict)
    ]
    bindings = [
        dict(row)
        for row in as_list(result.get("bindings"))
        if isinstance(row, dict)
    ]
    unknowns = [
        dict(row)
        for row in as_list(result.get("unknowns"))
        if isinstance(row, dict)
    ]
    bound = {
        text(row.get("artifact_ref"))
        for row in bindings
        if text(row.get("artifact_ref"))
    }
    unknowns = project_source_governed_table_bindings(
        asset,
        result,
        rule_authority=_rule_entity_authority(asset),
        mentions=mentions,
        edges=edges,
        bindings=bindings,
        bound_artifacts=bound,
        unknowns=unknowns,
    )
    result["edges"] = list(
        {
            text(row.get("edge_id")): row
            for row in edges
            if text(row.get("edge_id"))
        }.values()
    )
    result["bindings"] = list(
        {
            text(row.get("binding_id")): row
            for row in bindings
            if text(row.get("binding_id"))
        }.values()
    )
    result["unknowns"] = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if text(row.get("unknown_id"))
        }.values()
    )
    gate = dict(as_dict(result.get("gate")))
    if as_list(result.get("conflicts")):
        gate.update(
            {
                "status": "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT",
                "entry_allowed": False,
            }
        )
    elif result["unknowns"]:
        gate.update(
            {
                "status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING",
                "entry_allowed": True,
            }
        )
    else:
        gate.update({"status": "PASS", "entry_allowed": True})
    receipt = as_dict(result.get("source_governed_table_binding"))
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "technical_binding_count": len(result["bindings"]),
            "technical_identity_unknown_count": len(result["unknowns"]),
            "unknown_count": len(result["unknowns"]),
            "source_governed_table_binding_count": int(
                receipt.get("admitted_binding_count") or 0
            ),
            "source_governed_table_binding_conflict_count": int(
                receipt.get("conflict_count") or 0
            ),
        }
    )
    gate["metrics"] = metrics
    result["gate"] = gate
    asset["enterprise_identity_resolution"] = result
    asset["enterprise_identity_gate"] = gate
    return result


__all__ = [
    "API_AUTHORITY",
    "FIELD_AUTHORITY",
    "RECEIPT_SCHEMA",
    "augment_source_governed_table_bindings",
    "project_source_governed_table_bindings",
]
