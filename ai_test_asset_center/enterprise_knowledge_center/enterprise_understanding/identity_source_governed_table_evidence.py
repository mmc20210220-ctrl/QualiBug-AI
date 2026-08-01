"""Independent evidence collectors for source-governed table identity."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .identity_types import asset_evidence
from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    is_source_backed_evidence,
    text,
    unique_text,
)

FIELD_AUTHORITY = "SOURCE_BACKED_EXACT_FIELD_OWNERSHIP"
API_AUTHORITY = "SOURCE_BACKED_EXACT_API_RESOURCE_BRIDGE"
_ALLOWED_API_AUTHORITIES = {
    "SOURCE_BACKED_RULE_IMPLEMENTATION",
    "SOURCE_DECLARED_ASSET_RELATION",
}
_CAMEL_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.\[\]-]*")
_PATH_PARAMETER = re.compile(r"^[:{].*[}]?$|^<.*>$")


def source_id(row: dict[str, Any]) -> str:
    value = text(row.get("source_id"))
    return "" if not value or value == "asset" else value


def identifier(value: Any) -> str:
    raw = text(value).replace("[]", "")
    if not raw:
        return ""
    leaf = raw.rsplit(".", 1)[-1]
    leaf = _CAMEL_1.sub(r"\1_\2", leaf)
    leaf = _CAMEL_2.sub(r"\1_\2", leaf)
    return re.sub(r"[^A-Za-z0-9]+", "_", leaf).strip("_").casefold()


def table_rows(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("table_id")) or f"data_tables[{index}]": row
        for index, row in enumerate(as_list(asset.get("data_tables")))
        if isinstance(row, dict)
    }


def _entity_labels(result: dict[str, Any]) -> dict[str, list[str]]:
    return {
        text(row.get("entity_id")): unique_text(
            [
                row.get("canonical_label"),
                *as_list(row.get("labels")),
                *as_list(row.get("aliases")),
            ]
        )
        for row in as_list(result.get("clusters"))
        if isinstance(row, dict) and text(row.get("entity_id"))
    }


def semantic_matches(
    table: dict[str, Any], labels_by_entity: dict[str, list[str]]
) -> dict[str, list[str]]:
    statement = text(
        table.get("description")
        or table.get("business_label")
        or table.get("summary")
    )
    technical = identifier(table.get("name") or table.get("table"))
    matches: dict[str, list[str]] = {}
    for entity_id, labels in labels_by_entity.items():
        selected = [
            label
            for label in unique_text(labels)
            if len(label) >= 2
            and identifier(label) != technical
            and label in statement
        ]
        if selected:
            matches[entity_id] = selected
    return matches


def new_candidate(
    table_ref: str, entity_id: str, authority: str
) -> dict[str, Any]:
    return {
        "table_ref": table_ref,
        "entity_id": entity_id,
        "authority": authority,
        "matched_fields": [],
        "semantic_labels": [],
        "semantic_entity_ids": [],
        "rule_refs": [],
        "fact_refs": [],
        "interface_refs": [],
        "source_ids": [],
        "evidence": [],
    }


def _field_index(
    asset: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[tuple[str, str], list[dict[str, Any]]]]:
    owners: dict[str, set[str]] = defaultdict(set)
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    def add(table_ref: str, value: Any, row: dict[str, Any]) -> None:
        field = identifier(value)
        if not field:
            return
        owners[field].add(table_ref)
        field_ref = (
            text(row.get("field_id"))
            or text(row.get("field_path"))
            or f"{table_ref}.{field}"
        )
        evidence[(table_ref, field)].extend(
            asset_evidence(row, field_ref, "source_backed_database_field")
        )

    for table_ref, table in tables.items():
        for column in as_list(table.get("columns")):
            if isinstance(column, dict):
                add(
                    table_ref,
                    column.get("field")
                    or column.get("name")
                    or column.get("column")
                    or column.get("field_path"),
                    column,
                )
            else:
                add(table_ref, column, table)
        for row in as_list(table.get("field_dictionary")):
            if isinstance(row, dict):
                add(
                    table_ref,
                    row.get("field") or row.get("name") or row.get("field_path"),
                    row,
                )

    refs_by_name: dict[str, set[str]] = defaultdict(set)
    for table_ref, table in tables.items():
        name = text(table.get("name") or table.get("table")).casefold()
        if name:
            refs_by_name[name].add(table_ref)
    unique_name = {
        name: next(iter(refs))
        for name, refs in refs_by_name.items()
        if len(refs) == 1
    }
    for row in as_list(asset.get("field_dictionary")):
        if not isinstance(row, dict):
            continue
        table_ref = text(row.get("table_id")) or unique_name.get(
            text(row.get("table")).casefold(), ""
        )
        if table_ref in tables:
            add(
                table_ref,
                row.get("field") or row.get("name") or row.get("field_path"),
                row,
            )
    return owners, evidence


def collect_field_candidates(
    asset: dict[str, Any],
    result: dict[str, Any],
    tables: dict[str, dict[str, Any]],
    rule_authority: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    owners, field_evidence = _field_index(asset, tables)
    exclusive = {
        field: next(iter(table_refs))
        for field, table_refs in owners.items()
        if len(table_refs) == 1
    }
    labels_by_entity = _entity_labels(result)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for rule in as_list(asset.get("rule_library")):
        if not isinstance(rule, dict):
            continue
        rule_id, rule_source = text(rule.get("rule_id")), source_id(rule)
        governed = as_dict(rule_authority.get(rule_id))
        entity_ids = unique_text(as_list(governed.get("entity_ids")))
        if not rule_id or not rule_source or len(entity_ids) != 1:
            continue
        fields = {
            field
            for raw in _IDENTIFIER.findall(text(rule.get("statement")))
            if (field := identifier(raw)) in exclusive
        }
        table_refs = {exclusive[field] for field in fields}
        if len(table_refs) != 1:
            continue
        table_ref, entity_id = next(iter(table_refs)), entity_ids[0]
        table, table_source = tables[table_ref], source_id(tables[table_ref])
        if not table_source or table_source == rule_source:
            continue
        semantics = semantic_matches(table, labels_by_entity)
        if entity_id not in semantics:
            continue
        row = candidates.setdefault(
            (table_ref, entity_id),
            new_candidate(table_ref, entity_id, FIELD_AUTHORITY),
        )
        row["matched_fields"] = unique_text(
            [*as_list(row.get("matched_fields")), *fields]
        )
        row["semantic_labels"] = unique_text(
            [*as_list(row.get("semantic_labels")), *semantics[entity_id]]
        )
        row["semantic_entity_ids"] = unique_text(
            [*as_list(row.get("semantic_entity_ids")), *semantics]
        )
        row["rule_refs"] = unique_text(
            [*as_list(row.get("rule_refs")), rule_id]
        )
        row["fact_refs"] = unique_text(
            [*as_list(row.get("fact_refs")), *as_list(governed.get("fact_refs"))]
        )
        row["source_ids"] = unique_text(
            [*as_list(row.get("source_ids")), table_source, rule_source]
        )
        row["evidence"] = dedupe_evidence(
            [
                *as_list(row.get("evidence")),
                *asset_evidence(rule, rule_id, "source_backed_exact_field_rule"),
                *as_list(governed.get("evidence")),
                *[
                    item
                    for field in fields
                    for item in field_evidence.get((table_ref, field), [])
                ],
                *asset_evidence(table, table_ref, "source_backed_database_table"),
            ]
        )

    by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates.values():
        if len(unique_text(as_list(row.get("matched_fields")))) < 2:
            continue
        if any(
            is_source_backed_evidence(item)
            for item in as_list(row.get("evidence"))
            if isinstance(item, dict)
        ):
            by_table[text(row.get("table_ref"))].append(row)
    return by_table


def _path_segments(path: Any) -> set[str]:
    segments: set[str] = set()
    for raw in text(path).split("/"):
        segment = raw.strip().split("?", 1)[0].split("#", 1)[0]
        if segment and not _PATH_PARAMETER.match(segment):
            segments.add(segment.casefold())
    return segments


def collect_api_candidates(
    asset: dict[str, Any],
    result: dict[str, Any],
    tables: dict[str, dict[str, Any]],
    bindings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    interfaces = {
        text(row.get("interface_id") or row.get("operation_id")): row
        for row in as_list(asset.get("interfaces"))
        if isinstance(row, dict)
        and text(row.get("interface_id") or row.get("operation_id"))
    }
    labels_by_entity = _entity_labels(result)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        authorities = set(unique_text(as_list(binding.get("identity_authorities"))))
        if (
            text(binding.get("artifact_type")) != "API_OPERATION"
            or text(binding.get("status")).upper() != "RESOLVED"
            or not authorities & _ALLOWED_API_AUTHORITIES
        ):
            continue
        interface_ref, entity_id = (
            text(binding.get("artifact_ref")),
            text(binding.get("entity_id")),
        )
        interface = as_dict(interfaces.get(interface_ref))
        interface_source = source_id(interface)
        if not interface or not entity_id or not interface_source:
            continue
        path_segments = _path_segments(interface.get("path"))
        for table_ref, table in tables.items():
            table_source = source_id(table)
            technical_name = text(table.get("name") or table.get("table"))
            if (
                not table_source
                or table_source == interface_source
                or technical_name.casefold() not in path_segments
            ):
                continue
            semantics = semantic_matches(table, labels_by_entity)
            if entity_id not in semantics:
                continue
            row = candidates.setdefault(
                (table_ref, entity_id),
                new_candidate(table_ref, entity_id, API_AUTHORITY),
            )
            row["semantic_labels"] = unique_text(
                [*as_list(row.get("semantic_labels")), *semantics[entity_id]]
            )
            row["semantic_entity_ids"] = unique_text(
                [*as_list(row.get("semantic_entity_ids")), *semantics]
            )
            row["interface_refs"] = unique_text(
                [*as_list(row.get("interface_refs")), interface_ref]
            )
            row["rule_refs"] = unique_text(
                [*as_list(row.get("rule_refs")), *as_list(binding.get("source_rule_refs"))]
            )
            row["fact_refs"] = unique_text(
                [*as_list(row.get("fact_refs")), *as_list(binding.get("source_fact_refs"))]
            )
            row["source_ids"] = unique_text(
                [*as_list(row.get("source_ids")), table_source, interface_source]
            )
            row["evidence"] = dedupe_evidence(
                [
                    *as_list(row.get("evidence")),
                    *as_list(binding.get("evidence")),
                    *asset_evidence(interface, interface_ref, "source_backed_api_resource"),
                    *asset_evidence(table, table_ref, "source_backed_database_table"),
                ]
            )

    by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates.values():
        if any(
            is_source_backed_evidence(item)
            for item in as_list(row.get("evidence"))
            if isinstance(item, dict)
        ):
            by_table[text(row.get("table_ref"))].append(row)
    return by_table


__all__ = [
    "API_AUTHORITY",
    "FIELD_AUTHORITY",
    "collect_api_candidates",
    "collect_field_candidates",
    "table_rows",
]
