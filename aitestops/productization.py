
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


@dataclass
class ProductPackageSummary:
    output_dir: str
    product_name: str
    version: str
    module_count: int
    persona_count: int
    file_count: int
    files: list[str]


class ProductReadyPackager:
    product_name = "AI Test Asset Center"
    version = "v8_product_ready"

    def build(self, out_dir: Path) -> ProductPackageSummary:
        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[str] = []

        product_manifest = {
            "product_name": self.product_name,
            "tagline": "把需求、接口文档、代码变更和失败证据转化为可执行测试资产的 AI TestOps 产品",
            "version": self.version,
            "target_customers": ["中小型研发团队", "SaaS/电商/金融科技研发团队", "正在导入 AI 提效的测试团队"],
            "core_job_to_be_done": "让 QA 团队从人工维护测试资产，升级为 AI 生成、治理、执行和复盘测试资产。",
            "north_star_metric": "每周由 AI 生成并通过治理进入回归资产库的有效测试资产数量",
            "product_principles": [
                "AI 生成资产，但不直接失控执行",
                "所有 AI 输出必须结构化、可校验、可审计",
                "优先降低用例设计、数据准备、回归选择和失败分析成本",
                "先做 PoC 闭环，再接企业系统，再规模化治理",
            ],
            "demo_flows": [
                "需求文档 -> 测试资产 -> Pytest",
                "OpenAPI -> 接口测试资产 -> Pytest",
                "Git Diff -> 影响面分析 -> 精准回归",
                "失败证据包 -> AI 归因 -> 缺陷草稿",
                "企业成熟度 -> ROI -> 治理 -> 试点路线",
                "产品化工作台 -> 用户角色 -> 指标 -> 路线图",
            ],
        }
        write_json(out_dir / "product_manifest.json", product_manifest); files.append("product_manifest.json")

        personas = [
            {"persona": "QA Lead / 测试负责人", "goals": ["提升测试覆盖和发布信心", "减少回归周期", "向管理层证明 AI 提效 ROI"], "pain_points": ["测试用例维护成本高", "需求变化后回归范围难判断", "自动化失败后排查耗时"], "main_features": ["成熟度评分", "精准回归", "质量门禁", "ROI Dashboard"]},
            {"persona": "Automation QA / 测试开发", "goals": ["快速生成稳定可维护的测试资产", "减少重复脚本编写", "让失败定位更快"], "pain_points": ["脚本脆弱", "测试数据难维护", "接口用例要手工补边界"], "main_features": ["DSL 生成", "Schema/Semantic Guard", "OpenAPI 测试生成", "失败证据包"]},
            {"persona": "Developer / 开发工程师", "goals": ["尽早发现回归问题", "快速知道失败是不是自己的改动导致", "减少和 QA 来回沟通"], "pain_points": ["缺陷复现信息不足", "CI 失败原因不清楚", "不知道要跑哪些回归"], "main_features": ["Git Diff 影响面", "AI 归因", "缺陷草稿", "CI 质量门禁"]},
            {"persona": "Engineering Manager / 研发管理者", "goals": ["降低发布风险", "控制测试成本", "推动 AI 工程化落地"], "pain_points": ["AI 提效难量化", "测试投入产出不透明", "自动化 ROI 难解释"], "main_features": ["管理层报告", "ROI 估算", "采用率漏斗", "分阶段 rollout"]},
            {"persona": "Security/Compliance / 安全合规", "goals": ["防止敏感数据进入大模型", "确保 AI 输出可追溯", "控制企业风险"], "pain_points": ["AI 黑盒", "数据合规不清晰", "审计记录缺失"], "main_features": ["审计日志", "脱敏策略", "模型治理", "权限角色"]},
        ]
        write_json(out_dir / "persona_roles.json", personas); files.append("persona_roles.json")

        feature_matrix = [
            {"module": "Requirement Asset Generator", "stage": "MVP", "user_value": "从需求生成风险点、用例、数据 Profile、DSL", "enterprise_value": "降低测试设计时间"},
            {"module": "OpenAPI Test Generator", "stage": "MVP", "user_value": "从接口契约生成正向/异常/边界/权限测试", "enterprise_value": "接口测试资产快速补齐"},
            {"module": "Impact Regression", "stage": "Beta", "user_value": "基于 Git Diff 推荐最小回归集", "enterprise_value": "减少无效回归执行"},
            {"module": "Failure Triage", "stage": "Beta", "user_value": "失败证据包归因并生成缺陷草稿", "enterprise_value": "减少 QA/开发沟通成本"},
            {"module": "Governance Center", "stage": "Beta", "user_value": "Schema/Semantic/Execution/State Guard", "enterprise_value": "AI 输出可控、可审计"},
            {"module": "Quality Intelligence Dashboard", "stage": "Roadmap", "user_value": "测试资产健康、失败类型、节省工时可视化", "enterprise_value": "管理层可量化决策"},
            {"module": "Enterprise Integrations", "stage": "Roadmap", "user_value": "Jira/Jenkins/GitLab/飞书/企业微信", "enterprise_value": "接入现有研发流程"},
        ]
        write_json(out_dir / "feature_matrix.json", feature_matrix); files.append("feature_matrix.json")

        workspace = {
            "workspace_id": "demo-retail-qa",
            "workspace_name": "电商接口质量工作台",
            "tenant_mode": "single-tenant demo / enterprise multi-tenant ready",
            "projects": [
                {"name": "Shop API", "inputs": ["OpenAPI", "Git Diff", "Failure Evidence"], "health": "green", "last_run": "demo"},
                {"name": "Login/RBAC", "inputs": ["Requirement", "Failure Log"], "health": "yellow", "last_run": "demo"},
            ],
            "roles": {
                "admin": ["manage_workspace", "manage_models", "view_audit", "run_all"],
                "qa_lead": ["approve_assets", "configure_quality_gate", "view_roi"],
                "qa_engineer": ["generate_assets", "run_tests", "triage_failures"],
                "developer": ["view_impact", "view_bug_draft", "rerun_regression"],
                "viewer": ["view_reports"],
            },
            "asset_lifecycle": ["draft", "schema_checked", "semantic_checked", "approved", "executable", "retired"],
        }
        write_json(out_dir / "workspace_demo.json", workspace); files.append("workspace_demo.json")

        metrics = {
            "north_star": {"metric": "有效测试资产生成数", "current_demo": 18, "target_90_days": 300},
            "activation_metrics": [
                {"metric": "从需求生成测试资产成功率", "demo_value": "100%"},
                {"metric": "OpenAPI 生成测试执行通过率", "demo_value": "8/8 passed"},
                {"metric": "影响面推荐用例数", "demo_value": "5 selected"},
                {"metric": "失败归因置信度", "demo_value": "0.91"},
            ],
            "business_metrics": [
                {"metric": "用例设计节省工时", "measurement": "人工基线 - AI 生成审核耗时"},
                {"metric": "回归执行节省时间", "measurement": "全量回归集数量 - 精准回归集数量"},
                {"metric": "缺陷报告整理节省时间", "measurement": "人工整理耗时 - AI 草稿审核耗时"},
                {"metric": "AI 输出采纳率", "measurement": "被 QA 通过的资产 / AI 生成资产"},
            ],
            "guardrail_metrics": ["Schema 通过率", "Semantic 修正数", "Fallback 次数", "人工审核拒绝率", "敏感数据拦截次数"],
        }
        write_json(out_dir / "product_metrics_dashboard.json", metrics); files.append("product_metrics_dashboard.json")

        onboarding = {
            "day_0_precheck": ["选择 1 个低风险业务模块", "准备需求文档/OpenAPI/Git Diff 示例", "确认不上传真实敏感数据", "确定 PoC 成功指标"],
            "day_1_setup": ["配置模型或使用 local fallback", "导入 OpenAPI", "跑通接口测试生成", "确认审计日志"],
            "week_1_poc": ["生成 20-50 条测试资产", "QA 审核采纳率", "接入一次 CI 冒烟", "输出第一版 ROI 报告"],
            "week_2_to_4_expansion": ["接入 Jira/Jenkins/GitLab", "配置质量门禁", "建立失败归因 SOP", "准备管理层复盘"],
        }
        write_json(out_dir / "onboarding_checklist.json", onboarding); files.append("onboarding_checklist.json")

        pricing = {
            "internal_poc": {"target": "求职/内部试点", "price_model": "免费 PoC / 内部项目", "limits": ["单项目", "本地执行", "样例集成"]},
            "team_edition": {"target": "10-30 人测试/研发团队", "price_model": "按项目或按月", "key_features": ["需求/OpenAPI 生成", "精准回归", "失败归因", "基础治理"]},
            "enterprise_edition": {"target": "多团队企业", "price_model": "私有化/企业订阅", "key_features": ["SSO/RBAC", "模型网关", "审计", "企业集成", "多工作区", "质量仪表盘"]},
            "service_package": {"target": "副业/咨询交付", "price_model": "4 周 PoC 项目", "deliverables": ["现状诊断", "PoC 闭环", "ROI 报告", "SOP", "推广路线"]},
        }
        write_json(out_dir / "packaging_and_pricing.json", pricing); files.append("packaging_and_pricing.json")

        roadmap = [
            {"phase": "0-30 天", "theme": "产品 Demo 可演示", "deliverables": ["Web Dashboard", "需求/OpenAPI/Git Diff/失败归因闭环", "产品化 PRD"]},
            {"phase": "31-60 天", "theme": "企业 PoC 可试点", "deliverables": ["接 Jira/Jenkins/GitLab", "审批流", "资产采纳率", "CI 质量门禁"]},
            {"phase": "61-90 天", "theme": "团队级可运营", "deliverables": ["资产库", "质量趋势", "ROI 仪表盘", "模型治理策略"]},
            {"phase": "90 天后", "theme": "企业级平台化", "deliverables": ["多租户", "SSO/RBAC", "私有化模型网关", "合规审计", "插件市场"]},
        ]
        write_json(out_dir / "product_roadmap.json", roadmap); files.append("product_roadmap.json")

        backlog = [
            {"id": "P0-001", "title": "工作区与项目管理", "priority": "P0", "why": "让产品从 demo 变成可管理的团队工具"},
            {"id": "P0-002", "title": "资产审批流", "priority": "P0", "why": "AI 生成资产必须经过 QA 审核进入可执行库"},
            {"id": "P0-003", "title": "质量门禁配置 UI", "priority": "P0", "why": "企业需要可解释的阻断/放行规则"},
            {"id": "P1-004", "title": "Jira/Jenkins/GitLab 集成", "priority": "P1", "why": "接入现有研发流程，降低 adoption 成本"},
            {"id": "P1-005", "title": "模型网关与敏感数据脱敏", "priority": "P1", "why": "支持企业安全合规"},
            {"id": "P1-006", "title": "资产健康分与老化检测", "priority": "P1", "why": "减少测试资产长期维护成本"},
        ]
        write_json(out_dir / "product_backlog.json", backlog); files.append("product_backlog.json")

        api_contracts = """# Product API Contracts

## POST /api/product/run
Run product-level demo and generate product artifacts.

## GET /api/product/overview
Return product manifest, personas, feature matrix and metrics.

## GET /api/read?path=outputs/product_ready/...
Read generated product documents.

## Future APIs
- POST /api/workspaces
- POST /api/projects/{id}/generate-assets
- POST /api/assets/{id}/approve
- POST /api/quality-gates/evaluate
- GET /api/audit-events
"""
        write_md(out_dir / "product_api_contracts.md", api_contracts); files.append("product_api_contracts.md")

        prd = f"""# {self.product_name} 产品级 PRD

## 1. 产品定位
{self.product_name} 是一个 AI TestOps 产品，用于把需求文档、OpenAPI、Git Diff 和失败证据包转化为可治理、可执行、可审计的测试资产。

## 2. 核心问题
传统自动化测试只解决“执行”，但企业真正成本集中在测试资产的设计、数据准备、脚本维护、回归范围判断和失败分析。

## 3. 目标用户
- QA Lead：关注覆盖率、质量门禁和 ROI。
- 测试开发：关注 DSL、执行稳定性和失败定位。
- 开发工程师：关注影响面、回归范围和缺陷复现。
- 研发管理者：关注发布风险和 AI 提效指标。
- 安全合规：关注数据、审计和模型治理。

## 4. MVP 范围
- 需求生成测试资产。
- OpenAPI 生成接口测试。
- Git Diff 精准回归。
- 失败证据包归因。
- 企业落地包与产品指标。
- Web 演示工作台。

## 5. 非目标
- 不替代测试负责人做最终质量判断。
- 不让 LLM 直接无校验地生成生产脚本。
- 不在 PoC 阶段接入真实敏感生产数据。

## 6. 产品护城河
- 测试资产 DSL，而不是直接生成散乱代码。
- Schema Guard + Semantic Guard + Execution Guard + State Guard。
- Evidence Bundle 归因模型。
- 从技术执行到 ROI、治理、SOP 的完整企业闭环。
"""
        write_md(out_dir / "product_prd.md", prd); files.append("product_prd.md")

        gtm = """# 售前 / 求职产品化讲法

我不是做一个传统自动化测试框架，而是把它产品化成 AI Test Asset Center。

这个产品解决的是企业测试资产生产和维护成本高的问题。它可以从需求文档、OpenAPI、Git Diff 和失败证据包出发，自动生成测试资产、接口测试、精准回归计划和失败归因报告。

产品级设计包括工作区、角色权限、资产生命周期、质量门禁、审计日志、ROI 指标、企业集成和分阶段落地路线。也就是说，它不仅能跑 demo，还能解释企业如何试点、如何治理、如何评估收益。

面试时我会强调：AI 的价值不是帮我写几行测试代码，而是让测试资产从“人工维护”升级为“AI 生成 + 规则治理 + 人工审核 + 自动执行 + 智能复盘”。
"""
        write_md(out_dir / "gtm_pitch.md", gtm); files.append("gtm_pitch.md")

        demo_script = """# 产品级演示脚本

1. 打开 Web Dashboard，先讲产品定位：AI Test Asset Center 是 AI TestOps 工作台。
2. 运行 V2，展示需求如何转成风险点、测试用例、数据 Profile、DSL、Pytest。
3. 运行 V3，展示 OpenAPI 如何转成接口测试资产。
4. 运行 V4，展示 Git Diff 如何推荐最小回归集。
5. 运行 V5，展示失败证据包如何生成归因报告和缺陷草稿。
6. 运行 V7，展示企业成熟度、ROI、治理和 SOP。
7. 运行 V8，展示产品定位、用户角色、工作区、指标、路线图和商业包装。
8. 最后总结：这不是自动化脚本工具，而是测试资产生成、治理、执行和运营平台。
"""
        write_md(out_dir / "product_demo_script.md", demo_script); files.append("product_demo_script.md")

        security = """# 安全与合规产品模型

## 数据分级
- L0：公开接口文档，可直接使用。
- L1：内部需求文档，需要脱敏。
- L2：日志和失败证据，需要移除 Token、手机号、邮箱、身份证、订单号。
- L3：生产数据，PoC 阶段禁止发送到外部模型。

## 模型调用原则
- 优先通过企业模型网关。
- 所有 prompt 和 response 进入审计日志。
- 高风险资产必须人工审核。
- LLM 失败时 fallback 到 local engine。

## 输出治理
- JSON Schema 校验。
- Semantic Guard 语义修正。
- Execution Guard 运行保护。
- State Guard 状态一致性保护。
- 人工审批后进入可执行资产库。
"""
        write_md(out_dir / "security_privacy_model.md", security); files.append("security_privacy_model.md")

        onepager = """# AI Test Asset Center 产品一页纸

## 一句话
把需求、接口文档、代码变更和失败证据自动转化为可治理、可执行、可审计的测试资产。

## 目标
降低测试资产生成维护成本，提升回归效率，减少失败排查和缺陷整理时间。

## 核心能力
- 需求生成测试资产
- OpenAPI 生成接口测试
- Git Diff 精准回归
- 失败证据包 AI 归因
- 企业治理、质量门禁、ROI 指标
- 产品化工作区和角色权限

## 试点方式
4 周 PoC：选择一个模块，接入需求/OpenAPI/Git Diff，生成测试资产，接一次 CI，输出 ROI 和推广建议。

## 成功指标
- 用例设计耗时降低 30%+
- 回归范围减少 30%+
- 失败归因时间降低 40%+
- AI 生成资产采纳率 60%+
"""
        write_md(out_dir / "product_one_pager.md", onepager); files.append("product_one_pager.md")

        return ProductPackageSummary(str(out_dir), self.product_name, self.version, len(feature_matrix), len(personas), len(files), files)
