"""Project source occurrences into the finalized enterprise knowledge asset.

Business facts and Document IR are parsed once per interpretation.  The finalized asset then
exposes one occurrence-specific evidence view for every active source reference.  Occurrence
views reuse canonical blocks and parser receipts; they do not execute adapters or semantic
extractors again.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ._utils import _load_registry, _short_hash

SOURCE_OCCURRENCE_PROJECTION_SCHEMA = (
    "qualibug.enterprise-source-occurrence-projection.v1"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _active_occurrences(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in registry.get("source_occurrences") or []
        if isinstance(row, dict) and row.get("status") == "active"
    ]
    return sorted(
        rows,
        key=lambda row: (
            _text(row.get("source_ref")),
            _text(row.get("source_occurrence_id")),
        ),
    )


def _structure_assets(asset: dict[str, Any]) -> tuple[dict[str, Any], str]:
    direct = _dict(asset.get("document_structure_assets"))
    if direct:
        return direct, "asset"
    model = _dict(asset.get("enterprise_understanding_model"))
    nested = _dict(model.get("document_structure_assets"))
    return nested, "model" if nested else ""


def _canonical_inventory(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in asset.get("source_inventory") or []
        if isinstance(row, dict)
        and _text(row.get("source_id"))
        and not _text(row.get("source_occurrence_id"))
    ]


def _rebind_receipt_identity(
    value: Any,
    *,
    canonical_source_id: str,
    occurrence_id: str,
    source_ref: str,
) -> Any:
    if isinstance(value, list):
        return [
            _rebind_receipt_identity(
                row,
                canonical_source_id=canonical_source_id,
                occurrence_id=occurrence_id,
                source_ref=source_ref,
            )
            for row in value
        ]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, raw in value.items():
        if key == "source_id" and _text(raw) in {"", canonical_source_id}:
            result[key] = occurrence_id
        else:
            result[key] = _rebind_receipt_identity(
                raw,
                canonical_source_id=canonical_source_id,
                occurrence_id=occurrence_id,
                source_ref=source_ref,
            )
    result.setdefault("canonical_source_id", canonical_source_id)
    result.setdefault("source_occurrence_id", occurrence_id)
    result.setdefault("source_ref", source_ref)
    return result


def _occurrence_inventory_row(
    canonical: dict[str, Any],
    occurrence: dict[str, Any],
) -> dict[str, Any]:
    occurrence_id = _text(occurrence.get("source_occurrence_id"))
    canonical_source_id = _text(occurrence.get("canonical_source_id"))
    source_ref = _text(occurrence.get("source_ref"))
    return {
        **copy.deepcopy(canonical),
        "source_id": occurrence_id,
        "source_occurrence_id": occurrence_id,
        "canonical_source_id": canonical_source_id,
        "external_ref": source_ref,
        "source_origin_ref": source_ref,
        "source_ref": source_ref,
        "content_asset_id": occurrence.get("content_asset_id"),
        "interpretation_asset_id": occurrence.get("interpretation_asset_id"),
        "occurrence_version": occurrence.get("version"),
        "inventory_role": "SOURCE_OCCURRENCE",
        "parse_reused": bool(occurrence.get("parse_reused")),
        "canonical_parse_shared": True,
        "independent_evidence_identity": True,
        "absolute_workspace_path_is_identity": False,
    }


def _canonical_inventory_row(
    canonical: dict[str, Any],
    occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    source_id = _text(canonical.get("source_id"))
    refs = sorted(
        {
            _text(row.get("source_ref"))
            for row in occurrences
            if _text(row.get("canonical_source_id")) == source_id
            and _text(row.get("source_ref"))
        }
    )
    result = copy.deepcopy(canonical)
    result["inventory_role"] = "CANONICAL_INTERPRETATION"
    result["canonical_source_id"] = source_id
    result["source_refs"] = refs
    result["source_occurrence_count"] = len(refs)
    # A canonical interpretation is not itself a source occurrence.  Leaving an old
    # external_ref here would create a second candidate for the same human source_ref.
    result["external_ref"] = ""
    result["source_origin_ref"] = ""
    result["canonical_parse_shared"] = bool(refs)
    return result


def _occurrence_structure_item(
    canonical_item: dict[str, Any],
    occurrence: dict[str, Any],
) -> dict[str, Any]:
    canonical_source_id = _text(occurrence.get("canonical_source_id"))
    occurrence_id = _text(occurrence.get("source_occurrence_id"))
    source_ref = _text(occurrence.get("source_ref"))
    result = _rebind_receipt_identity(
        copy.deepcopy(canonical_item),
        canonical_source_id=canonical_source_id,
        occurrence_id=occurrence_id,
        source_ref=source_ref,
    )
    result["source_id"] = occurrence_id
    result["source_occurrence_id"] = occurrence_id
    result["canonical_source_id"] = canonical_source_id
    result["source_ref"] = source_ref
    result["content_asset_id"] = occurrence.get("content_asset_id")
    result["interpretation_asset_id"] = occurrence.get("interpretation_asset_id")
    result["structure_view_role"] = "SOURCE_OCCURRENCE_EVIDENCE_VIEW"
    result["canonical_parse_shared"] = True
    result["adapter_execution_repeated"] = False
    result["semantic_extraction_repeated"] = False
    result["occurrence_evidence_view_id"] = "occurrence-evidence:" + _short_hash(
        {
            "source_occurrence_id": occurrence_id,
            "canonical_source_id": canonical_source_id,
            "source_ref": source_ref,
            "content_hash": occurrence.get("content_hash"),
        },
        32,
    )
    blocks = []
    for raw in result.get("blocks") or []:
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        block["canonical_block_id"] = _text(block.get("block_id"))
        block["source_id"] = occurrence_id
        block["source_occurrence_id"] = occurrence_id
        block["canonical_source_id"] = canonical_source_id
        block["source_ref"] = source_ref
        address = _dict(block.get("evidence_address"))
        address.update(
            {
                "source_id": occurrence_id,
                "source_occurrence_id": occurrence_id,
                "canonical_source_id": canonical_source_id,
                "source_ref": source_ref,
                "content_asset_id": occurrence.get("content_asset_id"),
                "interpretation_asset_id": occurrence.get(
                    "interpretation_asset_id"
                ),
            }
        )
        block["evidence_address"] = address
        blocks.append(block)
    result["blocks"] = blocks
    return result


def project_source_occurrence_assets(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path,
) -> dict[str, Any]:
    """Attach deterministic occurrence inventories and evidence views to one final asset."""
    result = copy.deepcopy(asset or {})
    registry = _load_registry(project_id, root)
    occurrences = _active_occurrences(registry)
    canonical_inventory = _canonical_inventory(result)
    canonical_by_id = {
        _text(row.get("source_id")): row
        for row in canonical_inventory
        if _text(row.get("source_id"))
    }
    structure, location = _structure_assets(result)
    canonical_items = [
        dict(row)
        for row in structure.get("items") or []
        if isinstance(row, dict)
        and not _text(row.get("source_occurrence_id"))
    ]
    item_by_source = {
        _text(row.get("source_id")): row
        for row in canonical_items
        if _text(row.get("source_id"))
    }

    missing_canonical: list[dict[str, Any]] = []
    occurrence_inventory: list[dict[str, Any]] = []
    occurrence_items: list[dict[str, Any]] = []
    for occurrence in occurrences:
        canonical_source_id = _text(occurrence.get("canonical_source_id"))
        canonical = canonical_by_id.get(canonical_source_id)
        item = item_by_source.get(canonical_source_id)
        if canonical is None:
            missing_canonical.append(
                {
                    "source_occurrence_id": occurrence.get("source_occurrence_id"),
                    "canonical_source_id": canonical_source_id,
                    "reason_code": "SOURCE_OCCURRENCE_CANONICAL_INVENTORY_MISSING",
                }
            )
            continue
        occurrence_inventory.append(
            _occurrence_inventory_row(canonical, occurrence)
        )
        if item is not None:
            occurrence_items.append(_occurrence_structure_item(item, occurrence))

    canonical_projected = [
        _canonical_inventory_row(row, occurrences) for row in canonical_inventory
    ]
    result["canonical_source_inventory"] = canonical_projected
    result["source_occurrence_inventory"] = occurrence_inventory
    result["source_inventory"] = [*canonical_projected, *occurrence_inventory]
    result["content_assets"] = _rows(registry.get("content_assets"))
    result["interpretation_assets"] = _rows(
        registry.get("interpretation_assets")
    )

    if structure:
        projected_structure = copy.deepcopy(structure)
        projected_structure["canonical_items"] = canonical_items
        projected_structure["occurrence_items"] = occurrence_items
        projected_structure["items"] = [*canonical_items, *occurrence_items]
        projected_structure["canonical_source_count"] = len(canonical_items)
        projected_structure["source_occurrence_count"] = len(occurrence_items)
        projected_structure["source_count"] = len(projected_structure["items"])
        projected_structure["canonical_parse_count"] = len(canonical_items)
        projected_structure["adapter_execution_count"] = len(canonical_items)
        projected_structure["occurrence_evidence_view_count"] = len(
            occurrence_items
        )
        projected_structure["occurrence_projection_schema"] = (
            SOURCE_OCCURRENCE_PROJECTION_SCHEMA
        )
        if location == "asset":
            result["document_structure_assets"] = projected_structure
        elif location == "model":
            model = _dict(result.get("enterprise_understanding_model"))
            model["document_structure_assets"] = projected_structure
            result["enterprise_understanding_model"] = model

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "canonical_source_count": len(canonical_projected),
            "source_occurrence_count": len(occurrence_inventory),
            "content_asset_count": len(result["content_assets"]),
            "interpretation_asset_count": len(result["interpretation_assets"]),
            "occurrence_evidence_view_count": len(occurrence_items),
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "content_identity_separate_from_source_occurrence": True,
            "interpretation_identity_separate_from_content_identity": True,
            "same_interpretation_content_parsed_once": True,
            "source_occurrence_evidence_views_are_projection_only": True,
            "source_occurrence_projection_reexecutes_adapters": False,
            "source_occurrence_projection_reexecutes_semantic_extraction": False,
            "source_occurrence_identity_authority": "SOURCE_OCCURRENCE_REGISTRY",
        }
    )
    result["governance"] = governance
    result["source_occurrence_projection_receipt"] = {
        "schema": SOURCE_OCCURRENCE_PROJECTION_SCHEMA,
        "status": "BLOCKED" if missing_canonical else "PASS",
        "canonical_source_count": len(canonical_projected),
        "active_source_occurrence_count": len(occurrences),
        "projected_source_occurrence_count": len(occurrence_inventory),
        "occurrence_evidence_view_count": len(occurrence_items),
        "content_asset_count": len(result["content_assets"]),
        "interpretation_asset_count": len(result["interpretation_assets"]),
        "missing_canonical_count": len(missing_canonical),
        "missing_canonical": missing_canonical,
        "adapter_execution_repeated": False,
        "semantic_extraction_repeated": False,
        "automatic_source_occurrence_winner_used": False,
    }
    return result


__all__ = [
    "SOURCE_OCCURRENCE_PROJECTION_SCHEMA",
    "project_source_occurrence_assets",
]
