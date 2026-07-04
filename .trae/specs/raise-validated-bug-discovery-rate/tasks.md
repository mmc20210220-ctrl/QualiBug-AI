# Tasks
- [x] Task 1: 明确真实 Bug 计数口径与现状基线
  - [x] SubTask 1.1: 盘点当前 candidate、finding、validated bug、family 的现有定义与出数链路
  - [x] SubTask 1.2: 找出哪些路径仍把弱信号、不可复现结果或证据不全结果计入正式统计
  - [x] SubTask 1.3: 形成统一“真实 Bug 记账门槛”和 benchmark 基线口径

- [x] Task 2: 为发现漏斗建立阶段化观测与阻断原因
  - [x] SubTask 2.1: 在候选生成、Probe 选择、执行、验证、正式记账五个阶段定义统一指标
  - [x] SubTask 2.2: 为低产出项目输出 Top 阻断原因与阶段漏损摘要
  - [x] SubTask 2.3: 保证报表能清楚区分 candidate、pending、validated 三层结果

- [x] Task 3: 优化 Probe 选择逻辑以提高真实产出率
  - [x] SubTask 3.1: 识别当前预算分配中“放大候选量但不提升 validated yield”的选择偏差
  - [x] SubTask 3.2: 引入基于 validated yield、复现成功率、业务关键路径和风险族多样性的选择信号
  - [x] SubTask 3.3: 确保预算有限时高质量 Probe 不被低价值大批量候选挤出

- [x] Task 4: 强化严格验证与证据完整性门禁
  - [x] SubTask 4.1: 梳理 verifier 接受条件，收紧模糊命中、弱证据命中和不可复现命中
  - [x] SubTask 4.2: 为每条正式 Bug 强制绑定 reproduction pack、evidence refs 和失败归因
  - [x] SubTask 4.3: 让缺失 verifier、repro 或 evidence 的结果自动降级为 pending

- [x] Task 5: 建立低发现率诊断与定向回归
  - [x] SubTask 5.1: 对 0 发现/低发现项目输出“无候选、未入选、执行失败、验证失败、证据不足”分类结论
  - [x] SubTask 5.2: 选择代表性 benchmark 做阶段对比，验证真实 Bug 数与 discovery rate 是否提升
  - [x] SubTask 5.3: 补充针对计数口径、阶段漏斗、严格降级规则的自动化测试或回归用例

- [x] Task 6: 固化报表口径并输出最终复测结论
  - [x] SubTask 6.1: 更新项目级与 benchmark 级汇总，默认展示 validated bug 口径
  - [x] SubTask 6.2: 在最终输出中同时展示真实 Bug 数、发现率、复现成功率、证据完整率
  - [x] SubTask 6.3: 总结剩余低产出根因与下一轮最值得优化的环节

- [x] Task 7: 为 checklist 第6项补齐 validated-yield 优先信号证明
  - [x] SubTask 7.1: 在 risk_based_probe_planner 显式加入 validated-yield 优先信号、强弱分层与严格验证就绪标记
  - [x] SubTask 7.2: 输出预算选择偏向严格可验证产出的汇总证据，避免候选规模被误当成优先依据
  - [x] SubTask 7.3: 补充聚焦单测，覆盖 validated-yield 打分偏置与预算选择行为

- [x] Task 8: 为 checkpoint 9/10 做极窄收尾修复
  - [x] SubTask 8.1: 在 benchmark suite 汇总中新增代表性 benchmark funnel before/after 对比字段，显式展示 legacy 与 strict validated 口径差异
  - [x] SubTask 8.2: 为 discovery_accounting 严格降级规则补直接自动化测试，覆盖缺失 verifier、repro、evidence refs 与 candidate_only 分支

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 1
- Task 5 depends on Task 2
- Task 5 depends on Task 3
- Task 5 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 3
- Task 7 depends on Task 6
- Task 8 depends on Task 5
- Task 8 depends on Task 6
