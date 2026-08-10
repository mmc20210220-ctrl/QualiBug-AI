# QualiBug 前端验证轮次深链 SPEC

状态：已实现  
范围：仅前端导航与真实验证历史定位；不改变 Finding、Regression、Evidence 或 Release Gate 的后端事实与判定。

## 1. 目标

客户从 Dashboard 查看“刚验证修复 / 重新出现 / 仍失败 / 无法确认”等最新真实验证变化时，必须能够直接进入**造成该结果的具体 Finding + 具体真实验证轮次**，而不是只打开 Finding 后再人工寻找是哪一次验证。

统一阅读路径：

`Dashboard 最新验证变化 -> 指定 Finding + 指定验证轮次 -> Findings / Evidence -> Release Gate`

## 2. 稳定身份

深链使用两个独立身份：

- `finding=<finding.id>`：稳定 Finding 身份；
- `verification_at=<regression.history.generated_at>`：该 Finding 的具体真实验证回执时间锚点。

示例语义：

`?finding=<exact-id>&verification_at=<exact-generated-at>`

`verification_at` 只能在存在 `finding` 时生成。不得单独使用验证时间猜测 Finding。

## 3. Dashboard 出口

`DashboardVerificationDeltaPanel` 的具体变化列表直接消费 `deriveLatestVerificationRunSummary(...).rows`。

每一条下钻必须使用该 row 自身的：

- `finding.id`；
- `event.generatedAt`。

因此：

- `定位这次验证` -> Findings；
- `证据 + 本次验证` -> Evidence，且仅当该 Finding 有真实 evidence 时出现。

不得使用 title、列表位置、severity 或当前 latest_status 猜测目标。

## 4. Findings 行为

Findings 读取：

- `finding`；
- `verification_at`。

行为：

1. 自动展开精确 Finding；
2. 清除会遮挡该 Finding 的筛选和搜索；
3. 将 `verification_at` 仅传给该精确 Finding 的验证面板；
4. 时间线滚动并高亮精确 `generated_at`；
5. 从该 Finding 打开 Evidence Drawer -> Evidence Center 时继续保留同一验证轮次。

如果 Finding 已不存在：

- 明确提示旧结果 / 状态变化；
- 不按标题选择替代 Finding。

## 5. Evidence 行为

Evidence 读取同一 `finding + verification_at`。

若该 Finding 有真实 evidence：

- 选择该 Finding；
- 验证时间线定位同一 `verification_at`；
- 返回 Findings 或进入 Release 时继续保留同一上下文。

若该 Finding 没有 Evidence：

- 不得静默切到其他 Finding；
- 可以引导客户回 Findings；
- 可以让客户主动选择第一条真实 Evidence，但该主动切换必须删除旧 `verification_at`。

用户在 Evidence 左侧主动切换到另一 Finding 时，也必须删除旧 `verification_at`，避免把 A Finding 的验证轮次套到 B Finding。

## 6. 时间线定位规则

`FindingVerificationTimeline` 使用严格匹配：

`event.kind === 'verification' && event.generatedAt === verification_at`

匹配成功：

- 滚动至目标轮次；
- 获得键盘焦点；
- 显示“当前定位”；
- 显示该轮 transition / Probe / method-path / Gate 等真实信息。

匹配失败：

- 明确提示“指定验证轮次不在当前 Finding 的真实 history 中”；
- 不得退化为最近一次验证；
- 不得根据相邻时间猜测；
- 不得补造历史。

## 7. Release 紧凑时间线

Release Gate 仍然是项目级发布权威。

当 Release 接收到 `finding + verification_at` 时：

- 单 Finding 仅作为评审上下文；
- `FindingVerificationTimeline` 使用 compact 模式；
- 原始 Finding 基线必须保留；
- 被指定的验证轮次必须保留，即使它原本属于被折叠的旧历史；
- 同时保留最近真实验证以帮助理解当前状态；
- 折叠的中间历史必须明确显示数量。

指定 Finding 或指定验证轮次都不得覆盖 `deriveReleasePresentation()` 的项目级发布判断。

## 8. URL 生命周期

必须保留 `verification_at` 的路径：

- Dashboard delta -> Findings；
- Dashboard delta -> Evidence；
- Findings -> Evidence Center（针对同一指定 Finding）；
- Findings -> Release（针对同一指定 Finding）；
- Evidence -> Findings；
- Evidence -> Release；
- Release -> Findings；
- Release -> Evidence。

必须清除 `verification_at` 的情况：

- 用户主动选择另一 Finding；
- 导航目标不再是原 Finding 上下文；
- 仅项目级页面、不需要单 Finding 验证轮次时。

## 9. CI 合同

`test:finding-verification-timeline` 必须锁定：

- `evidenceDeepLinkSearch(findingId, verificationAt)`；
- Dashboard row 使用 `event.generatedAt`；
- Findings / Evidence / Release 读取 `verification_at`；
- Findings 只将 run focus 传给 exact Finding；
- Evidence 主动切 Finding 会删除旧 run focus；
- Evidence Drawer 进入完整 Evidence 时保留 run focus；
- Timeline 精确匹配 `generatedAt`；
- stale run fail-closed；
- Release compact 视图保留基线 + 指定 run + 最近 run；
- 项目级 Release authority 不变。

`test:finding-context-navigation` 与 `test:customer-action-guidance` 必须与上述上下文传播保持一致。

## 10. 非目标

本 SPEC 不定义：

- 验证历史如何由后端生成；
- Regression Probe 如何执行；
- Finding 是否修复的算法；
- Release Gate 如何计算；
- 企业研发流程、负责人、修复版本、任务状态或工单。

前端只负责对后端已经存在的真实 Finding 与真实验证轮次进行**精确、可追溯、不会串错对象**的阅读导航。
