# Phase106E：前端环境诊断真实触发与轮询状态

Phase106E 在 Phase106D 项目路由基础上，新增项目级环境诊断运行态：

- `/environment-runtime` 环境诊断真实触发页面
- 项目级预检 `triggerEnvironmentPreflight`
- 轮询状态 `pollEnvironmentDiagnosis`
- 阻断原因 `loadEnvironmentBlockers`
- 客户补料动作
- 只读安全执行模式
- real API mode / demo fallback
- Phase104 API 合同清单
- 默认脱敏扫描和 checksum 复验

本阶段仍然只向根仓库提交 Python 生成器和测试，不把 npm 依赖放进根目录。

## 生成

```powershell
python -m ai_test_asset_center.phase106_frontend_environment_runtime --scenario manufacturing --output-dir .\outputs\phase106_frontend_environment_runtime
```

## 启动

```powershell
cd .\outputs\phase106_frontend_environment_runtime\frontend_app
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173/environment-runtime
```

## 复验

```powershell
python -m ai_test_asset_center.phase106_frontend_environment_runtime --validate-only --output-dir .\outputs\phase106_frontend_environment_runtime
```
