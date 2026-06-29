# QualiBug Console Frontend (Next.js App Router + RSC)

该前端用于展示 QualiBug “价值呈现层”：能力覆盖/风险证据链/执行生命周期/领导层报告与 ROI，并支持企业 SSO（OIDC 优先，SAML 预留）。

## 本地运行

```bash
cd frontend
npm ci
npm run dev
```

默认 `AUTH_MODE=demo`，无需登录即可通过 `/login → 进入演示模式` 浏览核心页面。

## CI 门禁

```bash
cd frontend
npm ci
npm run ci:gate
```

门禁包含：typecheck、build、openapi check、基础 tests、脱敏扫描、npm audit 基线、E2E（Playwright）。

## 环境变量（部署约定）

建议复制 [./.env.example](./.env.example) 到 `.env.local` 并按需修改。

| 变量 | 作用 | 示例/默认 |
|---|---|---|
| `AUTH_MODE` | 认证模式：`demo` 或 `oidc` | 默认 `demo` |
| `AUTH_SESSION_SECRET` | session cookie 签名密钥（仅 `oidc` 模式需要） | 生产必须设置 |
| `OIDC_ISSUER` | OIDC Issuer URL（仅 `oidc` 模式） | `https://login.example.com/realms/acme` |
| `OIDC_CLIENT_ID` | OIDC Client ID（仅 `oidc` 模式） | `qualibug-console` |
| `OIDC_CLIENT_SECRET` | OIDC Client Secret（仅 `oidc` 模式） | 生产必须设置 |
| `OIDC_SCOPES` | OIDC scopes（仅 `oidc` 模式） | `openid profile email` |
| `QUALIBUG_API_BASE_URL` | 后端 API Base URL（server-side fetch 优先使用） | `https://qualibug.example.com` |
| `NEXT_PUBLIC_QUALIBUG_API_BASE_URL` | 同上（仅在无法注入 server env 时使用） | 可选 |
| `NEXT_PUBLIC_REALTIME_MODE` | 实时策略：`auto`/`sse`/`poll`/`ws` | 默认 `auto` |
| `NEXT_PUBLIC_REALTIME_POLL_INTERVAL_MS` | 轮询间隔（毫秒） | 默认 `3000` |
| `NEXT_PUBLIC_REALTIME_SSE_OPEN_TIMEOUT_MS` | SSE 打开超时（毫秒） | 默认 `4500` |

## 反向代理与私有化部署

- 建议在企业网关/反向代理后部署，并显式注入 `QUALIBUG_API_BASE_URL` 指向后端服务入口；未配置时会尝试基于 `Host/X-Forwarded-*` 推导，但在多域名/多级代理场景容易出错。
- `AUTH_MODE=oidc` 时需要确保 `/auth/callback` 回调地址可从 IdP 访问；session cookie 为 `httpOnly`，建议开启 HTTPS，并由网关统一处理 `X-Forwarded-Proto/Host`。
- 静态资源由 Next.js 生成与托管（`/_next/*`），网关需要允许该路径通过；建议开启缓存但注意版本化路径。
