# Tasks
- [x] Task 1: 明确持续检测 Campaign 的对象模型与状态机
  - [x] SubTask 1.1: 定义 `campaign`、`run`、`frontier`、`coverage ledger`、`revalidation` 的统一语义
  - [x] SubTask 1.2: 定义 campaign 的 `active`、`scheduled`、`blocked`、`completed`、`paused` 状态与迁移规则
  - [x] SubTask 1.3: 明确单轮 run 结束后如何产出下一轮推荐 frontier 与继续条件

- [x] Task 2: 建立跨轮覆盖账本
  - [x] SubTask 2.1: 为行为单元设计可跨轮追踪的稳定 key，至少覆盖路径、风险族、角色/状态或等价语义维度
  - [x] SubTask 2.2: 记录 `untouched`、`candidate`、`pending`、`validated`、`blocked`、`revalidate_due` 等状态
  - [x] SubTask 2.3: 记录每个 frontier 的阻断原因、最后一次运行结果、证据成熟度与下一步动作

- [x] Task 3: 增加 frontier 驱动的增量调度
  - [x] SubTask 3.1: 基于业务价值、validated yield、证据成熟度、未覆盖程度和阻断解除情况生成下一轮 frontier
  - [x] SubTask 3.2: 让预算选择器优先拉起高价值未闭环 frontier，而不是重复消费低价值候选
  - [x] SubTask 3.3: 输出“为什么选择这一轮 frontier”的可解释摘要

- [x] Task 4: 增加 `explore / exploit / revalidate` 三类预算
  - [x] SubTask 4.1: 设计三类预算的默认比例与可配置项
  - [x] SubTask 4.2: 定义哪些 frontier 进入 explore，哪些进入 exploit，哪些进入 revalidate
  - [x] SubTask 4.3: 保证高价值 pending frontier 在 exploit 预算中有稳定保留名额

- [x] Task 5: 增加重检触发与持续运行机制
  - [x] SubTask 5.1: 定义环境恢复、数据就绪、知识资产更新、路径对齐变化等重检触发条件
  - [x] SubTask 5.2: 让 campaign 在单轮结束后能自动进入下一次 scheduled 或 active 状态
  - [x] SubTask 5.3: 对长期阻断 frontier 输出暂停原因与唤醒条件

- [x] Task 6: 固化 campaign 级报表与看板口径
  - [x] SubTask 6.1: 区分本轮新增 validated bug、累计 validated bug、pending 转 validated 转化率
  - [x] SubTask 6.2: 展示 frontier burn-down、剩余高价值未覆盖行为数与重检队列规模
  - [x] SubTask 6.3: 保证对外正式汇总仍只使用 strict validated bug 口径

- [x] Task 7: 补充回归与 benchmark 验证
  - [x] SubTask 7.1: 增加 campaign 状态机、coverage ledger、frontier 选择和预算切片的自动化测试
  - [x] SubTask 7.2: 用至少一个代表性 benchmark 验证“单轮 run”与“多轮 campaign”在累计 validated bug 与 frontier 收敛上的差异
  - [x] SubTask 7.3: 明确 campaign 的停止条件、边际收益阈值与剩余风险说明

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 3
- Task 5 depends on Task 1
- Task 5 depends on Task 2
- Task 6 depends on Task 2
- Task 6 depends on Task 3
- Task 6 depends on Task 5
- Task 7 depends on Task 4
- Task 7 depends on Task 5
- Task 7 depends on Task 6
