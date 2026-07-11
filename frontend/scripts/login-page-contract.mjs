import { chromium } from 'playwright';

const baseUrl = process.env.LOGIN_PAGE_URL || 'http://127.0.0.1:5174/login?next=%2Fdashboard';
const browser = await chromium.launch({
  headless: true,
  channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge',
});

function fail(message, context = {}) {
  throw new Error(`${message}\n${JSON.stringify(context, null, 2)}`);
}

async function expectCount(locator, expected, context) {
  const actual = await locator.count();
  if (actual !== expected) fail(`Expected ${expected} matching element(s), received ${actual}`, context);
}

async function expectText(locator, expected, context) {
  await expectCount(locator, 1, context);
  const actual = (await locator.innerText()).trim();
  if (actual !== expected) fail(`Expected text "${expected}", received "${actual}"`, context);
}

async function assertNoHorizontalOverflow(page, context) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  if (dimensions.scrollWidth > dimensions.clientWidth) {
    fail('Page has horizontal overflow', { ...context, ...dimensions });
  }
}

async function assertLabel(page, text, context) {
  await expectCount(page.getByLabel(text, { exact: true }), 1, { ...context, label: text });
}

async function installHealthyApi(page) {
  await page.route('**/api/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        components: { api: { status: 'healthy' } },
      }),
    });
  });
}

async function verifyMode(page, mode, viewport) {
  const url = new URL(baseUrl);
  if (mode !== 'login') url.searchParams.set('mode', mode);
  const expected = mode === 'login'
    ? { heading: '登录工作区', action: '安全登录' }
    : mode === 'register'
      ? { heading: '创建工作区', action: '创建并进入工作区' }
      : { heading: '重置登录密码', action: '重置密码并登录' };
  const context = { mode, viewport, url: url.toString() };

  await page.setViewportSize(viewport);
  await page.goto(url.toString(), { waitUntil: 'domcontentloaded' });
  await expectCount(page.getByRole('heading', { name: expected.heading, exact: true }), 1, context);
  await expectText(page.getByRole('button', { name: expected.action, exact: true }), expected.action, context);
  await assertLabel(page, mode === 'login' ? '账号' : '登录账号', context);
  await assertLabel(page, mode === 'forgot' ? '新密码' : '密码', context);
  await expectCount(page.getByRole('button', { name: '显示密码', exact: true }), mode === 'login' ? 1 : 2, context);
  await expectCount(page.getByText('登录服务可用', { exact: true }), 1, context);
  await assertNoHorizontalOverflow(page, context);

  if (mode === 'login') {
    await expectCount(page.getByRole('button', { name: '忘记密码？', exact: true }), 1, context);
    const password = page.getByLabel('密码', { exact: true });
    await password.fill('contract-secret');
    await page.getByRole('button', { name: '显示密码', exact: true }).click();
    if (await password.getAttribute('type') !== 'text') fail('Password did not become visible', context);
    if (await password.inputValue() !== 'contract-secret') fail('Password value changed while toggling visibility', context);
    await expectCount(page.getByRole('button', { name: '隐藏密码', exact: true }), 1, context);
  }
}

try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await installHealthyApi(page);
  for (const viewport of [{ width: 1280, height: 720 }, { width: 390, height: 844 }]) {
    for (const mode of ['login', 'register', 'forgot']) {
      await verifyMode(page, mode, viewport);
    }
  }
  await context.close();
  console.log('PASS login-page-contract: 3 modes × 2 viewports');
} finally {
  await browser.close();
}
