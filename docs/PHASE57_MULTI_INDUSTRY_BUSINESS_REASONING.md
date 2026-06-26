# Phase57 · 多行业业务理解验证

## 目标

Phase57 让 QualiBug 在不要求客户手工选择或维护行业知识包的前提下，从 PRD、MRD、OpenAPI、接口描述与页面语义中推断业务场景，并将理解结果转为可审计的 Oracle 与风险验证计划。

它不承诺“覆盖所有业务 Bug”或“零缺陷”。它输出的是基于当前输入证据的行业理解、业务风险与覆盖缺口，低置信度时主动回退到通用业务模式。

## 输入

- `prd.md` / `requirements.md` / `business_rules.md`
- `mrd.md` / 页面标题、菜单、字段标签或接口描述
- `openapi.json` / 标准化 OpenAPI
- 已有企业接入配置中的非敏感领域提示（仅作为弱信号，不替代文档证据）

## 支持的评测行业

- CRM / 销售客户
- ERP / 供应链与经营资源
- 金融 / 资金账户
- 医疗 / 患者诊疗
- 教育 / 学生课程
- SaaS 多租户
- 电商 / 订单履约

评测样例在 `examples/phase57_multi_industry_evaluation.json`。它们仅用于回归与演示，不会在客户运行时作为强制规则包生效。

## 核心流程

```text
PRD / MRD / OpenAPI / 接口描述
        ↓
文档与契约证据抽取
        ↓
多标签行业推断（带置信度与证据）
        ↓
对象、角色、模块、状态机、数据依赖、权限边界、业务规则
        ↓
行业 Oracle、风险域与高价值 Probe
        ↓
风险雷达、真实项目报告、质量保障覆盖模型
```

## 输出

每个项目会生成：

- `platform_workspace/<project>/defect_discovery/multi_industry_business_profile.json`
- `platform_workspace/<project>/defect_discovery/multi_industry_business_probes.json`
- `platform_outputs/<project>/multi_industry_business_reasoning/multi_industry_business_report.html`

报告包括：

- 被识别的主行业与次行业、置信度、文档/接口证据
- 业务模块、对象和角色
- 典型状态机及终态保护
- 数据依赖与金额/数量/额度/容量等守恒规则
- 权限边界、租户边界、归属边界、敏感数据边界
- 行业 Oracle 与高价值风险域
- 风险域到 Probe、风险雷达和发布治理的映射

## 安全与治理

- 行业识别低置信度时，输出 `unknown_general_business`，不会强行贴行业标签。
- 读取类验证可进入 `safe_live`；状态跳转、资金、库存、审批、处方、退款等写路径均标记为 `sandbox_required`。
- 行业风险域不是已确认缺陷；它们是可执行 Oracle 或人工审查的优先验证计划。
- 识别结果会进入 Phase56 的质量保障覆盖模型，未被 Oracle 覆盖的关键失败模式会成为显式覆盖缺口。

## 验证入口

```bash
python -m ai_test_asset_center.multi_industry_business_reasoning --demo
python -m unittest tests.test_multi_industry_business_reasoning -v
```

`--demo` 会对 CRM、ERP、金融、医疗、教育、SaaS 多租户、电商七类输入运行相同的推断器，并验证各自生成不同的业务对象和高价值风险域。
