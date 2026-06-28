# Phase103U Static Frontend Exporter

本阶段新增 `ai_test_asset_center.phase103_static_frontend_exporter`，把 Phase103T 生成的演示数据直接渲染为客户安全的静态前端演示包。

## 能力范围

- 生成 `index.html` 入口页
- 生成质量驾驶舱、环境适配中心、AI 测试计划、实时地图、风险发现、成果战报、ROI 价值分析页面
- 生成共享企业级深色设计系统 CSS
- 生成脱敏后的 `phase103_demo_data.js`
- 生成静态前端 manifest 和 README
- 支持 manufacturing / ecommerce / saas 三个演示场景

## 使用方式

```powershell
python -m ai_test_asset_center.phase103_static_frontend_exporter --scenario manufacturing --output-dir .\outputs\phase103_static_frontend_manufacturing
```

然后打开：

```text
outputs/phase103_static_frontend_manufacturing/index.html
```

## 安全说明

所有导出的 HTML / JS / README 都走统一脱敏路径，不展示 token、cookie、password、session 原值和客户敏感业务数据原文。
