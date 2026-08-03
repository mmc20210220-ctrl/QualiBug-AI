"""V1.4.0 Phase 1 Pre-coding: Enterprise Comprehension Gap Map.

Analyzes the current pipeline from enterprise material → Behavior IR → obligations
to identify where field-level information is lost. Produces:
    artifacts/spec_v1_4_0/v140_enterprise_comprehension_gap_map.json

Usage:
    python _v140_gap_map.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT = "benchmark_mall"
OUT_DIR = ROOT / "artifacts" / "spec_v1_4_0"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    return str(v or "").strip()


def load_knowledge_asset() -> dict:
    """Load the enterprise business knowledge asset for benchmark_mall."""
    path = ROOT / "platform_workspace" / PROJECT / "defect_discovery" / "enterprise_business_knowledge_asset.json"
    if not path.exists():
        raise FileNotFoundError(f"Knowledge asset not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_ir(asset: dict) -> dict:
    """Build Behavior IR from the knowledge asset."""
    from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
    return build_behavior_ir_from_knowledge_asset(asset, project_id=PROJECT)


def analyze_asset_fields(asset: dict) -> dict:
    """Analyze field-level information in the knowledge asset."""
    result = {
        "total_business_objects": len(_list(asset.get("business_objects"))),
        "total_data_tables": len(_list(asset.get("data_tables"))),
        "total_interfaces": len(_list(asset.get("interfaces"))),
        "total_rules": len(_list(asset.get("rule_library"))),
        "total_entity_relations": len(_list(asset.get("entity_relations"))),
        "total_permission_rows": len(_list(asset.get("permission_matrix"))),
        "total_state_machines": len(_list(asset.get("state_machines"))),
    }

    # Count fields declared in data_tables
    table_fields_total = 0
    tables_with_fields = 0
    tables_with_field_dict = 0
    for table in _list(asset.get("data_tables")):
        t = _dict(table)
        cols = _list(t.get("columns"))
        fd = _list(t.get("field_dictionary"))
        table_fields_total += len(cols) + len(fd)
        if cols:
            tables_with_fields += 1
        if fd:
            tables_with_field_dict += 1
    result["table_fields_total"] = table_fields_total
    result["tables_with_columns"] = tables_with_fields
    result["tables_with_field_dictionary"] = tables_with_field_dict

    # Count fields in interfaces
    iface_fields_total = 0
    ifaces_with_fields = 0
    for iface in _list(asset.get("interfaces")):
        i = _dict(iface)
        fd = _list(i.get("field_dictionary"))
        params = _list(i.get("parameters"))
        count = len(fd) + len(params)
        iface_fields_total += count
        if count > 0:
            ifaces_with_fields += 1
    result["interface_fields_total"] = iface_fields_total
    result["interfaces_with_fields"] = ifaces_with_fields

    # Count rules with field references
    rules_with_field_ref = 0
    rules_with_entity_ref = 0
    rules_umbrella = 0
    _UMBRELLA_PATTERNS = [
        "数据一致性", "权限安全", "业务流程必须正确", "金额必须准确",
        "系统应保证", "数据安全", "系统稳定", "高可用",
    ]
    for rule in _list(asset.get("rule_library")):
        r = _dict(rule)
        stmt = _text(r.get("statement"))
        # Field reference: backtick-quoted or snake_case
        import re
        has_field = bool(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", stmt))
        has_field = has_field or bool(re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", stmt.lower()))
        if has_field:
            rules_with_field_ref += 1
        # Entity reference
        tokens = _list(r.get("tokens"))
        if any(t for t in tokens if t not in ("amount", "discount", "total", "status")):
            rules_with_entity_ref += 1
        # Umbrella detection
        if any(p in stmt for p in _UMBRELLA_PATTERNS):
            rules_umbrella += 1
    result["rules_with_field_reference"] = rules_with_field_ref
    result["rules_with_entity_reference"] = rules_with_entity_ref
    result["rules_umbrella_candidate"] = rules_umbrella

    return result


def analyze_ir_fields(ir: dict) -> dict:
    """Analyze field-level information in the Behavior IR."""
    result = {
        "total_entities": len(_list(ir.get("entities"))),
        "total_operations": len(_list(ir.get("operations"))),
        "total_invariants": len(_list(ir.get("invariants"))),
        "total_states": len(_list(ir.get("states"))),
        "total_actors": len(_list(ir.get("actors"))),
        "total_relations": len(_list(ir.get("relations"))),
        "total_coverage_gaps": len(_list(ir.get("coverage_gaps"))),
    }

    # Entity field analysis
    entities_with_fields = 0
    total_entity_fields = 0
    fields_are_structured = 0
    fields_are_plain_string = 0
    for ent in _list(ir.get("entities")):
        e = _dict(ent)
        fields = _list(e.get("fields"))
        if fields:
            entities_with_fields += 1
            total_entity_fields += len(fields)
            for f in fields:
                if isinstance(f, dict):
                    fields_are_structured += 1
                else:
                    fields_are_plain_string += 1
    result["entities_with_fields"] = entities_with_fields
    result["total_entity_fields"] = total_entity_fields
    result["fields_structured_dict"] = fields_are_structured
    result["fields_plain_string"] = fields_are_plain_string

    # Invariant field-level analysis
    invariants_with_expression = 0
    invariants_with_field_operands = 0
    invariants_with_operation_refs = 0
    invariants_umbrella = 0
    for inv in _list(ir.get("invariants")):
        i = _dict(inv)
        expr = _dict(i.get("expression"))
        if expr:
            invariants_with_expression += 1
        operands = _list(expr.get("operands"))
        has_field = any(_text(_dict(op).get("field")) for op in operands)
        if has_field:
            invariants_with_field_operands += 1
        if _list(i.get("operation_refs")):
            invariants_with_operation_refs += 1
        # Umbrella detection
        desc = _text(i.get("description"))
        _UMBRELLA = ["数据一致性", "权限安全", "业务流程必须正确", "金额必须准确", "系统应保证"]
        if any(p in desc for p in _UMBRELLA):
            invariants_umbrella += 1
    result["invariants_with_expression"] = invariants_with_expression
    result["invariants_with_field_operands"] = invariants_with_field_operands
    result["invariants_with_operation_refs"] = invariants_with_operation_refs
    result["invariants_umbrella"] = invariants_umbrella

    # Operation schema analysis
    ops_with_request_schema = 0
    ops_with_response_schema = 0
    ops_with_field_dictionary = 0
    for op in _list(ir.get("operations")):
        o = _dict(op)
        if _dict(o.get("request_schema")) or _dict(o.get("requestBody")):
            ops_with_request_schema += 1
        if _dict(o.get("response_schema")) or _dict(o.get("responseSchema")):
            ops_with_response_schema += 1
        if _list(o.get("field_dictionary")):
            ops_with_field_dictionary += 1
    result["ops_with_request_schema"] = ops_with_request_schema
    result["ops_with_response_schema"] = ops_with_response_schema
    result["ops_with_field_dictionary"] = ops_with_field_dictionary

    return result


def compute_gap(asset_stats: dict, ir_stats: dict) -> list[dict]:
    """Compute information loss points between asset and IR."""
    gaps = []

    # Gap 1: Field semantic typing
    gaps.append({
        "capability": "Field语义分类",
        "current_module": "behavior_ir.py (entity.fields)",
        "current_input": "knowledge_asset.data_tables[].columns + field_dictionary",
        "current_output": "plain string list (field names only)",
        "information_loss": "字段语义类型(AMOUNT/STATE/IDENTITY/SCOPE等)完全丢失；"
                           "data_type、nullable、enum_values、direction 未进入 IR；"
                           "parameter_value_classifier.py 有分类能力但只在运行时使用不回写 IR",
        "fix_approach": "在 build_behavior_ir_from_knowledge_asset() 实体构建阶段，"
                       "将 fields 从 string[] 升级为 canonical_field dict[]，"
                       "复用 parameter_value_classifier + project_context_compiler 分类逻辑",
    })

    # Gap 2: Field binding
    gaps.append({
        "capability": "Field多层Binding",
        "current_module": "无统一模块",
        "current_input": "operations[].request_schema + data_tables[].columns (分散)",
        "current_output": "无绑定关系输出",
        "information_loss": "API字段(request/response)和DB字段(table.column)没有统一映射；"
                           "oracle_expression_resolver 从 raw text 猜测字段归属；"
                           "runtime_binding_materializer 无法知道字段在哪个 API path 可观察",
        "fix_approach": "在 behavior_ir.py 实体构建后新增 _build_field_bindings()，"
                       "基于 operation.entity_refs + schema.properties + data_tables 建立绑定",
    })

    # Gap 3: Rule-Operation binding
    gaps.append({
        "capability": "Rule与Operation绑定",
        "current_module": "behavior_ir.py (invariant construction) + obligation_compiler_base.py",
        "current_input": "rule_library[].statement + interfaces[].path",
        "current_output": "invariants[].operation_refs (token overlap matching)",
        "information_loss": "绑定仅靠 token overlap (50% hit rate)；"
                           "无 Entity Signal + Action/State/Field Signal 双信号约束；"
                           "宽泛 Umbrella Rule 可能进入执行链",
        "fix_approach": "增强 invariant 构建阶段的 operation 绑定：要求双信号；"
                       "检测并标记 Umbrella Rule；通过 canonical field 建立 field→operation 绑定",
    })

    # Gap 4: Scope fields
    gaps.append({
        "capability": "Scope字段形式化",
        "current_module": "actor_matrix_planning.py (runtime inference)",
        "current_input": "permission_matrix[].scope + entity_relations",
        "current_output": "运行时猜测 owner_field/tenant_field",
        "information_loss": "permission_matrix 有 scope 信息(own_tenant/assigned_tenant)但不进入 IR；"
                           "entity_relations 有 owns/belongs_to 但不提取 scope_field；"
                           "actor_matrix_planning 在运行时用 heuristic 猜测",
        "fix_approach": "在 behavior_ir.py 实体构建阶段从 permission_matrix + entity_relations "
                       "提取 scope_fields 写入实体 typed_fields",
    })

    # Gap 5: _ENTITY_FIELD_REGISTRY not persisted
    gaps.append({
        "capability": "Entity-Field Registry持久化",
        "current_module": "behavior_ir.py L3229 (_ENTITY_FIELD_REGISTRY)",
        "current_input": "model.entities[].fields + operations[].schema",
        "current_output": "函数局部变量，IR构建完成后丢弃",
        "information_loss": "每次规则解析都要重建 registry；"
                           "下游 obligation_compiler 无法访问字段→实体映射；"
                           "oracle_expression_resolver 独立重建 vocabulary",
        "fix_approach": "将 registry 作为 IR model 的 capabilities 节点持久化，"
                       "或直接由 canonical field 结构替代（字段已在实体内声明）",
    })

    # Gap 6: Field conflict resolution
    gaps.append({
        "capability": "字段冲突检测",
        "current_module": "无",
        "current_input": "多来源声明同一字段（API schema vs DB DDL vs PRD）",
        "current_output": "静默覆盖或合并",
        "information_loss": "当 API schema 声明 amount 为 integer 但 DB 为 decimal(18,2) 时，"
                           "当前 merge_unique 只去重不检测类型冲突；"
                           "cross_document_conflicts 在 asset 中存在但不进入 IR",
        "fix_approach": "在 canonical field 构建时检测多来源冲突，"
                       "设置 conflict_status 字段，冲突字段标记为 AMBIGUOUS",
    })

    return gaps


def main() -> int:
    print("[V1.4.0 Gap Map] Loading knowledge asset...")
    t0 = time.time()
    asset = load_knowledge_asset()

    print("[V1.4.0 Gap Map] Analyzing asset field-level data...")
    asset_stats = analyze_asset_fields(asset)

    print("[V1.4.0 Gap Map] Building Behavior IR...")
    ir = build_ir(asset)

    print("[V1.4.0 Gap Map] Analyzing IR field-level data...")
    ir_stats = analyze_ir_fields(ir)

    print("[V1.4.0 Gap Map] Computing information loss gaps...")
    gaps = compute_gap(asset_stats, ir_stats)

    gap_map = {
        "schema": "qualibug.v140-enterprise-comprehension-gap-map.v1",
        "project_id": PROJECT,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "asset_statistics": asset_stats,
        "ir_statistics": ir_stats,
        "information_loss_points": gaps,
        "answers": {
            "field_info_loss_stage": "behavior_ir.py entity 构建阶段：fields 只保留名称字符串，"
                                     "丢弃 data_type/semantic_type/scope_role/direction 等全部语义",
            "nl_rule_no_structured_expression": "规则 statement 是自然语言，当前只用 regex 提取 backtick 字段名；"
                                               "无 NLP→结构化公式转换；oracle_expression_resolver 依赖 IR 实体词汇表但字段级不足",
            "state_subject_generic_placeholder": "state_machines 只有 1 个（orders），其他实体状态从 rule 推断；"
                                               "缺少 entity→state_field 显式声明导致状态主体变成通用 'entity'",
            "permission_scope_missing_tenant_owner": "permission_matrix 有 scope 列(own_tenant等)但 IR 构建不读取；"
                                                    "entity_relations 有 owns 关系但不提取 owner_field",
            "rule_cannot_bind_operation": "token overlap 匹配率不足；规则无显式 entity_ref + operation_ref；"
                                         "obligation_compiler entity-co-reference fallback 置信度仅 0.4",
            "operation_cannot_bind_field": "operations 有 request_schema 但字段不映射到实体 canonical field；"
                                          "field_dictionary 在 asset 中存在但 IR 构建只取名称",
            "api_db_field_no_canonical": "API 字段在 operations[].schema，DB 字段在 data_tables[].columns，"
                                        "两者通过实体名称间接关联但无统一 field_id",
            "silent_conflict_overwrite": "merge_unique 只去重不检测类型/语义冲突；"
                                        "cross_document_conflicts 在 asset 中存在但不进入 IR",
            "umbrella_rules_remaining": "宽泛规则如'数据一致性'可能被 token overlap 绑定到操作并进入执行",
            "llm_candidates_no_second_evidence": "semantic_candidates 标记为 model-inferred 但无独立验证；"
                                                "derivation='model-inferred' 的 invariant 缺少第二证据源",
        },
        "fix_priority": [
            "1. Canonical Field 模型 (Step 1) — 解决字段语义丢失根因",
            "2. Field Binding (Step 2) — 解决 API/DB 字段无统一映射",
            "3. Rule-Operation 双信号 (Step 3) — 解决绑定不可靠",
            "4. Scope 形式化 (Step 4) — 解决运行时猜测",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "v140_enterprise_comprehension_gap_map.json"
    out_path.write_text(json.dumps(gap_map, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - t0
    print(f"[V1.4.0 Gap Map] Done in {elapsed:.1f}s")
    print(f"[V1.4.0 Gap Map] Output: {out_path}")
    print(f"  Asset: {asset_stats['total_business_objects']} entities, "
          f"{asset_stats['table_fields_total']} table fields, "
          f"{asset_stats['interface_fields_total']} interface fields, "
          f"{asset_stats['total_rules']} rules")
    print(f"  IR: {ir_stats['total_entities']} entities, "
          f"{ir_stats['total_entity_fields']} entity fields "
          f"({ir_stats['fields_plain_string']} plain string, {ir_stats['fields_structured_dict']} structured), "
          f"{ir_stats['total_invariants']} invariants "
          f"({ir_stats['invariants_with_field_operands']} with field operands, "
          f"{ir_stats['invariants_with_operation_refs']} with operation refs)")
    print(f"  Gaps: {len(gaps)} information loss points identified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
