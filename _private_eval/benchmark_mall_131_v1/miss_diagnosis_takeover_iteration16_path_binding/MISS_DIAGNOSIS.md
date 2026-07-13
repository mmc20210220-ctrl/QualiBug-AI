# Bug 漏检诊断报告 (SPC Phase 1)

- 已知 Bug：131
- 真实发现 (TP)：14
- 漏检：117
- Recall：0.1069
- Precision：0.3256

## 核心分析指标

### 5.1 业务理解覆盖率
- PRD业务数量: 13
- 已理解业务: 0
- 业务理解覆盖率: 0.0%

### 5.2 行为路径覆盖率
- 理论行为路径: 0
- 实际执行: 34
- 行为覆盖率: 0.0%

### 5.3 Bug触达率
- 已知Bug: 131
- 进入相关代码路径: 16
- 未进入路径: 115
- 触达率: 12.2%

## 失败阶段分布

- 1. 企业资料理解失败: 0
- 2. 业务模型建立失败: 8
- 3. 行为路径生成失败: 84
- 4. 测试数据生成失败: 0
- 5. 自动执行失败: 0
- 6. 参数覆盖不足: 15
- 7. 异常识别失败: 0
- 8. AI判断失败: 10
- 9. 证据链不足: 0

**当前最大漏检阶段**: 3. 行为路径生成失败 (84)
**优化优先级提示**: Priority 1: 测试触达能力（行为/场景/参数覆盖）

## 逐 Bug 诊断（漏检）

### AUTH-002
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-004
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-005
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-002
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=1, near_findings=1
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### USER-003
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### USER-004
- 预期Bug类型: 资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-002
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-003
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-004
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-005
- 预期Bug类型: 参数校验/资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-007
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-001
- 预期Bug类型: 并发/库存
- 实际结果: 未发现
- 未发现原因: 已生成近似候选，但未被提升为正式发现。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=9, near_findings=0
- 建议优化位置: 检查候选确认/AI 判定门槛，定位误杀原因。

### INV-002
- 预期Bug类型: 数据一致性
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-003
- 预期Bug类型: 库存
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-004
- 预期Bug类型: 库存
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-005
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-006
- 预期Bug类型: 参数校验/库存
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-001
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=15, near_findings=3
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### CART-002
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-003
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=22, near_findings=2
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-004
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=2, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-005
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-001
- 预期Bug类型: 优惠券
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-002
- 预期Bug类型: 优惠券
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=2, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-003
- 预期Bug类型: 优惠券/金额
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-004
- 预期Bug类型: 优惠券/幂等
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-005
- 预期Bug类型: 优惠券
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-006
- 预期Bug类型: 优惠券/业务规则
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-007
- 预期Bug类型: 优惠券/金额
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-001
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-002
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=2
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-003
- 预期Bug类型: 参数校验/资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-004
- 预期Bug类型: 资金计算
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-005
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### ORDER-007
- 预期Bug类型: 数据隔离/状态机
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-009
- 预期Bug类型: 库存/幂等
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-010
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-011
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-001
- 预期Bug类型: 状态机/支付
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=2
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-002
- 预期Bug类型: 资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-003
- 预期Bug类型: 幂等/资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-004
- 预期Bug类型: 幂等
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-005
- 预期Bug类型: 数据隔离/资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-007
- 预期Bug类型: 分布式一致性
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-001
- 预期Bug类型: 状态机/退款
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-002
- 预期Bug类型: 资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-003
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-004
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-005
- 预期Bug类型: 幂等
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-006
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REPORT-002
- 预期Bug类型: 权限/隐私
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REPORT-003
- 预期Bug类型: 统计口径
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REPORT-004
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### DB-001
- 预期Bug类型: 数据库约束/幂等
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### DB-002
- 预期Bug类型: 数据库约束/金额
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### DB-003
- 预期Bug类型: 数据库约束/库存
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### DB-004
- 预期Bug类型: 数据库约束/参数
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### UI-001
- 预期Bug类型: 前端/UI
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### UI-002
- 预期Bug类型: 前端/状态机
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### UI-003
- 预期Bug类型: 前端/危险操作
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### UI-004
- 预期Bug类型: 前端/权限
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### AUTH-007
- 预期Bug类型: 越权/令牌签发
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-008
- 预期Bug类型: 信息泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-009
- 预期Bug类型: 后门/认证绕过
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-010
- 预期Bug类型: 权限/角色越权
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-011
- 预期Bug类型: 认证绕过
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-005
- 预期Bug类型: 数据隔离/隐私泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-006
- 预期Bug类型: 越权/数据破坏
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-007
- 预期Bug类型: 越权/业务状态
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-008
- 预期Bug类型: 敏感信息泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-009
- 预期Bug类型: 信息泄露/账号枚举
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-010
- 预期Bug类型: 权限/资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-008
- 预期Bug类型: 商品状态/UI数据
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-009
- 预期Bug类型: 越权/商业数据泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-010
- 预期Bug类型: SQL注入
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### PRODUCT-011
- 预期Bug类型: 越权/参数校验
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### PRODUCT-012
- 预期Bug类型: 权限/价格异常
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### INV-007
- 预期Bug类型: 数据泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-008
- 预期Bug类型: 参数校验/库存
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-009
- 预期Bug类型: 事务一致性
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### INV-010
- 预期Bug类型: 权限/库存篡改
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### INV-011
- 预期Bug类型: 状态机/越权
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=2
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-012
- 预期Bug类型: 数据一致性
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-006
- 预期Bug类型: 越权/数据破坏
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### CART-007
- 预期Bug类型: 价格篡改
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-008
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已生成近似候选，但未被提升为正式发现。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=7, near_findings=0
- 建议优化位置: 检查候选确认/AI 判定门槛，定位误杀原因。

### CART-009
- 预期Bug类型: 越权/数据破坏
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-010
- 预期Bug类型: 数据泄露/越权复制
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### CART-011
- 预期Bug类型: 越权/业务数据篡改
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-008
- 预期Bug类型: 权限/营销资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-009
- 预期Bug类型: 业务规则/优惠叠加
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### COUPON-010
- 预期Bug类型: 数据隔离/营销滥用
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-012
- 预期Bug类型: 信息泄露/营销规则
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### COUPON-013
- 预期Bug类型: 金额计算/参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-013
- 预期Bug类型: 越权/数据泄露
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### ORDER-014
- 预期Bug类型: 越权/地址篡改
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-015
- 预期Bug类型: 权限/资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-016
- 预期Bug类型: 越权/批量破坏
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### ORDER-017
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-008
- 预期Bug类型: 支付安全/回调伪造
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### PAY-009
- 预期Bug类型: 资金/余额透支
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-010
- 预期Bug类型: 资金/越权
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-011
- 预期Bug类型: 幂等/重放攻击
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### PAY-012
- 预期Bug类型: 数据泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PAY-013
- 预期Bug类型: 敏感信息泄露
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### REFUND-007
- 预期Bug类型: 越权/数据泄露
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-008
- 预期Bug类型: 资金/参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-009
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### REFUND-010
- 预期Bug类型: 审批绕过/资金
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### REFUND-011
- 预期Bug类型: 越权/状态机
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REFUND-012
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但 AI/门控判断未将其确认为该已知 Bug。
- 失败阶段: 8. AI判断失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=2, near_findings=2
- 建议优化位置: 检查判定与匹配信号，避免误杀真阳性（勿硬编码 GT）。

### REPORT-005
- 预期Bug类型: 数据泄露/报表权限
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REPORT-006
- 预期Bug类型: 权限/财务数据
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### REPORT-007
- 预期Bug类型: SQL注入/数据泄露
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### REPORT-008
- 预期Bug类型: 性能/资源耗尽
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REPORT-009
- 预期Bug类型: 数据隔离/报表越权
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### REPORT-010
- 预期Bug类型: 权限/审计数据泄露
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。
