"""Chinese-first enterprise business comprehension for the knowledge-center mainline.

The product targets Chinese enterprise materials. This module keeps Chinese source
text as the formal fact authority, builds a source-span coverage ledger, extracts
structured business facts without translating through English, and emits a
fail-closed comprehension gate before downstream consumers treat the asset as
business-complete.

The implementation is additive: it enriches the existing enterprise knowledge
asset and reuses its source registry, parser receipts, rule library and persistence
paths instead of creating a parallel product path.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from functools import wraps
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "qualibug.chinese-first-business-comprehension.v1"
COVERAGE_SCHEMA = "qualibug.document-coverage-ledger.v1"
FACT_SCHEMA = "qualibug.business-fact-ledger.v1"
GATE_SCHEMA = "qualibug.enterprise-comprehension-gate.v1"

_CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HEADING_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千]+[章节部分]|"
    r"[一二三四五六七八九十]+[、.]|\d+(?:\.\d+)*[、.)．]?)\s*(?P<title>.+?)\s*$"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])")
_RULE_SIGNAL_RE = re.compile(
    r"必须|应当|需要|需|不得|严禁|禁止|不允许|不可|不能|只能|仅能|"
    r"可以|允许|有权|无权|只有.+?才|除非|否则|"
    r"当.+?时|如果|若|一旦|之前|之后|超过|至少|至多|"
    r"审批|审核|提交|撤回|驳回|退回|作废|取消|发货|付款|退款|"
    r"状态|流转|进入|变为|转为|生成|创建|修改|删除|查看"
)
_CRITICAL_SIGNAL_RE = re.compile(
    r"不得|严禁|禁止|不允许|不可|不能|无权|只能|仅能|除外|除非|"
    r"但|但是|例外|否则|审批|权限|本人|本部门|本组织|本区域|本仓库"
)
_NEGATIVE_RE = re.compile(r"不得|严禁|禁止|不允许|不可|不能|无权|拒绝|禁止再")
_MUST_RE = re.compile(r"必须|应当|务必|需要|需(?=[由在对将把于])")
_ONLY_IF_RE = re.compile(r"只有.+?才|仅当.+?才|除非|只能|仅能")
_MAY_RE = re.compile(r"可以|允许|有权|可(?=[由在对将把于进行查看修改删除提交撤回])")
_EXCEPTION_RE = re.compile(r"(?:但|但是|不过|然而|除[^，,；;。]+外|除非)(?P<value>.+)")
_TEMPORAL_RE = re.compile(
    r"(?P<value>(?:在)?[^，,；;。]{0,48}?"
    r"(?:之前|之后|前|后|时|期间|以内|之内|超过\d+[^，,；;。]*|至少\d+[^，,；;。]*))"
)
_CONDITION_PATTERNS = (
    re.compile(r"(?:当|如果|若|一旦)(?P<value>.+?)(?:时|则|，|,|；|;|$)"),
    re.compile(r"只有(?P<value>.+?)才"),
    re.compile(r"仅当(?P<value>.+?)才"),
    re.compile(r"除非(?P<value>.+?)(?:，|,|；|;|$)"),
    re.compile(r"(?P<value>[^，,；;。]{1,60}(?:之前|之后|前|后))(?:，|,|；|;|$)"),
)
# Explicit source combinators only. Multiple conditions without one stay UNRESOLVED —
# never default to AND.
_AND_COMBINATOR_RE = re.compile(r"并且|同时满足|以及|(?<![并])且(?![不])")
_OR_COMBINATOR_RE = re.compile(r"或者|或则|任一条件|其中之一")
_CRITICAL_AMBIGUITY_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "CRITICAL_ACTION_",
    "EXCEPTION_SCOPE_",
    "CONDITION_COMBINATOR_",
)
_COREFERENCE_RE = re.compile(
    r"该(?:对象|记录|数据|单据|申请|订单|工单|合同|任务)?|"
    r"本(?:单|记录|对象|申请|订单|工单|合同)?|"
    r"此(?:时|对象|记录|操作|流程)?|其|上述|前述|对应|相关|当前"
)
_OWNERSHIP_SCOPE_RE = re.compile(r"本人|自己|其本人|原申请人|原发起人|本人创建|本人提交|本人负责")
_ORG_SCOPE_RE = re.compile(r"本部门|本组织|本区域|本仓库|当前租户|本租户|所属组织|所属部门|所属区域")
_ROLE_SUFFIX_RE = re.compile(
    r"(?P<role>[\u4e00-\u9fffA-Za-z0-9_-]{1,20}"
    r"(?:管理员|操作员|审批人|审核人|申请人|发起人|负责人|经办人|"
    r"经理|主管|用户|人员|员工|财务|会计|出纳|客服|仓管员|仓库员))"
)
_ENTITY_SUFFIX_RE = re.compile(
    r"(?P<entity>[\u4e00-\u9fffA-Za-z0-9_-]{1,24}"
    r"(?:申请单|申请|订单|工单|合同|出库单|入库单|任务单|任务|"
    r"记录|单据|凭证|发票|库存|商品|物料|批次|设备|计划|流程|数据))"
)
_ALIAS_PATTERNS = (
    re.compile(r"(?P<canonical>[\u4e00-\u9fffA-Za-z0-9_-]{2,40})[（(](?P<alias>[^()（）]{1,30})[）)]"),
    re.compile(r"(?P<canonical>[\u4e00-\u9fffA-Za-z0-9_-]{2,40})(?:以下简称|简称为|简称)(?P<alias>[\u4e00-\u9fffA-Za-z0-9_-]{1,30})"),
)

_ACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("重新编辑", r"重新编辑|再次编辑"),
    ("修改", r"修改|编辑|变更|调整"),
    ("删除", r"删除|移除"),
    ("创建", r"创建|新建|新增|生成"),
    ("提交", r"重新提交|提交|发起"),
    ("撤回", r"撤回|撤销"),
    ("审批通过", r"审批通过|审核通过|通过审批|通过审核"),
    ("审批退回", r"审批退回|审核退回|退回"),
    ("驳回", r"驳回|拒绝"),
    ("作废", r"作废|废弃"),
    ("取消", r"取消|终止"),
    ("查看", r"查看|查询|读取|浏览"),
    ("发货", r"发货|出库|配送"),
    ("收货", r"收货|签收|确认收货"),
    ("付款", r"付款|支付"),
    ("退款", r"退款|退费"),
    ("核销", r"核销|冲销|红冲"),
    ("审核", r"审批|审核"),
    ("保存", r"保存"),
    ("关闭", r"关闭"),
)
_ACTION_REGEXES = tuple((name, re.compile(pattern)) for name, pattern in _ACTION_PATTERNS)
_STATE_TRANSITION_RE = re.compile(
    r"(?:从(?P<from>[^，,；;。]{1,24}?)(?:状态)?"
    r"(?:流转|迁移|转|变更|变为|进入)到(?P<to>[^，,；;。]{1,24}))|"
    r"(?:(?:状态)?(?:变为|转为|置为|进入)(?P<to_only>[^，,；;。]{1,24}))"
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


def _normalize_chinese_text(text: str) -> str:
    """Normalize layout only; never translate or rewrite business terminology."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u3000", " ").replace("\xa0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _language_of(text: str) -> str:
    chinese = len(_CHINESE_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese and latin:
        return "zh-CN-mixed" if chinese >= max(4, latin // 4) else "mixed"
    if chinese:
        return "zh-CN"
    return "other"


def _known_names(asset: dict[str, Any], key: str) -> list[str]:
    values: list[str] = []
    if key == "entity":
        for row in _list(asset.get("business_objects")):
            name = _text(_dict(row).get("object") or _dict(row).get("name"))
            if name:
                values.append(name)
        for row in _list(asset.get("data_tables")):
            name = _text(_dict(row).get("name"))
            if name:
                values.append(name)
    elif key == "role":
        for row in _list(asset.get("roles")):
            name = _text(_dict(row).get("role") or _dict(row).get("name"))
            if name:
                values.append(name)
        for row in _list(asset.get("permission_matrix")):
            name = _text(_dict(row).get("role") or _dict(row).get("actor"))
            if name:
                values.append(name)
    return sorted(set(values), key=lambda item: (-len(item), item))


def _paragraph_chunks(text: str, max_chars: int = 900) -> list[dict[str, Any]]:
    normalized = _normalize_chinese_text(text)
    if not normalized:
        return []
    chunks: list[dict[str, Any]] = []
    cursor = 0
    section = "document"
    for paragraph in re.split(r"\n+", normalized):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        start = normalized.find(paragraph, cursor)
        if start < 0:
            start = cursor
        end = start + len(paragraph)
        cursor = end
        heading = _HEADING_RE.match(paragraph)
        if heading and len(paragraph) <= 80:
            section = _text(heading.group("title")) or paragraph
            chunks.append({"text": paragraph, "start": start, "end": end, "section": section, "kind": "heading"})
            continue
        parts = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(paragraph) if part.strip()]
        current = ""
        current_start = start
        search_from = start
        for part in parts or [paragraph]:
            part_start = normalized.find(part, search_from)
            if part_start < 0:
                part_start = search_from
            if current and len(current) + len(part) > max_chars:
                chunks.append({"text": current, "start": current_start, "end": current_start + len(current), "section": section, "kind": "content"})
                current = part
                current_start = part_start
            else:
                if not current:
                    current_start = part_start
                current += part
            search_from = part_start + len(part)
        if current:
            chunks.append({"text": current, "start": current_start, "end": current_start + len(current), "section": section, "kind": "content"})
    return chunks


def _extract_aliases(text: str, source_id: str, locator: str) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in _ALIAS_PATTERNS:
        for match in pattern.finditer(text):
            canonical = _text(match.group("canonical"))
            alias = _text(match.group("alias"))
            if not canonical or not alias or canonical == alias or (canonical, alias) in seen:
                continue
            seen.add((canonical, alias))
            quote = match.group(0)
            aliases.append({
                "fact_id": _stable_id("fact", source_id, locator, "TERM_ALIAS", canonical, alias),
                "kind": "TERM_ALIAS",
                "language": _language_of(quote),
                "canonical_term": canonical,
                "alias": alias,
                "raw_statement": quote,
                "source_spans": [{"source_id": source_id, "locator": locator, "quote": quote, "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest()}],
                "confidence": 0.98,
                "status": "ACCEPTED",
                "ambiguities": [],
            })
    return aliases


def _find_mentions(text: str, known: Iterable[str], fallback: re.Pattern[str]) -> list[str]:
    known_values = [name for name in known if name]
    mentions = [name for name in known_values if name in text]
    if not mentions:
        for width in (4, 3, 2):
            suffix_matches: dict[str, list[str]] = {}
            for name in known_values:
                if len(name) >= width:
                    suffix_matches.setdefault(name[-width:], []).append(name)
            for suffix, candidates in suffix_matches.items():
                if suffix in text and len(candidates) == 1:
                    mentions.append(candidates[0])
    if mentions:
        return sorted(set(mentions), key=lambda item: (text.find(item), -len(item), item))
    for match in fallback.finditer(text):
        value = _text(match.groupdict().get("entity") or match.groupdict().get("role"))
        value = re.sub(r"^(?:除|由|对|为|向|给|和|与|或|及|当|若|如果|但|但是|已|未|待|处于|进入|当前|本|该|上述|前述)+", "", value)
        value = re.sub(r"^.*?(?:的|后|前|时|则)", "", value)
        if value:
            mentions.append(value[-16:])
    return sorted(set(mentions), key=lambda item: (text.find(item), -len(item), item))


def _action(text: str) -> dict[str, Any]:
    matches: list[tuple[int, int, str, str]] = []
    for canonical, pattern in _ACTION_REGEXES:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), canonical, match.group(0)))
    if not matches:
        return {}
    pivots = [match.end() for match in re.finditer(r"不得|严禁|禁止|不允许|不可|不能|无权|必须|应当|只能|仅能|可以|允许|有权|可由|自动|则", text)]
    if not pivots:
        pivots = [match.end() for match in re.finditer(r"后|时|之后|之前", text)]
    pivot = pivots[-1] if pivots else -1
    after_pivot = [row for row in matches if row[0] >= pivot]
    selected = min(after_pivot, key=lambda row: row[0]) if after_pivot else max(matches, key=lambda row: row[0])
    _, _, canonical, raw = selected
    return {"canonical": canonical, "raw": raw}


def _conditions(text: str) -> list[str]:
    values: list[str] = []
    for pattern in _CONDITION_PATTERNS:
        for match in pattern.finditer(text):
            value = _text(match.group("value"))
            if value and value not in values:
                values.append(value)
    state_condition = re.search(r"(?P<value>(?:已|未|待|处于)[^，,；;。]{1,32}?)(?=不得|不能|不可|只能|仅能|可以|允许|必须|应当)", text)
    if state_condition:
        value = _text(state_condition.group("value"))
        if value and value not in values:
            values.append(value)
    return values


def _condition_combinator(text: str, conditions: list[str]) -> str:
    """Derive multi-condition combinator only from explicit source wording."""
    if len(conditions) <= 1:
        return "SINGLE_CONDITION" if conditions else ""
    has_and = bool(_AND_COMBINATOR_RE.search(text))
    has_or = bool(_OR_COMBINATOR_RE.search(text))
    if has_and and has_or:
        return "UNRESOLVED"
    if has_and:
        return "AND"
    if has_or:
        return "OR"
    return "UNRESOLVED"


def _state_effects(text: str) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for match in _STATE_TRANSITION_RE.finditer(text):
        from_state = _text(match.group("from"))
        to_state = _text(match.group("to") or match.group("to_only"))
        if from_state or to_state:
            effects.append({"from_state": from_state, "to_state": to_state, "raw": match.group(0)})
    return effects


def _modality(text: str) -> tuple[str, str]:
    if _NEGATIVE_RE.search(text):
        return "MUST_NOT", "NEGATIVE"
    if _ONLY_IF_RE.search(text):
        return "ONLY_IF", "POSITIVE"
    if _MUST_RE.search(text):
        return "MUST", "POSITIVE"
    if _MAY_RE.search(text):
        return "MAY", "POSITIVE"
    return "ASSERTS", "POSITIVE"


def _exception(text: str) -> list[str]:
    return list(dict.fromkeys(_text(match.group(0)) for match in _EXCEPTION_RE.finditer(text) if _text(match.group(0))))


def _scope(text: str) -> dict[str, str]:
    ownership_match = _OWNERSHIP_SCOPE_RE.search(text)
    organization_match = _ORG_SCOPE_RE.search(text)
    return {
        "tenant": "当前租户" if "租户" in text else "",
        "organization": organization_match.group(0) if organization_match else "",
        "ownership": ownership_match.group(0) if ownership_match else "",
        "data_scope": organization_match.group(0) if organization_match else "",
    }


def _split_rule_units(text: str) -> list[str]:
    units = [unit.strip() for unit in re.split(r"[。！？!?；;]+", text) if unit.strip()]
    result: list[str] = []
    for unit in units:
        contrast = re.search(r"[，,](?:但|但是|不过|然而)", unit)
        if contrast and contrast.start() > 0:
            left = unit[: contrast.start()].strip(" ，,")
            right = unit[contrast.start() + 1 :].strip()
            if left:
                result.append(left)
            if right:
                result.append(right)
        else:
            result.append(unit)
    return result


def _resolve_reference(text: str, explicit_entities: list[str], context_entities: list[str]) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    resolved = list(explicit_entities)
    evidence: list[dict[str, Any]] = []
    ambiguities: list[str] = []
    mention = _COREFERENCE_RE.search(text)
    if mention and not explicit_entities:
        unique_context = list(dict.fromkeys(context_entities[-3:]))
        if len(unique_context) == 1:
            resolved.append(unique_context[0])
            evidence.append({"mention": mention.group(0), "resolved_ref": unique_context[0], "method": "nearest_unambiguous_entity_context", "confidence": 0.78})
        elif len(unique_context) > 1:
            ambiguities.append("COREFERENCE_AMBIGUOUS:" + ",".join(unique_context))
        else:
            ambiguities.append("COREFERENCE_UNRESOLVED")
    return sorted(set(resolved)), evidence, ambiguities


def _fact_from_unit(unit: str, *, source_id: str, locator: str, known_entities: list[str], known_roles: list[str], context_entities: list[str], context_roles: list[str]) -> dict[str, Any] | None:
    raw = unit.strip()
    if not raw or not _RULE_SIGNAL_RE.search(raw):
        return None
    entities = _find_mentions(raw, known_entities, _ENTITY_SUFFIX_RE)
    roles = _find_mentions(raw, known_roles, _ROLE_SUFFIX_RE)
    entities, resolution_evidence, ambiguities = _resolve_reference(raw, entities, context_entities)
    if not roles and re.search(r"自动|系统", raw):
        roles = ["系统"]
    elif not roles and context_roles and re.search(r"其|该操作|完成后|通过后|退回后", raw):
        roles = [context_roles[-1]]
        resolution_evidence.append({"mention": "省略Actor", "resolved_ref": context_roles[-1], "method": "nearest_actor_context", "confidence": 0.72})

    action = _action(raw)
    modality, polarity = _modality(raw)
    conditions = _conditions(raw)
    condition_combinator = _condition_combinator(raw, conditions)
    exceptions = _exception(raw)
    states = _state_effects(raw)
    temporal = [match.group("value") for match in _TEMPORAL_RE.finditer(raw)]
    scope = _scope(raw)
    if not action and not states and modality == "ASSERTS":
        return None

    critical = bool(_CRITICAL_SIGNAL_RE.search(raw))
    if critical and not entities and re.search(r"该|本|其|上述|前述|对应|相关", raw) and "COREFERENCE_UNRESOLVED" not in ambiguities:
        ambiguities.append("BUSINESS_SUBJECT_UNRESOLVED")
    if modality in {"MUST_NOT", "ONLY_IF"} and not action and not states:
        ambiguities.append("CRITICAL_ACTION_UNRESOLVED")
    if exceptions and len(_split_rule_units(raw)) == 1 and not conditions and not re.match(r"^(?:除[^，,；;。]+外|但|但是|不过|然而)", raw):
        ambiguities.append("EXCEPTION_SCOPE_UNRESOLVED")
    if len(conditions) > 1 and condition_combinator == "UNRESOLVED":
        ambiguities.append("CONDITION_COMBINATOR_UNRESOLVED")

    confidence = 0.62 + (0.08 if entities else 0.0) + (0.06 if roles else 0.0) + (0.10 if action else 0.0) + (0.07 if conditions else 0.0) + (0.04 if states else 0.0) + (0.03 if modality != "ASSERTS" else 0.0) - 0.12 * len(ambiguities)
    confidence = max(0.05, min(0.99, confidence))
    status = "PENDING" if ambiguities else "ACCEPTED"
    kind = "STATE_TRANSITION" if states else "RULE"
    quote_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    fact_id = _stable_id("fact", source_id, locator, kind, raw)
    return {
        "fact_id": fact_id,
        "kind": kind,
        "language": _language_of(raw),
        "subject": {"actor_refs": roles, "entity_refs": entities, "resolution_evidence": resolution_evidence},
        "conditions": conditions,
        "condition_combinator": condition_combinator,
        "trigger": {"raw": conditions[0]} if conditions else {},
        "action": action,
        "object": {"entity_refs": entities},
        "scope": scope,
        "modality": modality,
        "polarity": polarity,
        "exceptions": exceptions,
        "postconditions": [],
        "state_effects": states,
        "data_effects": [],
        "temporal_constraints": temporal,
        "compensation": [],
        "raw_statement": raw,
        "normalized_statement": re.sub(r"\s+", "", raw),
        "source_spans": [{"source_id": source_id, "locator": locator, "quote": raw, "quote_hash": quote_hash}],
        "confidence": round(confidence, 4),
        "status": status,
        "ambiguities": ambiguities,
        "critical": critical,
    }


def analyze_chinese_business_source(source: dict[str, Any], *, asset: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return coverage chunks, business facts and glossary facts for one source."""
    asset = asset or {}
    source_id = _text(source.get("source_id")) or _stable_id("source", source.get("filename"), source.get("text"))
    filename = _text(source.get("filename") or source.get("original_name") or source.get("source_locator"))
    text = _normalize_chinese_text(_text(source.get("text")))
    known_entities = _known_names(asset, "entity")
    known_roles = _known_names(asset, "role")
    coverage: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    glossary: list[dict[str, Any]] = []
    context_entities: list[str] = []
    context_roles: list[str] = []

    for chunk in _paragraph_chunks(text):
        chunk_text = _text(chunk.get("text"))
        locator = f"{filename or source_id}#section={chunk.get('section')};chars={chunk.get('start')}-{chunk.get('end')}"
        chunk_id = _stable_id("chunk", source_id, locator, chunk_text)
        chunk_fact_ids: list[str] = []
        chunk_ambiguities: list[str] = []
        chunk_facts: list[dict[str, Any]] = []
        glossary_rows = _extract_aliases(chunk_text, source_id, locator)
        glossary.extend(glossary_rows)
        chunk_fact_ids.extend(row["fact_id"] for row in glossary_rows)

        if chunk.get("kind") != "heading":
            for unit in _split_rule_units(chunk_text):
                fact = _fact_from_unit(unit, source_id=source_id, locator=locator, known_entities=known_entities, known_roles=known_roles, context_entities=context_entities, context_roles=context_roles)
                if not fact:
                    continue
                chunk_facts.append(fact)
                chunk_fact_ids.append(fact["fact_id"])
                chunk_ambiguities.extend(_list(fact.get("ambiguities")))
                for entity in _list(_dict(fact.get("subject")).get("entity_refs")):
                    if entity:
                        context_entities.append(_text(entity))
                for role in _list(_dict(fact.get("subject")).get("actor_refs")):
                    if role and role != "系统":
                        context_roles.append(_text(role))

        facts.extend(chunk_facts)
        language = _language_of(chunk_text)
        contains_business_signal = bool(_RULE_SIGNAL_RE.search(chunk_text))
        if chunk.get("kind") == "heading":
            status = "UNDERSTOOD_CONTEXT"
        elif language not in {"zh-CN", "zh-CN-mixed"}:
            status = "TERMINAL_NON_CHINESE"
        elif contains_business_signal and not chunk_facts:
            status = "UNRESOLVED_BUSINESS_TEXT"
            chunk_ambiguities.append("BUSINESS_FACT_NOT_EXTRACTED")
        elif any(_text(row.get("status")) == "PENDING" for row in chunk_facts):
            status = "AMBIGUOUS"
        elif chunk_facts or glossary_rows:
            status = "UNDERSTOOD"
        else:
            status = "UNDERSTOOD_CONTEXT"

        coverage.append({
            "chunk_id": chunk_id,
            "source_id": source_id,
            "filename": filename,
            "language": language,
            "section": _text(chunk.get("section")) or "document",
            "kind": _text(chunk.get("kind")) or "content",
            "start_offset": int(chunk.get("start") or 0),
            "end_offset": int(chunk.get("end") or 0),
            "text_hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            "status": status,
            "contains_business_signal": contains_business_signal,
            "extracted_fact_ids": sorted(set(chunk_fact_ids)),
            "ambiguities": sorted(set(chunk_ambiguities)),
            "source_locator": locator,
        })
    return coverage, facts, glossary


def _rule_from_fact(fact: dict[str, Any]) -> dict[str, Any] | None:
    if _text(fact.get("status")) != "ACCEPTED" or _text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
        return None
    statement = _text(fact.get("raw_statement"))
    if not statement:
        return None
    subject = _dict(fact.get("subject"))
    action = _dict(fact.get("action"))
    spans = _list(fact.get("source_spans"))
    span = _dict(spans[0]) if spans else {}
    modality = _text(fact.get("modality"))
    if _list(fact.get("state_effects")):
        risk_type = "state_transition"
    elif _list(subject.get("actor_refs")) or any(_text(value) for value in _dict(fact.get("scope")).values()):
        risk_type = "authorization"
    else:
        risk_type = "business_logic"
    return {
        "rule_id": f"zh_business:{_text(fact.get('fact_id')).split(':')[-1]}",
        "source_id": span.get("source_id"),
        "source_locator": span.get("locator"),
        "source_type": "chinese_business_semantic_contract",
        "statement": statement,
        "rule_type": "permission" if risk_type == "authorization" else "business_rule",
        "risk_type": risk_type,
        "severity": "P0" if modality == "MUST_NOT" else "P1",
        "tokens": sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,12}", statement))),
        "semantic_contract": fact,
        "actor_refs": _list(subject.get("actor_refs")),
        "entity_refs": _list(subject.get("entity_refs")),
        "action": action.get("canonical") or action.get("raw"),
        "modality": modality,
        "conditions": _list(fact.get("conditions")),
        "condition_combinator": _text(fact.get("condition_combinator")),
        "confidence": fact.get("confidence"),
        "derivation": "chinese_first_business_comprehension",
    }


def _term_alias_map(facts: Iterable[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Build alias→canonical from ACCEPTED TERM_ALIAS facts; conflicting aliases stay unresolved."""
    alias_to_canonical: dict[str, str] = {}
    conflicting: dict[str, set[str]] = {}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if _text(fact.get("kind")) != "TERM_ALIAS" or _text(fact.get("status")) != "ACCEPTED":
            continue
        canonical = _text(fact.get("canonical_term"))
        alias = _text(fact.get("alias"))
        if not canonical or not alias or canonical == alias:
            continue
        existing = alias_to_canonical.get(alias)
        if existing and existing != canonical:
            conflicting.setdefault(alias, {existing}).add(canonical)
            continue
        alias_to_canonical[alias] = canonical
    conflict_rows: list[dict[str, Any]] = []
    for alias, canons in sorted(conflicting.items()):
        alias_to_canonical.pop(alias, None)
        conflict_rows.append(
            {
                "kind": "TERM_ALIAS_IDENTITY_CONFLICT",
                "alias": alias,
                "canonical_candidates": sorted(canons),
                "status": "UNRESOLVED",
                "automatic_resolution_allowed": False,
                "reason": "same alias maps to multiple source-backed canonical terms",
            }
        )
    return alias_to_canonical, conflict_rows


def apply_source_backed_term_aliases(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Rewrite entity refs through unambiguous TERM_ALIAS evidence without inventing identity.

    Cross-document alias evidence is accepted when every TERM_ALIAS statement is ACCEPTED
    and the alias maps to exactly one canonical term. Conflicting alias mappings are left
    unresolved and never merged.
    """
    alias_map, conflicts = _term_alias_map(facts)
    rewritten = 0
    for fact in facts:
        if not isinstance(fact, dict) or _text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            continue
        subject = _dict(fact.get("subject"))
        object_part = _dict(fact.get("object"))
        before = [
            *_list(subject.get("entity_refs")),
            *_list(object_part.get("entity_refs")),
        ]
        if not before or not any(name in alias_map for name in before):
            continue
        subject_refs = [_text(alias_map.get(_text(name), name)) for name in _list(subject.get("entity_refs"))]
        object_refs = [_text(alias_map.get(_text(name), name)) for name in _list(object_part.get("entity_refs"))]
        subject["entity_refs"] = sorted({name for name in subject_refs if name})
        object_part["entity_refs"] = sorted({name for name in object_refs if name})
        resolution = _list(subject.get("resolution_evidence"))
        for name in before:
            canonical = alias_map.get(_text(name))
            if canonical and canonical != name:
                resolution.append(
                    {
                        "mention": name,
                        "resolved_ref": canonical,
                        "method": "source_backed_term_alias",
                        "confidence": 1.0,
                    }
                )
                rewritten += 1
        subject["resolution_evidence"] = resolution
        fact["subject"] = subject
        fact["object"] = object_part
    for conflict in conflicts:
        for fact in facts:
            if not isinstance(fact, dict) or _text(fact.get("kind")) != "TERM_ALIAS":
                continue
            if _text(fact.get("alias")) != _text(conflict.get("alias")):
                continue
            fact["status"] = "PENDING"
            ambiguities = _list(fact.get("ambiguities"))
            if "TERM_ALIAS_IDENTITY_CONFLICT" not in ambiguities:
                ambiguities.append("TERM_ALIAS_IDENTITY_CONFLICT")
            fact["ambiguities"] = ambiguities
    return {
        "schema": "qualibug.enterprise-term-alias-identity-merge.v1",
        "alias_to_canonical": dict(sorted(alias_map.items())),
        "rewritten_entity_ref_count": rewritten,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "automatic_inference_allowed": False,
        "merge_policy": "source_backed_term_alias_only",
    }


def build_chinese_first_comprehension(asset: dict[str, Any], parsed_sources: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Enrich an existing knowledge asset with Chinese-first formal ledgers."""
    all_coverage: list[dict[str, Any]] = []
    all_facts: list[dict[str, Any]] = []
    all_glossary: list[dict[str, Any]] = []
    for source in parsed_sources:
        if isinstance(source, dict):
            coverage, facts, glossary = analyze_chinese_business_source(source, asset=asset)
            all_coverage.extend(coverage)
            all_facts.extend(facts)
            all_glossary.extend(glossary)

    all_facts = list({_text(row.get("fact_id")): row for row in [*all_facts, *all_glossary] if isinstance(row, dict) and _text(row.get("fact_id"))}.values())
    identity_merge = apply_source_backed_term_aliases(all_facts)
    all_coverage = list({_text(row.get("chunk_id")): row for row in all_coverage if isinstance(row, dict) and _text(row.get("chunk_id"))}.values())
    statuses = Counter(_text(row.get("status")) for row in all_coverage)
    chinese_chunks = [row for row in all_coverage if _text(row.get("language")) in {"zh-CN", "zh-CN-mixed"}]
    unresolved = [row for row in chinese_chunks if _text(row.get("status")) in {"AMBIGUOUS", "UNRESOLVED_BUSINESS_TEXT"}]
    critical_ambiguities = [{"chunk_id": row.get("chunk_id"), "source_id": row.get("source_id"), "source_locator": row.get("source_locator"), "ambiguities": row.get("ambiguities")} for row in unresolved if row.get("contains_business_signal") and (_text(row.get("status")) == "UNRESOLVED_BUSINESS_TEXT" or any(_text(value).startswith(_CRITICAL_AMBIGUITY_PREFIXES) for value in _list(row.get("ambiguities"))))]
    # Alias identity conflicts are critical even when the glossary chunk itself is UNDERSTOOD.
    for conflict in _list(identity_merge.get("conflicts")):
        critical_ambiguities.append(
            {
                "chunk_id": "",
                "source_id": "*",
                "source_locator": "",
                "ambiguities": ["TERM_ALIAS_IDENTITY_CONFLICT"],
                "details": conflict,
            }
        )
    accepted = [row for row in all_facts if _text(row.get("status")) == "ACCEPTED"]
    pending = [row for row in all_facts if _text(row.get("status")) == "PENDING"]
    terminal_statuses = {"UNDERSTOOD", "UNDERSTOOD_CONTEXT", "TERMINAL_NON_CHINESE", "AMBIGUOUS", "UNRESOLVED_BUSINESS_TEXT"}
    terminal_count = sum(1 for row in all_coverage if _text(row.get("status")) in terminal_statuses)
    source_ids = sorted({_text(row.get("source_id")) for row in all_coverage if _text(row.get("source_id"))})
    chinese_source_ids = sorted({_text(row.get("source_id")) for row in chinese_chunks if _text(row.get("source_id"))})
    status = "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE" if critical_ambiguities else "PASS"
    gate = {
        "schema": GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "language_contract": "CHINESE_SOURCE_TEXT_IS_FACT_AUTHORITY",
        "translation_as_fact_authority": False,
        "metrics": {
            "source_count": len(source_ids),
            "chinese_source_count": len(chinese_source_ids),
            "chunk_count": len(all_coverage),
            "chinese_chunk_count": len(chinese_chunks),
            "terminal_chunk_count": terminal_count,
            "accepted_fact_count": len(accepted),
            "pending_fact_count": len(pending),
            "unresolved_chunk_count": len(unresolved),
            "critical_ambiguity_count": len(critical_ambiguities),
            "term_alias_rewrite_count": int(identity_merge.get("rewritten_entity_ref_count") or 0),
            "term_alias_conflict_count": int(identity_merge.get("conflict_count") or 0),
            "status_distribution": dict(statuses),
        },
        "critical_unknowns": critical_ambiguities,
        "required_operator_action": "resolve Chinese business subject/action/exception ambiguity before promoting affected facts into Behavior IR" if critical_ambiguities else "",
    }

    asset["document_coverage_ledger"] = {"schema": COVERAGE_SCHEMA, "language_priority": ["zh-CN", "zh-CN-mixed"], "items": all_coverage}
    asset["business_fact_ledger"] = {"schema": FACT_SCHEMA, "fact_authority": "original_chinese_source_span", "translation_intermediate_forbidden": True, "items": all_facts}
    asset["chinese_business_glossary"] = {"schema": SCHEMA_VERSION, "merge_policy": "source_evidence_required", "items": all_glossary}
    asset["term_alias_identity_merge"] = identity_merge
    asset["enterprise_comprehension_gate"] = gate

    existing_rules = [dict(row) for row in _list(asset.get("rule_library")) if isinstance(row, dict)]
    existing_rule_ids = {_text(row.get("rule_id")) for row in existing_rules}
    existing_statements = {(_text(row.get("source_id")), re.sub(r"\s+", "", _text(row.get("statement")))) for row in existing_rules}
    promoted_rules: list[dict[str, Any]] = []
    for fact in all_facts:
        rule = _rule_from_fact(fact)
        if not rule:
            continue
        identity = (_text(rule.get("source_id")), re.sub(r"\s+", "", _text(rule.get("statement"))))
        if _text(rule.get("rule_id")) in existing_rule_ids or identity in existing_statements:
            continue
        existing_rule_ids.add(_text(rule.get("rule_id")))
        existing_statements.add(identity)
        promoted_rules.append(rule)
    asset["rule_library"] = [*existing_rules, *promoted_rules]

    coverage_gaps = [dict(row) for row in _list(asset.get("coverage_gaps")) if isinstance(row, dict) and _text(row.get("kind")) != "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE"]
    if status != "PASS":
        coverage_gaps.append({"kind": "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE", "gap_type": "chinese_business_semantics_incomplete", "source_id": "*", "critical_unknowns": critical_ambiguities, "operator_action": gate["required_operator_action"]})
    asset["coverage_gaps"] = coverage_gaps

    summary = _dict(asset.get("summary"))
    summary.update({
        "rule_count": len(asset["rule_library"]),
        "chinese_business_fact_count": len(all_facts),
        "chinese_business_fact_accepted": len(accepted),
        "chinese_business_fact_pending": len(pending),
        "chinese_business_chunk_count": len(chinese_chunks),
        "business_comprehension_status": status,
        "business_comprehension_ready": status == "PASS",
        "chinese_source_is_fact_authority": True,
        "term_alias_identity_merge_count": int(identity_merge.get("rewritten_entity_ref_count") or 0),
    })
    asset["summary"] = summary
    governance = _dict(asset.get("governance"))
    governance.update({
        "chinese_first_enterprise_comprehension": True,
        "chinese_source_text_is_fact_authority": True,
        "translation_intermediate_cannot_promote_facts": True,
        "ambiguous_critical_chinese_rules_fail_closed": True,
        "multi_condition_default_and_forbidden": True,
        "term_alias_identity_merge_source_backed_only": True,
    })
    asset["governance"] = governance
    return asset


def _parsed_sources_for_asset(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    from ._crud import _record_parse

    parsed_sources: list[dict[str, Any]] = []
    for source in _list(asset.get("source_inventory")):
        if not isinstance(source, dict) or _text(source.get("status")) != "active":
            continue
        parsed = _record_parse(source, root)
        parser_receipt = _dict(parsed.get("parser_receipt"))
        parsed_sources.append({"source_id": source.get("source_id"), "filename": source.get("original_name"), "source_locator": parser_receipt.get("source_locator"), "text": parsed.get("text") or ""})
    return parsed_sources


def _persist_enriched_asset(asset: dict[str, Any], project_id: str, root: Path) -> None:
    from . import _api
    from ._common import _write_json
    from ._utils import _paths

    paths = _paths(project_id, root)
    for key in ("asset", "asset_copy"):
        path = paths.get(key)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, asset)
    report = paths.get("report")
    if report:
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(_api.render_enterprise_business_knowledge_report(asset), encoding="utf-8")
    center_page = paths.get("center_page")
    if center_page:
        Path(center_page).parent.mkdir(parents=True, exist_ok=True)
        Path(center_page).write_text(_api.render_enterprise_business_knowledge_center(project_id, root, asset=asset), encoding="utf-8")


def install_chinese_first_business_comprehension():
    """Install the additive comprehension stage on the existing build mainline."""
    from . import _api
    from ._common import ROOT, _safe_project_id

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, "_qualibug_chinese_first_comprehension", False):
        return current
    original = current

    @wraps(original)
    def wrapped(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        asset = original(project, resolved_root, options)
        try:
            enriched = build_chinese_first_comprehension(asset, _parsed_sources_for_asset(asset, resolved_root))
        except Exception as exc:
            enriched = asset
            gaps = [dict(row) for row in _list(enriched.get("coverage_gaps")) if isinstance(row, dict)]
            gaps.append({"kind": "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE", "gap_type": "chinese_business_comprehension_stage_failed", "source_id": "*", "error_type": type(exc).__name__, "operator_action": "inspect Chinese comprehension stage failure"})
            enriched["coverage_gaps"] = gaps
            summary = _dict(enriched.get("summary"))
            summary.update({"business_comprehension_status": "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE", "business_comprehension_ready": False, "chinese_source_is_fact_authority": True})
            enriched["summary"] = summary
            enriched["enterprise_comprehension_gate"] = {"schema": GATE_SCHEMA, "status": "BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE", "entry_allowed": False, "language_contract": "CHINESE_SOURCE_TEXT_IS_FACT_AUTHORITY", "translation_as_fact_authority": False, "failure": {"type": type(exc).__name__, "message": str(exc)[:320]}}
        _persist_enriched_asset(enriched, project, resolved_root)
        return enriched

    wrapped._qualibug_chinese_first_comprehension = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "SCHEMA_VERSION",
    "COVERAGE_SCHEMA",
    "FACT_SCHEMA",
    "GATE_SCHEMA",
    "analyze_chinese_business_source",
    "apply_source_backed_term_aliases",
    "build_chinese_first_comprehension",
    "install_chinese_first_business_comprehension",
]
