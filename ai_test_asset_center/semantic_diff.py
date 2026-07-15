from __future__ import annotations

"""
Semantic Document Diff & Intelligent Change Prioritization.

Advanced upgrade over hash-based watching:
1. Semantic diff: understand WHAT changed, not just THAT it changed
2. LLM-powered impact analysis: which oracles/engines are affected?
3. Priority scoring: which changes matter most?
4. Git hook integration: pre-commit + post-merge automation

Combined with CI integration:
5. AST-level change impact: Python/JS/Java code change analysis
6. PR risk scoring: ML-free heuristic scoring based on change patterns
"""

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Semantic Document Diff
# ---------------------------------------------------------------------------

def semantic_diff(before: str, after: str) -> dict[str, Any]:
    """Analyze WHAT changed semantically, not just line-by-line.

    Categories:
    - new_requirement: new business rule added
    - modified_requirement: existing rule changed
    - removed_requirement: rule deleted
    - new_endpoint: new API path added
    - modified_endpoint: API path changed
    - schema_change: response/request schema modified
    - parameter_change: query/path parameter changed
    - structural_change: document restructured
    """
    changes: list[dict[str, Any]] = []
    before_lines = set(before.split("\n"))
    after_lines = set(after.split("\n"))
    added = after_lines - before_lines
    removed = before_lines - after_lines

    # Classify changes
    for line in added:
        if _is_endpoint_definition(line):
            changes.append({"type": "new_endpoint", "line": line[:200], "impact": "high"})
        elif _is_business_rule(line):
            changes.append({"type": "new_requirement", "line": line[:200], "impact": "high"})
        elif _is_schema_field(line):
            changes.append({"type": "schema_change", "line": line[:200], "impact": "medium"})
        elif _is_parameter(line):
            changes.append({"type": "parameter_change", "line": line[:200], "impact": "medium"})
        else:
            changes.append({"type": "other_addition", "line": line[:200], "impact": "low"})

    for line in removed:
        changes.append({"type": "removed", "line": line[:200], "impact": "medium"})

    # Impact scoring
    high = sum(1 for c in changes if c["impact"] == "high")
    medium = sum(1 for c in changes if c["impact"] == "medium")
    low = sum(1 for c in changes if c["impact"] == "low")

    return {
        "total_changes": len(changes),
        "high_impact": high,
        "medium_impact": medium,
        "low_impact": low,
        "priority_score": min(100, high * 30 + medium * 10 + low * 2),
        "changes": changes[:50],
        "affected_engines": _infer_affected_engines(changes),
    }


def _is_endpoint_definition(line: str) -> bool:
    return bool(re.match(r"(GET|POST|PUT|DELETE|PATCH)\s+/\S", line.strip(), re.I))


def _is_business_rule(line: str) -> bool:
    indicators = ["must", "shall", "should", "required", "必须", "禁止", "不得", "需要",
                   "约束", "规则", "不变量", "invariant", "constraint", "不能", "不允许"]
    line_lower = line.lower()
    return any(ind in line_lower for ind in indicators) and len(line.strip()) > 20


def _is_schema_field(line: str) -> bool:
    return bool(re.match(r'\s*"?\w+"?\s*:', line.strip()))


def _is_parameter(line: str) -> bool:
    return bool(re.search(r"(parameter|query|path|header|body)\b", line, re.I))


def _infer_affected_engines(changes: list[dict[str, Any]]) -> list[str]:
    """Infer which reasoning engines are affected by the changes."""
    engine_keywords = {
        "causality": ["payment", "refund", "order", "paid", "amount", "收到", "支付", "退款", "金额"],
        "reconciliation": ["dashboard", "report", "summary", "stat", "统计", "汇总", "报表"],
        "invariant": ["schema", "required", "enum", "type", "constraint", "约束", "类型"],
        "lifecycle": ["status", "state", "phase", "transition", "状态", "流转", "阶段"],
        "saga": ["compensation", "rollback", "saga", "补偿", "回滚"],
        "consistency": ["tenant", "isolation", "consistent", "租户", "隔离"],
        "event_chain": ["event", "message", "queue", "topic", "事件", "消息", "队列"],
    }
    text = " ".join(c["line"] for c in changes).lower()
    affected = []
    for engine, keywords in engine_keywords.items():
        if any(kw in text for kw in keywords):
            affected.append(engine)
    return affected or ["all"]


# ---------------------------------------------------------------------------
# AST-level Change Impact Analysis
# ---------------------------------------------------------------------------

def analyze_code_change_impact(
    diff_output: str,
    file_path: str = "",
) -> dict[str, Any]:
    """Analyze a git diff for code-level impact.

    Detects:
    - Function signature changes → high impact
    - Schema/DB migration changes → high impact  
    - Import changes → medium impact
    - Logic changes in core modules → medium impact
    - Test-only changes → low impact
    - Doc-only changes → minimal impact
    """
    if not diff_output.strip():
        return {"impact": "none", "risk": 0, "changes": []}

    changes: list[dict[str, Any]] = []
    risk = 0

    # Function signature changes
    func_changes = re.findall(r'^[-+]\s*def\s+(\w+)\s*\(', diff_output, re.MULTILINE)
    if func_changes:
        changes.append({"type": "function_changed", "functions": list(set(func_changes)), "impact": "high"})
        risk += 30

    # Class changes
    class_changes = re.findall(r'^[-+]\s*class\s+(\w+)', diff_output, re.MULTILINE)
    if class_changes:
        changes.append({"type": "class_changed", "classes": list(set(class_changes)), "impact": "high"})
        risk += 25

    # Import changes
    import_changes = re.findall(r'^[-+]\s*(from|import)\s+', diff_output, re.MULTILINE)
    if import_changes:
        changes.append({"type": "import_changed", "count": len(import_changes), "impact": "medium"})
        risk += 10

    # SQL/Schema changes
    if re.search(r'(CREATE|ALTER|DROP|migration)', diff_output, re.I):
        changes.append({"type": "schema_change", "impact": "high"})
        risk += 35

    # Environment/config changes
    if re.search(r'(\.env|config|setting)', diff_output, re.I):
        changes.append({"type": "config_change", "impact": "medium"})
        risk += 15

    # Test-only changes
    if "test" in file_path.lower() and not any(c["impact"] == "high" for c in changes):
        changes.append({"type": "test_only", "impact": "low"})
        risk = max(0, risk - 10)

    return {
        "file": file_path,
        "impact": "high" if risk > 30 else "medium" if risk > 10 else "low",
        "risk_score": min(100, risk),
        "changes": changes,
    }


def pr_risk_score(changed_files: list[str], diffs: dict[str, str]) -> dict[str, Any]:
    """Calculate overall risk score for a pull request.

    Factors:
    - Number of files changed (more files = higher risk)
    - Types of files (.py > .md for risk)
    - AST-level impact per file
    - Cross-service blast radius
    """
    total_risk = 0
    file_analyses = []

    for f in changed_files:
        diff = diffs.get(f, "")
        analysis = analyze_code_change_impact(diff, f)
        total_risk += analysis["risk_score"]
        file_analyses.append(analysis)

    # Adjust for file count
    file_count_penalty = min(20, len(changed_files) * 3)
    total_risk = min(100, total_risk + file_count_penalty)

    # Count by type
    code_files = sum(1 for f in changed_files if f.endswith((".py", ".js", ".java", ".go", ".rs")))
    config_files = sum(1 for f in changed_files if f.endswith((".yaml", ".yml", ".json", ".toml", ".env")))
    doc_files = sum(1 for f in changed_files if f.endswith((".md", ".txt", ".rst")))

    return {
        "overall_risk": "critical" if total_risk > 70 else "high" if total_risk > 40 else "medium" if total_risk > 15 else "low",
        "risk_score": total_risk,
        "files_changed": len(changed_files),
        "code_files": code_files,
        "config_files": config_files,
        "doc_files": doc_files,
        "file_analyses": file_analyses[:20],
        "recommended_actions": _recommend_actions(total_risk, code_files, file_analyses),
    }


def _recommend_actions(risk: int, code_files: int, analyses: list[dict[str, Any]]) -> list[str]:
    actions = []
    if risk > 70:
        actions.append("🚨 高风险 PR：建议在合并前运行完整回归测试套件")
        actions.append("建议至少 2 人 Code Review")
    elif risk > 40:
        actions.append("[WARN] 中高风险：运行受影响服务的增量测试")
    if code_files > 5:
        actions.append(f"📦 {code_files} 个代码文件变更，建议分批提交")
    if any(a.get("type") == "schema_change" for a in analyses):
        actions.append("🗄️ 检测到数据库变更，建议先在 staging 环境验证")
    if any(a.get("type") == "function_changed" for a in analyses):
        actions.append("[TOOL] 函数签名变更，检查调用方兼容性")
    return actions
