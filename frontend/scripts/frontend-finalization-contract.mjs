import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const journey = read('src/components/dashboard/JourneyStrip.tsx');
const onboardingLib = read('src/lib/onboarding-progress.ts');
assert(
  journey.includes('useOnboardingProgress(project)')
  && onboardingLib.includes("title: '连接企业资料'")
  && onboardingLib.includes("path: '/materials',"),
  '首次接入的企业资料步骤必须以在线连接为主并进入 /materials',
);

const releaseGate = read('src/pages/ReleaseGate.tsx');
assert(!releaseGate.includes('>导出报告</button>'), '页面跳转不得伪装成导出报告');
assert(releaseGate.includes('>返回价值总览</button>'), '发布门禁必须使用真实动作名称');

const settings = read('src/pages/Settings.tsx');
assert(!settings.includes('`${tenantId}123`'), '不得生成可预测的工作区默认密码');
assert(settings.includes('window.crypto.getRandomValues'), '工作区临时密码必须使用浏览器安全随机数');
assert(settings.includes('password: temporaryPassword'), '安全临时密码必须用于创建工作区');
assert(settings.includes('login(tenantId, temporaryPassword)'), '安全临时密码必须贯通自动登录');
assert(settings.includes('onBearerTokenChange={setCBearerToken}'), 'Bearer Token 表单绑定不得回归');

const toastMessage = read('src/lib/toast-message.ts');
for (const readable of [
  '请先粘贴一个在线资料入口 URL',
  '请输入有效的 HTTP(S) URL',
  '在线资料入口必须使用 HTTP(S) URL',
  '连接器 Manifest 未声明可用的 URL 范围字段',
  '当前没有声明 URL 入口的连接器 Manifest',
]) {
  assert(toastMessage.includes(readable), `缺少乱码兼容文案：${readable}`);
}
const toast = read('src/components/Toast.tsx');
assert(toast.includes('normalizeToastMessage'), 'Toast 必须统一规范化历史乱码消息');

const readme = read('README.md');
assert(readme.includes('Vite + React 19 + React Router'), 'README 必须声明实际前端技术栈');
assert(!readme.includes('Next.js App Router'), 'README 不得继续声明失效的 Next.js 架构');
assert(!readme.includes('/_next/'), 'README 不得继续声明失效的 Next.js 静态资源路径');

const packageJson = JSON.parse(read('package.json'));
assert(
  packageJson.scripts?.['test:frontend-finalization'] === 'node scripts/frontend-finalization-contract.mjs',
  'package.json 必须注册前端最终收口契约',
);

const ciGate = read('scripts/ci-gate.mjs');
assert(ciGate.includes('"test:frontend-finalization"'), 'ci:gate 必须执行前端最终收口契约');

console.log('frontend finalization contract passed');
