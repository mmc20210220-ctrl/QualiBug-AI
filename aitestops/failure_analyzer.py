from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aitestops.llm_client import LLMClientError, OpenAICompatibleLLMClient
from aitestops.prompt_templates import FAILURE_ANALYSIS_PROMPT, SYSTEM_PROMPT

FailureMode = Literal["auto", "local", "llm"]


@dataclass
class FailureAnalysis:
    failure_type: str
    confidence: float
    summary: str
    suspected_owner: str
    evidence: list[str]
    suggested_bug_title: str
    suggested_severity: str
    engine_used: str = "local"
    fallback_reason: str | None = None

    def to_markdown(self) -> str:
        evidence_md = "\n".join(f"- {item}" for item in self.evidence)
        fallback = f"\n- 兜底原因：{self.fallback_reason}" if self.fallback_reason else ""
        return f"""# AI 失败归因报告

## 结论

- 分析引擎：{self.engine_used}
- 失败类型：{self.failure_type}
- 置信度：{self.confidence:.2f}
- 疑似责任域：{self.suspected_owner}
- 建议严重级别：{self.suggested_severity}{fallback}

## 摘要

{self.summary}

## 证据

{evidence_md}

## 缺陷标题草稿

{self.suggested_bug_title}

## 建议下一步

1. 开发优先检查疑似责任域相关变更。
2. QA 保留失败日志、截图、Trace 或接口响应作为证据。
3. 修复后将该场景纳入防回归测试资产。
"""


class FailureAnalyzer:
    """Failure analyzer with real LLM + deterministic fallback."""

    def __init__(self, mode: FailureMode = "auto", audit_dir: Path | None = None):
        self.mode = mode
        self.audit_dir = audit_dir
        self.llm_client = OpenAICompatibleLLMClient()

    def analyze(self, log_text: str) -> FailureAnalysis:
        if self.mode == "local":
            return self._analyze_local(log_text, engine_used="local")

        try:
            if not self.llm_client.config.enabled:
                raise LLMClientError("LLM config is incomplete")
            prompt = FAILURE_ANALYSIS_PROMPT.replace("{failure_log}", log_text)
            data = self.llm_client.complete_json(SYSTEM_PROMPT, prompt, self.audit_dir)
            return self._from_llm_json(data)
        except Exception as exc:
            if self.mode == "llm":
                raise
            return self._analyze_local(log_text, engine_used="local", fallback_reason=str(exc))

    def analyze_file(self, log_path: Path, out_path: Path) -> FailureAnalysis:
        if self.audit_dir is None:
            self.audit_dir = out_path.parent / "failure_audit_logs"
        analysis = self.analyze(log_path.read_text(encoding="utf-8"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(analysis.to_markdown(), encoding="utf-8")
        return analysis

    def _from_llm_json(self, data: dict) -> FailureAnalysis:
        evidence = data.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        severity = data.get("severity") or data.get("suggested_severity") or "P2"
        return FailureAnalysis(
            failure_type=str(data.get("failure_type", "unknown")),
            confidence=confidence,
            summary=str(data.get("summary", "LLM 未给出摘要")),
            suspected_owner=str(data.get("suspected_owner", "unknown")),
            evidence=[str(item) for item in evidence],
            suggested_bug_title=str(data.get("suggested_bug_title", "自动化测试失败，需要复核")),
            suggested_severity=str(severity),
            engine_used="llm",
        )

    def _analyze_local(self, log_text: str, engine_used: str, fallback_reason: str | None = None) -> FailureAnalysis:
        lower = log_text.lower()
        evidence = [line.strip("- ").strip() for line in log_text.splitlines() if line.strip().startswith("-")]

        if "returned 200" in lower and "router" in lower:
            return FailureAnalysis(
                failure_type="real_bug",
                confidence=0.86,
                summary="登录接口返回成功，但浏览器仍停留在登录页，且前端路由出现导航错误。更像是真实前端路由缺陷，而不是测试脚本问题。",
                suspected_owner="frontend",
                evidence=evidence or ["接口成功但页面未跳转", "存在前端路由错误"],
                suggested_bug_title="登录成功后未跳转首页，疑似前端路由异常",
                suggested_severity="P1",
                engine_used=engine_used,
                fallback_reason=fallback_reason,
            )

        if "timeout" in lower:
            return FailureAnalysis(
                failure_type="environment_or_stability_issue",
                confidence=0.68,
                summary="日志中出现超时信号，建议先排查环境稳定性、服务响应时间和测试等待策略。",
                suspected_owner="qa/platform",
                evidence=evidence or ["出现 timeout 关键字"],
                suggested_bug_title="自动化执行超时，需确认环境或等待策略",
                suggested_severity="P2",
                engine_used=engine_used,
                fallback_reason=fallback_reason,
            )

        return FailureAnalysis(
            failure_type="needs_human_review",
            confidence=0.45,
            summary="当前日志信息不足，无法高置信度判断是真实缺陷、脚本问题还是环境问题。",
            suspected_owner="unknown",
            evidence=evidence or ["日志证据不足"],
            suggested_bug_title="自动化测试失败，需要人工复核",
            suggested_severity="P3",
            engine_used=engine_used,
            fallback_reason=fallback_reason,
        )
