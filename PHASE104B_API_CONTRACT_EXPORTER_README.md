# Phase104B：API 合同导出与前端联调包

Phase104B 在 Phase104A 可写本地 HTTP API 之上，增加稳定的前端联调合同导出能力。

## 新增能力

- 导出 `openapi.json`
- 导出 `API_CONTRACT.md`
- 导出 `frontend_api_client.ts`
- 导出 `contract_manifest.json`
- 统一记录 V1 路由、请求体、响应 envelope、错误规范
- 前端可直接使用 TypeScript fetch client 进行本地联调
- 合同示例默认脱敏，不包含 token/cookie/session/client_secret 原值

## 运行方式

```powershell
python -m ai_test_asset_center.phase104_api_contract_exporter --output-dir .\outputs\phase104_api_contract
```

输出：

```text
openapi.json
API_CONTRACT.md
frontend_api_client.ts
contract_manifest.json
```

## 推荐联调流程

1. 启动 Phase104A 本地 API：

```powershell
python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8790
```

2. 导出 API 合同：

```powershell
python -m ai_test_asset_center.phase104_api_contract_exporter --output-dir .\outputs\phase104_api_contract
```

3. 前端导入 `frontend_api_client.ts`，调用：

```ts
const client = new QualiBugCommandCenterClient('http://127.0.0.1:8790')
const projects = await client.listProjects()
const dashboard = await client.getCommandCenter(projects[0].project_id)
```

## 验证

```powershell
python -m pytest tests/test_phase104b_api_contract_exporter.py -q
```
