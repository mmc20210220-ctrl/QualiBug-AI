# QualiBug 自主发现 Bug 明细清单

**运行时间**：2026-06-24 13:43–13:57（13min42s，P0 修复后首次端到端验证）
**目标系统**：MES BugLab（127.0.0.1:8000）
**LLM**：DeepSeek deepseek-v4-pro
**运行模式**：自改进闭环 Observe→Diagnose→Improve→Verify（2 rounds + 1 re-verify）

## 统计汇总

| 严重度 | 数量 | 说明 |
|---|---|---|
| P0 | 9 | 阻断级：数据损坏/状态错乱/资金或库存丢失 |
| P1 | 33 | 严重：业务逻辑错误/引用悬空/校验缺失 |
| P2 | 17 | 一般：输入校验/缓存/并发/可观测性 |
| **合计** | **59** | 去重后（Round1 + Round2 + re-verify 合并） |

---

## P0 阻断级（9 个）

1. BOM 版本升级后历史生产订单不追溯，用料错误
2. BOM 激活操作未约束同一物料只能有一个 ACTIVE 版本，并发激活可能造成多版本同时生效
3. 删除物料后关联BOM未级联删除致引用孤儿
4. 完成事件早于开始事件导致状态机错乱
5. 已审核入库单的物料批次号可被修改
6. 生产订单下达时部分成功创建预留但部分失败无回滚
7. 生产订单下达生成工序与预留库存非原子，预留失败未回滚工序
8. 生产订单审批拒绝后未补偿物料预留
9. 质检结果更新后原始检验数据被覆盖，无法追溯
10. 重复报工导致工序工时/产量虚增

---

## P1 严重级（33 个）

### 引用完整性（7 个）
11. 删除已使用的物料主数据导致工单BOM引用悬空
12. 删除物料后，与之关联的BOM/工艺路线引用成为悬空指针
13. 删除物料时未检查BOM/工艺路线/库存引用导致悬挂引用
14. 删除物料时未检查BOM引用，导致BOM引用悬空
15. 删除已关联库存的库位导致库存不可见
16. 工艺路线删除后关联工单的步骤引用悬空
17. BOM被直接停用但未检查关联的进行中生产订单

### 版本不可变性（6 个）
18. BOM修改后已创建但未开始订单仍引用旧版本
19. BOM变更后订单物料需求未重新计算
20. BOM版本更新后已下达订单仍引用旧版本导致用料错误
21. BOM和Routing版本关联的materialCode与Material主数据不同步
22. 已发布(released)的BOM版本允许修改组件行，破坏版本不可变性
23. 已发布的Routing版本仍可增删工序，破坏工艺锁定

### 工艺路线同步（3 个）
24. 工艺路线修改后已下发订单仍按旧工序
25. 工艺路线变更后运行中的生产订单未重新同步工序
26. 已关闭工单的工艺路线步骤可被重新排序

### 幂等性（2 个）
27. 创建生产订单接口无幂等性可重复创建完全相同订单
28. Duplicate production order creation via same orderNo

### 补偿回滚（5 个）
29. 完工入库增加成品库存失败但订单状态变为"已完工"
30. 工序报工失败未回退订单进度或操作员绩效
31. 领料失败后未补回已扣减的库存
32. Material transfer out confirmation not rolled back when inbound receipt fails
33. Quality inspection rejection does not lock the rejected lot inventory

### 状态机（2 个）
34. 工单从 planned 直接跳至 completed 绕过执行
35. 设备状态变更乱序导致OEE计算错误

### 字段不可变性（5 个）
36. 物料编码 (material_code) 可被修改
37. 库存移动记录的源/目标库位事后可修改
38. 创建者ID (created_by) 可被更新为非原始用户
39. 生产订单完成时间可通过更新设置为未来日期
40. 库存调整缺少不可变日志导致追溯失败

### 数量约束（3 个）
41. Material spec 字段无长度限制，可能存储大量无用信息
42. CompletedQty exceeding planQty not rejected
43. Work order quantity exceeds production order planQty

---

## P2 一般级（17 个）

### 输入校验（4 个）
44. 物料批次号允许特殊字符导致序列化混乱
45. Material code length leading to silent truncation duplicate
46. Production Order planQty integer overflow
47. plannedStart later than plannedEnd accepted

### 时间一致性（3 个）
48. 工单实际开始时间晚于创建时间但更新后反而更早
49. 工序完成时间可被修改早于开始时间
50. 实体 updated_at 早于 created_at
51. 生产订单 created_at 可在更新时被修改

### 并发（2 个）
52. 并发更新BOM版本行可能导致数据不一致丢失修改
53. 并发更新物料安全库存导致最终值覆盖而非累加

### 缓存/异步（3 个）
54. 物料主数据修改后缓存未及时更新导致下游读到旧数据
55. 异步库存移动事件失败后无死信处理，导致数据丢失
56. No compensation when work order completion fails after partial progress updates

### 状态约束（1 个）
57. 物料状态从active改为inactive时未校验安全库存或在途订单

### 可观测性（3 个）
58. API响应缺少4xx错误定义，导致调用方无法区分违规原因
59. 高频更新导致分页数据重复
60. Work Order 完成数量可超过订单数量，破坏产能统计

---

## 数据来源

- 运行日志：`.e2e_run_stdout.log`（`[BUG]` 标记行提取，去重排序）
- 运行命令：`python run_loop_worker.py`（真实 worker，非 mock）
- 引擎：11 个 Reasoner 引擎并行（causality/invariant/reconciliation/counterexample/consistency/population/outcome/temporal/saga/event_chain/metamorphic）
- 验证器：stage_verify（before/after 状态快照对比 + 多观察者交叉验证 + 跨角色权限检查）

## 说明

- Round1 发现 28 confirmed，Round2 反馈新增 39，re-verify（自改进后）31 confirmed
- 本清单为 3 轮合并去重后的 59 个独立 bug
- inconclusive（未判定）的假设未计入，实际触达的假设数 113-127 条/轮
- 全部 bug 来自 MES BugLab 靶场系统，非真实生产环境
