# Phase103V Local Preview Server

本阶段新增 `ai_test_asset_center.phase103_preview_server`，把 Phase103T 的演示数据、Phase103U 的静态前端包和 Phase103S 的 API 门面层串成一个本地预览服务。

## 能力范围

- 一条命令生成并预览企业质量指挥中心静态页面
- 暴露只读 V1 API 路由，供前端联调和演示脚本调用
- 支持 manufacturing / ecommerce / saas 三个演示场景
- 默认使用 Python 标准库 `http.server`，不依赖 FastAPI、Flask 或 Node
- 所有 API 与静态资源继续使用统一脱敏路径

## 使用方式

只生成预览包，不启动服务：

```powershell
python -m ai_test_asset_center.phase103_preview_server --scenario manufacturing --output-dir .\outputs\phase103_preview_manufacturing --check
```

启动本地预览服务：

```powershell
python -m ai_test_asset_center.phase103_preview_server --scenario manufacturing --output-dir .\outputs\phase103_preview_manufacturing --port 8787
```

然后打开：

```text
http://127.0.0.1:8787/
```

## 主要 API

```text
/api/v1/preview/health
/api/v1/preview/manifest
/api/v1/projects
/api/v1/projects/{project_id}/command-center
/api/v1/projects/{project_id}/environment/readiness
/api/v1/projects/{project_id}/test-plan
/api/v1/projects/{project_id}/live-map
/api/v1/projects/{project_id}/risks
/api/v1/projects/{project_id}/value-metrics
/api/v1/projects/{project_id}/reports/executive
```

## 安全说明

预览服务只开放 GET 路由，所有返回内容均经过脱敏，不展示 token、cookie、password、session、client_secret 原值和客户敏感业务数据原文。
