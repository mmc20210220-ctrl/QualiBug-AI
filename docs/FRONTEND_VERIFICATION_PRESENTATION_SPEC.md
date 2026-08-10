# QualiBug 前端验证状态统一呈现 SPEC

状态：已实现  
范围：仅前端状态解释、视觉表达、关注排序、验证历史与客户下一步提示；不改变 Finding、Replay、Regression 或 Release Gate 的后端判定。

## 1. 目标

QualiBug 客户主链中的同一 Finding，在以下页面必须得到完全一致的验证状态表达：

`Dashboard -> Findings -> Evidence -> Release Gate`

禁止各页面分别解释 `regression.latest_status`、分别选择颜色或分别编写“待回归 / 已修复 / 失败”等文案。

唯一状态权威：

`deriveFindingVerification(finding)`

唯一视觉组件：

`FindingVerificationStatus`

## 2. 状态集合

前端只使用以下五个验证状态：

| state | 客户标签 | tone | 含义 |
| --- | --- | --- | --- |
| `still_failing` | 重新验证仍失败 | danger | 最新真实验证仍复现问题 |
| `inconclusive` | 本轮无法确认修复 | warning | 本轮没有形成可信 Pass / Fail |
| `pending` | 等待修复后重新验证 | warning | 已有真实验证义务，但尚无终态 |
| `not_enrolled` | 暂无可执行重新验证 | neutral | 当前没有真实回归义务 |
| `verified_fixed` | 修复验证通过 | success | 后端真实验证终态明确通过 |

blocked / error / unknown / skipped / not_ready 等不得显示为通过。

## 3. 统一语义色

所有页面通过 `frontend/src/styles/finding-verification.css` 使用同一组设计 Token：

- `still_failing`：`--danger / --danger-muted`；
- `inconclusive`：`--warning / --warning-muted`；
- `pending`：`--warning / --warning-muted`；
- `verified_fixed`：`--success / --success-muted`；
- `not_enrolled`：中性 `--surface-2 / --muted / --line`。

页面不得自行重新指定 Finding 验证状态颜色。

## 4. 客户关注优先级

共享解释器同时提供 `priority`，用于“先看什么”，不用于篡改严重级别或发布结论：

1. `still_failing = 50`；
2. `inconclusive = 40`；
3. `pending = 30`；
4. `not_enrolled = 20`；
5. `verified_fixed = 10`。

### Findings

默认列表优先展示未闭环验证风险；验证状态相同的 Finding 保留正常列表顺序。

### Dashboard Top 3

先按验证关注优先级，再按 Finding severity，最后按已有证据质量排序。

因此已经修复并验证通过的问题不会继续长期占据“重点关注”，但 severity 本身不会被修改。

## 5. 统一下一步语义

共享解释器提供 `nextAction / nextActionLabel`：

- `still_failing` -> `查看失败证据`；
- `inconclusive` -> `恢复验证条件后重试`；
- `pending` -> `客户修复后重新验证`；
- `verified_fixed` -> `查看发布门禁`；
- `not_enrolled` -> `查看验证依据`。

这些是**单 Finding 的验证下一步提示**，不是企业研发任务。

禁止转化为：

- 分配负责人；
- 开始修复；
- 设置修复版本；
- 进入开发中；
- 创建 Jira / GitLab / GitHub Issue；
- 人工把状态改成已修复。

## 6. 页面使用规则

### Dashboard

`DashboardFocusFindingCard` 必须展示 `FindingVerificationStatus`。

重点问题排序必须消费 `deriveFindingVerification(finding).priority`。

### Findings

`FindingCard` 不允许拥有第二套 `regressionStatusLabel()` 或 `regressionTone()`。

卡片摘要与展开验证面板都必须使用共享解释器 / 共享状态组件。

### Evidence

Evidence 列表和当前 Evidence 详情必须展示同一 `FindingVerificationStatus`。

Evidence 中的 Replay 结果仍必须遵守：一次“未复现”不等于 verified fixed。

### Release Gate

单 Finding 上下文卡展示共享状态和共享 `nextActionLabel`。

但是：

**项目级发布结论仍只由 `deriveReleasePresentation()`、真实 Release Gate、Regression Gate、Pipeline 和 Campaign 状态决定。**

单 Finding 的状态、优先级和 `nextActionLabel` 都不得覆盖项目级发布结论。

## 7. 真实验证历史时间线

前端使用 `buildFindingVerificationTimeline(finding)` 将后端已经返回的 `regression.history` 转换为客户可读时间线。

时间线必须从 **原始 Finding 已确认** 开始，然后按 `generated_at` 正序展示每一次真实修复后验证：

`原始 Finding -> 第 1 次验证 -> 第 2 次验证 -> ... -> 最新验证`

每一轮最多展示后端真实存在的：

- `generated_at`；
- `suite_mode / suite_mode_label`；
- `status / status_label`；
- `gate_status`；
- `reason / ci_message`；
- `regression_probe_id`；
- `method + path`。

后端没有 history 时，只显示原始 Finding 基线和“尚无真实修复后验证历史”，不得补造轮次。

### 7.1 结论变化规则

验证历史把问题结论抽象为：

- `open`：问题仍成立；
- `fixed`：真实验证明确通过；
- `unknown`：本轮无法确认。

只有真实终态在 `open <-> fixed` 之间切换时，当前验证轮次才显示 **“结论变化”**：

- `open -> fixed`：这一次真实验证使问题从成立变为验证通过；
- `fixed -> open`：这一次真实验证重新复现问题 / reopened。

`blocked / unknown / skipped / not_ready / unverifiable` 等 `unknown` 结果：

- 只能显示“本轮未形成可确认结论”；
- 不得覆盖上一轮已知 open / fixed 结论；
- 不得被标记成“已修复”或“重新打开”。

### 7.2 页面呈现

- Findings 展开 Finding：展示完整真实验证时间线；
- Evidence：通过 `FindingVerificationPanel` 展示同一完整时间线；
- Release Gate 单 Finding 上下文：展示紧凑时间线；
- Release 紧凑时间线必须始终保留原始 Finding 基线，并展示最近 3 次真实验证；中间历史折叠时明确显示折叠数量；
- 时间线只解释单 Finding 历史，不改变项目级 Release Gate。

### 7.3 Dashboard 最新一轮验证变化

Dashboard 首屏必须回答一个独立问题：**最新一次真实修复后验证，对哪些 Finding 的结论造成了什么变化？**

该口径与“当前各 Finding 的最新状态”不同，严禁把不同轮次的当前状态拼成所谓“本轮变化”。

项目最新真实轮次锚点只允许来自：

1. `regression_run.generated_at`；
2. 若上者不存在，则 `regression_summary.latest_run.generated_at`。

`deriveLatestVerificationRunSummary(findings, runAt)` 只统计 `Finding.regression.history` 中 `generated_at === runAt` 的真实回执。

如果项目级最新 Run 已存在，但 Finding history 没有同一个 `generated_at`：

- Dashboard 必须明确显示“逐问题变化暂不可对齐”；
- 不得拿每条 Finding 的最新历史替代；
- 不得补造“本轮修复 X 个”等数字。

同一真实 Run 内的 Finding 必须互斥归类：

- **刚验证修复**：该轮发生真实 `open -> fixed`；
- **重新出现**：该轮发生真实 `fixed -> open`；
- **仍失败**：该轮结果为 `open`，但没有发生 fixed -> open 变化；
- **无法确认**：该轮结果为 `unknown`；
- **保持通过**：该轮结果为 `fixed`，但之前已经是 fixed，不得重复计算成“刚验证修复”。

Dashboard 变化卡只表达验证价值事实；它不能改变项目级 Release Gate。

`RegressionGateBanner` 首屏区域必须同时容纳两类独立事实：

- `DashboardVerificationDeltaPanel`：这次验证改变了什么；
- Release / Regression Gate Banner：这些事实对发布意味着什么。

即使 Gate 通过，也不能隐藏有价值的最新 Finding 变化；即使 Gate 阻塞，也不能用 Gate 总数伪装成逐 Finding 变化。

## 8. 身份连续性

统一状态呈现和验证历史不能破坏既有：

`project + finding`

深链规则。

Dashboard / Findings / Evidence / Release 必须始终通过 Finding ID 精确识别问题；旧 Finding 不存在时明确提示，不按标题猜测替代。

## 9. CI 合同

现有 `test:finding-validation-boundary` 必须锁定：

- `FindingVerificationStatus` 只消费 `deriveFindingVerification()`；
- 统一 success / danger / warning / neutral 设计 Token；
- FindingCard 不存在第二套验证标签/色彩解释器；
- Findings 使用共享 priority；
- Dashboard Top 3 使用共享 priority；
- Dashboard focus、Evidence、Release 均使用共享状态组件；
- Release 仍使用 `deriveReleasePresentation()` 作为项目级发布权威。

`test:finding-verification-timeline` 必须锁定：

- 历史运行复用统一状态解释规则；
- 原始 Finding 永远作为时间线基线；
- `regression.history` 按真实时间正序展示；
- 只有 open / fixed 真实终态切换才能标记“结论变化”；
- unknown 不覆盖上一轮已知结论；
- 时间线保留 probe ID、method/path 等真实验证身份；
- Evidence 展示完整时间线；
- Release 展示保留基线的紧凑时间线；
- Dashboard 最新一轮变化必须按项目级 `generated_at` 精确锚定；
- “刚验证修复 / 重新出现 / 仍失败 / 无法确认 / 保持通过”必须互斥；
- 项目 Run 与 Finding history 无法精确关联时必须 fail-closed，不得补造变化数字；
- Dashboard 验证变化与项目级 Gate 权威必须保持分离；
- Release 项目级 Gate 权威不变。

`test:finding-context-navigation` 与 `test:customer-action-guidance` 必须适配 `DashboardFocusFindingCard` 的组件拆分，不得依赖旧的 Dashboard 内联实现。

## 10. 非目标

本 SPEC 不定义：

- Bug 如何发现；
- Finding 如何确认；
- Regression Probe 如何生成；
- Replay / Regression 如何执行；
- Release Gate 如何计算；
- 企业研发流程、工单、版本、负责人或项目管理。

前端只负责把 QualiBug 自己拥有权威的验证事实，稳定、一致、可理解地呈现给客户。
