# Phase105O 前端预览服务发布包

Phase105O 把 Phase105L 前端交付包、Phase105M 本地预览服务和 Phase105N 预览服务验收报告打成一个可演示、可发布、可复验的前端发布包。

## 生成

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_release_package --output-dir .\outputs\phase105_frontend_preview_release_package
```

## 启动预览服务

```powershell
cd .\outputs\phase105_frontend_preview_release_package\release
.\START_PREVIEW_SERVER.ps1
```

默认打开：`http://127.0.0.1:8795/`。

## 复验

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_release_package --validate-only --release-dir .\outputs\phase105_frontend_preview_release_package --output-dir .\outputs\phase105_frontend_preview_release_package_recheck
```

## 输出

- `frontend_delivery_bundle/`：Phase105L 前端交付包。
- `preview_acceptance/`：Phase105N 预览服务验收报告。
- `release/START_PREVIEW_SERVER.ps1`：PowerShell 启动脚本。
- `release/START_PREVIEW_SERVER.cmd`：CMD 启动脚本。
- `release/PREVIEW_API_CONTRACT.md`：预览服务只读 API 合同。
- `phase105_frontend_preview_release_manifest.json`：发布包清单。
- `frontend_preview_release_acceptance_report.md`：发布包验收报告。
- `CHECKSUMS.sha256`：发布包完整性校验。
- `phase105_frontend_preview_release_package.zip`：发布包归档。

## 安全

发布包验收会检查 token、cookie、session、client_secret、password 和 traceback 原文泄露。
