# Phase104F 前端联调交接包

Phase104F 在 Phase104A/B/C/D/E 的基础上，把前端联调所需内容打成一个可交接、可验收、可复验的目录和 zip：

- Phase104D frontend workspace
- OpenAPI 合同与 TypeScript client
- API 合同验收报告
- 前端运行时 smoke 报告
- 前端交接 README / Quickstart / Runbook / Checklist
- phase104_frontend_handoff_manifest.json / .md
- CHECKSUMS.sha256
- phase104_frontend_handoff_bundle.zip

## 生成交接包

```powershell
python -m ai_test_asset_center.phase104_frontend_handoff_bundle --output-dir .\outputs\phase104_frontend_handoff_bundle
```

## 指定场景和 API 地址

```powershell
python -m ai_test_asset_center.phase104_frontend_handoff_bundle --scenario manufacturing --api-base-url http://127.0.0.1:8790 --output-dir .\outputs\phase104_frontend_handoff_bundle
```

## 只验收已有交接包

```powershell
python -m ai_test_asset_center.phase104_frontend_handoff_bundle --validate-only --output-dir .\outputs\phase104_frontend_handoff_bundle
```

## 安全说明

交接包会扫描 raw token、cookie、password、session、client_secret 和 Python traceback 模式；发现后验收失败。
