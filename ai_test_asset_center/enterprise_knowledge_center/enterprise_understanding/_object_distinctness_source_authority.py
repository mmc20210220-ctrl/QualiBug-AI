"""Normalize common source heading forms before object authority is evaluated.

This adapter performs format-only normalization. It never invents an object,
creates an alias, or resolves identity. Existing source-declaration and conflict
authorities remain the sole type and source-selection authorities.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .._parsing import _canonical_entity_name
from ._object_role_evidence import comparison_key
from ._object_source_conflict_preparation import (
    finalize_conflict_governed_source_recognition,
    prepare_conflict_governed_source_asset,
)
from .schema import as_dict, as_list, clone_asset_for_understanding_projection, text

_CJK_ENTITY_SECTION = re.compile(
    r"^(?:(?:核心|主要|关键|业务|领域)+)(?:实体|对象)(?:定义|清单|列表)?$"
)
_STATE_MARKERS = (
    "状态机",
    "生命周期",
    "状态流转",
    "state machine",
    "lifecycle",
    "workflow",
)


def _formatted_heading(value: Any) -> str:
    base, gloss = _canonical_entity_name(text(value))
    compact = comparison_key(base)
    if _CJK_ENTITY_SECTION.fullmatch(compact):
        return "业务实体"
    return f"{base} ({gloss})" if base and gloss else base


def _normalized_trees(asset: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[tuple[str, str]]]]:
    result = clone_asset_for_understanding_projection(asset)
    root = result.get("document_semantic_trees")
    root_dict = dict(as_dict(root))
    trees = [
        deepcopy(row)
        for row in (as_list(root_dict.get("items")) or as_list(root))
        if isinstance(row, dict)
    ]
    declared: dict[str, list[tuple[str, str]]] = {}
    for tree in trees:
        source_id = text(tree.get("source_id"))
        nodes: list[dict[str, Any]] = []
        for raw in as_list(tree.get("nodes")):
            if not isinstance(raw, dict):
                continue
            node = deepcopy(raw)
            if bool(node.get("semantic_heading")):
                raw_heading = text(node.get("raw_heading") or node.get("title"))
                normalized = _formatted_heading(raw_heading)
                if normalized:
                    node["raw_heading"] = normalized
                    node["title"] = normalized
                node["path_titles"] = [
                    _formatted_heading(value) or text(value)
                    for value in as_list(node.get("path_titles"))
                ]
                base, gloss = _canonical_entity_name(normalized)
                if base and gloss:
                    declared.setdefault(source_id, []).append((base, gloss))
            nodes.append(node)
        tree["nodes"] = nodes
    if isinstance(root, dict):
        root_dict["items"] = trees
        result["document_semantic_trees"] = root_dict
    else:
        result["document_semantic_trees"] = trees
    return result, declared


def _state_heading_surfaces(asset: dict[str, Any]) -> dict[str, list[str]]:
    surfaces: dict[str, list[str]] = {}
    root = asset.get("document_semantic_trees")
    trees = as_list(as_dict(root).get("items")) or as_list(root)
    for tree in trees:
        if not isinstance(tree, dict):
            continue
        source_id = text(tree.get("source_id"))
        for node in as_list(tree.get("nodes")):
            if not isinstance(node, dict) or not bool(node.get("semantic_heading")):
                continue
            heading = text(node.get("raw_heading") or node.get("title"))
            lowered = heading.casefold()
            if not any(marker.casefold() in lowered for marker in _STATE_MARKERS):
                continue
            base, _gloss = _canonical_entity_name(heading)
            surface = base
            for marker in sorted(_STATE_MARKERS, key=len, reverse=True):
                surface = re.sub(re.escape(marker), " ", surface, flags=re.I)
            surface = re.sub(r"[^\w.\-]+", " ", surface, flags=re.UNICODE).strip()
            if surface:
                surfaces.setdefault(source_id, []).append(surface)
    return surfaces


def _bind_state_machines(
    asset: dict[str, Any], declared: dict[str, list[tuple[str, str]]]
) -> list[dict[str, Any]]:
    state_surfaces = _state_heading_surfaces(asset)
    machines: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for raw in as_list(asset.get("state_machines")):
        if not isinstance(raw, dict):
            continue
        machine = deepcopy(raw)
        source_id = text(machine.get("source_id"))
        surfaces = state_surfaces.get(source_id) or []
        declarations = declared.get(source_id) or []
        candidates: list[tuple[str, str]] = []
        for surface in surfaces:
            surface_key = comparison_key(surface)
            for base, gloss in declarations:
                keys = [comparison_key(base), comparison_key(gloss)]
                if surface_key and any(
                    surface_key == key
                    or (len(surface_key) >= 2 and (key.startswith(surface_key) or key.endswith(surface_key)))
                    for key in keys
                    if key
                ):
                    candidates.append((base, surface))
        unique = {comparison_key(base): (base, surface) for base, surface in candidates}
        if len(unique) == 1:
            base, surface = next(iter(unique.values()))
            machine["raw_object"] = text(machine.get("object"))
            machine["object"] = base
            machine["object_surface"] = surface
            machine["object_binding_authority"] = "SOURCE_LOCAL_UNIQUE_ENTITY_HEADING"
            machine["object_binding_scope"] = "LIFECYCLE_BINDING_ONLY"
            machine["automatic_identity_union_allowed"] = False
            bindings.append({
                "source_id": source_id,
                "surface_label": surface,
                "parent_label": base,
                "authority": "SOURCE_LOCAL_UNIQUE_ENTITY_HEADING",
                "scope": "LIFECYCLE_BINDING_ONLY",
                "automatic_identity_union_allowed": False,
            })
        machines.append(machine)
    if machines:
        asset["state_machines"] = machines
    return bindings


def prepare_distinctness_source_asset(
    asset: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized, declared = _normalized_trees(asset)
    state_bindings = _bind_state_machines(normalized, declared)
    prepared, authority = prepare_conflict_governed_source_asset(normalized)
    authority = dict(authority)
    authority["heading_format_normalization"] = {
        "numbered_headings_supported": True,
        "multi_modifier_entity_sections_supported": True,
        "state_machine_binding_scope": "LIFECYCLE_BINDING_ONLY",
        "identity_union_performed": False,
    }
    authority["state_machine_source_bindings"] = state_bindings
    return prepared, authority


def finalize_distinctness_source_recognition(
    recognition: dict[str, Any], authority: dict[str, Any]
) -> dict[str, Any]:
    result = finalize_conflict_governed_source_recognition(recognition, authority)
    result["state_machine_source_bindings"] = deepcopy(
        as_list(authority.get("state_machine_source_bindings"))
    )
    return result


__all__ = [
    "finalize_distinctness_source_recognition",
    "prepare_distinctness_source_asset",
]
