# QualiBug Console Frontend

QualiBug 的客户价值呈现与操作控制台，使用 **Vite + React 19 + React Router**。

前端覆盖系统接入、企业资料、运行中心、问题清单、证据中心、发布门禁、覆盖矩阵和后台任务，并通过真实后端数据展示检测结论、证据和回归状态。

## 本地运行

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

- 前端开发地址：`http://127.0.0.1:5174`
- 后端默认地址：`http://127.0.0.1:8088`
- Vite 将 `/api` 请求代理到 8088；5174 被占用时会直接失败，不会自动漂移端口。

正式后端入口：

```bash
python -m ai_test_asset_center.private_pilot_entrypoint
```

## 统一质量门禁

```bash
cd frontend
npm ci
npm run ci:gate
```

`ci:gate` 包含 TypeScript、ESLint、品牌契约、自主 UX、企业资料契约、前端最终收口契约、登录契约和生产构建。

## 常用命令

```bash
npm run typecheck
npm run lint
npm run build
npm run test:frontend-finalization
npm run ci:gate
```

## 运行配置

当前浏览器端主要通过同源 `/api` 访问后端，本地开发代理由 `vite.config.ts` 配置。`.env.example` 中仍保留部署侧认证、数据模式和实时策略参数；任何真实密钥都必须放在未提交的本地或部署环境变量中。

关键约定：

- `AUTH_MODE`：认证模式；
- `QUALIBUG_DATA_MODE`：`auto`、`demo` 或 `real`；
- `QUALIBUG_API_BASE_URL`：部署侧后端入口；
- `NEXT_PUBLIC_REALTIME_MODE`：历史命名的实时策略配置，当前保持兼容；
- `AUTH_SESSION_SECRET`、`OIDC_CLIENT_SECRET`：生产环境必须替换，禁止提交真实值。

## 部署

```bash
npm run build
npm run start
```

`npm run start` 使用 Vite Preview。生产环境可由现有私有服务或反向代理托管 `dist/`，并将 `/api` 转发到 QualiBug 后端。

## 产品主链

`登录 -> 选择/创建客户 -> 接入系统 -> 导入资料 -> 运行前检查 -> 一键检测 -> 问题与证据 -> 发布门禁 -> 回归闭环`

普通用户优先完成主链；Scope、Source、Fixture、Connector、覆盖矩阵和后台任务属于高级操作面。
