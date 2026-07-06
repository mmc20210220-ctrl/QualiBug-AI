# 正式企业 API 部署与身份策略

正式企业 API 由 `backend.main:app` 提供，开发环境默认监听 `127.0.0.1:8000`：

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

前端的企业能力使用独立路径 `/enterprise-api/v1`，不再复用历史 `/api` 私有服务路径。Vite 开发代理会把 `/enterprise-api` 转发到 `QUALIBUG_ENTERPRISE_API_ORIGIN`，默认是 `http://127.0.0.1:8000`。

生产反向代理应将 `/enterprise-api` 转发到正式 FastAPI 服务，并剥离该前缀，使服务实际接收 `/v1/...`。

## 身份与授权模式

部署只能选择以下受控模式之一：

1. **旧单令牌兼容模式**：设置 `QUALIBUG_API_TOKEN`。仅适用于受限试点，不提供项目级权限隔离。
2. **不透明令牌策略模式**：设置 `QUALIBUG_ACCESS_POLICY_JSON`。每个令牌绑定主体、租户、权限和允许项目。
3. **浏览器 JWT 身份映射模式**：设置 `QUALIBUG_JWT_SECRET` 与 `QUALIBUG_JWT_ACCESS_POLICY_JSON`。系统先验证现有登录 JWT，再按 JWT 的 `sub` 从部署策略取权限和项目范围。

JWT 中的 `role` 声明不会被用于授权。权限仅来自服务端配置。

## JWT 身份映射示例

```json
{
  "authenticated-subject": {
    "principal_id": "audit-principal",
    "tenant_id": "tenant-boundary",
    "permissions": [
      "identity.read",
      "source.register",
      "source.read",
      "campaign.plan"
    ],
    "project_ids": ["project-boundary"]
  }
}
```

常用权限字符串：

- `identity.read`
- `source.register` / `source.read`
- `test_data.receipt.issue`
- `execution.approval.issue`
- `campaign.plan` / `campaign.execute`
- `evidence.verify`

`*` 是部署显式授予的通配权限；不要把它作为默认策略。

## 目标环境保护

任何带 `base_url` 的扫描或执行批准还要求：

- `QUALIBUG_ALLOWED_TARGET_ORIGINS` 包含目标 Origin；
- 已登记来源的内容哈希；
- 对应 Campaign、范围与环境；
- 未过期的执行批准；
- 通过测试数据合同校验。

没有执行批准或目标白名单时，系统必须不产生目标流量。

## 前端配置

- 开发代理目标：`QUALIBUG_ENTERPRISE_API_ORIGIN`
- 生产前端基地址：`VITE_QUALIBUG_ENTERPRISE_API_BASE`，例如 `https://gateway.example.invalid/enterprise-api/v1`

不要将 API 令牌、JWT 签名密钥、数据库密码或连接器密钥写入前端环境变量。
