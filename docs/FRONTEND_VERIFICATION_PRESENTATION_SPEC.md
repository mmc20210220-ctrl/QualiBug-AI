# QualiBug 前端验证状态统一呈现 SPEC

状态：已实现  
范围：仅前端状态解释、视觉表达、关注排序与客户下一步提示；不改变 Finding、Replay、Regression 或 Release Gate 的后端判定。

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

## 7. 身份连续性

统一状态呈现不能破坏既有：

`project + finding`

深链规则。

Dashboard / Findings / Evidence / Release 必须始终通过 Finding ID 精确识别问题；旧 Finding 不存在时明确提示，不按标题猜测替代。

## 8. CI 合同

现有 `test:finding-validation-boundary` 必须锁定：

- `FindingVerificationStatus` 只消费 `deriveFindingVerification()`；
- 统一 success / danger / warning / neutral 设计 Token；
- FindingCard 不存在第二套验证标签/色彩解释器；
- Findings 使用共享 priority；
- Dashboard Top 3 使用共享 priority；
- Dashboard focus、Evidence、Release 均使用共享状态组件；
- Release 仍使用 `deriveReleasePresentation()` 作为项目级发布权威。

`test:finding-context-navigation` 与 `test:customer-action-guidance` 必须适配 `DashboardFocusFindingCard` 的组件拆分，不得依赖旧的 Dashboard 内联实现。

## 9. 非目标

本 SPEC 不定义：

- Bug 如何发现；
- Finding 如何确认；
- Regression Probe 如何生成；
- Replay / Regression 如何执行；
- Release Gate 如何计算；
- 企业研发流程、工单、版本、负责人或项目管理。

前端只负责把 QualiBug 自己拥有权威的验证事实，稳定、一致、可理解地呈现给客户。
