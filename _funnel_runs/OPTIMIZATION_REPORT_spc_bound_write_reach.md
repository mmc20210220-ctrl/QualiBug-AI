# Optimization Report

## 修改目标

提升已选中但未 HTTP 执行的 source-bound 写路径触达（SPC Priority 1：行为路径触达）。

## 修改文件

- `ai_test_asset_center/semantic_scenario_generator.py`
- `tests/test_bound_write_scenario_materialization.py`

## 原因分析

漏检诊断显示阶段 3（行为路径生成失败）44 个为最大 Priority-1 瓶颈。

对基线 `llm_throughput` 深挖后发现：`POST /api/auth/password/reset` 等路径已被选入 181 个 slice，但在 `SemanticScenarioGenerator._invariant_from_meta` 中因 `_hypothesis_family=state_machine` 被跳过 `_bound_write_scenario`，直接变成空 `plan_only_requires_fixture`（基线 23 个），`_scenario_executable` 过滤后永不发 HTTP。

因此不是“没生成路径”，而是“生成并选中后被空 plan_only 吞掉”。

## 修改内容

唯一改动：当 `_invariant_runtime_upgrade` 返回 None 时，对 state/lifecycle 族也尝试 `_bound_write_scenario`；成功则按文档 method/body 执行；失败仍保持 plan_only（绝不降级为同路径 GET）。

## 测试结果

修改前（基线 llm_throughput）：

- Bug发现: **7/131**
- Recall: 0.0534
- matched: ORDER-008, REPORT-001, ORDER-012, ORDER-018, USER-001, ORDER-006, PRODUCT-001

修改后（evaluation_submission @ 2026-07-11T11:44Z）：

- Bug发现: **8/131**
- Recall: 0.0611
- Precision: 0.2286
- FP: 27（未恶化）
- 新增 TP: **AUTH-001**（禁用用户仍可登录；match_score=0.95）
- formal_customer_deliverable_count: 38（基线 37）
- 单元测试: `tests/test_bound_write_scenario_materialization.py` 4 passed

提升：

- **+1 个真实 Bug（7 → 8）**

## 决策

**KEEP**（有数据提升，不回滚）

收据：`_funnel_runs/spc_bound_write_verify_receipt.json`

备注：全量 `llm_throughput.json` 落盘时遇到 WinError 5（文件替换权限），评分以同期写入成功的 `llm_throughput.evaluation_submission.json` 为准；完整结果副本：`_funnel_runs/llm_throughput.after_spc_bound_write.json`。
