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
    """Reuse literal existing identities before falling back to generic heads."""
    head_values = tuple(_text(value) for value in heads if _text(value))
    existing_values = [
        _text(value)
        for value in existing
        if _text(value)
        and _text(value) in statement
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


def _modality(statement: str) -> tuple[str, str]:
    """Explicit modal markers determine modality; operation polarity is separate."""
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


def _qualified_object_conditions(
    statement: str,
    action: dict[str, Any],
    entities: list[str],
) -> tuple[list[str], str]:
    """Project explicit qualifiers between an action and its actor-exclusive object."""
    if not _ONLY_ACTOR_PERMISSION_RE.search(statement) or not action or not entities:
        return [], ""
    action_end = int(action.get("end", -1))
    if action_end < 0:
        return [], ""
    candidates: list[tuple[int, str]] = []
    for entity in entities:
        for match in re.finditer(re.escape(entity), statement[action_end:]):
            candidates.append((action_end + match.start(), entity))
    if not candidates:
        return [], ""
    object_start, _entity = max(candidates, key=lambda row: row[0])
    qualifier = statement[action_end:object_start].strip(" ，,；;。")
    qualifier = re.sub(r"^(?:把|将|对|向|给)", "", qualifier)
    qualifier = re.sub(r"的$", "", qualifier).strip(" ，,；;。")
    if not qualifier:
        return [], ""
    has_and = bool(_AND_RE.search(qualifier))
    has_or = bool(_OR_RE.search(qualifier))
    if has_and and has_or:
        return [qualifier], "UNRESOLVED"
    if has_and:
        rows = [re.sub(r"的$", "", _clean_condition(row)) for row in _AND_RE.split(qualifier)]
        values = _ordered_unique(row for row in rows if row)
        return values, "AND" if len(values) > 1 else "SINGLE_CONDITION"
    if has_or:
        rows = [re.sub(r"的$", "", _clean_condition(row)) for row in _OR_RE.split(qualifier)]
        values = _ordered_unique(row for row in rows if row)
        return values, "OR" if len(values) > 1 else "SINGLE_CONDITION"
    value = re.sub(r"的$", "", _clean_condition(qualifier))
    return ([value] if value else []), ("SINGLE_CONDITION" if value else "")


def _condition_coordinates(
    statement: str,
    fact: dict[str, Any],
    *,
    action: dict[str, Any],
    entities: list[str],
) -> tuple[list[str], str]:
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

    qualified, qualified_combinator = _qualified_object_conditions(
        statement,
        action,
        entities,
    )
    existing = [_clean_condition(row) for row in _list(fact.get("conditions"))]
    rows = _ordered_unique([*(row for row in existing if row), *qualified])
    if qualified:
        if len(rows) <= 1:
            return rows, "SINGLE_CONDITION"
        return rows, qualified_combinator or "UNRESOLVED"
    return rows, _text(fact.get("condition_combinator"))


def _normalize_condition_frame(
    fact: dict[str, Any], conditions: list[str], combinator: str
) -> None:
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


def _normalized_postconditions(statement: str, existing: Iterable[Any]) -> list[str]:
    rows = [_text(row) for row in existing if _text(row)]
    for match in _STATE_INVARIANT_RE.finditer(statement):
        value = _text(match.group("value"))
        if value and value not in rows:
            rows.append(value)
    return rows


def _normalized_fact_type(fact: dict[str, Any]) -> str:
    if _list(fact.get("state_effects")):
        return "STATE_TRANSITION"
    if _text(fact.get("modality")).upper() in {"MAY", "MUST_NOT", "ONLY_IF"}:
        return "PERMISSION_RULE"
    return "BUSINESS_RULE"


def _normalize_primary_claim(
    fact: dict[str, Any],
    *,
    action: dict[str, Any],
    actors: list[str],
    entities: list[str],
) -> bool:
    claims = [dict(row) for row in _list(fact.get("claims")) if isinstance(row, dict)]
    changed = False
    for claim in claims:
        if _text(claim.get("claim_type")).upper() != "PRIMARY_OPERATION":
            continue
        predicate = _text(action.get("canonical"))
        if predicate and _text(claim.get("predicate")) != predicate:
            claim["predicate"] = predicate
            changed = True
        if actors and _list(claim.get("subject_refs")) != actors:
            claim["subject_refs"] = list(actors)
            changed = True
        if entities and _list(claim.get("object_refs")) != entities:
            claim["object_refs"] = list(entities)
            changed = True
    if changed:
        fact["claims"] = claims
    return changed


def _source_locator(fact: dict[str, Any]) -> str:
    for span in _list(fact.get("source_spans")):
        if not isinstance(span, dict):
            continue
        locator = _text(span.get("locator") or span.get("source_locator"))
        if locator:
            return locator
    return ""


def _pair_split_if_else_frames(facts: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Pair one unique split IF and ELSE fact sharing one exact source locator."""
    by_locator: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if _text(fact.get("fact_type")).upper() in _FORMAL_TYPED_COORDINATES:
            continue
        locator = _source_locator(fact)
        if locator:
            by_locator[locator].append(fact)

    paired_groups = 0
    changed_ids: list[str] = []
    for rows in by_locator.values():
        then_rows = [row for row in rows if _IF_BRANCH_RE.search(_text(row.get("raw_statement")))]
        else_rows = [row for row in rows if _ELSE_BRANCH_RE.search(_text(row.get("raw_statement")))]
        if len(then_rows) != 1 or len(else_rows) != 1:
            continue
        then_fact = then_rows[0]
        else_fact = else_rows[0]
        then_statement = _text(then_fact.get("raw_statement"))
        else_statement = _text(else_fact.get("raw_statement"))
        match = _IF_BRANCH_RE.search(then_statement)
        condition = _clean_condition(match.group("condition")) if match else ""
        conditions = [condition] if condition else list(_list(then_fact.get("conditions")))
        combinator = "SINGLE_CONDITION" if len(conditions) == 1 else _text(
            then_fact.get("condition_combinator")
        )
        paired_statement = f"{then_statement}；{else_statement}"
        for index, (fact, branch) in enumerate(((then_fact, "THEN"), (else_fact, "ELSE"))):
            fact["conditions"] = list(conditions)
            fact["condition_combinator"] = combinator
            fact["trigger"] = {"raw": conditions[0]} if conditions else {}
            frame = dict(_dict(fact.get("condition_frame")))
            frame.update(
                {
                    "kind": "IF_THEN_ELSE",
                    "combinator": combinator,
                    "conditions": list(conditions),
                    "branch": branch,
                    "branch_index": index,
                    "parent_conditions": [],
                    "paired_statement": paired_statement,
                    "source_backed": True,
                }
            )
            fact["condition_frame"] = frame
            normalization = dict(_dict(fact.get("explicit_semantic_normalization")))
            normalized_fields = set(_list(normalization.get("normalized_fields")))
            normalized_fields.add("if_then_else_frame")
            normalization.update(
                {
                    "status": "PASS",
                    "normalized_fields": sorted(_text(row) for row in normalized_fields if _text(row)),
                    "source_backed": True,
                    "governed_operation_binding": True,
                    "split_branch_pairing": True,
                    "new_fact_discovered": False,
                    "automatic_winner_used": False,
                }
            )
            fact["explicit_semantic_normalization"] = normalization
            fact["semantic_signature"] = _semantic_signature(fact)
            fact_id = _text(fact.get("fact_id"))
            if fact_id:
                changed_ids.append(fact_id)
        paired_groups += 1
    return paired_groups, changed_ids


def normalize_explicit_business_fact_semantics(asset: dict[str, Any]) -> dict[str, Any]:
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    normalized_ids: list[str] = []
    skipped_formal_typed = 0
    field_counts: dict[str, int] = {}

    for fact in facts:
        statement = _text(fact.get("raw_statement"))
        current_type = _text(fact.get("fact_type")).upper()
        if not statement or _text(fact.get("kind")) == "TERM_ALIAS":
            continue
        if current_type in _FORMAL_TYPED_COORDINATES:
            skipped_formal_typed += 1
            continue
        changed: list[str] = []

        subject = dict(_dict(fact.get("subject")))
        existing_actors = [
            _text(row) for row in _list(subject.get("actor_refs")) if _text(row)
        ]
        actor_vocabulary = _source_backed_vocabulary(
            statement,
            existing_actors,
            _ROLE_HEADS,
        )
        exception_scopes = set(_text(row) for row in _list(fact.get("exception_scope")))
        only_actor = _only_actor_role(statement)
        explicit_actors = [
            row
            for row in _explicit_refs(statement, actor_vocabulary)
            if row not in exception_scopes
        ]
        actors = [only_actor] if only_actor else (explicit_actors or existing_actors)
        if actors != _list(subject.get("actor_refs")):
            subject["actor_refs"] = actors
            changed.append("actor_refs")

        action_coordinate = _governed_action(statement, fact)
        action = {
            "canonical": _text(action_coordinate.get("canonical")),
            "raw": _text(action_coordinate.get("raw")),
        }
        action = {key: value for key, value in action.items() if value}
        if action and action != _dict(fact.get("action")):
            fact["action"] = action
            fact["predicate"] = action["canonical"]
            changed.append("action")

        existing_entities = [
            _text(row) for row in _list(subject.get("entity_refs")) if _text(row)
        ]
        governed_entities = _governed_entities(
            statement,
            action_coordinate,
            existing_entities,
        )
        entities = governed_entities or existing_entities
        if governed_entities and entities != _list(subject.get("entity_refs")):
            subject["entity_refs"] = entities
            changed.append("entity_refs")
        fact["subject"] = subject

        object_part = dict(_dict(fact.get("object")))
        if governed_entities and entities != _list(object_part.get("entity_refs")):
            object_part["entity_refs"] = entities
            changed.append("object_refs")
        fact["object"] = object_part

        modality, polarity = _modality(statement)
        if modality != _text(fact.get("modality")):
            fact["modality"] = modality
            changed.append("modality")
        if polarity != _text(fact.get("polarity")):
            fact["polarity"] = polarity
            changed.append("polarity")

        conditions, combinator = _condition_coordinates(
            statement,
            fact,
            action=action_coordinate,
            entities=entities,
        )
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
        if (
            time_window is not None
            and _list(fact.get("time_window_constraints")) != [time_window]
        ):
            fact["time_window_constraints"] = [time_window]
            changed.append("time_window_constraints")

        postconditions = _normalized_postconditions(
            statement,
            _list(fact.get("postconditions")),
        )
        if postconditions != _list(fact.get("postconditions")):
            fact["postconditions"] = postconditions
            changed.append("postconditions")

        fact_type = _normalized_fact_type(fact)
        if fact_type != current_type:
            fact["fact_type"] = fact_type
            changed.append("fact_type")

        if _normalize_primary_claim(
            fact,
            action=action_coordinate,
            actors=actors,
            entities=entities,
        ):
            changed.append("primary_operation_claim")

        if not changed:
            continue
        fact["explicit_semantic_normalization"] = {
            "status": "PASS",
            "normalized_fields": sorted(set(changed)),
            "source_backed": True,
            "governed_operation_binding": True,
            "new_fact_discovered": False,
            "automatic_winner_used": False,
        }
        fact["semantic_signature"] = _semantic_signature(fact)
        fact_id = _text(fact.get("fact_id"))
        if fact_id:
            normalized_ids.append(fact_id)
        for field in set(changed):
            field_counts[field] = field_counts.get(field, 0) + 1

    paired_groups, paired_ids = _pair_split_if_else_frames(facts)
    normalized_ids = _ordered_unique([*normalized_ids, *paired_ids])
    if paired_groups:
        field_counts["if_then_else_frame"] = field_counts.get("if_then_else_frame", 0) + (
            paired_groups * 2
        )

    ledger["items"] = facts
    asset["business_fact_ledger"] = ledger
    asset["explicit_fact_semantic_normalization_receipt"] = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "normalized_fact_count": len(normalized_ids),
        "normalized_fact_ids": normalized_ids,
        "formal_typed_fact_count_left_on_compiler_coordinates": skipped_formal_typed,
        "paired_if_then_else_group_count": paired_groups,
        "paired_if_then_else_fact_count": paired_groups * 2,
        "normalized_field_counts": dict(sorted(field_counts.items())),
        "existing_ledger_reused": True,
        "existing_source_backed_identity_vocabulary_reused": True,
        "governed_operation_binding": True,
        "qualified_object_conditions_compiled": True,
        "split_if_else_pairing_requires_one_unique_pair_per_locator": True,
        "negative_operation_is_not_negative_modality": True,
        "new_fact_discovery_allowed": False,
        "source_statement_rewrite_allowed": False,
        "conflicting_source_value_selection_allowed": False,
        "overlapping_identity_coordinate_emission_allowed": False,
        "formal_typed_coordinate_reinterpretation_allowed": False,
        "automatic_winner_used": False,
    }
    governance = dict(_dict(asset.get("governance")))
    governance.update(
        {
            "explicit_fact_coordinates_normalized_at_compiler_boundary": True,
            "explicit_fact_identity_is_bound_to_governed_operation": True,
            "explicit_fact_normalization_reuses_source_backed_identity_vocabulary": True,
            "explicit_fact_qualified_object_conditions_are_source_backed": True,
            "explicit_fact_split_if_else_pairing_is_locator_scoped": True,
            "explicit_fact_negative_operation_is_separate_from_modality": True,
            "explicit_fact_normalization_discovers_new_facts": False,
            "explicit_fact_normalization_selects_conflicting_values": False,
            "explicit_fact_identity_mentions_are_longest_non_overlapping": True,
            "formal_typed_fact_coordinates_remain_compiler_owned": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["RECEIPT_SCHEMA", "normalize_explicit_business_fact_semantics"]
