# QualiBug 企业无源码输入适配层加固 Spec

## Why
当前仓库已经具备 `enterprise_knowledge_center` 这条企业资料统一接入链路，但对真实企业无源码资料包的结构化成功率不均衡。实测 `QualiBug_ECommerce_Benchmark.zip` 时，`HISTORICAL_BUG_RECORDS.csv` 与 `schema.sql` 能较好沉淀为知识资产，而 `API_DOCS.md`、`DATABASE_DESIGN.md`、`field_dictionary.md`、`UIUX_SPEC.md` 与 `uiux/*.svg` 仍大量停留在浅层 ingest，导致 `interface_count` 与 `generated_probe_count` 偏低，影响后续 probe 生成与发现质量。

本次变更目标是在不改动 Bug Engine 核心推理逻辑的前提下，加固“企业资料 -> 标准化知识资产”的输入适配层，提升无源码企业资料包的结构化稳定性、分类准确性和可观测性，并通过真实 benchmark 回归验证改进结果。

## What Changes
- 加固 `enterprise_knowledge_center` 的 source type 分类逻辑，避免 `PRD.md` 等普通需求文档被误判为 `openapi`。
- 为 markdown 形态的接口文档新增结构化提取能力，从 `API_DOCS.md` 这类资料中提取 `method/path/auth/request/response/status` 等接口元信息。
- 为 `DATABASE_DESIGN.md` 与 `field_dictionary.md` 增加结构化提取能力，补充业务对象、字段语义、字段约束和实体关系映射。
- 为 `UIUX_SPEC.md` 与 `uiux/*.svg` 增加设计资料适配逻辑，输出可用于 design oracle / manifest 的页面、状态与关键交互线索。
- 修正知识中心和上传链路的格式支持口径，保证系统对“不支持/浅支持/强支持”的状态表达可信，避免前端宣称超出后端真实能力。
- 增加输入适配层的回归验证，至少覆盖 `API markdown`、`PRD 分类`、`field dictionary`、`UIUX spec/svg` 和 benchmark 实测结果。

## Impact
- Affected specs: 企业无源码输入接入、企业知识中心、design oracle 资料适配、历史缺陷与数据模型映射、真实 benchmark 回归验证
- Affected code: `ai_test_asset_center/enterprise_knowledge_center.py`、`ai_test_asset_center/private_pilot_service.py`、`ai_test_asset_center/ui_design_oracle_manifest.py`、`ai_test_asset_center/browser_ui_replay_discovery_adapter.py`、`tests/*enterprise_knowledge*`、`tests/*real_project_discovery*`

## ADDED Requirements
### Requirement: Markdown 接口文档必须能沉淀为结构化接口资产
系统 SHALL 支持从 markdown 形态的接口文档中提取结构化接口元信息，并将其写入企业知识资产中的 `interfaces`，使无源码企业项目在未提供标准 OpenAPI 的情况下仍可生成接口级 Probe 输入。

#### Scenario: 解析 markdown API 文档
- **WHEN** 用户向知识中心 ingest 一份包含 `GET /path`、`POST /path`、状态码、鉴权或请求响应说明的 `API_DOCS.md`
- **THEN** 系统生成至少包含 `method`、`path`、`source_id` 的接口记录
- **AND** 若文档中存在鉴权、状态码、请求参数或响应摘要，系统尽可能补齐对应字段
- **AND** 解析结果进入知识资产 `interfaces`

### Requirement: 普通需求文档分类必须可信
系统 SHALL 正确区分 `prd`、`openapi`、`database_schema`、`historical_bug`、`ticket`、`collaboration_document` 等 source type，不得因关键词误判导致普通需求文档被当成接口规范处理。

#### Scenario: 分类 PRD 文档
- **WHEN** 系统 ingest `PRD.md`
- **THEN** 若该文档不包含真实 OpenAPI/Swagger 结构
- **AND** 文档主要包含业务目标、流程、规则或角色描述
- **THEN** 它被分类为 `prd` 或 `collaboration_document`
- **AND** 不得被误判为 `openapi`

### Requirement: 数据说明资料必须补齐字段与关系语义
系统 SHALL 支持从 `DATABASE_DESIGN.md` 与 `field_dictionary.md` 中提取字段语义、实体说明、约束线索与关系映射，用于增强 `data_tables`、`business_objects` 和 rule/oracle 生成的质量。

#### Scenario: 解析字段字典与数据库设计
- **WHEN** 用户 ingest `DATABASE_DESIGN.md` 与 `field_dictionary.md`
- **THEN** 系统补充业务对象、字段说明、约束线索或实体关系
- **AND** 这些信息进入知识资产，而不是仅停留为零结构化输出的普通文档

### Requirement: UIUX 资料必须转成可消费的设计线索
系统 SHALL 支持从 `UIUX_SPEC.md` 与 `uiux/*.svg` 中提取页面名、状态名、关键交互词、流程节点或线框标题，并沉淀为设计 Oracle 可消费的结构化线索。

#### Scenario: 解析 UIUX 文档与 SVG
- **WHEN** 用户 ingest UIUX 规格文档与 SVG 设计稿
- **THEN** 系统生成页面/状态/交互线索摘要
- **AND** 这些线索能够进入 design oracle manifest 或等价的中间结构
- **AND** 不得仅表现为“文件已入库但结构化结果全部为 0”

### Requirement: 格式支持状态必须真实表达
系统 SHALL 对企业输入格式的支持状态给出真实分层表达，区分“强支持”“浅支持”“仅 ingest”“不支持”，不得把无有效结构化能力的格式宣传为已完整支持。

#### Scenario: 页面或接口展示格式能力
- **WHEN** 用户查看知识中心、上传入口或格式说明
- **THEN** 页面明确区分各格式支持级别
- **AND** 对需要文本层、OCR、二进制专用解析器或压缩包递归解包的场景给出真实限制

### Requirement: Benchmark 回归必须验证结构化增益
系统 SHALL 使用真实 benchmark 无源码资料包验证输入适配层改进效果，并输出关键结构化指标用于对比。

#### Scenario: 运行电商 benchmark 回归
- **WHEN** 系统 ingest `QualiBug_ECommerce_Benchmark` 的无源码资料
- **THEN** 回归结果至少输出 `interface_count`、`data_table_count`、`rule_count`、`oracle_count`、`generated_probe_count`
- **AND** 与改造前结果相比，关键短板项应有明确改善或明确说明剩余边界

## MODIFIED Requirements
### Requirement: 企业知识中心的目标从“接收资料”升级为“生成可消费结构”
系统现有企业知识中心 SHALL 继续承担统一接入与版本化治理职责，但其成功标准应从“文件已 ingest”提升为“能生成下游 probe/oracle/资产可消费的结构化结果”，尤其针对 markdown API 文档、字段字典、设计资料和无源码 benchmark 输入。

### Requirement: 上传链路的格式说明必须与后端真实能力对齐
系统现有上传与知识中心展示能力 SHALL 以真实可解析能力为准，不得继续用笼统的“任意企业文件均可自动理解”口径覆盖二进制、OCR、流程图和压缩包等尚未稳定支持的格式。

## REMOVED Requirements
### Requirement: 以 ingest 成功代表格式适配成功
**Reason**: 文件进入 source registry 并不等于系统已提取出 interfaces、tables、rules、oracles 等高价值结构。若继续把 ingest 成功视为适配成功，会掩盖大量“入库成功但结构化失败”的真实短板。
**Migration**: 改用“ingest 成功 + 结构化结果达标 + benchmark 回归指标改善”作为输入适配层的验收标准。
