# Optimization Report

## 修改目标

让 scenario 物化使用与 catalog/slice 相同的 enrich 后 API 文档，使 Markdown 中有、薄 openapi.json 中没有的写路径可真正 HTTP 执行。

## 修改文件

- `ai_test_asset_center/v12_pipeline.py`（已回滚）

## 原因分析

上一轮 KEEP 后基线 8/131。漏检诊断仍以 Priority 1 触达为主。

抽检：`password/reset` 在 enrich 后可物化，但 scenario 生成用未 enrich 文档。

## 修改内容

曾在 scenario 生成前调用 `enrich_api_spec_text`。

## 测试结果

修改前：

- Bug发现: **8/131**

修改后：

- Bug发现: **8/131**
- password/reset 触达: 仍否
- plan_only 计数下降（23→8），但真实 TP 无提升
- elapsed: 946.7s，WRAPPER_EXIT=0

提升：

- **+0 个 Bug**

## 决策

**ROLLBACK**（无 TP 提升，已回滚 `v12_pipeline.py`）

保留上一轮有效改动（bound_write state_machine 物化），当前有效基线仍为 **8/131**。
