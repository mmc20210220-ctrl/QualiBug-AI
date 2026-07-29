"""Hierarchical Chinese document context and fail-closed reference resolution.

This stage improves source comprehension only. It never creates business facts from
heading order, filename similarity, token similarity, or cross-document proximity.
A pending fact may be promoted only when its original source span belongs to a
section whose explicit heading context, or prior accepted facts inside that same
section, identifies a unique business object and/or actor.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

TREE_SCHEMA = "qualibug.chinese-document-semantic-tree.v1"
CONTEXT_RECEIPT_SCHEMA = "qualibug.chinese-document-context-resolution.v1"
SPAN_ATTACHMENT_RECEIPT_SCHEMA = "qualibug.chinese-document-span-attachment.v1"

_MARKDOWN_HEADING_RE = re.compile(r"^\s*(?P<marks>#{1,6})\s*(?P<title>.+?)\s*$")
_CHINESE_HEADING_RE = re.compile(
    r"^\s*第[一二三四五六七八九十百千0-9]+(?P<unit>章|部分|节|条)\s*(?P<title>.*?)\s*$"
)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+){0,5})[、.)．]?\s+(?P<title>.+?)\s*$"
)
_CHINESE_ORDER_HEADING_RE = re.compile(
    r"^\s*(?P<number>[一二三四五六七八九十百千]+)[、.]\s*(?P<title>.+?)\s*$"
)
_LOCATOR_RANGE_RE = re.compile(r"chars=(?P<start>\d+)-(?P<end>\d+)")
_REFERENCE_RE = re.compile(r"该|本|其|上述|前述|对应|相关|该人员|该角色|由其|由该")
_IR_HEADING_TYPE = "HEADING"
_IR_CONTENT_SPAN_TYPES = frozenset(
    {"PARAGRAPH", "LIST_ITEM", "TABLE", "TABLE_CELL", "KEY_VALUE", "NOTE"}
)
_CRITICAL_AMBIGUITY_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "CRITICAL_ACTION_",
    "EXCEPTION_SCOPE_",
    "CONDITION_COMBINATOR_",
    "TERM_ALIAS_",
    "DOCUMENT_CONTEXT_",
    "DOCUMENT_SEMANTIC_SPAN_",
)
_REFERENCE_AMBIGUITY_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "DOCUMENT_CONTEXT_",
    "OMITTED_ACTOR_",
)
_EXCEPTION_AMBIGUITY_PREFIXES = (
    "EXCEPTION_SCOPE_",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _unique(values: Iterable[Any]) -> list[str]:
    return sorted({_text(value) for value in values if _text(value)})


def _alias_map(facts: list[dict[str, Any]]) -> dict[str, str]:
    """Reuse ACCEPTED TERM_ALIAS map; conflicting aliases are already demoted."""
    from ._chinese_business_comprehension import _term_alias_map

    alias_map, _conflicts = _term_alias_map(facts)
    return alias_map


def _canonicalize(values: Iterable[Any], alias_map: dict[str, str]) -> list[str]:
    from ._chinese_business_comprehension import _canonicalize_names

    return _canonicalize_names((_text(value) for value in values), alias_map)


def _heading(line: str) -> tuple[int, str] | None:
    markdown = _MARKDOWN_HEADING_RE.match(line)
    if markdown:
        return len(markdown.group("marks")), _text(markdown.group("title"))
    chinese = _CHINESE_HEADING_RE.match(line)
    if chinese:
        level = {"章": 1, "部分": 1, "节": 2, "条": 3}.get(
            chinese.group("unit"), 2
        )
        return level, _text(chinese.group("title")) or _text(line)
    numbered = _NUMBERED_HEADING_RE.match(line)
    if numbered:
        return numbered.group("number").count(".") + 1, _text(
            numbered.group("title")
        )
    ordered = _CHINESE_ORDER_HEADING_RE.match(line)
    if ordered:
        return 1, _text(ordered.group("title"))
    return None


def _normalized_statement(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value))


def _source_identity(source: dict[str, Any]) -> tuple[str, str, str]:
    source_id = _text(source.get("source_id")) or _stable_id(
        "source", source.get("filename"), source.get("text")
    )
    filename = _text(
        source.get("filename")
        or source.get("original_name")
        or source.get("source_locator")
    )
    text = str(source.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
    return source_id, filename, text


def _root_node(source_id: str, filename: str, text: str) -> dict[str, Any]:
    root_id = _stable_id("document_node", source_id, "root")
    return {
        "node_id": root_id,
        "source_id": source_id,
        "filename": filename,
        "parent_id": "",
        "level": 0,
        "title": filename or source_id,
        "start_offset": 0,
        "content_start_offset": 0,
        "end_offset": len(text),
        # Root metadata is intentionally marked non-semantic. The filename must not
        # participate in formal business reference resolution.
        "path_titles": [],
        "semantic_heading": False,
        "span_kind": "DOCUMENT_ROOT",
        "document_block_id": "",
        "block_type": "",
        "continued_table_group_id": "",
        "attached_fact_ids": [],
        "evidence": {
            "source_id": source_id,
            "source_locator": f"{filename or source_id}#chars=0-{len(text)}",
            "quote": "",
        },
    }


def _tree_from_text_headings(source: dict[str, Any]) -> dict[str, Any]:
    """Fallback hierarchy from explicit text headings when Document IR is absent."""
    source_id, filename, text = _source_identity(source)
    root = _root_node(source_id, filename, text)
    root_id = root["node_id"]
    nodes: list[dict[str, Any]] = [root]
    stack: list[dict[str, Any]] = [root]
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        parsed = _heading(line)
        if parsed:
            level, title = parsed
            while len(stack) > 1 and int(stack[-1]["level"]) >= level:
                stack.pop()["end_offset"] = offset
            parent = stack[-1]
            node = {
                "node_id": _stable_id(
                    "document_node", source_id, offset, level, title
                ),
                "source_id": source_id,
                "filename": filename,
                "parent_id": parent["node_id"],
                "level": level,
                "title": title,
                "raw_heading": line,
                "start_offset": offset,
                "content_start_offset": offset + len(raw_line),
                "end_offset": len(text),
                "path_titles": [*parent.get("path_titles", []), title],
                "semantic_heading": True,
                "span_kind": "HEADING",
                "document_block_id": "",
                "block_type": "HEADING",
                "continued_table_group_id": "",
                "attached_fact_ids": [],
                "evidence": {
                    "source_id": source_id,
                    "source_locator": (
                        f"{filename or source_id}#chars={offset}-{offset + len(line)}"
                    ),
                    "quote": line,
                    "quote_hash": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                },
            }
            nodes.append(node)
            stack.append(node)
        offset += len(raw_line)
    while len(stack) > 1:
        stack.pop()["end_offset"] = len(text)
    return {
        "schema": TREE_SCHEMA,
        "source_id": source_id,
        "filename": filename,
        "source_char_count": len(text),
        "root_node_id": root_id,
        "nodes": nodes,
        "structure_authority": "text_heading_projection",
        "structure_underdetermined": False,
        "order_is_business_flow": False,
        "filename_is_business_context": False,
        "silent_truncation_applied": False,
        "node_count": len(nodes),
    }


def _eligible_ir_blocks(structure: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list(structure.get("blocks")):
        if not isinstance(row, dict):
            continue
        block_type = _text(row.get("type"))
        if block_type not in {_IR_HEADING_TYPE, *_IR_CONTENT_SPAN_TYPES}:
            continue
        if _text(row.get("region")) not in {"", "body"}:
            continue
        if row.get("excluded_from_main_flow") or row.get(
            "excluded_from_plain_text_projection"
        ):
            continue
        if not _text(row.get("block_id")):
            continue
        rows.append(row)
    rows.sort(key=lambda row: (int(row.get("order") or 0), _text(row.get("block_id"))))
    return rows


def _logical_table_id_for_block(
    block: dict[str, Any], block_index: dict[str, dict[str, Any]]
) -> str:
    direct = _text(block.get("logical_table_id") or block.get("continued_table_group_id"))
    if direct:
        return direct
    table_id = _text(block.get("table_block_id"))
    if table_id:
        table = block_index.get(table_id) or {}
        return _text(table.get("logical_table_id") or table_id)
    if _text(block.get("type")) == "TABLE":
        return _text(block.get("block_id"))
    return ""


def _tree_from_document_ir(source: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    """Build structure-preserving spans from Document IR blocks.

    Parentage comes only from IR heading/parent links. Document order never creates
    cross-section business joins. Continued-table groups are preserved when the IR
    already stamped ``logical_table_id``.
    """
    source_id, filename, text = _source_identity(source)
    if not text:
        text = str(structure.get("plain_text") or "").replace("\r\n", "\n").replace("\r", "\n")
    root = _root_node(source_id, filename, text)
    root_id = root["node_id"]
    blocks = _eligible_ir_blocks(structure)
    if not blocks:
        tree = _tree_from_text_headings({**source, "text": text})
        tree["structure_authority"] = "text_heading_projection"
        tree["structure_underdetermined"] = True
        tree["structure_underdetermined_reason"] = "DOCUMENT_IR_ELIGIBLE_BLOCKS_EMPTY"
        return tree

    block_index = {_text(row.get("block_id")): row for row in _list(structure.get("blocks")) if isinstance(row, dict)}
    nodes: list[dict[str, Any]] = [root]
    node_by_block: dict[str, dict[str, Any]] = {}
    heading_stack: list[dict[str, Any]] = [root]

    for block in blocks:
        block_id = _text(block.get("block_id"))
        block_type = _text(block.get("type"))
        start = int(block.get("start_offset") or 0)
        end = int(block.get("end_offset") or start)
        quote = _text(block.get("text"))
        locator = _text(block.get("source_locator")) or (
            f"{filename or source_id}#block={block.get('order')};chars={start}-{end}"
        )
        if block_type == _IR_HEADING_TYPE:
            level = int(block.get("level") or 1)
            while len(heading_stack) > 1 and int(heading_stack[-1].get("level") or 0) >= level:
                popped = heading_stack.pop()
                if int(popped.get("end_offset") or 0) < start:
                    popped["end_offset"] = start
            parent = heading_stack[-1]
            title = quote or _text(block.get("title")) or block_id
            node = {
                "node_id": _stable_id("document_node", source_id, "heading", block_id),
                "source_id": source_id,
                "filename": filename,
                "parent_id": parent["node_id"],
                "level": level,
                "title": title,
                "raw_heading": quote,
                "start_offset": start,
                "content_start_offset": end,
                "end_offset": len(text),
                "path_titles": [*_list(parent.get("path_titles")), title],
                "semantic_heading": True,
                "span_kind": "HEADING",
                "document_block_id": block_id,
                "block_type": block_type,
                "continued_table_group_id": "",
                "attached_fact_ids": [],
                "evidence": {
                    "source_id": source_id,
                    "source_locator": locator,
                    "quote": quote,
                    "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest() if quote else "",
                    "document_block_id": block_id,
                },
            }
            nodes.append(node)
            node_by_block[block_id] = node
            heading_stack.append(node)
            continue

        parent_block_id = _text(block.get("parent_id"))
        parent_node = node_by_block.get(parent_block_id)
        if parent_node is None:
            # Prefer the current heading stack (IR body heading parentage). Never climb
            # across sections by inventing joins from later document order alone.
            parent_node = heading_stack[-1]
        continued_group = _logical_table_id_for_block(block, block_index)
        node = {
            "node_id": _stable_id("document_node", source_id, "span", block_id),
            "source_id": source_id,
            "filename": filename,
            "parent_id": parent_node["node_id"],
            "level": int(parent_node.get("level") or 0) + 1,
            "title": quote[:80],
            "start_offset": start,
            "content_start_offset": start,
            "end_offset": max(end, start),
            "path_titles": list(_list(parent_node.get("path_titles"))),
            "semantic_heading": False,
            "span_kind": block_type,
            "document_block_id": block_id,
            "block_type": block_type,
            "continued_table_group_id": continued_group,
            "attached_fact_ids": [],
            "evidence": {
                "source_id": source_id,
                "source_locator": locator,
                "quote": quote,
                "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest() if quote else "",
                "document_block_id": block_id,
            },
        }
        nodes.append(node)
        node_by_block[block_id] = node

    while len(heading_stack) > 1:
        heading_stack.pop()["end_offset"] = len(text)

    continuation_groups = [
        dict(row)
        for row in _list(_dict(structure.get("visual_table_continuation_receipt")).get("groups"))
        if isinstance(row, dict)
    ]
    if not continuation_groups:
        continuation_groups = [
            dict(row) for row in _list(structure.get("table_groups")) if isinstance(row, dict)
        ]

    return {
        "schema": TREE_SCHEMA,
        "source_id": source_id,
        "filename": filename,
        "source_char_count": len(text),
        "root_node_id": root_id,
        "nodes": nodes,
        "structure_authority": "document_structure_ir",
        "structure_underdetermined": False,
        "order_is_business_flow": False,
        "filename_is_business_context": False,
        "silent_truncation_applied": False,
        "node_count": len(nodes),
        "continued_table_group_count": len(continuation_groups),
        "continued_table_groups": [
            {
                "logical_table_id": _text(row.get("logical_table_id")),
                "fragment_table_ids": _list(row.get("fragment_table_ids")),
                "pages": _list(row.get("pages")),
            }
            for row in continuation_groups
            if _text(row.get("logical_table_id"))
        ],
    }


def build_chinese_document_semantic_tree(source: dict[str, Any]) -> dict[str, Any]:
    """Build a structure-preserving Chinese document semantic tree.

    Prefer Document IR heading/list/table spans when present. Fall back to explicit
    text headings only when IR body blocks are unavailable. Never treats document
    order as business-process order.
    """
    structure = _dict(source.get("document_structure"))
    if _eligible_ir_blocks(structure):
        return _tree_from_document_ir(source, structure)
    return _tree_from_text_headings(source)


def _known_names(
    asset: dict[str, Any], facts: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    objects: list[str] = []
    actors: list[str] = []
    for row in _list(asset.get("business_objects")):
        if isinstance(row, dict):
            objects.append(_text(row.get("object") or row.get("name")))
    for row in _list(asset.get("data_tables")):
        if isinstance(row, dict):
            objects.append(_text(row.get("name")))
    for row in _list(asset.get("roles")):
        if isinstance(row, dict):
            actors.append(_text(row.get("role") or row.get("name")))
    for row in _list(asset.get("permission_matrix")):
        if isinstance(row, dict):
            actors.append(_text(row.get("role") or row.get("actor")))
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        subject = _dict(fact.get("subject"))
        objects.extend(_list(subject.get("entity_refs")))
        actors.extend(_list(subject.get("actor_refs")))
        if _text(fact.get("kind")) == "TERM_ALIAS":
            objects.extend([fact.get("canonical_term"), fact.get("alias")])
    return sorted(_unique(objects), key=lambda item: (-len(item), item)), sorted(
        _unique(actors), key=lambda item: (-len(item), item)
    )


def _fact_range(fact: dict[str, Any]) -> tuple[int, int] | None:
    spans = _list(fact.get("source_spans"))
    span = _dict(spans[0]) if spans else {}
    match = _LOCATOR_RANGE_RE.search(
        _text(span.get("locator") or span.get("source_locator"))
    )
    if not match:
        return None
    return int(match.group("start")), int(match.group("end"))


def _fact_source_id(fact: dict[str, Any]) -> str:
    spans = _list(fact.get("source_spans"))
    span = _dict(spans[0]) if spans else {}
    return _text(span.get("source_id") or fact.get("source_id"))


def _deepest_node(tree: dict[str, Any], start: int) -> dict[str, Any] | None:
    matches = [
        node
        for node in _list(tree.get("nodes"))
        if isinstance(node, dict)
        and int(node.get("start_offset") or 0)
        <= start
        < int(node.get("end_offset") or 0)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda node: (
            # Prefer concrete content spans (list/table/cell) over enclosing headings.
            1 if _text(node.get("span_kind")) in _IR_CONTENT_SPAN_TYPES else 0,
            int(node.get("level") or 0),
            int(node.get("start_offset") or 0),
        ),
    )


def _node_by_block_id(tree: dict[str, Any], block_id: str) -> dict[str, Any] | None:
    target = _text(block_id)
    if not target:
        return None
    for node in _list(tree.get("nodes")):
        if isinstance(node, dict) and _text(node.get("document_block_id")) == target:
            return node
    return None


def _section_node(tree: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Nearest semantic heading ancestor; never invents a section from document order."""
    nodes_by_id = {
        _text(row.get("node_id")): row
        for row in _list(tree.get("nodes"))
        if isinstance(row, dict) and _text(row.get("node_id"))
    }
    current: dict[str, Any] | None = node
    visited: set[str] = set()
    while current:
        node_id = _text(current.get("node_id"))
        if not node_id or node_id in visited:
            break
        visited.add(node_id)
        if bool(current.get("semantic_heading")):
            return current
        parent_id = _text(current.get("parent_id"))
        current = nodes_by_id.get(parent_id)
    return node


def _statement_span_candidates(
    statement: str, tree: dict[str, Any]
) -> list[dict[str, Any]]:
    target = _normalized_statement(statement)
    if not target:
        return []
    exact: list[dict[str, Any]] = []
    contained: list[dict[str, Any]] = []
    for node in _list(tree.get("nodes")):
        if not isinstance(node, dict):
            continue
        if _text(node.get("span_kind")) not in _IR_CONTENT_SPAN_TYPES:
            continue
        quote = _normalized_statement(_dict(node.get("evidence")).get("quote") or node.get("title"))
        if not quote:
            continue
        if quote == target:
            exact.append(node)
        elif target in quote:
            contained.append(node)
    candidates = exact or contained
    if len(candidates) <= 1:
        return candidates
    specificity = {
        "TABLE_CELL": 40,
        "LIST_ITEM": 30,
        "KEY_VALUE": 30,
        "NOTE": 20,
        "PARAGRAPH": 10,
        "TABLE": 0,
    }
    best = max(specificity.get(_text(row.get("span_kind")), 0) for row in candidates)
    refined = [
        row
        for row in candidates
        if specificity.get(_text(row.get("span_kind")), 0) == best
    ]
    return refined or candidates


def _heading_candidates(node: dict[str, Any], names: list[str]) -> list[str]:
    if not bool(node.get("semantic_heading")) and not _list(node.get("path_titles")):
        return []
    # path_titles contains explicit headings only; root filename is excluded.
    path = " ".join(_text(value) for value in _list(node.get("path_titles")))
    if bool(node.get("semantic_heading")):
        path = f"{path} {_text(node.get('title'))}".strip()
    return [name for name in names if name and name in path]


def _prior_fact_candidates(
    fact: dict[str, Any],
    node: dict[str, Any],
    facts: list[dict[str, Any]],
    *,
    kind: str,
    tree: dict[str, Any] | None = None,
) -> list[str]:
    """Collect unique same-section prior subjects. Never join across section spans."""
    current_range = _fact_range(fact)
    source_id = _fact_source_id(fact)
    prior: list[tuple[int, list[str]]] = []
    section = _section_node(tree, node) if tree else node
    section_id = _text(section.get("node_id"))
    section_start = int(
        section.get("content_start_offset") or section.get("start_offset") or 0
    )
    current_attachment = _dict(fact.get("structural_span_attachment"))
    current_section = _text(current_attachment.get("section_node_id")) or section_id
    nodes_by_id = {
        _text(row.get("node_id")): row
        for row in _list((tree or {}).get("nodes"))
        if isinstance(row, dict) and _text(row.get("node_id"))
    }

    for other in facts:
        if other is fact or _text(other.get("status")) != "ACCEPTED":
            continue
        if _fact_source_id(other) != source_id:
            continue
        other_attachment = _dict(other.get("structural_span_attachment"))
        other_section = _text(other_attachment.get("section_node_id"))
        if not other_section and _text(other_attachment.get("node_id")):
            other_node = nodes_by_id.get(_text(other_attachment.get("node_id")))
            if other_node is not None and tree is not None:
                other_section = _text(_section_node(tree, other_node).get("node_id"))
        if other_section:
            if other_section != current_section:
                continue
        else:
            other_range = _fact_range(other)
            if (
                not current_range
                or not other_range
                or other_range[0] >= current_range[0]
                or other_range[0] < section_start
            ):
                continue
        other_range = _fact_range(other)
        if current_range and other_range and other_range[0] >= current_range[0]:
            continue
        subject = _dict(other.get("subject"))
        values = (
            _list(subject.get("entity_refs"))
            if kind == "object"
            else _list(subject.get("actor_refs"))
        )
        if values:
            order_key = (
                other_range[0]
                if other_range
                else int(other_attachment.get("order") or 0)
            )
            prior.append((order_key, _unique(values)))
    prior.sort(key=lambda row: row[0], reverse=True)
    return _unique(value for _, values in prior[:3] for value in values)


def _attachment_payload(
    *,
    fact: dict[str, Any],
    node: dict[str, Any],
    tree: dict[str, Any],
    method: str,
    status: str,
    reason: str = "",
    candidate_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    section = _section_node(tree, node) if node else {}
    return {
        "schema": SPAN_ATTACHMENT_RECEIPT_SCHEMA,
        "fact_id": fact.get("fact_id"),
        "source_id": _fact_source_id(fact),
        "status": status,
        "method": method,
        "reason": reason,
        "node_id": node.get("node_id") if node else "",
        "section_node_id": section.get("node_id") if section else "",
        "section_path": list(_list(section.get("path_titles") if section else [])),
        "span_kind": node.get("span_kind") if node else "",
        "document_block_id": node.get("document_block_id") if node else "",
        "block_type": node.get("block_type") if node else "",
        "continued_table_group_id": node.get("continued_table_group_id") if node else "",
        "source_locator": _text(_dict(node.get("evidence") if node else {}).get("source_locator")),
        "order_is_business_flow": False,
        "cross_section_join_invented": False,
        "candidate_node_ids": candidate_node_ids or [],
    }


def _invalidate_cross_section_coreference(
    facts: list[dict[str, Any]], trees: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Demote flat-text coreference that crossed structural section spans.

    ``nearest_unambiguous_*`` may claim section_scoped while text heading detection
    failed to see IR section boundaries. After structure-preserving attachment, any
    such promotion whose resolved subject is not justified by the same structural
    section is cleared visibly instead of silently keeping a cross-section join.
    """
    tree_by_source = {
        _text(tree.get("source_id")): tree
        for tree in trees
        if isinstance(tree, dict) and _text(tree.get("source_id"))
    }
    invalidated: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        subject = _dict(fact.get("subject"))
        evidence_rows = [
            row
            for row in _list(subject.get("resolution_evidence"))
            if isinstance(row, dict)
            and _text(row.get("method")).startswith("nearest_unambiguous_")
        ]
        if not evidence_rows:
            continue
        attachment = _dict(fact.get("structural_span_attachment"))
        if _text(attachment.get("status")) != "ATTACHED":
            continue
        tree = tree_by_source.get(_fact_source_id(fact))
        if not tree:
            continue
        section_id = _text(attachment.get("section_node_id"))
        section_path = " ".join(_text(value) for value in _list(attachment.get("section_path")))
        justified_objects = {
            name
            for name in _heading_candidates(
                {
                    "semantic_heading": True,
                    "path_titles": _list(attachment.get("section_path")),
                    "title": section_path,
                },
                _list(subject.get("entity_refs")),
            )
        }
        justified_objects.update(
            _prior_fact_candidates(
                fact,
                {
                    "node_id": section_id,
                    "content_start_offset": 0,
                    "start_offset": 0,
                    "path_titles": _list(attachment.get("section_path")),
                    "semantic_heading": True,
                },
                facts,
                kind="object",
                tree=tree,
            )
        )
        justified_actors = {
            name
            for name in _heading_candidates(
                {
                    "semantic_heading": True,
                    "path_titles": _list(attachment.get("section_path")),
                    "title": section_path,
                },
                _list(subject.get("actor_refs")),
            )
        }
        justified_actors.update(
            _prior_fact_candidates(
                fact,
                {
                    "node_id": section_id,
                    "content_start_offset": 0,
                    "start_offset": 0,
                    "path_titles": _list(attachment.get("section_path")),
                    "semantic_heading": True,
                },
                facts,
                kind="actor",
                tree=tree,
            )
        )
        dropped_entities = [
            _text(row.get("resolved_ref"))
            for row in evidence_rows
            if _text(row.get("method")) == "nearest_unambiguous_entity_context"
            and _text(row.get("resolved_ref"))
            and _text(row.get("resolved_ref")) not in justified_objects
        ]
        dropped_actors = [
            _text(row.get("resolved_ref"))
            for row in evidence_rows
            if _text(row.get("method")) == "nearest_unambiguous_actor_context"
            and _text(row.get("resolved_ref"))
            and _text(row.get("resolved_ref")) not in justified_actors
        ]
        if not dropped_entities and not dropped_actors:
            continue
        remaining_evidence = [
            row
            for row in _list(subject.get("resolution_evidence"))
            if not (
                isinstance(row, dict)
                and _text(row.get("method")).startswith("nearest_unambiguous_")
                and _text(row.get("resolved_ref")) in {*dropped_entities, *dropped_actors}
            )
        ]
        entity_refs = [
            name
            for name in _list(subject.get("entity_refs"))
            if _text(name) not in dropped_entities
        ]
        actor_refs = [
            name
            for name in _list(subject.get("actor_refs"))
            if _text(name) not in dropped_actors
        ]
        fact["subject"] = {
            **subject,
            "entity_refs": entity_refs,
            "actor_refs": actor_refs,
            "resolution_evidence": remaining_evidence,
        }
        object_part = _dict(fact.get("object"))
        fact["object"] = {
            **object_part,
            "entity_refs": [
                name
                for name in _list(object_part.get("entity_refs"))
                if _text(name) not in dropped_entities
            ],
        }
        ambiguities = _unique(
            [
                *_list(fact.get("ambiguities")),
                "DOCUMENT_SEMANTIC_SPAN_CROSS_SECTION_JOIN_FORBIDDEN",
                *(
                    ["COREFERENCE_UNRESOLVED"]
                    if dropped_entities and not entity_refs
                    else []
                ),
                *(
                    ["OMITTED_ACTOR_UNRESOLVED"]
                    if dropped_actors and not actor_refs
                    else []
                ),
            ]
        )
        fact["ambiguities"] = ambiguities
        fact["status"] = "PENDING"
        invalidated.append(
            {
                "fact_id": fact.get("fact_id"),
                "section_node_id": section_id,
                "dropped_entities": dropped_entities,
                "dropped_actors": dropped_actors,
                "reason": "DOCUMENT_SEMANTIC_SPAN_CROSS_SECTION_JOIN_FORBIDDEN",
            }
        )
    return invalidated


def attach_facts_to_structural_spans(
    facts: list[dict[str, Any]],
    trees: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach each fact to exactly one structural span. Fail closed when underdetermined.

    Never invents cross-section joins from document order. Ambiguous or missing
    attachments remain visible instead of silently dropping the fact.
    """
    tree_by_source = {
        _text(tree.get("source_id")): tree
        for tree in trees
        if isinstance(tree, dict) and _text(tree.get("source_id"))
    }
    attached: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        source_id = _fact_source_id(fact)
        tree = tree_by_source.get(source_id)
        if not tree:
            payload = _attachment_payload(
                fact=fact,
                node={},
                tree={},
                method="",
                status="UNATTACHED",
                reason="DOCUMENT_SEMANTIC_SPAN_TREE_UNAVAILABLE",
            )
            fact["structural_span_attachment"] = payload
            fact["ambiguities"] = _unique(
                [*_list(fact.get("ambiguities")), "DOCUMENT_SEMANTIC_SPAN_TREE_UNAVAILABLE"]
            )
            unresolved.append(payload)
            continue

        alignment = _dict(fact.get("document_structure_alignment"))
        node: dict[str, Any] | None = None
        method = ""
        if _text(alignment.get("block_id")):
            node = _node_by_block_id(tree, alignment.get("block_id"))
            if node is not None:
                method = "document_ir_alignment_block"
        if node is None:
            candidates = _statement_span_candidates(
                _text(fact.get("raw_statement") or fact.get("statement")), tree
            )
            if len(candidates) == 1:
                node = candidates[0]
                method = "unique_statement_structural_span"
            elif len(candidates) > 1:
                payload = _attachment_payload(
                    fact=fact,
                    node={},
                    tree=tree,
                    method="",
                    status="AMBIGUOUS",
                    reason="DOCUMENT_SEMANTIC_SPAN_AMBIGUOUS",
                    candidate_node_ids=[_text(row.get("node_id")) for row in candidates],
                )
                fact["structural_span_attachment"] = payload
                fact["ambiguities"] = _unique(
                    [*_list(fact.get("ambiguities")), "DOCUMENT_SEMANTIC_SPAN_AMBIGUOUS"]
                )
                if _text(fact.get("status")) == "ACCEPTED":
                    # Underdetermined structure cannot silently keep a promoted attachment.
                    fact["status"] = "PENDING"
                unresolved.append(payload)
                continue
        if node is None:
            fact_range = _fact_range(fact)
            if fact_range:
                node = _deepest_node(tree, fact_range[0])
                if node is not None and _text(node.get("node_id")) != _text(tree.get("root_node_id")):
                    method = "unique_character_range_structural_span"
                else:
                    node = None
        if node is None:
            payload = _attachment_payload(
                fact=fact,
                node={},
                tree=tree,
                method="",
                status="UNATTACHED",
                reason="DOCUMENT_SEMANTIC_SPAN_UNATTACHED",
            )
            fact["structural_span_attachment"] = payload
            # Flat-text trees without content spans remain attachable only via range;
            # leave as visible unresolved when structure authority expected IR fidelity.
            if _text(tree.get("structure_authority")) == "document_structure_ir":
                fact["ambiguities"] = _unique(
                    [*_list(fact.get("ambiguities")), "DOCUMENT_SEMANTIC_SPAN_UNATTACHED"]
                )
            unresolved.append(payload)
            continue

        payload = _attachment_payload(
            fact=fact,
            node=node,
            tree=tree,
            method=method,
            status="ATTACHED",
        )
        fact["structural_span_attachment"] = payload
        attached_ids = _unique([*_list(node.get("attached_fact_ids")), fact.get("fact_id")])
        node["attached_fact_ids"] = attached_ids
        attached.append(payload)
    return facts, attached, unresolved


def _unique_context_candidate(
    heading: list[str],
    prior: list[str],
    *,
    alias_map: dict[str, str] | None = None,
) -> tuple[str, str, list[str]]:
    alias_map = alias_map or {}
    heading_values = _canonicalize(heading, alias_map)
    prior_values = _canonicalize(prior, alias_map)
    if len(heading_values) == 1 and len(prior_values) == 1:
        if heading_values[0] == prior_values[0]:
            return heading_values[0], "heading_and_same_section_prior_fact", []
        return "", "", [
            f"DOCUMENT_CONTEXT_CONFLICT:{heading_values[0]}_vs_{prior_values[0]}"
        ]
    if len(heading_values) == 1:
        return heading_values[0], "unique_heading_context", []
    if len(heading_values) > 1:
        return "", "", [
            "DOCUMENT_CONTEXT_HEADING_AMBIGUOUS:" + ",".join(heading_values)
        ]
    if len(prior_values) == 1:
        return prior_values[0], "unique_prior_fact_in_same_section", []
    if len(prior_values) > 1:
        return "", "", [
            "DOCUMENT_CONTEXT_PRIOR_FACT_AMBIGUOUS:" + ",".join(prior_values)
        ]
    return "", "", []


def _refresh_coverage(asset: dict[str, Any], facts: list[dict[str, Any]]) -> None:
    by_locator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        spans = _list(fact.get("source_spans"))
        span = _dict(spans[0]) if spans else {}
        locator = _text(span.get("locator") or span.get("source_locator"))
        if locator:
            by_locator[locator].append(fact)
    ledger = _dict(asset.get("document_coverage_ledger"))
    rows: list[dict[str, Any]] = []
    for raw in _list(ledger.get("items")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        related = by_locator.get(_text(row.get("source_locator")), [])
        if related:
            row["ambiguities"] = _unique(
                value for fact in related for value in _list(fact.get("ambiguities"))
            )
            row["extracted_fact_ids"] = _unique(
                fact.get("fact_id") for fact in related
            )
            if any(_text(fact.get("status")) == "PENDING" for fact in related):
                row["status"] = "AMBIGUOUS"
            elif _text(row.get("status")) == "AMBIGUOUS":
                row["status"] = "UNDERSTOOD"
        rows.append(row)
    ledger["items"] = rows
    asset["document_coverage_ledger"] = ledger


def _refresh_gate(asset: dict[str, Any], facts: list[dict[str, Any]]) -> None:
    ledger = _dict(asset.get("document_coverage_ledger"))
    coverage = [row for row in _list(ledger.get("items")) if isinstance(row, dict)]
    unresolved = [
        row
        for row in coverage
        if _text(row.get("status")) in {"AMBIGUOUS", "UNRESOLVED_BUSINESS_TEXT"}
    ]
    critical_unknowns = [
        {
            "chunk_id": row.get("chunk_id"),
            "source_id": row.get("source_id"),
            "source_locator": row.get("source_locator"),
            "ambiguities": row.get("ambiguities"),
        }
        for row in unresolved
        if row.get("contains_business_signal")
        and (
            _text(row.get("status")) == "UNRESOLVED_BUSINESS_TEXT"
            or any(
                _text(value).startswith(_CRITICAL_AMBIGUITY_PREFIXES)
                for value in _list(row.get("ambiguities"))
            )
        )
    ]
    gate = _dict(asset.get("enterprise_comprehension_gate"))
    metrics = _dict(gate.get("metrics"))
    metrics.update(
        {
            "accepted_fact_count": sum(
                1 for fact in facts if _text(fact.get("status")) == "ACCEPTED"
            ),
            "pending_fact_count": sum(
                1 for fact in facts if _text(fact.get("status")) == "PENDING"
            ),
            "unresolved_chunk_count": len(unresolved),
            "critical_ambiguity_count": len(critical_unknowns),
            "status_distribution": dict(
                Counter(_text(row.get("status")) for row in coverage)
            ),
        }
    )
    gate["metrics"] = metrics
    gate["critical_unknowns"] = critical_unknowns
    if critical_unknowns:
        gate["status"] = "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE"
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "resolve remaining Chinese document-context ambiguity before formal business understanding"
        )
    elif _text(gate.get("status")) == "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE":
        gate["status"] = "PASS"
        gate["entry_allowed"] = True
        gate["required_operator_action"] = ""
    asset["enterprise_comprehension_gate"] = gate


def _context_needed(fact: dict[str, Any], ambiguities: list[str]) -> bool:
    if _text(fact.get("status")) == "PENDING" and any(
        value.startswith(_REFERENCE_AMBIGUITY_PREFIXES) for value in ambiguities
    ):
        return True
    if _text(fact.get("status")) == "PENDING" and any(
        value.startswith(_EXCEPTION_AMBIGUITY_PREFIXES) for value in ambiguities
    ):
        return True
    # An accepted fact can still omit an actor. Enrich only when an explicit
    # Chinese reference marker exists; plain actorless statements are not inferred.
    subject = _dict(fact.get("subject"))
    return bool(
        not _list(subject.get("actor_refs"))
        and _REFERENCE_RE.search(_text(fact.get("raw_statement")))
    )


def _reduce_exception_scope(
    fact: dict[str, Any],
    node: dict[str, Any],
    facts: list[dict[str, Any]],
    *,
    tree: dict[str, Any] | None = None,
) -> bool:
    """Clear EXCEPTION_SCOPE_* only when same-section prior ACCEPTED fact uniquely binds.

    Never invent an exception actor from industry knowledge. Promotion requires an
    explicit exception_scope on the fact or a unique prior base rule in the section.
    """
    ambiguities = _unique(_list(fact.get("ambiguities")))
    if not any(value.startswith(_EXCEPTION_AMBIGUITY_PREFIXES) for value in ambiguities):
        return False
    explicit_scopes = _unique(_list(fact.get("exception_scope")))
    if len(explicit_scopes) == 1:
        remaining = [
            value
            for value in ambiguities
            if not value.startswith(_EXCEPTION_AMBIGUITY_PREFIXES)
        ]
        fact["ambiguities"] = remaining
        fact["status"] = "ACCEPTED" if not remaining else "PENDING"
        subject = _dict(fact.get("subject"))
        evidence = list(_list(subject.get("resolution_evidence")))
        evidence.append(
            {
                "mention": "例外范围",
                "resolved_ref": explicit_scopes[0],
                "method": "explicit_exception_scope_in_source",
                "document_node_id": node.get("node_id"),
                "section_path": node.get("path_titles"),
                "confidence": 0.9,
            }
        )
        fact["subject"] = {**subject, "resolution_evidence": evidence}
        return True
    # Unique prior ACCEPTED rule in the same section can anchor exception scope to
    # that rule's subject when the exception fact itself has no competing subjects.
    prior_objects = _prior_fact_candidates(
        fact, node, facts, kind="object", tree=tree
    )
    prior_actors = _prior_fact_candidates(
        fact, node, facts, kind="actor", tree=tree
    )
    subject = _dict(fact.get("subject"))
    if (
        not _list(subject.get("entity_refs"))
        and len(prior_objects) == 1
        and len(explicit_scopes) == 0
    ):
        # Still unresolved: exception actor unknown. Keep PENDING.
        return False
    if len(prior_objects) <= 1 and len(prior_actors) <= 1 and explicit_scopes:
        # Multiple explicit scopes already handled above; nothing further to invent.
        return False
    return False


def apply_chinese_document_context(
    asset: dict[str, Any], parsed_sources: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Resolve same-document references using unique hierarchical evidence."""
    trees = [
        build_chinese_document_semantic_tree(source)
        for source in parsed_sources
        if isinstance(source, dict)
    ]
    tree_by_source = {
        _text(tree.get("source_id")): tree
        for tree in trees
        if _text(tree.get("source_id"))
    }
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    # Attach facts to structural spans before reference resolution so same-section
    # priors cannot leak across headings/lists/tables from document order alone.
    facts, span_attached, span_unresolved = attach_facts_to_structural_spans(facts, trees)
    cross_section_invalidations = _invalidate_cross_section_coreference(facts, trees)
    object_names, actor_names = _known_names(asset, facts)
    alias_map = _alias_map(facts)
    resolutions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for fact in facts:
        ambiguities = _unique(_list(fact.get("ambiguities")))
        if not _context_needed(fact, ambiguities):
            continue
        fact_range = _fact_range(fact)
        source_id = _fact_source_id(fact)
        tree = tree_by_source.get(source_id)
        attachment = _dict(fact.get("structural_span_attachment"))
        node = None
        if tree and _text(attachment.get("node_id")):
            node = next(
                (
                    row
                    for row in _list(tree.get("nodes"))
                    if isinstance(row, dict)
                    and _text(row.get("node_id")) == _text(attachment.get("node_id"))
                ),
                None,
            )
        if node is None and fact_range and tree:
            node = _deepest_node(tree, fact_range[0])
        if not tree or not node:
            unresolved.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "reason": (
                        "DOCUMENT_CONTEXT_SOURCE_RANGE_UNAVAILABLE"
                        if not fact_range and not attachment.get("node_id")
                        else "DOCUMENT_CONTEXT_NODE_UNAVAILABLE"
                    ),
                }
            )
            continue
        # Prefer the section heading for heading-name candidates while keeping the
        # concrete content span for attachment identity.
        section_node = _section_node(tree, node)

        # EXCEPTION_SCOPE reduction is independent of pronoun resolution.
        if _reduce_exception_scope(fact, section_node, facts, tree=tree):
            resolutions.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "resolved_object": "",
                    "resolved_actor": "",
                    "resolved_exception_scope": (_list(fact.get("exception_scope")) or [""])[0],
                    "document_node_id": node.get("node_id"),
                    "section_path": section_node.get("path_titles"),
                    "status": fact.get("status"),
                }
            )
            ambiguities = _unique(_list(fact.get("ambiguities")))
            if not _context_needed(fact, ambiguities):
                continue

        subject = _dict(fact.get("subject"))
        existing_objects = _unique(_list(subject.get("entity_refs")))
        existing_actors = _unique(_list(subject.get("actor_refs")))
        object_value = ""
        object_method = ""
        object_errors: list[str] = []
        if not existing_objects:
            object_value, object_method, object_errors = _unique_context_candidate(
                _heading_candidates(section_node, object_names),
                _prior_fact_candidates(
                    fact, section_node, facts, kind="object", tree=tree
                ),
                alias_map=alias_map,
            )
        actor_value = ""
        actor_method = ""
        actor_errors: list[str] = []
        if not existing_actors:
            actor_value, actor_method, actor_errors = _unique_context_candidate(
                _heading_candidates(section_node, actor_names),
                _prior_fact_candidates(
                    fact, section_node, facts, kind="actor", tree=tree
                ),
                alias_map=alias_map,
            )
        context_errors = [*object_errors, *actor_errors]
        if context_errors:
            fact["ambiguities"] = _unique([*ambiguities, *context_errors])
            unresolved.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "reason": context_errors,
                    "document_node_id": node.get("node_id"),
                }
            )
            continue
        if not object_value and not actor_value:
            unresolved.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "reason": "DOCUMENT_CONTEXT_NO_UNIQUE_REFERENCE",
                    "document_node_id": node.get("node_id"),
                }
            )
            continue

        entity_refs = _unique([*existing_objects, object_value])
        actor_refs = _unique([*existing_actors, actor_value])
        resolution_evidence = list(_list(subject.get("resolution_evidence")))
        if object_value:
            resolution_evidence.append(
                {
                    "mention": "中文省略/指代对象",
                    "resolved_ref": object_value,
                    "method": object_method,
                    "document_node_id": node.get("node_id"),
                    "section_node_id": section_node.get("node_id"),
                    "section_path": section_node.get("path_titles"),
                    "heading_evidence": section_node.get("evidence"),
                    "structural_span_attachment": attachment,
                    "confidence": (
                        0.9 if object_method.startswith("unique_heading") else 0.84
                    ),
                    "alias_aware": bool(alias_map),
                }
            )
        if actor_value:
            resolution_evidence.append(
                {
                    "mention": "中文省略/指代角色",
                    "resolved_ref": actor_value,
                    "method": actor_method,
                    "document_node_id": node.get("node_id"),
                    "section_node_id": section_node.get("node_id"),
                    "section_path": section_node.get("path_titles"),
                    "heading_evidence": section_node.get("evidence"),
                    "structural_span_attachment": attachment,
                    "confidence": (
                        0.9 if actor_method.startswith("unique_heading") else 0.84
                    ),
                    "alias_aware": bool(alias_map),
                }
            )
        fact["subject"] = {
            **subject,
            "entity_refs": entity_refs,
            "actor_refs": actor_refs,
            "resolution_evidence": resolution_evidence,
        }
        fact["object"] = {
            **_dict(fact.get("object")),
            "entity_refs": entity_refs,
        }
        remaining = [
            value
            for value in ambiguities
            if not value.startswith(_REFERENCE_AMBIGUITY_PREFIXES)
            and not value.startswith(_EXCEPTION_AMBIGUITY_PREFIXES)
            and not value.startswith("DOCUMENT_SEMANTIC_SPAN_CROSS_SECTION_")
        ]
        # Preserve unresolved EXCEPTION_SCOPE unless already reduced above.
        if any(
            value.startswith(_EXCEPTION_AMBIGUITY_PREFIXES)
            for value in ambiguities
        ) and not _list(fact.get("exception_scope")):
            remaining = _unique(
                [
                    *remaining,
                    *[
                        value
                        for value in ambiguities
                        if value.startswith(_EXCEPTION_AMBIGUITY_PREFIXES)
                    ],
                ]
            )
        fact["ambiguities"] = remaining
        fact["status"] = "ACCEPTED" if not remaining else "PENDING"
        fact["document_context"] = {
            "node_id": node.get("node_id"),
            "section_node_id": section_node.get("node_id"),
            "section_path": section_node.get("path_titles"),
            "source_backed": True,
            "filename_used_as_context": False,
            "cross_document_resolution_used": False,
            "term_alias_aware": bool(alias_map),
            "structural_span_attachment": attachment,
        }
        resolutions.append(
            {
                "fact_id": fact.get("fact_id"),
                "resolved_object": object_value,
                "resolved_actor": actor_value,
                "document_node_id": node.get("node_id"),
                "section_path": section_node.get("path_titles"),
                "status": fact.get("status"),
            }
        )

    ledger["items"] = facts
    ledger["document_context_contract"] = {
        "same_source_only": True,
        "explicit_heading_or_same_section_prior_fact_required": True,
        "filename_context_forbidden": True,
        "document_order_is_not_business_flow": True,
        "cross_document_proximity_forbidden": True,
        "structural_span_attachment_required_for_ir_sources": True,
        "cross_section_join_from_document_order_forbidden": True,
    }
    asset["business_fact_ledger"] = ledger
    asset["document_semantic_trees"] = {
        "schema": TREE_SCHEMA,
        "source_count": len(trees),
        "items": trees,
        "silent_truncation_applied": any(
            bool(tree.get("silent_truncation_applied")) for tree in trees
        ),
        "structure_authority_distribution": dict(
            Counter(_text(tree.get("structure_authority")) for tree in trees)
        ),
    }
    asset["document_span_attachment_receipt"] = {
        "schema": SPAN_ATTACHMENT_RECEIPT_SCHEMA,
        "attached_fact_count": len(span_attached),
        "unresolved_fact_count": len(span_unresolved),
        "attached": span_attached,
        "unresolved": span_unresolved,
        "cross_section_coreference_invalidated_count": len(cross_section_invalidations),
        "cross_section_coreference_invalidations": cross_section_invalidations,
        "cross_section_join_from_document_order_allowed": False,
        "silent_drop_forbidden": True,
    }
    asset["document_context_resolution_receipt"] = {
        "schema": CONTEXT_RECEIPT_SCHEMA,
        "resolved_fact_count": len(resolutions),
        "unresolved_fact_count": len(unresolved),
        "resolutions": resolutions,
        "unresolved": unresolved,
        "fact_authority": "original_chinese_source_span_and_explicit_heading_span",
        "filename_context_allowed": False,
        "cross_document_proximity_resolution_allowed": False,
        "structural_span_attachment_enabled": True,
    }

    # Context can promote a pending fact. Rebuild Chinese-derived rules from the
    # updated ledger; the integration stage immediately re-runs conflict authority.
    from ._chinese_business_comprehension import _rule_from_fact

    preserved_rules = [
        dict(row)
        for row in _list(asset.get("rule_library"))
        if isinstance(row, dict)
        and _text(row.get("derivation")) != "chinese_first_business_comprehension"
    ]
    promoted_rules = [
        rule for fact in facts if (rule := _rule_from_fact(fact)) is not None
    ]
    asset["rule_library"] = [*preserved_rules, *promoted_rules]

    _refresh_coverage(asset, facts)
    _refresh_gate(asset, facts)
    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "document_semantic_tree_source_count": len(trees),
            "document_semantic_tree_node_count": sum(
                int(tree.get("node_count") or len(_list(tree.get("nodes"))))
                for tree in trees
            ),
            "document_span_attached_fact_count": len(span_attached),
            "document_span_unresolved_fact_count": len(span_unresolved),
            "document_context_resolved_fact_count": len(resolutions),
            "document_context_unresolved_fact_count": len(unresolved),
            "chinese_business_fact_accepted": sum(
                1 for fact in facts if _text(fact.get("status")) == "ACCEPTED"
            ),
            "chinese_business_fact_pending": sum(
                1 for fact in facts if _text(fact.get("status")) == "PENDING"
            ),
            "rule_count": len(asset["rule_library"]),
        }
    )
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "chinese_document_semantic_tree_enabled": True,
            "chinese_document_semantic_tree_enterprise_scale": True,
            "structure_preserving_span_fact_attachment_enabled": True,
            "same_section_context_resolution_source_backed": True,
            "filename_cannot_resolve_business_reference": True,
            "document_order_cannot_create_business_flow": True,
            "cross_document_proximity_cannot_resolve_references": True,
            "cross_section_join_from_document_order_forbidden": True,
            "structural_span_underdetermined_fails_closed": True,
            "structured_fact_silent_truncation_forbidden": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "TREE_SCHEMA",
    "CONTEXT_RECEIPT_SCHEMA",
    "SPAN_ATTACHMENT_RECEIPT_SCHEMA",
    "build_chinese_document_semantic_tree",
    "attach_facts_to_structural_spans",
    "apply_chinese_document_context",
]
