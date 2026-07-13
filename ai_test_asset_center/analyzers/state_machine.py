from __future__ import annotations

"""
QualiBug AI - 状态机分析器 (C06, C07)

用于分析业务状态机，提升对状态相关bug的发现能力。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from enum import Enum

logger = logging.getLogger(__name__)


@dataclass
class State:
    """状态定义"""
    id: str
    name: str
    description: str
    is_final: bool = False
    is_initial: bool = False
    allowed_next_states: List[str] = field(default_factory=list)
    allowed_previous_states: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)


@dataclass
class Transition:
    """状态转换"""
    from_state: str
    to_state: str
    trigger: str
    conditions: List[str]
    actions: List[str]
    required_permissions: List[str] = field(default_factory=list)


@dataclass
class StateMachine:
    """状态机定义"""
    id: str
    name: str
    description: str
    states: Dict[str, State] = field(default_factory=dict)
    transitions: List[Transition] = field(default_factory=list)
    initial_state: Optional[str] = None
    final_states: List[str] = field(default_factory=list)


@dataclass
class StateMachineBug:
    """状态机相关bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    state_involved: List[str]
    transitions_involved: List[Transition]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class StateMachineAnalyzer:
    """状态机分析器"""

    def __init__(self):
        self.state_machines: List[StateMachine] = []
        self.bugs: List[StateMachineBug] = []
        self.endpoint_state_mapping: Dict[str, List[str]] = {}

    def extract_state_machine(self, prd_text: str, api_spec: Dict[str, Any]) -> StateMachine:
        """
        从文档中提取状态机定义

        Args:
            prd_text: PRD文档
            api_spec: API规格

        Returns:
            提取到的状态机
        """
        logger.info("从文档中提取状态机定义...")

        sm = StateMachine(
            id="sm_default",
            name="业务状态机",
            description="从文档中提取的状态机"
        )

        # 从PRD中提取状态
        self._extract_states_from_prd(prd_text, sm)

        # 从API规格中提取状态相关接口
        self._extract_transitions_from_api(api_spec, sm)

        # 确定初始状态和终态
        self._determine_initial_and_final(sm)

        self.state_machines.append(sm)
        logger.info(f"提取到 {len(sm.states)} 个状态，{len(sm.transitions)} 个转换")

        return sm

    def _extract_states_from_prd(self, prd_text: str, sm: StateMachine):
        """从PRD中提取状态"""
        # 常见状态关键词
        state_keywords = [
            "状态", "status", "state", "draft", "pending", "active", "completed",
            "cancelled", "failed", "success", "paid", "shipped", "delivered",
            "refunded", "approved", "rejected", "pending_payment", "allocated"
        ]

        lines = prd_text.split('\n')
        state_names_found = set()

        # 查找包含状态关键词的行
        for i, line in enumerate(lines):
            for keyword in state_keywords:
                if keyword in line.lower():
                    # 尝试提取状态名
                    state_name = self._extract_state_name(line, keyword)
                    if state_name and state_name not in state_names_found:
                        state = State(
                            id=f"state_{len(sm.states)}",
                            name=state_name,
                            description=line.strip(),
                            is_final=self._is_likely_final_state(state_name),
                            is_initial=self._is_likely_initial_state(state_name)
                        )
                        sm.states[state.id] = state
                        state_names_found.add(state_name)
                        break

        # 如果没有提取到任何状态，不注入虚假状态机
        if not sm.states:
            logger.info("PRD中未提取到状态关键词，跳过状态机分析")
            return

    def _extract_state_name(self, line: str, keyword: str) -> Optional[str]:
        """从行中提取状态名"""
        # 简单实现：查找包含关键词的词组
        words = re.findall(r'[\w]+', line.lower())

        # 尝试找到状态名
        for word in words:
            if word in [
                "draft", "pending", "active", "completed", "cancelled",
                "failed", "success", "paid", "shipped", "delivered",
                "refunded", "approved", "rejected"
            ]:
                return word

        # 如果没找到，直接用关键词
        if len(keyword) <= 20:
            return keyword

        return None

    def _is_likely_final_state(self, state_name: str) -> bool:
        """判断是否可能是终态"""
        final_keywords = [
            "completed", "cancelled", "rejected", "failed", "refunded",
            "delivered", "closed", "结束", "完成", "取消", "失败"
        ]
        return any(kw in state_name.lower() for kw in final_keywords)

    def _is_likely_initial_state(self, state_name: str) -> bool:
        """判断是否可能是初始状态"""
        initial_keywords = [
            "draft", "pending", "new", "created", "初始", "新建", "草稿"
        ]
        return any(kw in state_name.lower() for kw in initial_keywords)

    def _extract_transitions_from_api(self, api_spec: Dict[str, Any], sm: StateMachine):
        """从API规格中提取状态转换 — 创建实际 Transition 对象"""
        paths = api_spec.get("paths", {})

        # 已知状态名集合（小写）
        known_states = {s.name.lower(): s for s in sm.states.values()}

        for path, methods in paths.items():
            for method, config in methods.items():
                summary = str(config.get("summary", "")).lower()
                description = str(config.get("description", "")).lower()
                combined = f"{summary} {description}"

                # 检查是否是状态转换相关
                transition_keywords = [
                    "update", "change", "transition", "set", "status",
                    "approve", "reject", "cancel", "submit", "complete",
                    "更新", "修改", "转换", "设置", "状态", "审批", "取消"
                ]

                is_transition = any(kw in combined for kw in transition_keywords)
                if not is_transition:
                    continue

                # 记录端点映射
                self.endpoint_state_mapping.setdefault(path, []).append("status")

                # 尝试从 summary/description 中提取目标状态
                for state_name, state_obj in known_states.items():
                    if state_name in combined:
                        # 创建 Transition 对象
                        transition = Transition(
                            from_state="",  # 来源状态未知，留空
                            to_state=state_obj.id,
                            trigger=f"{method.upper()} {path}",
                            conditions=[],
                            actions=[summary],
                            required_permissions=[]
                        )
                        sm.transitions.append(transition)
                        break

    def _determine_initial_and_final(self, sm: StateMachine):
        """确定初始状态和终态"""
        # 标记初始状态
        for state in sm.states.values():
            if state.is_initial:
                sm.initial_state = state.id
                break

        # 如果没找到，取第一个
        if not sm.initial_state and sm.states:
            sm.initial_state = list(sm.states.keys())[0]

        # 收集终态
        for state in sm.states.values():
            if state.is_final:
                sm.final_states.append(state.id)

    def validate_state_transitions(
        self,
        sm: StateMachine,
        endpoints: List[Dict[str, Any]]
    ) -> List[StateMachineBug]:
        """
        验证状态转换是否符合规则

        Args:
            sm: 状态机定义
            endpoints: API端点列表

        Returns:
            发现的bug列表
        """
        logger.info("验证状态转换...")

        bugs = []

        # 检查1: 终态是否可以转换
        bugs.extend(self._check_final_state_transitions(sm, endpoints))

        # 检查2: 是否缺少必要的前置条件检查
        bugs.extend(self._check_missing_precondition_checks(sm, endpoints))

        # 检查3: 是否有无效的状态转换
        bugs.extend(self._check_invalid_transitions(sm, endpoints))

        self.bugs.extend(bugs)
        logger.info(f"发现 {len(bugs)} 个状态机相关bug")
        return bugs

    def _check_final_state_transitions(
        self,
        sm: StateMachine,
        endpoints: List[Dict[str, Any]]
    ) -> List[StateMachineBug]:
        """检查终态转换问题"""
        bugs = []
        bug_id = len(self.bugs)

        for state in sm.states.values():
            if state.is_final:
                # 检查是否有端点可能修改终态对象
                for path in self.endpoint_state_mapping:
                    if any(keyword in path.lower() for keyword in [
                        "update", "modify", "edit", "change", "更新", "修改"
                    ]):
                        bug = StateMachineBug(
                            bug_id=f"SM_{bug_id:03d}",
                            category="C07",
                            severity="P1",
                            title=f"终态对象可能被修改: {state.name}",
                            description=f"检测到可能修改终态{state.name}对象的接口",
                            state_involved=[state.id],
                            transitions_involved=[],
                            evidence={
                                "endpoint": path,
                                "state": state.name
                            },
                            reproduction_steps=[
                                f"1. 将对象设置为 {state.name} 状态",
                                f"2. 调用可能修改对象的接口: {path}",
                                "3. 观察对象是否被修改"
                            ],
                            expected_behavior=f"终态 {state.name} 的对象应该是只读的或只能走受控恢复流程",
                            actual_behavior=f"存在可能修改终态对象的接口: {path}"
                        )
                        bugs.append(bug)
                        bug_id += 1

        return bugs

    def _check_missing_precondition_checks(
        self,
        sm: StateMachine,
        endpoints: List[Dict[str, Any]]
    ) -> List[StateMachineBug]:
        """检查是否缺少前置条件 — 降为P2，仅对有状态转换的端点标记"""
        bugs = []
        bug_id = len(self.bugs)

        if not sm.states:
            return bugs

        for path in self.endpoint_state_mapping:
            bug = StateMachineBug(
                bug_id=f"SM_{bug_id:03d}",
                category="C06",
                severity="P2",
                title="状态转换端点需验证前置条件",
                description=f"端点 {path} 涉及状态变更，需验证是否检查了当前状态的合法性",
                state_involved=list(sm.states.keys())[:3],
                transitions_involved=[],
                evidence={"endpoint": path, "known_states": list(sm.states.keys())},
                reproduction_steps=[
                    f"1. 检查端点 {path} 的实现",
                    "2. 验证是否检查了当前状态",
                    "3. 尝试从不合法的状态进行转换"
                ],
                expected_behavior="状态转换前应该检查当前状态是否合法",
                actual_behavior="需验证是否缺少当前状态检查"
            )
            bugs.append(bug)
            bug_id += 1

        return bugs

    def _check_invalid_transitions(
        self,
        sm: StateMachine,
        endpoints: List[Dict[str, Any]]
    ) -> List[StateMachineBug]:
        """检查无效状态转换 — 仅在有实际转换数据时检查"""
        bugs = []
        bug_id = len(self.bugs)

        # 没有提取到状态或转换时，不做猜测
        if len(sm.states) < 2 or not sm.transitions:
            return bugs

        # 检查是否有从终态出发的转换（真实风险）
        for transition in sm.transitions:
            from_state_id = transition.from_state
            if not from_state_id:
                continue
            from_state = sm.states.get(from_state_id)
            if from_state and from_state.is_final:
                bug = StateMachineBug(
                    bug_id=f"SM_{bug_id:03d}",
                    category="C06",
                    severity="P1",
                    title=f"终态可能有后续转换: {from_state.name} -> {transition.to_state}",
                    description=f"从终态 {from_state.name} 到 {transition.to_state} 的转换可能无效",
                    state_involved=[from_state.id, transition.to_state],
                    transitions_involved=[transition],
                    evidence={
                        "from_state": from_state.name,
                        "to_state": transition.to_state,
                        "trigger": transition.trigger
                    },
                    reproduction_steps=[
                        f"1. 将对象设置为终态: {from_state.name}",
                        f"2. 尝试触发转换: {transition.trigger}",
                        "3. 观察是否被允许"
                    ],
                    expected_behavior="终态不应该有后续状态转换",
                    actual_behavior=f"存在从终态 {from_state.name} 出发的转换"
                )
                bugs.append(bug)
                bug_id += 1

        return bugs

    def detect_invalid_transitions(
        self,
        sm: StateMachine,
        traces: List[Dict[str, Any]]
    ) -> List[StateMachineBug]:
        """
        从运行轨迹中检测无效的状态转换

        Args:
            sm: 状态机定义
            traces: 运行轨迹

        Returns:
            发现的bug列表
        """
        logger.info("从运行轨迹中检测无效状态转换...")

        bugs = []
        bug_id = len(self.bugs)

        for trace in traces:
            # 检查轨迹中的状态转换是否合法
            state_sequence = trace.get("states", [])
            if len(state_sequence) < 2:
                continue

            # 检查每对相邻状态
            for i in range(len(state_sequence) - 1):
                from_state = state_sequence[i]
                to_state = state_sequence[i + 1]

                # 简化检查：这里应该有更复杂的逻辑
                if self._is_likely_invalid_transition(sm, from_state, to_state):
                    bug = StateMachineBug(
                        bug_id=f"SM_{bug_id:03d}",
                        category="C06",
                        severity="P1",
                        title=f"检测到无效状态转换: {from_state} -> {to_state}",
                        description=f"从 {from_state} 到 {to_state} 的转换可能无效",
                        state_involved=[from_state, to_state],
                        transitions_involved=[],
                        evidence={
                            "trace": trace,
                            "from_state": from_state,
                            "to_state": to_state
                        },
                        reproduction_steps=[
                            "1. 复现状态序列",
                            "2. 观察是否发生了无效转换"
                        ],
                        expected_behavior="状态转换应该符合规则",
                        actual_behavior=f"发生了可能无效的转换: {from_state} -> {to_state}"
                    )
                    bugs.append(bug)
                    bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def _is_likely_invalid_transition(
        self,
        sm: StateMachine,
        from_state: str,
        to_state: str
    ) -> bool:
        """判断是否可能是无效转换"""
        # 简化实现
        # 1. 从终态转换总是可疑的
        for state in sm.states.values():
            if state.id == from_state and state.is_final:
                return True

        return False

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        return {
            "total_state_machines": len(self.state_machines),
            "total_bugs": len(self.bugs),
            "bugs_by_severity": {
                "P0": sum(1 for b in self.bugs if b.severity == "P0"),
                "P1": sum(1 for b in self.bugs if b.severity == "P1"),
                "P2": sum(1 for b in self.bugs if b.severity == "P2")
            },
            "total_states": sum(len(sm.states) for sm in self.state_machines),
            "total_transitions": sum(len(sm.transitions) for sm in self.state_machines)
        }


# 便捷函数
def analyze_state_machine(prd_text: str, api_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    快速分析状态机

    Args:
        prd_text: PRD文本
        api_spec: API规格

    Returns:
        分析结果
    """
    analyzer = StateMachineAnalyzer()
    sm = analyzer.extract_state_machine(prd_text, api_spec)
    bugs = analyzer.validate_state_transitions(sm, [])
    summary = analyzer.get_summary()
    return {
        "state_machine": sm,
        "bugs": bugs,
        "summary": summary
    }
