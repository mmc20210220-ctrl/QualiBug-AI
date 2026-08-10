# QualiBug Finding / Evidence / Reverification 前端闭环 SPEC

状态：已实现  
范围：仅前端展示、导航与交互约束；不改变 Finding 成立、Evidence 生成、Replay / Regression 执行或 Release Gate 的后端判定。

## 1. 产品边界

QualiBug 当前只负责自己的独立验证闭环：

`发现问题 -> 证据 -> 客户在企业内部自行修复 -> QualiBug 重新验证 -> 已修复 / 仍失败 / 无法确认 -> Release Gate 更新`

前端不得渗透或管理企业内部研发流程。

明确禁止在 Finding 客户主界面提供：

- 负责人分配；
- 修复版本；
- “开发中 / 修复中 / 待提测”等研发状态；
- 研发反馈；
- Jira / GitLab / GitHub Issue 等外部工单流转字段；
- 风险接受 / 误报等人工流程替代自动验证结论；
- 任何能够手工把真实验证失败改成“已修复”的操作。

后端历史兼容 API 可以继续存在，但不得成为当前前端客户主流程依赖。

## 2. 客户验证闭环

客户从价值总览或问题清单查看一条已确认 Finding 时，必须能够沿同一条问题完成：

`Dashboard 重点关注 -> 指定 Finding -> 指定 Evidence -> 客户自行修复 -> 修复后重新验证 -> 验证结果 -> Release Gate`

QualiBug 只记录和表达自身拥有权威的状态：

- 已确认问题；
- 等待修复后重新验证；
- 修复验证通过；
- 重新验证仍失败；
- 本轮无法确认修复；
- 当前没有真实可执行重新验证义务。

## 3. Finding 验证状态单一解释器

前端使用 `deriveFindingVerification(finding)` 统一解释后端 Regression 状态。

状态优先级：

1. `passed / verified_fixed` -> **修复验证通过**；
2. `failed / reopened` 或 Gate `failed` -> **重新验证仍失败**；
3. `blocked / error / failed_safe / indeterminate / needs_review / not_executed / not_ready / skipped / unverifiable / unknown` -> **本轮无法确认修复**；
4. 已有真实 `included_in_suite` 但无终态 -> **等待修复后重新验证**；
5. 没有真实回归义务 -> **暂无可执行重新验证**。

禁止将 blocked / unknown / skipped 等状态包装成通过。

## 4. 修复前 / 修复后证据真实性

Finding 验证面板必须区分：

### 修复前基线

来自当前 Finding 已有真实事实：

- Expected；
- 问题发生时 Actual；
- 原始 Evidence Chain；
- Evidence Quality；
- Regression Verification Obligations。

### 最新修复后验证

只展示后端真实 Regression / Replay 回执：

- 最新状态；
- 执行时间；
- regression probe ID；
- method / path；
- reason / gate status；
- 历史真实验证记录。

如果当前 Regression 回执没有返回新的原始 Response / DB / UI Evidence，必须显示“当前回执未提供”，不得伪造修复前后 Evidence Package 或 Diff。

## 5. 单问题 Replay

当 Finding 存在真实 Replay Asset 时，Evidence Center 提供“修复后重新验证”。

Replay 规则：

- 使用当前 Finding 的真实复现入口；
- 不构造 synthetic Replay；
- 将“问题发生时原始证据”和“修复后当前响应”并列展示；
- Replay 成功复现原问题 -> 明确显示“问题仍可复现”；
- Replay 本次未复现 -> 只能显示“本次未复现”，**不能单独解释为已修复**；
- Replay 完成后触发项目数据重新读取；
- Finding / Release Gate 最终状态以后端持久化验证 / Regression 状态为准；
- 前端不得根据 HTTP status/body diff 自行推导“已修复”。

## 6. Regression 重新验证门禁

Findings / Dashboard / Coverage 使用同一 fail-closed 原则：

- 只有存在真实 `regression.included_in_suite` / persisted regression obligation 才允许执行回归；
- 没有真实回归义务时按钮 disabled；
- handler 即使被程序调用也必须再次检查真实义务；
- 不允许为了让按钮可用而构造 synthetic regression probe；
- 项目级 Release Regression 可以同时验证多个已纳入 Finding，UI 必须明确这是当前项目已纳入的真实回归义务，而不是企业研发流程操作。

## 7. Dashboard 精确 Finding 入口

Dashboard `重点关注 Top 3` 每条真实 Finding 必须：

- 提供“处理这条问题”，进入 `/findings?project=...&finding=...`；
- 仅当本 Finding 存在真实 `evidence_chain` 时提供“查看这条证据”；
- 证据入口进入 `/evidence?project=...&finding=...`；
- 不允许通过标题、模块、severity 猜测 Finding 身份；
- 移动端按钮必须保持可点击。

这里的“处理”只表示进入 QualiBug 的验证闭环，不代表任务分配或研发工单处理。

## 8. Finding 身份连续性

- 证据深链使用 `finding=<finding.id>`；
- `project` 与 `finding` 必须同时保留；
- Evidence Drawer 的“证据中心完整查看”必须进入指定 Finding；
- Evidence Center 切换问题时 URL 必须同步；
- Evidence Center 返回 Findings 时保留当前 Finding；
- Evidence Center 进入 Release Gate 时保留当前 Finding；
- Findings 收到 `finding` 后自动展开该问题并清除会隐藏它的本地筛选；
- Release Gate 收到 `finding` 后只按 ID 精确解析当前评审问题；
- Finding 已失效时必须明确提示，不允许按标题猜测相似问题。

## 9. Release Gate 单问题上下文边界

`finding` 参数只表示“用户当前正在评审哪条问题”，不能改变项目级 Release Gate 判定。

要求：

- Release Gate 继续使用 `deriveReleasePresentation()` 与项目级 Gate / Regression / Pipeline / Campaign 事实；
- 当前 Finding 只作为上下文卡片；
- 从 Release 返回 Findings / Evidence 时保留同一 Finding；
- 如果 Finding 已失效，明确提示；
- 单条 Finding 的 severity 或当前查看上下文不得覆盖项目级发布结论。

## 10. Evidence 身份与评分真实性

如果 URL 指定 Finding A：

- A 有 Evidence Package：展示 A；
- A 已确认但尚无 Evidence Package：明确提示；
- A 已不在当前结果：明确提示旧链接/状态变化；
- 不得静默展示 Finding B 的证据。

证据评分：

- 后端真实返回 `0` -> `0/100`；
- 未提供 score -> “未评分”或 `—`；
- 禁止 unknown -> 0。

## 11. CI 回归契约

`test:customer-action-guidance`：基础结果真实性与空态。  
`test:finding-context-navigation`：`project + finding` 身份连续性。  
`test:finding-validation-boundary`：产品验证边界与修复后重新验证语义。

`test:finding-validation-boundary` 至少锁定：

- FindingCard 不出现负责人、修复版本、研发反馈、外部任务链接、人工处理状态等企业流程字段；
- FindingCard 不调用 `updateFindingCollaboration`；
- `deriveFindingVerification()` 区分 verified / failed / inconclusive / pending / not-enrolled；
- 没有真实回归义务时 fail-closed；
- 验证面板不伪造修复后原始证据；
- Replay “仍可复现”不能包装成成功；
- Replay “本次未复现”不能自动变成已修复；
- Replay 后重新读取后端真相；
- 前端数据投影继续删除修复建议 / 修复方案 / 修复代码。

以上 contract 必须进入 `npm run ci:gate`。

## 12. 非目标

本 SPEC 不负责：

- Finding 如何发现或确认；
- Evidence 如何采集或评分；
- Regression Probe 如何生成；
- Regression / Replay 如何在后端执行；
- Release Gate 如何判定；
- 企业研发任务管理、人员分配、修复版本、工单、项目管理；
- Bug 召回率或后端测试能力。

这些全部由后端既有合同或企业自身研发流程负责。前端只负责准确消费、身份连续性、验证闭环和客户行动表达。
