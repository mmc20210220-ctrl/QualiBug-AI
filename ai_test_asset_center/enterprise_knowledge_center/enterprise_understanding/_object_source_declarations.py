"""Read explicit object declarations from the existing semantic document tree."""
from __future__ import annotations

import re
from typing import Any

from ._object_role_evidence import comparison_key
from .schema import as_dict, as_list, dedupe_evidence, is_source_backed_evidence, stable_id, text, unique_text

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
    return unique_text([
        text(match.group(1)),
        *[text(item) for item in re.split(r"[、,/|]", match.group(2))],
    ])


def _relation_endpoints(value: Any) -> tuple[str, str] | None:
    match = _RELATION.fullmatch(text(value))
    if not match:
        return None
    link = text(match.group("link"))
    if not (
        any(symbol in link for symbol in ("→", "←", "->", "<-", "──>", "<──"))
        or re.search(r"(?:^|[^A-Za-z0-9])(?:1|N|M|\*)\s*[:：]\s*(?:1|N|M|\*)(?:$|[^A-Za-z0-9])", link, re.I)
    ):
        return None
    return text(match.group("left")), text(match.group("right"))


def _trees(asset: dict[str, Any]) -> list[dict[str, Any]]:
    root = asset.get("document_semantic_trees")
    return [row for row in (as_list(as_dict(root).get("items")) or as_list(root)) if isinstance(row, dict)]


def source_object_declarations(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Return source-backed object declarations and exact display aliases."""
    rows: list[dict[str, Any]] = []
    relation_keys: set[str] = set()
    for tree in _trees(asset):
        for node in as_list(tree.get("nodes")):
            if not isinstance(node, dict):
                continue
            path = [text(value) for value in as_list(node.get("path_titles")) if text(value)]
            evidence = _evidence(node)
            if not evidence:
                continue
            if bool(node.get("semantic_heading")) and len(path) >= 2 and _ENTITY_SECTION.fullmatch(_compact(path[-2])):
                labels = _labels(node.get("raw_heading") or node.get("title"))
                if labels:
                    rows.append({
                        "declaration_id": text(node.get("node_id")) or stable_id("source_object_heading", labels, evidence),
                        "canonical_label": labels[0],
                        "labels": labels,
                        "evidence": evidence,
                        "authority": "SOURCE_ENTITY_HEADING",
                    })
            elif not bool(node.get("semantic_heading")) and path and _RELATION_SECTION.fullmatch(_compact(path[-1])):
                endpoints = _relation_endpoints(node.get("title"))
                if endpoints:
                    for label in endpoints:
                        relation_keys.add(comparison_key(label))
                        rows.append({
                            "declaration_id": text(node.get("node_id")) or stable_id("source_object_relation", label, evidence),
                            "canonical_label": label,
                            "labels": [label],
                            "evidence": evidence,
                            "authority": "SOURCE_ENTITY_RELATION_ENDPOINT",
                        })

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
        field_key = comparison_key(re.sub(r"(?:_id|Id|ID)$", "", text(field.get("field") or field.get("field_path"))))
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
                evidence = [{
                    "source_id": source_id,
                    "source_locator": locator,
                    "quote": alias,
                    "derivation": "source_entity_relation_field_label",
                }]
        if not evidence:
            continue
        canonical = labels_by_key.get(target_key, target)
        rows.append({
            "declaration_id": text(field.get("field_id")) or stable_id("source_object_field_alias", target, alias),
            "canonical_label": canonical,
            "labels": [canonical, alias],
            "evidence": evidence,
            "authority": "SOURCE_ENTITY_RELATION_FIELD_LABEL",
        })
        aliased_targets.add(target_key)

    by_key: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for row in rows:
        key = (comparison_key(row.get("canonical_label")), tuple(sorted(comparison_key(v) for v in as_list(row.get("labels")) if comparison_key(v))))
        if key[0]:
            by_key.setdefault(key, row)
    return list(by_key.values())


__all__ = ["source_object_declarations"]
