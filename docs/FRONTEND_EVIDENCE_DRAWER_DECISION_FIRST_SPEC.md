# QualiBug Evidence Drawer 决策优先 SPEC

状态：已实现  
范围：仅前端快捷证据阅读、历史验证上下文、证据导出与安全分发的呈现优先级；不改变 Finding、Evidence、Regression 或 Release Gate 的后端事实与判定。

## 1. 目标

用户从 Findings 点击“查看证据”后，Drawer 的第一任务不是分享、导出或展示技术细节，而是帮助客户快速回答：

1. 这是什么问题；
2. 为什么成立；
3. 当前证据状态如何；
4. 当前最新验证结论是什么；
5. 下一步验证是什么。

推荐阅读顺序：

`问题标题 -> 问题判断摘要 -> 指定验证轮次（若存在） -> 核心证据 -> 完整证据中心 -> 技术复现信息 -> 证据工具`

## 2. 问题判断摘要

Drawer 必须复用 `FindingDecisionSnapshot`，不得另写一套证据充分性、修复状态或验证状态算法。

摘要只能消费已有真实 Finding / Evidence / Verification 字段。前端不得使用 evidence score、confidence 或自定义阈值独立判定：

- “问题成立”；
- “证据充分”；
- “已经修复”；
- “可以发布”。

## 3. 指定验证轮次

如果 Drawer 收到真实 `focusGeneratedAt`，必须复用 `FindingVerificationRunSummary` 展示该精确验证轮次。

该历史轮次只用于解释当时事实：

- 不得覆盖当前最新 Finding 验证状态；
- 不得替代当前 Release Gate；
- 不得用最近轮次或相邻时间替代 stale `verification_at`。

进入完整 Evidence Center 时必须继续携带相同 `finding + verification_at` 上下文。

## 4. 核心证据

Decision Snapshot 之后立即展示：

- 后端 Evidence Quality；
- Expected vs Actual；
- 真实 `evidence_chain`。

如果 `evidence_chain` 为空：

- 必须明确说明当前没有可展示的真实证据链；
- 不得用 evidence score、业务摘要或 Finding 标题冒充证据链。

Drawer 是快捷阅读，不需要把全部后端原始字段复制进首屏。完整证据仍由 Evidence Center 承担。

## 5. 完整证据中心是主出口

Drawer 头部和核心证据区可以提供“证据中心完整查看”入口。

该入口属于主要动作，因为它继续问题核验主线。

复制、打印、PDF 和公开分享不能与“完整证据查看”竞争同一首屏优先级。

## 6. 技术复现信息

原始 `curl_command` 如果真实存在，可以在登录态 Drawer 中保留，但必须：

- 位于核心证据之后；
- 默认作为进一步技术核对信息；
- 明确说明外部分发不得直接携带原始复现命令。

前端不得从 curl 是否存在推导 Finding 是否成立。

## 7. 证据工具

复制、打印 / PDF、临时只读分享统一进入 `EvidenceDistributionTools`。

该区域必须默认折叠，并明确：

> 这些是证据核对后的分发工具，不参与问题是否成立或是否修复的判断。

安全约束继续保持：

- 复制 / 打印使用统一脱敏 evidence package builder；
- 只读分享必须绑定稳定 `finding_persistence_id`；
- 找不到持久化 ID 时 fail-closed；
- 公开 Token 明文只返回一次；
- Token 不写 localStorage / sessionStorage；
- 公开链接只读取创建时冻结的脱敏快照；
- 支持到期与立即撤销；
- 原始 curl / credentials / raw sensitive bodies 不得直接进入外部分发包。

## 8. 响应式

移动端必须保证：

- Drawer 头部标题与动作不互相挤压；
- 主要按钮可以单列全宽；
- 证据工具按钮可以单列触控；
- 分享有效期、链接输入与按钮不会产生横向溢出。

响应式只改变布局，不改变验证语义、证据事实或安全边界。

## 9. CI 合同

现有合同必须共同锁定：

- `test:finding-decision-snapshot`：Drawer 先显示共享问题判断摘要；
- `test:evidence-distribution`：分发工具位于判断与核心证据之后，且脱敏规则不变；
- `test:evidence-share`：只读分享安全与稳定 Finding 身份边界不变；
- `test:finding-context-navigation` / `test:customer-action-guidance`：`finding + verification_at` 上下文继续精确传播。

## 10. 非目标

本 SPEC 不定义：

- Finding 是否成立的后端算法；
- Evidence Quality 如何评分；
- Replay / Regression 如何执行；
- Release Gate 如何计算；
- 企业内部研发负责人、任务、修复版本或流程管理。

Evidence Drawer 只负责把后端真实问题与证据按客户决策顺序呈现：**先理解问题，再核对证据，最后才分发证据。**
