# QualiBug 全谱系 Bug 发现引擎增强计划

## Summary

目标不是“再补几个分析器”，而是把现有 QualiBug 从“以业务语义和 API/数据类缺陷为强项的发现引擎”，升级为“面向企业 Web/API 系统的全谱系缺陷发现平台”。

本次规划按你的确认边界执行：

- 优先战场：企业 Web/API 系统
- 优先目标：覆盖率优先
- 设计原则：保守接入、零风险优先、向后兼容、已有能力尽量复用、不破坏 Phase92A / real_project 现有主链

核心结论：

1. 仓库里已经有大量前端、契约、运行时、体验、性能、安全、证据、治理模块，但它们没有像 `ai_test_asset_center/real_project_defect_discovery.py` 一样被统一编排成“一条总发现链”。
2. 现有强项在业务语义、数据一致性、生命周期、跨源推理、证据门控、预算治理。
3. 现有短板在 UI / UIUX / 性能 / 稳定性 / 兼容性 / 前端执行 / 浏览器行为 / 可用性 等能力没有被纳入统一 Probe 生成、执行、证据归一、去重、评分、报告与反馈闭环。
4. 现有 `enhanced_discovery_engine.py` 和部分 analyzer 仍带有“简化实现 / 模拟 / demo”痕迹，不能作为“全类型发现引擎”的生产主入口。

## Current State Analysis

### 已有核心主链

- `ai_test_asset_center/discovery_engine.py`
  - 已具备 Stage0-4 发现主链：上下文编译、Reader、Reasoner、Executor、Verifier。
  - 这是当前最成熟的通用发现管线。
- `ai_test_asset_center/stage_reason_all_v2.py`
  - 已形成多引擎推理主干，且保留超时 / token 安全下限。
- `ai_test_asset_center/real_project_defect_discovery.py`
  - 已是“真实项目发现”的统一入口。
  - 已接入大量业务/数据/一致性/生命周期/多源推理能力。
  - 已提供 probe、issue、summary、metrics、evidence 输出契约。
- `ai_test_asset_center/bug_validation_queue.py`
  - 已把候选缺陷区分为 confirmed / needs_validation / blocked 等状态。
- `ai_test_asset_center/bug_engine_gap_analyzer.py`
  - 已有“证据不足 / 为什么还不能报 bug”的缺口分析能力。

### 已有但未统一纳入主链的能力

- `ai_test_asset_center/phase104_api_contract_acceptance.py`
  - 已能做 API 合同验收、运行时 envelope 校验、脱敏检查。
  - 但更像“验收门禁”，不是总发现链中的标准 probe source。
- `ai_test_asset_center/phase106_frontend_execution_runtime.py`
  - 已覆盖前端执行事件流、风险信号、证据快照、跳转证据链。
  - 但它是前端/runtime 交付物生成器，不是主发现引擎的一等发现源。
- `ai_test_asset_center/performance_monitor.py`
  - 只提供计时和日志，不提供性能 oracle、SLO 判定、瓶颈归因、稳定性 verdict。
- `ai_test_asset_center/phase104_frontend_runtime_smoke.py`
- `ai_test_asset_center/phase105_frontend_interaction_acceptance.py`
- `ai_test_asset_center/phase105_frontend_preview_acceptance.py`
- `ai_test_asset_center/phase106_frontend_*`
  - 已存在前端体验、运行时、交付、验收链路，但主要服务“前端产物可交付/可演示/可验收”，还不是“自动缺陷发现编排”的组成部分。

### 当前直接短板

#### 1. 缺少统一缺陷覆盖地图

虽然 `docs/发现引擎能力增强方案.md` 已列出 C01-C35 的能力版图，但代码主链中没有一个统一的“缺陷家族注册表 + 覆盖状态 + 执行方式 + 证据要求”中心。

结果：

- 业务/接口类能力强
- UI/UIUX/兼容/性能/稳定性类能力分散
- 很难知道“哪些 bug 家族已发现、哪些只是有模块、哪些可执行、哪些只可候选”

#### 2. `real_project_defect_discovery.py` 仍偏业务/接口中心

该入口目前已接入：

- business outcome / reconciliation / invariant / lifecycle
- multi-source reasoning / consistency / temporal / causality
- business assurance / enterprise knowledge / history-informed probes

但没有同等级接入：

- 浏览器/页面行为探针
- UI 渲染 / DOM / 交互 / 文案 / 导航 / 可访问性探针
- 性能基线 / 稳定性退化 / 超时 / 重试风暴 / 资源泄漏探针
- 浏览器兼容 / 响应式 / 时区 / 本地化 / 环境差异探针

#### 3. 部分增强分析器仍是 demo / simplified 级别

从已探索文件可见：

- `ai_test_asset_center/enhanced_discovery_engine.py`
  - `_parse_api_spec()` 直接返回固定 `/api/orders`
  - 不适合作为生产统一入口
- `ai_test_asset_center/analyzers_adapter.py`
  - 虽已把 8 个 analyzer 接到 Phase92A 格式，但 API spec 解析仍是简化版 fallback
- `ai_test_asset_center/analyzers/business_rules.py`
- `ai_test_asset_center/analyzers/state_machine.py`
- `ai_test_asset_center/analyzers/conservation.py`
  - 存在“简化实现”“模拟检查”等明显信号

#### 4. 性能与稳定性只有“监控”，没有“发现判定”

`ai_test_asset_center/performance_monitor.py` 当前只能记录耗时：

- 没有 SLO / percentile / timeout budget / retry storm / saturation / contention / leak / degradation oracle
- 没有把性能异常转成标准 issue
- 没有进入统一 evidence / score / report

#### 5. 测试契约没有覆盖“全谱系发现能力”

当前代表性测试：

- `test_analyzers_integration.py`
- `tests/test_real_project_discovery_contract.py`

问题：

- 主要验证当前业务/预算/配置/集成契约
- 没有验证 UI / UIUX / performance / stability / compatibility 的发现源是否进入统一输出
- 没有“覆盖地图回归测试”

## Assumptions & Decisions

- 本轮不追求“数学意义上找出所有 bug”，而是建设“可持续扩展、可量化覆盖、可闭环学习”的全谱系发现平台。
- 目标边界限定为企业 Web/API 系统，不在本轮直接纳入桌面端、嵌入式、IoT、原生移动端深层实现。
- 优先补齐缺陷家族覆盖和统一编排，再逐步压低误报率。
- 严格复用现有：
  - Phase92A 证据管道
  - `real_project_defect_discovery.py` 输出契约
  - 预算反馈、部署治理、gap analyzer、validation queue
- 不替换现有业务推理主链，而是在其外层新增“全谱系发现层”。

## Proposed Changes

### 一、建立统一缺陷家族注册中心

#### 新增文件

- `ai_test_asset_center/defect_family_registry.py`
- `ai_test_asset_center/defect_signal_schema.py`

#### What

建立统一的缺陷家族注册表，覆盖至少以下家族：

- 场景流转 bug
- API / 契约 / schema bug
- 安全 / 权限 / 数据隔离 bug
- 数据一致性 / 生命周期 / 异步 / 缓存 bug
- 性能 bug
- 稳定性 bug
- 兼容性 bug
- UI bug
- UIUX bug
- 可访问性 / 国际化 / 时区 / 环境差异 bug

#### Why

把“模块存在”升级为“覆盖可见、状态可量化、执行方式可声明、证据要求可复用”。

#### How

每个 defect family 定义：

- `family_id`
- `display_name`
- `bug_examples`
- `discovery_mode`
- `probe_sources`
- `required_evidence`
- `allowed_execution_modes`
- `dedupe_keys`
- `confidence_policy`
- `reporting_bucket`

并提供统一 API 给：

- `real_project_defect_discovery.py`
- `bug_engine_gap_analyzer.py`
- `bug_validation_queue.py`
- 后续 coverage dashboard / regression suite builder

### 二、把现有“分散能力”接入统一总发现链

#### 重点修改

- `ai_test_asset_center/real_project_defect_discovery.py`

#### 新增适配层

- `ai_test_asset_center/api_contract_discovery_adapter.py`
- `ai_test_asset_center/frontend_runtime_discovery_adapter.py`
- `ai_test_asset_center/frontend_ux_discovery_adapter.py`
- `ai_test_asset_center/performance_stability_discovery_adapter.py`
- `ai_test_asset_center/compatibility_discovery_adapter.py`

#### What

把已经存在的 API 合同、前端运行时、前端体验、性能监控等能力封装成标准 probe source / candidate issue source。

#### Why

让这些能力不再只是“独立验收模块”或“演示产物”，而是进入统一：

- probe 生成
- 执行
- 证据归一
- issue 输出
- 去重聚合
- summary/metrics

#### How

1. `api_contract_discovery_adapter.py`
   - 复用 `phase104_api_contract_acceptance.py`
   - 输出标准 contract defect candidates：
   - schema drift
   - response envelope mismatch
   - method/path/export mismatch
   - backward compatibility break

2. `frontend_runtime_discovery_adapter.py`
   - 复用 `phase106_frontend_execution_runtime.py`
   - 将前端执行事件、风险信号、证据快照映射为标准 issue

3. `frontend_ux_discovery_adapter.py`
   - 复用 `phase104_frontend_runtime_smoke.py`
   - 复用 `phase105_frontend_interaction_acceptance.py`
   - 复用 `phase105_frontend_preview_acceptance.py`
   - 统一产出：
   - 页面不可达
   - 导航断链
   - 核心交互失败
   - 空白页 / 崩溃页
   - 文案错误 / 误导性状态
   - 关键 UX 反模式

4. `performance_stability_discovery_adapter.py`
   - 复用 `performance_monitor.py`
   - 结合已有 runtime / loop 输出
   - 统一产出：
   - 慢请求
   - 超时
   - retry storm
   - CPU/IO 饱和代理信号
   - 错误率异常
   - 间歇性失败
   - 状态抖动 / flaky 行为

5. `compatibility_discovery_adapter.py`
   - 先覆盖企业 Web/API 场景中的：
   - 浏览器差异
   - 响应式断裂
   - 时区 / 地区化差异
   - 配置 / 环境差异
   - schema 版本兼容问题

### 三、补一层“统一执行编排 + 证据门控”

#### 修改文件

- `ai_test_asset_center/runtime_probe_capability_matrix.py`
- `ai_test_asset_center/grounded_probe_executor.py`
- `ai_test_asset_center/bug_validation_queue.py`
- `ai_test_asset_center/bug_engine_gap_analyzer.py`

#### What

让新的 defect family 真正可执行、可门控、可解释“为什么这次没法报 bug”。

#### Why

如果只新增 family 而不补执行与门控，系统只会多产出候选，不会形成真正的发现能力。

#### How

1. 在 `runtime_probe_capability_matrix.py` 中声明每个 family 所需执行能力：
   - static_only
   - contract_only
   - api_probe
   - runtime_signal
   - frontend_runtime
   - compatibility_matrix
   - performance_oracle

2. 在 `grounded_probe_executor.py` 中增加 family-aware 执行路由
   - 不改变原业务主链
   - 只增加新的执行分发层

3. 在 `bug_validation_queue.py` 中增加 family-specific verdict 规则
   - UI 问题需要页面状态 / DOM / 交互证据
   - 性能问题需要阈值 / 对照 / 重复观测
   - 兼容性问题需要多环境对比证据

4. 在 `bug_engine_gap_analyzer.py` 中新增 coverage gap 维度
   - 哪类 family 没执行
   - 哪类执行了但证据不足
   - 哪类 family 缺探针
   - 哪类 family 缺 oracle

### 四、把“性能 / 稳定性 / 兼容性 / UIUX”从感知能力升级为标准 Oracle

#### 新增文件

- `ai_test_asset_center/performance_oracles.py`
- `ai_test_asset_center/stability_oracles.py`
- `ai_test_asset_center/compatibility_oracles.py`
- `ai_test_asset_center/uiux_oracles.py`

#### What

定义每类 bug 如何被判定，而不是只收集日志或截图。

#### Why

全谱系发现引擎的关键不是“能观察”，而是“能判定”。

#### How

1. 性能 Oracle
   - p50 / p95 / p99
   - baseline drift
   - request fanout amplification
   - timeout budget
   - cold/warm cache delta

2. 稳定性 Oracle
   - 同探针重复执行差异
   - flaky score
   - error burst
   - state recovery failure
   - retry without convergence

3. 兼容性 Oracle
   - browser/env diff
   - timezone/locale diff
   - schema version diff
   - responsive layout breakpoints

4. UIUX Oracle
   - 主任务不可完成
   - 关键 CTA 不可见 / 不可点击
   - 反馈缺失
   - 状态误导
   - 可访问性关键阻断

### 五、升级 analyzer 层，去掉“简化实现”短板

#### 修改文件

- `ai_test_asset_center/enhanced_discovery_engine.py`
- `ai_test_asset_center/analyzers_adapter.py`
- `ai_test_asset_center/analyzers/business_rules.py`
- `ai_test_asset_center/analyzers/state_machine.py`
- `ai_test_asset_center/analyzers/conservation.py`
- `ai_test_asset_center/analyzers/concurrency.py`
- `ai_test_asset_center/analyzers/async_task.py`
- `ai_test_asset_center/analyzers/cache_consistency.py`
- `ai_test_asset_center/analyzers/authorization.py`

#### What

把本地 analyzer 从“启发式 demo 能力”升级为“真实 spec/context 驱动的生产可复用能力”。

#### Why

这些 analyzer 很适合作为高覆盖率补充，但前提是不能再依赖固定 `/api/orders` 或过强的假设。

#### How

1. 移除固定 spec fallback 的主路径依赖
2. 仅把 fallback 保留为测试或空输入保护
3. 让 analyzer 消费真实：
   - OpenAPI
   - project context
   - entity catalog
   - route map
   - runtime evidence hints
4. 输出统一 defect family 标签，而不是只输出 analyzer category

### 六、建立“全谱系覆盖率仪表板”

#### 新增文件

- `ai_test_asset_center/bug_family_coverage_report.py`

#### 修改文件

- `ai_test_asset_center/real_project_defect_discovery.py`
- `ai_test_asset_center/bug_engine_reporter.py`

#### What

输出“系统已经覆盖哪些 bug 家族、哪些只具备静态检测、哪些可动态执行、哪些仍缺 oracle”。

#### Why

你的目标不是单次找到更多 bug，而是长期逼近“所有企业软件系统 bug 都能测出来”的能力边界。

#### How

报告至少包含：

- family coverage matrix
- executable family count
- candidate-only family count
- validated family count
- missing oracle families
- missing probe families
- false positive risk families

### 七、测试体系升级为“覆盖地图 + 主链契约 + 家族回归”

#### 修改文件

- `test_analyzers_integration.py`
- `tests/test_real_project_discovery_contract.py`

#### 新增测试

- `tests/test_defect_family_registry.py`
- `tests/test_real_project_discovery_full_spectrum_contract.py`
- `tests/test_api_contract_discovery_adapter.py`
- `tests/test_frontend_runtime_discovery_adapter.py`
- `tests/test_performance_stability_discovery_adapter.py`
- `tests/test_compatibility_discovery_adapter.py`
- `tests/test_bug_family_coverage_report.py`

#### What

从“验证当前链条不坏”升级为“验证新 family 真正进入主链”。

#### Why

如果没有这些测试，未来很容易退化回“模块存在但没接入发现总线”的状态。

#### How

测试必须验证：

- family registry 完整可读
- 新 family 能生成 probe 或 candidate issue
- `run_real_project_discovery()` summary/metrics 中出现对应 family 统计
- 新适配层 issue 能进入统一 issue 列表
- gap analyzer 能解释未命中的原因
- 不破坏现有 business-heavy 输出契约

## Implementation Order

### Phase 1：打地基

- 新建 `defect_family_registry.py`
- 新建 `defect_signal_schema.py`
- 为 `real_project_defect_discovery.py` 增加 family registry 装配点
- 为 `bug_engine_gap_analyzer.py` 增加 family coverage 输出

### Phase 2：先接高复用、低风险能力

- 接入 `api_contract_discovery_adapter.py`
- 接入 `frontend_runtime_discovery_adapter.py`
- 接入 `performance_stability_discovery_adapter.py`

理由：

- 这些能力仓库中已有产物最完整
- 复用价值最高
- 对现有主链侵入最低

### Phase 3：补兼容性和 UIUX

- 新增 `compatibility_discovery_adapter.py`
- 新增 `uiux_oracles.py`
- 接入前端 smoke / interaction / preview acceptance 结果

### Phase 4：升级 analyzer 生产化

- 去掉固定 spec/demo 主路径
- 对 analyzer output 统一打 defect family 标签
- 让 analyzer 成为高覆盖率补充源

### Phase 5：闭环与可视化

- 新建 `bug_family_coverage_report.py`
- 扩展 reporter / metrics / tests
- 把覆盖空洞显式输出给后续自进化与预算系统

## Verification Steps

### 静态验证

- 确认 `real_project_defect_discovery.py` 仍输出兼容的 `status / issues / probes / summary / metrics`
- 确认 defect family registry 覆盖目标家族且每个家族都有执行/证据策略
- 确认 adapter 输出遵循统一 issue schema

### 契约验证

- 扩展 `tests/test_real_project_discovery_contract.py`
- 新增 full-spectrum contract tests，验证新 family 已进入 summary/metrics

### 能力验证

- 用一组最小样本项目分别触发：
  - API contract bug
  - UI/导航 bug
  - 性能退化 bug
  - 稳定性 / flaky bug
  - 浏览器/时区兼容 bug
- 验证它们都能输出标准 issue 或 candidate，并被 gap analyzer 正确分类

### 回归验证

- 确认业务规则、状态机、生命周期、一致性、守恒、多源推理等现有强项不退化
- 确认预算、部署漂移、审批、证据门控契约不受破坏

## Success Criteria

- `run_real_project_discovery()` 不再只偏业务/接口类发现，而是能统一汇总：
  - 场景 bug
  - 接口 bug
  - 性能 bug
  - UI bug
  - 稳定性 bug
  - 兼容性 bug
  - UIUX bug
- 系统能明确回答：
  - 哪些 bug 家族已覆盖
  - 哪些已可执行验证
  - 哪些仍缺 probe / oracle / evidence
- 全谱系能力通过测试契约固化，而不是停留在模块散落存在

## Executor Notes

- 实施时优先复用现有 Phase104/105/106 与 runtime 模块，不重写已有能力。
- 严守 `override > env > project_config > policy > default` 配置优先级。
- 严守证据不跨边界上送原文的安全约束。
- 不得降低 `discovery_engine.py` 的 timeout / max_tokens 安全下限。
- 每次编辑 Python 文件后立即做 AST syntax check。
