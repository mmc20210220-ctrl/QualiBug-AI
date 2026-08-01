"""Normalize explicit Chinese fact coordinates at the existing compiler boundary.

The compatibility extractor intentionally remains broad so old enterprise fixtures keep
working. Structure-first compilation turns those rows into the formal typed ledger.
This boundary normalizes only coordinates already stated in the same source span. It
never discovers a new fact, chooses between conflicting statements, or creates a
second ledger.

The central rule is governed-operation binding: role and object coordinates belong to
the operation governed by the modality/trigger, not to every noun or verb in the whole
sentence. Formal relation/cardinality/formula facts already carry compiler-owned
subject/object coordinates and therefore bypass this compatibility normalization.

Two source-grammar contracts are also closed here because the compatibility parser may
split them before structure compilation: actor-exclusive permissions with qualified
objects, and one unambiguous IF/ELSE pair sharing the same exact source locator. Pairing
only annotates existing facts; it never creates a missing branch or chooses among
multiple candidates.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from .structured_fact_compiler import _semantic_signature

RECEIPT_SCHEMA = "qualibug.explicit-fact-semantic-normalization.v1"
_FORMAL_TYPED_COORDINATES = frozenset(
    {"OBJECT_RELATION", "CARDINALITY_CONSTRAINT", "DERIVED_VALUE", "DATA_EFFECT"}
)

_ROLE_HEADS = tuple(
    sorted(
        {
            "仓库管理员",
            "财务人员",
            "管理员",
            "操作员",
            "审批人",
            "审核人",
            "申请人",
            "发起人",
            "负责人",
            "经办人",
            "创建人",
            "经理",
            "主管",
            "用户",
            "人员",
            "员工",
            "财务",
            "会计",
            "出纳",
            "客服",
            "仓管员",
            "仓库员",
        },
        key=lambda value: (-len(value), value),
    )
)
_ENTITY_HEADS = tuple(
    sorted(
        {
            "采购订单",
            "订单明细",
            "订单头",
            "结算单",
            "出库单",
            "入库单",
            "申请单",
            "任务单",
            "退款金额",
            "实付金额",
            "优惠金额",
            "发货通知",
            "申请",
            "订单",
            "工单",
            "合同",
            "任务",
            "记录",
            "单据",
            "凭证",
            "发票",
            "库存",
            "商品",
            "物料",
            "批次",
            "设备",
            "计划",
            "流程",
            "数据",
        },
        key=lambda value: (-len(value), value),
    )
)
_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (canonical, re.compile(pattern))
    for canonical, pattern in (
        ("审批通过", r"审批通过|审核通过|通过审批|通过审核"),
        ("审批退回", r"审批退回|审核退回|退回"),
        ("重新编辑", r"重新编辑|再次编辑"),
        ("开具", r"开具|出具"),
        ("修改", r"修改|编辑|变更|调整"),
        ("删除", r"删除|移除"),
        ("创建", r"创建|新建|新增|生成"),
        ("提交", r"重新提交|提交|发起"),
        ("撤回", r"撤回|撤销"),
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
)
# Negative *modality* markers only. ``拒绝`` is an operation: ``必须拒绝`` is a
# positive obligation to reject, while ``不得拒绝`` is already covered by 不得.
_NEGATIVE_RE = re.compile(r"不得|严禁|禁止|不允许|不可|不能|无权|禁止再")
_MUST_RE = re.compile(r"必须|应当|务必|需要|需(?=[由在对将把于])")
_MAY_RE = re.compile(r"可以|允许|有权|可(?=[由在对将把于进行查看修改删除提交撤回审批开具])")
_ONLY_IF_RE = re.compile(r"只有.+?才|仅当.+?才|除非|只能|仅能")
_MODAL_PIVOT_RE = re.compile(
    r"不得|严禁|禁止|不允许|不可|不能|无权|必须|应当|务必|需要|"
    r"只能|仅能|可以|允许|有权"
)
_TEMPORAL_TRIGGER_RE = re.compile(
    r"(?P<trigger>[^，,；;。]{1,48}?)(?:之后|以后|后|时)[，,]?"
)
_WITHIN_WINDOW_RE = re.compile(
    r"在(?P<anchor>[^，,；;。]{1,24}?(?:之前|之后|以前|以后|前|后))"
    r"(?P<duration>\d+(?:\.\d+)?(?:天|日|小时|分钟|秒))"
    r"(?:内|以内|之内)"
)
_STATE_FROM_TO_RE = re.compile(
    r"从(?P<from>[^，,；;。]{1,24}?)(?:状态)?"
    r"(?:流转到|迁移到|转到|变更为|变为|转为|进入)"
    r"(?P<to>[^，,；;。]{1,24}?)"
    r"(?=，|,|并且|并|且|。|；|;|$)"
)
_ONLY_IF_FRAME_RE = re.compile(r"(?:只有|仅当)(?P<body>.+?)才")
_ONLY_ACTOR_PERMISSION_RE = re.compile(
    r"(?:只有|仅)(?P<actor>[^，,；;。]{1,24}?)(?:才)?(?:可以|允许|有权)"
)
_IF_BRANCH_RE = re.compile(r"^(?:如果|若|一旦)(?P<condition>.+?)(?:时)?[，,]?则")
_ELSE_BRANCH_RE = re.compile(r"^否则")
_STATE_INVARIANT_RE = re.compile(
    r"(?P<value>(?:保持|保留|维持)[^，,；;。]{1,24}?状态不变)"
)
_AND_RE = re.compile(r"并且|同时满足|以及|(?<![并])且(?![不])")
_OR_RE = re.compile(r"或者|或则|任一条件|其中之一")
_PUNCTUATION_BOUNDARY_RE = re.compile(r"[，,；;。]")
_GRAMMAR_FRAGMENT_RE = re.compile(
    r"如果|若|一旦|则|否则|只有|仅当|必须|应当|务必|不得|严禁|禁止|"
    r"不允许|不可|不能|可以|允许|有权|无权|本人|自己|尚未|已经|未审批|"
    r"状态为|并且|同时满足|以及|或者|其中之一|(?<![并])且(?![不])"
)
_EXACT_ADDRESS_KINDS = frozenset({"EXACT_SOURCE_LOCATOR", "PAGE_BBOX"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ordered_unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _explicit_refs(statement: str, vocabulary: Iterable[str]) -> list[str]:
    """Return longest non-overlapping coordinates literally present in source."""
    candidates: list[tuple[int, int, str]] = []
    for value in vocabulary:
        for match in re.finditer(re.escape(value), statement):
            candidates.append((match.start(), match.end(), value))
    candidates.sort(key=lambda row: (row[0], -(row[1] - row[0]), row[2]))

    selected: list[tuple[int, int, str]] = []
    for start, end, value in candidates:
        if any(start < used_end and end > used_start for used_start, used_end, _ in selected):
            continue
        selected.append((start, end, value))
    selected.sort(key=lambda row: (row[0], row[1], row[2]))
    return _ordered_unique(value for _start, _end, value in selected)


def _source_backed_vocabulary(
    statement: str,
    existing: Iterable[Any],
    heads: Iterable[str],
) -> list[str]:
    """Reuse literal identity names, never condition/modality/branch fragments."""
    head_values = tuple(_text(value) for value in heads if _text(value))
    existing_values = [
        _text(value)
        for value in existing
        if _text(value)
        and _text(value) in statement
        and not _GRAMMAR_FRAGMENT_RE.search(_text(value))
        and any(_text(value).endswith(head) for head in head_values)
    ]
    return _ordered_unique([*existing_values, *head_values])


def _only_actor_role(statement: str) -> str:
    match = _ONLY_ACTOR_PERMISSION_RE.search(statement)
    if not match:
        return ""
    actor = _text(match.group("actor")).strip(" ，,")
    if not actor or not any(actor.endswith(head) for head in _ROLE_HEADS):
        return ""
    return actor


def _action_matches(statement: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for priority, (canonical, pattern) in enumerate(_ACTION_PATTERNS):
        for match in pattern.finditer(statement):
            rows.append(
                {
                    "canonical": canonical,
                    "raw": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "priority": priority,
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            int(row["start"]),
            -len(_text(row["raw"])),
            int(row["priority"]),
        ),
    )


def _first_action_after(statement: str, pivot: int) -> dict[str, Any]:
    rows = [row for row in _action_matches(statement) if int(row["start"]) >= pivot]
    return dict(rows[0]) if rows else {}


def _governed_action(statement: str, fact: dict[str, Any]) -> dict[str, Any]:
    """Choose the governed operation from explicit grammar, not keyword order."""
    has_effect = bool(_list(fact.get("state_effects")) or _list(fact.get("data_effects")))
    if has_effect:
        trigger = _TEMPORAL_TRIGGER_RE.search(statement)
        if trigger:
            trigger_rows = _action_matches(_text(trigger.group("trigger")))
            if trigger_rows:
                return dict(trigger_rows[0])

    window = _WITHIN_WINDOW_RE.search(statement)
    if window:
        selected = _first_action_after(statement, window.end())
        if selected:
            return selected

    pivots = [match.end() for match in _MODAL_PIVOT_RE.finditer(statement)]
    selected = _first_action_after(statement, pivots[-1] if pivots else 0)
    if selected:
        return selected

    rows = _action_matches(statement)
    return dict(rows[0]) if rows else {}


def _governed_entities(
    statement: str,
    action: dict[str, Any],
    existing_entities: Iterable[Any],
) -> list[str]:
    """Bind objects to the selected operation, not to every entity in the sentence."""
    if not action:
        return []
    start = int(action.get("start", -1))
    end = int(action.get("end", -1))
    if start < 0 or end < start:
        return []

    vocabulary = _source_backed_vocabulary(
        statement,
        existing_entities,
        _ENTITY_HEADS,
    )
    # Object qualifiers often contain AND (本人创建且尚未审批的订单), so conjunctions
    # cannot be treated as an object boundary. Side-effect clauses are already separated
    # by punctuation or represented as formal DATA_EFFECT facts.
    suffix = statement[end:]
    suffix_clause = _PUNCTUATION_BOUNDARY_RE.split(suffix, maxsplit=1)[0]
    refs = _explicit_refs(suffix_clause, vocabulary)
    if refs:
        return refs

    prefix = statement[:start]
    prefix_clause = _PUNCTUATION_BOUNDARY_RE.split(prefix)[-1]
    refs = _explicit_refs(prefix_clause, vocabulary)
    return refs[-1:] if refs else []

__all__ = sorted(name for name in globals() if not name.startswith('__'))
