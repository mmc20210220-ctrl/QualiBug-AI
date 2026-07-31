"""Evaluator-side machine and human reports for enterprise understanding."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _percent(value: Any) -> str:
    if value is None:
        return "不可测"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "不可测"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def render_markdown_report(result: dict[str, Any]) -> str:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    root_causes = result.get("root_cause_analysis") if isinstance(result.get("root_cause_analysis"), dict) else {}
    fact_slots = (
        result.get("business_fact_slot_measurement")
        if isinstance(result.get("business_fact_slot_measurement"), dict)
        else {}
    )
    fact_slot_metrics = (
        fact_slots.get("metrics")
        if isinstance(fact_slots.get("metrics"), dict)
        else {}
    )
    implicit_rules = (
        result.get("implicit_rule_measurement")
        if isinstance(result.get("implicit_rule_measurement"), dict)
        else {}
    )
    implicit_rule_metrics = (
        implicit_rules.get("metrics")
        if isinstance(implicit_rules.get("metrics"), dict)
        else {}
    )
    ingestion = (
        result.get("ingestion_evidence_measurement")
        if isinstance(result.get("ingestion_evidence_measurement"), dict)
        else {}
    )
    ingestion_summary = ingestion.get("summary") if isinstance(ingestion.get("summary"), dict) else {}
    evidence_address = (
        ingestion.get("evidence_address_analysis")
        if isinstance(ingestion.get("evidence_address_analysis"), dict)
        else {}
    )
    structure_loss = (
        ingestion.get("structure_loss_analysis")
        if isinstance(ingestion.get("structure_loss_analysis"), dict)
        else {}
    )
    document_ground_truth = (
        ingestion.get("document_ground_truth_measurement")
        if isinstance(ingestion.get("document_ground_truth_measurement"), dict)
        else {}
    )
    document_metrics = (
        document_ground_truth.get("metrics")
        if isinstance(document_ground_truth.get("metrics"), dict)
        else {}
    )
    false_confirmation = metrics.get("false_confirmation_metrics") if isinstance(metrics.get("false_confirmation_metrics"), dict) else {}
    bug_metrics = metrics.get("bug_dependency_metrics") if isinstance(metrics.get("bug_dependency_metrics"), dict) else {}
    weighted = metrics.get("critical_rule_weighted_recall") if isinstance(metrics.get("critical_rule_weighted_recall"), dict) else {}
    evidence = metrics.get("source_evidence_metrics") if isinstance(metrics.get("source_evidence_metrics"), dict) else {}
    lines = [
        f"# QualiBug 企业业务理解 Benchmark：{_text(result.get('project_id'))}",
        "",
        f"- Benchmark状态：`{_text(result.get('status'))}`",
        f"- Ground Truth指纹：`{_text(result.get('ground_truth_fingerprint'))}`",
        f"- 产品资产指纹：`{_text(result.get('product_asset_fingerprint'))}`",
        f"- 最高影响根因：`{_text(root_causes.get('highest_impact_root_cause')) or '无'}`",
        f"- 隐式规则下一修复点：`{_text(result.get('next_implicit_rule_repair_target')) or '无'}`",
        f"- 文档接入/证据下一修复点：`{_text(result.get('next_ingestion_repair_target')) or '无'}`",
        "- 指标权威：Evaluator侧人工来源化Ground Truth；产品不能自评真伪。",
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 业务对象召回率 | {_percent(metrics.get('business_object_recall'))} |",
        f"| 角色召回率 | {_percent(metrics.get('actor_recall'))} |",
        f"| 操作召回率 | {_percent(metrics.get('operation_recall'))} |",
        f"| 操作—对象绑定准确率 | {_percent(metrics.get('operation_object_binding_accuracy'))} |",
        f"| 业务规则召回率 | {_percent(metrics.get('business_rule_recall'))} |",
        f"| Business Behavior召回率 | {_percent(metrics.get('business_behavior_recall'))} |",
        f"| 状态转换召回率 | {_percent(metrics.get('state_transition_recall'))} |",
        f"| 冲突暴露率 | {_percent(metrics.get('conflict_exposure_rate'))} |",
        f"| Expected Unknown暴露率 | {_percent(metrics.get('expected_unknown_exposure_rate'))} |",
        f"| P0/P1加权严格召回率 | {_percent(weighted.get('strict_weighted_recall'))} |",
        f"| 来源证据准确率 | {_percent(evidence.get('source_evidence_accuracy'))} |",
        f"| Bug依赖规则完整覆盖率 | {_percent(bug_metrics.get('bug_dependency_rule_coverage_rate'))} |",
        f"| 假确定率 | {_percent(false_confirmation.get('false_confirmation_rate'))} |",
        f"| 本应解决但仍暴露的Unknown | {int(metrics.get('unexpected_unknown_count') or 0)} |",
        "",
        "## 中文显式业务事实槽位测量",
        "",
        f"- 测量状态：`{_text(fact_slots.get('status')) or 'NOT_MEASURED'}`",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 人工标注事实数 | {int(fact_slot_metrics.get('annotated_fact_count') or 0)} |",
        f"| 显式事实召回率 | {_percent(fact_slot_metrics.get('fact_recall'))} |",
        f"| 完整事实精确率 | {_percent(fact_slot_metrics.get('exact_fact_rate'))} |",
        f"| 槽位精确准确率 | {_percent(fact_slot_metrics.get('slot_exact_accuracy'))} |",
        f"| P0显式事实精确召回率 | {_percent(fact_slot_metrics.get('p0_exact_fact_recall'))} |",
        f"| 精确地址标注事实数 | {int(fact_slot_metrics.get('source_locator_annotated_fact_count') or 0)} |",
        f"| 精确地址命中事实数 | {int(fact_slot_metrics.get('source_locator_exact_fact_count') or 0)} |",
        f"| 事实级精确证据地址准确率 | {_percent(fact_slot_metrics.get('source_locator_exact_accuracy'))} |",
        f"| 完整范围内ACCEPTED事实数 | {int(fact_slot_metrics.get('accepted_fact_count_in_scope') or 0)} |",
        f"| ACCEPTED事实Precision | {_percent(fact_slot_metrics.get('accepted_fact_precision'))} |",
        f"| 误接受率 | {_percent(fact_slot_metrics.get('false_accepted_rate'))} |",
        f"| 误接受事实数 | {int(fact_slot_metrics.get('false_accepted_fact_count') or 0)} |",
        f"| 缺失事实 | {int(fact_slot_metrics.get('missing_fact_count') or 0)} |",
        f"| 歧义事实 | {int(fact_slot_metrics.get('ambiguous_fact_count') or 0)} |",
        "",
        "## 隐式规则治理质量测量",
        "",
        f"- 测量状态：`{_text(implicit_rules.get('status')) or 'NOT_MEASURED'}`",
        f"- 下一修复点：`{_text(implicit_rules.get('next_repair_target')) or '无'}`",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 候选发现精确率 | {_percent(implicit_rule_metrics.get('candidate_precision'))} |",
        f"| 候选发现召回率 | {_percent(implicit_rule_metrics.get('candidate_recall'))} |",
        f"| 权威晋升精确率 | {_percent(implicit_rule_metrics.get('promotion_precision'))} |",
        f"| 权威晋升召回率 | {_percent(implicit_rule_metrics.get('promotion_recall'))} |",
        f"| 误晋升率 | {_percent(implicit_rule_metrics.get('overpromotion_rate'))} |",
        f"| P0/P1加权误晋升率 | {_percent(implicit_rule_metrics.get('criticality_weighted_overpromotion_rate'))} |",
        f"| 生命周期准确率 | {_percent(implicit_rule_metrics.get('lifecycle_accuracy'))} |",
        f"| STALE精确率 | {_percent(implicit_rule_metrics.get('stale_precision'))} |",
        f"| STALE召回率 | {_percent(implicit_rule_metrics.get('stale_recall'))} |",
        f"| 来源版本可追溯率 | {_percent(implicit_rule_metrics.get('source_version_traceability_rate'))} |",
        f"| 权威接口绑定召回率 | {_percent(implicit_rule_metrics.get('authoritative_operation_binding_recall'))} |",
        f"| Oracle投影召回率 | {_percent(implicit_rule_metrics.get('oracle_projection_recall'))} |",
        f"| 可执行投影召回率 | {_percent(implicit_rule_metrics.get('executable_projection_recall'))} |",
        f"| 运行观察召回率 | {_percent(implicit_rule_metrics.get('runtime_observation_recall'))} |",
        f"| 误晋升规则数 | {int(implicit_rule_metrics.get('promotion_false_positive_count') or 0)} |",
        f"| 漏发现规则数 | {int(implicit_rule_metrics.get('candidate_false_negative_count') or 0)} |",
        "",
        "## 多源接入与证据定位回执测量",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 活跃来源结构覆盖率 | {_percent(ingestion_summary.get('source_structure_coverage_rate'))} |",
        f"| 接入接受率 | {_percent(ingestion_summary.get('ingestion_acceptance_rate'))} |",
        f"| 结构完整率 | {_percent(ingestion_summary.get('structure_complete_rate'))} |",
        f"| 正式块来源哈希绑定率 | {_percent(evidence_address.get('source_hash_binding_rate'))} |",
        f"| 正式块来源可追溯率 | {_percent(evidence_address.get('source_traceability_rate'))} |",
        f"| 正式块精确地址率 | {_percent(evidence_address.get('exact_address_rate'))} |",
        f"| 未解决关键结构缺口 | {int(ingestion_summary.get('critical_structure_gap_count') or 0)} |",
        f"| 静默丢失风险来源 | {int(structure_loss.get('silent_loss_risk_source_count') or 0)} |",
        f"| 回执完整性门禁 | {'通过' if ingestion_summary.get('receipt_integrity_gate_pass') else '未通过'} |",
        "",
        "## 人工Ground Truth真实结构测量",
        "",
        f"- Profile状态：`{_text(document_ground_truth.get('status')) or 'NOT_DECLARED'}`",
        f"- Profile最高影响缺口：`{_text(document_ground_truth.get('highest_impact_gap')) or '无'}`",
        f"- Profile范围完整：`{bool(document_ground_truth.get('scope_complete'))}`",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 标注来源召回率 | {_percent(document_metrics.get('source_recall'))} |",
        f"| 结构元素严格召回率 | {_percent(document_metrics.get('strict_structure_element_recall'))} |",
        f"| 结构元素覆盖召回率 | {_percent(document_metrics.get('coverage_structure_element_recall'))} |",
        f"| 结构块类型准确率 | {_percent(document_metrics.get('block_type_accuracy'))} |",
        f"| 精确证据地址准确率 | {_percent(document_metrics.get('exact_evidence_address_accuracy'))} |",
        f"| 表格单元格召回率 | {_percent(document_metrics.get('table_cell_recall'))} |",
        f"| 阅读顺序准确率 | {_percent(document_metrics.get('reading_order_accuracy'))} |",
        "",
    ]
    if not fact_slot_metrics:
        lines.extend(
            [
                "> 中文显式事实槽位准确率尚不可测：必须声明并通过人工来源化槽位 Ground Truth。产品事实不能反向生成真值。",
                "",
            ]
        )
    if not implicit_rule_metrics:
        lines.extend(
            [
                "> 隐式规则Precision/Recall尚不可测：必须冻结完整候选宇宙，并由人工逐条标注真规则、硬负例、预期生命周期和执行要求。产品候选与运行结果不能反向生成真值。",
                "",
            ]
        )
    if not document_metrics.get("true_structure_recall_measured"):
        lines.extend(
            [
                "> 真实结构召回率尚不可测：必须在同一Evaluator Ground Truth中声明并完成人工文档结构标注。产品回执不能替代人工真值。",
                "",
            ]
        )
    elif document_ground_truth.get("profile_five_of_five_pass"):
        lines.extend(
            [
                "> 当前声明的文档Profile已达到5/5门槛；该结论只适用于已声明语料范围，不代表全行业通用覆盖已经证明。",
                "",
            ]
        )
    if false_confirmation.get("status") != "MEASURABLE":
        lines.extend([
            "> 假确定率不可测：Ground Truth必须验证通过并声明 `scope_complete=true`。",
            "",
        ])
    lines.extend(["## 根因排名", "", "| 排名 | 根因 | 漏项 | 加权影响 |", "|---:|---|---:|---:|"])
    ranked = _rows(root_causes.get("ranked_root_causes"))
    for index, row in enumerate(ranked, start=1):
        lines.append(
            f"| {index} | `{_text(row.get('root_cause'))}` | {int(row.get('miss_count') or 0)} | {float(row.get('criticality_weighted_impact') or 0):.1f} |"
        )
    if not ranked:
        lines.append("| 1 | 无已识别漏项 | 0 | 0.0 |")
    lines.extend(["", "## 未覆盖Bug依赖", ""])
    incomplete_bugs = [
        row for row in _rows(root_causes.get("bug_dependency_root_causes"))
        if not row.get("understanding_chain_complete")
    ]
    if incomplete_bugs:
        for row in incomplete_bugs:
            lines.append(
                f"- `{_text(row.get('bug_id'))}`：最早断点 `{_text(row.get('earliest_root_cause'))}`；缺少依赖 {', '.join(row.get('missing_dependency_ids') or [])}"
            )
    else:
        lines.append("- 当前已标注Bug依赖未发现企业理解链断点。")
    lines.extend([
        "",
        "## 下一步修复原则",
        "",
        "只修改最高影响根因所在的现有主链模块；不在下游补结果，不新增第二套事实、对象、流程、Behavior、Evidence、Unknown、Conflict、Gate或Benchmark。",
        "",
    ])
    return "\n".join(lines)


def write_benchmark_outputs(result: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    alignment = result.get("alignment") if isinstance(result.get("alignment"), dict) else {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    root_causes = result.get("root_cause_analysis") if isinstance(result.get("root_cause_analysis"), dict) else {}
    fact_slots = (
        result.get("business_fact_slot_measurement")
        if isinstance(result.get("business_fact_slot_measurement"), dict)
        else {}
    )
    implicit_rules = (
        result.get("implicit_rule_measurement")
        if isinstance(result.get("implicit_rule_measurement"), dict)
        else {}
    )
    ingestion = (
        result.get("ingestion_evidence_measurement")
        if isinstance(result.get("ingestion_evidence_measurement"), dict)
        else {}
    )
    document_ground_truth = (
        ingestion.get("document_ground_truth_measurement")
        if isinstance(ingestion.get("document_ground_truth_measurement"), dict)
        else {}
    )
    alignments = _rows(alignment.get("alignments"))
    files: dict[str, Any] = {
        "workflow_receipt.json": result.get("workflow_receipt") or {},
        "ground_truth_summary.json": result.get("ground_truth_summary") or {},
        "understanding_alignment.json": alignment,
        "metric_summary.json": metrics,
        "business_fact_slot_measurement.json": fact_slots,
        "business_fact_slot_alignments.json": fact_slots.get("alignments") or [],
        "business_fact_false_accepted.json": fact_slots.get("false_accepted_facts") or [],
        "implicit_rule_measurement.json": implicit_rules,
        "implicit_rule_metrics.json": implicit_rules.get("metrics") or {},
        "implicit_rule_alignments.json": implicit_rules.get("alignments") or [],
        "implicit_rule_false_promotions.json": implicit_rules.get("false_promotions") or [],
        "implicit_rule_missed_rules.json": implicit_rules.get("missed_rules") or [],
        "implicit_rule_lifecycle_errors.json": implicit_rules.get("lifecycle_errors") or [],
        "implicit_rule_execution_bridge_gaps.json": implicit_rules.get("execution_bridge_gaps") or [],
        "ingestion_metric_summary.json": ingestion.get("summary") or {},
        "evidence_address_analysis.json": ingestion.get("evidence_address_analysis") or {},
        "structure_loss_analysis.json": ingestion.get("structure_loss_analysis") or {},
        "format_coverage_analysis.json": ingestion.get("format_coverage_analysis") or {},
        "document_structure_ground_truth_alignment.json": {
            "status": document_ground_truth.get("status"),
            "validation_status": document_ground_truth.get("validation_status"),
            "scope_complete": document_ground_truth.get("scope_complete"),
            "highest_impact_gap": document_ground_truth.get("highest_impact_gap"),
            "source_alignments": document_ground_truth.get("source_alignments") or [],
            "element_alignments": document_ground_truth.get("element_alignments") or [],
            "reading_order_alignments": document_ground_truth.get("reading_order_alignments") or [],
            "gap_distribution": document_ground_truth.get("gap_distribution") or [],
        },
        "document_structure_ground_truth_metrics.json": document_ground_truth.get("metrics") or {},
        "missed_objects.json": [row for row in alignments if row.get("collection") == "business_objects" and row.get("alignment_status") != "EXACT_MATCH"],
        "missed_operations.json": [row for row in alignments if row.get("collection") == "operations" and row.get("alignment_status") != "EXACT_MATCH"],
        "missed_rules.json": [row for row in alignments if row.get("collection") in {"business_rules", "business_behaviors"} and row.get("alignment_status") != "EXACT_MATCH"],
        "false_confirmations.json": metrics.get("false_confirmation_metrics") or {},
        "unknown_analysis.json": {
            "expected_unknown_alignments": [row for row in alignments if row.get("collection") == "expected_unknowns"],
            "unexpected_unknowns": alignment.get("unexpected_unknowns") or [],
        },
        "conflict_analysis.json": [row for row in alignments if row.get("collection") == "conflicts"],
        "bug_dependency_analysis.json": metrics.get("bug_dependency_metrics") or {},
        "root_cause_distribution.json": root_causes,
    }
    paths: dict[str, str] = {}
    for filename, value in files.items():
        path = root / filename
        _write_json(path, value)
        paths[filename] = str(path)
    report_path = root / "report.md"
    report_path.write_text(render_markdown_report(result), encoding="utf-8")
    paths["report.md"] = str(report_path)
    return paths


__all__ = ["render_markdown_report", "write_benchmark_outputs"]
