# -*- coding: utf-8 -*-
"""Coverage Unit Registry — obligation-layer semantic uniquification (P0-1).

同一缺陷面（归一化操作 + 断言类别 + 违反形态）被展开成大量角色/来源规则/
输入变体，每个变体独立 obligation_id → 独立编译/执行/验证/持久化 → 最后才在
``canonical_defect_registry``（finding 层）聚合。语义唯一化发生得太晚：
义务/编译/执行全在重复变体上消耗。

本模块把语义唯一化提前到义务层（SPEC P0-1）：

1. 每个义务派生 ``canonical_obligation_key``（SPEC §2.1 身份组成）——
   归一化操作 + 断言类别 + 违反形态 + 关系类型 [+ 目标字段或状态]
   [+ 来源规则语义指纹]。角色不进身份：具体 actor 只进
   ``coverage_unit.actor_variants``（角色是证据不是身份）。
2. 按 key 归并为 Behavior Coverage Unit（§2.2）：变体并入
   ``actor_variants`` / ``input_variants`` / ``observation_variants``，
   无法归并的（语义不同 / 观察者不同 / 清理语义不同）保持独立（fail-closed）。
3. 选中的 Unit 编译一次，其余角色变体派生为多臂 Experiment Bundle（§2.3）：
   ``derive_arm_experiment`` 对已编译实验做 actor 重绑（编译期版本的
   ``apply_actor_execution_overlay`` 语义），严格校验，失败即回退独立编译。

通用机制，零硬编码：身份归一全部基于产品自身结构字段（操作+路径形态 /
断言类别 / 违反形态 / 关系类型 / 目标字段或状态 / 规则语义指纹），不含任何
行业或基准特定词汇；UUID/SKU/nonce 剥离复用 ``verified_discovery_archive``
的归一化规则。聚合键全部来自产品自身结构，绝不含 GT/基准推导规则。
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

_COVERAGE_UNIT_SCHEMA = "qualibug.coverage-unit-registry.v1"
_COVERAGE_UNIT_ID_PREFIX = "cunit_"
# 显式成本边界（receipted，SPEC 约束）：单 Unit 最多派生执行臂数，
# 超出部分回退独立编译——零覆盖损失，边界在 arm receipt 中可见。
MAX_ARMS_PER_UNIT = 24

# ── 运行时实例值剥离（复用 verified_discovery_archive 归一化规则）──────────
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NONCE_RE = re.compile(r"-[0-9a-f]{8,}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_operation_path(path: str) -> str:
    """归一化操作路径（行业无关）：剥离查询串、UUID、nonce、数字/test-id 段。

    Mirrors ``verified_discovery_archive._normalize_operation_path`` so the
    same interface yields the same identity across runs and role variants.
    """
    text = _text(path).split("?", 1)[0].rstrip("/")
    if not text:
        return ""
    text = _UUID_RE.sub("{id}", text)
    text = _NONCE_RE.sub("", text)
    segments: list[str] = []
    for segment in text.split("/"):
        if not segment:
            continue
        if segment.isdigit():
            segment = "{id}"
        elif re.fullmatch(r"(?i)qb[_-]test[_-].+", segment):
            segment = "{id}"
        elif re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9]*", segment) and len(segment) >= 3:
            segment = "{id}"
        segments.append(segment)
    return "/".join(segments)


# ── 断言类别（与 experiment_compiler_obligation_core._FAMILY_ASSERTION_KIND
#    同值的产品 SSOT；本地镜像避免反向循环导入，tests 交叉校验一致性）────────
_FAMILY_ASSERTION_KIND: dict[str, str] = {
    "authorization": "authorization",
    "isolation": "isolation",
    "visibility": "visibility",
    "privacy": "privacy",
    "validation": "validation_rejection",
    "state": "state_transition",
    "state_integrity": "state_transition",
    "lifecycle": "state_transition",
    "consistency": "cross_surface_consistency",
    "invariant": "state_transition",
    "conservation": "conservation",
    "concurrency": "concurrency",
    "idempotency": "idempotency",
    "temporal": "temporal",
    "persistence_integrity": "persistence_integrity",
    "ui_state_consistency": "ui_state_consistency",
}

# ── 违反形态（结构化断言形态：assertion 检查的违反形状，非执行结果）───────
# 全部来自产品自身模板/表达式词汇（generic，绝无行业术语）。
_EXPRESSION_KIND_TO_SHAPE = {
    "forbidden_state_transition": "forbidden_state_transition",
    "state_transition": "state_transition",
    "must_not_transition": "forbidden_state_transition",
}
_TEMPLATE_TO_SHAPE = {
    "state_transition": "state_transition",
    "authorization_control_treatment": "control_treatment_access",
    "visibility_control_treatment": "control_treatment_access",
    "owner_viewer_isolation": "owner_viewer_isolation",
    "permitted_operation_invocation": "permitted_invocation",
    "idempotent_effect_cardinality": "effect_cardinality",
    "concurrent_final_invariant": "concurrent_final_invariant",
    "invariant_conservation": "conservation",
    "persistence_state_enumeration": "persistence_enumeration",
    "persistence_field_bound": "persistence_field_bound",
    "ui_state_consistency": "ui_state_consistency",
    "single_dimension_mutation": "single_dimension_mutation",
    "credential_gated_write": "credential_gated_write",
    "cross_document_conflict": "cross_document_conflict",
    "owner_viewer_isolation_path": "owner_viewer_isolation",
}


def _expression(prop: dict[str, Any]) -> dict[str, Any]:
    expr = prop.get("expression")
    return expr if isinstance(expr, dict) else {}


def _violation_shape_of(obligation: dict[str, Any]) -> str:
    """违反形态：断言要检查的违反形状（结构推导，非执行结果）。"""
    prop = _dict(obligation.get("property"))
    template = _text(prop.get("template"))
    expression = _expression(prop)
    expr_kind = _text(expression.get("kind")).lower()
    if expr_kind in _EXPRESSION_KIND_TO_SHAPE:
        return _EXPRESSION_KIND_TO_SHAPE[expr_kind]
    if template in _TEMPLATE_TO_SHAPE:
        return _TEMPLATE_TO_SHAPE[template]
    if template == "invariant_validation":
        rejection_class = prop.get("expected_rejection_status_class")
        if rejection_class is not None:
            return f"rejection_expected_class_{_text(rejection_class)}"
        return "rejection_expected"
    if template.startswith("invariant_privacy") or template == "privacy_field_policy":
        policy = _text(prop.get("privacy_policy"))
        return f"privacy_field_{policy}" if policy else "privacy_field"
    if template == "permitted_operation_invocation":
        return "permitted_invocation"
    if template == "source_contract_conflict":
        return "cross_document_conflict"
    if template == "account_enumeration_guard":
        return "anonymous_account_enumeration"
    # 未知模板保留原模板名（open taxonomy：新产品模板天然可归并/保持独立）
    return _text(template) or "unspecified"


def _relation_type_of(obligation: dict[str, Any]) -> str:
    """关系类型：义务所经的 IR 关系类别（generic 关系词汇）。

    The relation type distinguishes structurally different violation
    semantics of the same operation: denies (control/treatment pair) vs
    permits (single-actor invocation) vs owns (isolation) vs observes
    (invariant) vs transitions (state machine).
    """
    prop = _dict(obligation.get("property"))
    template = _text(prop.get("template"))
    if "control_treatment" in template:
        return "denies"
    if template in {"owner_viewer_isolation", "owner_viewer_isolation_path"}:
        return "owns"
    if template == "permitted_operation_invocation":
        return "permits"
    if template == "state_transition":
        return "transitions"
    if template == "single_dimension_mutation":
        return "produces"
    if template == "cross_document_conflict":
        return "observes"
    if template == "ui_state_consistency":
        return "observes"
    family = _text(obligation.get("risk_family"))
    if family in {
        "validation",
        "conservation",
        "idempotency",
        "concurrency",
        "temporal",
        "visibility",
        "privacy",
        "persistence_integrity",
        "consistency",
    }:
        return "observes"
    if family == "state":
        return "transitions"
    return ""


def _target_field_or_state_of(obligation: dict[str, Any]) -> str:
    """目标字段或状态：规则约束的字段路径 / 状态（有则带，无则空）。"""
    prop = _dict(obligation.get("property"))
    template = _text(prop.get("template"))
    expression = _expression(prop)
    if template == "state_transition":
        from_ref = _text(prop.get("from_state_ref"))
        to_ref = _text(prop.get("to_state_ref"))
        if from_ref or to_ref:
            return f"{from_ref}>{to_ref}"
    for operand in _list(expression.get("operands")):
        if isinstance(operand, dict):
            field = _text(operand.get("field_id") or operand.get("field"))
            if field:
                return field
    field_tokens = prop.get("field_tokens")
    if isinstance(field_tokens, list) and field_tokens:
        return ".".join(_text(item) for item in field_tokens)
    for key in ("field", "json_path", "persistence_state_field", "persistence_bounded_field"):
        value = _text(prop.get(key))
        if value:
            return value
    equation = _dict(expression.get("equation"))
    for term in _list(equation.get("terms")):
        if isinstance(term, dict):
            field = _text(term.get("field_id") or term.get("field"))
            if field:
                return field
        elif _text(term):
            return _text(term)
    return ""


def _normalize_rule_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).lower()


def _source_rule_semantic_identity_of(obligation: dict[str, Any]) -> str:
    """来源规则语义指纹（有则带）：规则 statement 的归一指纹。

    义务携带 field_rule_binding（权限矩阵行 / 字段规则绑定）时取其
    statement；否则取表达式 raw 语句。同一规则的重复来源（跨文档重复规则、
    角色变体）归一到同一指纹；真正不同的规则保持不同指纹（fail-closed）。
    归一化剥离空白与大小写，不含任何实例值。
    """
    prop = _dict(obligation.get("property"))
    binding = _dict(prop.get("field_rule_binding"))
    statement = _text(binding.get("statement"))
    if statement:
        return _sha256({"rule_statement": _normalize_rule_text(statement)})[:16]
    expression = _expression(prop)
    raw = _text(expression.get("raw"))
    if raw:
        return _sha256({"rule_statement": _normalize_rule_text(raw)})[:16]
    return ""


def _observation_semantics_guard(obligation: dict[str, Any]) -> str:
    """语义等价守卫：观察者集合 + 清理语义。

    Merging obligations of one surface is only legal when the observation
    channels and cleanup semantics are identical (SPEC §2.4.1 语义等价).
    Different observers/cleanup → different coverage unit (fail-closed).
    """
    observers = sorted({
        _text(value)
        for value in _list(obligation.get("required_observers"))
        if _text(value)
    })
    cleanup = _dict(obligation.get("cleanup_requirement"))
    cleanup_key = "|".join((
        _text(cleanup.get("mode")),
        _text(cleanup.get("operation_ref")),
        "required" if cleanup.get("required") is True else "not_required",
    ))
    return f"obs:{','.join(observers)}|cleanup:{cleanup_key}"


def _operation_identity(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    """归一化操作身份：METHOD + 归一化路径（空串表示无法解析）。

    解析顺序：property.operation_ref / required_operations → IR 操作；
    回退 source_refs locator（"GET /path"）；UI 义务用归一化 ui_url；
    最终回退 operation_path_prefix。全空返回 ""（调用方做 fail-closed
    唯一化处理，见 derive_canonical_obligation_key）。
    """
    prop = _dict(obligation.get("property"))
    operations = operation_index
    if operations is None and behavior_ir is not None:
        operations = {
            _text(row.get("id")): dict(row)
            for row in _list(behavior_ir.get("operations"))
            if isinstance(row, dict) and _text(row.get("id"))
        }
    operations = operations or {}
    op_ref = _text(prop.get("operation_ref"))
    if not op_ref:
        for value in _list(obligation.get("required_operations")):
            if _text(value):
                op_ref = _text(value)
                break
    operation = _dict(operations.get(op_ref))
    method = _text(operation.get("method")).upper()
    path = _text(operation.get("path") or operation.get("raw_path"))
    if method and path:
        normalized = _normalize_operation_path(path)
        if normalized:
            return f"{method} {normalized}"
    # source_refs locator 回退（"GET /api/orders/{id}"）
    locator_re = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)$")
    for source in _list(obligation.get("source_refs")):
        if not isinstance(source, dict):
            continue
        locator = _text(source.get("locator"))
        match = locator_re.match(locator)
        if not match:
            continue
        normalized = _normalize_operation_path(match.group(2))
        if normalized:
            return f"{match.group(1).upper()} {normalized}"
    # UI 义务：页面 URL 是它的操作面
    ui_url = _text(prop.get("ui_url"))
    if ui_url:
        normalized = _normalize_operation_path(ui_url)
        if normalized:
            return f"UI {normalized}"
    prefix = _text(prop.get("operation_path_prefix"))
    if prefix:
        normalized = _normalize_operation_path(prefix)
        if normalized:
            return f"OP {normalized}"
    return ""


def derive_canonical_obligation_key(
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """派生义务的 canonical key（SPEC §2.1 身份组成）。

    返回::

        {
          "normalized_operation": "<METHOD> <path>",      # UUID/SKU/nonce 剥离
          "assertion_kind": "...",                          # 断言类别
          "violation_shape": "...",                         # 违反形态
          "relation_type": "...",                           # 关系类型
          "target_field_or_state": "...",                   # 有则带
          "source_contract_semantic_identity": "...",       # 有则带
          "canonical_obligation_key": "...",                # 身份串（SPEC 六元组）
          "coverage_unit_id": "cunit_...",                  # 归并单位 id
        }

    ``coverage_unit_id`` 的归并材质 = canonical 六元组 + 语义等价守卫
    （观察者集合 + 清理语义）：观察者/清理不同的义务绝不归并（fail-closed）。

    角色（required_actors / control / treatment）不进身份——角色变体在
    Coverage Unit 内作为 actor_variants 保留。
    """
    row = dict(obligation)
    normalized_operation = _operation_identity(
        row, behavior_ir=behavior_ir, operation_index=operation_index
    )
    family = _text(row.get("risk_family"))
    assertion_kind = _FAMILY_ASSERTION_KIND.get(family, family or "unspecified")
    violation_shape = _violation_shape_of(row)
    relation_type = _relation_type_of(row)
    target = _target_field_or_state_of(row)
    rule_identity = _source_rule_semantic_identity_of(row)

    components: list[str] = []
    if normalized_operation:
        components.append(f"op:{normalized_operation}")
    else:
        # fail-closed：无操作可解析时用模板+不变式身份兜底，绝不与无关义务误合并
        template = _text(_dict(row.get("property")).get("template")) or "noop"
        invariant_ref = _text(_dict(row.get("property")).get("invariant_ref"))
        components.append(f"noop:{template}:{invariant_ref or 'unbound'}")
    components.append(f"kind:{assertion_kind}")
    components.append(f"shape:{violation_shape}")
    if relation_type:
        components.append(f"rel:{relation_type}")
    if target:
        components.append(f"target:{target}")
    if rule_identity:
        components.append(f"rule:{rule_identity}")
    canonical_key = "|".join(components)

    unit_material = canonical_key + "|" + _observation_semantics_guard(row)
    coverage_unit_id = _COVERAGE_UNIT_ID_PREFIX + _sha256(unit_material)[:20]
    return {
        "normalized_operation": normalized_operation,
        "assertion_kind": assertion_kind,
        "violation_shape": violation_shape,
        "relation_type": relation_type,
        "target_field_or_state": target,
        "source_contract_semantic_identity": rule_identity,
        "canonical_obligation_key": canonical_key,
        "coverage_unit_id": coverage_unit_id,
    }


def attach_canonical_obligation_keys(
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """为义务列表标注 canonical key（幂等：重跑覆盖为同值）。"""
    annotated: list[dict[str, Any]] = []
    for obligation in obligations:
        row = dict(obligation) if isinstance(obligation, dict) else {}
        key = derive_canonical_obligation_key(
            row, behavior_ir=behavior_ir, operation_index=operation_index
        )
        row["canonical_obligation_key"] = key["canonical_obligation_key"]
        row["canonical_key_components"] = {
            component: key[component]
            for component in (
                "normalized_operation",
                "assertion_kind",
                "violation_shape",
                "relation_type",
                "target_field_or_state",
                "source_contract_semantic_identity",
            )
        }
        row["coverage_unit_id"] = key["coverage_unit_id"]
        annotated.append(row)
    return annotated


def _representative_obligation_id(rows: list[dict[str, Any]]) -> str:
    """确定性代表义务：最高 confidence，平局取最小 obligation_id。

    Mirrors the canonical registry's representative choice (highest
    confidence, ties by smallest id) so unit selection never depends on
    list order.
    """
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row.get("confidence") or 0.0),
            _text(row.get("obligation_id")),
        ),
    )
    return _text(ordered[0].get("obligation_id"))


def build_coverage_units(
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any] | None = None,
    operation_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """按 canonical key 把义务归并为 Coverage Units（SPEC §2.2 / §2.3）。

    返回::

        {
          "schema_version": "qualibug.coverage-unit-registry.v1",
          "obligation_count": N,
          "unit_count": M,
          "collapsed_variant_count": N - M,       # 消除的重复变体数
          "average_variants_per_unit": ...,
          "coverage_units": [unit, ...],
        }

    每个 unit::

        {
          "coverage_unit_id": "...",
          "canonical_obligation_key": "...",
          "operation_ref": "...",                 # 代表义务的操作引用
          "property_semantics": {assertion_kind, relation_type, violation_shape,
                                 target_state_or_field, source_contract_semantic_identity},
          "source_rule_refs": [...],              # 去重后的来源规则引用
          "actor_variants": [...],                # 角色变体（证据，非身份）
          "input_variants": [...],                # 输入变体
          "observation_variants": [...],          # 观察者变体
          "obligation_ids": [...],                # 全部变体义务 id
          "representative_obligation_id": "...",
          "variant_count": N,
        }

    无法归并的（语义不同 / 观察者 / 清理不同 → 不同 coverage_unit_id）保持
    独立——fail-closed，聚合不改变断言/观察者/oracle 判定。
    """
    annotated = attach_canonical_obligation_keys(
        obligations, behavior_ir=behavior_ir, operation_index=operation_index
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in annotated:
        unit_id = _text(row.get("coverage_unit_id"))
        if not unit_id:
            unit_id = _text(
                derive_canonical_obligation_key(
                    row, behavior_ir=behavior_ir, operation_index=operation_index
                ).get("coverage_unit_id")
            )
        grouped.setdefault(unit_id, []).append(row)

    units: list[dict[str, Any]] = []
    for unit_id in sorted(grouped):
        rows = sorted(
            grouped[unit_id],
            key=lambda row: _text(row.get("obligation_id")),
        )
        representative_id = _representative_obligation_id(rows)
        representative = next(
            (row for row in rows if _text(row.get("obligation_id")) == representative_id),
            rows[0],
        )
        prop = _dict(representative.get("property"))
        components = dict(_dict(representative.get("canonical_key_components")))
        actor_variants: list[str] = []
        input_variants: list[str] = []
        observation_variants: list[str] = []
        source_rule_refs: list[str] = []
        seen_actors: set[str] = set()
        seen_inputs: set[str] = set()
        seen_observations: set[str] = set()
        seen_rules: set[str] = set()
        for row in rows:
            for actor_ref in _list(row.get("required_actors")):
                actor_id = _text(actor_ref)
                if actor_id and actor_id not in seen_actors:
                    seen_actors.add(actor_id)
                    actor_variants.append(actor_id)
            for observer in _list(row.get("required_observers")):
                observer_id = _text(observer)
                if observer_id and observer_id not in seen_observations:
                    seen_observations.add(observer_id)
                    observation_variants.append(observer_id)
            row_prop = _dict(row.get("property"))
            for key in ("field", "field_path", "json_path", "semantic_validation_constraint"):
                input_value = _text(row_prop.get(key))
                if input_value and input_value not in seen_inputs:
                    seen_inputs.add(input_value)
                    input_variants.append(input_value)
            for rule in _list(row_prop.get("source_rule_refs")):
                rule_id = _text(rule)
                if rule_id and rule_id not in seen_rules:
                    seen_rules.add(rule_id)
                    source_rule_refs.append(rule_id)
        operation_ref = _text(prop.get("operation_ref"))
        if not operation_ref:
            for value in _list(representative.get("required_operations")):
                if _text(value):
                    operation_ref = _text(value)
                    break
        units.append({
            "coverage_unit_id": unit_id,
            "canonical_obligation_key": _text(representative.get("canonical_obligation_key")),
            "operation_ref": operation_ref,
            "property_semantics": {
                "assertion_kind": components.get("assertion_kind", ""),
                "relation_type": components.get("relation_type", ""),
                "violation_shape": components.get("violation_shape", ""),
                "target_state_or_field": components.get("target_field_or_state", ""),
                "source_contract_semantic_identity": components.get(
                    "source_contract_semantic_identity", ""
                ),
            },
            "source_rule_refs": source_rule_refs,
            "actor_variants": sorted(actor_variants),
            "input_variants": sorted(input_variants),
            "observation_variants": sorted(observation_variants),
            "obligation_ids": [_text(row.get("obligation_id")) for row in rows],
            "representative_obligation_id": representative_id,
            "variant_count": len(rows),
        })
    unit_count = len(units)
    return {
        "schema_version": _COVERAGE_UNIT_SCHEMA,
        "obligation_count": len(annotated),
        "unit_count": unit_count,
        "collapsed_variant_count": len(annotated) - unit_count,
        "average_variants_per_unit": round(
            len(annotated) / unit_count, 2
        ) if unit_count else 0.0,
        "max_variants_per_unit": max(
            (int(unit.get("variant_count") or 0) for unit in units),
            default=0,
        ),
        "coverage_units": units,
    }


# ── 多臂 Experiment Bundle（§2.3：一个 Unit 编译一次，N 执行臂）─────────────
# 与 actor_exploration_execution 的 overlay 语义一致的 actor 引用键集合
# （编译期版本的重绑；运行时 overlay 保留原样）。
_ACTOR_SCALAR_KEYS = frozenset({
    "actor_ref",
    "owner_actor_ref",
    "viewer_actor_ref",
    "control_actor_ref",
    "treatment_actor_ref",
    "fixture_owner_actor_ref",
    "resolver_actor_ref",
    "source_actor_ref",
    "cleanup_actor_ref",
    "created_by_actor_ref",
    "actor_id",
})
_ACTOR_LIST_KEYS = frozenset({"required_actors", "actor_refs", "candidate_ids"})


def _actor_refs_in(value: Any, actor_ids: set[str]) -> set[str]:
    """Collect paths where any of ``actor_ids`` appears under actor-keyed fields."""
    stale: set[str] = set()

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, raw in item.items():
                child_path = f"{path}.{key}" if path else key
                if key in _ACTOR_SCALAR_KEYS and _text(raw) in actor_ids:
                    stale.add(child_path)
                elif key in _ACTOR_LIST_KEYS and isinstance(raw, list):
                    if any(_text(value) in actor_ids for value in raw):
                        stale.add(child_path)
                else:
                    visit(raw, child_path)
        elif isinstance(item, list):
            for index, raw in enumerate(item):
                visit(raw, f"{path}[{index}]")

    visit(value, "")
    return stale


def _compiled_actor_refs(experiment: dict[str, Any]) -> dict[str, str]:
    """Resolve a compiled experiment's actor refs (contract-first).

    Compiled experiments carry the actor pair on ``actor_selection_contract``
    (control/treatment/owner/viewer), with the assertion property and plan
    steps carrying the same refs. Resolve contract-first, then assertion
    property, then top-level property — every source must agree on identity
    for an arm rebinding to be safe.
    """
    refs: dict[str, str] = {}
    contract = _dict(experiment.get("actor_selection_contract"))
    for key in (
        "control_actor_ref",
        "treatment_actor_ref",
        "owner_actor_ref",
        "viewer_actor_ref",
        "actor_ref",
    ):
        value = _text(contract.get(key))
        if value:
            refs[key] = value
    if not refs:
        assertions = _list(experiment.get("assertions"))
        for assertion in assertions:
            prop = _dict(_dict(assertion).get("property"))
            for key in (
                "control_actor_ref",
                "treatment_actor_ref",
                "owner_actor_ref",
                "viewer_actor_ref",
                "actor_ref",
            ):
                value = _text(prop.get(key))
                if value:
                    refs[key] = value
            if refs:
                break
    if not refs:
        prop = _dict(experiment.get("property"))
        for key in (
            "control_actor_ref",
            "treatment_actor_ref",
            "owner_actor_ref",
            "viewer_actor_ref",
            "actor_ref",
        ):
            value = _text(prop.get(key))
            if value:
                refs[key] = value
    return refs


def _obligation_actor_refs(obligation: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    prop = _dict(obligation.get("property"))
    for key in (
        "control_actor_ref",
        "treatment_actor_ref",
        "owner_actor_ref",
        "viewer_actor_ref",
        "actor_ref",
    ):
        value = _text(prop.get(key))
        if value:
            refs[key] = value
    return refs


def _single_actor_id(refs: dict[str, str]) -> str:
    """当所有语义角色指向同一 actor 时返回该 actor（单角色调用形态）。"""
    values = {value for value in refs.values() if value}
    return next(iter(values)) if len(values) == 1 else ""


def _arm_mapping(
    rep_refs: dict[str, str],
    arm_refs: dict[str, str],
) -> tuple[dict[str, str], str]:
    """Build the actor rebinding map (rep actor -> arm actor), fail-closed.

    Key alignment handles both actor pair shapes (control/treatment or
    owner/viewer) and the single-actor invocation shape (``actor_ref`` or
    control == treatment): the semantic role is identity, never the JSON key.
    """
    mapping: dict[str, str] = {}

    def _pair(rep_key: str, arm_key: str, fallback_key: str) -> None:
        rep_value = rep_refs.get(rep_key)
        if not rep_value:
            return
        arm_value = arm_refs.get(arm_key) or arm_refs.get(fallback_key)
        if arm_value and rep_value != arm_value:
            mapping[rep_value] = arm_value

    _pair("control_actor_ref", "control_actor_ref", "actor_ref")
    _pair("treatment_actor_ref", "treatment_actor_ref", "actor_ref")
    _pair("owner_actor_ref", "owner_actor_ref", "actor_ref")
    _pair("viewer_actor_ref", "viewer_actor_ref", "actor_ref")
    # 单角色形态：rep 所有角色同一 actor，arm 以 actor_ref 表达同一角色
    rep_single = _single_actor_id(rep_refs)
    arm_single = _single_actor_id(arm_refs)
    if not mapping and rep_single and arm_single and rep_single != arm_single:
        mapping[rep_single] = arm_single
    if not mapping:
        rep_actor = rep_refs.get("actor_ref")
        arm_actor = arm_refs.get("actor_ref")
        if rep_actor and arm_actor and rep_actor != arm_actor:
            mapping[rep_actor] = arm_actor
    return mapping


def derive_arm_experiment(
    experiment: dict[str, Any],
    arm_obligation: dict[str, Any],
    *,
    coverage_unit_id: str = "",
    representative_obligation_id: str = "",
    arm_index: int = 0,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """把一个已编译实验重绑为一个角色变体执行臂（多臂 Bundle 的臂）。

    语义：把代表义务编译实验中的代表 actor 引用（control/treatment/
    owner/viewer/actor）替换为变体义务的对应 actor。与运行时
    ``apply_actor_execution_overlay`` 同一键集合、同一 stale 校验——
    任何残余的代表 actor 引用 → 返回 (None, receipt)，调用方回退独立编译
    （fail-closed，零语义损失）。

    返回 (arm_experiment | None, receipt)。成功时 arm 保留：
    ``coverage_unit_id`` / ``canonical_obligation_key`` / ``arm_index`` /
    ``arm_of``（代表义务 id），compile_receipt 标记 ``arm_derived``。
    """
    row = dict(experiment)
    arm = dict(arm_obligation)
    arm_obligation_id = _text(arm.get("obligation_id"))
    rep_refs = _compiled_actor_refs(row)
    arm_refs = _obligation_actor_refs(arm)
    if not rep_refs or not arm_refs:
        return None, {
            "status": "FAILED",
            "reason": "missing_property",
            "obligation_id": arm_obligation_id,
            "rep_refs": sorted(rep_refs),
            "arm_refs": sorted(arm_refs),
        }
    # ── 形态兼容性（fail-closed）──
    # 单角色调用形态（permitted invocation / credential guard：所有角色同一
    # actor）与双角色形态（control/treatment 或 owner/viewer 对）之间互相
    # 派生会改变断言语义（双角色断言按对编译、单角色断言无对），因此混合
    # 形态不允许重绑——回退独立编译，零语义损失。
    rep_distinct = sorted({value for value in rep_refs.values() if value})
    arm_distinct = sorted({value for value in arm_refs.values() if value})
    if len(rep_distinct) != len(arm_distinct):
        return None, {
            "status": "FAILED",
            "reason": "arm_shape_incompatible",
            "obligation_id": arm_obligation_id,
            "rep_actor_shape": len(rep_distinct),
            "arm_actor_shape": len(arm_distinct),
        }

    mapping: dict[str, str] = _arm_mapping(rep_refs, arm_refs)
    if not mapping:
        return None, {
            "status": "FAILED",
            "reason": "no_actor_delta",
            "obligation_id": arm_obligation_id,
        }
    # 变体义务自己的 actor 引用必须都在重绑目标里（变体 actor 不在代表里则无法派生）
    arm_actor_set = set(arm_refs.values())
    if arm_actor_set and not arm_actor_set.issubset(set(mapping.values()) | set(rep_refs.values())):
        missing = sorted(
            actor
            for actor in arm_actor_set
            if actor not in mapping.values() and actor not in rep_refs.values()
        )
        return None, {
            "status": "FAILED",
            "reason": "variant_actor_not_in_representative",
            "obligation_id": arm_obligation_id,
            "missing_actors": missing,
        }

    bound = deepcopy(row)
    changed_paths: list[str] = []

    def visit(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for key, raw in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in _ACTOR_SCALAR_KEYS and _text(raw) in mapping:
                    output[key] = mapping[_text(raw)]
                    changed_paths.append(child_path)
                elif key in _ACTOR_LIST_KEYS and isinstance(raw, list):
                    replaced = [
                        mapping[_text(item)] if _text(item) in mapping else item
                        for item in raw
                    ]
                    if replaced != raw:
                        changed_paths.append(child_path)
                    output[key] = replaced
                else:
                    output[key] = visit(raw, child_path)
            return output
        if isinstance(value, list):
            return [visit(item, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    bound = visit(bound, "")

    stale = _actor_refs_in(bound, set(mapping))
    if stale:
        return None, {
            "status": "FAILED",
            "reason": "stale_actor_reference",
            "obligation_id": arm_obligation_id,
            "stale_paths": sorted(stale)[:8],
        }

    # 元数据：身份 / 臂归属 / 编译凭证
    source_experiment_id = _text(row.get("experiment_id"))
    material = "|".join([
        source_experiment_id or "exp",
        arm_obligation_id or "obl",
        coverage_unit_id or "unit",
        str(arm_index),
    ])
    bound["obligation_id"] = arm_obligation_id
    bound["experiment_id"] = f"{_text(row.get('experiment_id'))}__arm_{arm_index}" if row.get("experiment_id") else (
        "exp_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    )
    bound["coverage_unit_id"] = coverage_unit_id
    bound["canonical_obligation_key"] = _text(row.get("canonical_obligation_key"))
    bound["arm_index"] = arm_index
    bound["arm_of"] = representative_obligation_id or _text(row.get("obligation_id"))
    bound["arm_serial_group"] = coverage_unit_id
    receipt = dict(_dict(bound.get("compile_receipt")))
    receipt.update({
        "status": "COMPILED",
        "arm_derived": True,
        "source_experiment_id": source_experiment_id,
        "source_obligation_id": _text(row.get("obligation_id")),
        "changed_paths": changed_paths,
    })
    bound["compile_receipt"] = receipt
    return bound, {
        "status": "DERIVED",
        "obligation_id": arm_obligation_id,
        "arm_index": arm_index,
        "source_experiment_id": source_experiment_id,
        "changed_path_count": len(changed_paths),
    }
