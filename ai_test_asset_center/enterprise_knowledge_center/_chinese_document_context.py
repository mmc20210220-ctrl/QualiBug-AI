"""Hierarchical Chinese document context and fail-closed reference resolution.

This stage improves source comprehension only. It never creates business facts from
heading order, token similarity, or cross-document proximity. A pending fact may be
promoted only when its original source span belongs to a document section whose
heading context, or prior accepted facts inside the same section, identifies a unique
business object and/or actor.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any, Iterable


TREE_SCHEMA = "qualibug.chinese-document-semantic-tree.v1"
CONTEXT_RECEIPT_SCHEMA = "qualibug.chinese-document-context-resolution.v1"

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
_CRITICAL_AMBIGUITY_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "CRITICAL_ACTION_",
    "EXCEPTION_SCOPE_",
    "DOCUMENT_CONTEXT_",
)
_REFERENCE_AMBIGUITY_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "DOCUMENT_CONTEXT_",
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


def _heading(line: str) -> tuple[int, str] | None:
    markdown = _MARKDOWN_HEADING_RE.match(line)
    if markdown:
        return len(markdown.group("marks")), _text(markdown.group("title"))
    chinese = _CHINESE_HEADING_RE.match(line)
    if chinese:
        level = {"章": 1, "部分": 1, "节": 2, "条": 3}.get(chinese.group("unit"), 2)
        title = _text(chinese.group("title")) or _text(line)
        return level, title
    numbered = _NUMBERED_HEADING_RE.match(line)
    if numbered:
        return numbered.group("number").count(".") + 1, _text(numbered.group("title"))
    ordered = _CHINESE_ORDER_HEADING_RE.match(line)
    if ordered:
        return 1, _text(ordered.group("title"))
    return None


def build_chinese_document_semantic_tree(source: dict[str, Any]) -> dict[str, Any]:
    """Build a source-range document tree without interpreting heading order as flow."""
    source_id = _text(source.get("source_id")) or _stable_id(
        "source", source.get("filename"), source.get("text")
    )
    filename = _text(
        source.get("filename")
        or source.get("original_name")
        or source.get("source_locator")
    )
    text = str(source.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
    root_id = _stable_id("document_node", source_id, "root")
    nodes: list[dict[str, Any]] = [
        {
            "node_id": root_id,
            "source_id": source_id,
            "filename": filename,
            "parent_id": "",
            "level": 0,
            "title": filename or source_id,
            "start_offset": 0,
            "content_start_offset": 0,
            "end_offset": len(text),
            "path_titles": [filename or source_id],
            "evidence": {
                "source_id": source_id,
                "source_locator": f"{filename or source_id}#chars=0-{len(text)}",
                "quote": "",
            },
        }
    ]
    stack: list[dict[str, Any]] = [nodes[0]]
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        parsed = _heading(line)
        if parsed:
            level, title = parsed
            while len(stack) > 1 and int(stack[-1]["level"]) >= level:
                completed = stack.pop()
                completed["end_offset"] = offset
            parent = stack[-1]
            node = {
                "node_id": _stable_id("document_node", source_id, offset, level, title),
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
                "evidence": {
                    "source_id": source_id,
                    "source_locator": f"{filename or source_id}#chars={offset}-{offset + len(line)}",
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
        "order_is_business_flow": False,
    }


def _known_names(asset: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
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
    match = _LOCATOR_RANGE_RE.search(_text(span.get("locator") or span.get("source_locator")))
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
        and int(node.get("start_offset") or 0) <= start < int(node.get("end_offset") or 0)
    ]
    if not matches:
        return None
    return max(matches, key=lambda node: (int(node.get("level") or 0), int(node.get("start_offset") or 0)))


def _heading_candidates(node: dict[str, Any], names: list[str]) -> list[str]:
    path = " ".join(_text(value) for value in _list(node.get("path_titles")))
    return [name for name in names if name and name in path]


def _prior_fact_candidates(
    fact: dict[str, Any],
    node: dict[str, Any],
    facts: list[dict[str, Any]],
    *,
    kind: str,
) -> list[str]:
    current_range = _fact_range(fact)
    if not current_range:
        return []
    source_id = _fact_source_id(fact)
    prior: list[tuple[int, list[str]]] = []
    for other in facts:
        if other is fact or _text(other.get("status")) != "ACCEPTED":
            continue
        if _fact_source_id(other) != source_id:
            continue
        other_range = _fact_range(other)
        if not other_range or other_range[0] >= current_range[0]:
            continue
        if other_range[0] < int(node.get("content_start_offset") or node.get("start_offset") or 0):
            continue
        subject = _dict(other.get("subject"))
        values = (
            _list(subject.get("entity_refs"))
            if kind == "object"
            else _list(subject.get("actor_refs"))
        )
        if values:
            prior.append((other_range[0], _unique(values)))
    prior.sort(key=lambda row: row[0], reverse=True)
    return _unique(value for _, values in prior[:3] for value in values)


def _unique_context_candidate(
    heading: list[str], prior: list[str]
) -> tuple[str, str, list[str]]:
    heading_values = _unique(heading)
    prior_values = _unique(prior)
    if len(heading_values) == 1 and len(prior_values) == 1:
        if heading_values[0] == prior_values[0]:
            return heading_values[0], "heading_and_same_section_prior_fact", []
        return "", "", [
            f"DOCUMENT_CONTEXT_CONFLICT:{heading_values[0]}_vs_{prior_values[0]}"
        ]
    if len(heading_values) == 1:
        return heading_values[0], "unique_heading_context", []
    if len(heading_values) > 1:
        return "", "", ["DOCUMENT_CONTEXT_HEADING_AMBIGUOUS:" + ",".join(heading_values)]
    if len(prior_values) == 1:
        return prior_values[0], "unique_prior_fact_in_same_section", []
    if len(prior_values) > 1:
        return "", "", ["DOCUMENT_CONTEXT_PRIOR_FACT_AMBIGUOUS:" + ",".join(prior_values)]
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
            ambiguities = _unique(
                value for fact in related for value in _list(fact.get("ambiguities"))
            )
            row["ambiguities"] = ambiguities
            row["extracted_fact_ids"] = _unique(fact.get("fact_id") for fact in related)
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
    statuses = Counter(_text(row.get("status")) for row in coverage)
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
            "status_distribution": dict(statuses),
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


def apply_chinese_document_context(
    asset: dict[str, Any], parsed_sources: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Resolve pending same-document references using unique hierarchical context."""
    trees = [
        build_chinese_document_semantic_tree(source)
        for source in parsed_sources
        if isinstance(source, dict)
    ]
    tree_by_source = {
        _text(tree.get("source_id")): tree for tree in trees if _text(tree.get("source_id"))
    }
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    object_names, actor_names = _known_names(asset, facts)
    resolutions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for fact in facts:
        ambiguities = _unique(_list(fact.get("ambiguities")))
        if _text(fact.get("status")) != "PENDING" or not any(
            value.startswith(_REFERENCE_AMBIGUITY_PREFIXES) for value in ambiguities
        ):
            continue
        fact_range = _fact_range(fact)
        source_id = _fact_source_id(fact)
        tree = tree_by_source.get(source_id)
        if not fact_range or not tree:
            unresolved.append(
                {
                    "fact_id": fact.get("fact_id"),
                    "reason": "DOCUMENT_CONTEXT_SOURCE_RANGE_UNAVAILABLE",
                }
            )
            continue
        node = _deepest_node(tree, fact_range[0])
        if not node:
            unresolved.append(
                {"fact_id": fact.get("fact_id"), "reason": "DOCUMENT_CONTEXT_NODE_UNAVAILABLE"}
            )
            continue

        object_value, object_method, object_errors = _unique_context_candidate(
            _heading_candidates(node, object_names),
            _prior_fact_candidates(fact, node, facts, kind="object"),
        )
        actor_value, actor_method, actor_errors = _unique_context_candidate(
            _heading_candidates(node, actor_names),
            _prior_fact_candidates(fact, node, facts, kind="actor"),
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

        subject = _dict(fact.get("subject"))
        entity_refs = _unique([*_list(subject.get("entity_refs")), object_value])
        actor_refs = _unique([*_list(subject.get("actor_refs")), actor_value])
        evidence = list(_list(subject.get("resolution_evidence")))
        if object_value:
            evidence.append(
                {
                    "mention": "中文省略/指代对象",
                    "resolved_ref": object_value,
                    "method": object_method,
                    "document_node_id": node.get("node_id"),
                    "section_path": node.get("path_titles"),
                    "heading_evidence": node.get("evidence"),
                    "confidence": 0.9 if object_method.startswith("unique_heading") else 0.84,
                }
            )
        if actor_value:
            evidence.append(
                {
                    "mention": "中文省略/指代角色",
                    "resolved_ref": actor_value,
                    "method": actor_method,
                    "document_node_id": node.get("node_id"),
                    "section_path": node.get("path_titles"),
                    "heading_evidence": node.get("evidence"),
                    "confidence": 0.9 if actor_method.startswith("unique_heading") else 0.84,
                }
            )
        fact["subject"] = {
            **subject,
            "entity_refs": entity_refs,
            "actor_refs": actor_refs,
            "resolution_evidence": evidence,
        }
        fact["object"] = {
            **_dict(fact.get("object")),
            "entity_refs": entity_refs,
        }
        remaining = [
            value
            for value in ambiguities
            if not value.startswith(_REFERENCE_AMBIGUITY_PREFIXES)
        ]
        fact["ambiguities"] = remaining
        fact["status"] = "ACCEPTED" if not remaining else "PENDING"
        fact["document_context"] = {
            "node_id": node.get("node_id"),
            "section_path": node.get("path_titles"),
            "source_backed": True,
            "cross_document_resolution_used": False,
        }
        resolutions.append(
            {
                "fact_id": fact.get("fact_id"),
                "resolved_object": object_value,
                "resolved_actor": actor_value,
                "document_node_id": node.get("node_id"),
                "section_path": node.get("path_titles"),
                "status": fact.get("status"),
            }
        )

    ledger["items"] = facts
    ledger["document_context_contract"] = {
        "same_source_only": True,
        "heading_or_same_section_prior_fact_required": True,
        "document_order_is_not_business_flow": True,
        "cross_document_proximity_forbidden": True,
    }
    asset["business_fact_ledger"] = ledger
    asset["document_semantic_trees"] = {
        "schema": TREE_SCHEMA,
        "source_count": len(trees),
        "items": trees,
    }
    asset["document_context_resolution_receipt"] = {
        "schema": CONTEXT_RECEIPT_SCHEMA,
        "resolved_fact_count": len(resolutions),
        "unresolved_fact_count": len(unresolved),
        "resolutions": resolutions,
        "unresolved": unresolved,
        "fact_authority": "original_chinese_source_span_and_heading_span",
        "cross_document_proximity_resolution_allowed": False,
    }

    # Rebuild only Chinese-derived rules so facts promoted by this stage become visible.
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
            "same_section_context_resolution_source_backed": True,
            "document_order_cannot_create_business_flow": True,
            "cross_document_proximity_cannot_resolve_references": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "TREE_SCHEMA",
    "CONTEXT_RECEIPT_SCHEMA",
    "build_chinese_document_semantic_tree",
    "apply_chinese_document_context",
]
