# Phase103Y：交付包验收门禁

Phase103Y 在 Phase103X 交付演示包生成器之后增加一层客户交付前验收门禁，用于确认交付包中的静态页面、前端数据、验收报告、商业化材料、zip 归档和脱敏安全都满足 V1 演示标准。

## 能力范围

- 验证 `delivery_manifest.json` 与场景数量。
- 验证商业化一页纸、售前演示脚本、客户交接清单。
- 验证每个场景的静态页面、CSS、JS 和 page-ready JSON。
- 验证每个场景的验收报告、质量驾驶舱、实时地图、风险详情、ROI、成果战报。
- 验证 zip 归档可读且包含关键文件。
- 扫描 token、cookie、password、session、client_secret 等原始凭证泄露模式。
- 导出 `delivery_acceptance_report.json` 与 `delivery_acceptance_report.md`。

## 使用

先生成交付包：

```powershell
python -m ai_test_asset_center.phase103_delivery_bundle --output-dir .\outputs\phase103_delivery_bundle
```

再验收：

```powershell
python -m ai_test_asset_center.phase103_delivery_acceptance --bundle-dir .\outputs\phase103_delivery_bundle --output-dir .\outputs\phase103_delivery_acceptance --require-zip
```

也可以一条命令先构建再验收：

```powershell
python -m ai_test_asset_center.phase103_delivery_acceptance --build-first --scenario manufacturing --bundle-dir .\outputs\phase103_delivery_bundle_manufacturing --output-dir .\outputs\phase103_delivery_acceptance_manufacturing --require-zip
```

## 验收结论

通过该门禁表示交付包具备客户演示所需的页面、数据、报告、商业材料和脱敏安全基础。未通过时请查看 `delivery_acceptance_report.md` 中的待处理项。
