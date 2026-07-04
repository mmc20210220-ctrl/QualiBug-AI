from __future__ import annotations

"""
QualiBug AI - 守恒规则分析器 (C08, C09)

用于分析金额、库存、积分、额度等守恒规则，提升对数值守恒类bug的发现能力。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ConservationType(Enum):
    """守恒类型"""
    INVENTORY = "inventory"  # 库存
    BALANCE = "balance"  # 余额/金额
    POINTS = "points"  # 积分
    QUOTA = "quota"  # 额度


@dataclass
class ConservationRule:
    """守恒规则"""
    id: str
    name: str
    description: str
    conservation_type: ConservationType
    quantity_field: str
    debit_endpoints: List[str] = field(default_factory=list)
    credit_endpoints: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)


@dataclass
class ValueFlow:
    """数值流向"""
    source_endpoint: str
    target_endpoint: str
    quantity_field: str
    flow_type: str  # debit, credit, transfer
    expected_value: Optional[float] = None


@dataclass
class ConservationBug:
    """守恒相关bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    conservation_type: str
    affected_endpoints: List[str]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class ConservationAnalyzer:
    """守恒规则分析器"""

    def __init__(self):
        self.rules: List[ConservationRule] = []
        self.value_flows: List[ValueFlow] = []
        self.bugs: List[ConservationBug] = []

        # 守恒相关关键词
        self.conservation_keywords = {
            ConservationType.INVENTORY: [
                "库存", "inventory", "stock", "数量", "quantity",
                "扣库存", "减库存", "加库存", "释放库存"
            ],
            ConservationType.BALANCE: [
                "金额", "余额", "balance", "account", "支付",
                "payment", "refund", "退款", "扣款", "充值"
            ],
            ConservationType.POINTS: [
                "积分", "points", "bonus", "奖励", "消耗积分"
            ],
            ConservationType.QUOTA: [
                "额度", "quota", "limit", "限额", "配额"
            ]
        }

    def extract_conservation_rules(self, prd_text: str) -> List[ConservationRule]:
        """
        从PRD中提取守恒规则

        Args:
            prd_text: PRD文本

        Returns:
            守恒规则列表
        """
        logger.info("从PRD中提取守恒规则...")

        rules = []

        # 对每种守恒类型进行提取
        for cons_type, keywords in self.conservation_keywords.items():
            type_rules = self._extract_rules_by_type(prd_text, cons_type, keywords)
            rules.extend(type_rules)

        self.rules = rules
        logger.info(f"提取到 {len(rules)} 条守恒规则")
        return rules

    def _extract_rules_by_type(
        self,
        prd_text: str,
        cons_type: ConservationType,
        keywords: List[str]
    ) -> List[ConservationRule]:
        """按类型提取守恒规则"""
        rules = []
        rule_id = 0

        lines = prd_text.split('\n')

        for i, line in enumerate(lines):
            # 检查是否包含关键词
            has_keyword = any(kw in line for kw in keywords)
            if has_keyword:
                # 创建守恒规则
                rule = ConservationRule(
                    id=f"CR_{rule_id:03d}",
                    name=f"{cons_type.value}_规则_{rule_id}",
                    description=line.strip(),
                    conservation_type=cons_type,
                    quantity_field=self._extract_quantity_field(line)
                )
                rules.append(rule)
                rule_id += 1

        return rules

    def _extract_quantity_field(self, text: str) -> str:
        """从文本中提取数量字段名"""
        # 常见的数量字段名
        quantity_fields = [
            "amount", "quantity", "count", "number", "value", "balance",
            "total", "price", "points", "quota", "stock", "inventory",
            "金额", "数量", "余额", "积分", "额度", "库存"
        ]

        text_lower = text.lower()

        for field in quantity_fields:
            if field in text_lower:
                return field

        # 默认返回
        return "amount"

    def trace_value_flows(self, api_spec: Dict[str, Any]) -> List[ValueFlow]:
        """
        追踪数值在API之间的流向

        Args:
            api_spec: API规格

        Returns:
            数值流向列表
        """
        logger.info("追踪数值流向...")

        flows = []
        flow_id = 0

        paths = api_spec.get("paths", {})

        # 找出可能涉及数值操作的端点
        debit_endpoints = []
        credit_endpoints = []

        for path in paths:
            path_lower = path.lower()

            # 扣减/扣款操作
            if any(kw in path_lower for kw in ["deduct", "subtract", "debit", "pay", "扣"]):
                debit_endpoints.append(path)

            # 增加/充值操作
            if any(kw in path_lower for kw in ["add", "credit", "recharge", "refund", "加"]):
                credit_endpoints.append(path)

        # 构建流向
        for debit in debit_endpoints:
            for credit in credit_endpoints:
                # 简单假设：如果路径相似，可能是相关的
                if self._are_related_paths(debit, credit):
                    flow = ValueFlow(
                        source_endpoint=debit,
                        target_endpoint=credit,
                        quantity_field="amount",
                        flow_type="transfer"
                    )
                    flows.append(flow)
                    flow_id += 1

        self.value_flows = flows
        logger.info(f"识别到 {len(flows)} 条数值流向")
        return flows

    def _are_related_paths(self, path1: str, path2: str) -> bool:
        """判断两个路径是否相关"""
        # 简单实现：检查是否有共同的路径部分
        parts1 = set(p for p in path1.split('/') if p)
        parts2 = set(p for p in path2.split('/') if p)

        common = parts1 & parts2
        return len(common) >= 2

    def detect_inconsistencies(
        self,
        rules: List[ConservationRule],
        flows: List[ValueFlow]
    ) -> List[ConservationBug]:
        """
        检测数值不一致问题

        Args:
            rules: 守恒规则
            flows: 数值流向

        Returns:
            发现的bug列表
        """
        logger.info("检测数值不一致问题...")

        bugs = []
        bug_id = 0

        # 检测1: 扣减操作没有对应的回滚
        for flow in flows:
            if flow.flow_type == "transfer":
                bug = self._check_no_rollback(flow, bug_id)
                if bug:
                    bugs.append(bug)
                    bug_id += 1

        # 检测2: 并发安全问题
        for flow in flows:
            bug = self._check_concurrency_safety(flow, bug_id)
            if bug:
                bugs.append(bug)
                bug_id += 1

        # 检测3: 金额计算精度问题
        bugs.extend(self._check_precision_issues(rules, bug_id))

        self.bugs.extend(bugs)
        logger.info(f"发现 {len(bugs)} 个守恒问题")
        return bugs

    def _check_no_rollback(self, flow: ValueFlow, bug_id: int) -> Optional[ConservationBug]:
        """检查是否缺少回滚机制"""
        # 简化实现
        has_rollback = any(kw in flow.source_endpoint.lower()
                           for kw in ["rollback", "cancel", "refund", "回滚", "取消", "退款"])

        if not has_rollback:
            return ConservationBug(
                bug_id=f"CV_{bug_id:03d}",
                category="C08",
                severity="P1",
                title=f"数值操作缺少回滚机制: {flow.source_endpoint}",
                description=f"扣减操作 {flow.source_endpoint} 可能缺少对应的回滚或补偿机制",
                conservation_type=flow.quantity_field,
                affected_endpoints=[flow.source_endpoint],
                evidence={
                    "source_endpoint": flow.source_endpoint,
                    "target_endpoint": flow.target_endpoint
                },
                reproduction_steps=[
                    f"1. 调用扣减接口: {flow.source_endpoint}",
                    "2. 发生错误需要回滚",
                    "3. 观察是否有回滚机制",
                    "4. 验证数值是否恢复"
                ],
                expected_behavior="应该有回滚或补偿机制确保数值守恒",
                actual_behavior="可能缺少回滚机制"
            )

        return None

    def _check_concurrency_safety(self, flow: ValueFlow, bug_id: int) -> Optional[ConservationBug]:
        """检查并发安全问题 — 仅对涉及数值扣减的流程标记为待验证假设"""
        high_value_indicators = [
            "deduct", "subtract", "debit", "pay", "charge", "transfer",
            "withdraw", "扣", "减", "付", "转"
        ]
        is_high_value = any(
            kw in flow.source_endpoint.lower() for kw in high_value_indicators
        )
        if not is_high_value:
            return None

        return ConservationBug(
            bug_id=f"CV_{bug_id:03d}",
            category="C08",
            severity="P1",
            title=f"数值扣减操作需验证并发安全: {flow.source_endpoint}",
            description=f"扣减操作 {flow.source_endpoint} 涉及数值变更，需验证是否有并发控制机制",
            conservation_type=flow.quantity_field,
            affected_endpoints=[flow.source_endpoint, flow.target_endpoint],
            evidence={
                "source_endpoint": flow.source_endpoint,
                "target_endpoint": flow.target_endpoint,
                "indicator": "deduct_like_operation"
            },
            reproduction_steps=[
                "1. 准备测试数据，记录初始数值",
                f"2. 并发调用 {flow.source_endpoint} (建议10-20并发)",
                "3. 检查最终数值是否 = 初始值 - sum(每次扣减)",
                "4. 验证是否有超扣或少扣"
            ],
            expected_behavior="并发操作应该保证数值正确，使用锁或乐观锁机制",
            actual_behavior="需验证是否缺少并发控制"
        )

    def _check_precision_issues(
        self,
        rules: List[ConservationRule],
        bug_id: int
    ) -> List[ConservationBug]:
        """检查精度问题 — 仅对明确涉及小数/精度的规则标记"""
        bugs = []
        precision_indicators = ["小数", "decimal", "精度", "precision", "浮点", "float", "round", "四舍五入"]

        for rule in rules:
            if rule.conservation_type in [ConservationType.BALANCE, ConservationType.INVENTORY]:
                # 只在规则描述中明确提到精度相关概念时才标记
                has_precision_hint = any(kw in rule.description.lower() for kw in precision_indicators)
                if not has_precision_hint:
                    continue

                bug = ConservationBug(
                    bug_id=f"CV_{bug_id:03d}",
                    category="C08",
                    severity="P1",
                    title=f"金额计算可能存在精度问题: {rule.name}",
                    description=f"涉及金额或库存的计算可能存在浮点数精度问题",
                    conservation_type=rule.conservation_type.value,
                    affected_endpoints=rule.debit_endpoints + rule.credit_endpoints,
                    evidence={"rule": rule.description, "indicator": "precision_keyword_match"},
                    reproduction_steps=[
                        "1. 输入含有小数的金额",
                        "2. 执行多次计算操作",
                        "3. 检查最终结果是否精确",
                        "4. 验证是否有精度丢失"
                    ],
                    expected_behavior="应该使用高精度计算（如Decimal），避免浮点数精度问题",
                    actual_behavior="可能存在浮点数精度问题"
                )
                bugs.append(bug)
                bug_id += 1

        return bugs

    def analyze_conservation(
        self,
        prd_text: str,
        api_spec: Dict[str, Any]
    ) -> List[ConservationBug]:
        """
        综合分析守恒规则

        Args:
            prd_text: PRD文本
            api_spec: API规格

        Returns:
            发现的bug列表
        """
        rules = self.extract_conservation_rules(prd_text)
        flows = self.trace_value_flows(api_spec)
        bugs = self.detect_inconsistencies(rules, flows)
        return bugs

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        return {
            "total_rules": len(self.rules),
            "rules_by_type": {
                ct.value: sum(1 for r in self.rules if r.conservation_type == ct)
                for ct in ConservationType
            },
            "total_flows": len(self.value_flows),
            "total_bugs": len(self.bugs),
            "bugs_by_severity": {
                "P0": sum(1 for b in self.bugs if b.severity == "P0"),
                "P1": sum(1 for b in self.bugs if b.severity == "P1"),
                "P2": sum(1 for b in self.bugs if b.severity == "P2")
            }
        }


# 便捷函数
def analyze_conservation_rules(prd_text: str, api_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    快速分析守恒规则

    Args:
        prd_text: PRD文本
        api_spec: API规格

    Returns:
        分析结果
    """
    analyzer = ConservationAnalyzer()
    bugs = analyzer.analyze_conservation(prd_text, api_spec)
    summary = analyzer.get_summary()
    return {
        "rules": analyzer.rules,
        "flows": analyzer.value_flows,
        "bugs": bugs,
        "summary": summary
    }
