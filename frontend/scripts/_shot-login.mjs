import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' });
const page = await browser.newPage();
await page.setViewportSize({ width: 1440, height: 860 });
await page.goto('http://127.0.0.1:5174/login', { waitUntil: 'networkidle' });
await page.waitForTimeout(1600);
await page.screenshot({ path: process.argv[2] || 'login-before.png' });
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(800);
await page.screenshot({ path: (process.argv[2] || 'login-before.png').replace('.png', '-mobile.png') });
await browser.close();
console.log('screenshot done');
