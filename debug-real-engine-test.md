# Debug Session: real-engine-test

Status: OPEN

## User Request
- 跑真实项目测试 bug 引擎

## Scope
- 在不先修改业务逻辑的前提下，启动真实项目相关服务/测试链路
- 采集运行时证据，确认 bug 引擎在真实项目上的实际表现

## Initial Hypotheses
1. Bug 引擎本体可启动，但真实项目输入在预处理或仓库扫描阶段失败。
2. 模型、网络或密钥健康检查失败，导致真实项目任务提前中止。
3. 真实项目触发超时、token 截断或并发瓶颈，导致任务看似中断。
4. 结果已生成，但持久化、SSE 或前端展示链路断裂，造成“未出结果”假象。
5. 真实仓库中的特殊文件、编码或路径结构触发了解析异常。

## Planned Evidence
- 启动路径与依赖健康状态
- 真实项目任务创建与执行日志
- 引擎阶段性输出或异常栈
- 结果落库/接口/SSE 观测结果

## Notes
- 第一步仅做环境检查、启动与观测，不做业务逻辑修复。

## Evidence Collected
- `RUN_BENCHMARK_RUNTIME_VALIDATION.ps1` 首次执行失败，报错为 `Invoke-RestMethod : 无法连接到远程服务器`，失败点在对 `http://127.0.0.1:8011/__health` 的请求。
- 独立启动 `python -m uvicorn benchmark_runtime.runtime_target:app --host 127.0.0.1 --port 8011` 成功，靶场健康检查返回 `loaded_runtime_bug_surfaces=1095`。
- 盲测生成器在真实 benchmark 数据上成功执行：前 3 个项目共生成 `540` 个候选、`240` 个待 runtime 验证项，其中 `03_mes_work_order_quality_trace` 的探针计划成功产出。
- 对 `03_mes_work_order_quality_trace` 执行 `bug-engine-grounded-execute --max-probes 120` 成功产出完整报告与商业交付物。
- `grounded_probe_runtime_evidence_scoreboard.json` 显示：
  - `probe_count=120`
  - `executed_probe_count=120`
  - `validated_candidate_count=120`
  - `executed_readonly_count=6`
  - `executed_write_sandbox_count=114`
  - `snapshot_request_count=228`
  - `evidence_maturity.level=customer_ready_runtime_evidence`

## Hypothesis Status
1. 预处理或仓库扫描失败：否。真实 benchmark 输入已成功生成候选与探针计划。
2. 模型、网络或密钥健康失败：当前证据不支持。至少本次真实 benchmark 路径未在该处失败。
3. 超时、token 或并发导致执行中断：对引擎执行本身不成立；但单项目包装脚本存在启动就绪竞态，可能在靶场冷启动时误报失败。
4. 结果生成后未落地或不可见：否。执行报告、scoreboard 和交付物均已写入 `platform_outputs/benchmark_runtime_suite_v3_mes`。
5. 特殊文件/编码/路径触发异常：否。包含非 ASCII 路径的请求已在 live target 中正常返回。

## Current Conclusion
- 真实项目 bug 引擎链路已在 benchmark v3 的 `03_mes_work_order_quality_trace` 上跑通，并拿到 customer-ready 级 runtime evidence。
- 当前发现的实际问题更像是 `RUN_BENCHMARK_RUNTIME_VALIDATION.ps1` 的启动等待不足，而不是 bug 引擎本身无法处理真实项目。

## Full Suite Result
- Full suite 运行目录：`platform_outputs/benchmark_runtime_suite_v3_full/runs/20260630_055129`
- 15/15 个 benchmark 项目均已产出 `grounded_probe_execution_report.json`
- 按各项目实际落盘报告汇总：
  - `completed_projects=15`
  - `probes=600`
  - `validated_candidates=600`
  - `readonly=45`
  - `write=555`
  - `p0_findings=26`
  - `p1_findings=574`
  - `all_customer_ready=true`
- 最后一个项目 `15_integration_message_center` 的执行报告已完整落盘，说明 full suite 实际结果已完成；若外层 `latest_run.json` 尚未刷新为 `completed`，属于包装脚本收尾/汇总写回滞后，而非执行失败。

## Final Assessment
- 真实 benchmark suite v3 上，bug 引擎在 15 个项目、600 个 probe 的范围内实现了 100% runtime validation 覆盖与 100% validated candidate 命中。
- 当前最值得修复的不是引擎识别或执行正确性，而是单项目快捷脚本的冷启动健康检查竞态，以免误报“引擎跑不起来”。
