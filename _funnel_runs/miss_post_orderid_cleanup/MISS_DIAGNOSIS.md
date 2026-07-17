# Bug 漏检诊断报告 (SPC Phase 1)

- 已知 Bug：131
- 真实发现 (TP)：3
- 漏检：128
- Recall：0.0229
- Precision：0.1154

## 核心分析指标

### 5.1 业务理解覆盖率
- PRD业务数量: 7
- 已理解业务: 3
- 业务理解覆盖率: 42.9%

### 5.2 行为路径覆盖率
- 理论行为路径: 0
- 实际执行: 20
- 行为覆盖率: 0.0%

### 5.3 Bug触达率
- 已知Bug: 131
- 进入相关代码路径: 39
- 未进入路径: 92
- 触达率: 29.8%

## 失败阶段分布

- 1. 企业资料理解失败: 0
- 2. 业务模型建立失败: 43
- 3. 行为路径生成失败: 34
- 4. 测试数据生成失败: 0
- 5. 自动执行失败: 0
- 6. 参数覆盖不足: 17
- 7. 异常识别失败: 11
- 8. AI判断失败: 0
- 9. 证据链不足: 23

**当前最大漏检阶段**: 2. 业务模型建立失败 (43)
**优化优先级提示**: Priority 1: 测试触达能力（行为/场景/参数覆盖）

## 逐 Bug 诊断（漏检）

### AUTH-001
- 预期Bug类型: 权限/账号状态
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-002
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### AUTH-004
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### AUTH-005
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### USER-001
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### USER-002
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### USER-003
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=6
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### USER-004
- 预期Bug类型: 资金
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-001
- 预期Bug类型: 前端/商品状态
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=4
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

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
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### PRODUCT-004
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=4
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### PRODUCT-005
- 预期Bug类型: 参数校验/资金
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=4
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### PRODUCT-006
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### PRODUCT-007
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=4
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### INV-001
- 预期Bug类型: 并发/库存
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=2
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

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
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=2
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### INV-006
- 预期Bug类型: 参数校验/库存
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=2
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### CART-001
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### CART-002
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### CART-003
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=2
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### CART-004
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### CART-005
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### COUPON-001
- 预期Bug类型: 优惠券
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-002
- 预期Bug类型: 优惠券
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-003
- 预期Bug类型: 优惠券/金额
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-004
- 预期Bug类型: 优惠券/幂等
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-005
- 预期Bug类型: 优惠券
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-006
- 预期Bug类型: 优惠券/业务规则
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-007
- 预期Bug类型: 优惠券/金额
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### ORDER-001
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-002
- 预期Bug类型: 商品状态
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=2
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-003
- 预期Bug类型: 参数校验/资金
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-004
- 预期Bug类型: 资金计算
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-005
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-006
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-007
- 预期Bug类型: 数据隔离/状态机
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### ORDER-008
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### ORDER-009
- 预期Bug类型: 库存/幂等
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-010
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-011
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### ORDER-012
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### PAY-001
- 预期Bug类型: 状态机/支付
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-002
- 预期Bug类型: 资金
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-003
- 预期Bug类型: 幂等/资金
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-004
- 预期Bug类型: 幂等
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-005
- 预期Bug类型: 数据隔离/资金
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-006
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-007
- 预期Bug类型: 分布式一致性
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REFUND-001
- 预期Bug类型: 状态机/退款
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### REFUND-002
- 预期Bug类型: 资金
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### REFUND-003
- 预期Bug类型: 数据隔离
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### REFUND-004
- 预期Bug类型: 权限
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### REFUND-005
- 预期Bug类型: 幂等
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### REFUND-006
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 已有近似发现，但证据链/交付门控不足，未能计为正式缺陷。
- 失败阶段: 9. 证据链不足
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=1
- 建议优化位置: 补强请求/响应/断言/清理证据，满足 customer delivery gate。

### REPORT-001
- 预期Bug类型: 权限/敏感数据
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-002
- 预期Bug类型: 权限/隐私
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-003
- 预期Bug类型: 统计口径
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-004
- 预期Bug类型: 参数校验
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

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

### AUTH-006
- 预期Bug类型: 权限/注册越权
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### AUTH-007
- 预期Bug类型: 越权/令牌签发
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

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
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

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
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### PRODUCT-012
- 预期Bug类型: 权限/价格异常
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

### INV-007
- 预期Bug类型: 数据泄露
- 实际结果: 未发现
- 未发现原因: 已进入相关模块，但未覆盖该 Bug 的触发参数/关键词组合。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该模块异常/边界参数与触发条件生成。

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
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
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
- 未发现原因: 已触达相关接口，但触发参数/关键词组合未覆盖。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该接口的异常/边界参数组合生成。

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
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-009
- 预期Bug类型: 业务规则/优惠叠加
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-010
- 预期Bug类型: 数据隔离/营销滥用
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-012
- 预期Bug类型: 信息泄露/营销规则
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### COUPON-013
- 预期Bug类型: 金额计算/参数校验
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

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
- 未发现原因: 已触达相关接口，但触发参数/关键词组合未覆盖。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该接口的异常/边界参数组合生成。

### ORDER-017
- 预期Bug类型: 状态机
- 实际结果: 未发现
- 未发现原因: 相关代码路径未被行为切片触达（行为空间/场景覆盖不足）。
- 失败阶段: 3. 行为路径生成失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 优先扩大行为路径与场景生成，覆盖该 Bug 触发接口。

### ORDER-018
- 预期Bug类型: 业务规则/数据隔离
- 实际结果: 未发现
- 未发现原因: 路径与参数已覆盖，但异常识别未产出候选/发现。
- 失败阶段: 7. 异常识别失败
- 详细分析: path_reached=True, param_covered=True, near_candidates=0, near_findings=0
- 建议优化位置: 增强该路径的结果/状态/业务规则异常识别。

### PAY-008
- 预期Bug类型: 支付安全/回调伪造
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-009
- 预期Bug类型: 资金/余额透支
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-010
- 预期Bug类型: 资金/越权
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-011
- 预期Bug类型: 幂等/重放攻击
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-012
- 预期Bug类型: 数据泄露
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### PAY-013
- 预期Bug类型: 敏感信息泄露
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

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
- 未发现原因: 已触达相关接口，但触发参数/关键词组合未覆盖。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该接口的异常/边界参数组合生成。

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
- 未发现原因: 已触达相关接口，但触发参数/关键词组合未覆盖。
- 失败阶段: 6. 参数覆盖不足
- 详细分析: path_reached=True, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 只增强该接口的异常/边界参数组合生成。

### REPORT-005
- 预期Bug类型: 数据泄露/报表权限
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-006
- 预期Bug类型: 权限/财务数据
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-007
- 预期Bug类型: SQL注入/数据泄露
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-008
- 预期Bug类型: 性能/资源耗尽
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-009
- 预期Bug类型: 数据隔离/报表越权
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。

### REPORT-010
- 预期Bug类型: 权限/审计数据泄露
- 实际结果: 未发现
- 未发现原因: 资料中存在相关能力，但业务模型未建立到可调度的模块路径。
- 失败阶段: 2. 业务模型建立失败
- 详细分析: path_reached=False, param_covered=False, near_candidates=0, near_findings=0
- 建议优化位置: 检查业务模型/ontology 绑定，确保该模块可生成可执行行为。
