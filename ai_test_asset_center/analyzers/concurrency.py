from __future__ import annotations

"""
QualiBug AI - 并发与竞态分析器 (C11)

用于分析并发访问和竞态条件问题。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ConcurrencyIssueType(Enum):
    """并发问题类型"""
    RACE_CONDITION = "race_condition"  # 竞态条件
    DEADLOCK = "deadlock"  # 死锁
    LOCK_MISSING = "lock_missing"  # 缺少锁
    INVALID_LOCK = "invalid_lock"  # 锁使用不当


@dataclass
class ConcurrencyEndpoint:
    """可能有并发问题的端点"""
    path: str
    method: str
    risk_level: str
    issue_types: List[ConcurrencyIssueType]
    description: str


@dataclass
class ConcurrencyBug:
    """并发相关bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    issue_type: ConcurrencyIssueType
    affected_endpoints: List[str]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class ConcurrencyAnalyzer:
    """并发与竞态分析器"""

    def __init__(self):
        self.endpoints: List[ConcurrencyEndpoint] = []
        self.bugs: List[ConcurrencyBug] = []

        # 高风险操作关键词
        self.high_risk_keywords = [
            "update", "modify", "delete", "deduct", "subtract", "withdraw",
            "transfer", "charge", "pay", "allocate", "reserve", "book",
            "更新", "修改", "删除", "扣减", "扣除", "转账", "支付", "分配", "预约"
        ]

        # 共享资源关键词
        self.shared_resource_keywords = [
            "inventory", "stock", "balance", "quota", "count", "counter",
            "库存", "余额", "配额", "计数", "计数器", "数量", "金额"
        ]

    def identify_race_candidates(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None
    ) -> List[ConcurrencyEndpoint]:
        """
        识别可能产生竞态的端点

        Args:
            api_spec: API规格
            prd_text: PRD文本（可选）

        Returns:
            高风险端点列表
        """
        logger.info("识别可能产生竞态的端点...")

        candidates = []
        paths = api_spec.get("paths", {})

        for path, methods in paths.items():
            for method, config in methods.items():
                summary = str(config.get("summary", "")).lower()
                path_lower = path.lower()

                risk_level = "low"
                issue_types = []

                # 检查是否是高风险操作
                if any(kw in summary or kw in path_lower for kw in self.high_risk_keywords):
                    risk_level = "high"
                    issue_types.append(ConcurrencyIssueType.RACE_CONDITION)

                # 检查是否涉及共享资源
                if any(kw in summary or kw in path_lower for kw in self.shared_resource_keywords):
                    risk_level = "high"
                    if ConcurrencyIssueType.RACE_CONDITION not in issue_types:
                        issue_types.append(ConcurrencyIssueType.RACE_CONDITION)

                # 检查是否是写入操作
                if method.upper() in ["POST", "PUT", "PATCH", "DELETE"]:
                    if risk_level == "low":
                        risk_level = "medium"

                if risk_level != "low":
                    candidate = ConcurrencyEndpoint(
                        path=path,
                        method=method.upper(),
                        risk_level=risk_level,
                        issue_types=issue_types,
                        description=f"可能存在{risk_level}风险的并发操作"
                    )
                    candidates.append(candidate)

        self.endpoints.extend(candidates)
        logger.info(f"识别到 {len(candidates)} 个高风险并发端点")
        return candidates

    def detect_concurrency_issues(
        self,
        candidates: List[ConcurrencyEndpoint],
        code_snippets: Optional[List[str]] = None
    ) -> List[ConcurrencyBug]:
        """
        检测并发问题

        Args:
            candidates: 候选端点
            code_snippets: 代码片段（可选）

        Returns:
            发现的bug列表
        """
        logger.info("检测并发问题...")

        bugs = []
        bug_id = 0

        for candidate in candidates:
            if candidate.risk_level in ["high", "medium"]:
                # 检查竞态条件
                if ConcurrencyIssueType.RACE_CONDITION in candidate.issue_types:
                    bug = ConcurrencyBug(
                        bug_id=f"CC_{bug_id:03d}",
                        category="C11",
                        severity="P0" if candidate.risk_level == "high" else "P1",
                        title=f"可能存在竞态条件: {candidate.method} {candidate.path}",
                        description=f"该端点执行高风险操作，可能存在竞态条件",
                        issue_type=ConcurrencyIssueType.RACE_CONDITION,
                        affected_endpoints=[candidate.path],
                        evidence={
                            "path": candidate.path,
                            "method": candidate.method,
                            "risk_level": candidate.risk_level
                        },
                        reproduction_steps=[
                            "1. 准备测试环境和初始数据",
                            f"2. 发起多个并发请求到 {candidate.path}",
                            "3. 观察最终结果是否一致",
                            "4. 验证是否有超卖、少扣或数据不一致问题"
                        ],
                        expected_behavior="应该有适当的并发控制机制（如数据库锁、乐观锁、分布式锁）",
                        actual_behavior="可能没有并发控制，存在数据不一致风险"
                    )
                    bugs.append(bug)
                    bug_id += 1

        self.bugs.extend(bugs)
        logger.info(f"检测到 {len(bugs)} 个并发问题")
        return bugs

    def check_locking_issues(
        self,
        code_snippets: Optional[List[str]] = None
    ) -> List[ConcurrencyBug]:
        """
        检查锁使用问题

        Args:
            code_snippets: 代码片段（可选）

        Returns:
            发现的bug列表
        """
        logger.info("检查锁使用问题...")

        bugs = []
        bug_id = len(self.bugs)

        if code_snippets:
            for code in code_snippets:
                # 简单检查：查找锁相关关键词
                has_lock = any(kw in code.lower() for kw in [
                    "lock", "mutex", "semaphore", "for update",
                    "乐观锁", "悲观锁", "分布式锁"
                ])

                if not has_lock:
                    bug = ConcurrencyBug(
                        bug_id=f"CC_{bug_id:03d}",
                        category="C11",
                        severity="P1",
                        title="可能缺少锁机制",
                        description="代码片段中未发现明显的锁机制",
                        issue_type=ConcurrencyIssueType.LOCK_MISSING,
                        affected_endpoints=[],
                        evidence={"code_snippet": code[:100] + "..." if len(code) > 100 else code},
                        reproduction_steps=[
                            "1. 分析代码逻辑",
                            "2. 检查是否有共享资源访问",
                            "3. 验证是否有适当的锁机制"
                        ],
                        expected_behavior="应该有适当的并发控制和锁机制",
                        actual_behavior="未发现明显的锁机制"
                    )
                    bugs.append(bug)
                    bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def generate_concurrent_test_cases(
        self,
        candidate: ConcurrencyEndpoint,
        num_requests: int = 10
    ) -> List[Dict[str, Any]]:
        """
        生成并发测试用例

        Args:
            candidate: 候选端点
            num_requests: 并发请求数

        Returns:
            测试用例列表
        """
        test_cases = []

        test_cases.append({
            "name": f"并发{num_requests}个请求",
            "type": "concurrent_requests",
            "method": candidate.method,
            "endpoint": candidate.path,
            "num_requests": num_requests,
            "expected": "所有请求应该正确处理，无数据不一致"
        })

        return test_cases

    def analyze_concurrency(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None,
        code_snippets: Optional[List[str]] = None
    ) -> List[ConcurrencyBug]:
        """
        综合并发分析

        Args:
            api_spec: API规格
            prd_text: PRD文本
            code_snippets: 代码片段

        Returns:
            发现的bug列表
        """
        candidates = self.identify_race_candidates(api_spec, prd_text)
        bugs = self.detect_concurrency_issues(candidates, code_snippets)
        bugs.extend(self.check_locking_issues(code_snippets))
        return bugs

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        severity_count = {"P0": 0, "P1": 0, "P2": 0}
        for bug in self.bugs:
            if bug.severity in severity_count:
                severity_count[bug.severity] += 1

        return {
            "total_candidates": len(self.endpoints),
            "total_bugs": len(self.bugs),
            "severity_count": severity_count,
            "high_risk_endpoints": sum(1 for e in self.endpoints if e.risk_level == "high")
        }


# 便捷函数
def analyze_concurrency(api_spec: Dict[str, Any], prd_text: Optional[str] = None) -> Dict[str, Any]:
    """
    快速分析并发问题

    Args:
        api_spec: API规格
        prd_text: PRD文本

    Returns:
        分析结果
    """
    analyzer = ConcurrencyAnalyzer()
    bugs = analyzer.analyze_concurrency(api_spec, prd_text)
    summary = analyzer.get_summary()
    return {
        "candidates": analyzer.endpoints,
        "bugs": bugs,
        "summary": summary
    }
