# QualiBug 证据正确性二次收尾审计报告

## 本轮目标

在上一轮“数据统一出口/前端入口”基础上，继续把证据从“展示可读”推进到“事实正确”：

- 后端不能把 A 接口 / A 方法的响应，挂到 B 缺陷上。
- 后端不能只在状态判断里认为有运行时证据，但原始证据、证据链、断言、复现步骤却读不到同一份证据。
- 前端/HTTP 层不能把缓存中的旧证据、污染证据继续当成已复现 Bug 输出。

## 发现的深层问题

### 1. evidence.calls 与 har_evidence 没有完全统一

上一轮主要拦截了 `har_evidence` 的不匹配证据，但 `evidence.calls` 里的运行时结果仍可能被 `_has_runtime_response()` 认为是真实响应。问题是：

- 状态判断可能认为“有运行时证据”。
- 但原始证据 `raw_evidence.response_raw` 仍只从 `har_evidence` 取数。
- 失败断言、API 对比、复现步骤也主要读 `har_evidence`。

结果就是：同一条 Bug 的不同展示模块可能引用不同证据源，客户看起来会觉得数据乱、不可信。

### 2. 只检查 path，不检查 method

之前已经检查了 `/api/orders` 与 `/api/users` 这种路径不一致，但没有严格检查：

- finding 声称 `POST /api/accounts`
- 运行证据实际来自 `GET /api/accounts`

这种情况也必须降级，否则 GET 的 500 会被错误挂到 POST 缺陷上。

### 3. HTTP 输出层缓存风险没有完全兜住

如果之前缓存里已有 display risk，且里面带着不一致的 raw evidence，HTTP 输出层 sanitizer 只做语义相关性检查，不做 method/path 身份一致性检查。这样旧缓存可能继续污染前端展示。

## 本轮核心修改

### 1. 新增 canonical runtime observation

在 `ai_test_asset_center/display_ready_formatter.py` 新增统一运行时证据选择逻辑：

- `_declared_request_identity()`
- `_runtime_identity_mismatch_reasons()`
- `_accepted_runtime_observations()`
- `_best_runtime_observation()`
- `_runtime_body_excerpt()`

以后 HAR 和 `evidence.calls` 都先进入统一观察对象，再由同一套规则判断是否可作为证据。

### 2. 运行时证据必须同时满足三层门控

一条运行时响应要成为证据，必须同时满足：

1. 有真实响应负载：状态码或响应体。
2. 语义绑定：不能是明显无关的占位符、invalid uuid、mock/test 响应。
3. 请求身份绑定：method/path 必须和 finding 声称的一致。

不满足就不能进入 `raw_evidence`、不能生成失败断言、不能作为已复现依据。

### 3. 原始证据、断言、API 对比、复现步骤统一读同一个证据源

以下模块已改为读取 `_best_runtime_observation()`：

- `_has_runtime_response()`
- `_extract_har_response_evidence()`，保留函数名兼容，但内部已变成 canonical runtime evidence
- `_build_repro_steps_display()`
- `_generate_default_repro_steps()`
- `_extract_failed_assertions()`
- `_build_raw_evidence()`
- `_build_technical_details()`
- `_build_expected_actual_comparison()`
- `_extract_business_keys()`
- `_build_investigation_display()` 的 traceId 提取

这样一条 Bug 的状态、证据链、原始证据、复现步骤、研发定位会来自同一份运行时事实。

### 4. HTTP 输出层增加 method/path 兜底清洗

在 `ai_test_asset_center/phase104_command_center_http_api.py` 中，sanitizer 新增：

- `_runtime_identity_mismatch_reasons()` 校验

即便缓存里已经有“已复现”旧数据，只要 raw evidence 的 method/path 与 risk 声称不一致，也会被降级为：

- `bug_status = not_reproduced`
- `gate_passed = false`
- 清空 `raw_evidence.response_raw`
- 清空 `reproduction.har_evidence`
- `proof.repro_rate = 0`

## 新增回归测试

新增 3 个关键测试，总数从 46 增加到 49：

1. `test_runtime_call_evidence_populates_raw_evidence_and_assertions`
   - 验证 `evidence.calls` 也能正确进入 raw evidence、failed assertions、API comparison、reproduction evidence。

2. `test_method_mismatch_downgrades_runtime_call_to_not_reproduced`
   - 验证 POST finding 不能拿 GET 响应当证据。

3. `test_response_sanitizer_rejects_cached_method_mismatch`
   - 验证 HTTP 输出层能清洗缓存里的 method mismatch 污染证据。

## 验证结果

```bash
python -m pytest -q tests/test_evidence_audit_consistency.py
# 49 passed

python -m pytest -q tests/test_phase104d_frontend_integration_workspace.py
# 3 passed

cd frontend && npm run typecheck
# passed
```

额外尝试运行 `test_phase104e_frontend_runtime_smoke.py` 和 `test_phase104f_frontend_handoff_bundle.py`，当前仓库缺少对应模块：

- `ai_test_asset_center.phase104_frontend_runtime_smoke`
- `ai_test_asset_center.phase104_frontend_handoff_bundle`

这是仓库原有测试/模块缺失问题，不是本轮证据修改导致。

## 商业化效果

本轮之后，客户看到的 Bug 证据会更像真实交付物：

- “已复现”必须有同一条请求/响应证据支撑。
- “接口返回”和“Bug 描述”不一致时自动降级。
- “GET 响应”不能证明“POST 缺陷”。
- 运行证据来自 HAR 还是 evidence.calls，前端展示都统一。
- 缓存旧数据也会在 HTTP 输出层再清洗一次。

这一步解决的是信任问题：宁可少报已复现 Bug，也不能把证据不正确的线索包装成 Bug。
