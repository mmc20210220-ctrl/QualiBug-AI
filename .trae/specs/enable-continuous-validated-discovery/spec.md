# 持续严格验证缺陷检测 Campaign Spec

## Why
当前系统更接近“单次 run 扫描器”：跑完一轮就结束，容易给人“已经扫完”的错觉，但真实业务行为空间远大于单轮预算，很多缺陷还依赖异步链路、状态漂移、时间窗口和数据条件才会暴露。产品既然主打持续检测，就必须把能力模型从“单次跑完”升级为“多轮持续检测、持续补证、持续扩覆盖”，并且所有正式结果仍然只承认严格可复现、有完整证据的真实 Bug。

## What Changes
- 引入 `campaign` 概念，把单次 `run` 降级为持续检测过程中的一个预算切片。
- 增加跨轮覆盖账本，记录行为单元在不同轮次中的触达、阻断、待补证和已验证状态。
- 增加 frontier 选择与增量调度机制，让系统每轮优先处理最值得继续打的高价值未闭环行为。
- 增加 `explore / exploit / revalidate` 三类预算，分别用于拓新面、补 strict 证据、重检历史高价值路径。
- 增加环境变化、知识资产更新、阻断解除后的自动重检触发机制。
- 增加 campaign 级汇总报表，区分“本轮新增 validated bug”和“累计 validated bug”，并展示剩余高价值未覆盖 frontier。

## Impact
- Affected specs: runtime probe 选择链、discovery loop、coverage ledger、benchmark/项目汇总报表、调度控制面
- Affected code: `real_project_defect_discovery.py`、`risk_based_probe_planner.py`、`agent_discovery_loop.py`、coverage/ledger 相关模块、campaign 调度与汇总模块、对应 tests

## ADDED Requirements
### Requirement: 持续检测 Campaign 模式
系统 SHALL 以长期运行的 `campaign` 模式组织缺陷检测，而不是把单次 `run` 视为完整检测终点。

#### Scenario: 创建持续检测任务
- **WHEN** 用户启动某个项目的持续检测
- **THEN** 系统创建一个 campaign，并在其下持续产生多个 run
- **AND** 单次 run 只表示本轮预算切片，不得在语义上宣称“已扫完全部行为”

### Requirement: 跨轮覆盖账本
系统 SHALL 维护跨轮覆盖账本，对每个关键行为单元记录是否未触达、已触达、待补证、已验证、被阻断或待重检。

#### Scenario: 行为单元跨轮推进
- **WHEN** 某个行为单元在本轮只拿到 candidate 或 pending finding
- **THEN** 系统将该行为单元写入覆盖账本
- **AND** 账本显式记录缺失的是执行、verifier、repro、evidence 还是环境条件
- **AND** 后续 run 可以继续从该状态推进，而不是把本轮结果丢失

### Requirement: Frontier 驱动的增量调度
系统 SHALL 为每个 campaign 维护可执行 frontier，并在每轮优先选择最值得继续检测的高价值未闭环行为。

#### Scenario: 下一轮选择 frontier
- **WHEN** 系统为下一轮构建 probe 预算
- **THEN** 优先考虑高业务价值但未 validated 的行为、已 pending 且只差补证的行为、历史 validated yield 高的行为、以及长期未重检的关键路径
- **AND** 不得只因为单轮 candidate 容易变多就长期挤占 strict validated 产出更高的 frontier

### Requirement: 多预算切片
系统 SHALL 把 campaign 预算拆为 `explore`、`exploit`、`revalidate` 三类切片，而不是只按单一 probe 总量线性截断。

#### Scenario: 分配单轮预算
- **WHEN** 系统为某轮分配预算
- **THEN** `explore` 用于打开新的行为面和风险族
- **AND** `exploit` 用于把已有 pending finding 推进到 validated bug
- **AND** `revalidate` 用于重检历史 validated bug 或高风险关键路径的新鲜度

### Requirement: 条件变化触发重检
系统 SHALL 在环境、知识资产、数据条件或阻断状态发生变化时，自动唤醒相关 frontier 进行重检。

#### Scenario: 阻断解除后重试
- **WHEN** 某批 frontier 之前因环境不可测、数据不足或路径未对齐而被阻断
- **AND** 后续检测到相关阻断已解除
- **THEN** 系统自动将这些 frontier 放回候选执行队列
- **AND** 报表中清楚标记这是重检推进，而不是全新的首次发现

### Requirement: Campaign 级严格报表
系统 SHALL 在 campaign 级汇总中同时展示本轮与累计严格口径结果，并清楚展示剩余未覆盖高价值 frontier。

#### Scenario: 查看持续检测看板
- **WHEN** 用户查看 campaign 状态
- **THEN** 页面展示本轮新增 validated bug、累计 validated bug、pending 转 validated 转化率、frontier burn-down、剩余高价值未覆盖行为数
- **AND** 对外正式结论默认仍只使用 validated bug 口径

## MODIFIED Requirements
### Requirement: Discovery 结果语义
系统 SHALL 将 `run` 的定位修改为“持续检测 campaign 中的一次采样与推进动作”，而不是“项目检测已完成”的充分证据。

#### Scenario: 单轮执行结束
- **WHEN** 某次 run 正常完成
- **THEN** 系统输出该轮的新增结果、漏斗、阻断与下一轮推荐 frontier
- **AND** 若 campaign 仍存在未覆盖高价值行为或待补证 frontier，则系统继续保持 campaign 为 active 或 scheduled 状态
