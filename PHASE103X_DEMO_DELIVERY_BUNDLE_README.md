# Phase103X：交付演示包生成器

Phase103X 在 Phase103R-W 的基础上增加“一键打包”能力，用于生成客户安全的企业质量指挥中心交付演示包。

## 能力

- 打包 manufacturing / ecommerce / saas 多场景静态站点。
- 打包 page-ready JSON 种子数据。
- 打包预览 API manifest。
- 生成验收报告，证明页面、API、地图、风险、证据链、报告和脱敏门禁通过。
- 生成商业化材料：一页纸、售前演示脚本、客户交接清单。
- 生成 zip 归档，方便销售、实施、客户成功和前端联调分享。

## 使用

```powershell
python -m ai_test_asset_center.phase103_delivery_bundle --output-dir .\outputs\phase103_delivery_bundle
```

只打包单个场景：

```powershell
python -m ai_test_asset_center.phase103_delivery_bundle --scenario manufacturing --output-dir .\outputs\phase103_delivery_bundle_manufacturing
```

不生成 zip：

```powershell
python -m ai_test_asset_center.phase103_delivery_bundle --scenario saas --output-dir .\outputs\phase103_delivery_bundle_saas --no-zip
```

## 输出

- `delivery_manifest.json`
- `delivery_manifest.md`
- `README_DELIVERY_BUNDLE.md`
- `commercial/01_one_pager.md`
- `commercial/02_sales_demo_script.md`
- `commercial/03_customer_handoff_checklist.md`
- `scenarios/<scenario>/site/index.html`
- `scenarios/<scenario>/data/*.json`
- `scenarios/<scenario>/acceptance_report.md`
- `<output-dir>.zip`

## 安全

交付包使用统一脱敏路径生成，不包含 token、cookie、password、session、client_secret 原值或客户敏感业务数据原文。
