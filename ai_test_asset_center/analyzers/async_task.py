from __future__ import annotations

"""
QualiBug AI - 异步任务分析器 (C20)

用于分析异步任务、消息队列、定时任务问题。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class AsyncTaskType(Enum):
    """异步任务类型"""
    MESSAGE_QUEUE = "message_queue"  # 消息队列
    BACKGROUND_JOB = "background_job"  # 后台任务
    SCHEDULED_TASK = "scheduled_task"  # 定时任务
    EVENT_HANDLER = "event_handler"  # 事件处理


@dataclass
class AsyncTask:
    """异步任务定义"""
    name: str
    task_type: AsyncTaskType
    trigger: str
    description: str
    endpoints: List[str] = field(default_factory=list)
    retry_config: Optional[Dict[str, Any]] = None


@dataclass
class AsyncTaskBug:
    """异步任务相关bug"""
    bug_id: str
    category: str
    severity: str
    title: str
    description: str
    task_type: AsyncTaskType
    affected_tasks: List[str]
    evidence: Dict[str, Any]
    reproduction_steps: List[str]
    expected_behavior: str
    actual_behavior: str


class AsyncTaskAnalyzer:
    """异步任务分析器"""

    def __init__(self):
        self.tasks: List[AsyncTask] = []
        self.bugs: List[AsyncTaskBug] = []

        # 异步任务关键词
        self.async_keywords = [
            "async", "background", "queue", "celery", "rq", "kafka", "rabbitmq",
            "scheduled", "cron", "timer", "job", "task", "event", "message",
            "异步", "后台", "队列", "定时", "任务", "消息", "事件", "调度"
        ]

        # 重试相关关键词
        self.retry_keywords = [
            "retry", "backoff", "dead letter", "dlq", "重试", "退避", "死信"
        ]

    def identify_async_tasks(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None
    ) -> List[AsyncTask]:
        """
        识别异步任务

        Args:
            api_spec: API规格
            prd_text: PRD文本

        Returns:
            异步任务列表
        """
        logger.info("识别异步任务...")

        tasks = []
        task_id = 0

        paths = api_spec.get("paths", {})

        for path, methods in paths.items():
            for method, config in methods.items():
                summary = str(config.get("summary", "")).lower()
                description = str(config.get("description", "")).lower()
                path_lower = path.lower()
                combined = f"{summary} {description}"

                # 检查是否是异步任务
                if any(kw in summary or kw in path_lower for kw in self.async_keywords):
                    # 确定任务类型
                    task_type = AsyncTaskType.BACKGROUND_JOB
                    if any(kw in combined for kw in ["queue", "message", "kafka", "rabbitmq"]):
                        task_type = AsyncTaskType.MESSAGE_QUEUE
                    elif any(kw in combined for kw in ["schedule", "cron", "timer"]):
                        task_type = AsyncTaskType.SCHEDULED_TASK
                    elif any(kw in combined for kw in ["event"]):
                        task_type = AsyncTaskType.EVENT_HANDLER

                    # 检查是否有重试配置线索
                    retry_config = None
                    retry_hits = [kw for kw in self.retry_keywords if kw in combined]
                    if retry_hits:
                        retry_config = {"detected_keywords": retry_hits}

                    task = AsyncTask(
                        name=f"AsyncTask_{task_id}",
                        task_type=task_type,
                        trigger=path,
                        description=summary,
                        endpoints=[path],
                        retry_config=retry_config
                    )
                    tasks.append(task)
                    task_id += 1

        # 从PRD中补充
        if prd_text:
            lines = prd_text.split('\n')
            for line in lines:
                if any(kw in line.lower() for kw in self.async_keywords):
                    retry_config = None
                    retry_hits = [kw for kw in self.retry_keywords if kw in line.lower()]
                    if retry_hits:
                        retry_config = {"detected_keywords": retry_hits}

                    task = AsyncTask(
                        name=f"PRD_Task_{task_id}",
                        task_type=AsyncTaskType.BACKGROUND_JOB,
                        trigger="PRD_defined",
                        description=line.strip(),
                        retry_config=retry_config
                    )
                    tasks.append(task)
                    task_id += 1

        self.tasks.extend(tasks)
        logger.info(f"识别到 {len(tasks)} 个异步任务")
        return tasks

    def check_failure_handling(
        self,
        tasks: List[AsyncTask]
    ) -> List[AsyncTaskBug]:
        """
        检查失败处理

        Args:
            tasks: 异步任务列表

        Returns:
            发现的bug列表
        """
        logger.info("检查失败处理...")

        bugs = []
        bug_id = 0

        for task in tasks:
            # 检查是否有重试配置
            if not task.retry_config:
                bug = AsyncTaskBug(
                    bug_id=f"AT_{bug_id:03d}",
                    category="C20",
                    severity="P1",
                    title=f"异步任务缺少重试配置: {task.name}",
                    description=f"该异步任务可能缺少重试和失败处理机制",
                    task_type=task.task_type,
                    affected_tasks=[task.name],
                    evidence={"task_name": task.name, "trigger": task.trigger},
                    reproduction_steps=[
                        f"1. 触发异步任务: {task.name}",
                        "2. 模拟任务执行失败",
                        "3. 观察是否有重试机制",
                        "4. 验证失败后是否有补偿措施"
                    ],
                    expected_behavior="异步任务应该有适当的重试策略和失败处理",
                    actual_behavior="可能缺少重试和失败处理机制"
                )
                bugs.append(bug)
                bug_id += 1

            # 检查是否有死信队列
            if task.task_type == AsyncTaskType.MESSAGE_QUEUE:
                bug = AsyncTaskBug(
                    bug_id=f"AT_{bug_id:03d}",
                    category="C20",
                    severity="P2",
                    title=f"消息队列可能缺少死信队列: {task.name}",
                    description=f"该消息队列任务可能缺少死信队列配置",
                    task_type=task.task_type,
                    affected_tasks=[task.name],
                    evidence={"task_name": task.name},
                    reproduction_steps=[
                        "1. 发送会导致处理失败的消息",
                        "2. 观察消息是否进入死信队列",
                        "3. 验证是否可以手动或自动处理"
                    ],
                    expected_behavior="应该配置死信队列处理失败的消息",
                    actual_behavior="可能缺少死信队列配置"
                )
                bugs.append(bug)
                bug_id += 1

        self.bugs.extend(bugs)
        logger.info(f"发现 {len(bugs)} 个异步任务问题")
        return bugs

    def verify_message_queue(
        self,
        queue_config: Optional[Dict[str, Any]] = None
    ) -> List[AsyncTaskBug]:
        """
        验证消息队列

        Args:
            queue_config: 队列配置

        Returns:
            发现的bug列表
        """
        logger.info("验证消息队列...")

        bugs = []
        bug_id = len(self.bugs)

        if queue_config:
            # 检查配置
            has_dead_letter = any(kw in str(queue_config).lower() for kw in self.retry_keywords)

            if not has_dead_letter:
                bug = AsyncTaskBug(
                    bug_id=f"AT_{bug_id:03d}",
                    category="C20",
                    severity="P1",
                    title="消息队列配置可能缺少重试/死信机制",
                    description="消息队列配置中未发现明显的重试或死信机制",
                    task_type=AsyncTaskType.MESSAGE_QUEUE,
                    affected_tasks=[],
                    evidence={"config": queue_config},
                    reproduction_steps=[
                        "1. 检查消息队列配置",
                        "2. 验证是否有重试策略",
                        "3. 确认是否有死信队列"
                    ],
                    expected_behavior="消息队列应该有适当的重试和死信机制",
                    actual_behavior="可能缺少相关配置"
                )
                bugs.append(bug)
                bug_id += 1

        self.bugs.extend(bugs)
        return bugs

    def analyze_async_tasks(
        self,
        api_spec: Dict[str, Any],
        prd_text: Optional[str] = None,
        queue_config: Optional[Dict[str, Any]] = None
    ) -> List[AsyncTaskBug]:
        """
        综合异步任务分析

        Args:
            api_spec: API规格
            prd_text: PRD文本
            queue_config: 队列配置

        Returns:
            发现的bug列表
        """
        tasks = self.identify_async_tasks(api_spec, prd_text)
        bugs = self.check_failure_handling(tasks)
        bugs.extend(self.verify_message_queue(queue_config))
        return bugs

    def get_summary(self) -> Dict[str, Any]:
        """获取分析摘要"""
        severity_count = {"P0": 0, "P1": 0, "P2": 0}
        for bug in self.bugs:
            if bug.severity in severity_count:
                severity_count[bug.severity] += 1

        return {
            "total_tasks": len(self.tasks),
            "total_bugs": len(self.bugs),
            "severity_count": severity_count,
            "tasks_by_type": {
                tt.value: sum(1 for t in self.tasks if t.task_type == tt)
                for tt in AsyncTaskType
            }
        }


# 便捷函数
def analyze_async_tasks(api_spec: Dict[str, Any], prd_text: Optional[str] = None) -> Dict[str, Any]:
    """
    快速分析异步任务

    Args:
        api_spec: API规格
        prd_text: PRD文本

    Returns:
        分析结果
    """
    analyzer = AsyncTaskAnalyzer()
    bugs = analyzer.analyze_async_tasks(api_spec, prd_text)
    summary = analyzer.get_summary()
    return {
        "tasks": analyzer.tasks,
        "bugs": bugs,
        "summary": summary
    }
