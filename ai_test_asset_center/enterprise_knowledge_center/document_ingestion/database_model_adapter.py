"""Source-preserving adapters for enterprise database design artifacts.

The adapter extracts technical database structure only: schemas, tables, columns, keys,
indexes and declared relationships. It never infers business objects or workflow from table
names or diagram order. SQL and spreadsheet data dictionaries keep their existing canonical
paths; their semantic projection joins this same database-model contract downstream.
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from xml.etree import ElementTree

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .contract import (
    AdapterMatch,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_EXTRACTION,
    DocumentAdapter,
    DocumentSource,
    MODE_PRIMARY,
)

DATABASE_MODEL_STRUCTURE_SCHEMA = "qualibug.database-model-structure.v1"
_MAX_MODEL_XML_BYTES = 50 * 1024 * 1024
_MAX_MODEL_OBJECTS = 500_000
_MAX_MWB_MEMBERS = 20_000


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _local(tag: Any) -> str:
    value = _text(tag)
    return value.rsplit("}", 1)[-1].split(":", 1)[-1]


def _direct_child(node: ElementTree.Element, names: Iterable[str]) -> ElementTree.Element | None:
    wanted = set(names)
    for child in list(node):
        if _local(child.tag) in wanted:
            return child
    return None


def _child_text(node: ElementTree.Element, *names: str) -> str:
    # Names are ordered by priority (e.g. "Code" before "Name"): the physical
    # model code is the canonical name, the display label is only a fallback.
    # Document order must not override that priority.
    for name in names:
        for child in list(node):
            if _local(child.tag) == name:
                value = _text(child.text)
                if value:
                    return value
    return ""


def _bool(value: Any) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "y", "是", "必填", "mandatory"}


def _xml_root(data: bytes) -> ElementTree.Element:
    if len(data) > _MAX_MODEL_XML_BYTES:
        raise ValueError("DATABASE_MODEL_XML_SIZE_LIMIT_EXCEEDED")
    parser = ElementTree.XMLParser()
    return ElementTree.fromstring(data, parser=parser)


def _blocked_ir(
    source: DocumentSource,
    *,
    model_kind: str,
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    gap = {
        "kind": reason_code,
        "reason_code": reason_code,
        "count": 1,
        "status": "SOURCE_DATABASE_MODEL_NOT_PARSED",
        "severity": "P0",
        "blocks_formal_understanding": True,
        "included_in_plain_text_authority": False,
        "source_locator": f"{source.filename}#whole-file",
        "detail": detail[:500],
    }
    receipt = {
        "schema": STRUCTURE_RECEIPT_SCHEMA,
        "status": "BLOCKED",
        "format": source.suffix.lstrip(".") or "database_model",
        "block_count": 0,
        "source_traceability_rate": 1.0,
        "block_type_distribution": {},
        "section_count": 0,
        "table_count": 0,
        "unsupported_content_count": 1,
        "critical_unsupported_content_count": 1,
        "unsupported_content": [gap],
        "database_model_kind": model_kind,
        "database_model_structure_available": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }
    return {
        "schema": DOCUMENT_IR_SCHEMA,
        "format": source.suffix.lstrip(".") or "database_model",
        "filename": source.filename,
        "plain_text": "",
        "blocks": [],
        "sections": [],
        "tables": [],
        "pages": [],
        "unsupported_content": [gap],
        "artifact_structure": {
            "schema": DATABASE_MODEL_STRUCTURE_SCHEMA,
            "artifact_kind": "database_model",
            "database_model_kind": model_kind,
            "status": "BLOCKED",
            "tables": [],
            "relationships": [],
            "indexes": [],
        },
        "structure_receipt": receipt,
    }


def _model_ir(
    source: DocumentSource,
    *,
    model_kind: str,
    model_name: str,
    database_family: str,
    schemas: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    unsupported: list[dict[str, Any]] | None = None,
    parser_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gaps = [dict(row) for row in (unsupported or []) if isinstance(row, dict)]
    blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    document_tables: list[dict[str, Any]] = []
    plain_lines: list[str] = []
    order = 0

    model_locator = f"{source.filename}#database-model"
    order += 1
    model_block_id = _stable_id(
        "database_model", source.source_id, source.content_hash, model_kind, model_name
    )
    blocks.append(
        {
            "block_id": model_block_id,
            "type": "HEADING",
            "parent_id": "",
            "order": order,
            "region": "body",
            "level": 1,
            "text": model_name or database_family or "Database Model",
            "source_locator": model_locator,
            "node_kind": "DATABASE_MODEL",
            "database_model_kind": model_kind,
            "database_family": database_family,
            "structure_evidence": {"method": f"native_{model_kind}_model"},
        }
    )
    sections.append(
        {
            "block_id": model_block_id,
            "level": 1,
            "title": model_name or database_family or "Database Model",
            "source_locator": model_locator,
        }
    )
    plain_lines.append(f"# {model_name or database_family or 'Database Model'}")

    for table_index, table in enumerate(tables, start=1):
        table_name = _text(table.get("name") or table.get("code")) or f"table_{table_index}"
        schema_name = _text(table.get("schema"))
        table_locator = _text(table.get("source_locator")) or (
            f"{source.filename}#database-table={quote(table_name, safe='')}"
        )
        order += 1
        table_block_id = _stable_id(
            "database_table",
            source.source_id,
            source.content_hash,
            schema_name,
            table_name,
            table_locator,
        )
        table["block_id"] = table_block_id
        table["source_locator"] = table_locator
        blocks.append(
            {
                "block_id": table_block_id,
                "type": "HEADING",
                "parent_id": model_block_id,
                "order": order,
                "region": "body",
                "level": 2,
                "text": f"{schema_name + '.' if schema_name else ''}{table_name}",
                "source_locator": table_locator,
                "node_kind": "DATABASE_TABLE",
                "schema_name": schema_name,
                "table_name": table_name,
                "table_kind": _text(table.get("kind") or "table"),
                "comment": _text(table.get("comment")),
                "structure_evidence": {"method": f"native_{model_kind}_table"},
            }
        )
        sections.append(
            {
                "block_id": table_block_id,
                "level": 2,
                "title": table_name,
                "source_locator": table_locator,
            }
        )
        plain_lines.append(f"## {schema_name + '.' if schema_name else ''}{table_name}")
        child_ids: list[str] = []
        for column_index, column in enumerate(table.get("columns") or [], start=1):
            if not isinstance(column, dict):
                continue
            column_name = _text(column.get("name") or column.get("code")) or f"column_{column_index}"
            locator = _text(column.get("source_locator")) or (
                f"{table_locator};column={quote(column_name, safe='')}"
            )
            order += 1
            block_id = _stable_id(
                "database_column",
                source.source_id,
                source.content_hash,
                schema_name,
                table_name,
                column_name,
                locator,
            )
            column["block_id"] = block_id
            column["source_locator"] = locator
            child_ids.append(block_id)
            description = _text(column.get("comment") or column.get("description"))
            value = "; ".join(
                part
                for part in [
                    f"column={column_name}",
                    f"type={_text(column.get('data_type'))}" if _text(column.get("data_type")) else "",
                    f"nullable={str(bool(column.get('nullable', True))).lower()}",
                    f"primary_key={str(bool(column.get('primary_key'))).lower()}",
                    f"unique={str(bool(column.get('unique'))).lower()}",
                    f"default={_text(column.get('default'))}" if _text(column.get("default")) else "",
                    f"comment={description}" if description else "",
                ]
                if part
            )
            blocks.append(
                {
                    "block_id": block_id,
                    "type": "KEY_VALUE",
                    "parent_id": table_block_id,
                    "order": order,
                    "region": "body",
                    "text": value or column_name,
                    "source_locator": locator,
                    "node_kind": "DATABASE_COLUMN",
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "column_name": column_name,
                    "data_type": _text(column.get("data_type")),
                    "nullable": bool(column.get("nullable", True)),
                    "primary_key": bool(column.get("primary_key")),
                    "unique": bool(column.get("unique")),
                    "auto_increment": bool(column.get("auto_increment")),
                    "default": _text(column.get("default")),
                    "comment": description,
                    "ordinal_position": int(column.get("ordinal_position") or column_index),
                    "structure_evidence": {"method": f"native_{model_kind}_column"},
                }
            )
            plain_lines.append(value or column_name)
        document_tables.append(
            {
                "block_id": table_block_id,
                "type": "DATABASE_TABLE",
                "source_locator": table_locator,
                "schema_name": schema_name,
                "table_name": table_name,
                "child_block_ids": child_ids,
                "column_block_ids": child_ids,
                "header_semantics_confirmed": True,
                "document_order_is_business_flow": False,
            }
        )

    table_lookup = {
        (_text(row.get("schema")), _text(row.get("name") or row.get("code"))): row
        for row in tables
    }
    for relationship_index, relationship in enumerate(relationships, start=1):
        if not isinstance(relationship, dict):
            continue
        child_table = _text(relationship.get("child_table"))
        parent_table = _text(relationship.get("parent_table"))
        child_schema = _text(relationship.get("child_schema"))
        parent_schema = _text(relationship.get("parent_schema"))
        locator = _text(relationship.get("source_locator")) or (
            f"{source.filename}#database-relationship={relationship_index}"
        )
        order += 1
        block_id = _stable_id(
            "database_relationship",
            source.source_id,
            source.content_hash,
            relationship.get("name"),
            child_schema,
            child_table,
            parent_schema,
            parent_table,
            locator,
        )
        relationship["block_id"] = block_id
        relationship["source_locator"] = locator
        text_value = (
            f"{child_schema + '.' if child_schema else ''}{child_table}"
            f"({', '.join(relationship.get('child_columns') or [])}) -> "
            f"{parent_schema + '.' if parent_schema else ''}{parent_table}"
            f"({', '.join(relationship.get('parent_columns') or [])})"
        )
        parent = table_lookup.get((child_schema, child_table), {}).get("block_id", model_block_id)
        blocks.append(
            {
                "block_id": block_id,
                "type": "KEY_VALUE",
                "parent_id": parent,
                "order": order,
                "region": "body",
                "text": text_value,
                "source_locator": locator,
                "node_kind": "DATABASE_RELATIONSHIP",
                "relationship_name": _text(relationship.get("name")),
                "child_schema": child_schema,
                "child_table": child_table,
                "child_columns": list(relationship.get("child_columns") or []),
                "parent_schema": parent_schema,
                "parent_table": parent_table,
                "parent_columns": list(relationship.get("parent_columns") or []),
                "delete_rule": _text(relationship.get("delete_rule")),
                "update_rule": _text(relationship.get("update_rule")),
                "structure_evidence": {"method": f"native_{model_kind}_relationship"},
            }
        )
        plain_lines.append(text_value)

    critical = sum(
        int(row.get("count") or 0)
        for row in gaps
        if bool(row.get("blocks_formal_understanding"))
    )
    status = "BLOCKED" if critical else "PARTIAL" if gaps else "COMPLETE"
    block_counts = Counter(_text(row.get("type")) for row in blocks)
    receipt = {
        "schema": STRUCTURE_RECEIPT_SCHEMA,
        "status": status,
        "format": source.suffix.lstrip(".") or model_kind,
        "block_count": len(blocks),
        "source_traceability_rate": 1.0 if blocks else 0.0,
        "block_type_distribution": dict(block_counts),
        "section_count": len(sections),
        "table_count": len(tables),
        "column_count": sum(len(row.get("columns") or []) for row in tables),
        "relationship_count": len(relationships),
        "index_count": len(indexes),
        "schema_count": len(schemas),
        "unsupported_content_count": sum(int(row.get("count") or 0) for row in gaps),
        "critical_unsupported_content_count": critical,
        "unsupported_content": gaps,
        "database_model_kind": model_kind,
        "database_model_structure_available": True,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
        **dict(parser_metadata or {}),
    }
    artifact_structure = {
        "schema": DATABASE_MODEL_STRUCTURE_SCHEMA,
        "artifact_kind": "database_model",
        "database_model_kind": model_kind,
        "status": status,
        "model_name": model_name,
        "database_family": database_family,
        "schemas": schemas,
        "tables": tables,
        "relationships": relationships,
        "indexes": indexes,
        "table_count": len(tables),
        "column_count": sum(len(row.get("columns") or []) for row in tables),
        "relationship_count": len(relationships),
        "index_count": len(indexes),
        "business_semantics_added": False,
        "diagram_order_is_business_flow": False,
    }
    return {
        "schema": DOCUMENT_IR_SCHEMA,
        "format": source.suffix.lstrip(".") or model_kind,
        "filename": source.filename,
        "plain_text": "\n".join(plain_lines).strip(),
        "blocks": blocks,
        "sections": sections,
        "tables": document_tables,
        "pages": [],
        "unsupported_content": gaps,
        "artifact_structure": artifact_structure,
        "structure_receipt": receipt,
    }


def _pdm_ref(node: ElementTree.Element | None, object_name: str) -> str:
    if node is None:
        return ""
    for candidate in node.iter():
        if _local(candidate.tag) == object_name:
            return _text(candidate.attrib.get("Ref") or candidate.attrib.get("Id"))
    return ""


def _pdm_refs(node: ElementTree.Element | None, object_name: str) -> list[str]:
    if node is None:
        return []
    return [
        _text(candidate.attrib.get("Ref") or candidate.attrib.get("Id"))
        for candidate in node.iter()
        if _local(candidate.tag) == object_name
        and _text(candidate.attrib.get("Ref") or candidate.attrib.get("Id"))
    ]


def _parse_powerdesigner_pdm(source: DocumentSource) -> dict[str, Any]:
    stripped = source.data.lstrip()
    if not stripped.startswith(b"<") and not stripped.startswith(b"<?xml"):
        return _blocked_ir(
            source,
            model_kind="powerdesigner_pdm_binary",
            reason_code="POWERDESIGNER_BINARY_PDM_UNSUPPORTED",
            detail=(
                "PowerDesigner models may be stored as binary or XML. This source is not XML; "
                "export/save it as an XML model or provide generated DDL."
            ),
        )
    try:
        root = _xml_root(source.data)
    except Exception as exc:
        return _blocked_ir(
            source,
            model_kind="powerdesigner_pdm_xml",
            reason_code="POWERDESIGNER_PDM_XML_INVALID",
            detail=f"{type(exc).__name__}: {exc}",
        )

    model_node = next(
        (
            node
            for node in root.iter()
            if _local(node.tag) in {"Model", "PhysicalDataModel"}
            and _text(node.attrib.get("Id"))
        ),
        root,
    )
    model_name = _child_text(model_node, "Name", "Code") or Path(source.filename).stem
    database_family = _child_text(model_node, "DBMS", "TargetModel", "TargetDBMS")
    object_count = 0
    tables: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    table_by_id: dict[str, dict[str, Any]] = {}
    column_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    key_columns: dict[str, list[str]] = {}
    primary_key_by_table: dict[str, str] = {}

    for node in root.iter():
        if _local(node.tag) not in {"Table", "View"} or not _text(node.attrib.get("Id")):
            continue
        object_count += 1
        if object_count > _MAX_MODEL_OBJECTS:
            return _blocked_ir(
                source,
                model_kind="powerdesigner_pdm_xml",
                reason_code="DATABASE_MODEL_OBJECT_LIMIT_EXCEEDED",
                detail=f"model object limit {_MAX_MODEL_OBJECTS} exceeded",
            )
        table_id = _text(node.attrib.get("Id"))
        table_name = _child_text(node, "Code", "Name") or table_id
        locator = f"{source.filename}#pdm-object={_local(node.tag)};id={quote(table_id, safe='')}"
        table = {
            "object_id": table_id,
            "name": table_name,
            "display_name": _child_text(node, "Name"),
            "schema": _child_text(node, "Owner", "Schema"),
            "kind": "view" if _local(node.tag) == "View" else "table",
            "comment": _child_text(node, "Comment", "Description"),
            "source_locator": locator,
            "columns": [],
        }
        for column in node.iter():
            if _local(column.tag) != "Column" or not _text(column.attrib.get("Id")):
                continue
            column_id = _text(column.attrib.get("Id"))
            column_name = _child_text(column, "Code", "Name") or column_id
            column_row = {
                "object_id": column_id,
                "name": column_name,
                "display_name": _child_text(column, "Name"),
                "data_type": _child_text(column, "DataType", "PhysicalDataType"),
                "length": _child_text(column, "Length"),
                "precision": _child_text(column, "Precision"),
                "scale": _child_text(column, "Scale"),
                "nullable": not _bool(_child_text(column, "Mandatory")),
                "default": _child_text(column, "DefaultValue", "Default"),
                "identity": _bool(_child_text(column, "Identity")),
                "auto_increment": _bool(_child_text(column, "Identity")),
                "comment": _child_text(column, "Comment", "Description"),
                "source_locator": f"{locator};column-id={quote(column_id, safe='')}",
            }
            table["columns"].append(column_row)
            column_by_id[column_id] = (table, column_row)
        for key in node.iter():
            if _local(key.tag) != "Key" or not _text(key.attrib.get("Id")):
                continue
            key_id = _text(key.attrib.get("Id"))
            collection = _direct_child(key, {"Key.Columns", "Columns"})
            key_columns[key_id] = _pdm_refs(collection, "Column")
        primary = _direct_child(node, {"PrimaryKey"})
        primary_key_by_table[table_id] = _pdm_ref(primary, "Key")
        for index_node in node.iter():
            if _local(index_node.tag) != "Index" or not _text(index_node.attrib.get("Id")):
                continue
            index_id = _text(index_node.attrib.get("Id"))
            collection = _direct_child(index_node, {"Index.Columns", "Columns"})
            column_ids = _pdm_refs(collection, "Column")
            indexes.append(
                {
                    "object_id": index_id,
                    "name": _child_text(index_node, "Code", "Name") or index_id,
                    "table": table_name,
                    "columns": [
                        _text(column_by_id.get(value, ({}, {}))[1].get("name"))
                        for value in column_ids
                        if value in column_by_id
                    ],
                    "unique": _bool(_child_text(index_node, "Unique")),
                    "source_locator": f"{locator};index-id={quote(index_id, safe='')}",
                }
            )
        tables.append(table)
        table_by_id[table_id] = table

    for table_id, key_id in primary_key_by_table.items():
        for column_id in key_columns.get(key_id, []):
            pair = column_by_id.get(column_id)
            if pair and _text(pair[0].get("object_id")) == table_id:
                pair[1]["primary_key"] = True
                pair[1]["nullable"] = False

    relationships: list[dict[str, Any]] = []
    for reference in root.iter():
        if _local(reference.tag) != "Reference" or not _text(reference.attrib.get("Id")):
            continue
        reference_id = _text(reference.attrib.get("Id"))
        parent_id = _pdm_ref(_direct_child(reference, {"ParentTable"}), "Table")
        child_id = _pdm_ref(_direct_child(reference, {"ChildTable"}), "Table")
        parent_table = table_by_id.get(parent_id, {})
        child_table = table_by_id.get(child_id, {})
        child_columns: list[str] = []
        parent_columns: list[str] = []
        for join in reference.iter():
            if _local(join.tag) != "ReferenceJoin":
                continue
            parent_column_id = _pdm_ref(_direct_child(join, {"Object1", "ParentColumn"}), "Column")
            child_column_id = _pdm_ref(_direct_child(join, {"Object2", "ChildColumn"}), "Column")
            if parent_column_id in column_by_id:
                parent_columns.append(_text(column_by_id[parent_column_id][1].get("name")))
            if child_column_id in column_by_id:
                child_columns.append(_text(column_by_id[child_column_id][1].get("name")))
        relationships.append(
            {
                "object_id": reference_id,
                "name": _child_text(reference, "Code", "Name") or reference_id,
                "child_schema": _text(child_table.get("schema")),
                "child_table": _text(child_table.get("name")) or child_id,
                "child_columns": child_columns,
                "parent_schema": _text(parent_table.get("schema")),
                "parent_table": _text(parent_table.get("name")) or parent_id,
                "parent_columns": parent_columns,
                "delete_rule": _child_text(reference, "DeleteConstraint", "DeleteRule"),
                "update_rule": _child_text(reference, "UpdateConstraint", "UpdateRule"),
                "source_locator": f"{source.filename}#pdm-object=Reference;id={quote(reference_id, safe='')}",
            }
        )

    schemas = sorted(
        [
            {"name": value, "source_locator": f"{source.filename}#schema={quote(value, safe='')}"}
            for value in {_text(row.get("schema")) for row in tables}
            if value
        ],
        key=lambda row: row["name"],
    )
    return _model_ir(
        source,
        model_kind="powerdesigner_pdm_xml",
        model_name=model_name,
        database_family=database_family,
        schemas=schemas,
        tables=tables,
        relationships=relationships,
        indexes=indexes,
        parser_metadata={"powerdesigner_xml_model": True},
    )


def _grt_children(node: ElementTree.Element, key: str) -> list[ElementTree.Element]:
    for child in list(node):
        if _text(child.attrib.get("key")) != key:
            continue
        if _text(child.attrib.get("type")) == "list":
            return [value for value in list(child) if _local(value.tag) in {"value", "link"}]
        return [child]
    return []


def _grt_value(node: ElementTree.Element, key: str) -> str:
    for child in list(node):
        if _text(child.attrib.get("key")) == key:
            return _text(child.text or child.attrib.get("value"))
    return ""


def _grt_link(node: ElementTree.Element, key: str) -> str:
    for child in list(node):
        if _text(child.attrib.get("key")) == key:
            return _text(child.text or child.attrib.get("id") or child.attrib.get("ref"))
    return ""


def _mwb_xml(source: DocumentSource) -> tuple[bytes, dict[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(source.data)) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_MWB_MEMBERS:
                raise ValueError("MWB_MEMBER_LIMIT_EXCEEDED")
            info = next((row for row in infos if row.filename == "document.mwb.xml"), None)
            if info is None:
                raise ValueError("MWB_DOCUMENT_XML_MISSING")
            if int(info.flag_bits) & 0x1:
                raise ValueError("MWB_DOCUMENT_XML_ENCRYPTED")
            if int(info.file_size) > _MAX_MODEL_XML_BYTES:
                raise ValueError("MWB_DOCUMENT_XML_SIZE_LIMIT_EXCEEDED")
            if int(info.file_size) and int(info.compress_size) == 0:
                raise ValueError("MWB_DOCUMENT_XML_RATIO_UNBOUNDED")
            if int(info.compress_size) > 0 and int(info.file_size) / int(info.compress_size) > 200:
                raise ValueError("MWB_DOCUMENT_XML_RATIO_EXCEEDED")
            data = archive.read(info)
            if len(data) != int(info.file_size):
                raise ValueError("MWB_DOCUMENT_XML_SIZE_MISMATCH")
            return data, {
                "mwb_zip_member_count": len(infos),
                "mwb_document_xml_compressed_size": int(info.compress_size),
                "mwb_document_xml_size": int(info.file_size),
            }
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ValueError(f"MWB_CONTAINER_INVALID:{exc}") from exc


def _parse_mysql_workbench(source: DocumentSource) -> dict[str, Any]:
    try:
        xml_data, metadata = _mwb_xml(source)
        root = _xml_root(xml_data)
    except Exception as exc:
        return _blocked_ir(
            source,
            model_kind="mysql_workbench_mwb",
            reason_code="MYSQL_WORKBENCH_MODEL_INVALID",
            detail=f"{type(exc).__name__}: {exc}",
        )

    objects: dict[str, ElementTree.Element] = {}
    schema_names: dict[str, str] = {}
    for node in root.iter():
        if _local(node.tag) != "value" or _text(node.attrib.get("type")) != "object":
            continue
        object_id = _text(node.attrib.get("id"))
        if object_id:
            objects[object_id] = node
        struct_name = _text(node.attrib.get("struct-name"))
        if struct_name.endswith(".Schema") and object_id:
            schema_names[object_id] = _grt_value(node, "name")

    tables: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    table_by_id: dict[str, dict[str, Any]] = {}
    column_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    foreign_key_nodes: list[tuple[dict[str, Any], ElementTree.Element]] = []

    for object_id, node in objects.items():
        if not _text(node.attrib.get("struct-name")).endswith(".Table"):
            continue
        table_name = _grt_value(node, "name") or object_id
        schema_id = _grt_link(node, "owner")
        schema_name = schema_names.get(schema_id, "")
        locator = f"{source.filename}#document.mwb.xml;object={quote(object_id, safe='')}"
        table = {
            "object_id": object_id,
            "name": table_name,
            "schema": schema_name,
            "kind": "table",
            "comment": _grt_value(node, "comment"),
            "source_locator": locator,
            "columns": [],
        }
        for column in _grt_children(node, "columns"):
            if _local(column.tag) != "value":
                continue
            column_id = _text(column.attrib.get("id"))
            column_name = _grt_value(column, "name") or column_id
            column_row = {
                "object_id": column_id,
                "name": column_name,
                "data_type": _grt_value(column, "formattedType")
                or _grt_value(column, "datatypeExplicitParams"),
                "nullable": not _bool(_grt_value(column, "isNotNull")),
                "default": _grt_value(column, "defaultValue"),
                "auto_increment": _bool(_grt_value(column, "autoIncrement")),
                "comment": _grt_value(column, "comment"),
                "source_locator": f"{locator};column={quote(column_id or column_name, safe='')}",
            }
            table["columns"].append(column_row)
            if column_id:
                column_by_id[column_id] = (table, column_row)
        for index_node in _grt_children(node, "indices"):
            if _local(index_node.tag) != "value":
                continue
            index_id = _text(index_node.attrib.get("id"))
            column_ids = [
                _grt_link(index_column, "referencedColumn")
                for index_column in _grt_children(index_node, "columns")
            ]
            index_row = {
                "object_id": index_id,
                "name": _grt_value(index_node, "name") or index_id,
                "table": table_name,
                "columns": [
                    _text(column_by_id[value][1].get("name"))
                    for value in column_ids
                    if value in column_by_id
                ],
                "unique": _bool(_grt_value(index_node, "unique")),
                "primary": _bool(_grt_value(index_node, "isPrimary")),
                "source_locator": f"{locator};index={quote(index_id or _grt_value(index_node, 'name'), safe='')}",
            }
            indexes.append(index_row)
            if index_row["primary"]:
                for value in column_ids:
                    if value in column_by_id:
                        column_by_id[value][1]["primary_key"] = True
                        column_by_id[value][1]["nullable"] = False
            elif index_row["unique"]:
                for value in column_ids:
                    if value in column_by_id:
                        column_by_id[value][1]["unique"] = True
        for foreign_key in _grt_children(node, "foreignKeys"):
            if _local(foreign_key.tag) == "value":
                foreign_key_nodes.append((table, foreign_key))
        tables.append(table)
        table_by_id[object_id] = table

    relationships: list[dict[str, Any]] = []
    for child_table, foreign_key in foreign_key_nodes:
        foreign_key_id = _text(foreign_key.attrib.get("id"))
        parent_table_id = _grt_link(foreign_key, "referencedTable")
        parent_table = table_by_id.get(parent_table_id, {})
        child_column_ids = [
            _text(value.text or value.attrib.get("id") or value.attrib.get("ref"))
            for value in _grt_children(foreign_key, "columns")
        ]
        parent_column_ids = [
            _text(value.text or value.attrib.get("id") or value.attrib.get("ref"))
            for value in _grt_children(foreign_key, "referencedColumns")
        ]
        relationships.append(
            {
                "object_id": foreign_key_id,
                "name": _grt_value(foreign_key, "name") or foreign_key_id,
                "child_schema": _text(child_table.get("schema")),
                "child_table": _text(child_table.get("name")),
                "child_columns": [
                    _text(column_by_id[value][1].get("name"))
                    for value in child_column_ids
                    if value in column_by_id
                ],
                "parent_schema": _text(parent_table.get("schema")),
                "parent_table": _text(parent_table.get("name")) or parent_table_id,
                "parent_columns": [
                    _text(column_by_id[value][1].get("name"))
                    for value in parent_column_ids
                    if value in column_by_id
                ],
                "delete_rule": _grt_value(foreign_key, "deleteRule"),
                "update_rule": _grt_value(foreign_key, "updateRule"),
                "source_locator": (
                    f"{source.filename}#document.mwb.xml;foreign-key="
                    f"{quote(foreign_key_id or _grt_value(foreign_key, 'name'), safe='')}"
                ),
            }
        )

    model_name = Path(source.filename).stem
    database_family = "mysql"
    schemas = [
        {
            "object_id": object_id,
            "name": name,
            "source_locator": f"{source.filename}#document.mwb.xml;schema={quote(object_id, safe='')}",
        }
        for object_id, name in sorted(schema_names.items(), key=lambda row: row[1])
    ]
    return _model_ir(
        source,
        model_kind="mysql_workbench_mwb",
        model_name=model_name,
        database_family=database_family,
        schemas=schemas,
        tables=tables,
        relationships=relationships,
        indexes=indexes,
        parser_metadata={"mysql_workbench_document_xml": True, **metadata},
    )


def _sqlite_pragma(connection: sqlite3.Connection, pragma: str, name: str) -> list[tuple[Any, ...]]:
    escaped = str(name).replace("'", "''")
    return list(connection.execute(f"PRAGMA {pragma}('{escaped}')"))


def _parse_sqlite(source: DocumentSource) -> dict[str, Any]:
    if not source.data.startswith(b"SQLite format 3\x00"):
        return _blocked_ir(
            source,
            model_kind="sqlite_database",
            reason_code="DATABASE_BINARY_IS_NOT_SQLITE",
            detail=(
                "The .db suffix is ambiguous and this source does not have a SQLite header. "
                "Provide DDL, a data dictionary, PDM/MWB model, or a SQLite database file."
            ),
        )
    with tempfile.TemporaryDirectory(prefix="qualibug-sqlite-model-") as directory:
        path = Path(directory) / "source.sqlite"
        path.write_bytes(source.data)
        uri = f"file:{quote(str(path))}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            try:
                connection.execute("PRAGMA trusted_schema=OFF")
            except sqlite3.DatabaseError:
                pass
            objects = list(
                connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY type, name"
                )
            )
            tables: list[dict[str, Any]] = []
            indexes: list[dict[str, Any]] = []
            relationships: list[dict[str, Any]] = []
            for object_type, name, _table_name, sql in objects:
                locator = f"{source.filename}#sqlite-object={quote(str(name), safe='')}"
                table = {
                    "name": str(name),
                    "schema": "main",
                    "kind": str(object_type),
                    "definition": _text(sql),
                    "source_locator": locator,
                    "columns": [],
                }
                for row in _sqlite_pragma(connection, "table_xinfo", str(name)):
                    cid, column_name, data_type, not_null, default, primary_key, hidden = row
                    table["columns"].append(
                        {
                            "name": str(column_name),
                            "data_type": _text(data_type),
                            "nullable": not bool(not_null),
                            "default": _text(default),
                            "primary_key": bool(primary_key),
                            "hidden": int(hidden or 0),
                            "ordinal_position": int(cid) + 1,
                            "source_locator": (
                                f"{locator};column={quote(str(column_name), safe='')}"
                            ),
                        }
                    )
                for index_row in _sqlite_pragma(connection, "index_list", str(name)):
                    sequence, index_name, unique, origin, partial = index_row[:5]
                    columns = [
                        str(info[2])
                        for info in _sqlite_pragma(connection, "index_info", str(index_name))
                        if len(info) >= 3 and info[2] is not None
                    ]
                    indexes.append(
                        {
                            "name": str(index_name),
                            "table": str(name),
                            "columns": columns,
                            "unique": bool(unique),
                            "origin": _text(origin),
                            "partial": bool(partial),
                            "sequence": int(sequence),
                            "source_locator": (
                                f"{locator};index={quote(str(index_name), safe='')}"
                            ),
                        }
                    )
                for foreign_key in _sqlite_pragma(connection, "foreign_key_list", str(name)):
                    fk_id, sequence, parent_table, child_column, parent_column, on_update, on_delete, match = foreign_key[:8]
                    relationships.append(
                        {
                            "name": f"fk_{name}_{fk_id}_{sequence}",
                            "child_schema": "main",
                            "child_table": str(name),
                            "child_columns": [str(child_column)],
                            "parent_schema": "main",
                            "parent_table": str(parent_table),
                            "parent_columns": [str(parent_column)],
                            "delete_rule": _text(on_delete),
                            "update_rule": _text(on_update),
                            "match": _text(match),
                            "source_locator": f"{locator};foreign-key={fk_id};sequence={sequence}",
                        }
                    )
                tables.append(table)
            return _model_ir(
                source,
                model_kind="sqlite_database",
                model_name=Path(source.filename).stem,
                database_family="sqlite",
                schemas=[
                    {
                        "name": "main",
                        "source_locator": f"{source.filename}#sqlite-schema=main",
                    }
                ],
                tables=tables,
                relationships=relationships,
                indexes=indexes,
                parser_metadata={
                    "sqlite_open_mode": "read_only_immutable",
                    "sqlite_data_rows_read": 0,
                    "sqlite_schema_only": True,
                },
            )
        except sqlite3.DatabaseError as exc:
            return _blocked_ir(
                source,
                model_kind="sqlite_database",
                reason_code="SQLITE_SCHEMA_READ_FAILED",
                detail=f"{type(exc).__name__}: {exc}",
            )
        finally:
            connection.close()


class DatabaseModelDocumentAdapter(DocumentAdapter):
    """Extract exact database-model structure without assigning business meaning."""

    name = "database-model-native-structure"
    parser_version = "1"
    priority = 116
    mode = MODE_PRIMARY
    capabilities = frozenset({CAP_TEXT_EXTRACTION, CAP_TABLE_STRUCTURE})
    _PDM_SUFFIXES = {".pdm"}
    _MWB_SUFFIXES = {".mwb"}
    _SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}

    @staticmethod
    def _looks_like_mwb(data: bytes) -> bool:
        if not data.startswith(b"PK"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = {row.filename for row in archive.infolist()[:_MAX_MWB_MEMBERS]}
                return "document.mwb.xml" in names
        except Exception:
            return False

    @staticmethod
    def _looks_like_pdm_xml(data: bytes) -> bool:
        prefix = data.lstrip()[:8192].lower()
        return prefix.startswith(b"<") and (
            b"powerdesigner" in prefix
            or b"<o:model" in prefix
            or b"physicaldatamodel" in prefix
        )

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        sqlite_signature = source.data.startswith(b"SQLite format 3\x00")
        mwb_signature = self._looks_like_mwb(source.data)
        pdm_signature = self._looks_like_pdm_xml(source.data)
        suffix = source.suffix
        if not (
            sqlite_signature
            or mwb_signature
            or pdm_signature
            or suffix in self._PDM_SUFFIXES | self._MWB_SUFFIXES | self._SQLITE_SUFFIXES
        ):
            return None
        if sqlite_signature:
            reason = "sqlite_file_signature"
            score = 130
        elif mwb_signature:
            reason = "mysql_workbench_document_xml_container"
            score = 130
        elif pdm_signature:
            reason = "powerdesigner_xml_model_signature"
            score = 128
        else:
            reason = "database_model_filename_suffix"
            score = 100
        return AdapterMatch(
            self.name,
            score,
            reason,
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        if source.data.startswith(b"SQLite format 3\x00") or source.suffix in self._SQLITE_SUFFIXES:
            return _parse_sqlite(source)
        if self._looks_like_mwb(source.data) or source.suffix in self._MWB_SUFFIXES:
            return _parse_mysql_workbench(source)
        if self._looks_like_pdm_xml(source.data) or source.suffix in self._PDM_SUFFIXES:
            return _parse_powerdesigner_pdm(source)
        return _blocked_ir(
            source,
            model_kind="unknown_database_model",
            reason_code="DATABASE_MODEL_FORMAT_UNSUPPORTED",
            detail="No supported database model signature matched the source bytes.",
        )


__all__ = [
    "DATABASE_MODEL_STRUCTURE_SCHEMA",
    "DatabaseModelDocumentAdapter",
]
