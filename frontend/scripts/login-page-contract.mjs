import { chromium } from 'playwright';

const baseUrl = process.env.LOGIN_PAGE_URL || 'http://127.0.0.1:5174/login?next=%2Fdashboard';
let healthRequestCount = 0;
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

async function expectTextContains(locator, expected, context) {
  await expectCount(locator, 1, context);
  const actual = (await locator.innerText()).trim();
  if (!actual.includes(expected)) fail(`Expected text containing "${expected}", received "${actual}"`, context);
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

async function assertCurrentCopy(page, context) {
  for (const text of [
    'EVIDENCE-DRIVEN QUALITY',
    '上线前，先看清',
    '业务会不会出事',
    '把软件风险变成可复现、可验收、可决策的业务结论。',
    '发现真问题',
    '验证后再交付',
    '结论有证据',
    '影响与复现可追溯',
    '发布有依据',
    '风险门禁清晰可见',
  ]) {
    await expectCount(page.getByText(text, { exact: true }), 1, { ...context, text });
  }
  await expectCount(
    page.locator('[data-brand-detail="compact"][data-brand-tone="dark"]'),
    1,
    context,
  );
  await expectCount(page.locator('[data-login-visual="radar"]'), 1, context);
  const scanLayer = page.locator('[data-login-visual-layer="scan"]');
  await expectCount(scanLayer, 1, context);
  const scanStyle = await scanLayer.evaluate((element) => {
    const style = getComputedStyle(element);
    return { display: style.display, animationName: style.animationName };
  });
  if (context.viewport.width <= 900) {
    if (scanStyle.display !== 'none') fail('Mobile scan-light layer must be hidden', { ...context, scanStyle });
  } else if (scanStyle.animationName !== 'scan-sweep') {
    fail('Desktop scan-light animation is missing', { ...context, scanStyle });
  }
  const visual = page.locator('[data-brand-visual-state]');
  await expectCount(visual, 1, context);
  await page.waitForFunction(() => {
    const state = document.querySelector('[data-brand-visual-state]')?.getAttribute('data-brand-visual-state');
    return state === 'ready' || state === 'reduced-motion';
  });
}

async function assertFavicon(page) {
  const faviconUrl = new URL('/favicon.svg', baseUrl).toString();
  const response = await page.request.get(faviconUrl);
  const contentType = response.headers()['content-type'] || '';
  const body = await response.text();
  if (!response.ok()) fail('Favicon request failed', { faviconUrl, status: response.status() });
  if (!contentType.startsWith('image/svg+xml')) fail('Favicon has wrong content type', { faviconUrl, contentType });
  if (!body.trimStart().startsWith('<svg')) fail('Favicon returned non-SVG content', { faviconUrl, prefix: body.slice(0, 80) });
}

async function installHealthyApi(page) {
  await page.route('**/api/health', async (route) => {
    healthRequestCount += 1;
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
  const healthRequestsBeforeNavigation = healthRequestCount;

  await page.setViewportSize(viewport);
  await page.goto(url.toString(), { waitUntil: 'domcontentloaded' });
  await expectCount(page.getByRole('heading', { name: expected.heading, exact: true }), 1, context);
  await expectTextContains(page.getByRole('button', { name: expected.action, exact: true }), expected.action, context);
  await assertLabel(page, mode === 'login' ? '账号' : '登录账号', context);
  await assertLabel(page, mode === 'forgot' ? '新密码' : '密码', context);
  await expectCount(page.getByRole('button', { name: '显示密码', exact: true }), mode === 'login' ? 1 : 2, context);
  const healthLabel = page.getByText('登录服务可用', { exact: true });
  await healthLabel.waitFor({ state: 'visible' });
  await expectCount(healthLabel, 1, context);
  if (healthRequestCount !== healthRequestsBeforeNavigation + 1) {
    fail('Expected exactly one health request for this page load', {
      ...context,
      requestDelta: healthRequestCount - healthRequestsBeforeNavigation,
    });
  }
  await assertNoHorizontalOverflow(page, context);

  if (mode === 'login') {
    await assertCurrentCopy(page, context);
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
  await assertFavicon(page);
  await context.close();

  const reducedContext = await browser.newContext({ reducedMotion: 'reduce' });
  const reducedPage = await reducedContext.newPage();
  await installHealthyApi(reducedPage);
  await reducedPage.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await reducedPage.locator('[data-brand-visual-state="reduced-motion"]').waitFor({ state: 'visible' });
  await expectCount(
    reducedPage.locator('[data-brand-visual-state="reduced-motion"]'),
    1,
    { mode: 'reduced-motion' },
  );
  const reducedScanAnimation = await reducedPage
    .locator('[data-login-visual-layer="scan"]')
    .evaluate((element) => getComputedStyle(element).animationName);
  if (reducedScanAnimation !== 'none') {
    fail('Reduced-motion scan-light animation must be disabled', {
      mode: 'reduced-motion',
      reducedScanAnimation,
    });
  }
  await reducedContext.close();

  const failedContext = await browser.newContext();
  const failedPage = await failedContext.newPage();
  const brandErrors = [];
  failedPage.on('console', (message) => {
    if (message.type() === 'error' && message.text().includes('[login.brand-visual]')) {
      brandErrors.push(message.text());
    }
  });
  await failedPage.addInitScript(() => {
    HTMLCanvasElement.prototype.getContext = () => null;
  });
  await installHealthyApi(failedPage);
  await failedPage.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await failedPage.locator('[data-brand-visual-state="failed"]').waitFor({ state: 'visible' });
  await expectCount(
    failedPage.locator('[data-brand-visual-state="failed"]'),
    1,
    { mode: 'canvas-failure' },
  );
  if (brandErrors.length === 0) fail('Canvas failure was not logged', { mode: 'canvas-failure' });
  await failedContext.close();

  console.log('PASS login-page-contract: 3 modes × 2 viewports + copy/favicon/motion/failure');
} finally {
  await browser.close();
}
