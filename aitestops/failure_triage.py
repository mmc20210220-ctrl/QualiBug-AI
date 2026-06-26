from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from aitestops.llm_client import LLMClientError, OpenAICompatibleLLMClient
from aitestops.prompt_templates import SYSTEM_PROMPT

TriageMode = Literal["auto", "local", "llm"]


V5_FAILURE_TRIAGE_PROMPT = """请基于下面的失败证据包，做企业级自动化测试失败归因。只输出严格 JSON，不要输出 Markdown。

必须输出这个结构：
{
  "failure_type": "real_bug|test_data_issue|script_issue|environment_issue|flaky_issue|unknown",
  "confidence": 0.0,
  "suspected_owner": "frontend|backend|qa|devops|unknown",
  "severity": "P0|P1|P2|P3",
  "summary": "string",
  "root_cause_hypothesis": "string",
  "evidence": ["string"],
  "bug_title": "string",
  "bug_steps": ["string"],
  "expected_result": "string",
  "actual_result": "string",
  "next_actions": ["string"],
  "regression_recommendations": ["string"]
}

证据包：
{evidence_json}
"""


@dataclass
class FailureTriageResult:
    failure_type: str
    confidence: float
    suspected_owner: str
    severity: str
    summary: str
    root_cause_hypothesis: str
    evidence: List[str]
    bug_title: str
    bug_steps: List[str]
    expected_result: str
    actual_result: str
    next_actions: List[str]
    regression_recommendations: List[str]
    engine_used: str = "local"
    fallback_reason: Optional[str] = None

    def to_json_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        evidence_md = "\n".join(f"- {x}" for x in self.evidence) or "- none"
        steps_md = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(self.bug_steps)) or "1. 复现步骤不足，需要补充。"
        next_md = "\n".join(f"- {x}" for x in self.next_actions) or "- 人工复核"
        regression_md = "\n".join(f"- {x}" for x in self.regression_recommendations) or "- 修复后加入回归。"
        fallback = f"\n- fallback_reason: {self.fallback_reason}" if self.fallback_reason else ""
        return f"""# V5 AI 失败归因增强报告

## 归因结论

- analysis_engine: {self.engine_used}
- failure_type: {self.failure_type}
- confidence: {self.confidence:.2f}
- suspected_owner: {self.suspected_owner}
- severity: {self.severity}{fallback}

## 摘要

{self.summary}

## 根因假设

{self.root_cause_hypothesis}

## 证据链

{evidence_md}

## 缺陷草稿

**标题**：{self.bug_title}

**复现步骤**：

{steps_md}

**期望结果**：

{self.expected_result}

**实际结果**：

{self.actual_result}

## 建议下一步

{next_md}

## 回归建议

{regression_md}

## 企业落地说明

V5 不只读取一段失败日志，而是把 pytest 输出、接口响应、Trace 摘要、测试用例、相关 Git Diff 和 CI 上下文合并成证据包，再进行失败分类、责任域判断、严重级别建议和缺陷草稿生成。这样可以减少 QA 与开发之间反复沟通的时间。
"""

    def bug_draft_markdown(self) -> str:
        steps_md = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(self.bug_steps))
        evidence_md = "\n".join(f"- {x}" for x in self.evidence)
        return f"""# 缺陷报告草稿

## 标题

{self.bug_title}

## 严重级别

{self.severity}

## 责任域建议

{self.suspected_owner}

## 复现步骤

{steps_md}

## 期望结果

{self.expected_result}

## 实际结果

{self.actual_result}

## 证据

{evidence_md}

## 根因假设

{self.root_cause_hypothesis}
"""


class FailureEvidenceLoader:
    """Load a failure evidence folder into a compact structured JSON payload."""

    TEXT_FILES = {
        "pytest_output.txt",
        "trace_summary.json",
        "api_response.json",
        "test_case.json",
        "ci_context.json",
        "related_diff.diff",
        "failing_test_source.py",
        "screenshot_ocr.txt",
        "console_log.txt",
    }

    def load(self, evidence_dir: Path) -> Dict[str, Any]:
        if not evidence_dir.exists():
            raise FileNotFoundError(f"Evidence directory not found: {evidence_dir}")

        evidence: Dict[str, Any] = {
            "evidence_dir": str(evidence_dir),
            "files": sorted([p.name for p in evidence_dir.iterdir() if p.is_file()]),
        }
        for name in self.TEXT_FILES:
            path = evidence_dir / name
            if not path.exists():
                continue
            if path.suffix == ".json":
                try:
                    evidence[name] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    evidence[name] = path.read_text(encoding="utf-8", errors="replace")
            else:
                evidence[name] = self._clip(path.read_text(encoding="utf-8", errors="replace"), 8000)
        return evidence

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit] + "\n...[clipped]"


class FailureTriageEngine:
    """V5 enhanced failure triage with evidence package + LLM + local fallback."""

    def __init__(self, mode: TriageMode = "auto", audit_dir: Optional[Path] = None):
        self.mode = mode
        self.audit_dir = audit_dir
        self.llm_client = OpenAICompatibleLLMClient()
        self.loader = FailureEvidenceLoader()

    def analyze_dir(self, evidence_dir: Path, out_dir: Path) -> FailureTriageResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        evidence = self.loader.load(evidence_dir)
        (out_dir / "evidence_bundle.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

        result = self.analyze_evidence(evidence, out_dir)
        (out_dir / "triage_result.json").write_text(json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "failure_triage_report.md").write_text(result.to_markdown(), encoding="utf-8")
        (out_dir / "bug_draft.md").write_text(result.bug_draft_markdown(), encoding="utf-8")
        (out_dir / "regression_recommendations.json").write_text(json.dumps({
            "recommendations": result.regression_recommendations,
            "source": "v5_failure_triage",
            "suggested_priority": result.severity,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def analyze_evidence(self, evidence: Dict[str, Any], out_dir: Optional[Path] = None) -> FailureTriageResult:
        if self.mode == "local":
            return self._analyze_local(evidence, engine_used="local")

        try:
            if not self.llm_client.config.enabled:
                raise LLMClientError("LLM config is incomplete")
            audit = self.audit_dir or ((out_dir / "failure_triage_audit_logs") if out_dir else None)
            prompt = V5_FAILURE_TRIAGE_PROMPT.replace(
                "{evidence_json}", json.dumps(evidence, ensure_ascii=False, indent=2)
            )
            data = self.llm_client.complete_json(SYSTEM_PROMPT, prompt, audit)
            return self._from_llm_json(data)
        except Exception as exc:
            if self.mode == "llm":
                raise
            return self._analyze_local(evidence, engine_used="local", fallback_reason=str(exc))

    def _from_llm_json(self, data: Dict[str, Any]) -> FailureTriageResult:
        def as_list(value: Any) -> List[str]:
            if isinstance(value, list):
                return [str(x) for x in value]
            if value is None:
                return []
            return [str(value)]

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        return FailureTriageResult(
            failure_type=str(data.get("failure_type", "unknown")),
            confidence=confidence,
            suspected_owner=str(data.get("suspected_owner", "unknown")),
            severity=str(data.get("severity", "P2")),
            summary=str(data.get("summary", "LLM did not provide summary")),
            root_cause_hypothesis=str(data.get("root_cause_hypothesis", "unknown")),
            evidence=as_list(data.get("evidence")),
            bug_title=str(data.get("bug_title", data.get("suggested_bug_title", "自动化测试失败，需要复核"))),
            bug_steps=as_list(data.get("bug_steps")),
            expected_result=str(data.get("expected_result", "见测试断言")),
            actual_result=str(data.get("actual_result", "见失败日志")),
            next_actions=as_list(data.get("next_actions")),
            regression_recommendations=as_list(data.get("regression_recommendations")),
            engine_used="llm",
        )

    def _analyze_local(self, evidence: Dict[str, Any], engine_used: str, fallback_reason: Optional[str] = None) -> FailureTriageResult:
        pytest_output = str(evidence.get("pytest_output.txt", ""))
        api_response = evidence.get("api_response.json", {})
        trace = evidence.get("trace_summary.json", {})
        test_case = evidence.get("test_case.json", {})
        diff = str(evidence.get("related_diff.diff", ""))
        lower_all = "\n".join([
            pytest_output,
            json.dumps(api_response, ensure_ascii=False),
            json.dumps(trace, ensure_ascii=False),
            json.dumps(test_case, ensure_ascii=False),
            diff,
        ]).lower()

        expected_status = self._find_expected_status(pytest_output, test_case)
        actual_status = self._find_actual_status(pytest_output, api_response, trace)
        endpoint = self._find_endpoint(test_case, trace, api_response, pytest_output)

        evidence_lines: List[str] = []
        if endpoint:
            evidence_lines.append(f"失败接口/资源：{endpoint}")
        if expected_status is not None or actual_status is not None:
            evidence_lines.append(f"状态码断言：expected={expected_status}, actual={actual_status}")
        if "assert" in pytest_output.lower():
            evidence_lines.append("pytest 输出包含断言失败。")
        if "x-role" in lower_all or "permission" in lower_all or "admin" in lower_all or "forbidden" in lower_all:
            evidence_lines.append("证据中出现权限/管理员资源相关信息。")
        if diff.strip():
            matched = self._summarize_diff(diff)
            if matched:
                evidence_lines.append(f"相关代码变更：{matched}")

        # Permission bypass: expected 403 but actual 200.
        if expected_status == 403 and actual_status == 200:
            return FailureTriageResult(
                failure_type="real_bug",
                confidence=0.91,
                suspected_owner="backend",
                severity="P1",
                summary="普通用户访问管理员接口时预期被拒绝，但实际返回成功，属于高风险权限控制缺陷。",
                root_cause_hypothesis="服务端权限校验逻辑可能被改动或绕过。相关接口应在返回用户列表前强制校验 X-Role/admin 权限。",
                evidence=evidence_lines or ["expected 403 but actual 200"],
                bug_title="普通用户可访问管理员用户列表接口，疑似权限绕过",
                bug_steps=[
                    "使用普通用户身份或 X-Role=user 请求管理员接口。",
                    f"调用接口：{endpoint or 'GET /admin/users'}。",
                    "观察接口响应状态码和响应体。",
                ],
                expected_result="接口应返回 403 FORBIDDEN，不应返回管理员用户列表。",
                actual_result="接口返回 200，说明普通用户可能成功访问管理员资源。",
                next_actions=[
                    "后端检查管理员接口权限中间件或角色判断逻辑。",
                    "补充权限绕过单元测试、接口测试和发版门禁。",
                    "排查近期 Git Diff 中与 admin/users、role、permission 相关的改动。",
                ],
                regression_recommendations=[
                    "新增普通用户访问所有 admin 接口必须返回 403 的契约测试。",
                    "把 admin 权限测试加入 PR 阶段 P0 冒烟集。",
                    "将本次失败证据沉淀为防回归测试资产。",
                ],
                engine_used=engine_used,
                fallback_reason=fallback_reason,
            )

        # Backend status regression.
        if expected_status is not None and actual_status is not None and expected_status != actual_status:
            return FailureTriageResult(
                failure_type="real_bug",
                confidence=0.78,
                suspected_owner="backend",
                severity="P2",
                summary="接口实际状态码与测试契约预期不一致，更像后端行为回归或接口契约变更未同步。",
                root_cause_hypothesis="接口响应状态码变化，可能来自业务逻辑变更、异常处理变更或 OpenAPI 契约未同步。",
                evidence=evidence_lines or [f"expected={expected_status}, actual={actual_status}"],
                bug_title=f"接口响应状态码与契约不一致：expected {expected_status}, actual {actual_status}",
                bug_steps=[
                    f"调用接口：{endpoint or 'unknown endpoint'}。",
                    "使用证据包中的请求头和请求体复现。",
                    "对比 OpenAPI/测试 DSL 中的 expected_status。",
                ],
                expected_result=f"接口应返回 {expected_status}。",
                actual_result=f"接口实际返回 {actual_status}。",
                next_actions=["确认接口契约是否变更。", "后端检查响应码处理逻辑。", "QA 同步测试资产或缺陷单。"],
                regression_recommendations=["保留该接口状态码契约测试。", "将该接口加入精准回归影响面规则。"],
                engine_used=engine_used,
                fallback_reason=fallback_reason,
            )

        if "timeout" in lower_all:
            return FailureTriageResult(
                failure_type="environment_issue",
                confidence=0.66,
                suspected_owner="devops",
                severity="P2",
                summary="失败证据包含 timeout，优先排查环境稳定性、服务响应时间和网络问题。",
                root_cause_hypothesis="测试环境或依赖服务响应不稳定，导致自动化超时。",
                evidence=evidence_lines or ["timeout keyword detected"],
                bug_title="自动化测试执行超时，疑似环境稳定性问题",
                bug_steps=["重新执行失败用例。", "检查服务健康状态和 CI 节点资源。"],
                expected_result="测试应在超时时间内稳定完成。",
                actual_result="测试发生 timeout。",
                next_actions=["检查环境监控。", "复跑确认是否偶现。", "必要时调整等待策略。"],
                regression_recommendations=["记录 flaky 标签并观察三次执行结果。"],
                engine_used=engine_used,
                fallback_reason=fallback_reason,
            )

        return FailureTriageResult(
            failure_type="unknown",
            confidence=0.42,
            suspected_owner="unknown",
            severity="P3",
            summary="证据不足，无法高置信度判断失败类型。",
            root_cause_hypothesis="需要补充接口响应、Trace、截图、日志或相关代码 Diff。",
            evidence=evidence_lines or ["No strong local heuristic matched"],
            bug_title="自动化测试失败，需要人工复核",
            bug_steps=["查看 evidence_bundle.json 中的原始证据。"],
            expected_result="见测试断言。",
            actual_result="见失败日志。",
            next_actions=["补充更完整的失败证据。", "由 QA/开发共同复核。"],
            regression_recommendations=["复核后再决定是否沉淀回归资产。"],
            engine_used=engine_used,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _find_expected_status(pytest_output: str, test_case: Dict[str, Any]) -> Optional[int]:
        for key in ["expected_status", "expected_status_code"]:
            if isinstance(test_case, dict) and isinstance(test_case.get(key), int):
                return int(test_case[key])
        m = re.search(r"assert\s+\d+\s*==\s*(\d+)", pytest_output)
        if m:
            return int(m.group(1))
        m = re.search(r"expected[=: ]+(\d{3})", pytest_output, re.I)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _find_actual_status(pytest_output: str, api_response: Any, trace: Any) -> Optional[int]:
        if isinstance(api_response, dict):
            for key in ["status_code", "actual_status", "status"]:
                if isinstance(api_response.get(key), int):
                    return int(api_response[key])
            if isinstance(api_response.get("response"), dict) and isinstance(api_response["response"].get("status_code"), int):
                return int(api_response["response"]["status_code"])
        if isinstance(trace, dict):
            for key in ["actual_status", "status_code"]:
                if isinstance(trace.get(key), int):
                    return int(trace[key])
        m = re.search(r"assert\s+(\d+)\s*==\s*\d+", pytest_output)
        if m:
            return int(m.group(1))
        m = re.search(r"actual[=: ]+(\d{3})", pytest_output, re.I)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _find_endpoint(test_case: Dict[str, Any], trace: Any, api_response: Any, pytest_output: str) -> str:
        if isinstance(test_case, dict):
            method = test_case.get("method") or ""
            path = test_case.get("path") or test_case.get("endpoint") or ""
            if path:
                return (str(method) + " " + str(path)).strip()
        if isinstance(trace, dict) and trace.get("endpoint"):
            return str(trace["endpoint"])
        if isinstance(api_response, dict) and api_response.get("endpoint"):
            return str(api_response["endpoint"])
        m = re.search(r"(GET|POST|PUT|DELETE|PATCH)\s+(/[A-Za-z0-9_/{}/.-]+)", pytest_output)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        return ""

    @staticmethod
    def _summarize_diff(diff: str) -> str:
        hits = []
        for line in diff.splitlines():
            l = line.strip()
            if "admin" in l.lower() or "role" in l.lower() or "permission" in l.lower() or "forbidden" in l.lower():
                hits.append(l[:180])
        return "; ".join(hits[:3])
