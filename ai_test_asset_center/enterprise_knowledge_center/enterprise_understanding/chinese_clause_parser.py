"""Atomic Chinese clause parsing — structural candidates, never final facts.

SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1 (P0-B: atomic clause splitting,
negation scope, condition tree, exception tree).

Contract:
- This is the CANDIDATE layer for Chinese clause structure. It recognizes
  LANGUAGE FUNCTION words only (modality, negation, enumeration, condition and
  exception markers — the generic vocabulary SPEC §9.1 / §9.4 lists). It never
  contains industry terms, role names, action dictionaries or benchmark
  vocabulary, and it never decides which candidate is the final action, actor
  or object — that arbitration belongs to concept resolution and grounding
  (P0-C / P0-D).
- Complex rules are split into atomic clauses WITHOUT losing shared structure:
  enumeration parts inherit the sentence modality, conditions, exceptions and
  negation scope; list children inherit their list parent's condition.
- Negation scope is explicit: modal prohibitions (不得/禁止/…) are
  ACTION_SCOPE; non-modal negations of states (未发货/无库存/尚未…) are
  CONDITION_ONLY — never a second prohibition; 非X+modal is an ACTOR_NEGATION
  shared condition. Unresolvable scope is AMBIGUOUS with NEGATION_SCOPE_AMBIGUOUS.
- Enumeration splitting is conservative: a state-modified tail
  (删除已发布内容) makes the split deterministic; without it
  (修改订单或发票) the interpretation is AMBIGUOUS and the frame keeps the
  raw text — no forced guess.
- Every node carries its source span and resolution status; the tree is
  fail-closed validated (qualibug.chinese-clause-tree.v1).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .chinese_context_envelope import envelope_from_asset
from .chinese_semantic_receipts import build_receipt
from .chinese_semantic_schema import (
    MODALITY_TYPES,
    REASON_CODES,
    _canonical_json,
    quote_hash,
)

CHINESE_CLAUSE_TREE_SCHEMA = "qualibug.chinese-clause-tree.v1"
CHINESE_CLAUSE_TREE_LEDGER_SCHEMA = "qualibug.chinese-clause-tree-ledger.v1"

# ── Language function words (SPEC §9.1 / §9.3 / §9.4) ──
_MODAL_PROHIBITED = re.compile(r"不得|严禁|禁止|不允许|不可|不能|无权|不许")
_MODAL_REQUIRED = re.compile(r"必须|应当|务必|需要|需(?=[由在对将把于])")
_MODAL_MAY = re.compile(r"可以|允许|有权")
_MODAL_ONLY_IF_DIRECT = ("只能", "仅能")
_UNTIL_RE = re.compile(r"除非(?P<cond>[^，,；;。]+?)(?:，|,|；|;|$)")

# Condition heads (generic structural markers).
_CONDITION_IF_HEAD = re.compile(r"(?:如果|若|一旦)(?P<v>[^，,；;。]+?)(?:时|则|，|,|；|;|$)")
_CONDITION_WHEN_HEAD = re.compile(
    r"当(?!前|期|日|月|年|次|笔|个|下|中)(?P<v>[^，,；;。]+?)(?:时|则|，|,|；|;|$)"
)
_CONDITION_TIME = re.compile(
    r"(?P<v>[^，,；;。]{1,60}?(?:之前|以前|之后|以后|后))(?=自动|必须|应当|则|，|,|$)"
)
_CONDITION_STATE = re.compile(
    r"(?P<v>(?:已|未|待|处于)[^，,；;。]{1,32}?)"
    r"(?=不得|不能|不可|只能|仅能|可以|允许|必须|应当|才|才能|否则|时|前|后)"
)
_CONDITION_COMBINATOR_AND = re.compile(r"并且|同时满足|以及|(?<![并])且(?![不])")
_CONDITION_COMBINATOR_OR = re.compile(r"或者|或则|任一条件|其中之一")

# Enumeration joiners inside a modal phrase.
_ENUMERATION = re.compile(r"以及|、|和|或|及|与")

# Non-modal negation (state/attribute scope). 不 excludes modal spellings that
# were already consumed (不得/不能/不可/不许).
_NEGATION_NON_MODAL = re.compile(r"未|尚未|没(?:有)?|无|不(?![能可得许])")
_ACTOR_NEGATION = re.compile(
    r"非(?P<actor>[^，。；,;]{1,8}?)"
    r"(?=不得|禁止|严禁|不允许|不可|不能|无权|可以|允许|有权|必须|应当|才)"
)

# Exception markers.
_EXCEPTION_CONTRAST = re.compile(r"(?:但|但是|不过|然而)(?P<raw>[^。！？!?；;]+)")
_EXCEPTION_EXCLUDE_SPAN = re.compile(r"除(?P<raw>[^，,；;。外]{1,24})外")
_EXCEPTION_EXCLUDE_SUFFIX = re.compile(r"(?P<raw>[\u4e00-\u9fffA-Za-z0-9_-]{1,20})除外")

# State modifiers inside a modal phrase (已发布 → object state condition).
_STATE_MODIFIER = re.compile(r"已|未|待|处于")

# Aspect particles stripped from the front of action candidates (function words).
_ACTION_PREFIX = re.compile(r"^(?:进行|发起|予以|自行|手动|自动|继续|重新|直接|立即|再次)+")

# Visible list markers stripped from LIST_ITEM text before parsing.
_LIST_MARKER = re.compile(
    r"^\s*(?:\d{1,4}[.)、）]|[（(]\d{1,4}[）)]|"
    r"[一二三四五六七八九十百千]+[、.)）]|[（(][一二三四五六七八九十百千]+[）)]|"
    r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|[-*•·▪◦‣]|[A-Za-z][.)、）]|"
    r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)、）])\s*"
)
_TRAILING_PUNCT = re.compile(r"[；;。！？!?]+$")
_COLON_END = re.compile(r"[:：]\s*$")


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


def _stable_id(kind: str, *parts: Any) -> str:
    encoded = _canonical_json([_text(part) for part in parts])
    return f"{kind}:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _strip_visible_marker(text: str) -> str:
    return _LIST_MARKER.sub("", _text(text))


def _clean_sentence(text: str) -> str:
    return _TRAILING_PUNCT.sub("", _norm(text))


def _has_clause_signal(text: str) -> bool:
    """A block is parsed only when it carries at least one structural signal."""
    value = _norm(text)
    if not value:
        return False
    return bool(
        _MODAL_PROHIBITED.search(value)
        or _MODAL_REQUIRED.search(value)
        or _MODAL_MAY.search(value)
        or any(marker in value for marker in _MODAL_ONLY_IF_DIRECT)
        or _modal_two_part(value) is not None
        or _UNTIL_RE.search(value)
        or _CONDITION_IF_HEAD.search(value)
        or _CONDITION_WHEN_HEAD.search(value)
        or _CONDITION_STATE.search(value)
        or _NEGATION_NON_MODAL.search(value)
        or _ACTOR_NEGATION.search(value)
        or _EXCEPTION_CONTRAST.search(value)
        or _EXCEPTION_EXCLUDE_SPAN.search(value)
        or _EXCEPTION_EXCLUDE_SUFFIX.search(value)
        or _ENUMERATION.search(value)
        or _COLON_END.search(value)
    )


def _modal_two_part(text: str) -> tuple[str, str, str] | None:
    """Manual 只有X才 / 仅当X才 scan → (marker, condition, subject_gap).

    The subject may sit between the condition and 才 ("只有A时，用户才能B");
    the condition is everything before the LAST separator before 才, the gap
    (subject) is between that separator and 才.
    """
    for marker in ("只有", "仅当"):
        start = _text(text).find(marker)
        if start < 0:
            continue
        end = _text(text).find("才", start)
        if end < 0:
            continue
        between = _text(text)[start + len(marker) : end]
        sep_pos = max(
            between.rfind("，"),
            between.rfind(","),
            between.rfind("；"),
            between.rfind(";"),
        )
        if sep_pos >= 0:
            condition = _norm(between[:sep_pos])
            gap = _norm(between[sep_pos + 1 :])
        else:
            condition = _norm(between)
            gap = ""
        # The 才能 compound leaves one trailing 能 in the captured span.
        condition = re.sub(r"能$", "", condition)
        gap = re.sub(r"能$", "", gap)
        if condition:
            return marker, condition, gap
    return None


def _modal_of(text: str) -> tuple[str, str, int, int]:
    """(modality, raw_marker, start, end) over the base text (0-based)."""
    two_part = _modal_two_part(text)
    if two_part:
        marker, _condition, _gap = two_part
        start = _text(text).find(marker)
        end = _text(text).find("才", start)
        return "ONLY_IF", marker, start, end + 1
    for marker in _MODAL_ONLY_IF_DIRECT:
        index = _text(text).find(marker)
        if index >= 0:
            return "ONLY_IF", marker, index, index + len(marker)
    prohibited = _MODAL_PROHIBITED.search(text)
    if prohibited:
        return "MUST_NOT", prohibited.group(0), prohibited.start(), prohibited.end()
    required = _MODAL_REQUIRED.search(text)
    if required:
        return "MUST", required.group(0), required.start(), required.end()
    may = _MODAL_MAY.search(text)
    if may:
        return "MAY", may.group(0), may.start(), may.end()
    return "ASSERTS", "", -1, -1


def _split_exceptions(
    text: str,
    *,
    modal_end: int,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Extract exception nodes and return the base text without their spans.

    A contrast marker (但/但是/不过/然而) is only split off when it appears
    AFTER the modal word; exclusions (除X外 / X除外) are removed wherever they
    appear. 除非X is an exception only when it follows the modal AND no 否则
    branch follows; otherwise it stays in the base as a negated condition.
    """
    nodes: list[dict[str, Any]] = []
    base = _text(text)
    reason_codes: list[str] = []

    contrast = _EXCEPTION_CONTRAST.search(base)
    if contrast and (modal_end < 0 or contrast.start() > modal_end):
        raw = _norm(contrast.group("raw"))
        if raw:
            nodes.append(
                {
                    "exception_id": _stable_id("exception", "contrast", base, contrast.start()),
                    "raw": raw,
                    "kind": "CONTRAST",
                    "clauses": [],
                    "resolution_status": "RESOLVED",
                }
            )
            base = base[: contrast.start()].rstrip("，,；;")
        else:
            reason_codes.append("EXCEPTION_SCOPE_UNRESOLVED")

    spans: list[tuple[int, int, dict[str, Any]]] = []
    for match in _EXCEPTION_EXCLUDE_SPAN.finditer(base):
        raw = _norm(match.group("raw"))
        if raw:
            spans.append(
                (
                    match.start(),
                    match.end(),
                    {
                        "exception_id": _stable_id("exception", "exclusion", base, match.start()),
                        "raw": raw,
                        "kind": "EXCLUSION",
                        "clauses": [],
                        "resolution_status": "RESOLVED",
                    },
                )
            )
    for match in _EXCEPTION_EXCLUDE_SUFFIX.finditer(base):
        raw = _norm(match.group("raw"))
        if raw:
            spans.append(
                (
                    match.start(),
                    match.end(),
                    {
                        "exception_id": _stable_id("exception", "exclusion", base, match.start()),
                        "raw": raw,
                        "kind": "EXCLUSION",
                        "clauses": [],
                        "resolution_status": "RESOLVED",
                    },
                )
            )
    until = _UNTIL_RE.search(base)
    if until and (modal_end < 0 or until.start() > modal_end):
        after = _norm(base[until.end() :])
        if not after.startswith("否则"):
            raw = _norm(until.group("cond"))
            if raw:
                spans.append(
                    (
                        until.start(),
                        until.end(),
                        {
                            "exception_id": _stable_id("exception", "unless", base, until.start()),
                            "raw": raw,
                            "kind": "UNLESS",
                            "clauses": [],
                            "resolution_status": "RESOLVED",
                        },
                    )
                )
            else:
                reason_codes.append("EXCEPTION_SCOPE_UNRESOLVED")
    for start, end, node in sorted(spans, key=lambda row: row[0], reverse=True):
        nodes.append(node)
        base = base[:start] + base[end:]
    return nodes, _norm(base), reason_codes


def _actor_negation_condition(text: str) -> dict[str, Any]:
    match = _ACTOR_NEGATION.search(_text(text))
    if not match:
        return {}
    return {
        "raw": "非" + _norm(match.group("actor")),
        "kind": "ACTOR_NEGATION",
        "resolution_status": "RESOLVED",
    }


def _split_condition_leaves(
    raw_condition: str,
    combinator_region: str,
) -> tuple[list[str], str]:
    """Split one condition region on explicit combinators only.

    Multiple conditions without an explicit combinator are UNRESOLVED — never
    silently AND-ed (aligned with the legacy extractor's fail-closed rule).
    """
    has_and = bool(_CONDITION_COMBINATOR_AND.search(combinator_region))
    has_or = bool(_CONDITION_COMBINATOR_OR.search(combinator_region))
    if has_and and has_or:
        return [_norm(raw_condition)], "UNRESOLVED"
    if has_and:
        leaves = [
            _norm(part)
            for part in _CONDITION_COMBINATOR_AND.split(raw_condition)
            if _norm(part)
        ]
        return leaves or [_norm(raw_condition)], "AND"
    if has_or:
        leaves = [
            _norm(part)
            for part in _CONDITION_COMBINATOR_OR.split(raw_condition)
            if _norm(part)
        ]
        return leaves or [_norm(raw_condition)], "OR"
    return [_norm(raw_condition)], "SINGLE_CONDITION"


def _extract_conditions(
    base: str,
    modal_start: int,
    two_part_condition: str,
    until_condition: str,
) -> tuple[list[str], str]:
    """Condition leaves + combinator from explicit heads / regions."""
    if two_part_condition or until_condition:
        region = _norm(two_part_condition or until_condition)
        region = _TRAILING_PUNCT.sub("", region)
        region = re.sub(r"[时后前]+$", "", region)
        if not region:
            return [], ""
        leaves, combinator = _split_condition_leaves(region, region)
        return leaves, combinator

    candidates: list[str] = []
    for pattern in (_CONDITION_IF_HEAD, _CONDITION_WHEN_HEAD, _CONDITION_TIME):
        for match in pattern.finditer(base):
            raw = _norm(match.group("v"))
            if raw and raw not in candidates:
                candidates.append(raw)
    if not candidates:
        for match in _CONDITION_STATE.finditer(base):
            if modal_start >= 0 and match.start() >= modal_start:
                continue  # state modifier after the modal belongs to the action region
            raw = _norm(match.group("v"))
            if raw and raw not in candidates:
                candidates.append(raw)
    if not candidates:
        return [], ""
    leaves: list[str] = []
    for raw in candidates:
        parts, _combinator = _split_condition_leaves(raw, base)
        for part in parts:
            if part and part not in leaves:
                leaves.append(part)
    if len(leaves) == 1:
        return leaves, "SINGLE_CONDITION"
    if _CONDITION_COMBINATOR_AND.search(base) and not _CONDITION_COMBINATOR_OR.search(base):
        return leaves, "AND"
    if _CONDITION_COMBINATOR_OR.search(base) and not _CONDITION_COMBINATOR_AND.search(base):
        return leaves, "OR"
    return leaves, "UNRESOLVED"


def _negation_scope(
    condition_leaves: list[str],
    action_region: str,
    modality: str,
    actor_condition: dict[str, Any],
    raw_marker: str,
) -> dict[str, Any]:
    """Classify the sentence negation scope (never a second prohibition)."""
    if actor_condition:
        return {
            "type": "ACTOR_NEGATION",
            "raw": _norm(actor_condition.get("raw")),
            "resolution_status": "RESOLVED",
        }
    condition_hits = [
        _norm(match.group(0))
        for leaf in condition_leaves
        for match in _NEGATION_NON_MODAL.finditer(leaf)
    ]
    if condition_hits:
        return {
            "type": "CONDITION_ONLY",
            "raws": list(dict.fromkeys(condition_hits)),
            "resolution_status": "RESOLVED",
        }
    if modality == "MUST_NOT":
        return {
            "type": "ACTION_SCOPE",
            "raw": _norm(raw_marker),
            "resolution_status": "RESOLVED",
        }
    stray = list(_NEGATION_NON_MODAL.finditer(action_region))
    if stray:
        return {
            "type": "AMBIGUOUS",
            "raws": list(dict.fromkeys(_norm(m.group(0)) for m in stray)),
            "resolution_status": "UNKNOWN",
        }
    return {"type": "NONE", "raws": [], "resolution_status": "NOT_MENTIONED"}


def _action_clauses(
    action_region: str,
    modality: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """Split the modal phrase into atomic action candidates.

    Deterministic when a state-modified tail exists; otherwise the
    enumeration interpretation is AMBIGUOUS (object lists must never be
    forced into action splits).
    """
    region = _clean_sentence(action_region)
    region = region.lstrip("，,；;、")
    region = _ACTION_PREFIX.sub("", region)
    region = re.sub(r"[，,、\s]+$", "", region)
    region = region.strip()
    if not region:
        return [], {"joiner": "", "part_count": 0, "interpretation": "EMPTY"}, False

    parts = [
        _norm(part)
        for part in _ENUMERATION.split(region)
        if _norm(part)
    ]
    clauses: list[dict[str, Any]] = []
    object_conditions: list[str] = []
    state_tail_found = False
    for part in parts:
        state = _STATE_MODIFIER.search(part)
        if state:
            state_tail_found = True
            before = _norm(part[: state.start()])
            after = _norm(part[state.start() :])
            clauses.append(
                {
                    "clause_id": _stable_id("clause", region, part),
                    "action_mention": before,
                    "modality": modality,
                    "object_condition": {
                        "raw": after,
                        "kind": "OBJECT_STATE_MODIFIER",
                        "resolution_status": "RESOLVED",
                    },
                    "resolution_status": "RESOLVED",
                }
            )
            if after:
                object_conditions.append(after)
        else:
            clauses.append(
                {
                    "clause_id": _stable_id("clause", region, part),
                    "action_mention": part,
                    "modality": modality,
                    "object_condition": {},
                    "resolution_status": "RESOLVED",
                }
            )

    if len(parts) > 1:
        interpretation = "ACTION_SPLIT" if state_tail_found else "AMBIGUOUS"
        ambiguous = not state_tail_found
    else:
        interpretation = "SINGLE"
        ambiguous = False

    joiner = ""
    enumeration_match = _ENUMERATION.search(region)
    if enumeration_match:
        joiner = enumeration_match.group(0)
    return (
        clauses,
        {
            "joiner": joiner,
            "part_count": len(parts),
            "interpretation": interpretation,
        },
        ambiguous,
    )


def parse_block_text(
    text: str,
    *,
    source_id: str = "",
    block_id: str = "",
    block_type: str = "PARAGRAPH",
    locator: str = "",
) -> dict[str, Any]:
    """Parse one body block into a validated clause tree."""
    original = _norm(text)
    raw_text = _text(text)
    if block_type == "LIST_ITEM":
        raw_text = _strip_visible_marker(raw_text)
    sentence = _clean_sentence(raw_text)
    reason_codes: list[str] = []

    # Modality + spans over the sentence.
    modality, raw_marker, modal_start, modal_end = _modal_of(sentence)

    # Exception nodes (contrast after modal; exclusions; post-modal 除非).
    exception_nodes, base, exception_codes = _split_exceptions(
        sentence, modal_end=modal_end
    )
    reason_codes.extend(exception_codes)

    # Re-locate the modal on the exception-stripped base.
    modality, raw_marker, modal_start, modal_end = _modal_of(base)

    # Two-part condition region (只有X才 / 仅当X才) and 除非X condition.
    two_part_condition = ""
    until_condition = ""
    two_part = _modal_two_part(base)
    if two_part:
        two_part_condition = _norm(two_part[1])
    elif modality != "ONLY_IF":
        until = _UNTIL_RE.search(base)
        if until:
            until_condition = _norm(until.group("cond"))
            base = _norm(base[: until.start()] + base[until.end() :])
            modality, raw_marker, modal_start, modal_end = _modal_of(base)

    action_region = _norm(base[modal_end:]) if modal_end >= 0 else _norm(base)

    # 才-subject: the nominal between the ONLY_IF condition and 才.
    only_if_subject = ""
    if two_part:
        only_if_subject = _norm(two_part[2])

    if modality == "ONLY_IF" and raw_marker in ("只有", "仅当"):
        action_region = re.sub(r"^(?:能|可以|可|才|方能)", "", action_region)

    conditions, combinator = _extract_conditions(
        base, modal_start, two_part_condition, until_condition
    )

    # List header condition: a LIST_ITEM ending with ：/： IS the condition of
    # its children ("已取消订单：") — parsed as a condition, never an action.
    list_header = ""
    if block_type == "LIST_ITEM":
        colon = _COLON_END.search(sentence)
        if colon:
            list_header = _norm(sentence[: colon.start()])

    clauses: list[dict[str, Any]] = []
    enumeration: dict[str, Any] = {"joiner": "", "part_count": 0, "interpretation": "EMPTY"}
    enumeration_ambiguous = False
    if not list_header:
        clauses, enumeration, enumeration_ambiguous = _action_clauses(
            action_region, modality
        )
    else:
        if list_header not in conditions:
            conditions = [list_header] + conditions
            combinator = (
                "SINGLE_CONDITION" if len(conditions) == 1 else (combinator or "UNRESOLVED")
            )
        enumeration["interpretation"] = "HEADER"

    actor_condition = _actor_negation_condition(sentence)
    negation_scope = _negation_scope(
        conditions, action_region, modality, actor_condition, raw_marker
    )
    if until_condition and negation_scope.get("type") != "CONDITION_ONLY":
        # 除非X means "X must NOT hold" — a negated condition, not a prohibition.
        negation_scope = {
            "type": "CONDITION_ONLY",
            "raws": ["除非"],
            "resolution_status": "RESOLVED",
        }

    shared: dict[str, Any] = {}
    if actor_condition:
        shared["actor_condition"] = actor_condition
    object_conditions = [
        _norm(_dict(row.get("object_condition")).get("raw"))
        for row in clauses
        if _norm(_dict(row.get("object_condition")).get("raw"))
    ]
    if len(dict.fromkeys(object_conditions)) == 1 and object_conditions:
        shared["object_condition"] = {
            "raw": object_conditions[0],
            "kind": "OBJECT_STATE_MODIFIER",
            "resolution_status": "RESOLVED",
        }

    if enumeration_ambiguous:
        reason_codes.append("CLAUSE_SEGMENTATION_AMBIGUOUS")
    if negation_scope.get("type") == "AMBIGUOUS":
        reason_codes.append("NEGATION_SCOPE_AMBIGUOUS")
    if combinator == "UNRESOLVED":
        reason_codes.append("CONDITION_SCOPE_AMBIGUOUS")

    quote = original or sentence
    tree = {
        "schema": CHINESE_CLAUSE_TREE_SCHEMA,
        "tree_id": _stable_id("clause_tree", source_id, block_id, quote),
        "source_id": _text(source_id),
        "block_id": _text(block_id),
        "block_type": _text(block_type),
        "source_span": {
            "source_id": _text(source_id),
            "block_id": _text(block_id),
            "locator": _text(locator),
            "quote": quote,
            "quote_hash": quote_hash(quote),
        },
        "modality": {
            "type": modality,
            "raw_marker": _text(raw_marker),
            "resolution_status": "RESOLVED" if raw_marker else "NOT_MENTIONED",
        },
        "negation_scope": negation_scope,
        "actor_mention": {"raw": only_if_subject, "origin": "ONLY_IF_SUBJECT"}
        if only_if_subject
        else {},
        "conditions": [
            {
                "condition_id": f"condition:{index}",
                "raw": raw,
                "logic_group": combinator or "main",
                "resolution_status": "RESOLVED",
            }
            for index, raw in enumerate(dict.fromkeys(conditions), start=1)
        ],
        "condition_combinator": combinator,
        "exceptions": exception_nodes,
        "clauses": clauses,
        "enumeration": enumeration,
        "shared_conditions": shared,
        "reason_codes": sorted(set(reason_codes)),
        "resolution": {
            "status": "RESOLVED" if not reason_codes else "PARTIALLY_RESOLVED",
            "reason_codes": sorted(set(reason_codes)),
        },
    }
    errors = validate_clause_tree(tree)
    if errors:
        raise ValueError("chinese_clause_tree_invalid:" + ",".join(sorted(errors)))
    return tree


def validate_clause_tree(tree: dict[str, Any]) -> list[str]:
    """Fail-closed structural validation of a clause tree."""
    errors: list[str] = []
    if not isinstance(tree, dict):
        return ["clause_tree_not_object"]
    if _text(tree.get("schema")) != CHINESE_CLAUSE_TREE_SCHEMA:
        errors.append("clause_tree_schema_mismatch")
    if not _text(tree.get("tree_id")):
        errors.append("clause_tree_id_missing")
    if not _text(_dict(tree.get("source_span")).get("quote")):
        errors.append("clause_tree_quote_missing")
    modality = _dict(tree.get("modality"))
    if _text(modality.get("type")) not in MODALITY_TYPES:
        errors.append(f"clause_tree_modality_invalid:{_text(modality.get('type'))}")
    for index, row in enumerate(_list(tree.get("conditions"))):
        if not isinstance(row, dict) or not _text(row.get("condition_id")):
            errors.append(f"clause_tree_condition_invalid:{index}")
    for index, row in enumerate(_list(tree.get("exceptions"))):
        if not isinstance(row, dict) or not _text(row.get("exception_id")):
            errors.append(f"clause_tree_exception_invalid:{index}")
    for index, row in enumerate(_list(tree.get("clauses"))):
        if not isinstance(row, dict) or not _text(row.get("clause_id")):
            errors.append(f"clause_tree_clause_invalid:{index}")
    for code in _list(tree.get("reason_codes")):
        if _text(code) not in REASON_CODES:
            errors.append(f"clause_tree_reason_code_invalid:{_text(code)}")
    return errors


def _parsable_blocks(asset: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(envelope_entry, envelope_source) for signal-bearing body blocks."""
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for source in _list(envelope_from_asset(asset).get("sources")):
        for block in _dict(source.get("blocks")).values():
            if not isinstance(block, dict):
                continue
            if _text(block.get("block_type")) not in ("PARAGRAPH", "LIST_ITEM", "TABLE_CELL"):
                continue
            if _has_clause_signal(_text(block.get("text"))):
                rows.append((block, source))
    return rows


def parse_chinese_clause_trees(asset: dict[str, Any]) -> dict[str, Any]:
    """Parse all signal-bearing body blocks into the clause tree ledger."""
    items: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    failures: list[str] = []
    reason_counts: dict[str, int] = {}
    scanned = 0
    for block, source in _parsable_blocks(asset):
        scanned += 1
        try:
            tree = parse_block_text(
                _text(block.get("text")),
                source_id=_text(source.get("source_id")),
                block_id=_text(block.get("block_id")),
                block_type=_text(block.get("block_type")),
                locator=_text(block.get("locator")),
            )
        except ValueError as exc:
            message = str(exc)
            failures.append(message)
            reason_counts["CLAUSE_PARSE_FAILURE"] = reason_counts.get(
                "CLAUSE_PARSE_FAILURE", 0
            ) + 1
            receipts.append(
                build_receipt(
                    receipt_kind="FRAME_VALIDATION",
                    frame_id=_text(block.get("block_id")),
                    status="FAIL",
                    reason_codes=["CLAUSE_SEGMENTATION_AMBIGUOUS"],
                    payload={"error": message},
                )
            )
            continue
        items.append(tree)
        for code in tree["reason_codes"]:
            reason_counts[code] = reason_counts.get(code, 0) + 1
        receipts.append(
            build_receipt(
                receipt_kind="FRAME_VALIDATION",
                frame_id=tree["tree_id"],
                status="PASS",
                reason_codes=tree["reason_codes"],
                payload={
                    "block_id": tree["block_id"],
                    "modality": tree["modality"]["type"],
                    "clause_count": len(tree["clauses"]),
                },
            )
        )

    asset["chinese_clause_tree_ledger"] = {
        "schema": CHINESE_CLAUSE_TREE_LEDGER_SCHEMA,
        "items": items,
        "receipts": receipts,
        "closure": {
            "status": "FAIL" if failures else "PASS",
            "scanned_block_count": scanned,
            "tree_count": len(items),
            "failed_count": len(failures),
            "reason_code_counts": dict(sorted(reason_counts.items())),
            "silent_drop_allowed": False,
            "industry_vocabulary_added": False,
        },
    }
    return asset


def clause_tree_for_block(asset: dict[str, Any], block_id: str) -> dict[str, Any]:
    """Return the clause tree attached to one block (empty dict when absent)."""
    for row in _list(_dict(asset.get("chinese_clause_tree_ledger")).get("items")):
        if isinstance(row, dict) and _text(row.get("block_id")) == _text(block_id):
            return dict(row)
    return {}
