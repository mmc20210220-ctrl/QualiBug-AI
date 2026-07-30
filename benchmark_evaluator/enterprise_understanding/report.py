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
    ]
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
        "只修改最高影响根因所在的现有主链模块；不在下游补结果，不新增第二套事实、对象、流程、Behavior、Evidence、Unknown、Conflict或Gate。",
        "",
    ])
    return "\n".join(lines)


def write_benchmark_outputs(result: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    alignment = result.get("alignment") if isinstance(result.get("alignment"), dict) else {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    root_causes = result.get("root_cause_analysis") if isinstance(result.get("root_cause_analysis"), dict) else {}
    alignments = _rows(alignment.get("alignments"))
    files: dict[str, Any] = {
        "workflow_receipt.json": result.get("workflow_receipt") or {},
        "ground_truth_summary.json": result.get("ground_truth_summary") or {},
        "understanding_alignment.json": alignment,
        "metric_summary.json": metrics,
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
