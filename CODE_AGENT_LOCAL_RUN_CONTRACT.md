# QualiBug 本机 Code Agent 运行合同

## 目标

在 Windows 本机安全启动完整产品包中的 MES BugLab 靶场与唯一 QualiBug 自进化 Loop，并持续读取心跳、日志和结果。不要修改产品源码来“让它跑起来”。

## 必须遵守

1. 只在本机开发 / 测试环境运行；禁止将 Loop 指向生产环境。
2. `.env.local` 是本机秘密文件；不得打印、提交、上传或写入日志。
3. 只允许一个 Loop Supervisor。禁止同时启动：
   - `loop_daemon.py`
   - `run_cron_loop.py` 的多个实例
   - `run_loop1_sweep.py`
   - `run_loop2_improve.py`
   - `run_continuous_loop.py`
4. 不得降低 `timeout_seconds >= 300`、`max_tokens >= 32768`、`max_workers = 4` 等既有运行保护。
5. 不得把失败改成 `CONVERGED`，不得在失败轮写学习记忆或推广策略。
6. 不得因为 DeepSeek 慢而杀死仍持续更新心跳的 Worker。
7. 不得用删除测试、跳过测试或放宽验证门槛来声称运行成功。

## 第一次启动步骤

在产品包根目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\BOOTSTRAP_LOCAL.ps1
```

然后编辑本机 `.env.local`，填写真实 DeepSeek Key 与可用模型名。

执行真实 LLM 连通性测试：

```powershell
.\TEST_DEEPSEEK_CONNECTION.ps1
```

只有看到 `DEEPSEEK_CONNECTION_OK` 才能启动 Loop。

启动持续运行：

```powershell
.\START_LOCAL_QUALIBUG.ps1 -Mode Daemon
```

启动一次受控运行：

```powershell
.\START_LOCAL_QUALIBUG.ps1 -Mode Once
```

## 运行后必须检查

等待至少 30 秒后运行：

```powershell
.\CHECK_LOCAL_QUALIBUG.ps1
```

至少确认：

- `MES target: HEALTHY`
- `.loop_heartbeat.json` 时间持续更新
- `cron_worker.log` 或 `loop_daemon.log` 中没有未处理 Traceback
- `terminal` 不是由异常伪造的 `CONVERGED`
- `FAILED_RETRYABLE` 时保留失败信息，但没有策略推广和学习记忆写入

## 停止

```powershell
.\STOP_LOCAL_QUALIBUG.ps1
```

## Code Agent 的完成标准

只在以下条件全部满足时才能报告“已启动”：

1. 靶场 `/api/health` 健康；
2. DeepSeek 最小请求成功；
3. 只有一个 Supervisor / Worker 获得项目租约；
4. 心跳文件持续更新；
5. 日志可见，未被丢弃；
6. 当前运行结果与状态机一致；
7. 没有暴露 `.env.local` 的密钥。

任何一项失败时，只能报告 `BLOCKED`，并写明精确命令、日志文件和错误原因。
