"""Chinese-first enterprise business comprehension for the knowledge-center mainline.

The product targets Chinese enterprise materials. This module keeps Chinese source
text as the formal fact authority, builds a source-span coverage ledger, extracts
structured business facts without translating through English, and emits a
fail-closed comprehension gate before downstream consumers treat the asset as
business-complete.

The implementation is additive: it enriches the existing enterprise knowledge
asset and reuses its source registry, parser receipts, rule library and persistence
paths instead of creating a parallel product path.

RESPONSIBILITY BOUNDARY (source-backed business-rule semantic extraction):
this regex extractor is the HIGH-PRECISION CANDIDATE layer, not the open-semantic
comprehension layer. It owns:
  * generic language-form parsing (numbers, amounts, percentages, quantities,
    dates, times, durations, windows, comparison operators, ranges, units,
    enumerations, table structure, source spans);
  * high-precision rule hints from cross-industry signal words (必须/不得/禁止/
    仅限/至少/最多/除非/方可/不超过/大于等于 …);
  * deterministic evidence validation (quotes exist, spans align, numerics match).
It must NOT grow industry keyword tables (订单/库存/审批/医疗/制造/财务 vocabularies
are legacy and frozen). Open-semantic recall of rules that do not hit the signal
vocabulary is the LLM semantic-extraction layer's job (_semantic_extraction.py,
kind=rule), which feeds the same candidate → validation → governance chain.
Regex output is CANDIDATE, never a formal fact.
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
    r"^\s*(?:#{1,6}\s+(?P<title>.+?)|"
    r"第[一二三四五六七八九十百千]+[章节部分]\s*(?P<title2>.+?)|"
    r"[一二三四五六七八九十]+[、.]\s*(?P<title3>.+?)|"
    # Numbered headings require an explicit separator and whitespace. Lines such as
    # ``1）其不得删除`` are list items with business rules, not headings — treating
    # them as headings silently drops structured facts.
    r"\d+(?:\.\d+)*[、.)）．]\s+(?P<title4>.+?))\s*$"
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
_EXCEPTION_RE = re.compile(
    r"(?:但|但是|不过|然而)(?P<contrast>.+)|"
    r"除(?P<except_of>[^，,；;。外]{1,24})外|"
    r"(?P<except_actor>[\u4e00-\u9fffA-Za-z0-9_-]{1,20})除外|"
    r"除非(?P<unless>.+)"
)
_TEMPORAL_RE = re.compile(
    r"(?P<value>(?:在)?[^，,；;。]{0,48}?"
    r"(?:之前|之后|前|后|时|期间|以内|之内|超过\d+[^，,；;。]*|至少\d+[^，,；;。]*))"
)
_EXPLICIT_CONDITION_HEAD_RE = re.compile(
    r"(?:如果|若|一旦|当(?!前|期|日|月|年|次|笔|个|下|中))"
)
_FOLLOW_ON_CONDITION_RE = re.compile(
    r"(?:并且|同时满足|以及|(?<![并])且(?![不]))(?P<value>[^，,；;。则]{1,48})"
)
_CONDITION_PATTERNS = (
    # ``当`` is a condition introducer, but lexical compounds such as ``当前组织``
    # are business identities rather than condition heads.
    re.compile(
        r"(?:如果|若|一旦|当(?!前|期|日|月|年|次|笔|个|下|中))"
        r"(?P<value>.+?)(?:时|则|，|,|；|;|$)"
    ),
    re.compile(r"只有(?P<value>.+?)才"),
    re.compile(r"仅当(?P<value>.+?)才"),
    re.compile(r"除非(?P<value>.+?)(?:，|,|；|;|$)"),
    # Follow-on AND clauses are conditions only inside an already explicit
    # condition frame. A qualifier such as ``本人创建且尚未审批的订单`` belongs
    # to the governed object and is normalized at the semantic boundary instead.
    _FOLLOW_ON_CONDITION_RE,
    # Temporal prefix before an effect/action — do not require a delimiter after 后/之前.
    re.compile(
        r"(?P<value>[^，,；;。]{1,60}?(?:之前|之后|以前|以后|前|后))"
        r"(?=自动|必须|应当|需要|应|不得|不能|不可|只能|仅能|可以|允许|则|，|,|；|;|$)"
    ),
)
# Explicit source combinators only. Multiple conditions without one stay UNRESOLVED —
# never default to AND.
_AND_COMBINATOR_RE = re.compile(r"并且|同时满足|以及|(?<![并])且(?![不])")
_OR_COMBINATOR_RE = re.compile(r"或者|或则|任一条件|其中之一")
_IF_THEN_ELSE_RE = re.compile(
    r"^(?P<prefix>(?:除[^，,；;。外]{1,24}外[，,]?)?)"
    r"(?:如果|若|一旦)(?P<cond>.+?)"
    r"(?:时)?[，,]?则(?P<then>.+?)"
    r"[，,]?否则(?P<else>.+)$"
)
_EXCEPTION_ATOM_RE = (
    r"(?:除[^。外]{1,24}外|[\u4e00-\u9fffA-Za-z0-9_-]{1,20}除外)"
)
# Single or chained overlays: 但管理员除外 / 但管理员除外，财务除外 / 除A外，除B外
_EXCEPTION_OVERLAY_CLAUSE_RE = re.compile(
    rf"^(?:但|但是|不过|然而)?{_EXCEPTION_ATOM_RE}"
    rf"(?:[，,]{_EXCEPTION_ATOM_RE})*$"
)
_ELSE_IF_HEAD_RE = re.compile(r"^(?:如果|若|一旦)")
_CRITICAL_AMBIGUITY_PREFIXES = (
    "COREFERENCE_",
    "BUSINESS_SUBJECT_",
    "CRITICAL_ACTION_",
    "EXCEPTION_SCOPE_",
    "CONDITION_COMBINATOR_",
    "OMITTED_ACTOR_",
    "IF_THEN_ELSE_",
    "NESTED_BRANCH_",
    "BRANCH_",
)
_COREFERENCE_RE = re.compile(
    r"该(?:对象|记录|数据|单据|申请|订单|工单|合同|任务)?|"
    r"本(?:单|记录|对象|申请|订单|工单|合同)?|"
    r"此(?:时|对象|记录|操作|流程)?|其|上述|前述|对应|相关|当前"
)
_OMITTED_ACTOR_RE = re.compile(r"其|该操作|完成后|通过后|退回后|审批后|审核后")
_OWNERSHIP_SCOPE_RE = re.compile(r"本人|自己|其本人|原申请人|原发起人|本人创建|本人提交|本人负责")
_ORG_SCOPE_RE = re.compile(r"本部门|本组织|本区域|本仓库|当前租户|本租户|所属组织|所属部门|所属区域")
_DELEGATION_RE = re.compile(
    r"(?P<delegator>[\u4e00-\u9fffA-Za-z0-9_-]{1,20})"
    r"(?:授权|委托|转授)(?P<delegatee>[\u4e00-\u9fffA-Za-z0-9_-]{1,20})"
    r"(?:代为|进行|执行|办理|审批|审核|操作)?"
)
_EXCEPTION_SCOPE_RE = re.compile(
    r"除(?P<scope>[^，,；;。外]{1,24})外|(?P<actor>[\u4e00-\u9fffA-Za-z0-9_-]{1,20})除外"
)
_DEICTIC_GENERIC_RE = re.compile(
    r"(?:该|本|此)(?P<generic>对象|记录|数据|单据|申请单|申请|订单|工单|合同|任务)"
)
_QUANTITY_RE = re.compile(
    r"(?P<op>超过|不少于|至少|至多|不超过|大于|小于|等于|最多|最少)"
    r"(?P<value>\d+(?:\.\d+)?)(?P<unit>[A-Za-z%％元件个台次天小时分钟]{0,8})"
)
_TIME_WINDOW_RE = re.compile(
    r"(?P<raw>(?:在)?(?P<anchor>[^，,；;。]{0,24}?)"
    r"(?P<rel>之前|之后|前|后|以内|之内|期间)"
    r"(?:的?(?P<duration>\d+(?:\.\d+)?(?:天|日|小时|分钟|秒)))?)"
)
_FORMULA_RE = re.compile(
    r"(?P<lhs>[\u4e00-\u9fffA-Za-z0-9_.]{1,24})\s*[=＝]\s*"
    r"(?P<rhs>[^，,；;。]{1,48})"
)
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
_TERM_TOKEN = r"[\u4e00-\u9fffA-Za-z0-9_.-]{1,40}"
_ALIAS_PATTERNS = (
    # Parenthetical short form: 生产任务单（MO）
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})[（(](?P<alias>[^()（），,；;。]{{1,30}})[）)]"
    ),
    # Explicit short-name markers: 以下简称 / 简称为 / 简称
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})(?:以下简称|简称为|简称)(?P<alias>{_TERM_TOKEN})"
    ),
    # Synonym / also-known-as markers (left = stated term, right = alternate)
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})(?:又称|也称|又名|也叫|又叫|亦称)(?P<alias>{_TERM_TOKEN})"
    ),
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})(?:即为|即是|也即|即(?!可|使|便|将|刻|日|令))(?P<alias>{_TERM_TOKEN})"
    ),
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})(?:等同于|相当于|同义于)(?P<alias>{_TERM_TOKEN})"
    ),
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})(?:是指|指的是|定义为|定义是)(?P<alias>{_TERM_TOKEN})"
    ),
    # English source-backed markers
    re.compile(
        rf"(?P<canonical>{_TERM_TOKEN})\s*(?:aka|a\.k\.a\.|also known as|also called)\s*"
        rf"(?P<alias>{_TERM_TOKEN})",
        re.I,
    ),
)
_GLOSSARY_TERM_HEADER_RE = re.compile(
    r"术语|名称|中文名|中文|全称|词条|对象名|业务对象|定义项|标准名|canonical|term|name",
    re.I,
)
_GLOSSARY_ALIAS_HEADER_RE = re.compile(
    r"别名|简称|英文名|英文|代号|代码|别称|又称|缩写|同义词|alias|code|abbr|abbreviation|english",
    re.I,
)
_GENERIC_TERM_SUFFIX_RE = re.compile(r"(?:单据|对象|记录|数据|实体|术语|名称)$")
_TRIGGER_THEN_EFFECT_RE = re.compile(
    r"(?P<trigger>[^，,；;。]{1,48}?)(?:之后|以后|后)"
    r"(?P<effect>(?:自动|必须|应当|需要|应)?"
    r"(?:生成|创建|新建|写入|更新|删除|释放|扣减|增加|发送|通知|补偿|回滚|冲正|冲销|红冲|退款)"
    r"[^，,；;。]{0,40})"
)
_DATA_EFFECT_RE = re.compile(
    r"(?:自动)?(?P<action>生成|创建|新建|写入|更新|删除|释放|扣减|减少|增加|恢复|发送|通知)"
    r"\s*"
    r"(?P<object>[\u4e00-\u9fffA-Za-z0-9_-]{1,24})"
)
_ACTION_NOUN_SUFFIX_RE = re.compile(
    r"^(?:人|人员|员|者|方|角色|金额|数量|比例|率|时间|日期|状态|编号|单号|结果|记录|信息)"
)
_EFFECT_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?:[，,；;、](?:并|并且|且|同时|以及|然后|随后)?|"
    r"(?:并|并且|同时|然后|随后)|"
    r"(?:时|之后|以后|后)[，,；;]?(?:应当|必须|应|需要)?)\s*$"
)
_COMPENSATION_RE = re.compile(
    r"(?P<raw>(?:补偿|回滚|冲正|冲销|红冲|退款(?!金额|数量|比例|率|时间|日期|状态|编号|单号)|退货|反向冲销)"
    r"[^，,；;。]{0,32})"
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
            section = (
                _text(heading.group("title"))
                or _text(heading.group("title2"))
                or _text(heading.group("title3"))
                or _text(heading.group("title4"))
                or paragraph
            )
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


def _clean_term(value: str) -> str:
    cleaned = _text(value).strip(" ：:、，,；;。\"'“”‘’[]【】")
    cleaned = _GENERIC_TERM_SUFFIX_RE.sub("", cleaned)
    return _text(cleaned)


def _prefer_canonical_alias(left: str, right: str) -> tuple[str, str]:
    """Order identity without industry knowledge: prefer CJK full form over short code."""
    left_cjk = len(_CHINESE_RE.findall(left))
    right_cjk = len(_CHINESE_RE.findall(right))
    left_latin = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,15}", left))
    right_latin = bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,15}", right))
    if left_cjk >= 2 and right_latin:
        return left, right
    if right_cjk >= 2 and left_latin:
        return right, left
    if left_cjk > right_cjk and len(left) >= len(right) + 1:
        return left, right
    if right_cjk > left_cjk and len(right) >= len(left) + 1:
        return right, left
    return left, right


def _alias_fact(
    *,
    source_id: str,
    locator: str,
    canonical: str,
    alias: str,
    quote: str,
) -> dict[str, Any] | None:
    canonical = _clean_term(canonical)
    alias = _clean_term(alias)
    if not canonical or not alias or canonical == alias:
        return None
    if len(canonical) < 1 or len(alias) < 1:
        return None
    # Reject connector-only or modality fragments accidentally captured as terms.
    if re.fullmatch(r"(?:必须|应当|可以|不得|如果|当|后|时|则|和|与|或|及)", canonical):
        return None
    if re.fullmatch(r"(?:必须|应当|可以|不得|如果|当|后|时|则|和|与|或|及)", alias):
        return None
    canonical, alias = _prefer_canonical_alias(canonical, alias)
    return {
        "fact_id": _stable_id("fact", source_id, locator, "TERM_ALIAS", canonical, alias),
        "kind": "TERM_ALIAS",
        "language": _language_of(quote),
        "canonical_term": canonical,
        "alias": alias,
        "raw_statement": quote,
        "source_spans": [
            {
                "source_id": source_id,
                "locator": locator,
                "quote": quote,
                "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            }
        ],
        "confidence": 0.98,
        "status": "ACCEPTED",
        "ambiguities": [],
    }


def _extract_glossary_table_aliases(
    text: str, source_id: str, locator: str
) -> list[dict[str, Any]]:
    """Extract TERM_ALIAS rows from source-backed glossary/definition markdown tables."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    rows: list[list[str]] = []
    for line in lines:
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        return []
    header = rows[0]
    term_indexes = [index for index, cell in enumerate(header) if _GLOSSARY_TERM_HEADER_RE.search(cell)]
    alias_indexes = [index for index, cell in enumerate(header) if _GLOSSARY_ALIAS_HEADER_RE.search(cell)]
    if not term_indexes or not alias_indexes:
        return []
    # Prefer the first non-overlapping alias column.
    term_idx = term_indexes[0]
    alias_idx = next((index for index in alias_indexes if index != term_idx), None)
    if alias_idx is None:
        return []
    aliases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for cells in rows[1:]:
        if max(term_idx, alias_idx) >= len(cells):
            continue
        canonical = _clean_term(cells[term_idx])
        alias = _clean_term(cells[alias_idx])
        if not canonical or not alias:
            continue
        quote = f"{header[term_idx]}={canonical};{header[alias_idx]}={alias}"
        row = _alias_fact(
            source_id=source_id,
            locator=locator,
            canonical=canonical,
            alias=alias,
            quote=quote,
        )
        if not row:
            continue
        key = (row["canonical_term"], row["alias"])
        if key in seen:
            continue
        seen.add(key)
        aliases.append(row)
    return aliases


def _extract_aliases(text: str, source_id: str, locator: str) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in _ALIAS_PATTERNS:
        for match in pattern.finditer(text):
            quote = match.group(0)
            row = _alias_fact(
                source_id=source_id,
                locator=locator,
                canonical=match.group("canonical"),
                alias=match.group("alias"),
                quote=quote,
            )
            if not row:
                continue
            key = (row["canonical_term"], row["alias"])
            if key in seen:
                continue
            seen.add(key)
            aliases.append(row)
    return aliases


def _data_effects(
    text: str,
    *,
    primary_action: dict[str, Any] | None = None,
    modality: str = "ASSERTS",
) -> list[dict[str, Any]]:
    """Extract only independent outcome clauses, never the governed operation itself.

    The compatibility parser historically treated every action-looking token as a data
    effect. That turned prohibited operations (``不得删除``), role names (``创建人``),
    object qualifiers (``本人创建且尚未审批``), and formula nouns (``退款金额``) into
    executable child facts. An effect must now be source-backed by a distinct clause
    boundary or the explicit trigger-after-effect grammar already owned by this parser.
    """
    effects: list[dict[str, Any]] = []
    seen: set[str] = set()
    action = dict(primary_action or {})
    primary_raw = _text(action.get("raw"))
    primary_canonical = _text(action.get("canonical"))
    primary_start = text.find(primary_raw) if primary_raw else -1
    primary_end = primary_start + len(primary_raw) if primary_start >= 0 else -1
    trigger_effect = _TRIGGER_THEN_EFFECT_RE.search(text)
    trigger_effect_start = (
        trigger_effect.start("effect") if trigger_effect is not None else -1
    )

    for match in _DATA_EFFECT_RE.finditer(text):
        raw = match.group(0)
        action_raw = _text(match.group("action"))
        suffix = text[match.end("action") :]
        if _ACTION_NOUN_SUFFIX_RE.match(suffix):
            continue

        same_as_primary = bool(
            primary_raw
            and (
                action_raw == primary_raw
                or (primary_canonical and action_raw == primary_canonical)
            )
        )
        after_primary = primary_end >= 0 and match.start() >= primary_end
        between = text[primary_end : match.start()] if after_primary else ""
        independent_clause = bool(
            (trigger_effect_start >= 0 and match.start() >= trigger_effect_start)
            or _EFFECT_CLAUSE_BOUNDARY_RE.search(between)
        )
        if same_as_primary and not independent_clause:
            continue
        if after_primary and not independent_clause:
            continue
        if modality in {"MAY", "MUST", "MUST_NOT", "ONLY_IF"} and not independent_clause:
            continue

        entity = _clean_term(match.group("object"))
        if not entity or _ACTION_NOUN_SUFFIX_RE.match(entity):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        effects.append(
            {
                "statement": raw,
                "action": action_raw,
                "entity": entity,
                "source_backed": True,
                "independent_effect_clause": True,
            }
        )
    return effects


def _compensations(text: str) -> list[str]:
    rows: list[str] = []
    for match in _COMPENSATION_RE.finditer(text):
        value = _text(match.group("raw"))
        if value and value not in rows:
            rows.append(value)
    return rows


def _postconditions(text: str, *, data_effects: list[dict[str, Any]], compensations: list[str]) -> list[str]:
    rows: list[str] = []
    for match in _TRIGGER_THEN_EFFECT_RE.finditer(text):
        effect = _text(match.group("effect"))
        if effect and effect not in rows:
            rows.append(effect)
    for row in data_effects:
        statement = _text(row.get("statement"))
        if statement and statement not in rows:
            rows.append(statement)
    for value in compensations:
        if value not in rows:
            rows.append(value)
    return rows


def _find_mentions(text: str, known: Iterable[str], fallback: re.Pattern[str]) -> list[str]:
    known_values = [name for name in known if name]
    mentions = [name for name in known_values if name in text]
    if not mentions:
        # Suffix fallback exists for Chinese role/entity names whose governing
        # text drops the leading qualifier (e.g. 仓库管理员 mentioned as 管理员).
        # A 2-char suffix has no discriminative power: it matches arbitrary
        # substrings of unrelated tokens (merchant[-2:] == "nt" collides with
        # payable_amount), which promotes phantom actors into rule subjects and
        # silently reclassifies conservation/business rules as authorization.
        # Non-CJK suffixes additionally require word boundaries, because a
        # Latin suffix is not a meaningful term fragment the way 管理员 is.
        for width in (4, 3):
            suffix_matches: dict[str, list[str]] = {}
            for name in known_values:
                if len(name) >= width:
                    suffix_matches.setdefault(name[-width:], []).append(name)
            for suffix, candidates in suffix_matches.items():
                if len(candidates) != 1:
                    continue
                if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", suffix):
                    matched = suffix in text
                else:
                    matched = re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(suffix)}(?![A-Za-z0-9_])",
                        text,
                    ) is not None
                if matched:
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
            if _ACTION_NOUN_SUFFIX_RE.match(text[match.end() :]):
                continue
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
            if pattern is _FOLLOW_ON_CONDITION_RE and not _EXPLICIT_CONDITION_HEAD_RE.search(
                text[: match.start()]
            ):
                continue
            value = _text(match.group("value")).strip(" ，,")
            value = re.split(r"[则]", value, maxsplit=1)[0].strip(" ，,")
            if value and value not in values:
                values.append(value)
    state_condition = re.search(r"(?P<value>(?:已|未|待|处于)[^，,；;。]{1,32}?)(?=不得|不能|不可|只能|仅能|可以|允许|必须|应当)", text)
    if state_condition:
        value = _text(state_condition.group("value"))
        if value and value not in values:
            values.append(value)
    return values


def _split_condition_leaves(condition: str) -> tuple[list[str], str]:
    """Split one captured condition on explicit AND/OR markers only."""
    value = _text(condition)
    if not value:
        return [], ""
    has_and = bool(_AND_COMBINATOR_RE.search(value))
    has_or = bool(_OR_COMBINATOR_RE.search(value))
    if has_and and has_or:
        return [value], "UNRESOLVED"
    if has_and:
        parts = [
            part.strip(" ，,")
            for part in re.split(r"并且|同时满足|以及|(?<![并])且(?![不])", value)
            if part and part.strip(" ，,")
        ]
        if len(parts) >= 2:
            return parts, "AND"
        return [value], "UNRESOLVED"
    if has_or:
        parts = [
            part.strip(" ，,")
            for part in re.split(r"或者|或则|任一条件|其中之一", value)
            if part and part.strip(" ，,")
        ]
        if len(parts) >= 2:
            return parts, "OR"
        return [value], "UNRESOLVED"
    return [value], "SINGLE_CONDITION"


def _normalize_conditions(text: str, conditions: list[str]) -> tuple[list[str], str]:
    """Project multi-condition leaves with an explicit combinator; never default AND."""
    leaves: list[str] = []
    leaf_combinators: list[str] = []
    for condition in conditions:
        parts, combinator = _split_condition_leaves(condition)
        for part in parts:
            if part and part not in leaves:
                leaves.append(part)
        if combinator and combinator not in {"", "SINGLE_CONDITION"}:
            leaf_combinators.append(combinator)
    if len(leaves) <= 1:
        return leaves, "SINGLE_CONDITION" if leaves else ""
    has_and = bool(_AND_COMBINATOR_RE.search(text)) or "AND" in leaf_combinators
    has_or = bool(_OR_COMBINATOR_RE.search(text)) or "OR" in leaf_combinators
    if has_and and has_or:
        return leaves, "UNRESOLVED"
    if has_and:
        return leaves, "AND"
    if has_or:
        return leaves, "OR"
    return leaves, "UNRESOLVED"


def _condition_combinator(text: str, conditions: list[str]) -> str:
    """Derive multi-condition combinator only from explicit source wording."""
    _leaves, combinator = _normalize_conditions(text, conditions)
    return combinator


def _condition_frame(
    *,
    conditions: list[str],
    combinator: str,
    exception_scopes: list[str],
    branch_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project nested condition / exception structure with explicit combinators only."""
    branch_meta = dict(branch_meta or {})
    branch = _text(branch_meta.get("branch"))
    if _text(branch_meta.get("kind")) == "IF_THEN_ELSE" or branch in {
        "THEN",
        "ELSE",
        "ELSE_IF",
    }:
        kind = "IF_THEN_ELSE"
    elif exception_scopes:
        kind = "EXCEPT_OVERLAY"
    elif combinator == "AND":
        kind = "ALL"
    elif combinator == "OR":
        kind = "ANY"
    elif combinator == "UNRESOLVED":
        kind = "UNRESOLVED"
    elif conditions:
        kind = "LEAF"
    else:
        kind = ""
    overlays: list[dict[str, Any]] = []
    if exception_scopes:
        overlays.append(
            {
                "kind": "EXCEPT_OVERLAY",
                "exception_scopes": list(exception_scopes),
                "source_backed": True,
            }
        )
    branch_index = branch_meta.get("branch_index")
    if branch_index is None or (isinstance(branch_index, str) and not branch_index.strip()):
        branch_index_value: int | str = ""
    else:
        try:
            branch_index_value = int(branch_index)
        except (TypeError, ValueError):
            branch_index_value = ""
    frame = {
        "kind": kind,
        "combinator": combinator,
        "conditions": list(conditions),
        "exception_scopes": list(exception_scopes),
        "overlays": overlays,
        "branch": branch,
        "branch_index": branch_index_value,
        "parent_conditions": [
            _text(item) for item in _list(branch_meta.get("parent_conditions")) if _text(item)
        ],
        "paired_statement": _text(branch_meta.get("paired_statement")),
        "source_backed": True,
    }
    if not kind:
        return {}
    return frame


def _is_exception_overlay_clause(text: str) -> bool:
    stripped = re.sub(r"^(?:但|但是|不过|然而)", "", _text(text)).strip(" ，,")
    return bool(stripped and _EXCEPTION_OVERLAY_CLAUSE_RE.match(stripped))


def _incomplete_nested_conditional(text: str) -> bool:
    """True when a nested 若/如果 fragment lacks a complete 则…否则… frame."""
    raw = _text(text)
    if not raw or _IF_THEN_ELSE_RE.match(raw):
        return False
    return bool(_ELSE_IF_HEAD_RE.match(raw))


def _expand_conditional_units(
    unit: str,
    *,
    depth: int = 0,
    chain_root: str = "",
    parent_conditions: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Expand 若…则…否则… and 否则若… chains into explicit THEN/ELSE_IF/ELSE frames.

    Underdetermined nesting stays visible — never invent branches or default AND.
    """
    raw = _text(unit)
    ancestry = [_text(item) for item in (parent_conditions or []) if _text(item)]
    if not raw:
        return []
    match = _IF_THEN_ELSE_RE.match(raw)
    if not match:
        if depth > 0 and _incomplete_nested_conditional(raw):
            return [
                (
                    raw,
                    {
                        "kind": "IF_THEN_ELSE",
                        "branch": "",
                        "branch_index": depth,
                        "paired_statement": chain_root or raw,
                        "parent_conditions": list(ancestry),
                        "underdetermined": True,
                        "underdetermined_reason": "NESTED_BRANCH_UNDERDETERMINED",
                        "source_backed": True,
                    },
                )
            ]
        return [(raw, {})]
    prefix = _text(match.group("prefix"))
    cond = _text(match.group("cond"))
    then_body = _text(match.group("then"))
    else_body = _text(match.group("else"))
    root = chain_root or raw
    if not cond or not then_body or not else_body:
        return [
            (
                raw,
                {
                    "kind": "IF_THEN_ELSE",
                    "branch": "",
                    "branch_index": depth,
                    "paired_statement": root,
                    "parent_conditions": list(ancestry),
                    "underdetermined": True,
                    "underdetermined_reason": "IF_THEN_ELSE_UNDERDETERMINED",
                    "source_backed": True,
                },
            )
        ]
    cond_prefix = f"{prefix}若{cond}，则"
    frame_base = {
        "kind": "IF_THEN_ELSE",
        "paired_statement": root,
        "condition_text": cond,
        "parent_conditions": list(ancestry),
        "source_backed": True,
    }
    then_branch = "THEN" if depth == 0 else "ELSE_IF"
    rows: list[tuple[str, dict[str, Any]]] = [
        (
            f"{cond_prefix}{then_body}",
            {**frame_base, "branch": then_branch, "branch_index": depth},
        )
    ]
    nested_ancestry = [*ancestry, cond]
    else_rows = _expand_conditional_units(
        else_body,
        depth=depth + 1,
        chain_root=root,
        parent_conditions=nested_ancestry,
    )
    nested_framed = any(
        _text(_dict(meta).get("kind")) == "IF_THEN_ELSE" for _body, meta in else_rows
    )
    if nested_framed:
        for branch_unit, meta in else_rows:
            remapped = dict(meta or {})
            remapped["kind"] = "IF_THEN_ELSE"
            remapped["paired_statement"] = root
            remapped["source_backed"] = True
            remapped.setdefault("parent_conditions", list(nested_ancestry))
            if remapped.get("underdetermined"):
                rows.append((branch_unit, remapped))
                continue
            branch = _text(remapped.get("branch"))
            if branch == "THEN":
                remapped["branch"] = "ELSE_IF"
            elif branch not in {"ELSE", "ELSE_IF"}:
                remapped["branch"] = "ELSE_IF" if _ELSE_IF_HEAD_RE.match(_text(branch_unit)) else "ELSE"
            rows.append((branch_unit, remapped))
        # Normalize sequential branch_index from the chain root — never invent gaps.
        for index, (branch_unit, meta) in enumerate(rows):
            meta = dict(meta)
            meta["branch_index"] = index
            rows[index] = (branch_unit, meta)
        return rows
    rows.append(
        (
            f"{cond_prefix}{else_body}",
            {
                **frame_base,
                "branch": "ELSE",
                "branch_index": depth + 1,
                "parent_conditions": list(ancestry),
            },
        )
    )
    return rows


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
    rows: list[str] = []
    for match in _EXCEPTION_RE.finditer(text):
        quote = _text(match.group(0))
        if quote and quote not in rows:
            rows.append(quote)
    return rows


def _exception_scopes(
    text: str,
    exceptions: list[str],
    *,
    known_roles: list[str] | None = None,
) -> list[str]:
    """Extract explicit exception actors/scopes from source wording only."""
    scopes: list[str] = []
    corpus_parts = [*exceptions, text]
    known = [_text(name) for name in (known_roles or []) if _text(name)]
    for role in known:
        for row in corpus_parts:
            if f"除{role}外" in row or f"{role}除外" in row:
                if role not in scopes:
                    scopes.append(role)
    if scopes:
        return scopes
    for row in corpus_parts:
        for match in re.finditer(r"除([^，,；;。外]{1,24})外", row):
            value = _text(match.group(1))
            if value and value not in scopes:
                scopes.append(value)
        # Prefer the shortest rightmost role-like token before 除外 — never the whole clause.
        short_matches = list(re.finditer(r"([\u4e00-\u9fffA-Za-z0-9_-]{2,6})除外", row))
        if short_matches:
            value = _text(short_matches[-1].group(1))
            if value and value not in scopes:
                scopes.append(value)
    return scopes


def _strip_deictic_placeholder_entities(
    text: str, entities: list[str], known_entities: list[str]
) -> list[str]:
    """Treat 该单据/本记录 as pronouns when the generic token is not a known object."""
    known = set(known_entities)
    placeholders = {
        _text(match.group("generic"))
        for match in _DEICTIC_GENERIC_RE.finditer(text)
        if _text(match.group("generic")) not in known
    }
    if not placeholders:
        return entities
    return [name for name in entities if name not in placeholders]


def _scope(text: str) -> dict[str, str]:
    ownership_match = _OWNERSHIP_SCOPE_RE.search(text)
    organization_match = _ORG_SCOPE_RE.search(text)
    return {
        "tenant": "当前租户" if "租户" in text else "",
        "organization": organization_match.group(0) if organization_match else "",
        "ownership": ownership_match.group(0) if ownership_match else "",
        "data_scope": organization_match.group(0) if organization_match else "",
    }


def _quantity_constraints(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _QUANTITY_RE.finditer(text):
        raw = match.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        rows.append(
            {
                "raw": raw,
                "operator": match.group("op"),
                "value": match.group("value"),
                "unit": _text(match.group("unit")),
                "source_backed": True,
            }
        )
    return rows


def _time_window_constraints(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _TIME_WINDOW_RE.finditer(text):
        raw = _text(match.group("raw"))
        if not raw or raw in seen:
            continue
        seen.add(raw)
        rows.append(
            {
                "raw": raw,
                "anchor": _text(match.group("anchor")),
                "relation": _text(match.group("rel")),
                "duration": _text(match.group("duration")),
                "source_backed": True,
            }
        )
    return rows


def _formula_constraints(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _FORMULA_RE.finditer(text):
        raw = match.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        rows.append(
            {
                "raw": raw,
                "lhs": _text(match.group("lhs")),
                "rhs": _text(match.group("rhs")),
                "source_backed": True,
            }
        )
    return rows


def _authorization_delegation(text: str, known_roles: list[str]) -> dict[str, Any]:
    match = _DELEGATION_RE.search(text)
    if not match:
        return {}
    delegator = _text(match.group("delegator"))
    delegatee = _text(match.group("delegatee"))
    # Prefer known role names contained in the matched spans; never invent roles.
    known = [name for name in known_roles if name]
    for name in known:
        if name in delegator:
            delegator = name
            break
    for name in known:
        if name in delegatee:
            delegatee = name
            break
    if not delegator or not delegatee or delegator == delegatee:
        return {}
    return {
        "raw": match.group(0),
        "delegator": delegator,
        "delegatee": delegatee,
        "source_backed": True,
    }


def _canonicalize_names(names: Iterable[str], alias_map: dict[str, str] | None = None) -> list[str]:
    """Collapse alias→canonical identities; preserve order of first occurrence."""
    alias_map = alias_map or {}
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        canonical = _text(alias_map.get(_text(name), name))
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def _split_rule_units(text: str) -> list[str]:
    units = [unit.strip() for unit in re.split(r"[。！？!?；;]+", text) if unit.strip()]
    result: list[str] = []
    for unit in units:
        contrast = re.search(r"[，,](?:但|但是|不过|然而)", unit)
        if contrast and contrast.start() > 0:
            left = unit[: contrast.start()].strip(" ，,")
            right = unit[contrast.start() + 1 :].strip()
            # Exception overlays cover the preceding main rule — never orphan-drop them.
            if right and _is_exception_overlay_clause(right):
                result.append(unit)
            else:
                if left:
                    result.append(left)
                if right:
                    result.append(right)
        else:
            result.append(unit)
    return result


def _pending_branch_fact(
    unit: str,
    *,
    source_id: str,
    locator: str,
    branch_meta: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Emit a visible unresolved branch instead of silently dropping IF/ELSE structure."""
    raw = _text(unit)
    quote_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    branch_index = branch_meta.get("branch_index")
    if branch_index is None or (isinstance(branch_index, str) and not branch_index.strip()):
        branch_index_value: int | str = ""
    else:
        try:
            branch_index_value = int(branch_index)
        except (TypeError, ValueError):
            branch_index_value = ""
    return {
        "fact_id": _stable_id("fact", source_id, locator, "RULE", raw, reason, branch_meta.get("branch")),
        "kind": "RULE",
        "language": _language_of(raw),
        "subject": {"actor_refs": [], "entity_refs": [], "resolution_evidence": []},
        "conditions": [],
        "condition_combinator": "UNRESOLVED",
        "condition_frame": {
            "kind": "IF_THEN_ELSE",
            "combinator": "UNRESOLVED",
            "conditions": [],
            "exception_scopes": [],
            "overlays": [],
            "branch": _text(branch_meta.get("branch")),
            "branch_index": branch_index_value,
            "parent_conditions": [
                _text(item) for item in _list(branch_meta.get("parent_conditions")) if _text(item)
            ],
            "paired_statement": _text(branch_meta.get("paired_statement") or raw),
            "source_backed": True,
        },
        "trigger": {},
        "action": {},
        "object": {"entity_refs": []},
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
        "modality": "ASSERTS",
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": [],
        "state_effects": [],
        "data_effects": [],
        "temporal_constraints": [],
        "quantity_constraints": [],
        "time_window_constraints": [],
        "formula_constraints": [],
        "authorization_delegation": {},
        "compensation": [],
        "compensations": [],
        "raw_statement": raw,
        "normalized_statement": re.sub(r"\s+", "", raw),
        "source_spans": [
            {"source_id": source_id, "locator": locator, "quote": raw, "quote_hash": quote_hash}
        ],
        "confidence": 0.2,
        "status": "PENDING",
        "ambiguities": [reason],
        "critical": True,
    }


def _resolve_reference(
    text: str,
    explicit_entities: list[str],
    context_entities: list[str],
    *,
    alias_map: dict[str, str] | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Resolve pronouns only when same-section context collapses to one identity.

    TERM_ALIAS evidence may collapse alias/canonical to one identity. Ambiguous or
    empty context stays PENDING — never invent from industry knowledge.
    """
    resolved = list(explicit_entities)
    evidence: list[dict[str, Any]] = []
    ambiguities: list[str] = []
    mention = _COREFERENCE_RE.search(text)
    if mention and not explicit_entities:
        unique_context = _canonicalize_names(context_entities[-3:], alias_map)
        if len(unique_context) == 1:
            resolved.append(unique_context[0])
            evidence.append(
                {
                    "mention": mention.group(0),
                    "resolved_ref": unique_context[0],
                    "method": "nearest_unambiguous_entity_context",
                    "confidence": 0.78,
                    "section_scoped": True,
                    "alias_aware": bool(alias_map),
                }
            )
        elif len(unique_context) > 1:
            ambiguities.append("COREFERENCE_AMBIGUOUS:" + ",".join(unique_context))
        else:
            ambiguities.append("COREFERENCE_UNRESOLVED")
    return sorted(set(resolved)), evidence, ambiguities


def _fact_from_unit(
    unit: str,
    *,
    source_id: str,
    locator: str,
    known_entities: list[str],
    known_roles: list[str],
    context_entities: list[str],
    context_roles: list[str],
    alias_map: dict[str, str] | None = None,
    branch_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    raw = unit.strip()
    if not raw or not _RULE_SIGNAL_RE.search(raw):
        return None
    branch_meta = dict(branch_meta or {})
    if branch_meta.get("underdetermined"):
        reason = _text(branch_meta.get("underdetermined_reason")) or "IF_THEN_ELSE_UNDERDETERMINED"
        return {
            **_pending_branch_fact(
                raw,
                source_id=source_id,
                locator=locator,
                branch_meta=branch_meta,
                reason=reason,
            ),
            "ambiguities": [reason],
        }
    entities = _find_mentions(raw, known_entities, _ENTITY_SUFFIX_RE)
    entities = _strip_deictic_placeholder_entities(raw, entities, known_entities)
    roles = _find_mentions(raw, known_roles, _ROLE_SUFFIX_RE)
    entities, resolution_evidence, ambiguities = _resolve_reference(
        raw, entities, context_entities, alias_map=alias_map
    )
    entities = _canonicalize_names(entities, alias_map)
    if not roles and re.search(r"自动|系统", raw):
        roles = ["系统"]
    elif not roles and context_roles and _OMITTED_ACTOR_RE.search(raw):
        unique_roles = list(dict.fromkeys(_text(name) for name in context_roles[-3:] if _text(name)))
        if len(unique_roles) == 1:
            roles = [unique_roles[0]]
            resolution_evidence.append(
                {
                    "mention": "省略Actor",
                    "resolved_ref": unique_roles[0],
                    "method": "nearest_unambiguous_actor_context",
                    "confidence": 0.72,
                    "section_scoped": True,
                }
            )
        elif len(unique_roles) > 1:
            ambiguities.append("OMITTED_ACTOR_AMBIGUOUS:" + ",".join(unique_roles))

    action = _action(raw)
    modality, polarity = _modality(raw)
    conditions, condition_combinator = _normalize_conditions(raw, _conditions(raw))
    exceptions = _exception(raw)
    exception_scopes = _exception_scopes(raw, exceptions, known_roles=known_roles)
    # Exception scopes are overlays, not primary actors of the governing rule.
    if exception_scopes:
        roles = [role for role in roles if role not in exception_scopes]
    states = _state_effects(raw)
    temporal = [match.group("value") for match in _TEMPORAL_RE.finditer(raw)]
    quantity_constraints = _quantity_constraints(raw)
    time_window_constraints = _time_window_constraints(raw)
    formula_constraints = _formula_constraints(raw)
    delegation = _authorization_delegation(raw, known_roles)
    data_effects = _data_effects(
        raw,
        primary_action=action,
        modality=modality,
    )
    compensation = _compensations(raw)
    postconditions = _postconditions(raw, data_effects=data_effects, compensations=compensation)
    scope = _scope(raw)

    # When source states "trigger后 effect", prefer the trigger action as the operation
    # and keep the effect in postcondition / data_effect / compensation slots.
    trigger_effect = _TRIGGER_THEN_EFFECT_RE.search(raw)
    if trigger_effect:
        trigger_text = _text(trigger_effect.group("trigger"))
        effect_text = _text(trigger_effect.group("effect"))
        temporal_condition = trigger_text
        if temporal_condition and not temporal_condition.endswith(("之前", "之后", "以前", "以后", "前", "后")):
            temporal_condition = f"{temporal_condition}后"
        if temporal_condition and not any(
            temporal_condition == item
            or temporal_condition in item
            or item in temporal_condition
            for item in conditions
        ):
            conditions = [temporal_condition, *conditions]
            conditions, condition_combinator = _normalize_conditions(raw, conditions)
        trigger_action = _action(trigger_text)
        if trigger_action:
            action = trigger_action
        if effect_text and effect_text not in postconditions:
            postconditions = [effect_text, *postconditions]
        if not data_effects:
            data_effects = _data_effects(
                raw,
                primary_action=action,
                modality=modality,
            )
        if not compensation:
            compensation = _compensations(effect_text)

    if not action and not states and modality == "ASSERTS" and not formula_constraints and not postconditions:
        return None

    critical = bool(_CRITICAL_SIGNAL_RE.search(raw))
    if critical and not entities and re.search(r"该|本|其|上述|前述|对应|相关", raw) and "COREFERENCE_UNRESOLVED" not in ambiguities:
        ambiguities.append("BUSINESS_SUBJECT_UNRESOLVED")
    if modality in {"MUST_NOT", "ONLY_IF"} and not action and not states:
        ambiguities.append("CRITICAL_ACTION_UNRESOLVED")
    if exceptions and len(_split_rule_units(raw)) == 1 and not conditions and not re.match(r"^(?:除[^，,；;。]+外|但|但是|不过|然而)", raw):
        if exception_scopes:
            # Explicitly named exception actor/scopes — promote all; never invent.
            for scope in exception_scopes:
                resolution_evidence.append(
                    {
                        "mention": "例外范围",
                        "resolved_ref": scope,
                        "method": "explicit_exception_scope_in_source",
                        "confidence": 0.9,
                        "source_backed": True,
                    }
                )
        else:
            ambiguities.append("EXCEPTION_SCOPE_UNRESOLVED")
    if len(conditions) > 1 and condition_combinator == "UNRESOLVED":
        ambiguities.append("CONDITION_COMBINATOR_UNRESOLVED")

    condition_frame = _condition_frame(
        conditions=conditions,
        combinator=condition_combinator,
        exception_scopes=exception_scopes,
        branch_meta=branch_meta,
    )

    confidence = 0.62 + (0.08 if entities else 0.0) + (0.06 if roles else 0.0) + (0.10 if action else 0.0) + (0.07 if conditions else 0.0) + (0.04 if states else 0.0) + (0.03 if modality != "ASSERTS" else 0.0) + (0.03 if postconditions or compensation else 0.0) - 0.12 * len(ambiguities)
    confidence = max(0.05, min(0.99, confidence))
    status = "PENDING" if ambiguities else "ACCEPTED"
    kind = "STATE_TRANSITION" if states else "RULE"
    quote_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    fact_id = _stable_id("fact", source_id, locator, kind, raw, branch_meta.get("branch"))
    return {
        "fact_id": fact_id,
        "kind": kind,
        "language": _language_of(raw),
        "subject": {"actor_refs": roles, "entity_refs": entities, "resolution_evidence": resolution_evidence},
        "conditions": conditions,
        "condition_combinator": condition_combinator,
        "condition_frame": condition_frame,
        "trigger": {"raw": conditions[0]} if conditions else {},
        "action": action,
        "object": {"entity_refs": entities},
        "scope": scope,
        "modality": modality,
        "polarity": polarity,
        "exceptions": exceptions,
        "exception_scope": exception_scopes,
        "postconditions": postconditions,
        "state_effects": states,
        "data_effects": data_effects,
        "temporal_constraints": temporal,
        "quantity_constraints": quantity_constraints,
        "time_window_constraints": time_window_constraints,
        "formula_constraints": formula_constraints,
        "authorization_delegation": delegation,
        "compensation": compensation,
        "compensations": compensation,
        "raw_statement": raw,
        "normalized_statement": re.sub(r"\s+", "", raw),
        "source_spans": [{"source_id": source_id, "locator": locator, "quote": raw, "quote_hash": quote_hash}],
        "confidence": round(confidence, 4),
        "status": status,
        "ambiguities": ambiguities,
        "critical": critical,
    }


def _interface_prose_fields(interface: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (span_kind, field_name, prose) for OpenAPI Chinese attachment.

    Path / operationId are never returned — vocabulary alone is not business text.
    """
    summary = _text(
        interface.get("openapi_summary")
        if "openapi_summary" in interface
        else interface.get("summary")
    )
    description = _text(
        interface.get("openapi_description")
        if "openapi_description" in interface
        else interface.get("description")
    )
    # When parser only coalesced description into summary, avoid double-processing.
    rows: list[tuple[str, str, str]] = []
    if summary:
        rows.append(("OPENAPI_OPERATION_SUMMARY", "summary", summary))
    if description and description != summary:
        rows.append(("OPENAPI_OPERATION_DESCRIPTION", "description", description))
    return rows


def _asset_source_backed_alias_map(asset: dict[str, Any]) -> dict[str, str]:
    """Collect unambiguous ACCEPTED TERM_ALIAS maps already on the asset.

    Never invent aliases from path vocabulary. Conflicting alias→canonical pairs
    are omitted until an operator SELECT resolves them.
    """
    alias_to_canonical: dict[str, str] = {}
    conflicting: set[str] = set()
    sources: list[dict[str, Any]] = []
    sources.extend(
        row
        for row in _list(_dict(asset.get("business_fact_ledger")).get("items"))
        if isinstance(row, dict)
    )
    sources.extend(
        row
        for row in _list(_dict(asset.get("chinese_business_glossary")).get("items"))
        if isinstance(row, dict)
    )
    for fact in sources:
        if _text(fact.get("kind")) != "TERM_ALIAS":
            continue
        if _text(fact.get("status")) not in {"", "ACCEPTED"}:
            continue
        alias = _text(fact.get("alias"))
        canonical = _text(fact.get("canonical_term"))
        if not alias or not canonical or alias == canonical:
            continue
        existing = alias_to_canonical.get(alias)
        if existing and existing != canonical:
            conflicting.add(alias)
            continue
        alias_to_canonical[alias] = canonical
    for alias in conflicting:
        alias_to_canonical.pop(alias, None)
    return alias_to_canonical


def project_openapi_interface_chinese_spans(
    asset: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project Chinese OpenAPI summary/description onto interface-attached spans.

    Fail-closed contract:
    - Only Chinese (or Chinese-mixed) prose is considered.
    - Facts come only from existing rule-signal extraction; path vocabulary alone
      never invents a business rule.
    - Ambiguous extraction stays PENDING / coverage AMBIGUOUS or UNRESOLVED.
    - Within one interface, summary then description units thread entity/role
      context so description-only multi-unit prose can bind without invention.
    """
    known_entities = _known_names(asset, "entity")
    known_roles = _known_names(asset, "role")
    alias_map = _asset_source_backed_alias_map(asset)
    coverage: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for interface in _list(asset.get("interfaces")):
        if not isinstance(interface, dict):
            continue
        source_kind = _text(interface.get("source_kind")).lower()
        has_openapi_prose_fields = any(
            key in interface
            for key in ("openapi_summary", "openapi_description")
        )
        if source_kind not in {"openapi", "swagger"} and not has_openapi_prose_fields:
            continue
        interface_id = _text(interface.get("interface_id") or interface.get("operation_id"))
        source_id = _text(interface.get("source_id"))
        if not interface_id:
            continue
        # Interface-scoped context: summary units seed description units.
        context_entities: list[str] = []
        context_roles: list[str] = []
        for span_kind, field_name, prose in _interface_prose_fields(interface):
            language = _language_of(prose)
            locator = (
                f"{source_id}#interface={interface_id}/{field_name}"
                if source_id
                else f"interface={interface_id}/{field_name}"
            )
            chunk_id = _stable_id("chunk", source_id, interface_id, field_name, prose)
            chunk_facts: list[dict[str, Any]] = []
            chunk_ambiguities: list[str] = []
            contains_business_signal = bool(_RULE_SIGNAL_RE.search(prose))
            if not source_id:
                chunk_ambiguities.append("SOURCE_ID_MISSING")
            elif language in {"zh-CN", "zh-CN-mixed"} and contains_business_signal:
                for unit in _split_rule_units(prose):
                    for branch_unit, branch_meta in _expand_conditional_units(unit):
                        fact = _fact_from_unit(
                            branch_unit,
                            source_id=source_id,
                            locator=locator,
                            known_entities=known_entities,
                            known_roles=known_roles,
                            context_entities=context_entities,
                            context_roles=context_roles,
                            alias_map=alias_map,
                            branch_meta=branch_meta,
                        )
                        if not fact:
                            continue
                        spans = [
                            dict(row)
                            for row in _list(fact.get("source_spans"))
                            if isinstance(row, dict)
                        ]
                        if spans:
                            spans[0]["interface_id"] = interface_id
                            spans[0]["span_kind"] = span_kind
                            spans[0]["attachment"] = "openapi_interface_prose"
                        else:
                            spans = [
                                {
                                    "source_id": source_id,
                                    "locator": locator,
                                    "quote": _text(fact.get("raw_statement") or prose),
                                    "quote_hash": hashlib.sha256(
                                        prose.encode("utf-8")
                                    ).hexdigest(),
                                    "interface_id": interface_id,
                                    "span_kind": span_kind,
                                    "attachment": "openapi_interface_prose",
                                }
                            ]
                        fact["source_spans"] = spans
                        fact["interface_id"] = interface_id
                        fact["interface_span_kind"] = span_kind
                        fact["derivation"] = "openapi_interface_chinese_span"
                        chunk_facts.append(fact)
                        chunk_ambiguities.extend(_list(fact.get("ambiguities")))
                        for entity in _list(_dict(fact.get("subject")).get("entity_refs")):
                            if entity:
                                context_entities.append(_text(entity))
                        for role in _list(_dict(fact.get("subject")).get("actor_refs")):
                            if role and role != "系统":
                                context_roles.append(_text(role))
            if not source_id:
                status = "SOURCE_ID_MISSING"
            elif language not in {"zh-CN", "zh-CN-mixed"}:
                status = "TERMINAL_NON_CHINESE"
            elif contains_business_signal and not chunk_facts:
                status = "UNRESOLVED_BUSINESS_TEXT"
                chunk_ambiguities.append("BUSINESS_FACT_NOT_EXTRACTED")
            elif any(_text(row.get("status")) == "PENDING" for row in chunk_facts):
                status = "AMBIGUOUS"
            elif chunk_facts:
                status = "UNDERSTOOD"
            else:
                # Chinese prose present but no rule signal — context only, never invent.
                # Empty/non-rule summary still allows later description units to bind.
                status = "UNDERSTOOD_CONTEXT"
            coverage.append(
                {
                    "chunk_id": chunk_id,
                    "source_id": source_id,
                    "source_locator": locator,
                    "interface_id": interface_id,
                    "span_kind": span_kind,
                    "language": language,
                    "status": status,
                    "contains_business_signal": contains_business_signal,
                    "fact_ids": [_text(row.get("fact_id")) for row in chunk_facts],
                    "ambiguities": sorted({_text(v) for v in chunk_ambiguities if _text(v)}),
                    "quote": prose[:500],
                    "attachment": "openapi_interface_prose",
                }
            )
            facts.extend(chunk_facts)
    return coverage, facts


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
    alias_map: dict[str, str] = {}
    current_section = ""

    # Glossary/definition tables are line-oriented; extract from the full source so
    # paragraph chunking cannot silently drop TERM_ALIAS rows.
    document_locator = f"{filename or source_id}#document"
    for row in _extract_glossary_table_aliases(text, source_id, document_locator):
        glossary.append(row)
        alias = _text(row.get("alias"))
        canonical = _text(row.get("canonical_term"))
        if alias and canonical and alias not in alias_map:
            alias_map[alias] = canonical

    for chunk in _paragraph_chunks(text):
        chunk_text = _text(chunk.get("text"))
        section = _text(chunk.get("section")) or "document"
        # Section boundary resets local 指代/省略 context. Prior-section objects must
        # never leak; document-context stage may still resolve via unique heading.
        if section != current_section:
            context_entities = []
            context_roles = []
            current_section = section
        locator = f"{filename or source_id}#section={section};chars={chunk.get('start')}-{chunk.get('end')}"
        chunk_id = _stable_id("chunk", source_id, locator, chunk_text)
        chunk_fact_ids: list[str] = []
        chunk_ambiguities: list[str] = []
        chunk_facts: list[dict[str, Any]] = []
        glossary_rows = _extract_aliases(chunk_text, source_id, locator)
        for row in glossary_rows:
            key = (_text(row.get("canonical_term")), _text(row.get("alias")))
            if any(
                (_text(existing.get("canonical_term")), _text(existing.get("alias"))) == key
                for existing in glossary
            ):
                continue
            glossary.append(row)
            alias = _text(row.get("alias"))
            canonical = _text(row.get("canonical_term"))
            if alias and canonical and alias not in alias_map:
                alias_map[alias] = canonical
        chunk_fact_ids.extend(row["fact_id"] for row in glossary_rows)

        if chunk.get("kind") != "heading":
            for unit in _split_rule_units(chunk_text):
                branch_facts: list[dict[str, Any]] = []
                for branch_unit, branch_meta in _expand_conditional_units(unit):
                    fact = _fact_from_unit(
                        branch_unit,
                        source_id=source_id,
                        locator=locator,
                        known_entities=known_entities,
                        known_roles=known_roles,
                        context_entities=context_entities,
                        context_roles=context_roles,
                        alias_map=alias_map,
                        branch_meta=branch_meta,
                    )
                    if fact is None and branch_meta:
                        fact = _pending_branch_fact(
                            branch_unit,
                            source_id=source_id,
                            locator=locator,
                            branch_meta=branch_meta,
                            reason="BRANCH_FACT_UNRESOLVED",
                        )
                    if not fact:
                        continue
                    branch_facts.append(fact)
                # IF_THEN_ELSE ELSE/ELSE_IF may omit actor/object — inherit only from the
                # paired THEN fact in the same source frame (not proximity coreference).
                then_fact = next(
                    (
                        row
                        for row in branch_facts
                        if _text(_dict(row.get("condition_frame")).get("branch")) == "THEN"
                    ),
                    None,
                )
                if then_fact is not None:
                    then_subject = _dict(then_fact.get("subject"))
                    then_entities = _list(then_subject.get("entity_refs"))
                    then_actors = _list(then_subject.get("actor_refs"))
                    then_action = _dict(then_fact.get("action"))
                    for row in branch_facts:
                        branch = _text(_dict(row.get("condition_frame")).get("branch"))
                        if branch not in {"ELSE", "ELSE_IF"}:
                            continue
                        subject = _dict(row.get("subject"))
                        if not _list(subject.get("entity_refs")) and then_entities:
                            subject["entity_refs"] = list(then_entities)
                            row["subject"] = subject
                            row["object"] = {"entity_refs": list(then_entities)}
                            evidence = _list(subject.get("resolution_evidence"))
                            evidence.append(
                                {
                                    "mention": f"{branch}分支省略对象",
                                    "resolved_ref": then_entities[0],
                                    "method": "if_then_else_frame_inheritance",
                                    "confidence": 0.88,
                                    "source_backed": True,
                                }
                            )
                            subject["resolution_evidence"] = evidence
                        if not _list(subject.get("actor_refs")) and then_actors:
                            subject["actor_refs"] = list(then_actors)
                            row["subject"] = subject
                            evidence = _list(subject.get("resolution_evidence"))
                            evidence.append(
                                {
                                    "mention": f"{branch}分支省略Actor",
                                    "resolved_ref": then_actors[0],
                                    "method": "if_then_else_frame_inheritance",
                                    "confidence": 0.88,
                                    "source_backed": True,
                                }
                            )
                            subject["resolution_evidence"] = evidence
                        if not _dict(row.get("action")) and then_action:
                            row["action"] = dict(then_action)
                for fact in branch_facts:
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
            "section": section,
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
    spans = [row for row in _list(fact.get("source_spans")) if isinstance(row, dict)]
    # Prefer the structure-aligned span so rule evidence does not silently keep only
    # the coarse text locator after Document IR attachment.
    preferred_span = next(
        (
            row
            for row in spans
            if _text(row.get("document_block_id"))
            or _text(row.get("derivation")) == "document_ir_exact_statement_alignment"
        ),
        None,
    )
    span = preferred_span or (_dict(spans[0]) if spans else {})
    attachment = _dict(fact.get("structural_span_attachment"))
    alignment = _dict(fact.get("document_structure_alignment"))
    modality = _text(fact.get("modality"))
    if _list(fact.get("state_effects")):
        risk_type = "state_transition"
    elif _list(subject.get("actor_refs")) or any(_text(value) for value in _dict(fact.get("scope")).values()):
        risk_type = "authorization"
    else:
        risk_type = "business_logic"
    rule = {
        "rule_id": f"zh_business:{_text(fact.get('fact_id')).split(':')[-1]}",
        "source_id": span.get("source_id"),
        "source_locator": span.get("locator")
        or attachment.get("source_locator")
        or alignment.get("source_locator"),
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
        "condition_frame": _dict(fact.get("condition_frame")),
        "exceptions": _list(fact.get("exceptions")),
        "exception_scope": _list(fact.get("exception_scope")),
        "postconditions": _list(fact.get("postconditions")),
        "state_effects": _list(fact.get("state_effects")),
        "data_effects": _list(fact.get("data_effects")),
        "compensation": _list(fact.get("compensation") or fact.get("compensations")),
        "quantity_constraints": _list(fact.get("quantity_constraints")),
        "time_window_constraints": _list(fact.get("time_window_constraints")),
        "formula_constraints": _list(fact.get("formula_constraints")),
        "authorization_delegation": _dict(fact.get("authorization_delegation")),
        "confidence": fact.get("confidence"),
        "derivation": "chinese_first_business_comprehension",
        "document_block_id": _text(
            span.get("document_block_id")
            or attachment.get("document_block_id")
            or alignment.get("block_id")
        ),
        "structural_span_attachment": attachment,
    }
    return rule


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

    # Attach Chinese OpenAPI summary/description as interface-scoped source spans.
    # Never invent rules from path / operationId vocabulary alone.
    openapi_coverage, openapi_facts = project_openapi_interface_chinese_spans(asset)
    all_coverage.extend(openapi_coverage)
    all_facts.extend(openapi_facts)

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
        "section_scoped_coreference_only": True,
        "omitted_actor_requires_unique_section_context": True,
        "exception_scope_requires_explicit_source_actor": True,
        "structured_quantity_time_formula_source_backed_only": True,
        "openapi_interface_chinese_span_attachment": True,
        "openapi_path_vocabulary_not_business_fact_authority": True,
    })
    asset["governance"] = governance
    receipt = {
        "schema": "qualibug.openapi-interface-span-attachment.v1",
        "attached_interface_prose_chunk_count": len(openapi_coverage),
        "attached_interface_prose_fact_count": len(openapi_facts),
        "chinese_interface_prose_chunk_count": len(
            [
                row
                for row in openapi_coverage
                if _text(row.get("language")) in {"zh-CN", "zh-CN-mixed"}
            ]
        ),
        "automatic_inference_from_path_vocabulary_allowed": False,
    }
    asset["openapi_interface_span_attachment_receipt"] = receipt
    summary["openapi_interface_prose_chunk_count"] = receipt["attached_interface_prose_chunk_count"]
    summary["openapi_interface_prose_fact_count"] = receipt["attached_interface_prose_fact_count"]
    asset["summary"] = summary
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
    "project_openapi_interface_chinese_spans",
]
