const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ headless: true, channel: 'chrome', executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const p = await b.newPage();
  await p.goto('http://localhost:3002/login', { waitUntil: 'networkidle', timeout: 10000 });
  await p.fill('input[type="email"]', 'admin@example.com');
  await p.fill('input[type="password"]', 'Admin@123456');
  await p.click('button[type="submit"]');
  await p.waitForTimeout(2000);
  
  await p.goto('http://localhost:3002/inventory', { waitUntil: 'networkidle', timeout: 10000 });
  await p.waitForTimeout(2000);
  
  const inputs = await p.evaluate(() => {
    return Array.from(document.querySelectorAll('input')).map(e => ({
      type: e.type, name: e.name, placeholder: e.placeholder, id: e.id, value: e.value
    }));
  });
  const buttons = await p.evaluate(() => {
    return Array.from(document.querySelectorAll('button')).map(e => ({
      text: e.textContent?.trim().substring(0, 30), type: e.type
    }));
  });
  const pageText = await p.textContent('body');
  
  console.log('INPUTS:', JSON.stringify(inputs.slice(0, 20)));
  console.log('BUTTONS:', JSON.stringify(buttons.slice(0, 20)));
  console.log('TEXT:', pageText?.substring(0, 600));
  
  await b.close();
})();
