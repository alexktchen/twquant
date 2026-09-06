const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const html = fs.readFileSync(path.join(__dirname, '../dist/index.html'), 'utf8');
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // All HTTP is fulfilled locally; the test never reaches data services.
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Fixture: offline"}' });
      return route.fulfill({ contentType: 'text/html', body: html });
    });
    await page.goto('http://twquant.test/#/models/model-a?date=2026-08-24&run=run-a');
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    assert.match(await page.locator('.platform-context').innerText(), /2026-08-24/);
    await page.getByRole('link', { name: '回測中心', exact: true }).click();
    await page.getByRole('heading', { name: '回測中心', exact: true }).waitFor();
    assert.equal(new URL(page.url()).hash, '#/backtests');
    await page.goBack();
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    assert.match(page.url(), /date=2026-08-24&run=run-a/);
    await page.reload();
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    await page.goForward();
    await page.getByRole('heading', { name: '回測中心', exact: true }).waitFor();
    await page.goto('http://twquant.test/#/stocks/0050');
    await page.getByRole('heading', { name: '個股研究', exact: true }).waitFor();
    await page.getByText('離線快照僅含熱門', { exact: false }).waitFor();
    assert.match(await page.locator('#platform-content').innerText(), /0050/);
    assert.equal(new URL(page.url()).hash, '#/stocks/0050');
    await page.goto('http://twquant.test/#/not-a-page');
    await page.getByRole('heading', { name: '找不到這個頁面' }).waitFor();
    await page.getByRole('link', { name: '回今日總覽 →' }).click();
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 900 });
      for (const name of ['今日總覽', 'ML 選股', '我的追蹤', '產業研究', '洞悉與證據', '模型中心', '回測中心', '資料與工作']) {
        await page.getByRole('link', { name, exact: true }).click();
        await page.getByRole('heading', { name, exact: true }).waitFor();
        assert(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), `${width}: ${name} overflow`);
      }
    }
    await page.getByRole('link', { name: '模型中心', exact: true }).focus();
    await page.keyboard.press('Enter');
    await page.getByRole('heading', { name: '模型中心', exact: true }).waitFor();
    await page.getByRole('link', { name: '跳至內容' }).focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#platform-content').evaluate(el => el === document.activeElement), true);
    assert.equal(new URL(page.url()).hash, '#/models');
    assert.deepEqual(errors, []);
    console.log('PASS AC-201-1/2/3: deep links, reload, back/forward, leading zero, explicit empty state, responsive navigation and keyboard focus (offline HTTP fixtures).');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
