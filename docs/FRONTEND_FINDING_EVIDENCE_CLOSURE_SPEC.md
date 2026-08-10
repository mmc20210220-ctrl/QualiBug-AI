# QualiBug Finding / Evidence 前端闭环 SPEC

状态：已实现  
范围：仅前端展示、导航与交互约束；不改变 Finding 成立、Evidence 生成、Regression 执行或 Release Gate 的后端判定。

## 1. 目标

客户从价值总览或问题清单查看一条已确认问题时，必须能够沿同一条 Finding 完成：

`Dashboard 重点关注 -> 指定问题 -> 指定证据 -> 发布门禁 -> 返回同一问题 / 同一证据 -> 回归闭环`

前端不得因为页面切换丢失 Finding 身份，也不得把后端未知状态展示成确定的负面数值。

## 2. Dashboard 重点问题入口

Dashboard `重点关注 Top 3` 不是只读摘要。

每条真实 Finding 必须：

- 提供“处理这条问题”，进入 `/findings?project=...&finding=...`；
- 仅当本条 Finding 存在真实 `evidence_chain` 时提供“查看这条证据”；
- 证据入口进入 `/evidence?project=...&finding=...`；
- 不允许通过标题、模块、severity 猜测 Finding 身份；
- 移动端按钮必须换行并在 560px 以下保持可点击的全宽操作。

项目级“查看完整清单 / 查看证据”仍可以存在，但不能替代单问题精确入口。

## 3. Finding 身份连续性

- 证据深链使用 `finding=<finding.id>`；
- `project` 与 `finding` 必须同时保留；
- Evidence Drawer 的“证据中心完整查看”必须进入指定 Finding；
- Evidence Center 切换问题时 URL 必须同步更新；
- Evidence Center 返回问题清单时必须保留当前 Finding；
- Evidence Center 进入 Release Gate 时必须保留当前 Finding；
- Findings 收到 `finding` 参数后自动展开该问题并清除会隐藏它的本地筛选；
- Release Gate 收到 `finding` 后只按 `finding.id` 精确解析当前评审问题；
- 如果 Finding 已不在当前已确认结果中，前端必须明确提示旧链接/状态变化，不允许按标题猜测相似问题。

## 4. Release Gate 的单问题上下文边界

`finding` 参数只表示“用户当前正在评审哪条问题”，**不能改变项目级 Release Gate 判定**。

要求：

- Release Gate 继续使用现有 `deriveReleasePresentation()` 与项目级 Gate / Regression / Pipeline / Campaign 事实；
- 当前 Finding 只作为上下文卡片展示；
- 从 Release 返回问题清单时，若该 Finding 仍存在，必须返回同一 Finding；
- 从 Release 返回证据中心时，仅在本 Finding 真实存在 Evidence Package 时保留精确证据入口；
- 如果 `finding` 已失效，明确提示“原问题已不在当前已确认结果中”；
- 失效上下文不得按标题或相似内容绑定到另一条 Finding；
- 单条 Finding 的 severity、证据、人工处置都不得覆盖项目级发布结论。

## 5. 证据身份保护

如果 URL 指定 Finding A：

- A 有 Evidence Package：展示 A；
- A 是已确认 Finding 但尚无 Evidence Package：明确显示“该问题当前还没有可展示证据包”；
- A 已不在当前已确认结果：明确显示旧链接/状态变化；
- 不得静默展示 Finding B 的证据来填充详情区。

没有指定 Finding 时，才允许 Evidence Center 默认展示第一条真实证据包。

## 6. 证据评分真实性

- 后端真实返回 `0`：允许显示 `0/100`；
- 后端没有提供 score：显示“未评分”或 `—`；
- Evidence 列表、Evidence Drawer、QualityScore 圆环必须使用同一未知值语义；
- 禁止通过 `?? 0` 将 unknown 转成 0 分；
- 未评分时圆环不得绘制红色 0 分进度，避免制造“证据质量极差”的虚假结论。

## 7. Regression 前端门禁

Findings 与 Coverage 必须保持一致：

- 只有存在真实 `included_in_suite` / persisted regression obligation 才显示可执行回归；
- 没有真实回归义务时按钮 disabled；
- handler 即使被程序调用也必须 fail-closed；
- 不允许为了让按钮可用而构造 synthetic regression probe；
- Regression 结果仍以既有后端返回为唯一事实来源。

## 8. 项目导航工具

`navigateToProjectPath(pathname, projectId, currentSearch?)` 必须允许携带额外实体上下文，例如 `finding`。

要求：

- 自动写入/覆盖当前 `project`；
- 保留调用方显式提供的实体 query；
- 旧调用不提供 `currentSearch` 时行为保持不变；
- 不通过全局 sessionStorage 保存当前 Finding，避免跨客户污染。

## 9. 回归契约

`test:customer-action-guidance` 继续锁定 Finding / Evidence 基础真实性与空态。

新增 `test:finding-context-navigation` 锁定：

- Dashboard Top 3 使用稳定 Finding 深链；
- Dashboard 仅在本 Finding 有证据时显示精确证据入口；
- Findings 接受并展开 `finding`；
- 旧 Finding 不按标题猜测替代；
- Evidence 精确匹配指定 Finding；
- 指定 Finding 无证据时不静默 fallback；
- Evidence -> Findings / Release 往返保留 `finding`；
- Release Gate 精确解析 Finding ID，但仍由项目级 `deriveReleasePresentation()` 决定发布状态；
- Release -> Findings / Evidence 返回同一 Finding；
- project-navigation 支持 `project + entity query` 组合导航；
- Dashboard Top 3 单问题操作在窄屏保持可用。

该 contract 必须进入 `npm run ci:gate`。

## 10. 非目标

本 SPEC 不负责：

- Finding 如何发现或确认；
- Evidence 如何采集或评分；
- Regression Probe 如何生成；
- Regression 如何执行；
- Release Gate 如何判定；
- Bug 召回率或后端测试能力。

上述全部由后端既有合同提供，前端只负责准确消费、身份连续性与客户行动表达。