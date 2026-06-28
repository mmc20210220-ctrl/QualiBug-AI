# Phase104D：前端联调工作区生成器

Phase104D 在 Phase104A/B/C 之后补齐真实前端工程联调所需的工作区材料。

它会生成：

- `contract/openapi.json`
- `contract/API_CONTRACT.md`
- `contract/frontend_api_client.ts`
- `src/api/qualibugClient.ts`
- `src/api/pageDataAdapters.ts`
- `src/api/qualibugWorkflowSmoke.ts`
- `src/types/qualibug.ts`
- `.env.example`
- `package.json`
- `README_FRONTEND_INTEGRATION.md`
- `INTEGRATION_CHECKLIST.md`
- `workspace_manifest.json`
- `frontend_workspace_acceptance_report.json/.md`

## 生成工作区

```powershell
python -m ai_test_asset_center.phase104_frontend_integration_workspace --output-dir .\outputs\phase104_frontend_workspace
```

## 指定 API 地址

```powershell
python -m ai_test_asset_center.phase104_frontend_integration_workspace --output-dir .\outputs\phase104_frontend_workspace --api-base-url http://127.0.0.1:8790
```

## 只验收已有工作区

```powershell
python -m ai_test_asset_center.phase104_frontend_integration_workspace --validate-only --output-dir .\outputs\phase104_frontend_workspace
```

## 前端联调建议

先启动本地 API：

```powershell
python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8790
```

再把生成的 `src/api` 和 `src/types` 复制到前端工程，按 `README_FRONTEND_INTEGRATION.md` 联调。

## 安全约束

生成器会扫描以下原始凭证样式，发现后验收失败：

- raw token
- raw cookie
- raw session
- raw password
- client_secret
- Authorization: Bearer raw
- SESSION=raw

前端示例只使用占位配置，不写入真实客户凭证。
