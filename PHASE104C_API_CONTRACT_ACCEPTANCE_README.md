# Phase104C API 合同验收门禁

Phase104C 在 Phase104A 可写本地 HTTP API 与 Phase104B API 合同导出器之后，增加一层前端联调前的合同验收门禁。

它会检查：

1. `openapi.json`、`API_CONTRACT.md`、`frontend_api_client.ts`、`contract_manifest.json` 是否完整；
2. OpenAPI 路由、方法、operationId 是否与后端合同一致；
3. 统一 `success/data/error/meta` 响应 envelope 和错误/脱敏文档是否存在；
4. TypeScript client 是否覆盖项目创建、业务建模、环境预检、测试计划、测试运行、驾驶舱、地图、风险、ROI、报告等核心流程；
5. 内嵌 Phase104A HTTP App 是否能跑通本地联调流程；
6. 运行时 API 响应是否返回业务价值数据；
7. 合同与运行时响应是否泄露 token/cookie/password/session/client_secret 原值。

## 生成合同并验收

```powershell
python -m ai_test_asset_center.phase104_api_contract_acceptance --build-first --contract-dir .\outputs\phase104_api_contract --output-dir .\outputs\phase104_api_contract_acceptance
```

## 只验收已有合同

```powershell
python -m ai_test_asset_center.phase104_api_contract_acceptance --contract-dir .\outputs\phase104_api_contract --output-dir .\outputs\phase104_api_contract_acceptance
```

## 跳过运行时 smoke，仅检查合同文件

```powershell
python -m ai_test_asset_center.phase104_api_contract_acceptance --contract-dir .\outputs\phase104_api_contract --output-dir .\outputs\phase104_api_contract_acceptance --skip-live-smoke
```

## 输出

```text
api_contract_acceptance_report.json
api_contract_acceptance_report.md
```

## 价值

Phase104C 让前端联调前有明确门禁：不是只生成 OpenAPI，而是能证明合同完整、client 可用、运行时 API 可跑、响应业务化且默认脱敏。
