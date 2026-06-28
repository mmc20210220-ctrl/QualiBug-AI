from __future__ import annotations

"""
QualiBug AI - 业务规则分析器 (C01, C08, C09, C13)

用于分析和验证业务规则，提升对业务类bug的发现能力。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class RuleType(Enum):
    """业务规则类型"""
    VALIDATION = "validation"
    TRANSFORMATION = "transformation"
    CONSERVATION = "conservation"
    WORKFLOW = "workflow"
    STATE_MACHINE = "state_machine"


class RulePriority(Enum):
    """规则优先级"""
    CRITICAL = "P0"
    HIGH = "P1"
    MEDIUM = "P2"
    LOW = "P3"


@dataclass
class BusinessRule:
    """业务规则"""
    id: str
    name: str
    description: str
    rule_type: RuleType
    priority: RulePriority
    conditions: List[str]
    expected_actions: List[str]
    source_document: str
    line_number: Optional[int] = None
    related_endpoints: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


@dataclass
class Violation:
    """规则违反"""
    rule_id: str
    rule_name: str
    severity: str
    description: str
    location: str
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str
    related_endpoints: List[str]


class BusinessRulesAnalyzer:
    """业务规则分析器"""

    def __init__(self):
        self.rules: List[BusinessRule] = []
        self.violations: List[Violation] = []

    def extract_rules_from_prd(self, prd_text: str) -> List[BusinessRule]:
        """
        从PRD中提取业务规则

        Args:
            prd_text: PRD文档文本

        Returns:
            提取到的业务规则列表
        """
        logger.info("从PRD中提取业务规则...")

        rules = []

        # 规则1: 查找包含"必须"、"应该"、"不得"、"禁止"的句子
        self._extract_mandatory_rules(prd_text, rules)

        # 规则2: 查找数字边界条件
        self._extract_boundary_rules(prd_text, rules)

        # 规则3: 查找状态转换规则
        self._extract_state_rules(prd_text, rules)

        # 规则4: 查找守恒规则（金额、库存等）
        self._extract_conservation_rules(prd_text, rules)

        self.rules = rules
        logger.info(f"提取到 {len(rules)} 条业务规则")
        return rules

    def _extract_mandatory_rules(self, prd_text: str, rules: List[BusinessRule]):
        """提取强制规则"""
        # 查找包含关键词的句子
        keywords = [
            "必须", "应当", "不得", "禁止", "需要", "应该", "只能", "只能由",
            "must", "should", "shall", "must not", "should not",
            "required", "forbidden", "prohibited", "mandatory"
        ]

        lines = prd_text.split('\n')
        rule_id = 0

        for i, line in enumerate(lines):
            for keyword in keywords:
                if keyword in line.lower():
                    rule = BusinessRule(
                        id=f"BR_{rule_id:03d}",
                        name=f"规则_{rule_id}",
                        description=line.strip(),
                        rule_type=RuleType.VALIDATION,
                        priority=self._infer_priority(line),
                        conditions=[],
                        expected_actions=[],
                        source_document="PRD",
                        line_number=i + 1
                    )
                    rules.append(rule)
                    rule_id += 1
                    break

    def _extract_boundary_rules(self, prd_text: str, rules: List[BusinessRule]):
        """提取边界规则"""
        # 查找数字边界模式
        boundary_patterns = [
            r'(\d+)\s*[-~至]\s*(\d+)',  # 范围: 1-100
            r'(?:大于|小于|超过|最多|最少|至少|不超过)\s*(\d+)',
            r'(?:>|>=|<|<=)\s*(\d+)'
        ]

        lines = prd_text.split('\n')
        rule_id = len(rules)

        for i, line in enumerate(lines):
            for pattern in boundary_patterns:
                if re.search(pattern, line):
                    rule = BusinessRule(
                        id=f"BR_{rule_id:03d}",
                        name=f"边界规则_{rule_id}",
                        description=line.strip(),
                        rule_type=RuleType.VALIDATION,
                        priority=RulePriority.HIGH,
                        conditions=[],
                        expected_actions=[],
                        source_document="PRD",
                        line_number=i + 1,
                        tags=["边界检查"]
                    )
                    rules.append(rule)
                    rule_id += 1
                    break

    def _extract_state_rules(self, prd_text: str, rules: List[BusinessRule]):
        """提取状态转换规则"""
        state_keywords = [
            "状态", "状态机", "转换", "流转", "state", "status",
            "transition", "lifecycle", "生命周期"
        ]

        lines = prd_text.split('\n')
        rule_id = len(rules)

        for i, line in enumerate(lines):
            for keyword in state_keywords:
                if keyword in line:
                    rule = BusinessRule(
                        id=f"BR_{rule_id:03d}",
                        name=f"状态规则_{rule_id}",
                        description=line.strip(),
                        rule_type=RuleType.STATE_MACHINE,
                        priority=RulePriority.HIGH,
                        conditions=[],
                        expected_actions=[],
                        source_document="PRD",
                        line_number=i + 1,
                        tags=["状态机", "C06"]
                    )
                    rules.append(rule)
                    rule_id += 1
                    break

    def _extract_conservation_rules(self, prd_text: str, rules: List[BusinessRule]):
        """提取守恒规则"""
        conservation_keywords = [
            "金额", "库存", "积分", "额度", "守恒", "一致", "不变",
            "balance", "inventory", "points", "quota", "conservation"
        ]

        lines = prd_text.split('\n')
        rule_id = len(rules)

        for i, line in enumerate(lines):
            for keyword in conservation_keywords:
                if keyword in line:
                    rule = BusinessRule(
                        id=f"BR_{rule_id:03d}",
                        name=f"守恒规则_{rule_id}",
                        description=line.strip(),
                        rule_type=RuleType.CONSERVATION,
                        priority=RulePriority.CRITICAL,
                        conditions=[],
                        expected_actions=[],
                        source_document="PRD",
                        line_number=i + 1,
                        tags=["守恒", "C08"]
                    )
                    rules.append(rule)
                    rule_id += 1
                    break

    def _infer_priority(self, text: str) -> RulePriority:
        """从文本中推断规则优先级"""
        text_lower = text.lower()

        # P0关键词
        p0_keywords = ["必须", "不得", "禁止", "严禁", "critical", "severe"]
        for keyword in p0_keywords:
            if keyword in text_lower:
                return RulePriority.CRITICAL

        # P1关键词
        p1_keywords = ["应该", "应当", "需要", "important", "high"]
        for keyword in p1_keywords:
            if keyword in text_lower:
                return RulePriority.HIGH

        # 默认P2
        return RulePriority.MEDIUM

    def validate_rule_implementation(
        self,
        rules: List[BusinessRule],
        api_spec: Dict[str, Any]
    ) -> List[Violation]:
        """
        验证规则在API中的实现情况

        Args:
            rules: 业务规则列表
            api_spec: API规格

        Returns:
            违规列表
        """
        logger.info("验证业务规则实现...")

        violations = []

        # 检查每个规则是否在API中有实现
        for rule in rules:
            # 这里是简化实现，实际应该更复杂
            # 1. 检查API的输入验证
            # 2. 检查API的响应处理
            # 3. 检查错误码和错误消息

            # 模拟检查：看看API描述中是否包含规则关键词
            rule_mentioned = self._is_rule_mentioned(rule, api_spec)

            if not rule_mentioned:
                violation = Violation(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    severity=rule.priority.value,
                    description=f"规则未在API中明确实现: {rule.name}",
                    location="API规格",
                    evidence={"rule": rule.description},
                    reproduction_steps=[
                        "1. 查阅PRD中的规则描述",
                        "2. 检查API实现",
                        "3. 尝试触发该规则边界"
                    ],
                    expected_behavior=rule.description,
                    actual_behavior="规则未明确实现",
                    related_endpoints=[]
                )
                violations.append(violation)

        self.violations = violations
        logger.info(f"发现 {len(violations)} 个规则违规")
        return violations

    def _is_rule_mentioned(self, rule: BusinessRule, api_spec: Dict[str, Any]) -> bool:
        """检查规则是否在API规格中被提及"""
        # 简化实现：检查规则中的关键词是否在API规格中出现
        api_text = str(api_spec).lower()
        rule_text = rule.description.lower()

        # 提取规则中的关键词
        keywords = self._extract_keywords(rule_text)

        # 检查是否有至少2个关键词匹配
        matches = sum(1 for keyword in keywords if keyword in api_text)
        return matches >= 2

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词"""
        # 简单实现：移除停用词，取剩余词
        stop_words = {
            "的", "是", "在", "了", "和", "与", "或", "但", "这", "那",
            "a", "an", "the", "and", "or", "but", "in", "on", "at"
        }

        words = re.findall(r'[\w]+', text.lower())
        return [w for w in words if w not in stop_words and len(w) > 1]

    def generate_edge_cases(self, rule: BusinessRule) -> List[Dict[str, Any]]:
        """
        为规则生成边界值测试用例

        Args:
            rule: 业务规则

        Returns:
            测试用例列表
        """
        logger.info(f"为规则 {rule.name} 生成边界测试用例...")

        test_cases = []

        # 根据规则类型生成不同的测试用例
        if rule.rule_type == RuleType.VALIDATION:
            # 数值边界
            test_cases.extend(self._generate_numeric_boundary_cases(rule))

            # 字符串边界
            test_cases.extend(self._generate_string_boundary_cases(rule))

        elif rule.rule_type == RuleType.STATE_MACHINE:
            # 状态转换边界
            test_cases.extend(self._generate_state_transition_cases(rule))

        elif rule.rule_type == RuleType.CONSERVATION:
            # 守恒规则测试
            test_cases.extend(self._generate_conservation_cases(rule))

        return test_cases

    def _generate_numeric_boundary_cases(self, rule: BusinessRule) -> List[Dict[str, Any]]:
        """生成数值边界测试用例"""
        return [
            {
                "name": "最小值",
                "type": "numeric_boundary",
                "value": 0,
                "expected": "应该接受或拒绝（根据规则）"
            },
            {
                "name": "最大值+1",
                "type": "numeric_boundary",
                "value": 1000001,
                "expected": "应该拒绝"
            },
            {
                "name": "负数",
                "type": "numeric_boundary",
                "value": -1,
                "expected": "应该拒绝"
            }
        ]

    def _generate_string_boundary_cases(self, rule: BusinessRule) -> List[Dict[str, Any]]:
        """生成字符串边界测试用例"""
        return [
            {
                "name": "空字符串",
                "type": "string_boundary",
                "value": "",
                "expected": "应该拒绝或接受空值（根据规则）"
            },
            {
                "name": "超长字符串",
                "type": "string_boundary",
                "value": "x" * 10000,
                "expected": "应该拒绝"
            },
            {
                "name": "特殊字符",
                "type": "string_boundary",
                "value": "\x00\x01\x02",
                "expected": "应该拒绝"
            }
        ]

    def _generate_state_transition_cases(self, rule: BusinessRule) -> List[Dict[str, Any]]:
        """生成状态转换测试用例"""
        return [
            {
                "name": "终态转换",
                "type": "state_transition",
                "scenario": "从终态尝试转换",
                "expected": "应该拒绝"
            },
            {
                "name": "无效源状态",
                "type": "state_transition",
                "scenario": "从不存在的状态转换",
                "expected": "应该拒绝"
            },
            {
                "name": "跳过中间状态",
                "type": "state_transition",
                "scenario": "直接从A转换到C，跳过B",
                "expected": "应该拒绝（如果需要中间状态）"
            }
        ]

    def _generate_conservation_cases(self, rule: BusinessRule) -> List[Dict[str, Any]]:
        """生成守恒规则测试用例"""
        return [
            {
                "name": "双重扣减",
                "type": "conservation",
                "scenario": "对同一资源执行两次扣减操作",
                "expected": "总数应该守恒"
            },
            {
                "name": "并发扣减",
                "type": "conservation",
                "scenario": "同时对同一资源执行扣减操作",
                "expected": "总数应该守恒"
            },
            {
                "name": "扣减后回滚",
                "type": "conservation",
                "scenario": "扣减操作后执行回滚",
                "expected": "应该恢复到初始状态"
            }
        ]

    def get_rules_by_category(self, category: str) -> List[BusinessRule]:
        """按分类获取规则"""
        return [r for r in self.rules if category in r.tags]

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        return {
            "total_rules": len(self.rules),
            "total_violations": len(self.violations),
            "violations_by_severity": {
                "P0": sum(1 for v in self.violations if v.severity == "P0"),
                "P1": sum(1 for v in self.violations if v.severity == "P1"),
                "P2": sum(1 for v in self.violations if v.severity == "P2"),
                "P3": sum(1 for v in self.violations if v.severity == "P3")
            },
            "rules_by_type": {
                rt.value: sum(1 for r in self.rules if r.rule_type == rt)
                for rt in RuleType
            }
        }


# 便捷函数
def analyze_prd_rules(prd_text: str) -> Dict[str, Any]:
    """
    快速分析PRD中的业务规则

    Args:
        prd_text: PRD文本

    Returns:
        分析结果
    """
    analyzer = BusinessRulesAnalyzer()
    rules = analyzer.extract_rules_from_prd(prd_text)
    summary = analyzer.get_summary()
    return {
        "rules": rules,
        "summary": summary
    }
