# Phase106D Frontend Project Routes

Phase106D 把 Phase106C 的真实 API runtime 前端推进到项目级工作区：项目列表、项目详情、当前项目切换、项目级 API 请求、项目级状态缓存。

## 生成

```powershell
python -m ai_test_asset_center.phase106_frontend_project_routes --scenario manufacturing --output-dir .\outputs\phase106_frontend_project_routes
```

## 启动前端

```powershell
cd .\outputs\phase106_frontend_project_routes\frontend_app
npm install
npm run dev
```

打开：`http://127.0.0.1:5173/projects`

## 复验

```powershell
python -m ai_test_asset_center.phase106_frontend_project_routes --validate-only --output-dir .\outputs\phase106_frontend_project_routes
```

## 输出

- `frontend_app/src/pages/ProjectListPage.tsx`
- `frontend_app/src/pages/ProjectDetailPage.tsx`
- `frontend_app/src/hooks/useProjectWorkspace.ts`
- `frontend_app/src/services/projectWorkspace.ts`
- `frontend_project_routes_manifest.json`
- `frontend_project_routes_acceptance_report.json`
- `CHECKSUMS_PHASE106D.sha256`
- `phase106_frontend_project_routes.zip`
