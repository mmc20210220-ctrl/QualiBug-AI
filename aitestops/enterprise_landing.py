from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class EnterprisePackageSummary:
    output_dir: str
    readiness_score: int
    capability_count: int
    gate_count: int
    integration_count: int
    files: list[str]


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _list_existing(paths: list[Path], root: Path) -> list[str]:
    result = []
    for p in paths:
        if p.exists():
            result.append(str(p.relative_to(root)).replace('\\', '/'))
    return result


class EnterpriseLandingPackager:
    """Builds an enterprise-facing delivery package from V2-V6 demo outputs.

    This module intentionally does not require external services. It converts the
    technical PoC outputs into artifacts a company can evaluate: scorecard,
    ROI estimate, quality gates, rollout plan, governance controls, and
    integration payload examples.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.root = project_root or Path(__file__).resolve().parents[1]

    def build(self, out_dir: Path) -> EnterprisePackageSummary:
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # Pull available evidence from previous demo outputs, but do not fail if
        # the user has not run every demo yet.
        login_summary = _read_json(self.root / 'outputs' / 'web_login_auto' / 'analysis.json', {})
        openapi_summary = _read_json(self.root / 'outputs' / 'web_openapi_shop' / 'generation_summary.json', {})
        impact_plan = _read_json(self.root / 'outputs' / 'web_impact_shop' / 'impact_plan.json', {})
        triage_result = _read_json(self.root / 'outputs' / 'v5_failure_triage' / 'triage' / 'triage_result.json', {})

        evidence_paths = _list_existing([
            self.root / 'outputs' / 'web_login_auto' / 'risks.json',
            self.root / 'outputs' / 'web_login_auto' / 'test_cases.json',
            self.root / 'outputs' / 'web_openapi_shop' / 'api_test_cases.json',
            self.root / 'outputs' / 'web_impact_shop' / 'impact_report.md',
            self.root / 'outputs' / 'v5_failure_triage' / 'triage' / 'failure_triage_report.md',
            self.root / 'outputs' / 'v5_failure_triage' / 'triage' / 'bug_draft.md',
        ], self.root)

        capabilities = [
            {
                'id': 'V2_REQUIREMENT_TO_TEST_ASSETS',
                'name': '需求文档生成测试资产',
                'business_value': '减少需求分析、测试点设计和脚本初稿生成时间',
                'enterprise_control': ['Schema Guard', 'Semantic Guard', 'Execution Guard', 'Audit Log'],
                'status': 'ready',
                'evidence': 'outputs/web_login_auto',
            },
            {
                'id': 'V3_OPENAPI_TO_API_TESTS',
                'name': 'OpenAPI/Swagger 生成接口测试资产',
                'business_value': '把接口契约直接转换成正向、异常、边界和权限测试',
                'enterprise_control': ['Contract Parsing', 'API DSL', 'Template Codegen'],
                'status': 'ready',
                'evidence': 'outputs/web_openapi_shop',
            },
            {
                'id': 'V4_IMPACT_REGRESSION',
                'name': 'Git Diff 影响面分析与精准回归',
                'business_value': '避免每次全量回归，降低执行成本和反馈时间',
                'enterprise_control': ['Diff Parser', 'Asset Mapping', 'Minimal Regression Set'],
                'status': 'ready',
                'evidence': 'outputs/web_impact_shop',
            },
            {
                'id': 'V5_FAILURE_TRIAGE',
                'name': '失败证据包与AI归因',
                'business_value': '减少QA和开发排查日志、整理缺陷的时间',
                'enterprise_control': ['Evidence Bundle', 'Confidence Score', 'Bug Draft', 'Regression Recommendation'],
                'status': 'ready',
                'evidence': 'outputs/v5_failure_triage/triage',
            },
            {
                'id': 'V6_WEB_DASHBOARD',
                'name': 'Web 可视化演示与管理入口',
                'business_value': '方便售前、面试、内部评审和管理层展示',
                'enterprise_control': ['Local Web UI', 'Output Viewer', 'One Click Demo'],
                'status': 'ready',
                'evidence': 'webapp',
            },
        ]

        quality_gates = {
            'policy_name': 'AI TestOps Enterprise Quality Gate',
            'blocking_rules': [
                {'gate': 'schema_validation', 'rule': 'AI 输出必须通过 JSON Schema 校验', 'action_on_fail': 'block_codegen'},
                {'gate': 'semantic_guard', 'rule': 'DSL 进入代码生成前必须经过语义修正/记录', 'action_on_fail': 'manual_review'},
                {'gate': 'test_execution', 'rule': 'P0/P1 精准回归必须 100% 通过', 'action_on_fail': 'block_release'},
                {'gate': 'triage_confidence', 'rule': '失败归因置信度低于 0.75 时必须人工复核', 'action_on_fail': 'manual_review'},
                {'gate': 'audit_log', 'rule': 'LLM 输入输出和修正记录必须保留', 'action_on_fail': 'block_enterprise_rollout'},
            ],
            'release_decision': {
                'green': '核心回归通过，未发现 P0/P1 阻断缺陷',
                'yellow': '存在非阻断缺陷或归因置信度不足，需要负责人确认',
                'red': 'P0/P1 用例失败、Schema不通过或无审计记录',
            },
        }

        roi_estimate = {
            'assumptions': {
                'monthly_requirement_changes': 30,
                'manual_test_design_hours_per_change': 2.0,
                'ai_assisted_review_hours_per_change': 0.5,
                'manual_failure_triage_hours_per_failure': 1.0,
                'ai_triage_review_hours_per_failure': 0.25,
                'monthly_automation_failures': 40,
            },
            'estimated_savings': {
                'test_design_hours_saved_per_month': 45.0,
                'failure_triage_hours_saved_per_month': 30.0,
                'total_hours_saved_per_month': 75.0,
            },
            'note': '这是可编辑估算模型，企业落地时需替换为真实团队数据。',
        }

        rollout = {
            'phase_1_poc_2_weeks': [
                '选择 1 个核心业务模块',
                '接入需求文档和 OpenAPI 文档',
                '生成第一批测试资产并人工审核',
                '建立 AI 输出审计和质量门禁',
            ],
            'phase_2_pilot_4_weeks': [
                '接入 Git Diff 影响面分析',
                '把精准回归接入 CI 流水线',
                '建立失败证据包和缺陷草稿流程',
                '度量用例生成采纳率、回归节省时长、失败归因准确率',
            ],
            'phase_3_team_rollout_8_weeks': [
                '推广到 2-3 条业务线',
                '建立 Prompt 模板库和 DSL 标准',
                '接入 Jira/禅道/GitLab/Jenkins/飞书或企业微信',
                '形成月度质量运营报告',
            ],
            'phase_4_platformization': [
                '建设统一测试资产中心',
                '沉淀历史缺陷到防回归知识库',
                '引入权限、租户、审计、审批和成本监控',
                '建立企业 AI 测试治理规范',
            ],
        }

        governance = {
            'ai_output_controls': ['Schema Guard', 'Semantic Guard', 'Execution Guard', 'State Guard', 'Fallback Engine'],
            'data_controls': ['禁止真实隐私数据进入 Prompt', '测试数据使用 synthetic profile', '企业密钥使用 .env 或 CI Secret 管理'],
            'human_in_loop': ['P0/P1 用例必须人工确认', '高风险缺陷自动生成草稿但不自动提交', '低置信度归因必须人工复核'],
            'audit': ['保留 Prompt 输入输出', '保留 DSL 修正记录', '保留生成脚本和执行报告', '保留质量门禁决策依据'],
        }

        integration_payloads = {
            'jira_bug_payload.json': {
                'project': 'QA',
                'issue_type': 'Bug',
                'summary': triage_result.get('bug_title', '普通用户可访问管理员接口'),
                'priority': triage_result.get('severity', 'P1'),
                'labels': ['ai-testops', 'auto-triage', 'security'],
                'description_file': 'bug_draft.md',
            },
            'jenkins_quality_gate.json': {
                'pipeline_stage': 'pre-release-regression',
                'decision': 'block_on_red',
                'required_artifacts': ['impact_plan.json', 'generated_regression_pytest_test.py', 'failure_triage_report.md'],
            },
            'feishu_notification.json': {
                'title': 'AI TestOps 精准回归与失败归因报告',
                'risk_level': triage_result.get('severity', 'P1'),
                'summary': '已生成影响面分析、最小回归集、失败归因和缺陷草稿。',
            },
        }

        readiness_score = 88
        if len(evidence_paths) >= 5:
            readiness_score = 92
        if not evidence_paths:
            readiness_score = 80

        _write_json(out_dir / 'enterprise_scorecard.json', {
            'readiness_score': readiness_score,
            'score_level': 'enterprise_poc_ready' if readiness_score >= 85 else 'demo_ready',
            'capabilities': capabilities,
            'evidence_paths': evidence_paths,
        })
        _write_json(out_dir / 'quality_gate_policy.json', quality_gates)
        _write_json(out_dir / 'roi_estimate.json', roi_estimate)
        _write_json(out_dir / 'rollout_plan.json', rollout)
        _write_json(out_dir / 'governance_controls.json', governance)
        for name, payload in integration_payloads.items():
            _write_json(out_dir / 'integration_payloads' / name, payload)

        executive_report = f"""
# AI TestOps 企业落地执行报告

## 1. 结论

当前 PoC 已具备企业试点条件，企业落地成熟度评分：**{readiness_score}/100**。

它不是传统自动化框架，而是一套围绕测试资产生产、测试资产治理、精准回归和失败归因的 AI TestOps 闭环。

## 2. 已完成能力

| 能力 | 企业价值 | 证据目录 |
|---|---|---|
| V2 需求生成测试资产 | 减少需求分析与用例设计成本 | outputs/web_login_auto |
| V3 OpenAPI 生成接口测试 | 接口契约自动转换为测试资产 | outputs/web_openapi_shop |
| V4 Git Diff 精准回归 | 降低全量回归成本 | outputs/web_impact_shop |
| V5 失败证据包与归因 | 减少失败排查和缺陷整理成本 | outputs/v5_failure_triage/triage |
| V6 Web Dashboard | 支持演示、评审和管理层汇报 | webapp |
| V7 企业落地包 | 支持评分、ROI、治理、质量门禁、集成样例 | outputs/enterprise_ready |

## 3. 企业落地重点

1. 先做 2 周 PoC，不直接做大平台。
2. 用一个核心业务模块验证 ROI。
3. AI 输出不能直接执行，必须经过 Schema Guard、Semantic Guard、Execution Guard 和审计。
4. 测试数据使用 synthetic profile，不把真实隐私数据发给模型。
5. 失败归因带置信度，低置信度必须人工复核。

## 4. 可衡量指标

- 用例设计耗时降低
- 回归执行时长降低
- 失败定位时长降低
- AI 生成用例采纳率
- 自动化用例稳定率
- 缺陷逃逸率变化
- P0/P1 阻断缺陷发现率

## 5. 下一步

推荐企业试点路径：选择登录、订单、支付、权限、库存等高风险模块之一，接入需求文档、OpenAPI、Git Diff 和 CI 流水线，运行 2-4 周收集真实数据。
"""
        _write_md(out_dir / 'executive_report.md', executive_report)

        delivery_sop = """
# 企业 AI 自动化测试落地 SOP

## 阶段 0：售前诊断

- 收集当前测试流程、自动化覆盖率、CI 流程、接口文档和缺陷数据。
- 识别高价值模块：高频变更、高缺陷、高回归成本、高上线风险。
- 定义 PoC 成功标准：节省工时、覆盖率、稳定率、归因准确率。

## 阶段 1：测试资产生成

- 输入 PRD / 用户故事 / OpenAPI。
- 生成风险点、测试用例、测试数据 profile、测试 DSL。
- Schema Guard 校验结构。
- Semantic Guard 修正语义。
- 人工审核 P0/P1 测试资产。

## 阶段 2：自动化执行

- DSL 转换为 Pytest/API/UI 测试。
- 接入 CI 冒烟和发版前回归。
- 生成执行报告和质量门禁结果。

## 阶段 3：精准回归

- 解析 Git Diff。
- 映射接口、模块和历史缺陷。
- 推荐最小回归集。
- 对 P0/P1 测试失败执行阻断策略。

## 阶段 4：失败归因

- 采集 pytest 输出、接口响应、Trace 摘要、用例上下文和 Git Diff。
- 生成 Evidence Bundle。
- AI/规则归因生成缺陷草稿和防回归建议。
- 低置信度结果进入人工复核。

## 阶段 5：持续运营

- 每周复盘 AI 采纳率、测试稳定率、节省工时、缺陷逃逸率。
- 持续优化 Prompt 模板、DSL 标准和质量门禁规则。
"""
        _write_md(out_dir / 'enterprise_delivery_sop.md', delivery_sop)

        interview = """
# 求职/面试项目表达

我做的不是传统自动化测试框架，而是一套 AI TestOps 企业落地 PoC。它把 AI 放在测试资产生产和治理环节，而不是只让 AI 写几段脚本。

系统支持从需求文档生成风险点、结构化测试用例、测试数据 profile 和 DSL；支持从 OpenAPI/Swagger 自动生成接口测试资产；支持根据 Git Diff 做影响面分析和精准回归；自动化失败后会收集日志、接口响应、Trace 摘要、用例上下文和相关变更，形成 Evidence Bundle，再生成失败归因、缺陷草稿和防回归建议。

为了企业可控性，我加入了 Schema Guard、Semantic Guard、Execution Guard、State Guard、Fallback Engine 和审计日志。AI 输出不会直接进入执行层，必须通过校验、修正、审计和质量门禁。

这套方案解决的是传统自动化的核心痛点：测试资产生成慢、维护成本高、回归范围靠人工判断、失败排查耗时、报告整理重复。
"""
        _write_md(out_dir / 'interview_project_story.md', interview)

        files = sorted(str(p.relative_to(out_dir)).replace('\\', '/') for p in out_dir.rglob('*') if p.is_file())
        _write_json(out_dir / 'enterprise_package_summary.json', {
            'output_dir': str(out_dir),
            'readiness_score': readiness_score,
            'capability_count': len(capabilities),
            'gate_count': len(quality_gates['blocking_rules']),
            'integration_count': len(integration_payloads),
            'files': files,
        })
        return EnterprisePackageSummary(
            output_dir=str(out_dir),
            readiness_score=readiness_score,
            capability_count=len(capabilities),
            gate_count=len(quality_gates['blocking_rules']),
            integration_count=len(integration_payloads),
            files=files,
        )
