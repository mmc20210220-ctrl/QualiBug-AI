# [OPEN] command-exec-stall

## 背景
- 目标：验证 `benchmark_mall_v05_p0probe` 的新回归套件构建逻辑是否真正进入运行链路。
- 现象：代码已修改并通过单测，但本地命令执行显示 `exit_code=0`，却看不到预期副作用；产物仍保持旧结果。

## 当前症状
- `RunCommand` 执行 `python -c ...` 后没有标准输出。
- 最小写文件命令未产生目标文件。
- `regression_suite.json` 与 `regression_ci_feedback.json` 仍停留在旧时间点和旧结果。

## 假设
- H1: 当前 `RunCommand`/PowerShell 执行器存在工具层问题，命令状态被错误报告为成功，但命令体未真实执行。
- H2: 私有服务 `8088` 仍在运行旧代码进程，因此浏览器触发的扫描/回归不会反映本地最新改动。
- H3: `regression_runner` 读取的是旧的 `platform_outputs/.../regression_suite/regression_suite.json`，没有触发重新构建。
- H4: 自动扫描链路会在手动扫描后覆盖 `scan_result.json` 和相关衍生产物，导致观察到的文件不是同一轮结果。
- H5: 新增的 defect-to-probe 回退逻辑依赖的 `real_project_defect_data.json` 结构与真实产物存在偏差，导致运行时没有产出 probe。

## 计划
- 先用浏览器和磁盘文件继续取证，确认在线服务和落盘产物的时间线。
- 再判断阻塞点位于工具执行层、服务进程层，还是产物构建层。

## 证据
- 已删除 `platform_outputs/benchmark_mall_v05_p0probe/regression_suite/*` 与 `regression_run/*` 旧产物。
- 随后通过浏览器真实调用 `POST /api/v1/projects/benchmark_mall_v05_p0probe/regression/run`。
- 新产物时间已刷新到 `2026-07-08 08:30:39/08:30:40`，说明服务确实重新生成了文件。
- 但新产物仍然是：
  - `regression_suite_summary.json.total_probe_count = 0`
  - `regression_ci_feedback.json.gate_status = "passed"`
  - `regression_ci_feedback.json.ci_message = "回归套件通过，允许继续发布。"`
- 与当前源码不一致：
  - 当前 [regression_runner.py](file:///d:/QualiBug-AI/QualiBug-AI-main/ai_test_asset_center/regression_runner.py#L196-L237) 明确规定 `total_probe_count <= 0` 时必须输出 `manual_approval_required` 和 `continue_regression`。

## 假设状态
- H1: 部分成立。`RunCommand` 仍存在“退出 0 但缺少副作用”的异常证据。
- H2: 强成立。`8088` 服务当前高概率仍在运行旧代码进程。
- H3: 已排除为唯一根因。旧文件已删除后，服务仍生成旧语义结果。
- H4: 仍成立，但不是本次回归空套件问题的主因。
- H5: 暂未证伪。需要在服务加载新代码后再验证真实 benchmark 是否能从 defect data 产出 probe。

## 下一步
- 需要重启或重新部署 `8088` 私有服务，使其加载当前源码。
- 重启后再次调用 `/regression/run`，重点看：
  - 空套件时是否变为 `manual_approval_required`
  - 是否开始从 `real_project_defect_data.json` 生成非零 probes

## 新证据
- 已经通过真实 HTTP 调用确认：`/regression/run` 现在会生成 `13` 个 probe，说明 defect-to-probe 修复已被在线服务加载。
- 当 `allow_destructive_execution=true` 时，13 个 probe 会全部进入执行，但全部落为 `needs_review`。
- 实际运行落盘文件 [regression_run_result.json](file:///d:/QualiBug-AI/QualiBug-AI-main/platform_outputs/benchmark_mall_v05_p0probe/regression_run/regression_run_result.json) 中每个条目的共同错误都是 `execution.error = "base_url_missing"`。
- 配置根因已定位到 [load_real_project_config()](file:///d:/QualiBug-AI/QualiBug-AI-main/ai_test_asset_center/real_project_onboarding.py#L148-L169)：旧逻辑只读 `platform_inputs/<project>/real_project_config.json`，没有复用 `platform_workspace/<project>/enterprise_pilot_runtime/connector_registry.json`。
- benchmark 实际运行配置存在于 [connector_registry.json](file:///d:/QualiBug-AI/QualiBug-AI-main/platform_workspace/benchmark_mall_v05_p0probe/enterprise_pilot_runtime/connector_registry.json#L28-L54)，其中 `test_profile.api_base_url = http://127.0.0.1:8080`。

## 已实施修复
- 已在 [real_project_onboarding.py](file:///d:/QualiBug-AI/QualiBug-AI-main/ai_test_asset_center/real_project_onboarding.py#L110-L169) 增加通用回退：
  - 当 `real_project_config.json` 缺失或字段为空时，自动从 `connector_registry.test_profile` 回填 `base_url`、`ui_base_url`、`frontend_urls`、`test_credentials`、`database`、`environment_ref`、`deployment_scope_id`。
- 已新增定向测试 [test_real_project_config_connector_fallback.py](file:///d:/QualiBug-AI/QualiBug-AI-main/tests/test_real_project_config_connector_fallback.py) 锁住该行为。

## 假设状态
- H1: 仍部分成立。终端输出可观测性差，但已不影响本轮根因判断。
- H2: 已从“旧进程未加载 defect-to-probe 修复”转为“不确定是否已加载最新 config fallback 修复”，需要重启后验证。
- H3: 已排除。当前已生成非零 probe。
- H4: 仍成立，但不是当前 `base_url_missing` 的主因。
- H5: 已部分证伪。probe 生成链路已正常产出 13 个探针。
