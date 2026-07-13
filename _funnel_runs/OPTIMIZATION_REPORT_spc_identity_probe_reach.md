# Optimization Report

## 修改目标

让已物化的 identity-mutation 探针在空 body / 无具体账号目标时能真正发 HTTP，提升触达与发现。

## 修改文件

- `ai_test_asset_center/sandbox_write_executor.py`
- `tests/test_nonproduction_write_contract.py`

## 原因分析

基线 8/131。`password/reset` 等 identity mutation 切片已可物化，但 sandbox 对所有此类写（含空 body）一律要求 disposable fixture，导致运行时被拦、无 HTTP 触达。

## 修改内容

仅当 body 含具体 identity locator，或 path 含 placeholder 时，才要求 disposable fixture；空/未绑定探针 body 允许执行。受保护账号拦截不变。

## 测试结果

修改前：

- Bug发现: **8/131**
- matched: AUTH-001, ORDER-008, REPORT-001, ORDER-012, ORDER-018, USER-001, ORDER-006, PRODUCT-001

修改后：

- Bug发现: **14/131**
- Recall: 0.1069（原 0.0611）
- Precision: 0.3256（原 0.2286）
- FP: 29（原 27，轻微上升但 TP 净增）
- 新增 TP: AUTH-003, AUTH-006, PRODUCT-006, PRODUCT-013, PAY-006, COUPON-011
- 单元测试: 2 passed
- elapsed: 966.5s，WRAPPER_EXIT=0

提升：

- **+6 个真实 Bug（8 → 14）**

## 决策

**KEEP**
