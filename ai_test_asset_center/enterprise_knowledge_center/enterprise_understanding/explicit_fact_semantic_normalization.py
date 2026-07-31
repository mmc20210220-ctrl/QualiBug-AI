"""Normalize explicit Chinese fact coordinates at the existing compiler boundary.

The compatibility extractor intentionally remains broad so old enterprise fixtures keep
working.  Structure-first compilation turns those rows into the formal typed ledger.
This module closes the contract between both stages: it normalizes coordinates already
stated in the same source span, but never discovers a new fact, chooses between
conflicting source statements, or creates a second ledger.

Normalization is deliberately limited to deterministic Chinese grammar that is visible
in ``raw_statement``:

* explicit role/object lexical heads are added as source-backed identity coordinates;
* governed actions are separated from temporal anchors and downstream effects;
* ``只有…才可以`` remains a conditional MAY permission rather than ONLY_IF modality;
* source/target states are retained for ``从A变为B`` transitions;
* ``在X后24小时内`` becomes one WITHIN time-window coordinate; and
* typed fact classification distinguishes actor obligations from permissions.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .structured_fact_compiler import _semantic_signature

RECEIPT_SCHEMA = "qualibug.explicit-fact-semantic-normalization.v1"

_ROLE_SUFFIXES = tuple(
    sorted(
        {
            "仓库管理员",
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
            "测试订单",
            "已出库订单",
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
_NEGATIVE_RE = re.compile(r"不得|严禁|禁止|不允许|不可|不能|无权|拒绝|禁止再")
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
    r"(?:在)?(?P<anchor>[^，,；;。]{1,24}?(?:之前|之后|以前|以后|前|后))"
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
_AND_RE = re.compile(r"并且|同时满足|以及|(?<![并])且(?![不])")
_OR_RE = re.compile(r"或者|或则|任一条件|其中之一")


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
    """Return only vocabulary coordinates literally present in the source span."""
    rows: list[tuple[int, int, str]] = []
    for value in vocabulary:
        for match in re.finditer(re.escape(value), statement):
            rows.append((match.start(), -len(value), value))
    return _ordered_unique(value for _start, _length, value in sorted(rows))


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


def _first_action_after(statement: str, pivot: int) -> dict[str, str]:
    rows = [row for row in _action_matches(statement) if int(row["start"]) >= pivot]
    if not rows:
        return {}
    row = rows[0]
    return {"canonical": _text(row["canonical"]), "raw": _text(row["raw"])}


def _governed_action(statement: str, fact: dict[str, Any]) -> dict[str, str]:
    """Choose the governed operation from explicit grammar, not keyword order."""
    has_effect = bool(_list(fact.get("state_effects")) or _list(fact.get("data_effects")))
    if has_effect:
        trigger = _TEMPORAL_TRIGGER_RE.search(statement)
        if trigger:
            trigger_rows = _action_matches(_text(trigger.group("trigger")))
            if trigger_rows:
                row = trigger_rows[0]
                return {
                    "canonical": _text(row["canonical"]),
                    "raw": _text(row["raw"]),
                }

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
    if not rows:
        return {}
    row = rows[0]
    return {"canonical": _text(row["canonical"]), "raw": _text(row["raw"])}


def _modality(statement: str) -> tuple[str, str]:
    """Explicit action modality wins; 只有/仅当 remains the condition frame."""
    if _NEGATIVE_RE.search(statement):
        return "MUST_NOT", "NEGATIVE"
    if _MUST_RE.search(statement):
        return "MUST", "POSITIVE"
    if _MAY_RE.search(statement):
        return "MAY", "POSITIVE"
    if _ONLY_IF_RE.search(statement):
        return "ONLY_IF", "POSITIVE"
    return "ASSERTS", "POSITIVE"


def _clean_condition(value: Any) -> str:
    item = _text(value).strip(" ，,；;。")
    item = re.sub(r"^(?:在|当|如果|若|一旦)", "", item)
    item = re.sub(r"(?:的情况下|条件下|情况下|时)$", "", item)
    return item.strip(" ，,；;。")


def _condition_coordinates(statement: str, fact: dict[str, Any]) -> tuple[list[str], str]:
    match = _ONLY_IF_FRAME_RE.search(statement)
    if match:
        body = _clean_condition(match.group("body"))
        has_and = bool(_AND_RE.search(body))
        has_or = bool(_OR_RE.search(body))
        if has_and and has_or:
            return [body], "UNRESOLVED"
        if has_and:
            rows = [_clean_condition(row) for row in _AND_RE.split(body)]
            return _ordered_unique(row for row in rows if row), "AND"
        if has_or:
            rows = [_clean_condition(row) for row in _OR_RE.split(body)]
            return _ordered_unique(row for row in rows if row), "OR"
        return ([body] if body else []), ("SINGLE_CONDITION" if body else "")

    existing = [_clean_condition(row) for row in _list(fact.get("conditions"))]
    rows = _ordered_unique(row for row in existing if row)
    return rows, _text(fact.get("condition_combinator"))


def _normalize_condition_frame(fact: dict[str, Any], conditions: list[str], combinator: str) -> None:
    fact["conditions"] = conditions
    fact["condition_combinator"] = combinator
    frame = dict(_dict(fact.get("condition_frame")))
    if not conditions and not frame:
        return
    frame["conditions"] = list(conditions)
    frame["combinator"] = combinator
    if combinator == "AND":
        frame["kind"] = "ALL"
    elif combinator == "OR":
        frame["kind"] = "ANY"
    elif combinator == "UNRESOLVED":
        frame["kind"] = "UNRESOLVED"
    elif conditions:
        frame["kind"] = "LEAF"
    fact["condition_frame"] = frame
    fact["trigger"] = {"raw": conditions[0]} if conditions else {}


def _normalized_state_effect(statement: str) -> dict[str, Any] | None:
    matches = list(_STATE_FROM_TO_RE.finditer(statement))
    if len(matches) != 1:
        return None
    match = matches[0]
    from_state = _text(match.group("from")).removesuffix("状态")
    to_state = _text(match.group("to")).removesuffix("状态")
    if not from_state or not to_state:
        return None
    return {
        "from_state": from_state,
        "to_state": to_state,
        "raw": match.group(0),
        "source_backed": True,
    }


def _normalized_time_window(statement: str) -> dict[str, Any] | None:
    matches = list(_WITHIN_WINDOW_RE.finditer(statement))
    if len(matches) != 1:
        return None
    match = matches[0]
    anchor = _text(match.group("anchor"))
    anchor = re.sub(r"^(?:在)", "", anchor)
    duration = _text(match.group("duration"))
    if not anchor or not duration:
        return None
    return {
        "raw": match.group(0),
        "anchor": anchor,
        "relation": "WITHIN",
        "duration": duration,
        "source_backed": True,
    }


def _normalized_fact_type(fact: dict[str, Any]) -> str:
    current = _text(fact.get("fact_type")).upper()
    if current in {
        "TERM_ALIAS",
        "OBJECT_RELATION",
        "CARDINALITY_CONSTRAINT",
        "DERIVED_VALUE",
        "DATA_EFFECT",
    }:
        return current
    if _list(fact.get("state_effects")):
        return "STATE_TRANSITION"
    if _text(fact.get("modality")).upper() in {"MAY", "MUST_NOT", "ONLY_IF"}:
        return "PERMISSION_RULE"
    return "BUSINESS_RULE"


def normalize_explicit_business_fact_semantics(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    normalized_ids: list[str] = []
    field_counts: dict[str, int] = {}

    for fact in facts:
        statement = _text(fact.get("raw_statement"))
        if not statement or _text(fact.get("kind")) == "TERM_ALIAS":
            continue
        changed: list[str] = []

        subject = dict(_dict(fact.get("subject")))
        actors = _ordered_unique(
            [*_list(subject.get("actor_refs")), *_explicit_refs(statement, _ROLE_SUFFIXES)]
        )
        entities = _ordered_unique(
            [*_list(subject.get("entity_refs")), *_explicit_refs(statement, _ENTITY_HEADS)]
        )
        if actors != _list(subject.get("actor_refs")):
            subject["actor_refs"] = actors
            changed.append("actor_refs")
        if entities != _list(subject.get("entity_refs")):
            subject["entity_refs"] = entities
            changed.append("entity_refs")
        fact["subject"] = subject
        object_part = dict(_dict(fact.get("object")))
        object_entities = _ordered_unique(
            [*_list(object_part.get("entity_refs")), *entities]
        )
        if object_entities != _list(object_part.get("entity_refs")):
            object_part["entity_refs"] = object_entities
            changed.append("object_refs")
        fact["object"] = object_part

        action = _governed_action(statement, fact)
        if action and action != _dict(fact.get("action")):
            fact["action"] = action
            fact["predicate"] = action["canonical"]
            changed.append("action")

        modality, polarity = _modality(statement)
        if modality != _text(fact.get("modality")):
            fact["modality"] = modality
            changed.append("modality")
        if polarity != _text(fact.get("polarity")):
            fact["polarity"] = polarity
            changed.append("polarity")

        conditions, combinator = _condition_coordinates(statement, fact)
        before_frame = (
            list(_list(fact.get("conditions"))),
            _text(fact.get("condition_combinator")),
            dict(_dict(fact.get("condition_frame"))),
        )
        _normalize_condition_frame(fact, conditions, combinator)
        after_frame = (
            list(_list(fact.get("conditions"))),
            _text(fact.get("condition_combinator")),
            dict(_dict(fact.get("condition_frame"))),
        )
        if before_frame != after_frame:
            changed.append("condition_frame")

        state_effect = _normalized_state_effect(statement)
        if state_effect is not None and _list(fact.get("state_effects")) != [state_effect]:
            fact["state_effects"] = [state_effect]
            changed.append("state_effects")

        time_window = _normalized_time_window(statement)
        if time_window is not None and _list(fact.get("time_window_constraints")) != [time_window]:
            fact["time_window_constraints"] = [time_window]
            changed.append("time_window_constraints")

        fact_type = _normalized_fact_type(fact)
        if fact_type != _text(fact.get("fact_type")).upper():
            fact["fact_type"] = fact_type
            changed.append("fact_type")

        if not changed:
            continue
        fact["explicit_semantic_normalization"] = {
            "status": "PASS",
            "normalized_fields": sorted(set(changed)),
            "source_backed": True,
            "new_fact_discovered": False,
            "automatic_winner_used": False,
        }
        fact["semantic_signature"] = _semantic_signature(fact)
        fact_id = _text(fact.get("fact_id"))
        if fact_id:
            normalized_ids.append(fact_id)
        for field in set(changed):
            field_counts[field] = field_counts.get(field, 0) + 1

    ledger["items"] = facts
    asset["business_fact_ledger"] = ledger
    asset["explicit_fact_semantic_normalization_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "normalized_fact_count": len(normalized_ids),
        "normalized_fact_ids": normalized_ids,
        "normalized_field_counts": dict(sorted(field_counts.items())),
        "existing_ledger_reused": True,
        "new_fact_discovery_allowed": False,
        "source_statement_rewrite_allowed": False,
        "conflicting_source_value_selection_allowed": False,
        "automatic_winner_used": False,
    }
    governance = dict(_dict(asset.get("governance")))
    governance.update(
        {
            "explicit_fact_coordinates_normalized_at_compiler_boundary": True,
            "explicit_fact_normalization_discovers_new_facts": False,
            "explicit_fact_normalization_selects_conflicting_values": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "normalize_explicit_business_fact_semantics"]
