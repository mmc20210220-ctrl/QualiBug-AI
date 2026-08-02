"""Single ingestion bridge from immutable source bytes to business-semantic inputs.

Container/format decoding is owned exclusively by ``document_ingestion``. This module
projects source-preserving Document IR into the existing business extraction contract and
GraphRAG chunk contract. It deliberately contains no file-format parser.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ._common import SOURCE_CODE_SUFFIXES
from ._parsing import _parse_source, _risk_type_from_text
from .document_ingestion import build_document_structure_ir
from .document_ir_api_semantics import enrich_parsed_api_artifact_semantics
from .document_ir_tabular_semantics import extract_tabular_enterprise_semantics

SEMANTIC_SOURCE_PROJECTION_SCHEMA = "qualibug.semantic-source-projection.v1"
DOCUMENT_IR_CHUNK_SCHEMA = "qualibug.document-ir-retrieval-chunk.v1"

_FORMAL_TEXT_BLOCK_TYPES = frozenset(
    {
        "HEADING",
        "PARAGRAPH",
        "LIST_ITEM",
        "TABLE_CELL",
        "KEY_VALUE",
        "NOTE",
        "CAPTION",
        "FORMULA",
    }
)
_TEXT_NATIVE_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".ndjson",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".sql",
        ".ddl",
        ".dml",
        ".log",
        ".har",
        ".svg",
        ".feature",
        ".proto",
        ".graphql",
        ".gql",
        ".raml",
        ".http",
        ".rest",
        ".dbml",
        ".prisma",
        ".toml",
        ".ini",
        ".conf",
        ".cfg",
        ".env",
        ".properties",
        ".bpmn",
        ".mmd",
    }
) | SOURCE_CODE_SUFFIXES
_EXACT_ADDRESS_KINDS = frozenset(
    {
        "PAGE_BBOX",
        "SPREADSHEET_CELL",
        "PRESENTATION_SHAPE",
        "EXACT_SOURCE_LOCATOR",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ordered_blocks(document_ir: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in _list(document_ir.get("blocks")) if isinstance(row, dict)]
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("order") or 0),
            _text(row.get("source_locator")),
            _text(row.get("block_id")),
        ),
    )


def _table_membership(document_ir: dict[str, Any]) -> dict[str, str]:
    membership: dict[str, str] = {}
    for index, raw in enumerate(_list(document_ir.get("tables")), start=1):
        if not isinstance(raw, dict):
            continue
        table = dict(raw)
        table_id = _text(table.get("block_id") or table.get("table_id")) or f"table:{index}"
        for field in ("cell_block_ids", "block_ids", "child_block_ids"):
            for block_id in _list(table.get(field)):
                if _text(block_id):
                    membership[_text(block_id)] = table_id
    return membership


def _fallback_table_key(block: dict[str, Any]) -> str:
    sheet = _text(block.get("sheet"))
    if sheet:
        return f"sheet:{sheet}"
    locator = _text(block.get("source_locator"))
    if ";cell=" in locator:
        return locator.split(";cell=", 1)[0]
    if ";table-cell=" in locator:
        return locator.split(";table-cell=", 1)[0]
    return _text(block.get("parent_id")) or locator or "ungrouped-table"


def _escape_markdown_cell(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", "<br>")
    return text.replace("|", r"\|").strip()


_COMPOSED_SOURCE_MARKER_RE = re.compile(
    r"<!--\s*qualibug:source\b.*?-->",
    re.IGNORECASE | re.DOTALL,
)


def _source_marker_before_order(
    document_ir: dict[str, Any],
    order: int,
) -> str:
    """Return the source marker governing a table at its original order."""

    marker = ""
    for block in _ordered_blocks(document_ir):
        block_order = int(block.get("order") or 0)
        if block_order > order:
            break
        candidate = _COMPOSED_SOURCE_MARKER_RE.search(_text(block.get("text")))
        if candidate:
            marker = candidate.group(0)
    return marker


def _candidate_markdown_tables(
    document_ir: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    membership = _table_membership(document_ir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in _ordered_blocks(document_ir):
        if _text(block.get("type")) != "TABLE_CELL":
            continue
        key = membership.get(_text(block.get("block_id"))) or _fallback_table_key(block)
        grouped[key].append(block)

    projections: list[str] = []
    receipts: list[dict[str, Any]] = []
    for table_key, cells in grouped.items():
        coordinate_cells = [
            row
            for row in cells
            if int(row.get("row_index") or 0) > 0
            and int(row.get("column_index") or 0) > 0
        ]
        if not coordinate_cells:
            continue
        grid: dict[int, dict[int, str]] = defaultdict(dict)
        locators: list[str] = []
        for cell in coordinate_cells:
            row_index = int(cell.get("row_index") or 0)
            column_index = int(cell.get("column_index") or 0)
            grid[row_index][column_index] = _text(cell.get("text"))
            if _text(cell.get("source_locator")):
                locators.append(_text(cell.get("source_locator")))
        row_numbers = sorted(grid)
        max_column = max((max(columns) for columns in grid.values() if columns), default=0)
        if not row_numbers or max_column <= 0:
            continue
        first_row = row_numbers[0]
        headers = [
            grid[first_row].get(column, "") or f"column_{column}"
            for column in range(1, max_column + 1)
        ]
        lines = [
            "| " + " | ".join(_escape_markdown_cell(value) for value in headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
        for row_number in row_numbers[1:]:
            values = [grid[row_number].get(column, "") for column in range(1, max_column + 1)]
            lines.append(
                "| " + " | ".join(_escape_markdown_cell(value) for value in values) + " |"
            )
        table_order = min(int(cell.get("order") or 0) for cell in coordinate_cells)
        source_marker = _source_marker_before_order(document_ir, table_order)
        projection = "\n".join(lines)
        if source_marker:
            projection = f"{source_marker}\n{projection}"
        projections.append(projection)
        receipts.append(
            {
                "table_key": table_key,
                "cell_count": len(coordinate_cells),
                "row_count": len(row_numbers),
                "column_count": max_column,
                "header_row_candidate": first_row,
                "header_semantics_confirmed": False,
                "projection_method": "coordinate_preserving_candidate_header_projection",
                "source_locators": sorted(set(locators))[:200],
                "source_marker": source_marker,
                "business_semantics_added": False,
            }
        )
    return projections, receipts


def project_document_ir_for_semantic_extraction(
    document_ir: dict[str, Any],
    *,
    filename: str,
) -> tuple[str, dict[str, Any]]:
    """Project exact Document IR into the existing semantic extractor contract."""

    table_cell_ids = {
        _text(row.get("block_id"))
        for row in _ordered_blocks(document_ir)
        if _text(row.get("type")) == "TABLE_CELL"
    }
    lines: list[str] = []
    projected_block_ids: list[str] = []
    for block in _ordered_blocks(document_ir):
        block_type = _text(block.get("type"))
        if block_type not in _FORMAL_TEXT_BLOCK_TYPES or block_type == "TABLE_CELL":
            continue
        if block.get("excluded_from_main_flow") or block.get(
            "excluded_from_plain_text_projection"
        ):
            continue
        value = _text(block.get("text"))
        if not value:
            continue
        if block_type == "HEADING":
            level = max(1, min(6, int(block.get("level") or 1)))
            lines.append(f"{'#' * level} {value}")
        elif block_type == "LIST_ITEM":
            lines.append(f"- {value}")
        elif block_type == "KEY_VALUE":
            key = _text(block.get("key"))
            lines.append(f"{key}: {value}" if key else value)
        elif block_type == "NOTE":
            lines.append(f"> {value}")
        else:
            lines.append(value)
        projected_block_ids.append(_text(block.get("block_id")))

    table_projections, table_receipts = _candidate_markdown_tables(document_ir)
    lines.extend(table_projections)
    projection = "\n\n".join(value for value in lines if value).strip()
    if not projection:
        projection = _text(document_ir.get("plain_text"))
    return projection, {
        "schema": SEMANTIC_SOURCE_PROJECTION_SCHEMA,
        "filename": filename,
        "document_ir_format": _text(document_ir.get("format")),
        "projection_method": "source_preserving_document_ir_to_semantic_markdown",
        "projected_text_length": len(projection),
        "projected_block_count": len([value for value in projected_block_ids if value]),
        "table_cell_block_count": len([value for value in table_cell_ids if value]),
        "projected_table_count": len(table_receipts),
        "tables": table_receipts,
        "business_semantics_added": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
    }


def _semantic_filename(filename: str) -> str:
    path = Path(str(filename or "document"))
    if path.suffix.lower() in _TEXT_NATIVE_SUFFIXES:
        return path.name
    return f"{path.stem or 'document'}.md"


def _document_ir_errors(document_ir: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for raw in _list(document_ir.get("unsupported_content")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        errors.append(
            {
                "stage": "document_structure",
                "code": _text(row.get("reason_code") or row.get("kind"))
                or "DOCUMENT_STRUCTURE_GAP",
                "identity": source_id,
                "retryability": "after_source_or_adapter_fix",
                "operator_action": "inspect document ingestion and evidence receipts",
                "detail": _text(row.get("status"))[:500],
                "severity": _text(row.get("severity")) or "P1",
                "source_locator": _text(row.get("source_locator")),
                "blocks_formal_understanding": bool(row.get("blocks_formal_understanding")),
            }
        )
    return errors


def _ticket_severity(value: Any) -> str:
    raw = _text(value).lower()
    if raw.upper() in {"P0", "P1", "P2", "P3"}:
        return raw.upper()
    if any(token in raw for token in ("致命", "阻断", "blocker", "fatal")):
        return "P0"
    if any(token in raw for token in ("严重", "critical", "high")):
        return "P1"
    if any(token in raw for token in ("轻微", "minor", "low", "提示")):
        return "P3"
    return "P2"


def _bug_ticket_projection(bug: dict[str, Any], source_type: str) -> dict[str, Any]:
    title = _text(bug.get("title") or bug.get("actual") or bug.get("bug_id"))
    evidence_parts = [
        f"precondition={_text(bug.get('precondition'))}" if _text(bug.get("precondition")) else "",
        f"steps={_text(bug.get('steps'))}" if _text(bug.get("steps")) else "",
        f"expected={_text(bug.get('expected'))}" if _text(bug.get("expected")) else "",
        f"actual={_text(bug.get('actual'))}" if _text(bug.get("actual")) else "",
    ]
    return {
        "risk_id": _text(bug.get("bug_id")) or _text(bug.get("historical_bug_id")),
        "source_id": _text(bug.get("source_id")),
        "source_type": source_type or "historical_bug",
        "title": title[:320],
        "severity": _ticket_severity(bug.get("severity") or bug.get("priority")),
        "severity_raw": _text(bug.get("severity") or bug.get("priority")),
        "status": _text(bug.get("status")) or "historical",
        "risk_type": _risk_type_from_text(" ".join([title, _text(bug.get("actual"))])),
        "evidence": "; ".join(value for value in evidence_parts if value)[:1200]
        or title[:600],
        "field_evidence": dict(bug.get("field_evidence") or {}),
        "source_locators": list(bug.get("source_locators") or []),
        "high_fidelity_document_ir_projection": True,
    }


def _merge_ticket_rows(
    generic: list[Any],
    high_fidelity: list[dict[str, Any]],
    source_type: str,
) -> list[dict[str, Any]]:
    projected = [_bug_ticket_projection(row, source_type) for row in high_fidelity]
    if projected:
        return projected
    return [dict(row) for row in generic if isinstance(row, dict)]


def parse_enterprise_source(
    blob: bytes,
    filename: str,
    source_type: str,
    source_id: str,
) -> dict[str, Any]:
    """Run one source through the single format authority and semantic layer."""

    document_ir = build_document_structure_ir(
        bytes(blob or b""),
        filename=str(filename or "document"),
        source_id=str(source_id or ""),
    )
    projection, projection_receipt = project_document_ir_for_semantic_extraction(
        document_ir,
        filename=filename,
    )
    parsed = _parse_source(
        projection.encode("utf-8"),
        _semantic_filename(filename),
        source_type,
        source_id,
    )
    parsed = enrich_parsed_api_artifact_semantics(
        parsed,
        document_ir,
        source_id=source_id,
        source_type=source_type,
    )
    tabular = extract_tabular_enterprise_semantics(
        document_ir,
        source_id=source_id,
        source_type=source_type,
        filename=filename,
    )
    historical_bugs = [
        dict(row) for row in _list(tabular.get("historical_bugs")) if isinstance(row, dict)
    ]
    test_cases = [
        dict(row) for row in _list(tabular.get("test_cases")) if isinstance(row, dict)
    ]
    parsed["historical_bugs"] = historical_bugs
    parsed["test_cases"] = test_cases
    parsed["tickets"] = _merge_ticket_rows(
        _list(parsed.get("tickets")),
        historical_bugs,
        source_type,
    )
    parsed["tabular_semantic_receipt"] = tabular

    structure_receipt = _dict(document_ir.get("structure_receipt"))
    evidence_receipt = _dict(document_ir.get("evidence_closure_receipt"))
    ingestion_receipt = _dict(document_ir.get("ingestion_pipeline_receipt"))
    api_semantic_receipt = _dict(parsed.get("api_artifact_semantic_receipt"))
    document_errors = _document_ir_errors(document_ir, source_id)
    parse_errors = [
        dict(row)
        for row in [*_list(parsed.get("parse_errors")), *document_errors]
        if isinstance(row, dict)
    ]
    formal_status = _text(structure_receipt.get("status")) or "UNKNOWN"
    if formal_status == "BLOCKED":
        parsed["parse_status"] = "failed"
    parsed["parse_errors"] = parse_errors
    parsed["text"] = projection
    parsed["text_hash"] = hashlib.sha256(projection.encode("utf-8")).hexdigest()
    parsed["text_length"] = len(projection)
    parsed["document_structure"] = document_ir
    parsed["document_ir"] = document_ir
    parsed["document_ir_status"] = formal_status
    parsed["semantic_projection_receipt"] = projection_receipt
    receipt = dict(parsed.get("parser_receipt") or {})
    outputs = dict(receipt.get("outputs") or {})
    outputs.update(
        {
            "operations": len(_list(parsed.get("operations"))),
            "tickets": len(parsed["tickets"]),
            "historical_bugs": len(historical_bugs),
            "test_cases": len(test_cases),
        }
    )
    fidelity = (
        "blocked"
        if formal_status == "BLOCKED"
        else "degraded"
        if formal_status == "PARTIAL"
        else "full"
    )
    receipt.update(
        {
            "source_locator": filename,
            "detected_format": _text(document_ir.get("format"))
            or _text(receipt.get("detected_format")),
            "parser": "document_ir+" + _text(receipt.get("parser") or "semantic"),
            "parser_status": (
                "failed"
                if formal_status == "BLOCKED"
                else "degraded"
                if formal_status == "PARTIAL" or parse_errors
                else _text(receipt.get("parser_status") or parsed.get("parse_status"))
            ),
            "decode_fidelity": fidelity,
            "fidelity": fidelity,
            "errors": parse_errors,
            "outputs": outputs,
            "document_ir_status": formal_status,
            "document_ir_format": _text(document_ir.get("format")),
            "semantic_projection_receipt": projection_receipt,
            "api_artifact_semantic_receipt": api_semantic_receipt,
            "tabular_semantic_receipt": tabular,
            "ingestion_pipeline_receipt": ingestion_receipt,
            "evidence_closure_receipt": evidence_receipt,
            "business_semantics_added_by_document_adapter": False,
        }
    )
    parsed["parser_receipt"] = receipt
    return parsed


def _entity_candidates(parsed: dict[str, Any]) -> list[str]:
    candidates: list[str] = []

    def add(value: Any) -> None:
        item = _text(value)
        if item and len(item) <= 160 and item not in candidates:
            candidates.append(item)

    for row in _list(parsed.get("tables")):
        if isinstance(row, dict):
            add(row.get("name") or row.get("table") or row.get("entity"))
    for row in _list(parsed.get("field_dictionary")):
        if isinstance(row, dict):
            add(row.get("table") or row.get("entity"))
            add(row.get("field"))
    for row in _list(parsed.get("operations")):
        if not isinstance(row, dict):
            continue
        for token in re.split(
            r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", _text(row.get("path"))
        ):
            if len(token) >= 3 and token.lower() not in {"api", "http", "https"}:
                add(token)
    for row in _list(parsed.get("roles")):
        if isinstance(row, dict):
            add(row.get("role") or row.get("name"))
        else:
            add(row)
    for row in _list(parsed.get("state_machines")):
        if isinstance(row, dict):
            add(row.get("entity") or row.get("object") or row.get("name"))
    for row in _list(parsed.get("historical_bugs")):
        if isinstance(row, dict):
            add(row.get("module"))
            add(row.get("requirement"))
    for row in _list(parsed.get("test_cases")):
        if isinstance(row, dict):
            add(row.get("module"))
            add(row.get("requirement"))
    return candidates


def _matching_entities(content: str, candidates: Iterable[str]) -> list[str]:
    normalized = content.casefold()
    return sorted(
        {
            candidate
            for candidate in candidates
            if candidate and candidate.casefold() in normalized
        }
    )[:40]


def build_document_ir_retrieval_chunks(
    parsed: dict[str, Any],
    *,
    source_id: str,
    source_hash: str,
    source_version: Any = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build retrieval chunks from exact Document IR blocks, never raw binary bytes."""

    document_ir = _dict(parsed.get("document_ir") or parsed.get("document_structure"))
    candidates = _entity_candidates(parsed)
    chunks: list[dict[str, Any]] = []
    exact_count = 0
    for block in _ordered_blocks(document_ir):
        block_type = _text(block.get("type"))
        content = _text(block.get("text"))
        if block_type not in _FORMAL_TEXT_BLOCK_TYPES or not content:
            continue
        if block.get("excluded_from_main_flow") or block.get(
            "excluded_from_plain_text_projection"
        ):
            continue
        block_id = _text(block.get("block_id"))
        locator = _text(block.get("source_locator"))
        evidence = _dict(block.get("evidence_address"))
        address_kind = _text(evidence.get("address_kind"))
        exact = address_kind in _EXACT_ADDRESS_KINDS
        if exact:
            exact_count += 1
        chunk_id = (
            f"chunk:{source_id}:{block_id}"
            if block_id
            else "chunk:"
            + source_id
            + ":"
            + hashlib.sha256((locator + content).encode("utf-8")).hexdigest()[:24]
        )
        chunks.append(
            {
                "schema": DOCUMENT_IR_CHUNK_SCHEMA,
                "chunk_id": chunk_id,
                "source_id": source_id,
                "source_hash": source_hash,
                "source_version": str(source_version or ""),
                "block_id": block_id,
                "chunk_type": block_type.lower(),
                "content": content,
                "entities": _matching_entities(content, candidates),
                "source_locator": locator,
                "evidence_address": evidence,
                "page": block.get("page"),
                "bbox": block.get("bbox"),
                "sheet": block.get("sheet"),
                "cell_ref": block.get("cell_ref"),
                "slide": block.get("slide"),
                "shape_id": block.get("shape_id"),
                "parent_id": _text(block.get("parent_id")),
                "extraction_method": _text(
                    _dict(block.get("structure_evidence")).get("method")
                )
                or "document_ir",
                "confidence": 1.0 if exact else 0.85,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "business_semantics_added": False,
            }
        )

    fallback_used = False
    if not chunks:
        content = _text(parsed.get("text"))
        if content:
            fallback_used = True
            chunks.append(
                {
                    "schema": DOCUMENT_IR_CHUNK_SCHEMA,
                    "chunk_id": f"chunk:{source_id}:semantic-projection",
                    "source_id": source_id,
                    "source_hash": source_hash,
                    "source_version": str(source_version or ""),
                    "block_id": "",
                    "chunk_type": "semantic_projection",
                    "content": content,
                    "entities": _matching_entities(content, candidates),
                    "source_locator": _text(
                        _dict(parsed.get("parser_receipt")).get("source_locator")
                    ),
                    "evidence_address": {},
                    "extraction_method": "semantic_projection_fallback",
                    "confidence": 0.6,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "business_semantics_added": False,
                }
            )
    receipt = {
        "schema": "qualibug.document-ir-chunk-index-receipt.v1",
        "source_id": source_id,
        "source_hash": source_hash,
        "chunk_count": len(chunks),
        "exact_address_chunk_count": exact_count,
        "exact_address_rate": round(exact_count / len(chunks), 4) if chunks else 1.0,
        "semantic_projection_fallback_used": fallback_used,
        "raw_binary_utf8_decode_used": False,
        "silent_failure_allowed": False,
    }
    return chunks, receipt


__all__ = [
    "SEMANTIC_SOURCE_PROJECTION_SCHEMA",
    "DOCUMENT_IR_CHUNK_SCHEMA",
    "project_document_ir_for_semantic_extraction",
    "parse_enterprise_source",
    "build_document_ir_retrieval_chunks",
]
