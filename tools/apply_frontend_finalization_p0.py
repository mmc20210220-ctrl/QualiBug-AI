from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected exactly one match, got {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "frontend/src/components/dashboard/JourneyStrip.tsx",
    "{ index: 2, title: '导入企业资料', description: '上传 PRD、接口规范等来源资料，系统据此建模业务行为', path: '/settings', action: '导入资料' },",
    "{ index: 2, title: '导入企业资料', description: '上传 PRD、接口规范等来源资料，系统据此建模业务行为', path: '/materials', action: '导入资料' },",
)

replace_once(
    "frontend/src/pages/Dashboard.tsx",
    "onOpenMaterials={() => navigateToProjectPath('/settings', project)}",
    "onOpenMaterials={() => navigateToProjectPath('/materials', project)}",
)

materials_replacements = {
    "璇峰厛绮樿创涓€涓湪绾胯祫鏂欏叆鍙ｆ湇鍔″櫒 URL": "请先粘贴一个在线资料入口 URL",
    "璇疯緭鍏ユ湁鏁堢殑 HTTP(S) URL": "请输入有效的 HTTP(S) URL",
    "鍦ㄧ嚎璧勬枡鍏ュ彛蹇呴』浣跨敤 HTTP(S) URL": "在线资料入口必须使用 HTTP(S) URL",
    "鎺ュ叆鍣ㄧ殑 Manifest 鏈０鏄庡彲鐢ㄧ殑 URL 鑼冨洿瀛楁": "连接器 Manifest 未声明可用的 URL 范围字段",
}
for old, new in materials_replacements.items():
    replace_once("frontend/src/pages/Materials.tsx", old, new)

replace_once(
    "frontend/src/pages/ReleaseGate.tsx",
    '<button className="btn btn-primary" onClick={() => navigateToProjectPath(\'/dashboard\', project)}>导出报告</button>',
    '<button className="btn btn-primary" onClick={() => navigateToProjectPath(\'/dashboard\', project)}>返回价值总览</button>',
)

replace_once(
    "frontend/src/pages/Settings.tsx",
    "export function Settings() {",
    """function createSecureTemporaryPassword(length = 24): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%';
  const values = new Uint32Array(length);
  window.crypto.getRandomValues(values);
  return Array.from(values, (value) => alphabet[value % alphabet.length]).join('');
}

export function Settings() {""",
)

replace_once(
    "frontend/src/pages/Settings.tsx",
    """    try {
      const tenantId = buildTenantId(name);
      const response = await fetch('/api/tenants/create', {""",
    """    try {
      const tenantId = buildTenantId(name);
      const temporaryPassword = createSecureTemporaryPassword();
      const response = await fetch('/api/tenants/create', {""",
)

replace_once(
    "frontend/src/pages/Settings.tsx",
    "password: `${tenantId}123`,",
    "password: temporaryPassword,",
)

replace_once(
    "frontend/src/pages/Settings.tsx",
    "const loginOk = await login(tenantId, `${tenantId}123`);",
    "const loginOk = await login(tenantId, temporaryPassword);",
)

readme = """# QualiBug Console Frontend

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
"""
(ROOT / "frontend/README.md").write_text(readme, encoding="utf-8")

contract = """import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const journey = read('src/components/dashboard/JourneyStrip.tsx');
assert(
  journey.includes("title: '导入企业资料'") && journey.includes("path: '/materials'"),
  '首次接入的企业资料步骤必须进入 /materials',
);

const dashboard = read('src/pages/Dashboard.tsx');
assert(
  dashboard.includes("onOpenMaterials={() => navigateToProjectPath('/materials', project)}"),
  'Dashboard 企业资料入口必须进入 /materials',
);
assert(
  !dashboard.includes("onOpenMaterials={() => navigateToProjectPath('/settings', project)}"),
  'Dashboard 不得把企业资料入口错误指向 /settings',
);

const materials = read('src/pages/Materials.tsx');
for (const token of ['璇峰', '璇疯', '鍦ㄧ', '鎺ュ']) {
  assert(!materials.includes(token), `Materials 仍包含乱码片段: ${token}`);
}
assert(materials.includes('请先粘贴一个在线资料入口 URL'), 'Materials 缺少可读的 URL 必填提示');

const releaseGate = read('src/pages/ReleaseGate.tsx');
assert(!releaseGate.includes('>导出报告</button>'), '页面跳转不得伪装成导出报告');
assert(releaseGate.includes('>返回价值总览</button>'), '发布门禁必须使用真实动作名称');

const settings = read('src/pages/Settings.tsx');
assert(!settings.includes('`${tenantId}123`'), '不得生成可预测的工作区默认密码');
assert(settings.includes('window.crypto.getRandomValues'), '工作区临时密码必须使用安全随机数');
assert(settings.includes('temporaryPassword'), '安全临时密码必须贯通创建和自动登录');

const readme = read('README.md');
assert(readme.includes('Vite + React 19 + React Router'), 'README 必须声明实际前端技术栈');
assert(!readme.includes('Next.js App Router'), 'README 不得继续声明失效的 Next.js 架构');
assert(!readme.includes('/_next/'), 'README 不得继续声明失效的 Next.js 静态资源路径');

console.log('frontend finalization contract passed');
"""
(ROOT / "frontend/scripts/frontend-finalization-contract.mjs").write_text(contract, encoding="utf-8")

package_path = ROOT / "frontend/package.json"
package_data = json.loads(package_path.read_text(encoding="utf-8"))
package_data["scripts"]["test:frontend-finalization"] = "node scripts/frontend-finalization-contract.mjs"
package_path.write_text(json.dumps(package_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

replace_once(
    "frontend/scripts/ci-gate.mjs",
    '  "test:autonomous-ux",\n',
    '  "test:autonomous-ux",\n  "test:frontend-finalization",\n',
)

print("Applied frontend finalization P0 changes.")
