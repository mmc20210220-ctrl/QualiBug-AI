# Phase105E · 业务流程地图真实 UI

Phase105E 继续聚焦前端显示层，把“AI 已理解的业务链路、节点覆盖状态、风险爆点、环境阻断链路、证据回流”做成一个独立静态页面。

## 生成

```powershell
python -m ai_test_asset_center.phase105_business_flow_map_experience --scenario manufacturing --output-dir .\outputs\phase105_business_flow_map_experience
```

打开：

```powershell
Start-Process .\outputs\phase105_business_flow_map_experience\business_flow_map.html
```

## 验收已有输出

```powershell
python -m ai_test_asset_center.phase105_business_flow_map_experience --validate-only --output-dir .\outputs\phase105_business_flow_map_experience
```

## 输出文件

- `business_flow_map.html`
- `assets/qualibug_business_flow_map.css`
- `assets/qualibug_business_flow_map.js`
- `data/business_flow_map_experience_data.json`
- `README_BUSINESS_FLOW_MAP_EXPERIENCE.md`
- `business_flow_map_experience_manifest.json`
- `business_flow_map_experience_acceptance_report.json`
- `business_flow_map_experience_acceptance_report.md`

## 页面重点

- 以业务链路为主，而不是以技术接口为主。
- 每个风险必须落到业务节点，并显示业务影响。
- 环境阻断必须解释到具体链路，方便客户补料。
- 默认只展示脱敏状态、证据评分、风险标题和业务影响摘要。
