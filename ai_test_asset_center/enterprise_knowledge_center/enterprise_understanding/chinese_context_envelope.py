"""Semantic Context Envelope — structural coordinates over Document Structure IR.

SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1 (P0-B: section/list/table context).

Contract:
- The envelope models only STRUCTURE, never business semantics: it answers
  "which section / list / table cell does this block live in" with exact
  coordinates and source text, so downstream clause parsing and frame
  compilation can inherit list-parent scopes, table row/column headers with
  exact header-cell lineage, and section paths without re-scanning raw
  documents.
- List nesting is recovered from ``numbering.level`` (docx ``w:ilvl``; promoted
  visible-marker lists default to level 0) with an order-scanned stack that is
  reset at every HEADING / TABLE boundary — list context never crosses a
  section. Without a level signal, no fake nesting is invented.
- Table context maps a TABLE_CELL to its table, row/column index, row header
  (first column text of the row), column header (first-row header text), and
  the header cells' block ids/locators. Missing headers stay empty strings
  (never guessed).
- Block lookup is unique-match only: a quote that matches several blocks
  resolves to nothing (ambiguity is surfaced in the receipt, never resolved
  by picking one).
- Input is ``asset["document_structure_assets"]`` (populated by the knowledge
  composition for both full and incremental paths), so this module is pure
  asset-in/asset-out and never needs parsed sources.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

CHINESE_SEMANTIC_CONTEXT_ENVELOPE_SCHEMA = "qualibug.chinese-semantic-context-envelope.v1"

_NEIGHBOR_WINDOW = 3


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return " ".join(_text(value).split()).strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(kind: str, *parts: Any) -> str:
    encoded = _canonical_json([_text(part) for part in parts])
    return f"{kind}:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _numbering_level(block: dict[str, Any]) -> int:
    """List depth from docx ``w:ilvl``; promoted lists without a level are 0."""
    numbering = _dict(block.get("numbering"))
    level = numbering.get("level")
    try:
        return max(0, int(level))
    except (TypeError, ValueError):
        return 0


def _body_blocks(document_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(document_ir.get("blocks"))
        if isinstance(row, dict)
        and _text(row.get("region")) == "body"
    ]


def _section_path(
    block_id: str,
    blocks_by_id: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Walk the parent_id chain (HEADING ancestors) → titles and block ids."""
    titles: list[str] = []
    ids: list[str] = []
    seen: set[str] = set()
    current = _text(block_id)
    while current and current not in seen:
        seen.add(current)
        block = blocks_by_id.get(current)
        if not block:
            break
        if _text(block.get("type")) == "HEADING":
            titles.append(_norm(block.get("text")))
            ids.append(current)
        current = _text(block.get("parent_id"))
    return list(reversed(titles)), list(reversed(ids))


def _build_list_context(
    blocks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Order-scanned list stack: LIST_ITEM parent chains within one section.

    The stack is reset at HEADING (new section) and TABLE boundaries; item
    parent = nearest preceding item with strictly smaller level. Cross-section
    inheritance is structurally impossible.
    """
    context: dict[str, dict[str, Any]] = {}
    stack: list[dict[str, Any]] = []
    for block in blocks:
        block_type = _text(block.get("type"))
        if block_type in ("HEADING", "TABLE"):
            stack.clear()
            continue
        if block_type != "LIST_ITEM":
            continue
        level = _numbering_level(block)
        while stack and _numbering_level(stack[-1]) >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        context[_text(block.get("block_id"))] = {
            "list_level": level,
            "list_parent": _text(parent.get("block_id")) if parent else "",
            "list_ancestor_chain": [
                _text(item.get("block_id")) for item in stack
            ],
        }
        stack.append(block)
    return context


def _build_table_context(
    document_ir: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """TABLE_CELL → table/row/column coordinates + row/column header text."""
    tables = [
        row for row in _list(document_ir.get("tables")) if isinstance(row, dict)
    ]
    tables_by_index: dict[int, dict[str, Any]] = {}
    for table in tables:
        try:
            tables_by_index[int(table.get("table_index"))] = table
        except (TypeError, ValueError):
            continue
    cells_by_coordinate: dict[tuple[int, int, int], dict[str, Any]] = {}
    for block in _list(document_ir.get("blocks")):
        if (
            not isinstance(block, dict)
            or _text(block.get("type")) != "TABLE_CELL"
        ):
            continue
        row_index = _int_or_none(block.get("row_index"))
        column_index = _int_or_none(block.get("column_index"))
        if row_index is None or column_index is None:
            continue
        cells_by_coordinate[(_table_index(block), row_index, column_index)] = block
    context: dict[str, dict[str, Any]] = {}
    for block in _list(document_ir.get("blocks")):
        if not isinstance(block, dict) or _text(block.get("type")) != "TABLE_CELL":
            continue
        block_id = _text(block.get("block_id"))
        table_index = _table_index(block)
        row_index = _int_or_none(block.get("row_index"))
        column_index = _int_or_none(block.get("column_index"))
        table = tables_by_index.get(table_index)
        headers = [ _norm(row) for row in _list(_dict(table).get("headers")) ] if table else []
        column_header = ""
        if headers and column_index is not None and 0 <= column_index < len(headers):
            column_header = headers[column_index]
        row_header = ""
        if table and row_index is not None and row_index >= 1 and headers:
            rows = [row for row in _list(table.get("rows")) if isinstance(row, dict)]
            data_index = row_index - 1
            if 0 <= data_index < len(rows):
                row_header = _norm(rows[data_index].get(headers[0]) if headers else "")
        row_header_block = (
            cells_by_coordinate.get((table_index, row_index, 0), {})
            if row_index is not None and row_index >= 1 and row_header
            else {}
        )
        column_header_block = (
            cells_by_coordinate.get((table_index, 0, column_index), {})
            if column_index is not None and column_header
            else {}
        )
        context[block_id] = {
            "table_id": _text(table.get("table_block_id") if table else ""),
            "table_index": table_index,
            "row_index": row_index,
            "column_index": column_index,
            "row_header": row_header,
            "column_header": column_header,
            "row_header_block_id": _text(row_header_block.get("block_id")),
            "row_header_locator": _text(
                row_header_block.get("source_locator")
                or row_header_block.get("locator")
            ),
            "column_header_block_id": _text(
                column_header_block.get("block_id")
            ),
            "column_header_locator": _text(
                column_header_block.get("source_locator")
                or column_header_block.get("locator")
            ),
        }
    return context


def _table_index(block: dict[str, Any]) -> int:
    try:
        return int(block.get("table_index"))
    except (TypeError, ValueError):
        return -1


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_source_envelope(
    source: dict[str, Any],
) -> dict[str, Any]:
    document_ir = _dict(source.get("structure")) or _dict(source)
    blocks = _body_blocks(document_ir)
    blocks_by_id = {_text(row.get("block_id")): row for row in blocks}
    list_context = _build_list_context(blocks)
    table_context = _build_table_context(document_ir)

    # Unique exact-text lookup: a quote shared by several blocks is ambiguous
    # and therefore absent (never resolved by picking one).
    text_lookup: dict[str, str] = {}
    text_counts: dict[str, int] = {}
    for block in blocks:
        text = _norm(block.get("text"))
        if text:
            text_counts[text] = text_counts.get(text, 0) + 1
    for block in blocks:
        text = _norm(block.get("text"))
        if text and text_counts.get(text) == 1:
            text_lookup[text] = _text(block.get("block_id"))

    blocks_out: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for index, block in enumerate(blocks):
        block_id = _text(block.get("block_id"))
        if not block_id:
            continue
        ordered_ids.append(block_id)
        block_type = _text(block.get("type"))
        titles, heading_ids = _section_path(block_id, blocks_by_id)
        entry: dict[str, Any] = {
            "source_id": _text(source.get("source_id") or source.get("id")),
            "block_id": block_id,
            "block_type": block_type,
            "order": block.get("order"),
            "locator": _text(block.get("source_locator") or block.get("locator")),
            "text": _norm(block.get("text")),
            "section_path": titles,
            "section_block_ids": heading_ids,
            "list_context": dict(list_context.get(block_id, {})),
            "table_context": dict(table_context.get(block_id, {})),
            "neighbors": {
                "previous": [
                    _text(blocks[index - offset].get("block_id"))
                    for offset in range(1, min(_NEIGHBOR_WINDOW, index) + 1)
                ],
                "next": [
                    _text(blocks[index + offset].get("block_id"))
                    for offset in range(1, _NEIGHBOR_WINDOW + 1)
                    if index + offset < len(blocks)
                ],
            },
        }
        blocks_out[block_id] = entry

    return {
        "source_id": _text(source.get("source_id") or source.get("id")),
        "filename": _text(source.get("filename") or source.get("name")),
        "blocks": blocks_out,
        "block_ids": ordered_ids,
        "text_lookup": text_lookup,
        "block_count": len(blocks_out),
        "contextualized_block_count": sum(
            1
            for entry in blocks_out.values()
            if entry["list_context"] or entry["table_context"] or entry["section_path"]
        ),
    }


def _structure_sources(asset: dict[str, Any]) -> list[dict[str, Any]]:
    items = _list(_dict(asset.get("document_structure_assets")).get("items"))
    return [row for row in items if isinstance(row, dict)]


def build_chinese_semantic_context_envelopes(asset: dict[str, Any]) -> dict[str, Any]:
    """Build the per-source context envelope and store it on the asset."""
    sources = _structure_sources(asset)
    envelopes: list[dict[str, Any]] = []
    for source in sources:
        envelope = _build_source_envelope(source)
        if envelope["block_count"]:
            envelopes.append(envelope)

    ambiguous_lookup_entries = sum(
        1
        for envelope in envelopes
        for block in _dict(envelope.get("blocks")).values()
        if _norm(block.get("text")) and _norm(block.get("text")) not in envelope.get("text_lookup", {})
    )
    asset["chinese_semantic_context_envelopes"] = {
        "schema": CHINESE_SEMANTIC_CONTEXT_ENVELOPE_SCHEMA,
        "sources": envelopes,
        "receipt": {
            "schema": "qualibug.chinese-semantic-context-envelope-receipt.v1",
            "status": "PASS",
            "source_count": len(envelopes),
            "block_count": sum(row.get("block_count", 0) for row in envelopes),
            "contextualized_block_count": sum(
                row.get("contextualized_block_count", 0) for row in envelopes
            ),
            "ambiguous_lookup_entries": ambiguous_lookup_entries,
            "document_order_is_business_flow": False,
            "filename_is_business_context": False,
        },
    }
    return asset


def envelope_from_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return _dict(asset.get("chinese_semantic_context_envelopes"))


def block_context_for(
    asset: dict[str, Any],
    source_id: str,
    block_id: str,
) -> dict[str, Any]:
    """Return the envelope entry for one block (empty dict when unknown)."""
    for source in _list(envelope_from_asset(asset).get("sources")):
        if _text(source.get("source_id")) == _text(source_id):
            entry = _dict(_dict(source.get("blocks")).get(block_id))
            return dict(entry)
    return {}


def locate_unique_block(
    asset: dict[str, Any],
    *,
    source_id: str,
    quote: str,
) -> dict[str, Any]:
    """Unique-match block lookup by exact text (ambiguity resolves to none)."""
    normalized = _norm(quote)
    if not normalized:
        return {}
    for source in _list(envelope_from_asset(asset).get("sources")):
        if source_id and _text(source.get("source_id")) != _text(source_id):
            continue
        block_id = _text(_dict(source.get("text_lookup")).get(normalized))
        if block_id:
            entry = _dict(_dict(source.get("blocks")).get(block_id))
            if entry:
                return dict(entry)
    return {}
