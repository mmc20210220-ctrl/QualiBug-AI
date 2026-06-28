# Phase105I · AI 测试计划 + 实时测试执行页

Phase105I 继续聚焦前端显示层，补齐 Phase105H 总装后仍缺的执行控制台页面。

## 目标

让客户和实施人员在执行前、执行中、执行后都能看懂：

1. AI 准备测什么。
2. 哪些探针可执行。
3. 哪些探针被环境、权限、安全模式阻断。
4. 实时测试运行到了哪里。
5. 产生了哪些风险事件。
6. 哪些证据链已经回流。
7. 后续如何通过 Phase104 API 接入真实前端。

## 生成命令

```powershell
python -m ai_test_asset_center.phase105_test_execution_experience --scenario manufacturing --output-dir .\outputs\phase105_test_execution_experience
```

打开页面：

```powershell
Start-Process .\outputs\phase105_test_execution_experience\test_execution.html
```

只验收已有输出：

```powershell
python -m ai_test_asset_center.phase105_test_execution_experience --validate-only --output-dir .\outputs\phase105_test_execution_experience
```

## 输出文件

- `test_execution.html`
- `assets/qualibug_test_execution.css`
- `assets/qualibug_test_execution.js`
- `data/test_execution_experience_data.json`
- `README_TEST_EXECUTION_EXPERIENCE.md`
- `test_execution_experience_manifest.json`
- `test_execution_experience_acceptance_report.json`
- `test_execution_experience_acceptance_report.md`

## 安全规则

页面和 JSON 默认使用脱敏数据，不展示 token、cookie、password、session、client_secret 原值，不展示 Python traceback。
