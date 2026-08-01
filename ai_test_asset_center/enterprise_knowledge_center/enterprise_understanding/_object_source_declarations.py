"""Read source-backed object declarations before object-type governance.

This module is the single authority that interprets *source structure* as an
object declaration.  The candidate collector remains responsible for role
classification and never treats a technical table inventory as self-authorizing.
"""
from __future__ import annotations

import re
from typing import Any

from ._object_role_evidence import comparison_key
from .identity_types import asset_evidence
from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    is_source_backed_evidence,
    stable_id,
    text,
    unique_text,
)

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
_DECLARATION = re.compile(r"^(.+?)\s*[（(]([^）)]+)[）)]\s*$")
_ENTITY_SECTION = re.compile(
    r"^(?:(?:核心|主要|关键|业务|领域)?(?:实体|对象)(?:定义|清单|列表)?|"
    r"(?:(?:core|primary|key|business|domain))?(?:entities|entitydefinitions|entityinventory|entitylist|objects|objectdefinitions|objectlist))$",
    re.I,
)
_RELATION_SECTION = re.compile(
    r"^(?:(?:(?:业务|领域|核心))?(?:实体|对象)(?:关系|关联|关系图|关联图)|"
    r"(?:(?:business|domain|core))?(?:entity|object)(?:relationship|relationships|relation|relations|graph))$",
    re.I,
)
_RELATION = re.compile(
    rf"^\s*(?P<left>[A-Za-z][A-Za-z0-9_.-]*|[{_CJK}]{{2,}})\s+"
    rf"(?P<link>.+?)\s+(?P<right>[A-Za-z][A-Za-z0-9_.-]*|[{_CJK}]{{2,}})\s*$"
)
_FK = re.compile(
    rf"(?:^|\b)(?:FK|FOREIGN\s+KEY)\s*(?:→|->|:|=)?\s*"
    rf"(?P<target>[A-Za-z][A-Za-z0-9_.-]*|[{_CJK}]{{2,}})",
    re.I,
)
_ALIAS = re.compile(rf"^[{_CJK}A-Za-z0-9_.-]+$")
_QUALIFIER = re.compile(
    r"^(?:所属|所在|关联|对应|来源|目标|父级|上级|下级|当前|默认|相关|原)|"
    r"^(?:related|associated|parent|source|target|current|owning|owner)\b",
    re.I,
)
_ENTITY_INVENTORY_DERIVATION = "entity_inventory_table"
_API_OPERATION = re.compile(
    r"^(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?P<path>/\S+)$",
    re.I,
)
_TECHNICAL_DESCRIPTION_SUFFIX = re.compile(
    r"(?:核心)?(?:主表|数据表|业务表|实体表|信息表|明细表|表)$", re.I
)
_DESCRIPTION_DELIMITER = re.compile(r"[，,；;。]")
_COMPOSITE_DELIMITER = re.compile(r"\s*(?:与|和|及|/|／|、|&|＆)\s*")
_WORD = re.compile(r"[A-Za-z0-9]+")


def _compact(value: Any) -> str:
    return comparison_key(re.sub(rf"[^\w{_CJK}]+", "", text(value)))


def _evidence(node: dict[str, Any]) -> list[dict[str, Any]]:
    row = as_dict(node.get("evidence"))
    rows = dedupe_evidence([row] if row else [])
    return rows if any(is_source_backed_evidence(item) for item in rows) else []


def _labels(value: Any) -> list[str]:
    raw = text(value)
    match = _DECLARATION.fullmatch(raw)
    if not match:
        return [raw] if raw else []
    return unique_text(
        [
            text(match.group(1)),
            *[text(item) for item in re.split(r"[、,/|]", match.group(2))],
        ]
    )


def _relation_endpoints(value: Any) -> tuple[str, str] | None:
    match = _RELATION.fullmatch(text(value))
    if not match:
        return None
    link = text(match.group("link"))
    if not (
        any(symbol in link for symbol in ("→", "←", "->", "<-", "──>", "<──"))
        or re.search(
            r"(?:^|[^A-Za-z0-9])(?:1|N|M|\*)\s*[:：]\s*(?:1|N|M|\*)(?:$|[^A-Za-z0-9])",
            link,
            re.I,
        )
    ):
        return None
    return text(match.group("left")), text(match.group("right"))


def _trees(asset: dict[str, Any]) -> list[dict[str, Any]]:
    root = asset.get("document_semantic_trees")
    return [
        row
        for row in (as_list(as_dict(root).get("items")) or as_list(root))
        if isinstance(row, dict)
    ]


def _source_types(asset: dict[str, Any]) -> dict[str, str]:
    return {
        text(row.get("source_id")): text(row.get("source_type")).casefold()
        for row in as_list(asset.get("source_inventory"))
        if isinstance(row, dict) and text(row.get("source_id"))
    }


def _inventory_row(row: dict[str, Any]) -> bool:
    derivations = {
        text(row.get("derivation")),
        *[text(value) for value in as_list(row.get("derivations"))],
    }
    return _ENTITY_INVENTORY_DERIVATION in derivations


def _explicit_entity_sources(asset: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for tree in _trees(asset):
        nodes = [row for row in as_list(tree.get("nodes")) if isinstance(row, dict)]
        if any(
            bool(node.get("semantic_heading"))
            and _ENTITY_SECTION.fullmatch(_compact(node.get("raw_heading") or node.get("title")))
            for node in nodes
        ):
            source_id = text(tree.get("source_id"))
            if not source_id:
                for node in nodes:
                    source_id = text(as_dict(node.get("evidence")).get("source_id"))
                    if source_id:
                        break
            if source_id:
                result.add(source_id)
    return result


def _explicit_inventory_row(row: dict[str, Any], explicit_sources: set[str]) -> bool:
    if not _inventory_row(row):
        return False
    if text(row.get("source_id")) in explicit_sources:
        return True
    locator = _compact(row.get("source_locator"))
    return bool(
        locator
        and any(
            marker in locator
            for marker in (
                "coreentities",
                "entityinventory",
                "businessobjects",
                "核心实体",
                "业务实体",
                "业务对象",
            )
        )
    )


def _description_labels(row: dict[str, Any], *, aligned_resource: bool) -> list[str]:
    raw = _DESCRIPTION_DELIMITER.split(text(row.get("description")), maxsplit=1)[0].strip()
    if not raw:
        return []
    normalized = _TECHNICAL_DESCRIPTION_SUFFIX.sub("", raw).strip()
    normalized = normalized or raw

    parts = [text(value) for value in _COMPOSITE_DELIMITER.split(normalized) if text(value)]
    if len(parts) <= 1:
        return [normalized] if _ALIAS.fullmatch(normalized) else []
    if not aligned_resource:
        return []

    field_terms: set[str] = set()
    for field in as_list(row.get("field_dictionary")):
        if not isinstance(field, dict):
            continue
        for value in (
            field.get("field"),
            field.get("name"),
            field.get("description"),
        ):
            key = comparison_key(value)
            if key:
                field_terms.add(key)
    retained = [
        value
        for value in parts
        if comparison_key(value) not in field_terms and _ALIAS.fullmatch(value)
    ]
    return unique_text(retained)


def _resource_token(value: Any) -> str:
    return "".join(_WORD.findall(text(value))).casefold()


def source_name_tokens(value: Any) -> list[str]:
    return [token.casefold() for token in _WORD.findall(text(value))]


def source_singular_forms(value: str) -> set[str]:
    values = {value}
    if value.endswith("ies") and len(value) > 3:
        values.add(value[:-3] + "y")
    if value.endswith("es") and len(value) > 2:
        values.add(value[:-2])
    if value.endswith("s") and len(value) > 1:
        values.add(value[:-1])
    return values


def _resource_matches_table(resource: Any, table_name: Any) -> bool:
    resource_key = _resource_token(resource)
    tokens = source_name_tokens(table_name)
    if not resource_key or not tokens:
        return False
    return resource_key in source_singular_forms(tokens[0])


def _api_resource_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    inventory_rows = [
        row
        for row in as_list(asset.get("data_tables"))
        if isinstance(row, dict) and _inventory_row(row)
    ]
    result: list[dict[str, Any]] = []
    for tree in _trees(asset):
        nodes = [row for row in as_list(tree.get("nodes")) if isinstance(row, dict)]
        for node in nodes:
            path = [text(value) for value in as_list(node.get("path_titles")) if text(value)]
            if not bool(node.get("semantic_heading")) or len(path) != 2:
                continue
            heading = text(node.get("raw_heading") or node.get("title"))
            evidence = _evidence(node)
            if not heading or not evidence:
                continue
            operations = [
                text(descendant.get("raw_heading") or descendant.get("title"))
                for descendant in nodes
                if isinstance(descendant, dict)
                and bool(descendant.get("semantic_heading"))
                and len(as_list(descendant.get("path_titles"))) >= 3
                and [
                    text(value)
                    for value in as_list(descendant.get("path_titles"))[:2]
                    if text(value)
                ]
                == path
            ]
            operations = [value for value in operations if _API_OPERATION.fullmatch(value)]
            aligned = [
                row
                for row in inventory_rows
                if _resource_matches_table(heading, row.get("name") or row.get("table"))
            ]
            if not operations or not aligned:
                continue
            result.append(
                {
                    "declaration_id": text(node.get("node_id"))
                    or stable_id("source_api_resource", heading, operations),
                    "canonical_label": heading,
                    "labels": [heading],
                    "evidence": evidence,
                    "authority": "SOURCE_API_RESOURCE_HEADING",
                    "surface_suffix_discovery_allowed": False,
                    "surface_prefix_discovery_allowed": False,
                    "aligned_table_names": unique_text(
                        [row.get("name") or row.get("table") for row in aligned]
                    ),
                }
            )
    return result


def _table_declaration_rows(
    asset: dict[str, Any],
    api_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit_sources = _explicit_entity_sources(asset)
    api_by_table: dict[str, dict[str, Any]] = {}
    for api_row in api_rows:
        for table_name in as_list(api_row.get("aligned_table_names")):
            api_by_table[comparison_key(table_name)] = api_row

    rows: list[dict[str, Any]] = []
    for row in as_list(asset.get("data_tables")):
        if not isinstance(row, dict) or not _inventory_row(row):
            continue
        table_name = text(row.get("name") or row.get("table"))
        ref = text(row.get("table_id")) or f"data_tables[{table_name}]"
        evidence = asset_evidence(row, ref, "source_object_declaration")
        if not evidence:
            continue

        strong = _explicit_inventory_row(row, explicit_sources)
        api_row = api_by_table.get(comparison_key(table_name))
        aligned = api_row is not None
        description_labels = _description_labels(row, aligned_resource=aligned)
        if strong:
            canonical = table_name or (description_labels[0] if description_labels else "")
            labels = unique_text([canonical, *description_labels])
            authority = "SOURCE_ENTITY_INVENTORY"
            suffix_allowed = True
            prefix_allowed = False
        else:
            canonical = (
                text(api_row.get("canonical_label"))
                if api_row
                else (description_labels[0] if description_labels else "")
            )
            labels = unique_text([canonical, *description_labels])
            authority = (
                "SOURCE_API_ALIGNED_TABLE_LABEL"
                if api_row
                else "SOURCE_TABLE_BUSINESS_LABEL"
            )
            # Weak technical inventories may provide a human business label, but
            # never authorize the raw table name.  Standalone descriptions may
            # expose a conservative suffix (收货地址 -> 地址); API-aligned
            # descriptions may expose a conservative prefix (退款售后 -> 退款).
            suffix_allowed = bool(description_labels and not aligned)
            prefix_allowed = bool(description_labels and aligned)
        if not canonical or not labels:
            continue
        rows.append(
            {
                "declaration_id": stable_id(
                    "source_table_object_declaration", ref, canonical, labels
                ),
                "canonical_label": canonical,
                "labels": labels,
                "evidence": evidence,
                "authority": authority,
                "surface_suffix_discovery_allowed": suffix_allowed,
                "surface_prefix_discovery_allowed": prefix_allowed,
                "technical_table_name": table_name,
            }
        )
    return rows


def _merge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        canonical = text(row.get("canonical_label"))
        key = comparison_key(canonical)
        if not key:
            continue
        prior = merged.get(key)
        if prior is None:
            prior = dict(row)
            prior["labels"] = unique_text(as_list(row.get("labels")))
            prior["evidence"] = dedupe_evidence(as_list(row.get("evidence")))
            prior["authorities"] = unique_text(
                [row.get("authority"), *as_list(row.get("authorities"))]
            )
            merged[key] = prior
            continue
        prior["labels"] = unique_text(
            [*as_list(prior.get("labels")), *as_list(row.get("labels"))]
        )
        prior["evidence"] = dedupe_evidence(
            [*as_list(prior.get("evidence")), *as_list(row.get("evidence"))]
        )
        prior["authorities"] = unique_text(
            [
                *as_list(prior.get("authorities")),
                row.get("authority"),
                *as_list(row.get("authorities")),
            ]
        )
        prior["surface_suffix_discovery_allowed"] = bool(
            prior.get("surface_suffix_discovery_allowed")
            or row.get("surface_suffix_discovery_allowed")
        )
        prior["surface_prefix_discovery_allowed"] = bool(
            prior.get("surface_prefix_discovery_allowed")
            or row.get("surface_prefix_discovery_allowed")
        )
    return list(merged.values())


def source_object_declarations(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Return source-backed object declarations and exact display aliases."""

    rows: list[dict[str, Any]] = []
    relation_keys: set[str] = set()
    for tree in _trees(asset):
        for node in as_list(tree.get("nodes")):
            if not isinstance(node, dict):
                continue
            path = [
                text(value)
                for value in as_list(node.get("path_titles"))
                if text(value)
            ]
            evidence = _evidence(node)
            if not evidence:
                continue
            if (
                bool(node.get("semantic_heading"))
                and len(path) >= 2
                and _ENTITY_SECTION.fullmatch(_compact(path[-2]))
            ):
                labels = _labels(node.get("raw_heading") or node.get("title"))
                if labels:
                    rows.append(
                        {
                            "declaration_id": text(node.get("node_id"))
                            or stable_id("source_object_heading", labels, evidence),
                            "canonical_label": labels[0],
                            "labels": labels,
                            "evidence": evidence,
                            "authority": "SOURCE_ENTITY_HEADING",
                            "surface_suffix_discovery_allowed": True,
                            "surface_prefix_discovery_allowed": False,
                        }
                    )
            elif (
                not bool(node.get("semantic_heading"))
                and path
                and _RELATION_SECTION.fullmatch(_compact(path[-1]))
            ):
                endpoints = _relation_endpoints(node.get("title"))
                if endpoints:
                    for label in endpoints:
                        relation_keys.add(comparison_key(label))
                        rows.append(
                            {
                                "declaration_id": text(node.get("node_id"))
                                or stable_id(
                                    "source_object_relation", label, evidence
                                ),
                                "canonical_label": label,
                                "labels": [label],
                                "evidence": evidence,
                                "authority": "SOURCE_ENTITY_RELATION_ENDPOINT",
                                "surface_suffix_discovery_allowed": False,
                                "surface_prefix_discovery_allowed": False,
                            }
                        )

    api_rows = _api_resource_rows(asset)
    rows.extend(api_rows)
    rows.extend(_table_declaration_rows(asset, api_rows))

    labels_by_key = {
        comparison_key(label): label
        for row in rows
        for label in as_list(row.get("labels"))
        if comparison_key(label)
    }
    aliased_targets = {
        comparison_key(row.get("canonical_label"))
        for row in rows
        if len(as_list(row.get("labels"))) > 1
    }
    for field in as_list(asset.get("field_dictionary")):
        if not isinstance(field, dict):
            continue
        match = _FK.search(text(field.get("type") or field.get("constraint")))
        target = text(match.group("target")) if match else ""
        target_key = comparison_key(target)
        field_key = comparison_key(
            re.sub(
                r"(?:_id|Id|ID)$",
                "",
                text(field.get("field") or field.get("field_path")),
            )
        )
        alias = text(field.get("description"))
        if (
            not target_key
            or target_key not in relation_keys
            or target_key in aliased_targets
            or field_key != target_key
            or not alias
            or len(alias) > 40
            or _QUALIFIER.search(alias)
            or not _ALIAS.fullmatch(alias)
        ):
            continue
        evidence = dedupe_evidence(as_list(field.get("evidence")))
        if not evidence:
            source_id = text(field.get("source_id"))
            locator = text(field.get("source_locator") or field.get("field_id"))
            if source_id and locator:
                evidence = [
                    {
                        "source_id": source_id,
                        "source_locator": locator,
                        "quote": alias,
                        "derivation": "source_entity_relation_field_label",
                    }
                ]
        if not evidence:
            continue
        canonical = labels_by_key.get(target_key, target)
        rows.append(
            {
                "declaration_id": text(field.get("field_id"))
                or stable_id("source_object_field_alias", target, alias),
                "canonical_label": canonical,
                "labels": [canonical, alias],
                "evidence": evidence,
                "authority": "SOURCE_ENTITY_RELATION_FIELD_LABEL",
                "surface_suffix_discovery_allowed": False,
                "surface_prefix_discovery_allowed": False,
            }
        )
        aliased_targets.add(target_key)

    return _merge_rows(rows)


__all__ = ["source_name_tokens", "source_object_declarations", "source_singular_forms"]
