# QualiBug 前端验证轮次深链 SPEC

状态：已实现  
范围：仅前端导航、真实验证历史定位与指定轮次解释；不改变 Finding、Regression、Evidence 或 Release Gate 的后端事实与判定。

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
5. 展示该轮“上一已知结论 -> 本轮真实结果 -> 是否改变结论 -> 对发布的含义”摘要；
6. 从该 Finding 打开 Evidence Drawer -> Evidence Center 时继续保留同一验证轮次。

如果 Finding 已不存在：

- 明确提示旧结果 / 状态变化；
- 不按标题选择替代 Finding。

## 5. Evidence 行为

Evidence 读取同一 `finding + verification_at`。

若该 Finding 有真实 evidence：

- 选择该 Finding；
- 验证时间线定位同一 `verification_at`；
- 展示与 Findings 相同的指定轮次变化摘要；
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

## 7. 指定验证轮次变化摘要

`FindingVerificationRunSummary` 只在存在 `verification_at` 时显示，并且必须消费共享解释器：

`deriveFocusedVerificationRunSummary(finding, verification_at)`

摘要必须回答四个问题：

1. **上一已知结论**：该轮之前最近一个真实 `open / fixed` 终态；
2. **本轮真实结果**：指定 history event 本身的真实解释；
3. **是否改变结论**：只有 `open <-> fixed` 才算真实结论变化；
4. **对发布的含义**：解释单 Finding 对风险判断的含义，但绝不生成项目级放行结论。

### 7.1 上一已知结论规则

从指定 event 向前查找最近一个 `open / fixed`：

- 中间 `unknown / blocked / skipped / unverifiable` 必须跳过；
- unknown 不得成为下一轮比较基线；
- 如果之前没有其他验证终态，则原始 Finding 基线视为 `open`。

例如：

`open -> fixed -> unknown -> unknown -> fixed`

最后一轮的上一已知结论仍然是 `fixed`，因此只能显示“修复结论保持通过”，不能显示“unknown -> fixed”或“再次修复”。

### 7.2 发布含义边界

指定轮次为 `open`：

- 可以说明这条 Finding 本轮仍是已知验证风险；
- 是否阻断发布仍由项目级 Release Gate 判定。

指定轮次为 `fixed`：

- 可以说明这条 Finding 本轮已验证恢复；
- **单条 Finding 通过不等于项目可以发布**。

指定轮次为 `unknown`：

- 不能证明已修复；
- 不能作为放行依据；
- 项目级 Release Gate 仍需其他真实事实。

摘要必须保留该轮后端真实存在的 Probe、method/path、Gate 等身份信息；不存在的字段保持缺失，不补造。

指定 `verification_at` 不存在时，摘要必须 fail-closed，明确提示无法形成这一轮前后变化摘要，不得使用最近 run 替代。

### 7.3 历史轮次与当前最新结论必须分离

`verification_at` 是**阅读锚点**，不是当前 Finding 状态覆盖器。

前端必须通过 `deriveFindingVerificationFocusContext(finding, verification_at)` 判断被指定的真实 history event 是否就是当前最新真实验证 event。

规则：

- 最新真实验证 event 必须来自 `buildFindingVerificationTimeline(finding)` 中最后一个真实 `kind === 'verification'` event；
- 不能用 `Date.now()`、时间窗口、最近邻时间或标题相似度判断；
- 指定 event 与最新 event 使用真实 timeline event identity 比较；
- 指定 event 不存在时继续 fail-closed，不得降级到 latest。

如果指定轮次就是最新轮次：

- 标记 `当前最新验证 / 最新`；
- 本轮结果可以描述为“当前最新真实结果”；
- 仍不得覆盖项目级 Release Gate。

如果指定轮次是历史轮次：

- 必须标记 `历史验证轮次 / 历史`；
- 必须同时显示当前最新真实验证时间与当前最新真实结论；
- 必须明确“正在查看历史轮次”；
- 历史本轮结果只能描述当时的 Finding 状态变化；
- 历史轮次不能覆盖当前最新 Finding 结论；
- 历史轮次的发布含义只能用于追溯，当下发布判断必须结合当前最新 Finding 结论 + 项目级 Release Gate。

`FindingVerificationPanel` 顶部共享 `FindingVerificationStatus` 永远表示**当前最新结论**，因此必须显式标记“当前最新结论”。当页面同时定位历史 `verification_at` 时，还必须提示：上方状态是当前最新状态，下方指定轮次只是历史追溯。

这样可以避免以下真实但容易误读的组合：

`当前最新结论 = 修复验证通过`

同时：

`正在查看的历史轮次 = 重新验证仍失败`

两者都是真实事实，但属于不同时间点，前端不得把它们混成同一时刻的结论。

## 8. Release 紧凑时间线与轮次摘要

Release Gate 仍然是项目级发布权威。

当 Release 接收到 `finding + verification_at` 时：

- 单 Finding 仅作为评审上下文；
- 展示与 Findings / Evidence 相同的 `FindingVerificationRunSummary`；
- `FindingVerificationTimeline` 使用 compact 模式；
- 原始 Finding 基线必须保留；
- 被指定的验证轮次必须保留，即使它原本属于被折叠的旧历史；
- 同时保留最近真实验证以帮助理解当前状态；
- 折叠的中间历史必须明确显示数量。

如果指定轮次是历史轮次，Release 中的共享 summary 必须明确它是历史追溯，并同时展示当前最新 Finding 验证结论；无论历史还是最新指定轮次，都不得覆盖 `deriveReleasePresentation()` 的项目级发布判断。

## 9. URL 生命周期

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

## 10. CI 合同

`test:finding-verification-timeline` 必须锁定：

- `evidenceDeepLinkSearch(findingId, verificationAt)`；
- Dashboard row 使用 `event.generatedAt`；
- Findings / Evidence / Release 读取 `verification_at`；
- Findings 只将 run focus 传给 exact Finding；
- Evidence 主动切 Finding会删除旧 run focus；
- Evidence Drawer 进入完整 Evidence 时保留 run focus；
- Timeline 精确匹配 `generatedAt`；
- stale run fail-closed；
- `deriveFocusedVerificationRunSummary()` 只解析精确 run；
- 上一已知结论跳过 unknown；
- Findings / Evidence / Release 共用 `FindingVerificationRunSummary`；
- 单 Finding fixed 不得被解释为项目可以发布；
- unknown 不得作为放行依据；
- Release compact 视图保留基线 + 指定 run + 最近 run；
- 项目级 Release authority 不变。

`test:finding-verification-focus` 必须额外锁定：

- 历史 / 最新判断来自真实 timeline verification event；
- 不允许时间窗口或 nearest-run heuristic；
- 最新指定轮次显示“当前最新验证 / 最新”；
- 历史指定轮次显示“历史验证轮次 / 历史”；
- 历史轮次同时暴露当前最新验证时间与最新结论；
- 历史轮次不能覆盖当前 Finding 状态；
- `FindingVerificationPanel` 顶部状态显式标记“当前最新结论”；
- 历史轮次发布说明必须回到当前最新 Finding 结论 + 项目级 Release Gate。

`test:finding-context-navigation` 与 `test:customer-action-guidance` 必须与上述上下文传播保持一致。

## 11. 非目标

本 SPEC 不定义：

- 验证历史如何由后端生成；
- Regression Probe 如何执行；
- Finding 是否修复的算法；
- Release Gate 如何计算；
- 企业研发流程、负责人、修复版本、任务状态或工单。

前端只负责对后端已经存在的真实 Finding 与真实验证轮次进行**精确、可追溯、不会串错对象、不会把历史状态冒充当前状态、不会夸大发布结论**的阅读与解释。
