# QualiBug 运行结果前端表达 SPEC

状态：已实现  
范围：仅前端运行结果表达与导航；不修改扫描、Bug 发现、Finding 判定、覆盖计算或发布门禁后端逻辑。

## 1. 目标

运行中心在真实扫描回执返回后，客户首屏必须先回答四个问题：

1. 本次执行是否真正形成终态回执；
2. 运行回执返回了多少发现；
3. 本轮范围是否完整或存在阻断；
4. 客户下一步应该去哪里。

技术字段继续保留，但不能与客户结论争夺首屏主视觉。

## 2. 数据来源

客户结果摘要只消费既有 `RUN_LIFECYCLE_EVENT`：

- `submitted`：清空上一轮客户摘要；
- `completed`：根据真实 `executionStatus`、`campaignStatus`、`testDataStatus`、`totalFindings` 形成前端展示；
- `failed`：明确显示本次请求没有形成可用于发布判断的完整回执。

前端不得自行按时间或百分比推测执行成功。

## 3. 展示优先级

### 请求失败 / 明确阻断

- 红色状态；
- 不允许出现安全或发布通过暗示；
- 主动作进入接入与运行条件检查。

### 覆盖不完整

包括 `plan_only`、`partial`、`partial_coverage`、`coverage_deferred`、`not_executed`、Campaign coverage deferred，或测试数据未 ready。

- 黄色状态；
- 0 条发现必须明确说明不等于系统没有问题；
- 主动作进入 Coverage；
- 若已有发现，可辅助进入问题清单。

### 完整回执且存在发现

- 黄色状态；
- 标题使用“运行回执包含 N 条发现”，不直接把 raw run count 命名为客户已确认缺陷；
- 明确说明正式客户交付口径以 Dashboard / Findings 为准；
- 主动作先进入结果总览。

### 完整回执且 0 发现

- 可使用成功色表达“运行完成”；
- 文案仍必须声明：运行页 0 条发现不能独立推出系统安全；
- 主动作进入结果总览，再结合 Coverage / Evidence / Release Gate 判断。

## 4. 客户与技术信息分层

终态时展示顺序：

1. `RunCustomerResultSummary` 客户摘要；
2. `RunLifecycleBanner` 真实阶段与技术运行信息；
3. 运行中心原有详细回执。

运行中 `RunCustomerResultSummary` 不展示，因此仍由 `RunLifecycleBanner` 作为真实阶段主视图。

客户摘要不展示 lifecycle `coverage` 数值，因为当前 lifecycle 数值合同会把缺失值归一为 0；避免把 unknown 误表示为真实 0。

扫描 ID、HAR 请求、Fixture、Runtime Contract、阶段明细等继续保留在既有技术回执中。

## 5. 防止陈旧状态

- 新扫描 `submitted` 时立即清空上一轮摘要；
- 切换客户项目时立即清空；
- 离开 `/campaigns` 时立即清空；
- 不使用 sessionStorage/localStorage 保存运行结果业务状态；
- 不让前端缓存覆盖后端最终事实。

## 6. 回归门禁

`test:run-customer-result` 进入 `npm run ci:gate`，必须锁定：

- 复用真实 lifecycle event；
- 忽略其他 project 的运行事件；
- submitted / 切换 project / 离开页面会清理 stale 状态；
- incomplete + 0 finding 不显示安全；
- raw run count 不冒充正式 Finding 交付口径；
- incomplete 主要 CTA 指向 Coverage；
- 终态结果保持 Dashboard result-first；
- 客户摘要在终态时排在技术 Banner 前；
- 客户摘要不直接展示可能把 unknown 编成 0 的 lifecycle coverage 数值。

## 7. 非目标

本 SPEC 不定义或修改：

- Bug 如何发现；
- `total_findings` 如何生成；
- Finding 是否 customer-ready；
- Coverage 如何计算；
- Campaign / Test Data 状态如何产生；
- Release Gate / Regression Gate 后端判断；
- HAR、Fixture、Runtime Contract 如何生成。

这些全部继续由后端既有合同负责。