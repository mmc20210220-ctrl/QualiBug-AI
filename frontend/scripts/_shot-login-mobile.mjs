import { chromium } from 'playwright';

const target = process.argv[2];
const url = process.argv[3] || 'http://127.0.0.1:5174/login';
const width = Number(process.argv[4] || 390);
const height = Number(process.argv[5] || 844);
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' });
const page = await browser.newPage({ viewport: { width, height } });
await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(1800);
await page.screenshot({ path: target });
await browser.close();
console.log('screenshot done');
