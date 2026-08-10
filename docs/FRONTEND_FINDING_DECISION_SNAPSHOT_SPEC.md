# QualiBug Finding / Evidence 首屏判断摘要 SPEC

状态：已实现  
范围：仅前端信息架构与展示解释；不改变 Finding、Evidence、Regression、Replay 或 Release Gate 的后端事实与判定。

## 1. 目标

客户打开一条已确认 Finding 后，第一屏应在 5～10 秒内回答：

1. **发生了什么**；
2. **为什么成立**；
3. **证据状态如何**；
4. **当前真实验证结论是什么**；
5. **下一步验证动作是什么**。

完整证据、复现步骤、历史验证时间线继续保留，但不能要求客户先阅读所有工程字段才能理解问题。

## 2. 单一共享摘要

Findings 与 Evidence Center 必须复用：

`FindingDecisionSnapshot`

展示数据由：

`deriveFindingDecisionPresentation(finding)`

统一整理。

不得在 Findings、Evidence 各自维护另一套问题结论或验证解释器。

## 3. 事实来源

### 3.1 发生了什么

按现有后端字段优先级展示：

- `business_summary`；
- `business_impact.summary`；
- `actual`；
- 最后才使用中性缺省文案。

不得由前端生成新的业务影响结论。

### 3.2 为什么成立

优先使用后端：

`expected_actual_comparison.difference`

若未提供，只展示已有 `expected` 与 `actual` 的并列事实。

不得由前端基于文本差异重新推导 Bug 是否成立。

### 3.3 证据状态

只能汇总：

- `evidence_quality.label`；
- `evidence_quality.summary`；
- `evidence_chain.length`；
- `proof.repro_rate` 或已上报复现次数。

禁止定义前端阈值，例如：

- score >= N => 证据充分；
- confidence >= N => 已确认；
- 证据条数 >= N => 可发布。

### 3.4 当前验证结论

必须复用：

`deriveFindingVerification(finding)`

以及：

`FindingVerificationStatus`

不得根据 Replay Diff、证据数量、当前页面状态重新推断“已修复”。

### 3.5 下一步验证

必须使用共享 verification presentation 的：

- `nextActionLabel`；
- `detail`。

该动作只属于 QualiBug 自己的验证闭环，不得扩展成企业负责人、修复版本、工单、研发进度等项目管理流程。

## 4. Findings 首屏

展开 Finding 后顺序必须是：

1. `FindingDecisionSnapshot`；
2. 需要进一步核对时查看预期 / 实际与复现细节；
3. 问题摘要复制与产品责任边界；
4. 完整 `FindingVerificationPanel`。

客户无需先阅读完整验证历史才能知道当前状态。

## 5. Evidence Center 首屏

选择一条真实 Evidence Finding 后顺序必须是：

1. Finding severity + title；
2. `FindingDecisionSnapshot`；
3. 完整证据依据：Quality、Expected/Actual、Replay、Evidence Timeline；
4. 完整修复后验证面板。

证据页仍然以证据为主体，因此完整证据可以默认展开；但摘要必须先出现。

## 6. 产品真实性边界

判断摘要必须明确：

- 这里只汇总后端已有 Finding / Evidence / Verification 事实；
- 前端不自行判定“已修复”；
- 单条 Finding 的状态不替代项目级 Release Gate；
- 单次 Replay 未复现不自动关闭 Finding；
- 缺失证据字段保持“未上报 / 未评分”，不得补造。

## 7. 响应式

桌面端可使用双列信息网格；窄屏必须降为单列，保证长业务摘要、Expected/Actual 与验证说明可以自然换行。

不得为了首屏紧凑而截断关键结论到不可读。

## 8. CI 合同

`test:finding-decision-snapshot` 必须锁定：

- 共享 decision presentation；
- 共享 verification interpreter；
- 后端 evidence quality / evidence chain / repro truth；
- 五个首屏问题；
- Findings 先摘要后完整验证；
- Evidence 先摘要后详细证据；
- 禁止前端 evidence/confidence 阈值；
- 禁止前端自行分配“已修复”；
- Release Gate authority 不变；
- 移动端单列。

`test:finding-validation-boundary` 必须接受共享摘要作为 Evidence 当前验证状态的展示载体，而不是锁死状态组件必须直接写在 Evidence 页面 JSX 中。
