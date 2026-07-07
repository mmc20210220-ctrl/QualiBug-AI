# Debug Session: scan-round3-hang [OPEN]

## Symptom
- 统一 `scan()` 第 3 轮未完成。
- CLI 已修复语法错误后，真实运行卡在 `v12_pipeline.__execute_scenario_once()` 的 `urllib.request.urlopen(...)`。
- 当前持久化状态停留在 rerun campaign `CMP_62472d8016f0a42f7a700c66` 第 2 轮，`confirmed_slice_count = 15`。

## Expected
- 第 3 轮应继续推进当前 `active` rerun campaign，完成新的 runtime attempts，并更新 campaign / scan result 持久化状态。

## Hypotheses
1. 某个具体 scenario step 的目标接口无响应，导致 `urlopen()` 阻塞到人工中断。
2. 不是接口本身挂住，而是某个请求体/路径参数导致服务端进入极慢路径。
3. 第 3 轮选中的 slices 中存在单个异常 step，拖住整轮执行，其余逻辑并未回退。
4. 运行时卡住发生在 scenario 执行层，而不是 campaign 调度层；campaign 在本轮尚未来得及 `record_cycle()`。
5. 当前 CLI/scan 路径没有足够的 step 级 runtime 观测，导致只能看到 `KeyboardInterrupt`，看不到卡住前最后一个 method/path。

## Evidence Plan
- 给 scenario 执行入口和 step 执行点加最小运行时插桩。
- 复现第 3 轮，记录最后一个成功 step 与卡住 step 的 `method/path/actor/scenario_id/behavior_slice_id`。
- 对照 campaign 持久化结果，确认是否确实未进入 `record_cycle()`。

## Status
- Session initialized.
- Instrumentation added to `v12_pipeline.py` at scenario start / before request / after request / retry exception.

## Runtime Evidence
- Debug server session: `scan-round3-hang`
- Log file: `.dbg/trae-debug-log-scan-round3-hang.ndjson`
- Reproduction run completed instead of hanging.
- Every observed `B:before-request` event had a matching `C:after-request` event.
- Representative completed requests:
  - `GET /api/orders` -> `200`
  - `GET /api/cart/items` -> `200`
  - `GET /api/products` -> `200`
  - `GET /api/coupons/validate` -> `404`
  - `POST /api/payments/pay` -> `201`
  - `POST /api/refunds` -> `201`

## Hypothesis Evaluation
1. `某个具体接口无响应导致 urlopen() 阻塞` -> Rejected in this reproduction.
2. `某个请求体/路径参数触发服务端慢路径` -> Not supported by current evidence; observed writes completed within ~19-459ms.
3. `只是单个 scenario step 异常拖住整轮执行` -> Rejected in this reproduction.
4. `中断发生在执行层，campaign 未 record_cycle()` -> Rejected for current reproduction; campaign advanced to round 3.
5. `缺少 step 级 runtime 观测` -> Confirmed before instrumentation; resolved by current instrumentation.

## Current Outcome
- Rerun campaign `CMP_62472d8016f0a42f7a700c66` advanced to round 3.
- Campaign status became `coverage_deferred`.
- `attempted_slice_count = 45`
- `confirmed_slice_count = 18`
- `coverage_deferred_reason = slice_budget_reached`

## Next Focus
- The runtime hang is not the current blocker.
- Next issue is downstream: command center still omits 2 confirmed titles from defects view.

## Follow-up Reproduction
- Reproduced the same class of symptom on rerun campaign `CMP_c2656a6a5f600773c960d528` (`r5`, round 3 target) with Debug Server enabled.
- The run completed successfully instead of hanging:
  - `campaign_status = coverage_deferred`
  - `run_count = 3`
  - `round_count = 3`
  - `confirmed_slice_count = 18`
- Log file `.dbg/trae-debug-log-scan-round3-hang.ndjson` shows every `B:before_request` event has a corresponding `C:after_request` event.
- Observed request set in this reproduction includes:
  - `GET /api/orders`
  - `GET /api/cart/items`
  - `GET /api/products`
  - `GET /api/coupons/validate`
  - `POST /api/payments/pay`
  - `POST /api/refunds`

## Updated Hypothesis Evaluation
1. `某个具体接口无响应导致 urlopen() 阻塞` -> Rejected again in the latest reproduction.
2. `某个请求体/路径参数触发服务端慢路径` -> Not supported; the slowest observed calls still returned with normal completion.
3. `只是单个 scenario step 异常拖住整轮执行` -> Rejected in the latest reproduction.
4. `中断发生在执行层，campaign 未 record_cycle()` -> Rejected for the latest reproduction; round 3 persisted normally.
5. `缺少 step 级 runtime 观测` -> Confirmed before instrumentation and now resolved.

## Updated Focus
- The "hang" is currently non-deterministic and was not reproduced under instrumentation.
- The validated fact is that the read-only continuation path is effective: `r5` also reached `18` confirmed after three rounds.
- If the interrupt appears again, the existing instrumentation is sufficient to capture the last completed and in-flight request.
