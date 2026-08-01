"""Derive conservative object-type surfaces from existing source structures."""
from __future__ import annotations

import re
from typing import Any

from ._object_role_evidence import comparison_key
from ._object_source_declarations import (
    source_name_tokens,
    source_singular_forms,
)
from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    text,
    unique_text,
)

_CJK_SURFACE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_.-]{2,40}$")
_SURFACE_SPLIT = re.compile(r"\s*(?:、|，|,|；|;|和|及|与)\s*")
_TRAILING_PRESENTATION = re.compile(r"(?:详情|列表|信息|数据)$")
_PARENTHETICAL = re.compile(r"[（(].*$")
_PATH_PARAMETER = re.compile(r"^[:{].*[}]?$")


def _normalized_sentence(value: Any) -> str:
    return text(value).strip().rstrip("。.!！?？")


def _statement_surface_phrases(
    statement: Any, action_tokens: list[str]
) -> list[str]:
    raw = _normalized_sentence(statement)
    positions = [
        (raw.find(token), token)
        for token in action_tokens
        if token and raw.find(token) >= 0
    ]
    if not raw or not positions:
        return []
    position, token = min(positions, key=lambda row: row[0])
    tail = raw[position + len(token) :]
    tail = re.split(r"[。.!！?？；;]", tail, maxsplit=1)[0]
    tail = _PARENTHETICAL.sub("", tail).strip(" /／:-：")
    values: list[str] = []
    for part in _SURFACE_SPLIT.split(tail):
        candidate = part.strip()
        candidate = re.sub(
            r"^(?:当前用户|指定用户|目标用户|自己的|自己负责的|相关的)",
            "",
            candidate,
        ).strip()
        candidate = _TRAILING_PRESENTATION.sub("", candidate).strip()
        if _CJK_SURFACE.fullmatch(candidate):
            values.append(candidate)
    return unique_text(values)


def _fact_object_refs(fact: dict[str, Any]) -> list[str]:
    return unique_text(
        value
        for claim in as_list(fact.get("claims"))
        if isinstance(claim, dict)
        and text(claim.get("claim_type")) in {"PRIMARY_OPERATION", "DATA_EFFECT"}
        for value in as_list(claim.get("object_refs"))
        if text(value)
    )


def _path_tokens(path: Any) -> list[str]:
    return [
        value.casefold()
        for value in text(path).split("/")
        if value and value.casefold() != "api" and not _PATH_PARAMETER.match(value)
    ]


def _subsequence_match_positions(
    path: list[str], table_tokens: list[str]
) -> list[int]:
    positions: list[int] = []
    cursor = 0
    for index, token in enumerate(path):
        if cursor >= len(table_tokens):
            break
        if token == table_tokens[cursor] or source_singular_forms(token) & source_singular_forms(
            table_tokens[cursor]
        ):
            positions.append(index)
            cursor += 1
    return positions if cursor == len(table_tokens) else []


def _interface_parent_keys(
    interface: dict[str, Any], declarations: list[dict[str, Any]]
) -> list[str]:
    path = _path_tokens(interface.get("path"))
    if not path:
        return []
    scored: list[tuple[int, str]] = []
    for declaration in declarations:
        key = comparison_key(declaration.get("canonical_label"))
        if not key:
            continue
        table_tokens = source_name_tokens(declaration.get("technical_table_name"))
        positions = _subsequence_match_positions(path, table_tokens)
        if positions:
            scored.append((100 + len(positions) * 10 + positions[-1], key))
            continue
        canonical_tokens = source_name_tokens(declaration.get("canonical_label"))
        if canonical_tokens and source_singular_forms(path[0]) & source_singular_forms(canonical_tokens[0]):
            scored.append((10, key))
    if not scored:
        return []
    best = max(score for score, _key in scored)
    return sorted({key for score, key in scored if score == best})


def _edge_overlap(left: Any, right: Any) -> int:
    a, b = text(left), text(right)
    limit = min(len(a), len(b))
    best = 0
    for size in range(2, limit + 1):
        if a[:size] == b[:size] or a[-size:] == b[-size:]:
            best = size
    return best


def _parent_scores(
    label: Any, declarations: list[dict[str, Any]]
) -> list[tuple[int, str, int]]:
    rows: list[tuple[int, str, int]] = []
    for declaration in declarations:
        key = comparison_key(declaration.get("canonical_label"))
        labels = [text(value) for value in as_list(declaration.get("labels")) if text(value)]
        scores = [(_edge_overlap(label, value), len(value)) for value in labels]
        score, parent_length = max(scores or [(0, 0)])
        if key and score >= 2:
            rows.append((score, key, parent_length))
    return rows


def _lexical_parent_keys(
    label: Any, declarations: list[dict[str, Any]]
) -> list[str]:
    scored = _parent_scores(label, declarations)
    if not scored:
        return []
    best = max(score for score, _key, _length in scored)
    parents = sorted({key for score, key, _length in scored if score == best})
    return parents if len(parents) == 1 else []


def _parent_label_length(
    label: Any, parent_keys: list[str], declarations: list[dict[str, Any]]
) -> int:
    lengths = [
        parent_length
        for score, key, parent_length in _parent_scores(label, declarations)
        if key in parent_keys and score >= 2
    ]
    return max(lengths or [0])


def _overlapping_parent_keys(
    label: Any, parent_keys: list[str], declarations: list[dict[str, Any]]
) -> list[str]:
    return sorted(
        {
            key
            for score, key, _length in _parent_scores(label, declarations)
            if key in parent_keys and score >= 2
        }
    )


def _permission_source_ids(asset: dict[str, Any]) -> set[str]:
    return {
        text(row.get("source_id"))
        for row in as_list(asset.get("source_inventory"))
        if isinstance(row, dict)
        and text(row.get("source_id"))
        and (
            int(as_dict(row.get("parse")).get("permission_count") or 0) > 0
            or text(row.get("source_type")).casefold()
            in {"roles", "role", "permissions"}
        )
    }


def _source_types(asset: dict[str, Any]) -> dict[str, str]:
    return {
        text(row.get("source_id")): text(row.get("source_type")).casefold()
        for row in as_list(asset.get("source_inventory"))
        if isinstance(row, dict) and text(row.get("source_id"))
    }


def _action_tokens(facts: list[dict[str, Any]]) -> list[str]:
    return sorted(
        unique_text(
            value
            for fact in facts
            for value in (
                as_dict(fact.get("action")).get("raw"),
                as_dict(fact.get("action")).get("canonical"),
            )
            if text(value)
        ),
        key=lambda value: (-len(value), value),
    )


def _has_embedded_other_action(label: str, actions: list[str]) -> bool:
    return any(action != label and action in label for action in actions)


def _surface_evidence_from_interface(interface: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = text(interface.get("source_id"))
    locator = text(interface.get("source_locator") or interface.get("interface_id"))
    quote = text(interface.get("summary"))
    if not source_id or not locator or not quote:
        return []
    return [
        {
            "source_id": source_id,
            "source_locator": locator,
            "quote": quote,
            "asset_ref": text(interface.get("interface_id")),
            "derivation": "SOURCE_INTERFACE_OPERATION_OBJECT_SURFACE",
        }
    ]


def _shared_declared_suffixes(
    label: str, parent_keys: list[str], declarations: list[dict[str, Any]]
) -> list[str]:
    if not label or not re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", label):
        return []
    label_suffixes = {label[index:] for index in range(1, len(label) - 1)}
    parent_suffixes: set[str] = set()
    for declaration in declarations:
        key = comparison_key(declaration.get("canonical_label"))
        if key not in parent_keys:
            continue
        for parent_label in as_list(declaration.get("labels")):
            value = text(parent_label)
            if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", value):
                parent_suffixes.update(
                    value[index:] for index in range(1, len(value) - 1)
                )
    return sorted(label_suffixes & parent_suffixes, key=lambda value: (-len(value), value))


def collect_source_attested_object_surfaces(
    asset: dict[str, Any], declarations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    facts = [
        row
        for row in as_list(as_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict)
        and not text(row.get("parent_fact_ref"))
        and text(row.get("status")) in {"", "ACCEPTED"}
    ]
    actions = _action_tokens(facts)
    source_types = _source_types(asset)
    permission_sources = _permission_source_ids(asset)
    declared_keys = {
        comparison_key(value)
        for declaration in declarations
        for value in as_list(declaration.get("labels"))
        if comparison_key(value)
    }

    def add(label: str, parents: list[str], evidence: list[dict[str, Any]]) -> None:
        key = comparison_key(label)
        if not key or key in declared_keys or not parents or not evidence:
            return
        row = rows.setdefault(
            key,
            {"label": label, "parents": set(), "evidence": []},
        )
        row["parents"].update(parents)
        row["evidence"].extend(evidence)

    fact_refs_by_statement: dict[str, set[str]] = {}
    for fact in facts:
        statement = _normalized_sentence(
            fact.get("raw_statement") or fact.get("normalized_statement")
        )
        if statement:
            fact_refs_by_statement.setdefault(statement, set()).update(
                _fact_object_refs(fact)
            )

    for interface in as_list(asset.get("interfaces")):
        if not isinstance(interface, dict):
            continue
        summary = _normalized_sentence(interface.get("summary"))
        parents = _interface_parent_keys(interface, declarations)
        if not summary or not parents:
            continue
        evidence = _surface_evidence_from_interface(interface)
        direct_refs = fact_refs_by_statement.get(summary, set())
        method = text(interface.get("method")).upper()
        for label in _statement_surface_phrases(summary, actions):
            chosen = _overlapping_parent_keys(label, parents, declarations)
            if not chosen:
                continue
            parent_length = _parent_label_length(label, chosen, declarations)
            longer_than_parent = len(label) > parent_length > 0
            if longer_than_parent and method != "GET" and label not in direct_refs:
                continue
            add(label, chosen, evidence)

    for fact in facts:
        evidence = evidence_from_fact(fact)
        if not evidence:
            continue
        source_id = text(evidence[0].get("source_id"))
        source_type = source_types.get(source_id, "")
        if (
            source_id not in permission_sources
            and source_type not in {"roles", "role", "permissions", "config"}
        ):
            continue
        action = as_dict(fact.get("action"))
        fact_actions = unique_text([action.get("raw"), action.get("canonical")])
        for label in _statement_surface_phrases(
            fact.get("raw_statement") or fact.get("normalized_statement"),
            fact_actions,
        ):
            if _has_embedded_other_action(label, actions):
                continue
            parents = _lexical_parent_keys(label, declarations)
            parent_length = _parent_label_length(label, parents, declarations)
            if parents and len(label) == parent_length:
                add(label, parents, evidence)

    for _key, row in list(rows.items()):
        parents = sorted(row["parents"])
        evidence = dedupe_evidence(row["evidence"])
        quotes = [text(item.get("quote")) for item in evidence if isinstance(item, dict)]
        for suffix in _shared_declared_suffixes(row["label"], parents, declarations):
            if not any(suffix in quote for quote in quotes):
                continue
            suffix_key = comparison_key(suffix)
            nested = rows.setdefault(
                suffix_key,
                {"label": suffix, "parents": set(), "evidence": []},
            )
            nested["parents"].update(parents)
            nested["evidence"].extend(evidence)

    return [
        {
            "label": row["label"],
            "parents": sorted(row["parents"]),
            "evidence": dedupe_evidence(row["evidence"]),
        }
        for _key, row in sorted(rows.items())
    ]


__all__ = ["collect_source_attested_object_surfaces"]
