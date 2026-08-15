"""Single explicit composition root for enterprise knowledge construction.

The module owns one call graph:
base source asset -> OpenAPI schema facts -> API artifact projection -> exact operation-schema
binding -> database-model facts -> cross-source contract alignment -> operation-scoped storage
candidates -> durable table/field mapping authority -> root database observers -> exact FK relation
candidates -> durable relation authority -> child collection observers -> enterprise understanding
(source fact reconciliation -> implicit rule projection -> model) -> structure-first atomic fact compilation
-> second-pass identity/conflict governance -> downstream binding -> governed Jobs -> final Probe
admission -> source-occurrence evidence views -> one final persistence receipt.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from . import _api as _base_api
from . import _chinese_business_downstream as _downstream
from ._common import ROOT, _safe_project_id
from ._formal_ui_contract_guard import install_formal_ui_root_array_guard
from ._formal_ui_contracts import install_formal_ui_contract_parser
from ._ui_surface_declarations import install_ui_surface_declaration_parser
from ._formal_ui_persistent_probe_guard import install_formal_ui_persistent_probe_guard
from ._formal_ui_visual_baseline_guard import install_formal_ui_visual_baseline_guard
from ._formal_ui_visual_viewport_guard import install_formal_ui_visual_viewport_guard
from ._utils import _load_registry, _now, _paths, _save_registry
from .api_artifact_asset_projection import enrich_asset_with_api_artifact_semantics
from .api_database_contract_alignment import (
    enrich_asset_with_api_database_alignment_candidates,
)
from .api_operation_database_projection import (
    enrich_asset_with_api_operation_database_candidates,
)
from .api_operation_schema_binding import (
    enrich_asset_with_api_operation_schema_bindings,
)
from .database_mapping_authority import apply_database_mapping_authority_decisions
from .database_model_asset_projection import enrich_asset_with_database_model_facts
from .database_model_index_reconciliation import reconcile_database_model_index_assets
from .database_model_semantic_bridge import install_database_model_semantic_bridge
from .database_observer_contract_projection import (
    enrich_asset_with_database_observer_contracts,
)
from .database_relation_authority import apply_database_relation_authority_decisions
from .database_relation_observer_contract_projection import (
    enrich_asset_with_database_relation_observer_contracts,
)
from .database_relation_observer_projection import (
    enrich_asset_with_database_relation_observer_candidates,
)
from .database_table_source_alignment import (
    enrich_asset_with_database_table_alignment_candidates,
)
from ._chinese_business_comprehension import (
    analyze_chinese_business_source,
    apply_v1_extractor_frame_confirmation,
    close_state_guard_coordinates,
    synchronize_rule_library_from_facts,
)
from ._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
from ._document_ir_context import apply_document_ir_context
from ._document_ir_fact_evidence import align_business_facts_to_document_ir
from ._chinese_document_context import apply_chinese_document_context
from .enterprise_understanding.identity_benchmark_repository import (
    apply_identity_benchmark_repository,
)
from .enterprise_understanding.integration import (
    _parsed_sources_for_context,
    enrich_asset_with_enterprise_understanding,
)
from .enterprise_understanding.interface_runtime_contracts import (
    install_interface_runtime_contract_parser,
)
from .enterprise_understanding.post_compile_fact_governance import (
    govern_compiled_business_facts,
)
from .enterprise_understanding.probe_policy import (
    build_gated_probes,
    probe_generation_block_reason,
)
from .enterprise_understanding.semantic_lexicon_contract import (
    apply_semantic_lexicon_contract,
)
from .enterprise_understanding.structured_fact_compiler import (
    compile_structure_first_business_facts,
)
from .enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)
from .enterprise_understanding.chinese_context_envelope import (
    build_chinese_semantic_context_envelopes,
)
from .enterprise_understanding.chinese_clause_parser import (
    parse_chinese_clause_trees,
)
from .enterprise_understanding.chinese_semantic_frame_compiler import (
    enrich_frames_with_clause_structure,
)
from .enterprise_understanding.chinese_context_resolver import (
    resolve_chinese_semantic_context,
)
from .enterprise_understanding.chinese_semantic_grounding import (
    ground_semantic_frames,
)
from .job_asset_pipeline import enrich_job_assets_with_governance
from .job_behavior_projection import refresh_job_behavior_projection
from .openapi_schema_fact_asset_projection import enrich_asset_with_openapi_schema_facts
from .source_occurrence_projection import project_source_occurrence_assets


_DURABLE_IMPLICIT_RULE_GOVERNANCE_FIELDS = (
    "implicit_rule_lifecycle_ledger",
    "implicit_rule_authority_decision_ledger",
    "implicit_rule_runtime_evolution",
    "latest_implicit_rule_runtime_evolution",
)

_INCREMENTAL_SOURCE_EVENT_TYPES = {
    "SOURCE_CREATED",
    "SOURCE_REVISION_CHANGED",
    "SOURCE_REAPPEARED",
    "SOURCE_CAPABILITY_NOW_SUPPORTED",
}
_INCREMENTAL_VALIDATION_EVENT_TYPES = {
    "SOURCE_BECAME_UNAVAILABLE",
    "SOURCE_PERMISSION_CHANGED",
    "SOURCE_RETIRED",
}
_INCREMENTAL_SOURCE_FIELDS = {
    "interfaces": "operations",
    "data_tables": "tables",
    "field_dictionary": "field_dictionary",
    "ui_design_specs": "ui_specs",
    "permission_matrix": "permissions",
    "rule_library": "rules",
    "roles": "roles",
    "state_machines": "state_machines",
    "semantic_candidates": "semantic_candidates",
}
_INCREMENTAL_SOURCE_LIST_FIELDS = {
    "interfaces",
    "data_tables",
    "tables",
    "field_dictionary",
    "ui_design_specs",
    "permission_matrix",
    "rule_library",
    "roles",
    "state_machines",
    "semantic_candidates",
    "tickets",
    "openapi_schema_definitions",
    "openapi_schema_fields",
    "openapi_schema_references",
    "database_model_sources",
    "database_model_relationships",
    "database_model_indexes",
    "database_model_conflicts",
    "api_artifact_contract_conflicts",
}


def _probe_limit(value: Any, *, default: int = 140) -> int:
    """Resolve a Probe budget without treating an explicit zero as missing."""
    if value is None or value == "":
        return default
    return max(0, int(value))


_INCREMENTAL_PROVENANCE_KEYS = {
    "source_id",
    "source_ref",
    "canonical_source_id",
    "source_occurrence_id",
    "source_ids",
    "source_refs",
    "source_occurrence_ids",
}


def _incremental_text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _incremental_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _incremental_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _incremental_source_provenance(value: Any) -> set[str]:
    """Collect only explicit provenance identities, never arbitrary source prose."""
    found: set[str] = set()

    def visit(node: Any, *, provenance_key: bool = False) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                visit(child, provenance_key=key in _INCREMENTAL_PROVENANCE_KEYS)
            return
        if isinstance(node, list):
            for child in node:
                visit(child, provenance_key=provenance_key)
            return
        if provenance_key:
            identity = _incremental_text(node)
            if identity:
                found.add(identity)

    visit(value)
    return found


def _incremental_row_matches(
    row: Any,
    identities: set[str],
) -> bool:
    return bool(identities and _incremental_source_provenance(row).intersection(identities))


def _incremental_row_identity(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    for key in (
        "content_block_id",
        "document_block_id",
        "block_id",
        "chunk_id",
        "fact_id",
        "candidate_id",
        "entity_id",
        "business_object_id",
        "table_id",
        "field_id",
        "interface_id",
        "operation_id",
        "permission_id",
        "role_id",
        "state_machine_id",
        "behavior_id",
        "scenario_id",
        "scenario_ir_id",
        "job_asset_id",
        "probe_id",
        "regression_probe_id",
        "relationship_id",
        "edge_id",
    ):
        identity = _incremental_text(row.get(key), 500)
        if identity:
            return identity
    return ""


def _incremental_row_provenance(
    asset: dict[str, Any],
    row: Any,
) -> set[str]:
    """Resolve only explicit downstream references back to source identities."""
    found = _incremental_source_provenance(row)
    if not isinstance(row, dict):
        return found
    references: set[str] = set()

    def collect_references(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {
                    "interface_id",
                    "operation_id",
                    "operation_ref",
                    "behavior_id",
                    "behavior_ref",
                    "rule_id",
                    "rule_ref",
                    "risk_id",
                    "risk_ref",
                    "implementation_binding_ref",
                }:
                    identity = _incremental_text(value, 500)
                    if identity:
                        references.add(identity)
                elif isinstance(value, (dict, list)):
                    collect_references(value)
        elif isinstance(node, list):
            for value in node:
                collect_references(value)

    collect_references(row)
    candidates: list[dict[str, Any]] = []
    for field in (
        "interfaces",
        "rule_library",
        "risk_domains",
        "state_machines",
        "permission_matrix",
    ):
        candidates.extend(
            dict(item)
            for item in _incremental_list(asset.get(field))
            if isinstance(item, dict)
        )
    model = _incremental_dict(asset.get("enterprise_understanding_model"))
    for field in ("business_behaviors", "operations", "behavior_implementation_bindings"):
        candidates.extend(
            dict(item)
            for item in _incremental_list(model.get(field))
            if isinstance(item, dict)
        )
    for _ in range(2):
        matched = False
        for candidate in candidates:
            candidate_ids = {
                _incremental_text(candidate.get(key), 500)
                for key in (
                    "interface_id",
                    "operation_id",
                    "behavior_id",
                    "rule_id",
                    "risk_id",
                    "binding_id",
                    "implementation_binding_ref",
                )
                if _incremental_text(candidate.get(key), 500)
            }
            if not candidate_ids.intersection(references):
                continue
            before = len(found)
            found.update(_incremental_source_provenance(candidate))
            for key in (
                "interface_id",
                "operation_id",
                "behavior_id",
                "rule_id",
                "risk_id",
            ):
                identity = _incremental_text(candidate.get(key), 500)
                if identity:
                    references.add(identity)
            matched = matched or len(found) != before
        if not matched:
            break
    return found


def _incremental_impact_rows(
    asset: dict[str, Any],
    *,
    identities: set[str],
    probes: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Collect source-bound downstream rows without inventing semantic joins."""
    fields: dict[str, tuple[tuple[str, ...], ...]] = {
        "fact": (
            ("business_fact_ledger", "items"),
            ("rule_library",),
            ("semantic_candidates",),
        ),
        "entity": (
            ("business_objects",),
            ("data_tables",),
            ("field_dictionary",),
        ),
        "conflict": (
            ("cross_document_conflicts",),
            ("database_model_conflicts",),
            ("api_artifact_contract_conflicts",),
        ),
        "behavior": (
            ("interfaces",),
            ("permission_matrix",),
            ("state_machines",),
        ),
        "scenario": (
            ("scenario_ir",),
            ("scenario_execution_contracts",),
            ("job_assets",),
        ),
    }
    collected: dict[str, list[dict[str, Any]]] = {
        key: [] for key in (*fields, "regression")
    }
    for bucket, paths in fields.items():
        seen: set[str] = set()
        for path in paths:
            value: Any = asset
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            for row in _incremental_list(value):
                if not isinstance(row, dict) or not _incremental_row_provenance(
                    asset, row
                ).intersection(identities):
                    continue
                identity = _incremental_row_identity(row)
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                collected[bucket].append(dict(row))
    for row in probes or []:
        if not isinstance(row, dict) or not _incremental_row_provenance(
            asset, row
        ).intersection(identities):
            continue
        identity = _incremental_row_identity(row)
        if identity and identity not in {
            _incremental_row_identity(item) for item in collected["regression"]
        }:
            collected["regression"].append(dict(row))
    return collected


def _incremental_impact_projection(
    asset: dict[str, Any],
    *,
    identities: set[str],
    source_contexts: list[dict[str, Any]],
    probes: list[dict[str, Any]] | None = None,
    refresh_receipt: dict[str, Any],
) -> dict[str, Any]:
    rows = _incremental_impact_rows(asset, identities=identities, probes=probes)
    relations: list[dict[str, Any]] = []
    for bucket, bucket_rows in rows.items():
        relation = f"incremental_source_to_{bucket}"
        for row in bucket_rows:
            target_id = _incremental_row_identity(row)
            if not target_id:
                continue
            matched = sorted(
                _incremental_row_provenance(asset, row).intersection(identities)
            )
            for source_identity in matched:
                relations.append(
                    {
                        "edge_id": "edge:"
                        + _base_api._short_hash(
                            {
                                "source": source_identity,
                                "target": target_id,
                                "relation": relation,
                            }
                        ),
                        "from": f"source:{source_identity}",
                        "to": target_id,
                        "relation": relation,
                        "status": "affected",
                        "derivation": "connector_incremental_semantic_refresh",
                        "evidence": {
                            "sync_epoch_id": refresh_receipt.get("sync_epoch_id"),
                            "source_identity": source_identity,
                        },
                    }
                )

    content_block_count = 0
    content_block_relations: list[dict[str, Any]] = []
    for source in source_contexts:
        source_id = _incremental_text(source.get("source_id"), 300)
        structure = _incremental_dict(source.get("document_structure"))
        blocks = [
            row
            for row in _incremental_list(structure.get("blocks"))
            if isinstance(row, dict)
        ]
        content_block_count += len(blocks)
        for block in blocks:
            target_id = _incremental_row_identity(block)
            if not source_id or not target_id:
                continue
            content_block_relations.append(
                {
                    "edge_id": "edge:"
                    + _base_api._short_hash(
                        {
                            "source": source_id,
                            "target": target_id,
                            "relation": "incremental_source_to_content_block",
                        }
                    ),
                    "from": f"source:{source_id}",
                    "to": target_id,
                    "relation": "incremental_source_to_content_block",
                    "status": "affected",
                    "derivation": "connector_incremental_artifact_diff",
                    "evidence": {"sync_epoch_id": refresh_receipt.get("sync_epoch_id")},
                }
            )
    relations.extend(content_block_relations)
    relations = _base_api._dedupe_by_id(relations, "edge_id")
    counts = {
        "content_block": content_block_count,
        "fact": len(rows["fact"]),
        "entity": len(rows["entity"]),
        "conflict": len(rows["conflict"]),
        "behavior": len(rows["behavior"]),
        "scenario": len(rows["scenario"]),
        "regression": len(rows["regression"]),
    }
    return {
        "schema": "qualibug.connector-semantic-impact.v1",
        "status": "PASS",
        "sync_epoch_id": refresh_receipt.get("sync_epoch_id"),
        "source_identity_count": len(identities),
        "affected_counts": counts,
        "relation_count": len(relations),
        "relations": relations,
        "source_scoped_reextraction": True,
        "unchanged_materials_reanalyzed": False,
        "full_project_recompute_requested": False,
    }


def _incremental_replace_rows(
    rows: Any,
    *,
    identities: set[str],
    replacements: list[dict[str, Any]],
    identity_field: str,
) -> list[dict[str, Any]]:
    retained = [
        dict(row)
        for row in _incremental_list(rows)
        if isinstance(row, dict) and not _incremental_row_matches(row, identities)
    ]
    combined = [*retained, *[dict(row) for row in replacements if isinstance(row, dict)]]
    result: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for row in combined:
        identity = _incremental_text(row.get(identity_field), 500)
        if identity and identity in positions:
            result[positions[identity]] = row
        else:
            if identity:
                positions[identity] = len(result)
            result.append(row)
    return result


def _incremental_source_ref(source: dict[str, Any]) -> str:
    refs = source.get("source_refs")
    first_ref = refs[0] if isinstance(refs, list) and refs else ""
    return _incremental_text(
        source.get("external_ref") or source.get("source_ref") or first_ref,
        2000,
    )


def _incremental_active_sources(project: str, root: Path) -> list[dict[str, Any]]:
    from .source_occurrence_lifecycle import list_enterprise_knowledge_sources

    inventory = list_enterprise_knowledge_sources(
        project,
        root=root,
        include_deleted=False,
    )
    active: list[dict[str, Any]] = []
    for raw in inventory.get("sources") or []:
        if not isinstance(raw, dict) or raw.get("status") != "active":
            continue
        row = dict(raw)
        canonical_id = _incremental_text(row.get("canonical_source_id"), 300)
        if canonical_id:
            row["source_id"] = canonical_id
        source_ref = _incremental_source_ref(row)
        if source_ref:
            row["external_ref"] = source_ref
        active.append(row)
    return active


def _incremental_event_rows(
    refresh_receipt: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    source_diff = _incremental_dict(refresh_receipt.get("source_occurrence_diff"))
    events = [
        dict(row)
        for row in source_diff.get("events") or []
        if isinstance(row, dict) and _incremental_text(row.get("source_ref"), 2000)
    ]
    by_ref: dict[str, set[str]] = {}
    for event in events:
        by_ref.setdefault(_incremental_text(event.get("source_ref"), 2000), set()).add(
            _incremental_text(event.get("event"), 120)
        )
    return events, by_ref


def _incremental_source_context(
    source: dict[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse exactly one changed source through the existing document authorities."""
    from ._crud import _record_parse

    parsed = _record_parse(source, root)
    if _incremental_text(parsed.get("parse_status"), 80) == "failed":
        raise RuntimeError(
            "incremental_source_parse_failed:"
            + _incremental_text(source.get("source_id"), 300)
        )
    source_contexts = _parsed_sources_for_context(
        {"source_inventory": [dict(source)]},
        root,
        parsed_overrides={
            _incremental_text(source.get("source_id"), 300): dict(parsed)
        },
    )
    if len(source_contexts) != 1:
        raise RuntimeError(
            "incremental_source_context_missing:"
            + _incremental_text(source.get("source_id"), 300)
        )
    return parsed, dict(source_contexts[0])


def _incremental_merge_source_structures(
    asset: dict[str, Any],
    source_contexts: list[dict[str, Any]],
    *,
    identities: set[str],
) -> None:
    structure_assets = _incremental_dict(asset.get("document_structure_assets"))
    items = [
        dict(row)
        for row in _incremental_list(structure_assets.get("items"))
        if isinstance(row, dict) and not _incremental_row_matches(row, identities)
    ]
    for source in source_contexts:
        structure = _incremental_dict(source.get("document_structure"))
        if not structure:
            continue
        items.append(
            {
                "source_id": source.get("source_id"),
                "filename": source.get("filename"),
                **structure,
            }
        )
    structure_assets["items"] = items
    structure_assets["source_count"] = len(items)
    structure_assets["block_count"] = sum(
        len(_incremental_list(row.get("blocks"))) for row in items
    )
    structure_assets["page_count"] = sum(
        int(_incremental_dict(row.get("structure_receipt")).get("page_count") or 0)
        for row in items
    )
    structure_assets["scanned_page_count"] = sum(
        int(
            _incremental_dict(row.get("structure_receipt")).get(
                "scanned_page_count"
            )
            or 0
        )
        for row in items
    )
    structure_assets["image_count"] = sum(
        int(_incremental_dict(row.get("structure_receipt")).get("image_count") or 0)
        for row in items
    )
    structure_assets["unsupported_content_count"] = sum(
        int(
            _incremental_dict(row.get("structure_receipt")).get(
                "unsupported_content_count"
            )
            or 0
        )
        for row in items
    )
    structure_assets["adapter_execution_count"] = sum(
        len(_incremental_list(row.get("adapter_receipts"))) for row in items
    )
    structure_assets["adapter_names"] = sorted(
        {
            _incremental_text(receipt.get("adapter_name"), 200)
            for row in items
            for receipt in _incremental_list(row.get("adapter_receipts"))
            if isinstance(receipt, dict)
            and _incremental_text(receipt.get("adapter_name"), 200)
        }
    )
    asset["document_structure_assets"] = structure_assets


def _incremental_merge_source_rows(
    asset: dict[str, Any],
    parsed_rows: list[dict[str, Any]],
    source_contexts: list[dict[str, Any]],
    *,
    identities: set[str],
    preserved_interface_ids: set[str] | None = None,
) -> None:
    for target_field, parsed_field in _INCREMENTAL_SOURCE_FIELDS.items():
        replacements = [
            dict(row)
            for parsed in parsed_rows
            for row in _incremental_list(parsed.get(parsed_field))
            if isinstance(row, dict)
        ]
        if target_field == "interfaces" and preserved_interface_ids:
            replacements = [
                row
                for row in replacements
                if _incremental_text(row.get("interface_id"), 500)
                not in preserved_interface_ids
            ]
        current = asset.get(target_field)
        asset[target_field] = _incremental_replace_rows(
            current,
            identities=identities,
            replacements=replacements,
            identity_field={
                "interfaces": "interface_id",
                "data_tables": "table_id",
                "field_dictionary": "field_id",
                "ui_design_specs": "ui_spec_id",
                "permission_matrix": "permission_id",
                "rule_library": "rule_id",
                "roles": "role_id",
                "state_machines": "state_machine_id",
                "semantic_candidates": "candidate_id",
            }[target_field],
        )

    table_rows = [
        dict(row)
        for row in _incremental_list(asset.get("data_tables"))
        if isinstance(row, dict)
    ]
    asset["tables"] = [dict(row) for row in table_rows]
    asset["data_tables"] = [dict(row) for row in table_rows]
    asset["data_fields"] = [
        {
            "table_id": row.get("table_id"),
            "table": row.get("name"),
            "fields": list(row.get("columns") or []),
            "source_id": row.get("source_id"),
        }
        for row in table_rows
    ]
    for source in source_contexts:
        source_id = _incremental_text(source.get("source_id"), 300)
        if not source_id:
            continue
        if any(
            _incremental_text(row.get("source_id"), 300) == source_id
            for row in asset.get("business_objects") or []
            if isinstance(row, dict)
        ):
            continue
        for table in table_rows:
            if _incremental_text(table.get("source_id"), 300) != source_id:
                continue
            name = _incremental_text(table.get("name"), 300)
            if not name:
                continue
            asset.setdefault("business_objects", []).append(
                {
                    "object": name,
                    "source": "database_schema",
                    "source_id": source_id,
                    "evidence": [{"source_id": source_id, "table_id": table.get("table_id")}],
                    "confidence": 0.62,
                }
            )
    _incremental_merge_source_structures(
        asset, source_contexts, identities=identities
    )


def _incremental_preserve_shared_api_artifacts(
    asset: dict[str, Any],
    *,
    identities: set[str],
) -> set[str]:
    """Keep unchanged source records when one logical API is multi-sourced."""
    from .api_artifact_asset_projection import _apply_record_ledger

    preserved: set[str] = set()
    for interface in _incremental_list(asset.get("interfaces")):
        if not isinstance(interface, dict):
            continue
        records = [
            dict(row)
            for row in _incremental_list(interface.get("api_artifact_source_records"))
            if isinstance(row, dict)
        ]
        retained = [
            row
            for row in records
            if not _incremental_row_matches(row, identities)
        ]
        if not retained or len(retained) == len(records):
            continue
        base = {
            "interface_id": interface.get("interface_id"),
            "method": interface.get("method"),
            "path": interface.get("path"),
        }
        rebuilt = _apply_record_ledger(base, retained)
        interface.clear()
        interface.update(rebuilt)
        interface["source_scoped_reconciliation"] = True
        interface["unchanged_source_records_preserved"] = True
        identity = _incremental_text(interface.get("interface_id"), 500)
        if identity:
            preserved.add(identity)
    return preserved


def _incremental_merge_comprehension(
    asset: dict[str, Any],
    source_contexts: list[dict[str, Any]],
    *,
    identities: set[str],
) -> dict[str, int]:
    coverage_rows: list[dict[str, Any]] = []
    fact_rows: list[dict[str, Any]] = []
    glossary_rows: list[dict[str, Any]] = []
    for source in source_contexts:
        coverage, facts, glossary = analyze_chinese_business_source(
            source, asset=asset
        )
        close_state_guard_coordinates(facts)
        coverage_rows.extend(dict(row) for row in coverage if isinstance(row, dict))
        fact_rows.extend(dict(row) for row in facts if isinstance(row, dict))
        glossary_rows.extend(dict(row) for row in glossary if isinstance(row, dict))

    coverage_ledger = _incremental_dict(asset.get("document_coverage_ledger"))
    coverage_ledger["items"] = _incremental_replace_rows(
        coverage_ledger.get("items"),
        identities=identities,
        replacements=coverage_rows,
        identity_field="chunk_id",
    )
    asset["document_coverage_ledger"] = coverage_ledger

    fact_ledger = _incremental_dict(asset.get("business_fact_ledger"))
    merged_facts = _incremental_replace_rows(
        fact_ledger.get("items"),
        identities=identities,
        replacements=[*fact_rows, *glossary_rows],
        identity_field="fact_id",
    )
    fact_ledger["items"] = merged_facts
    asset["business_fact_ledger"] = fact_ledger

    glossary = _incremental_dict(asset.get("chinese_business_glossary"))
    glossary["items"] = _incremental_replace_rows(
        glossary.get("items"),
        identities=identities,
        replacements=glossary_rows,
        identity_field="fact_id",
    )
    asset["chinese_business_glossary"] = glossary
    synchronize_rule_library_from_facts(asset, merged_facts)
    asset = align_business_facts_to_document_ir(asset, source_contexts)
    asset = apply_document_ir_context(asset, source_contexts)
    asset = apply_chinese_document_context(asset, source_contexts)
    asset = reconcile_chinese_business_fact_conflicts(asset)

    coverage = [
        row
        for row in _incremental_list(asset.get("document_coverage_ledger", {}).get("items"))
        if isinstance(row, dict)
    ]
    facts = [
        row
        for row in _incremental_list(asset.get("business_fact_ledger", {}).get("items"))
        if isinstance(row, dict)
    ]
    critical = [
        row
        for row in coverage
        if _incremental_text(row.get("status"), 80) == "UNRESOLVED_BUSINESS_TEXT"
        and row.get("contains_business_signal")
    ]
    comprehension_gate = _incremental_dict(asset.get("enterprise_comprehension_gate"))
    comprehension_gate.update(
        {
            "status": (
                "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE"
                if critical
                else "PASS"
            ),
            "entry_allowed": not bool(critical),
            "critical_unknowns": critical,
            "metrics": {
                **_incremental_dict(comprehension_gate.get("metrics")),
                "chunk_count": len(coverage),
                "accepted_fact_count": sum(
                    _incremental_text(row.get("status"), 40) == "ACCEPTED"
                    for row in facts
                ),
                "pending_fact_count": sum(
                    _incremental_text(row.get("status"), 40) == "PENDING"
                    for row in facts
                ),
                "critical_ambiguity_count": len(critical),
            },
        }
    )
    asset["enterprise_comprehension_gate"] = comprehension_gate
    summary = _incremental_dict(asset.get("summary"))
    summary.update(
        {
            "chinese_business_fact_count": len(facts),
            "chinese_business_fact_accepted": sum(
                _incremental_text(row.get("status"), 40) == "ACCEPTED"
                for row in facts
            ),
            "chinese_business_fact_pending": sum(
                _incremental_text(row.get("status"), 40) == "PENDING"
                for row in facts
            ),
            "chinese_business_chunk_count": sum(
                _incremental_text(row.get("language"), 40)
                in {"zh-CN", "zh-CN-mixed"}
                for row in coverage
            ),
            "business_comprehension_status": comprehension_gate["status"],
            "business_comprehension_ready": bool(comprehension_gate["entry_allowed"]),
        }
    )
    asset["summary"] = summary
    return {
        "coverage_count": len(coverage_rows),
        "fact_count": len(fact_rows),
        "glossary_count": len(glossary_rows),
    }


def _incremental_mark_pending_validation(
    asset: dict[str, Any],
    *,
    identities: set[str],
    event: str,
) -> int:
    marked = 0
    for field in (
        "interfaces",
        "data_tables",
        "tables",
        "field_dictionary",
        "ui_design_specs",
        "permission_matrix",
        "rule_library",
        "roles",
        "state_machines",
        "business_objects",
        "relationships",
        "entity_relations",
        "semantic_candidates",
        "scenario_ir",
        "scenario_execution_contracts",
        "job_assets",
        "runtime_plans",
    ):
        rows = asset.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not _incremental_row_provenance(
                asset, row
            ).intersection(identities):
                continue
            row["semantic_validation_status"] = "PENDING_SOURCE_VALIDATION"
            row["semantic_validation_event"] = event
            row["semantic_validation_required"] = True
            marked += 1
    for ledger_name in (
        "document_coverage_ledger",
        "business_fact_ledger",
        "chinese_business_glossary",
    ):
        ledger = asset.get(ledger_name)
        if not isinstance(ledger, dict):
            continue
        for row in ledger.get("items") or []:
            if not isinstance(row, dict) or not _incremental_row_provenance(
                asset, row
            ).intersection(identities):
                continue
            row["semantic_validation_status"] = "PENDING_SOURCE_VALIDATION"
            row["semantic_validation_event"] = event
            row["semantic_validation_required"] = True
            marked += 1
    return marked


def _incremental_mark_probe_validation(
    asset: dict[str, Any],
    probes: list[dict[str, Any]],
    *,
    identities: set[str],
    event: str,
) -> int:
    marked = 0
    for probe in probes:
        if not isinstance(probe, dict) or not _incremental_row_provenance(
            asset, probe
        ).intersection(identities):
            continue
        probe["semantic_validation_status"] = "PENDING_SOURCE_VALIDATION"
        probe["semantic_validation_event"] = event
        probe["semantic_validation_required"] = True
        probe["execution_allowed"] = False
        probe["regression_scope_status"] = "INVALIDATED_SOURCE_VALIDATION"
        marked += 1
    return marked


def _incremental_purge_derived_source_rows(
    asset: dict[str, Any],
    *,
    identities: set[str],
) -> None:
    for field in _INCREMENTAL_SOURCE_LIST_FIELDS:
        if field in _INCREMENTAL_SOURCE_FIELDS:
            continue
        rows = asset.get(field)
        if isinstance(rows, list):
            asset[field] = [
                dict(row)
                for row in rows
                if isinstance(row, dict)
                and not _incremental_row_matches(row, identities)
            ]


def _incremental_recompute_relations(asset: dict[str, Any]) -> None:
    interfaces = [
        dict(row) for row in asset.get("interfaces") or [] if isinstance(row, dict)
    ]
    tables = [
        dict(row)
        for row in (asset.get("tables") or asset.get("data_tables") or [])
        if isinstance(row, dict)
    ]
    fields = [
        dict(row)
        for row in asset.get("field_dictionary") or []
        if isinstance(row, dict)
    ]
    rules = [
        dict(row) for row in asset.get("rule_library") or [] if isinstance(row, dict)
    ]
    states = [
        dict(row)
        for row in asset.get("state_machines") or []
        if isinstance(row, dict)
    ]
    permissions = [
        dict(row)
        for row in asset.get("permission_matrix") or []
        if isinstance(row, dict)
    ]
    relationships = _base_api._extract_entity_relations(
        interfaces, tables, fields, rules, states, permissions
    )
    for source in asset.get("source_inventory") or []:
        if not isinstance(source, dict):
            continue
        source_id = _incremental_text(source.get("source_id"), 300)
        if not source_id:
            continue
        for row in [*interfaces, *tables, *fields, *rules, *states, *permissions]:
            if _incremental_text(row.get("source_id"), 300) != source_id:
                continue
            node_id = next(
                (
                    row.get(key)
                    for key in (
                        "rule_id",
                        "interface_id",
                        "table_id",
                        "field_id",
                        "permission_id",
                        "state_machine_id",
                    )
                    if row.get(key)
                ),
                "",
            )
            if node_id:
                relationships.append(
                    {
                        "edge_id": "edge:"
                        + _base_api._short_hash(
                            {"source": source_id, "node": node_id}
                        ),
                        "from": f"source:{source_id}",
                        "to": node_id,
                        "relation": "source_to_asset",
                        "confidence": 1.0,
                        "evidence": {"source_version": source.get("version")},
                    }
                )
    asset["entity_relations"] = relationships
    asset["relationships"] = _base_api._dedupe_by_id(relationships, "edge_id")
    asset["cross_document_conflicts"] = _base_api._detect_cross_document_conflicts(
        fields, rules, interfaces, permissions
    )


def _incremental_run_semantic_extraction(
    parsed_rows: list[dict[str, Any]],
    *,
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    requested = bool(
        options.get("enable_semantic_extraction")
        or os.getenv("QUALIBUG_SEMANTIC_EXTRACTION", "").strip()
        in {"1", "true", "yes"}
    )
    from ._semantic_extraction import (
        provider_status,
        resolve_semantic_rule_extraction_mode,
        run_semantic_extraction_batch,
        semantic_extraction_availability,
    )

    # SPEC §12/§13: four-mode rule extraction. Default augment — validated
    # explicit LLM-only rule candidates are promoted into formal Canonical Rule
    # output through the deterministic promotion gate (promote_rule_candidates_to_rules
    # + rule_promotion_gates_met): only llm+explicit+non-conflicted candidates
    # carrying anchored evidence are promoted; nothing is promoted without
    # evidence. An operator explicit rule_promotion_gates_met=False is the kill
    # switch and resolves to shadow (promotion_gates_not_met). Degradation is
    # never silent.
    rule_mode_receipt = resolve_semantic_rule_extraction_mode(
        requested_mode=_incremental_text(
            options.get("semantic_rule_extraction_mode") or "augment"
        ),
        provider_status_value=provider_status(),
        governance_policy={
            "promotion_gates_met": options.get("rule_promotion_gates_met")
        },
    )
    should_run_llm = requested or rule_mode_receipt["effective_mode"] in {
        "shadow",
        "augment",
        "required",
    }
    availability = semantic_extraction_availability(requested=should_run_llm)
    if not should_run_llm or not availability.get("available"):
        # off / provider unavailable: formal output stays regex-only. The mode
        # receipt is still recorded — no silent degradation (SPEC §12.5).
        return (
            [],
            [rule_mode_receipt],
            _incremental_text(
                "NOT_TRIGGERED"
                if not should_run_llm
                else availability.get("reason"),
                160,
            ),
        )
    candidates: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    rule_mode_active = rule_mode_receipt["effective_mode"] in {
        "shadow",
        "augment",
        "required",
    }
    targets: list[tuple[dict[str, Any], str]] = []
    for parsed in parsed_rows:
        source_text = str(parsed.get("text") or "").strip()
        structured_count = sum(
            len(_incremental_list(parsed.get(key)))
            for key in ("tables", "field_dictionary", "permissions")
        )
        if not source_text:
            continue
        # The structured-output skip applies to the legacy 5-kind extraction
        # only. Rule extraction must still run on structured sources: tables /
        # field dictionaries never cover the textual business rules the regex
        # signal vocabulary may have missed.
        if structured_count and not rule_mode_active:
            continue
        targets.append((
            {
                "source_id": _incremental_text(parsed.get("source_id"), 300),
                "original_name": _incremental_text(
                    parsed.get("original_name"), 500
                ),
            },
            source_text,
        ))
    max_chunks = options.get("semantic_max_chunks_per_source")
    if max_chunks in (None, ""):
        max_chunks = None
    results, batch_receipt = run_semantic_extraction_batch(
        targets,
        max_chunks_per_source=max_chunks,
    )
    receipts.append(batch_receipt)
    for _, receipt in results:
        receipts.append(receipt.to_dict())
        candidates.extend(receipt.candidates_validated)
    receipts.append(rule_mode_receipt)
    return candidates, receipts, "AVAILABLE"


def _incremental_attach_promoted_rules(
    parsed_rows: list[dict[str, Any]],
    promoted_rows: list[dict[str, Any]],
) -> int:
    """Attach promoted rules to the changed-source rows before source replacement.

    Incremental source replacement treats ``parsed_rows`` as the changed source
    authority. Mutating ``asset['rule_library']`` before that replacement loses
    the promoted rows immediately, so promotion must join the same source row
    that will be merged.
    """
    promoted_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in promoted_rows:
        if not isinstance(row, dict):
            continue
        source_id = _incremental_text(row.get("source_id"), 300)
        rule_id = _incremental_text(row.get("rule_id"), 300)
        if source_id and rule_id:
            promoted_by_source.setdefault(source_id, []).append(dict(row))

    attached = 0
    for parsed in parsed_rows:
        source_id = _incremental_text(parsed.get("source_id"), 300)
        additions = promoted_by_source.get(source_id, [])
        if not additions:
            continue
        existing = [
            dict(row)
            for row in _incremental_list(parsed.get("rules"))
            if isinstance(row, dict)
        ]
        existing_ids = {
            _incremental_text(row.get("rule_id"), 300) for row in existing
        }
        accepted = [
            dict(row)
            for row in additions
            if _incremental_text(row.get("rule_id"), 300) not in existing_ids
        ]
        parsed["rules"] = [*existing, *accepted]
        attached += len(accepted)
    return attached


def _incremental_refresh_semantic_candidate_projection(
    asset: dict[str, Any],
) -> int:
    """Recompute the full semantic-candidate gate after changed rows are merged."""
    from ._candidate_validation import (
        candidates_to_behavior_ir_entries,
        validate_and_promote_candidates,
    )

    candidates = [
        dict(row)
        for row in _incremental_list(asset.get("semantic_candidates"))
        if isinstance(row, dict)
    ]
    receipt = validate_and_promote_candidates(
        candidates,
        interfaces=[
            dict(row)
            for row in _incremental_list(asset.get("interfaces"))
            if isinstance(row, dict)
        ],
        tables=[
            dict(row)
            for row in _incremental_list(asset.get("data_tables"))
            if isinstance(row, dict)
        ],
        rules=[
            dict(row)
            for row in _incremental_list(asset.get("rule_library"))
            if isinstance(row, dict)
        ],
        state_machines=[
            dict(row)
            for row in _incremental_list(asset.get("state_machines"))
            if isinstance(row, dict)
        ],
    )
    asset["candidate_validation_receipt"] = receipt.to_dict()
    projected = candidates_to_behavior_ir_entries(
        receipt.validated,
        receipt.pending,
    )
    objects = [
        dict(row)
        for row in _incremental_list(asset.get("business_objects"))
        if isinstance(row, dict)
        and _incremental_text(row.get("source"), 100)
        != "semantic_extraction_validated"
    ]
    object_names = {
        _incremental_text(row.get("object") or row.get("name"), 500)
        for row in objects
    }
    added = 0
    for row in projected:
        name = _incremental_text(row.get("object"), 500)
        if not name or name in object_names:
            continue
        objects.append(dict(row))
        object_names.add(name)
        added += 1
    asset["business_objects"] = objects
    return added


def _incremental_asset_identity(
    project: str,
    active_sources: list[dict[str, Any]],
) -> str:
    return "knowledge_asset:" + project + ":" + _base_api._short_hash(
        {
            "sources": [
                (
                    row.get("source_id"),
                    row.get("content_hash"),
                    row.get("version"),
                )
                for row in active_sources
            ]
        }
    )


def _incremental_load_probe_catalog(
    project: str,
    root: Path,
) -> list[dict[str, Any]]:
    catalog_path = _base_api._paths(project, root)["probe_catalog"]
    catalog = _base_api._load_json(catalog_path, {})
    return [
        dict(row)
        for row in _incremental_list(_incremental_dict(catalog).get("items"))
        if isinstance(row, dict)
    ]


def _incremental_update_summary(
    asset: dict[str, Any],
    *,
    project: str,
    active_sources: list[dict[str, Any]],
    refresh_receipt: dict[str, Any],
) -> None:
    summary = _incremental_dict(asset.get("summary"))
    summary.update(
        {
            "active_source_count": len(active_sources),
            "interface_count": len(asset.get("interfaces") or []),
            "data_table_count": len(asset.get("data_tables") or []),
            "field_dictionary_count": len(asset.get("field_dictionary") or []),
            "rule_count": len(asset.get("rule_library") or []),
            "permission_matrix_count": len(asset.get("permission_matrix") or []),
            "role_count": len(asset.get("roles") or []),
            "state_machine_count": len(asset.get("state_machines") or []),
            "relationship_count": len(asset.get("relationships") or []),
            "incremental_semantic_refresh_status": _incremental_text(
                refresh_receipt.get("status"), 100
            ),
            "incremental_semantic_refresh_epoch": _incremental_text(
                refresh_receipt.get("sync_epoch_id"), 200
            ),
        }
    )
    asset["summary"] = summary
    governance = _incremental_dict(asset.get("governance"))
    governance.update(
        {
            "connector_incremental_semantic_executor_installed": True,
            "connector_incremental_source_reextraction_is_source_scoped": True,
            "connector_incremental_unchanged_material_reanalysis_forbidden": True,
            "connector_incremental_full_source_rebuild_requested": False,
            "connector_incremental_downstream_reconciliation_is_deterministic": True,
            "connector_incremental_source_identity_authority": "source_occurrence_ref_and_content_hash",
            "connector_incremental_project": project,
        }
    )
    asset["governance"] = governance


def refresh_enterprise_business_knowledge_asset_incremental(
    project_id: str,
    refresh_receipt: dict[str, Any],
    *,
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one connector diff to the existing knowledge asset.

    Only source occurrences named by the receipt are parsed and semantically extracted. The
    existing deterministic merge, conflict, behavior and probe authorities remain the only
    downstream authorities; this function merely supplies a source-scoped input and persists
    their refreshed projection. Missing baselines or source material fail visibly.
    """
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    resolved_options = dict(options or {})
    receipt = copy.deepcopy(refresh_receipt or {})
    events, events_by_ref = _incremental_event_rows(receipt)
    active_sources = _incremental_active_sources(project, resolved_root)
    active_by_ref = {
        _incremental_source_ref(row): row
        for row in active_sources
        if _incremental_source_ref(row)
    }
    asset = _base_api.load_enterprise_business_knowledge_asset(project, resolved_root)
    if not events:
        if asset is None:
            raise RuntimeError("incremental_refresh_asset_missing_without_source_change")
        return {
            "asset": asset,
            "mode": "NO_CHANGE",
            "parsed_source_count": 0,
            "llm_reanalysis_count": 0,
            "pending_validation_count": 0,
            "stage_counts": {},
        }

    if asset is None:
        if not any(ref in active_by_ref for ref in events_by_ref):
            raise RuntimeError("incremental_refresh_initial_source_not_active")
        initial_options = {
            **resolved_options,
            "sync_declared_sources": False,
        }
        built = build_enterprise_business_knowledge_asset(
            project,
            resolved_root,
            initial_options,
        )
        initial_probes = _incremental_load_probe_catalog(project, resolved_root)
        initial_identities = set(events_by_ref)
        for source in active_sources:
            initial_identities.update(_incremental_source_provenance(source))
        initial_impact = _incremental_impact_projection(
            built,
            identities=initial_identities,
            source_contexts=[],
            probes=initial_probes,
            refresh_receipt=receipt,
        )
        structure_assets = _incremental_dict(built.get("document_structure_assets"))
        initial_impact["affected_counts"]["content_block"] = sum(
            len(_incremental_list(row.get("blocks")))
            for row in _incremental_list(structure_assets.get("items"))
            if isinstance(row, dict)
        )
        built["incremental_semantic_impact"] = initial_impact
        built["relationships"] = _base_api._dedupe_by_id(
            [
                *[
                    dict(row)
                    for row in built.get("relationships") or []
                    if isinstance(row, dict)
                ],
                *[
                    dict(row)
                    for row in initial_impact.get("relations") or []
                    if isinstance(row, dict)
                ],
            ],
            "edge_id",
        )
        initial_counts = dict(initial_impact.get("affected_counts") or {})
        built["incremental_refresh_receipt"] = {
            **copy.deepcopy(receipt),
            "status": "EXECUTED",
            "incremental_executor_installed": True,
            "completion_reason": "INITIAL_ASSET_BUILD_EXECUTED",
            "incremental_execution_mode": "INITIAL_ASSET_BUILD",
            "incremental_parsed_source_count": len(active_sources),
            "llm_reanalysis_scheduled_count": len(
                _incremental_list(built.get("semantic_extraction_receipts"))
            ),
            "affected_content_blocks": int(initial_counts.get("content_block") or 0),
            "affected_facts": int(initial_counts.get("fact") or 0),
            "affected_entities": int(initial_counts.get("entity") or 0),
            "affected_behaviors": int(initial_counts.get("behavior") or 0),
            "affected_scenarios": int(initial_counts.get("scenario") or 0),
            "affected_regression_items": int(initial_counts.get("regression") or 0),
            "semantic_impact_relation_count": int(
                initial_impact.get("relation_count") or 0
            ),
            "unchanged_materials_reanalyzed": False,
            "full_project_recompute_requested": True,
            "source_content_returned": False,
        }
        _persist_final(
            built,
            initial_probes,
            project_id=project,
            root=resolved_root,
        )
        return {
            "asset": built,
            "mode": "INITIAL_ASSET_BUILD",
            "parsed_source_count": len(active_sources),
            "llm_reanalysis_count": len(
                _incremental_list(built.get("semantic_extraction_receipts"))
            ),
            "pending_validation_count": 0,
            "affected_content_blocks": int(initial_counts.get("content_block") or 0),
            "semantic_impact_relation_count": int(
                initial_impact.get("relation_count") or 0
            ),
            "unchanged_materials_reanalyzed": False,
            "full_project_recompute_requested": True,
            "stage_counts": {
                "fact_reextraction": int(initial_counts.get("fact") or 0),
                "entity_remerge": int(initial_counts.get("entity") or 0),
                "conflict_recomputation": int(initial_counts.get("conflict") or 0),
                "behavior_model_impact_analysis": int(initial_counts.get("behavior") or 0),
                "scenario_regeneration_or_invalidation": int(initial_counts.get("scenario") or 0),
                "regression_scope_update": int(initial_counts.get("regression") or 0),
            },
        }

    prior_probes = _incremental_load_probe_catalog(project, resolved_root)
    prior_inventory = [
        dict(row)
        for row in asset.get("source_inventory") or []
        if isinstance(row, dict)
    ]
    old_ids_by_ref: dict[str, set[str]] = {}
    for source in prior_inventory:
        source_ref = _incremental_source_ref(source)
        if not source_ref:
            continue
        identities = {
            _incremental_text(source.get(key), 300)
            for key in (
                "source_id",
                "canonical_source_id",
                "source_occurrence_id",
            )
            if _incremental_text(source.get(key), 300)
        }
        if identities:
            old_ids_by_ref.setdefault(source_ref, set()).update(identities)

    content_refs = {
        ref
        for ref, kinds in events_by_ref.items()
        if kinds.intersection(_INCREMENTAL_SOURCE_EVENT_TYPES)
    }
    validation_refs = {
        ref
        for ref, kinds in events_by_ref.items()
        if kinds.intersection(_INCREMENTAL_VALIDATION_EVENT_TYPES)
    }
    all_identities = set(events_by_ref)
    for ref in events_by_ref:
        all_identities.update(old_ids_by_ref.get(ref, set()))
        current = active_by_ref.get(ref)
        if current is not None:
            all_identities.add(_incremental_text(current.get("source_id"), 300))

    parsed_rows: list[dict[str, Any]] = []
    source_contexts: list[dict[str, Any]] = []
    content_sources: list[dict[str, Any]] = []
    for source_ref in sorted(content_refs):
        source = active_by_ref.get(source_ref)
        if source is None:
            raise RuntimeError("incremental_source_manifest_missing:" + source_ref)
        parsed, context = _incremental_source_context(source, root=resolved_root)
        parsed_rows.append(parsed)
        source_contexts.append(context)
        content_sources.append(source)

    semantic_candidates, semantic_receipts, semantic_status = (
        _incremental_run_semantic_extraction(
            parsed_rows,
            options=resolved_options,
        )
    )
    for parsed in parsed_rows:
        parsed["semantic_candidates"] = [
            dict(row)
            for row in semantic_candidates
            if isinstance(row, dict)
            and _incremental_text(row.get("source_id"), 300)
            == _incremental_text(parsed.get("source_id"), 300)
        ]

    # ── Unified rule candidate ledger (SPEC §9/§10, P0-4) ──
    # Observation layer: regex rule facts and validated LLM rule candidates are
    # merged per source into ONE ledger (evidence de-dup + semantic-signature
    # merge, conflicts preserved with mutual refs). Promotion statistics are
    # always computed (they feed the SPEC §19 gates); promoted rows enter
    # rule_library ONLY when the mode receipt resolved to augment (P0-6).
    rule_ledgers: list[dict[str, Any]] = []
    promotion_receipts: list[dict[str, Any]] = []
    promoted_rows_all: list[dict[str, Any]] = []
    from ._semantic_extraction import (
        build_rule_candidate_ledger,
        promote_rule_candidates_to_rules,
        rule_promotion_gates_met,
    )

    for parsed in parsed_rows:
        parsed_source_id = _incremental_text(parsed.get("source_id"), 300)
        parsed_text = str(parsed.get("text") or "").strip()
        regex_rules = [
            dict(row)
            for row in _incremental_list(parsed.get("rules"))
            if isinstance(row, dict)
        ]
        llm_rules = [
            dict(row)
            for row in _incremental_list(parsed.get("semantic_candidates"))
            if isinstance(row, dict)
            and _incremental_text(row.get("kind"), 30).lower() == "rule"
        ]
        if not regex_rules and not llm_rules:
            continue
        ledger = build_rule_candidate_ledger(
            regex_rules,
            llm_rules,
            source_id=parsed_source_id,
            source_text=parsed_text,
        )
        rule_ledgers.append(ledger)
        promoted, promotion_receipt = promote_rule_candidates_to_rules(
            ledger.get("entries", []),
            source_id=parsed_source_id,
        )
        promotion_receipts.append(promotion_receipt)
        promoted_rows_all.extend(promoted)

    asset["rule_candidate_ledger"] = rule_ledgers
    # Promotion gates (SPEC §19) are recorded as data and feed the mode
    # resolution of the NEXT build — augment stays shadow until gates pass.
    asset["rule_promotion_receipts"] = promotion_receipts
    asset["rule_promotion_gates"] = rule_promotion_gates_met(
        promotion_receipts,
        ledger_stats={
            "regex_entry_count": sum(
                int(row.get("regex_entry_count") or 0) for row in rule_ledgers
            )
        },
    )
    # Augment merge gate: promoted rows enter rule_library ONLY when the mode
    # receipt resolved to augment (SPEC §12.3). Shadow records candidates and
    # gates but never touches formal output.
    _augment_active = any(
        isinstance(row, dict)
        and row.get("schema_version") == "qualibug.semantic-rule-extraction-mode.v1"
        and row.get("effective_mode") == "augment"
        for row in semantic_receipts
    )
    attached_promoted_rules = (
        _incremental_attach_promoted_rules(parsed_rows, promoted_rows_all)
        if _augment_active and promoted_rows_all
        else 0
    )
    asset["rule_promotion_applied"] = attached_promoted_rules > 0
    asset["rule_promotion_applied_count"] = attached_promoted_rules

    asset["source_inventory"] = copy.deepcopy(active_sources)
    if content_sources:
        preserved_interface_ids = _incremental_preserve_shared_api_artifacts(
            asset,
            identities=all_identities,
        )
        _incremental_purge_derived_source_rows(asset, identities=all_identities)
        _incremental_merge_source_rows(
            asset,
            parsed_rows,
            source_contexts,
            identities=all_identities,
            preserved_interface_ids=preserved_interface_ids,
        )
        parser_receipts = [
            dict(row)
            for row in asset.get("parser_receipts") or []
            if isinstance(row, dict)
        ]
        parser_receipts = _incremental_replace_rows(
            parser_receipts,
            identities=all_identities,
            replacements=[
                dict(parsed.get("parser_receipt") or {})
                for parsed in parsed_rows
                if isinstance(parsed.get("parser_receipt"), dict)
            ],
            identity_field="receipt_id",
        )
        asset["parser_receipts"] = parser_receipts
        semantic_ledger = [
            dict(row)
            for row in asset.get("semantic_extraction_receipts") or []
            if isinstance(row, dict)
        ]
        asset["semantic_extraction_receipts"] = _incremental_replace_rows(
            semantic_ledger,
            identities=all_identities,
            replacements=semantic_receipts,
            identity_field="receipt_id",
        )
        _incremental_refresh_semantic_candidate_projection(asset)

        asset = enrich_asset_with_openapi_schema_facts(asset, source_contexts)
        asset = enrich_asset_with_api_artifact_semantics(asset, source_contexts)
        asset = enrich_asset_with_api_operation_schema_bindings(asset)
        asset = enrich_asset_with_database_model_facts(asset, source_contexts)
        asset = reconcile_database_model_index_assets(asset)
        asset = enrich_asset_with_database_table_alignment_candidates(asset)
        asset = enrich_asset_with_api_database_alignment_candidates(asset)
        asset = enrich_asset_with_api_operation_database_candidates(asset)
        asset = apply_database_mapping_authority_decisions(
            asset,
            project_id=project,
            root=resolved_root,
        )
        asset = enrich_asset_with_database_observer_contracts(asset)
        asset = enrich_asset_with_database_relation_observer_candidates(asset)
        asset = apply_database_relation_authority_decisions(
            asset,
            project_id=project,
            root=resolved_root,
        )
        asset = enrich_asset_with_database_relation_observer_contracts(asset)
        asset = apply_semantic_lexicon_contract(asset)
        asset = apply_identity_benchmark_repository(
            asset,
            project_id=project,
            root=resolved_root,
        )
        comprehension_counts = _incremental_merge_comprehension(
            asset,
            source_contexts,
            identities=all_identities,
        )
        previous_candidate_rows = [
            dict(row)
            for row in _incremental_list(
                _incremental_dict(asset.get("business_fact_candidate_ledger")).get(
                    "items"
                )
            )
            if isinstance(row, dict)
        ]
        asset = compile_structure_first_business_facts(asset, source_contexts)
        candidate_ledger = _incremental_dict(
            asset.get("business_fact_candidate_ledger")
        )
        candidate_ledger["items"] = _incremental_replace_rows(
            previous_candidate_rows,
            identities=all_identities,
            replacements=[
                dict(row)
                for row in _incremental_list(candidate_ledger.get("items"))
                if isinstance(row, dict)
            ],
            identity_field="candidate_id",
        )
        candidate_ledger["all_candidates_terminal"] = all(
            bool(row.get("terminal"))
            for row in candidate_ledger["items"]
            if isinstance(row, dict)
        )
        asset["business_fact_candidate_ledger"] = candidate_ledger
        asset = govern_compiled_business_facts(
            asset,
            project_id=project,
            root=resolved_root,
        )
        _incremental_recompute_relations(asset)
        asset = enrich_asset_with_enterprise_understanding(
            asset,
            parsed_sources=None,
        )
        asset = project_business_facts_to_semantic_frames(asset)
        asset = build_chinese_semantic_context_envelopes(asset)
        asset = parse_chinese_clause_trees(asset)
        asset = enrich_frames_with_clause_structure(asset)
        asset = resolve_chinese_semantic_context(asset)
        asset = ground_semantic_frames(asset)
        # P0-E phase 2: v1 regex-candidate rules are decided against the
        # grounded frame SSOT (CONFIRMED / FALLBACK_UNGROUNDED /
        # UNCONFIRMED_NO_FRAME) with an observable receipt. The function
        # mutates the asset in place and RETURNS ONLY THE RECEIPT — the
        # return value must never replace the asset, or the entire knowledge
        # asset (ui_design_specs / rule_library / interfaces …) is wiped to
        # a receipt dict on every rebuild.
        apply_v1_extractor_frame_confirmation(asset)
        asset, _discarded = _downstream.refresh_chinese_business_downstream(
            asset,
            max_probe_count=0,
        )
        asset = enrich_job_assets_with_governance(
            asset,
            project_id=project,
            root=resolved_root,
            options=resolved_options,
        )
        asset = refresh_job_behavior_projection(asset)
    else:
        comprehension_counts = {"coverage_count": 0, "fact_count": 0, "glossary_count": 0}

    pending_validation_count = 0
    for source_ref in sorted(validation_refs):
        identities = set(old_ids_by_ref.get(source_ref, set())) | {source_ref}
        pending_validation_count += _incremental_mark_pending_validation(
            asset,
            identities=identities,
            event=next(
                iter(sorted(events_by_ref.get(source_ref, set()))),
                "SOURCE_BECAME_UNAVAILABLE",
            ),
        )

    asset["asset_id"] = _incremental_asset_identity(project, active_sources)
    asset["project_id"] = project
    asset["generated_at_utc"] = _now()
    execution_mode = "INCREMENTAL" if content_sources else "METADATA_ONLY"
    execution_receipt = copy.deepcopy(receipt)
    execution_receipt.update(
        {
            "status": "EXECUTED",
            "incremental_executor_installed": True,
            "completion_reason": (
                "INCREMENTAL_SEMANTIC_EXECUTOR_EXECUTED"
                if content_sources
                else "SOURCE_METADATA_OR_VALIDATION_EXECUTED"
            ),
            "incremental_execution_mode": execution_mode,
            "incremental_parsed_source_count": len(content_sources),
            "llm_reanalysis_scheduled_count": len(semantic_receipts),
            "unchanged_materials_reanalyzed": False,
            "full_project_recompute_requested": False,
            "source_content_returned": False,
        }
    )
    _incremental_update_summary(
        asset,
        project=project,
        active_sources=active_sources,
        refresh_receipt=execution_receipt,
    )

    if content_sources or validation_refs:
        block_reason = probe_generation_block_reason(asset)
        compiled_probes = build_gated_probes(
            asset,
            _probe_limit(resolved_options.get("probe_limit")),
            compiler=_base_api._probes_from_asset,
        )
        retain_previous_probe_catalog = bool(
            prior_probes and not compiled_probes and block_reason
        )
        probes = prior_probes if retain_previous_probe_catalog else compiled_probes
        _finalize_probe_relationships(asset, probes)
        asset["probe_generation_gate"] = {
            "schema": "qualibug.enterprise-probe-generation-gate.v1",
            "status": "PASS" if not block_reason else "BLOCKED",
            "entry_allowed": not bool(block_reason),
            "block_reason": block_reason,
            "probe_count": len(probes),
            "probe_limit": _probe_limit(resolved_options.get("probe_limit")),
            "previous_probe_catalog_retained": retain_previous_probe_catalog,
            "build_authority": "incremental_enterprise_knowledge_composition",
        }
    else:
        probes = _incremental_load_probe_catalog(project, resolved_root)

    pending_probe_count = 0
    for source_ref in sorted(validation_refs):
        identities = set(old_ids_by_ref.get(source_ref, set())) | {source_ref}
        pending_probe_count += _incremental_mark_probe_validation(
            asset,
            probes,
            identities=identities,
            event=next(
                iter(sorted(events_by_ref.get(source_ref, set()))),
                "SOURCE_BECAME_UNAVAILABLE",
            ),
        )

    impact_projection = _incremental_impact_projection(
        asset,
        identities=all_identities,
        source_contexts=source_contexts,
        probes=probes,
        refresh_receipt=receipt,
    )
    asset["incremental_semantic_impact"] = impact_projection
    asset["relationships"] = _base_api._dedupe_by_id(
        [
            *[
                dict(row)
                for row in asset.get("relationships") or []
                if isinstance(row, dict)
            ],
            *[
                dict(row)
                for row in impact_projection.get("relations") or []
                if isinstance(row, dict)
            ],
        ],
        "edge_id",
    )
    impact_counts = dict(impact_projection.get("affected_counts") or {})
    stage_counts = {
        "fact_reextraction": int(impact_counts.get("fact") or 0),
        "entity_remerge": int(impact_counts.get("entity") or 0),
        "conflict_recomputation": int(impact_counts.get("conflict") or 0),
        "behavior_model_impact_analysis": int(impact_counts.get("behavior") or 0),
        "scenario_regeneration_or_invalidation": int(
            impact_counts.get("scenario") or 0
        ),
        "regression_scope_update": int(impact_counts.get("regression") or 0),
    }
    execution_receipt.update(
        {
            "artifact_diff": {
                **_incremental_dict(execution_receipt.get("artifact_diff")),
                "status": "COMPLETE",
                "content_block_count": int(impact_counts.get("content_block") or 0),
            },
            "affected_content_blocks": int(impact_counts.get("content_block") or 0),
            "affected_facts": stage_counts["fact_reextraction"],
            "affected_entities": stage_counts["entity_remerge"],
            "affected_behaviors": stage_counts["behavior_model_impact_analysis"],
            "affected_scenarios": stage_counts[
                "scenario_regeneration_or_invalidation"
            ],
            "affected_regression_items": stage_counts["regression_scope_update"],
            "semantic_impact_relation_count": int(
                impact_projection.get("relation_count") or 0
            ),
            "pending_validation_count": pending_validation_count + pending_probe_count,
            "downstream": [
                {
                    "stage": stage,
                    "status": "EXECUTED_INCREMENTAL",
                    "executed": True,
                    "source_refs_bound": len(events_by_ref),
                    "authority": "enterprise_knowledge_composition",
                    "affected_count": int(stage_counts.get(stage) or 0),
                }
                for stage in (
                    "fact_reextraction",
                    "entity_remerge",
                    "conflict_recomputation",
                    "behavior_model_impact_analysis",
                    "scenario_regeneration_or_invalidation",
                    "regression_scope_update",
                )
            ],
        }
    )
    asset["incremental_refresh_receipt"] = copy.deepcopy(execution_receipt)

    asset = project_source_occurrence_assets(
        asset,
        project_id=project,
        root=resolved_root,
    )
    _persist_final(asset, probes, project_id=project, root=resolved_root)
    return {
        "asset": asset,
        "mode": execution_mode,
        "parsed_source_count": len(content_sources),
        "llm_reanalysis_count": len(semantic_receipts),
        "semantic_extraction_status": semantic_status,
        "pending_validation_count": pending_validation_count + pending_probe_count,
        "comprehension_counts": comprehension_counts,
        "stage_counts": stage_counts,
        "affected_content_blocks": int(impact_counts.get("content_block") or 0),
        "semantic_impact_relation_count": int(
            impact_projection.get("relation_count") or 0
        ),
        "unchanged_materials_reanalyzed": False,
        "full_project_recompute_requested": False,
    }


def _capture_previous_implicit_rule_governance(
    project_id: str,
    root: Path,
) -> dict[str, Any]:
    """Capture only durable authority from the previously finalized asset.

    The current build has two understanding passes. The first pass is provisional and
    must never become the historical identity registry consumed by the second pass.
    Both passes therefore share the same registry baseline captured here.
    """

    previous = _base_api.load_enterprise_business_knowledge_asset(project_id, root) or {}
    carried = {
        field: copy.deepcopy(previous.get(field))
        for field in _DURABLE_IMPLICIT_RULE_GOVERNANCE_FIELDS
        if previous.get(field) not in (None, "", {}, [])
    }
    prior_registry = previous.get("enterprise_identity_registry")
    return {
        "previous_asset_id": previous.get("asset_id"),
        "fields": carried,
        "identity_registry": (
            copy.deepcopy(prior_registry) if isinstance(prior_registry, dict) else {}
        ),
    }


def _restore_previous_implicit_rule_governance(
    asset: dict[str, Any],
    captured: dict[str, Any],
) -> None:
    """Restore governance history without reusing prior extraction or executable rows."""

    fields = captured.get("fields") if isinstance(captured.get("fields"), dict) else {}
    restored: list[str] = []
    for field in _DURABLE_IMPLICIT_RULE_GOVERNANCE_FIELDS:
        if field not in fields:
            continue
        asset[field] = copy.deepcopy(fields[field])
        restored.append(field)
    asset["implicit_rule_governance_carry_forward_receipt"] = {
        "schema_version": "qualibug.implicit-rule-governance-carry-forward.v1",
        "status": "RESTORED" if restored else "NO_PREVIOUS_GOVERNANCE_STATE",
        "previous_asset_id": captured.get("previous_asset_id"),
        "restored_fields": restored,
        "restored_field_count": len(restored),
        "authority": "previous_finalized_enterprise_knowledge_asset",
        "captured_before_base_rebuild": True,
        "prior_rule_library_reused": False,
        "prior_business_fact_ledger_reused": False,
        "prior_relationships_reused": False,
        "prior_enterprise_understanding_model_reused": False,
        "prior_probe_catalog_reused": False,
    }


def _restore_previous_identity_registry(
    asset: dict[str, Any],
    captured: dict[str, Any],
    *,
    pass_name: str,
) -> None:
    """Reset one understanding pass to the previous finalized registry baseline."""
    prior = captured.get("identity_registry")
    if isinstance(prior, dict) and prior:
        asset["enterprise_identity_registry"] = copy.deepcopy(prior)
        status = "RESTORED"
    else:
        asset.pop("enterprise_identity_registry", None)
        status = "NO_PREVIOUS_FINALIZED_REGISTRY"
    receipt = dict(asset.get("identity_registry_carry_forward_receipt") or {})
    passes = [
        str(value)
        for value in receipt.get("restored_for_understanding_passes") or []
        if str(value)
    ]
    if pass_name not in passes:
        passes.append(pass_name)
    asset["identity_registry_carry_forward_receipt"] = {
        "schema": "qualibug.enterprise-identity-registry-carry-forward.v1",
        "status": status,
        "previous_asset_id": captured.get("previous_asset_id"),
        "prior_entity_count": len(
            [
                row
                for row in (prior or {}).get("entities") or []
                if isinstance(row, dict)
            ]
        ),
        "restored_for_understanding_passes": passes,
        "same_finalized_baseline_used_for_all_passes": True,
        "provisional_first_pass_registry_promoted": False,
        "authority": "previous_finalized_enterprise_knowledge_asset",
    }


def configure_source_parser_extensions() -> None:
    """Explicit compatibility boundary for legacy parser plugins.

    Parser extension registration remains idempotent, but it is no longer performed
    merely by importing the knowledge package and it never replaces the build
    authority. New parser work should move into a registry rather than add wrappers.
    """
    install_formal_ui_root_array_guard()
    install_formal_ui_contract_parser()
    install_ui_surface_declaration_parser()
    install_formal_ui_persistent_probe_guard()
    install_formal_ui_visual_baseline_guard()
    install_formal_ui_visual_viewport_guard()
    install_interface_runtime_contract_parser()
    install_database_model_semantic_bridge()


def _finalize_probe_relationships(
    asset: dict[str, Any], probes: list[dict[str, Any]]
) -> None:
    ready_rules = {
        str(row.get("rule_id") or "").strip()
        for row in asset.get("rule_library") or []
        if isinstance(row, dict)
        and str(row.get("downstream_binding_status") or "").strip()
        == "READY_AUTHORITATIVE_OPERATION_BOUND"
    }
    implementation_status = str(
        (asset.get("implementation_binding_gate") or {}).get("status") or "NOT_BUILT"
    )
    scenario_status = str(
        (asset.get("scenario_planning_gate") or {}).get("status") or "NOT_BUILT"
    )
    for probe in probes:
        lineage = dict(probe.get("knowledge_lineage") or {})
        rule_id = str(lineage.get("rule_id") or "").strip()
        if rule_id in ready_rules:
            lineage.update(
                {
                    "business_comprehension_gate": "READY_AUTHORITATIVE_OPERATION_BOUND",
                    "implementation_binding_gate": implementation_status,
                    "scenario_planning_gate": scenario_status,
                    "fact_authority": "original_chinese_source_span",
                }
            )
            probe["knowledge_lineage"] = lineage

    relationships = [
        dict(row)
        for row in asset.get("relationships") or []
        if isinstance(row, dict)
        and not (
            str(row.get("relation") or "") == "risk_to_probe"
            and str(row.get("to") or "").startswith("probe:")
        )
    ]
    for probe in probes:
        lineage = dict(probe.get("knowledge_lineage") or {})
        risk_id = str(lineage.get("risk_id") or "").strip()
        probe_id = str(probe.get("probe_id") or "").strip()
        if not risk_id or not probe_id:
            continue
        relationships.append(
            {
                "edge_id": f"edge:risk-probe:{risk_id}:{probe_id}",
                "from": risk_id,
                "to": f"probe:{probe_id}",
                "relation": "risk_to_probe",
                "confidence": 1.0,
                "status": "accepted",
                "derivation": "final_knowledge_composition",
                "evidence": {
                    "execution_policy": probe.get("execution_policy"),
                    "scenario_planning_gate": scenario_status,
                    "runtime_materialization_gate": (
                        asset.get("runtime_materialization_gate") or {}
                    ).get("status"),
                },
            }
        )
    asset["relationships"] = _base_api._dedupe_by_id(relationships, "edge_id")


def _persist_final(
    asset: dict[str, Any],
    probes: list[dict[str, Any]],
    *,
    project_id: str,
    root: Path,
) -> None:
    _downstream._persist(asset, probes, project_id=project_id, root=root)
    registry = _load_registry(project_id, root)
    registry["audit_events"].append(
        {
            "event": "finalize_enterprise_knowledge_composition",
            "at_utc": _now(),
            "actor": {"name": "system", "role": "knowledge_composition_root"},
            "asset_id": asset.get("asset_id"),
            "probe_count": len(probes),
            "probe_generation_status": asset.get("probe_generation_gate", {}).get(
                "status"
            ),
            "source_occurrence_projection_status": (
                asset.get("source_occurrence_projection_receipt") or {}
            ).get("status"),
            "structure_first_business_fact_status": (
                asset.get("structure_first_business_fact_compilation_receipt") or {}
            ).get("status"),
            "identity_second_pass_status": (
                asset.get("identity_evidence_policy_receipt") or {}
            ).get("classified_fact_count"),
            "identity_benchmark_measurement_status": (
                asset.get("enterprise_identity_benchmark") or {}
            ).get("status"),
        }
    )
    _save_registry(project_id, root, registry)


def build_enterprise_business_knowledge_asset(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic final asset without wrapper-installed authorities."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    resolved_options = dict(options or {})
    final_probe_limit = _probe_limit(resolved_options.get("probe_limit"))

    configure_source_parser_extensions()

    # Capture only durable implicit-rule governance before the extraction primitive
    # overwrites the persisted asset path. Current source facts are always rebuilt.
    previous_finalized_governance = _capture_previous_implicit_rule_governance(
        project, resolved_root
    )

    # The base compiler is an extraction primitive in this composition. It is not
    # allowed to publish Probes before semantic, implementation and runtime gates.
    base_options = {**resolved_options, "probe_limit": 0}
    base_parsed_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    asset = _base_api.build_enterprise_business_knowledge_asset(
        project,
        resolved_root,
        base_options,
        parsed_source_sink=base_parsed_rows,
    )
    _restore_previous_implicit_rule_governance(
        asset, previous_finalized_governance
    )
    _restore_previous_identity_registry(
        asset, previous_finalized_governance, pass_name="source_fact_pass"
    )
    parsed_overrides = {
        _incremental_text(source.get("source_id"), 300): parsed
        for source, parsed in base_parsed_rows
        if _incremental_text(source.get("source_id"), 300)
    }
    parsed_sources = _parsed_sources_for_context(
        asset,
        resolved_root,
        parsed_overrides=parsed_overrides,
        require_overrides=True,
    )
    asset["source_parse_execution_receipt"] = {
        "schema": "qualibug.enterprise-source-parse-execution.v1",
        "active_source_count": len(asset.get("source_inventory") or []),
        "parse_execution_count": len(base_parsed_rows),
        "context_reparse_count": 0,
        "document_structure_rebuild_count": sum(
            1
            for row in parsed_sources
            if not row.get("document_structure_reused_from_parse")
        ),
        "document_structure_reuse_count": sum(
            1
            for row in parsed_sources
            if row.get("document_structure_reused_from_parse")
        ),
        "handoff_scope": "same_build_invocation",
        "cross_build_cache_used": False,
    }

    # Technical declarations must be projected before enterprise cognition. No stage
    # below invents a business sequence from document, schema or diagram order.
    asset = enrich_asset_with_openapi_schema_facts(asset, parsed_sources)
    asset = enrich_asset_with_api_artifact_semantics(asset, parsed_sources)
    asset = enrich_asset_with_api_operation_schema_bindings(asset)
    asset = enrich_asset_with_database_model_facts(asset, parsed_sources)
    asset = reconcile_database_model_index_assets(asset)
    asset = enrich_asset_with_database_table_alignment_candidates(asset)
    asset = enrich_asset_with_api_database_alignment_candidates(asset)
    asset = enrich_asset_with_api_operation_database_candidates(asset)
    # Durable table/field approvals are always re-applied to freshly rebuilt candidates.
    asset = apply_database_mapping_authority_decisions(
        asset,
        project_id=project,
        root=resolved_root,
    )
    asset = enrich_asset_with_database_observer_contracts(asset)
    # Relation candidates require the current root Observer, then reuse the same durable
    # mapping ledger under candidate_kind=relation. Candidate drift fails closed.
    asset = enrich_asset_with_database_relation_observer_candidates(asset)
    asset = apply_database_relation_authority_decisions(
        asset,
        project_id=project,
        root=resolved_root,
    )
    asset = enrich_asset_with_database_relation_observer_contracts(asset)

    # The shared language policy is a formal runtime dependency. A missing or malformed
    # lexicon blocks comprehension instead of silently degrading to an empty vocabulary.
    asset = apply_semantic_lexicon_contract(asset)

    # Externally supplied Ground Truth and quality policy are project-scoped durable
    # inputs. They enter only here, before the first identity benchmark projection, and
    # never bypass the canonical understanding or Probe gates.
    asset = apply_identity_benchmark_repository(
        asset,
        project_id=project,
        root=resolved_root,
    )

    # Existing Chinese-first parsing remains the compatibility parser. It creates the
    # current ledger and conflict/context assets through the existing authority. The
    # understanding boundary derives implicit rules only after those conflicts resolve.
    asset = enrich_asset_with_enterprise_understanding(
        asset, parsed_sources=parsed_sources
    )

    # Compile the same source-backed facts through Document Structure IR addresses,
    # atomize multi-predicate statements, add typed non-modal facts, and upgrade the
    # single existing ledger. This is not a second product path.
    asset = compile_structure_first_business_facts(asset, parsed_sources)

    # Two-pass identity governance operates only on the final compiled ledger. It
    # classifies source-backed alias evidence and re-applies the existing durable
    # conflict authority; it never creates another identity engine.
    asset = govern_compiled_business_facts(
        asset,
        project_id=project,
        root=resolved_root,
    )

    # Rebuild cognition from the upgraded ledger without rerunning source extraction.
    # Both cognition passes use the same previous-finalized identity registry baseline;
    # the provisional first-pass registry cannot manufacture a same-build collision.
    _restore_previous_identity_registry(
        asset, previous_finalized_governance, pass_name="compiled_fact_pass"
    )
    asset = enrich_asset_with_enterprise_understanding(asset, parsed_sources=None)

    # Project the compiled fact ledger into the Chinese Semantic Frame SSOT
    # (P0-A). Runs after the second cognition pass so actor/entity registries
    # are final; frames carry exact source spans, slot resolution statuses and
    # the semantic signature. This is a pure projection of typed fact slots —
    # no Chinese text is re-parsed and no vocabulary is added.
    asset = project_business_facts_to_semantic_frames(asset)

    # P0-B: document-structure context envelope → atomic clause trees → frame
    # enrichment. List children inherit their list parent's conditions, table
    # cells receive row/column header mention candidates, and enumeration
    # action candidates are added to frame mentions. All three stages are
    # candidate layers: they add structure the fact missed, never override the
    # fact-derived slots, and never bind semantics to technical objects.
    asset = build_chinese_semantic_context_envelopes(asset)
    asset = parse_chinese_clause_trees(asset)
    asset = enrich_frames_with_clause_structure(asset)

    # P0-C: frame-level context resolution — omitted actors are recovered only
    # from unique evidence (only-if subject, unique prior frame in the same
    # section, unique section heading), coreference stays mention-level, and
    # anything unresolvable keeps its explicit UNKNOWN status + reason code.
    # Raw text is never rewritten.
    asset = resolve_chinese_semantic_context(asset)

    # P0-D: evidence-driven technical grounding — actor/operation/entity/state/
    # scope bindings with typed receipts. This ACTIVATES the P0-A Behavior IR
    # channel: grounded frames now contribute owns/permits/denies relations
    # (deduped against legacy by canonical node ids and permission-row scope).
    asset = ground_semantic_frames(asset)

    # P0-E phase 2: v1 regex-candidate rules are decided against the
    # grounded frame SSOT (CONFIRMED / FALLBACK_UNGROUNDED /
    # UNCONFIRMED_NO_FRAME) with an observable receipt. The function mutates
    # the asset in place and returns only the receipt — the return value must
    # never replace the asset, or the entire knowledge asset (ui_design_specs
    # / rule_library / interfaces …) is wiped to a receipt dict on every
    # rebuild.
    apply_v1_extractor_frame_confirmation(asset)

    # Downstream rule/oracle projection is still needed before Job projection, but
    # Probe compilation remains deferred to the final stage.
    asset, _discarded = _downstream.refresh_chinese_business_downstream(
        asset, max_probe_count=0
    )
    asset = enrich_job_assets_with_governance(
        asset,
        project_id=project,
        root=resolved_root,
        options=resolved_options,
    )
    asset = refresh_job_behavior_projection(asset)

    block_reason = probe_generation_block_reason(asset)
    probes = build_gated_probes(
        asset,
        final_probe_limit,
        compiler=_base_api._probes_from_asset,
    )
    _finalize_probe_relationships(asset, probes)

    asset["probe_generation_gate"] = {
        "schema": "qualibug.enterprise-probe-generation-gate.v1",
        "status": "PASS" if not block_reason else "BLOCKED",
        "entry_allowed": not bool(block_reason),
        "block_reason": block_reason,
        "probe_count": len(probes),
        "probe_limit": final_probe_limit,
        "build_authority": "explicit_enterprise_knowledge_composition",
    }
    summary = dict(asset.get("summary") or {})
    summary.update(
        {
            "generated_probe_count": len(probes),
            "relationship_count": len(asset.get("relationships") or []),
            "probe_generation_status": asset["probe_generation_gate"]["status"],
            "knowledge_composition_authority": "explicit_single_call_graph",
            "implicit_rule_governance_carried_field_count": int(
                (
                    asset.get("implicit_rule_governance_carry_forward_receipt")
                    or {}
                ).get("restored_field_count")
                or 0
            ),
        }
    )
    asset["summary"] = summary
    governance = dict(asset.get("governance") or {})
    governance.update(
        {
            "knowledge_builder_uses_explicit_composition_root": True,
            "knowledge_builder_wrapper_chain_enabled": False,
            "probe_generation_occurs_after_final_gates": True,
            "zero_probe_budget_is_strict": True,
            "job_governance_uses_direct_function_calls": True,
            "package_import_replaces_build_authority": False,
            "parser_extension_registration_is_explicit_compatibility_boundary": True,
            "openapi_schema_fact_projection_precedes_enterprise_understanding": True,
            "api_artifact_projection_precedes_enterprise_understanding": True,
            "api_operation_schema_binding_precedes_enterprise_understanding": True,
            "database_model_projection_precedes_enterprise_understanding": True,
            "database_model_semantic_bridge_installed_explicitly": True,
            "database_model_index_reconciliation_precedes_enterprise_understanding": True,
            "database_table_alignment_precedes_enterprise_understanding": True,
            "api_database_contract_alignment_precedes_enterprise_understanding": True,
            "api_operation_database_projection_precedes_enterprise_understanding": True,
            "database_mapping_authority_precedes_enterprise_understanding": True,
            "database_observer_contract_projection_precedes_enterprise_understanding": True,
            "database_relation_candidate_projection_precedes_enterprise_understanding": True,
            "database_relation_authority_precedes_enterprise_understanding": True,
            "database_relation_observer_projection_precedes_enterprise_understanding": True,
            "identity_benchmark_repository_precedes_enterprise_understanding": True,
            "identity_benchmark_inputs_use_project_workspace": True,
            "identity_benchmark_api_reuses_composition_root": True,
            "implicit_rule_projection_runs_inside_understanding_boundary": True,
            "implicit_rule_projection_runs_after_conflict_reconciliation": True,
            "implicit_rule_projection_uses_existing_rule_library": True,
            "implicit_rule_governance_loaded_before_reprojection": True,
            "implicit_rule_governance_uses_previous_finalized_asset": True,
            "implicit_rule_rebuild_reuses_prior_business_extraction": False,
            "semantic_lexicon_contract_precedes_formal_comprehension": True,
            "structure_first_fact_compilation_uses_existing_ledger": True,
            "structure_first_fact_compilation_precedes_downstream_binding": True,
            "second_pass_identity_governance_uses_existing_ledger": True,
            "second_pass_conflict_governance_uses_existing_authority": True,
            "understanding_model_rebuilt_from_typed_atomic_ledger": True,
            "database_mapping_authority_reapplied_on_every_build": True,
            "database_relation_authority_reapplied_on_every_build": True,
            "source_occurrence_projection_runs_after_business_and_probe_compilation": True,
        }
    )
    asset["governance"] = governance

    # Occurrence evidence views are the final provenance projection. They never feed back
    # into business extraction, entity fusion, rules, Jobs, or Probe compilation.
    asset = project_source_occurrence_assets(
        asset,
        project_id=project,
        root=resolved_root,
    )
    occurrence_receipt = dict(asset.get("source_occurrence_projection_receipt") or {})
    if occurrence_receipt.get("status") == "BLOCKED":
        raise RuntimeError(
            "source occurrence projection blocked: "
            + str(occurrence_receipt.get("missing_canonical") or [])[:1000]
        )

    _persist_final(asset, probes, project_id=project, root=resolved_root)
    return asset


def load_enterprise_business_knowledge_asset(
    project_id: str = "real_project_demo", root: Path | None = None
) -> dict[str, Any] | None:
    """Load the already-finalized asset; loading never enriches or rewrites it."""
    return _base_api.load_enterprise_business_knowledge_asset(project_id, root)


def generate_enterprise_business_knowledge_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    project_id: str = "real_project_demo",
    root: Path | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Return the final governed catalog, rebuilding through the composition root if absent."""
    del openapi, cfg
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    asset = load_enterprise_business_knowledge_asset(project, resolved_root)
    if asset is None:
        asset = build_enterprise_business_knowledge_asset(
            project,
            resolved_root,
            {"probe_limit": _probe_limit(max_count)},
        )
    catalog = _base_api._load_json(_paths(project, resolved_root)["probe_catalog"], {})
    rows = [
        dict(row)
        for row in (catalog.get("items") if isinstance(catalog, dict) else []) or []
        if isinstance(row, dict)
    ]
    return (
        rows[: _probe_limit(max_count, default=len(rows))]
        if max_count is not None
        else rows
    )


__all__ = [
    "configure_source_parser_extensions",
    "build_enterprise_business_knowledge_asset",
    "refresh_enterprise_business_knowledge_asset_incremental",
    "load_enterprise_business_knowledge_asset",
    "generate_enterprise_business_knowledge_probes",
]
