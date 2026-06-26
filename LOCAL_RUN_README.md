# QualiBug 本机运行包（Phase83C）

这是给 Windows 本机 Code Agent 使用的可运行产品包。它包含完整 QualiBug 代码、MES BugLab 靶场、Phase83C Loop Runtime 修复，以及相对路径的一键启动脚本。

## 最快启动

1. 解压后打开根目录 PowerShell。
2. 运行 `./BOOTSTRAP_LOCAL.ps1`。
3. 在本机 `.env.local` 填写 DeepSeek 密钥和模型名。
4. 运行 `./TEST_DEEPSEEK_CONNECTION.ps1`。
5. 运行 `./START_LOCAL_QUALIBUG.ps1 -Mode Daemon`。
6. 运行 `./CHECK_LOCAL_QUALIBUG.ps1` 查看心跳、Worker 和日志。

也可以双击：

- `START_LOCAL_QUALIBUG.bat`：持续运行
- `START_ONCE_QUALIBUG.bat`：单轮受控运行
- `CHECK_LOCAL_QUALIBUG.bat`：查看状态
- `STOP_LOCAL_QUALIBUG.bat`：停止本包启动的本地进程

## 安全边界

- 本包不含 `.env.local`、API Key、Token、缓存、日志、SQLite Runtime 数据或运行产物。
- Loop 仅用于本地 MES BugLab 或客户明确提供的非生产测试环境。
- `Production Safety Gate` 不得关闭。
- 本机 Code Agent 必须遵守 `CODE_AGENT_LOCAL_RUN_CONTRACT.md`。
