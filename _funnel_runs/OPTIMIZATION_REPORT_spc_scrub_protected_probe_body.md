# Optimization Report

## 修改目标

避免 API 文档中的演示账号示例（如 buyer01@example.com）导致 identity-mutation 探针被 `protected_runtime_identity_mutation_blocked` 永久拦截，提升 password/reset 等路径触达。

## 修改文件

- `ai_test_asset_center/sandbox_write_executor.py`
- `tests/test_nonproduction_write_contract.py`

## 原因分析

基线 14/131。AUTH-002（password/reset）切片已选中且可物化，但 `projects/benchmark_mall/input/API_SPEC.md` 示例 body 含 `buyer01@example.com`，sandbox 判定为受保护账号写 → hard block → 未 attempted。

## 修改内容

在 `execute_with_sandbox_write` 预检时，若因 body 含受保护 identity 被拦，则剥离这些字面量后重检；仍危险则继续拦截。不放行对受保护账号的真实篡改。

## 测试结果

修改前：

- Bug发现: 14/131

修改后：

- 单元测试: 3 passed
- 全量 131：进行中

## 保留/回滚准则

TP 未提升则回滚。
